"""`seed-image comfyui` 子命令。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Mapping, Sequence

import httpx
import typer

from .. import config
from ..comfy import client as api
from ..comfy import outputs as products
from ..comfy import service
from ..comfy.waiting import CANCELLED, FAILED, WaitOutcome, wait_for_jobs
from ..core.values import parse_assignment
from ..core.workflow import inspect_inputs, load_workflow
from ..errors import AppError, ExitCode
from ..render import Output, note
from .context import FormatOption, ServerOption, resolve, resolve_format, run, store

comfyui_app = typer.Typer(no_args_is_help=True, help="包装本地 ComfyUI 的 HTTP 接口。")
job_app = typer.Typer(no_args_is_help=True, help="提交 / 等待 / 查询 / 取消任务。")
workflow_app = typer.Typer(no_args_is_help=True, help="workflow 文件自省。")
comfyui_app.add_typer(job_app, name="job")
comfyui_app.add_typer(workflow_app, name="workflow")

SUBMIT_EPILOG = """\b
示例：
  seed-image comfyui job submit --workflow wf.json \\
    --values "75:74.inputs.text=一只柯基穿着西装" \\
    --values "7.inputs.seed=42" --values "7.inputs.steps=30"

  # 本地文件用 @upload{...} 包围，提交前自动上传（图片/视频/音频/文本通用）
  seed-image comfyui job submit --workflow wf.json \\
    --values "10.inputs.image=@upload{./cat.png}" --mode async
