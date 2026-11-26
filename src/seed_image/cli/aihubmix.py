"""`seed-image aihubmix` 子命令。"""

from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path
from typing import Annotated, Any, Mapping, Sequence

import httpx
import typer

from .. import config
from ..aihubmix import client as api
from ..comfy.waiting import CANCELLED, FAILED, TERMINAL, WaitOutcome, wait_for_jobs
from ..core import aihubmixparams as ap
from ..core.naming import short_uid
from ..errors import AppError, ExitCode
from ..images.save import write_image, write_sidecar
from ..render import Output, note
from .context import FormatOption, resolve_format, run

aihubmix_app = typer.Typer(
    no_args_is_help=True,
    help=(
        "调用 AIHubMix 原生图片协议（/ai/v1/images）。密钥取自环境变量 "
        f"{config.AIHUBMIX_API_KEY.env} 或配置 {config.AIHUBMIX_API_KEY.key}；"
        f"地址取自 {config.AIHUBMIX_BASE_URL.env} 或配置 {config.AIHUBMIX_BASE_URL.key}。"
    ),
)
model_app = typer.Typer(no_args_is_help=True, help="查询可用模型与原生接口参数 Schema。")
task_app = typer.Typer(no_args_is_help=True, help="等待 / 查询 / 列出异步图片任务。")
aihubmix_app.add_typer(model_app, name="model")
aihubmix_app.add_typer(task_app, name="task")

GENERATE_EPILOG = """\b
示例：
  seed-image aihubmix generate --model doubao-seedream-5-5 --prompt "阳光洒进花店" --size 2048x2048
  seed-image aihubmix generate --model grok-imagine-image-2.0 --prompt "霓虹街景" --aspect-ratio 16:9 --resolution 2K
  seed-image aihubmix generate --model doubao-seedream-5-5 --prompt "换成赛博朋克风格" \\
    --input-image cat.png --extra watermark=false
"""

MODEL_HELP = f"模型 ID（默认取配置 {config.AIHUBMIX_MODEL.key}，未配置则 {config.AIHUBMIX_MODEL.default}）"
KV_EXTRA_HELP = "写入请求体 extra 的扩展参数（key=value，可重复，支持 a.b=value 与 JSON 字面量）"
KV_PARAM_HELP = "写入请求体顶层的任意参数（key=value，可重复，支持 JSON 字面量）"


