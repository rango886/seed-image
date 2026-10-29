"""`seed-image gemini-image` 子命令。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Sequence

import typer

from .. import config
from ..core import geminiparams as gp
from ..core.naming import short_uid
from ..errors import AppError, ExitCode
from ..gemini import client as api
from ..images.save import write_image, write_sidecar
from ..render import Output, note
from .context import FormatOption, resolve_format, run

gemini_app = typer.Typer(
    no_args_is_help=True,
    help=(
        "调用 Gemini 原生 generateContent 出图（Nano Banana）。密钥取自环境变量 "
        f"{config.GEMINI_API_KEY.env} 或配置 {config.GEMINI_API_KEY.key}；"
        f"地址取自 {config.GEMINI_BASE_URL.env} 或配置 {config.GEMINI_BASE_URL.key}。"
    ),
)

GENERATE_EPILOG = """\b
示例：
  seed-image gemini-image generate --prompt "写着「开业大吉」的红色海报" --aspect-ratio 3:4
"""
EDIT_EPILOG = """\b
示例：
  seed-image gemini-image edit --prompt "把第一张图的帽子戴到第二张图的人头上" \\
    --input-image a.png --input-image b.png
"""

ModelOption = Annotated[
    str | None,
    typer.Option(
        "--model",
        help=f"模型名（默认取配置 {config.GEMINI_MODEL.key}，未配置则 {config.GEMINI_MODEL.default}）",
        show_default=False,
    ),
]
AspectOption = Annotated[
    str | None,
    typer.Option("--aspect-ratio", help="如 1:1 / 3:4 / 16:9 / 21:9；不传则跟随输入图或上游默认", show_default=False),
]
SizeOption = Annotated[
    str | None,
    typer.Option("--image-size", help="512 / 1K / 2K / 4K（受模型档位限制）；不传为上游默认 1K", show_default=False),
]
NumberOption = Annotated[int, typer.Option("--number", min=1, help="张数；每张一次请求，按 --concurrency 并发")]
ConcurrencyOption = Annotated[int, typer.Option("--concurrency", min=1, help="--number > 1 时的并发请求数")]
OutputOption = Annotated[Path, typer.Option("--output-path", help="图片落盘目录")]
SaveParamsOption = Annotated[
    bool, typer.Option("--save-params/--no-save-params", help="是否额外写一份参数记录 json（默认不写）")
]
TimeoutOption = Annotated[float, typer.Option("--timeout", help="单次请求超时秒数")]
RetriesOption = Annotated[int, typer.Option("--max-retries", help="网络错误 / 429 / 5xx 的重试次数")]


@gemini_app.command("generate", epilog=GENERATE_EPILOG, help="文生图，返回落盘的图片路径列表。")
def generate(
    ctx: typer.Context,
    prompt: Annotated[str, typer.Option("--prompt", help="生成提示词")],
    model: ModelOption = None,
    aspect_ratio: AspectOption = None,
    image_size: SizeOption = None,
    number: NumberOption = 1,
    concurrency: ConcurrencyOption = 4,
    output_path: OutputOption = Path("."),
    save_params: SaveParamsOption = False,
    timeout: TimeoutOption = 300.0,
    max_retries: RetriesOption = 2,
    fmt: FormatOption = None,
) -> None:
    output_format = resolve_format(ctx, fmt)
    run(output_format, lambda: _produce(
        prompt=prompt, inputs=(), model=model, aspect_ratio=aspect_ratio, image_size=image_size,
        number=number, concurrency=concurrency, output_path=output_path, save_params=save_params,
        timeout=timeout, max_retries=max_retries,
    ))


@gemini_app.command(
    "edit", epilog=EDIT_EPILOG,
    help=f"按提示词编辑/融合本地图片（最多 {gp.MAX_IMAGES} 张，顺序即“第一张/第二张”），返回落盘的图片路径列表。",
)
def edit(
    ctx: typer.Context,
    prompt: Annotated[str, typer.Option("--prompt", help="编辑指令")],
    input_image: Annotated[list[Path], typer.Option("--input-image", help="本地参考图，可重复；按 base64 内联，排在指令前")],
    model: ModelOption = None,
    aspect_ratio: AspectOption = None,
    image_size: SizeOption = None,
    number: NumberOption = 1,
    concurrency: ConcurrencyOption = 4,
    output_path: OutputOption = Path("."),
    save_params: SaveParamsOption = False,
    timeout: TimeoutOption = 300.0,
    max_retries: RetriesOption = 2,
    fmt: FormatOption = None,
) -> None:
    output_format = resolve_format(ctx, fmt)
    run(output_format, lambda: _produce(
        prompt=prompt, inputs=tuple(input_image), model=model, aspect_ratio=aspect_ratio,
        image_size=image_size, number=number, concurrency=concurrency, output_path=output_path,
        save_params=save_params, timeout=timeout, max_retries=max_retries,
    ))


def _produce(
    *,
    prompt: str,
    inputs: Sequence[Path],
    model: str | None,
    aspect_ratio: str | None,
    image_size: str | None,
    number: int,
    concurrency: int,
    output_path: Path,
    save_params: bool,
    timeout: float,
    max_retries: int,
) -> Output:
    body = gp.build_body(
        prompt=prompt, images=gp.read_images(inputs),
        aspect_ratio=aspect_ratio, image_size=image_size,
    )
    target = api.target()
    url = gp.endpoint(target.base_url, config.value(config.GEMINI_MODEL, model) or "")
    with api.make_client(target, timeout=timeout) as http:
        results = api.run_parallel(
            [lambda: api.call(http, url, body, max_retries=max_retries)] * number, concurrency
        )

    uid = short_uid()
    paths: list[Path] = []
    texts: list[str] = []
    failures: list[str] = []
    errors: list[AppError] = []
    replies: list[api.Reply] = []
    for attempt, result in enumerate(results, start=1):
        if isinstance(result, AppError):
            errors.append(result)
            failures.append(f"第 {attempt} 次请求：{result.message}")
            continue
        replies.append(result)
        parsed = gp.parse_response(result.response)
        texts.extend(parsed.texts)
        if not parsed.images:
            err = _no_image(parsed, result.response)
            errors.append(err)
            failures.append(f"第 {attempt} 次请求：{err.message}")
            continue
        for mime, payload in parsed.images:
            paths.append(write_image(output_path, prompt, len(paths) + 1, uid, gp.ext_for(mime), payload))

    for text in texts:
        note(f"model: {text}")
    if not paths:
        raise _all_failed(errors, number)

    sidecar = None
    if save_params:
        sidecar = write_sidecar(output_path, prompt, uid, {
            "endpoint": url,
            "request": gp.without_payload(replies[0].body),
            "responses": [gp.without_payload(r.response) for r in replies],
            "images": [p.name for p in paths],
            "texts": texts,
            "failures": failures,
        })
    for failure in failures:
        note(f"failed: {failure}")
    data = {
        "images": [str(p) for p in paths],
        "texts": texts,
        "sidecar": str(sidecar) if sidecar else None,
        "failures": failures,
    }
    return Output(data, [str(p) for p in paths], ExitCode.PARTIAL if failures else ExitCode.OK)


_BLOCK_HINTS = ("SAFETY", "PROHIBITED", "BLOCK", "SPII", "RECITATION")


def _no_image(parsed: gp.Parsed, response: Any) -> AppError:
    details = {
        "finish_reasons": list(parsed.finish_reasons),
        "block_reason": parsed.block_reason,
        "texts": list(parsed.texts),
        "response": gp.without_payload(response),
    }
    if parsed.block_reason:
        return AppError("blocked", f"请求被安全策略拦截：{parsed.block_reason}", ExitCode.API, details)
    reasons = ",".join(parsed.finish_reasons) or "未知"
    if any(hint in reason for reason in parsed.finish_reasons for hint in _BLOCK_HINTS):
        return AppError("blocked", f"出图被拦截：finishReason={reasons}", ExitCode.API, details)
    said = f"；模型回复：{parsed.texts[0][:200]}" if parsed.texts else ""
    return AppError("no_image", f"响应里没有图片（finishReason={reasons}）{said}", ExitCode.API, details)


def _all_failed(errors: Sequence[AppError], number: int) -> AppError:
    first = errors[0]
    if number == 1:
        return first
    return AppError(
        first.code,
        f"{number} 次请求全部失败；第一个错误：{first.message}",
        first.exit_code,
        {"errors": [e.as_dict() for e in errors]},
    )