"""

VALUES_HELP = (
    "path=value，可重复。值按 JSON 字面量解析（42→数字、true→布尔、[..]→数组），"
    "解析不了就当字符串。需要上传的本地文件写成 @upload{./cat.png}，"
    "会先上传再把服务端文件名填回该位置。"
)


@comfyui_app.callback()
def comfyui_main(ctx: typer.Context, server: ServerOption = None, fmt: FormatOption = None) -> None:
    store(ctx, server=server, format=fmt)


@job_app.command("submit", epilog=SUBMIT_EPILOG, help="提交 workflow。sync 阻塞并下载产物，async 立刻返回 job id。")
def submit(
    ctx: typer.Context,
    workflow: Annotated[Path, typer.Option("--workflow", help="API 格式 workflow JSON")],
    values: Annotated[list[str] | None, typer.Option("--values", help=VALUES_HELP)] = None,
    mode: Annotated[str, typer.Option("--mode", help="sync=等待并下载产物；async=返回 job id")] = "sync",
    output_path: Annotated[Path, typer.Option("--output-path", help="产物落盘目录")] = Path("."),
    timeout: Annotated[float, typer.Option("--timeout", help="sync 模式最长等待秒数")] = 300.0,
    poll: Annotated[float, typer.Option("--poll", help="轮询间隔秒数")] = 1.0,
    on_timeout: Annotated[str, typer.Option("--on-timeout", help="keep=保留任务继续跑；cancel=顺手取消")] = "keep",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="只打印打好补丁的 workflow，不上传不提交")] = False,
    concurrency: Annotated[int, typer.Option("--concurrency", help="上传/下载并发数")] = 4,
    server: ServerOption = None,
    fmt: FormatOption = None,
) -> None:
    output_format = resolve_format(ctx, fmt)

    def produce() -> Output:
        base = _server(ctx, server)
        _require(mode in ("sync", "async"), "invalid_mode", f"--mode 只能是 sync 或 async，实际得到 {mode!r}")
        _require_on_timeout(on_timeout)
        graph = load_workflow(workflow)
        assignments = [parse_assignment(spec) for spec in (values or [])]
        if dry_run:
            prepared = service.prepare(graph, assignments, service.naming_only_uploader)
            body = json.dumps(prepared.workflow, ensure_ascii=False, indent=2)
            return Output({"workflow": prepared.workflow, "uploaded": prepared.uploaded}, [body])
        with api.make_client(base) as http:
            prepared = service.prepare(
                graph, assignments, service.uploader(http), concurrency=concurrency
            )
            for local, ref in prepared.uploaded.items():
                note(f"uploaded {local} -> {ref}")
            job_id = service.submit(http, prepared.workflow)
            note(f"submitted job {job_id}")
            if mode == "async":
                return Output(
                    {"job_id": job_id, "status": "pending", "uploaded": dict(prepared.uploaded)},
                    [job_id],
                )
            return _wait_and_collect(
                http, [job_id], timeout=timeout, poll=poll, out_dir=output_path,
                on_timeout=on_timeout, concurrency=concurrency,
            )

    run(output_format, produce)


@job_app.command("wait", help="等待一个或多个任务到终态，并下载产物。可一次等待多个 id（并发提交后用它收网）。")
def wait(
    ctx: typer.Context,
    job_ids: Annotated[list[str], typer.Argument(help="一个或多个 job id")],
    output_path: Annotated[Path, typer.Option("--output-path", help="产物落盘目录")] = Path("."),
    timeout: Annotated[float, typer.Option("--timeout", help="最长等待秒数")] = 300.0,
    poll: Annotated[float, typer.Option("--poll", help="轮询间隔秒数")] = 1.0,
    on_timeout: Annotated[str, typer.Option("--on-timeout", help="keep=保留任务继续跑；cancel=顺手取消")] = "keep",
    concurrency: Annotated[int, typer.Option("--concurrency", help="下载并发数")] = 4,
    server: ServerOption = None,
    fmt: FormatOption = None,
) -> None:
    output_format = resolve_format(ctx, fmt)

    def produce() -> Output:
        base = _server(ctx, server)
        _require_on_timeout(on_timeout)
        with api.make_client(base) as http:
            return _wait_and_collect(
                http, job_ids, timeout=timeout, poll=poll, out_dir=output_path,
                on_timeout=on_timeout, concurrency=concurrency,
            )

    run(output_format, produce)


@job_app.command("status", help="查询任务状态（不下载产物）。")
def status(
    ctx: typer.Context,
    job_ids: Annotated[list[str], typer.Argument(help="一个或多个 job id")],
    server: ServerOption = None,
    fmt: FormatOption = None,
) -> None:
    output_format = resolve_format(ctx, fmt)

    def produce() -> Output:
        base = _server(ctx, server)
        with api.make_client(base) as http:
            jobs = [_summary(job_id, api.get_job(http, job_id)) for job_id in job_ids]
        _report_errors(jobs)
        return Output(
            {"jobs": jobs},
            [f"{item['job_id']} {item['status']}" for item in jobs],
            _exit_code(jobs, timed_out=False),
        )

    run(output_format, produce)


@job_app.command("cancel", help="取消任务。幂等：已完成或未知 id 返回 cancelled=false，不算失败。")
def cancel(
    ctx: typer.Context,
    job_ids: Annotated[list[str], typer.Argument(help="一个或多个 job id")],
    server: ServerOption = None,
    fmt: FormatOption = None,
) -> None:
    output_format = resolve_format(ctx, fmt)

    def produce() -> Output:
        base = _server(ctx, server)
        with api.make_client(base) as http:
            results = [
                {"job_id": job_id, "cancelled": bool(api.cancel_job(http, job_id).get("cancelled"))}
                for job_id in job_ids
            ]
        return Output(
            {"cancelled": results},
            [f"{item['job_id']} cancelled={str(item['cancelled']).lower()}" for item in results],
        )

    run(output_format, produce)


@job_app.command("list", help="列出服务端任务（丢了 job id 时用它找回来）。")
def list_jobs(
    ctx: typer.Context,
    status_filter: Annotated[str | None, typer.Option("--status", help="逗号分隔：pending,in_progress,completed,failed")] = None,
    limit: Annotated[int, typer.Option("--limit")] = 20,
    offset: Annotated[int, typer.Option("--offset")] = 0,
    server: ServerOption = None,
    fmt: FormatOption = None,
) -> None:
    output_format = resolve_format(ctx, fmt)

    def produce() -> Output:
        base = _server(ctx, server)
        with api.make_client(base) as http:
            body = api.list_jobs(http, status=status_filter, limit=limit, offset=offset)
        jobs = body.get("jobs") or []
        lines = [f"{job.get('id')} {job.get('status')} outputs={job.get('outputs_count', 0)}" for job in jobs]
        return Output(body, lines)

    run(output_format, produce)


@workflow_app.command("inspect", help="列出 workflow 里可被 --values 赋值的输入槽及当前值。")
def inspect(
    ctx: typer.Context,
    workflow: Annotated[Path, typer.Argument(help="API 格式 workflow JSON")],
    keyword: Annotated[str | None, typer.Option("--filter", help="只显示路径或类名含该关键字的槽")] = None,
    fmt: FormatOption = None,
) -> None:
    output_format = resolve_format(ctx, fmt)

    def produce() -> Output:
        slots = [
            {"path": slot.path, "class_type": slot.class_type, "value": slot.value}
            for slot in inspect_inputs(load_workflow(workflow))
            if keyword is None or keyword in slot.path or keyword in slot.class_type
        ]
        lines = [
            f"{slot['path']} = {json.dumps(slot['value'], ensure_ascii=False)}  ({slot['class_type']})"
            for slot in slots
        ]
        return Output({"inputs": slots}, lines)

    run(output_format, produce)


def _wait_and_collect(
    http: httpx.Client,
    job_ids: Sequence[str],
    *,
    timeout: float,
    poll: float,
    out_dir: Path,
    on_timeout: str,
    concurrency: int,
) -> Output:
    """等待 → 下载 → 汇总。超时不丢任务，只在退出码和 hint 里说明。"""
    outcome = wait_for_jobs(
        lambda job_id: api.get_job(http, job_id),
        job_ids,
        timeout=timeout,
        poll_interval=poll,
        report=note,
    )
    if outcome.timed_out and on_timeout == "cancel":
        for job_id in outcome.unfinished:
            api.cancel_job(http, job_id)

    jobs: list[dict[str, Any]] = []
    paths: list[str] = []
    for job_id, job in outcome.finished.items():
        found = products.collect(job)
        saved = products.download(http, job_id, found.files, out_dir, concurrency=concurrency)
        paths.extend(str(p) for p in saved)
        jobs.append(
            _summary(job_id, job)
            | {"images": [str(p) for p in saved], "texts": [t.text for t in found.texts]}
        )
    jobs.extend(_summary(job_id, job) for job_id, job in outcome.unfinished.items())

    _report_errors(jobs)
    data: dict[str, Any] = {"images": paths, "jobs": jobs}
    if outcome.timed_out:
        data["timeout"] = _timeout_note(outcome, on_timeout)
    return Output(data, paths, _exit_code(jobs, timed_out=outcome.timed_out))


def _timeout_note(outcome: WaitOutcome, on_timeout: str) -> dict[str, Any]:
    unfinished = list(outcome.unfinished)
    return {
        "unfinished": unfinished,
        "action": on_timeout,
        "hint": (
            "任务仍在服务端执行；用 `seed-image comfyui job wait "
            f"{' '.join(unfinished)} --timeout 600` 继续等待"
            if on_timeout == "keep"
            else "已请求取消这些任务"
        ),
    }


def _summary(job_id: str, job: Mapping[str, Any]) -> dict[str, Any]:
    summary = {
        "job_id": job_id,
        "status": str(job.get("status") or "unknown"),
        "outputs_count": job.get("outputs_count", 0),
    }
    if job.get("execution_error"):
        summary["error"] = job["execution_error"]
    return summary


def _report_errors(jobs: Sequence[Mapping[str, Any]]) -> None:
    """执行失败的细节必须原样给出——那是修 workflow 的唯一线索。"""
    for job in jobs:
        if job.get("error"):
            note(f"job {job['job_id']} {job['status']}: {json.dumps(job['error'], ensure_ascii=False)}")


def _exit_code(jobs: Sequence[Mapping[str, Any]], *, timed_out: bool) -> int:
    statuses = {job.get("status") for job in jobs}
    if timed_out:
        return ExitCode.TIMEOUT
    if FAILED in statuses:
        return ExitCode.JOB_FAILED
    if CANCELLED in statuses:
        return ExitCode.JOB_CANCELED
    return ExitCode.OK


def _server(ctx: typer.Context, override: str | None) -> str:
    """--server（任意一层） > COMFYUI_SERVER > 配置文件 comfyui.server > 默认值。"""
    return resolve(ctx, "server", override, "") or config.value(config.COMFYUI_SERVER) or ""


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise AppError(code, message, ExitCode.USAGE)


def _require_on_timeout(value: str) -> None:
    _require(
        value in ("keep", "cancel"),
        "invalid_on_timeout",
        f"--on-timeout 只能是 keep 或 cancel，实际得到 {value!r}",
    )