@aihubmix_app.command(
    "generate",
    epilog=GENERATE_EPILOG,
    help="文生图，或传 --input-image 做参考图/编辑生成，返回落盘图片路径列表。",
)
def generate(
    ctx: typer.Context,
    prompt: Annotated[str, typer.Option("--prompt", help="生成或编辑提示词")],
    input_image: Annotated[
        list[str] | None,
        typer.Option("--input-image", help="参考图本地路径或 http(s) URL，可重复"),
    ] = None,
    mask: Annotated[str | None, typer.Option("--mask", help="编辑蒙版本地路径或 http(s) URL", show_default=False)] = None,
    image_field: Annotated[str, typer.Option("--image-field", help="输入图字段名：auto/image/images")] = "auto",
    model: Annotated[str | None, typer.Option("--model", help=MODEL_HELP, show_default=False)] = None,
    size: Annotated[str | None, typer.Option("--size", help="宽高 WxH 或模型支持的档位（如 2K）", show_default=False)] = None,
    aspect_ratio: Annotated[str | None, typer.Option("--aspect-ratio", help="宽高比（与 --size 互斥，如 16:9）", show_default=False)] = None,
    resolution: Annotated[str | None, typer.Option("--resolution", help="分辨率档位（如 1K/1.5K/2K）", show_default=False)] = None,
    quality: Annotated[str | None, typer.Option("--quality", help="画质档位（如 low/medium/high）", show_default=False)] = None,
    background: Annotated[str | None, typer.Option("--background", help="背景透明度（auto/transparent/opaque）", show_default=False)] = None,
    seed: Annotated[int | None, typer.Option("--seed", help="随机种子", show_default=False)] = None,
    negative_prompt: Annotated[str | None, typer.Option("--negative-prompt", help="负向提示词", show_default=False)] = None,
    output_format: Annotated[str | None, typer.Option("--output-format", help="输出格式：png/jpeg/webp", show_default=False)] = None,
    response_format: Annotated[str | None, typer.Option("--response-format", help="返回载体：url/b64_json", show_default=False)] = None,
    number: Annotated[int | None, typer.Option("--number", min=1, help="生成张数（不传由模型默认）", show_default=False)] = None,
    extra: Annotated[list[str] | None, typer.Option("--extra", help=KV_EXTRA_HELP)] = None,
    param: Annotated[list[str] | None, typer.Option("--param", help=KV_PARAM_HELP)] = None,
    mode: Annotated[str, typer.Option("--mode", help="sync=同步出图；poll=异步提交并轮询等待；async=异步提交立刻返回 task_id")] = "sync",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="只打印构造好的请求体，不实际调用接口")] = False,
    output_path: Annotated[Path, typer.Option("--output-path", help="图片落盘目录")] = Path("."),
    save_params: Annotated[bool, typer.Option("--save-params/--no-save-params", help="是否额外写一份参数记录 json（默认不写）")] = False,
    timeout: Annotated[float, typer.Option("--timeout", help="请求或 poll 等待最长秒数")] = 300.0,
    poll: Annotated[float, typer.Option("--poll", help="poll 模式的轮询间隔秒数")] = 5.0,
    max_retries: Annotated[int, typer.Option("--max-retries", help="可重试错误的最大重试次数")] = 2,
    fmt: FormatOption = None,
) -> None:
    output_format_cli = resolve_format(ctx, fmt)

    def produce() -> Output:
        if mode not in ("sync", "poll", "async"):
            raise AppError("invalid_mode", f"--mode 只能是 sync / poll / async，实际得到 {mode!r}", ExitCode.USAGE)
        resolved_model = config.value(config.AIHUBMIX_MODEL, model) or ""
        body = ap.build_request(
            prompt=prompt,
            model=resolved_model,
            input_images=input_image or (),
            image_field=image_field,
            mask=mask,
            size=size,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            quality=quality,
            background=background,
            seed=seed,
            negative_prompt=negative_prompt,
            output_format=output_format,
            response_format=response_format,
            number=number,
            async_mode=(mode in ("poll", "async")),
            extras=extra or (),
            params=param or (),
        )
        if dry_run:
            preview = ap.without_payload(body)
            return Output({"request": preview}, [json.dumps(preview, ensure_ascii=False, indent=2)])

        t = api.target(require_key=True)
        with api.make_client(t, timeout=timeout) as http:
            task = api.create_generation(http, body, max_retries=max_retries)
            task_id = str(task.get("id") or "")
            status = str(task.get("status") or "unknown")
            if task_id:
                note(f"task {task_id} status={status}")

            if mode == "async":
                return Output(
                    {"task_id": task_id, "status": status, "model": task.get("model") or resolved_model},
                    [task_id],
                )

            if status not in TERMINAL and task_id:
                return _wait_and_collect(
                    http,
                    [task_id],
                    base_url=t.base_url,
                    timeout=timeout,
                    poll=poll,
                    max_retries=max_retries,
                    out_dir=output_path,
                    prompt=prompt,
                    request_body=body,
                    save_params=save_params,
                )

            return _collect_single(
                http,
                task,
                base_url=t.base_url,
                max_retries=max_retries,
                out_dir=output_path,
                prompt=prompt,
                request_body=body,
                save_params=save_params,
            )

    run(output_format_cli, produce)


@task_app.command("wait", help="等待一个或多个异步任务到终态，并下载落盘。")
def task_wait(
    ctx: typer.Context,
    task_ids: Annotated[list[str], typer.Argument(help="一个或多个 task id")],
    output_path: Annotated[Path, typer.Option("--output-path", help="图片落盘目录")] = Path("."),
    save_params: Annotated[bool, typer.Option("--save-params/--no-save-params", help="是否额外写一份参数记录 json（默认不写）")] = False,
    timeout: Annotated[float, typer.Option("--timeout", help="最长等待秒数")] = 300.0,
    poll: Annotated[float, typer.Option("--poll", help="轮询间隔秒数")] = 5.0,
    max_retries: Annotated[int, typer.Option("--max-retries")] = 2,
    fmt: FormatOption = None,
) -> None:
    output_format_cli = resolve_format(ctx, fmt)

    def produce() -> Output:
        t = api.target(require_key=True)
        with api.make_client(t, timeout=timeout) as http:
            return _wait_and_collect(
                http,
                task_ids,
                base_url=t.base_url,
                timeout=timeout,
                poll=poll,
                max_retries=max_retries,
                out_dir=output_path,
                prompt=None,
                request_body=None,
                save_params=save_params,
            )

    run(output_format_cli, produce)


@task_app.command("status", help="查询一个或多个任务状态（不下载）。")
def task_status(
    ctx: typer.Context,
    task_ids: Annotated[list[str], typer.Argument(help="一个或多个 task id")],
    timeout: Annotated[float, typer.Option("--timeout", help="请求超时秒数")] = 60.0,
    max_retries: Annotated[int, typer.Option("--max-retries")] = 2,
    fmt: FormatOption = None,
) -> None:
    output_format_cli = resolve_format(ctx, fmt)

    def produce() -> Output:
        t = api.target(require_key=True)
        with api.make_client(t, timeout=timeout) as http:
            tasks = [_task_summary(tid, api.get_image_task(http, tid, max_retries=max_retries)) for tid in task_ids]
        _report_task_errors(tasks)
        lines = [f"{item['task_id']} {item['status']} outputs={item['outputs_count']}" for item in tasks]
        return Output({"tasks": tasks}, lines, _tasks_exit_code(tasks, timed_out=False, partial=False))

    run(output_format_cli, produce)


@task_app.command("list", help="列出图片任务快照（sync 超时或响应丢失时用于找回任务 ID）。")
def task_list(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option("--limit", min=1, max=100, help="返回条数（1–100）")] = 20,
    after: Annotated[str | None, typer.Option("--after", help="分页游标 next_after", show_default=False)] = None,
    order: Annotated[str, typer.Option("--order", help="排序：desc 或 asc")] = "desc",
    timeout: Annotated[float, typer.Option("--timeout", help="请求超时秒数")] = 60.0,
    max_retries: Annotated[int, typer.Option("--max-retries")] = 2,
    fmt: FormatOption = None,
) -> None:
    output_format_cli = resolve_format(ctx, fmt)

    def produce() -> Output:
        t = api.target(require_key=True)
        with api.make_client(t, timeout=timeout) as http:
            body = api.list_image_tasks(http, limit=limit, after=after, order=order, max_retries=max_retries)
        items = body.get("data") or []
        lines = [
            f"{item.get('id')} {item.get('status')} model={item.get('model')} outputs={len(item.get('output') or ())}"
            for item in items
        ]
        return Output(body, lines)

    run(output_format_cli, produce)


@model_app.command("list", help="列出 AIHubMix 支持原生 Schema 校验的生图模型。")
def model_list(
    ctx: typer.Context,
    keyword: Annotated[str | None, typer.Option("--filter", help="按模型 ID/名称/厂商/简介过滤", show_default=False)] = None,
    timeout: Annotated[float, typer.Option("--timeout", help="请求超时秒数")] = 30.0,
    fmt: FormatOption = None,
) -> None:
    output_format_cli = resolve_format(ctx, fmt)

    def produce() -> Output:
        t = api.target(require_key=False)
        with api.make_client(t, timeout=timeout) as http:
            payload = api.list_models(http)
        models = ap.summarize_models(payload, keyword=keyword)
        lines = [
            f"{m['model_id']}  [{m['vendor'] or '-'}]  input={m['input_modalities'] or '-'}  {m['model_name']}"
            for m in models
        ]
        return Output({"models": models}, lines)

    run(output_format_cli, produce)


@model_app.command("inspect", help="查看指定模型在 /ai/v1/images/generations 端点支持的字段与 extra 参数。")
def model_inspect(
    ctx: typer.Context,
    model_id: Annotated[str, typer.Argument(help="模型 ID，例如 doubao-seedream-5-5")],
    keyword: Annotated[str | None, typer.Option("--filter", help="只显示字段名或描述含该关键字的参数", show_default=False)] = None,
    raw: Annotated[bool, typer.Option("--raw", help="输出完整原始 JSON Schema")] = False,
    timeout: Annotated[float, typer.Option("--timeout", help="请求超时秒数")] = 30.0,
    fmt: FormatOption = None,
) -> None:
    output_format_cli = resolve_format(ctx, fmt)

    def produce() -> Output:
        t = api.target(require_key=False)
        with api.make_client(t, timeout=timeout) as http:
            payload = api.get_model_endpoints(http, model_id)
        summary = ap.extract_generation_schema(payload, model_id, keyword=keyword)
        if raw:
            data: dict[str, Any] = {
                "model": summary["model"],
                "endpoint": summary["endpoint"],
                "schema": summary["raw_schema"],
            }
            return Output(data, [json.dumps(summary["raw_schema"], ensure_ascii=False, indent=2)])

        data = {
            "model": summary["model"],
            "endpoint": summary["endpoint"],
            "fields": summary["fields"],
            "extra_fields": summary["extra_fields"],
        }
        lines = [_format_field_line(f) for f in summary["fields"]]
        if summary["extra_fields"]:
            lines.append("# extra (--extra key=value):")
            lines.extend(_format_field_line(f, prefix="extra.") for f in summary["extra_fields"])
        return Output(data, lines)

    run(output_format_cli, produce)


def _format_field_line(field: Mapping[str, Any], *, prefix: str = "") -> str:
    req = "*" if field.get("required") else ""
    parts = [f"{prefix}{field['name']}{req} ({field.get('type', 'any')})"]
    if "default" in field:
        parts.append(f"default={json.dumps(field['default'], ensure_ascii=False)}")
    if "enum" in field:
        parts.append(f"enum={json.dumps(field['enum'], ensure_ascii=False)}")
    if "constraints" in field:
        parts.append(f"constraints={json.dumps(field['constraints'], ensure_ascii=False)}")
    if "description" in field:
        parts.append(field["description"])
    return "  ".join(parts)


def _wait_and_collect(
    http: httpx.Client,
    task_ids: Sequence[str],
    *,
    base_url: str,
    timeout: float,
    poll: float,
    max_retries: int,
    out_dir: Path,
    prompt: str | None,
    request_body: Mapping[str, Any] | None,
    save_params: bool,
) -> Output:
    outcome = wait_for_jobs(
        lambda tid: api.get_image_task(http, tid, max_retries=max_retries),
        task_ids,
        timeout=timeout,
        poll_interval=poll,
        report=note,
    )
    return _collect_outcome(
        http,
        outcome,
        base_url=base_url,
        max_retries=max_retries,
        out_dir=out_dir,
        prompt=prompt,
        request_body=request_body,
        save_params=save_params,
    )


def _collect_single(
    http: httpx.Client,
    task: Mapping[str, Any],
    *,
    base_url: str,
    max_retries: int,
    out_dir: Path,
    prompt: str | None,
    request_body: Mapping[str, Any] | None,
    save_params: bool,
) -> Output:
    task_id = str(task.get("id") or "sync")
    outcome = WaitOutcome(finished={task_id: task})
    return _collect_outcome(
        http,
        outcome,
        base_url=base_url,
        max_retries=max_retries,
        out_dir=out_dir,
        prompt=prompt,
        request_body=request_body,
        save_params=save_params,
    )


def _collect_outcome(
    http: httpx.Client,
    outcome: WaitOutcome,
    *,
    base_url: str,
    max_retries: int,
    out_dir: Path,
    prompt: str | None,
    request_body: Mapping[str, Any] | None,
    save_params: bool,
) -> Output:
    tasks: list[dict[str, Any]] = []
    all_images: list[str] = []
    sidecars: list[str] = []
    all_failures: list[str] = []

    for tid, task in outcome.finished.items():
        saved, failures, sidecar = _save_task_outputs(
            http,
            tid,
            task,
            base_url=base_url,
            max_retries=max_retries,
            out_dir=out_dir,
            prompt=prompt,
            request_body=request_body,
            save_params=save_params,
        )
        all_images.extend(saved)
        all_failures.extend(failures)
        if sidecar:
            sidecars.append(sidecar)
        tasks.append(_task_summary(tid, task) | {"images": saved, "failures": failures})

    for tid, task in outcome.unfinished.items():
        tasks.append(_task_summary(tid, task))

    _report_task_errors(tasks)
    for failure in all_failures:
        note(f"failed: {failure}")

    data: dict[str, Any] = {
        "images": all_images,
        "tasks": tasks,
        "failures": all_failures,
    }
    if sidecars:
        data["sidecar"] = sidecars[0] if len(sidecars) == 1 else sidecars
    if outcome.timed_out:
        unfinished = list(outcome.unfinished)
        data["timeout"] = {
            "unfinished": unfinished,
            "hint": f"任务仍在服务端执行；用 `seed-image aihubmix task wait {' '.join(unfinished)} --timeout 600` 继续等待",
        }

    code = _tasks_exit_code(tasks, timed_out=outcome.timed_out, partial=bool(all_failures))
    return Output(data, all_images, code)


def _save_task_outputs(
    http: httpx.Client,
    task_id: str,
    task: Mapping[str, Any],
    *,
    base_url: str,
    max_retries: int,
    out_dir: Path,
    prompt: str | None,
    request_body: Mapping[str, Any] | None,
    save_params: bool,
) -> tuple[list[str], list[str], str | None]:
    if str(task.get("status")) != "completed":
        return [], [], None

    outputs = task.get("output")
    if not isinstance(outputs, list) or not outputs:
        raise AppError(
            "empty_response",
            f"任务 {task_id} 已完成但没有返回任何图片",
            ExitCode.API,
            {"task": ap.without_payload(task)},
        )

    label = prompt or str(task.get("model") or task_id)
    uid = short_uid()
    fallback_fmt = str(request_body.get("output_format") or "") if request_body else None

    paths: list[Path] = []
    failures: list[str] = []
    for idx, item in enumerate(outputs, start=1):
        if not isinstance(item, Mapping):
            failures.append(f"第 {idx} 张：返回项格式异常")
            continue
        try:
            payload, ext = _extract_bytes(
                http,
                item,
                base_url=base_url,
                max_retries=max_retries,
                fallback_ext=fallback_fmt,
            )
        except AppError as exc:
            failures.append(f"第 {idx} 张：{exc.message}")
            continue
        paths.append(write_image(out_dir, label, idx, uid, ext, payload))

    if not paths:
        raise AppError(
            "save_failed",
            f"任务 {task_id} 的图片全部获取失败：{'；'.join(failures)}",
            ExitCode.API,
            {"task_id": task_id, "failures": failures},
        )

    sidecar: Path | None = None
    if save_params:
        sidecar = write_sidecar(
            out_dir,
            label,
            uid,
            {
                "endpoint": ap.GENERATION_PATH,
                "task_id": task_id,
                "request": ap.without_payload(request_body) if request_body else None,
                "task": ap.without_payload(task),
                "images": [p.name for p in paths],
                "failures": failures,
            },
        )
    return [str(p) for p in paths], failures, (str(sidecar) if sidecar else None)


def _extract_bytes(
    http: httpx.Client,
    item: Mapping[str, Any],
    *,
    base_url: str,
    max_retries: int,
    fallback_ext: str | None,
) -> tuple[bytes, str]:
    b64 = item.get("b64_json")
    if isinstance(b64, str) and b64:
        try:
            payload = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise AppError("bad_b64_payload", f"b64_json 解码失败：{exc}", ExitCode.API) from None
        ext = ap.ext_for_output(fallback=fallback_ext)
        return payload, ext

    content_url = item.get("content_url")
    if isinstance(content_url, str) and content_url:
        payload, content_type = api.download_content(
            http,
            content_url,
            base_url=base_url,
            max_retries=max_retries,
        )
        ext = ap.ext_for_output(content_type=content_type, content_url=content_url, fallback=fallback_ext)
        return payload, ext

    raise AppError("no_image_payload", "返回项既没有 b64_json 也没有 content_url", ExitCode.API)


def _task_summary(task_id: str, task: Mapping[str, Any]) -> dict[str, Any]:
    outputs = task.get("output")
    summary: dict[str, Any] = {
        "task_id": str(task.get("id") or task_id),
        "status": str(task.get("status") or "unknown"),
        "model": task.get("model"),
        "outputs_count": len(outputs) if isinstance(outputs, list) else 0,
    }
    if task.get("error"):
        summary["error"] = task["error"]
    return summary


def _report_task_errors(tasks: Sequence[Mapping[str, Any]]) -> None:
    for task in tasks:
        if task.get("error"):
            note(f"task {task['task_id']} {task['status']}: {json.dumps(task['error'], ensure_ascii=False)}")


def _tasks_exit_code(tasks: Sequence[Mapping[str, Any]], *, timed_out: bool, partial: bool) -> int:
    if timed_out:
        return ExitCode.TIMEOUT
    statuses = {task.get("status") for task in tasks}
    if FAILED in statuses:
        return ExitCode.JOB_FAILED
    if CANCELLED in statuses:
        return ExitCode.JOB_CANCELED
    if partial:
        return ExitCode.PARTIAL
    return ExitCode.OK
