"""`seed-image openai-image` 子命令。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from .. import config
from ..core.imageparams import build_request, with_images
from ..errors import ExitCode
from ..images import client as api
from ..images.save import Saved, save_response
from ..render import Output, note
from .context import FormatOption, resolve_format, run

image_app = typer.Typer(
    no_args_is_help=True,
    help=(
        "调用 OpenAI 兼容的图片接口。地址与密钥取自环境变量 "
        f"{config.OPENAI_BASE_URL.env} / {config.OPENAI_API_KEY.env}，"
        f"或配置文件里的 {config.OPENAI_BASE_URL.key} / {config.OPENAI_API_KEY.key}。"
    ),
)

MODEL_HELP = f"模型名（默认取配置 {config.OPENAI_MODEL.key}，未配置则 {config.OPENAI_MODEL.default}）"

GENERATE_EPILOG = """\b
示例：
  seed-image openai-image generate --prompt "生成个城市夜景" --number 2
"""
EDIT_EPILOG = """\b
示例：
  seed-image openai-image edit --prompt "把图一的帽子戴在图二的人物上，用图三的画风" \\
    --input-image a.png --input-image b.png --input-image c.png
"""


@image_app.command("generate", epilog=GENERATE_EPILOG, help="文生图，返回落盘的图片路径列表。")
def generate(
    ctx: typer.Context,
    prompt: Annotated[str, typer.Option("--prompt", help="生成提示词")],
    model: Annotated[str | None, typer.Option("--model", help=MODEL_HELP, show_default=False)] = None,
    size: Annotated[str, typer.Option("--size")] = "auto",
    quality: Annotated[str, typer.Option("--quality")] = "auto",
    background: Annotated[str, typer.Option("--background", help="背景，原样透传给接口")] = "auto",
    number: Annotated[str, typer.Option("--number", help="生成张数")] = "1",
    output_path: Annotated[Path, typer.Option("--output-path", help="图片落盘目录")] = Path("."),
    save_params: Annotated[bool, typer.Option("--save-params/--no-save-params", help="是否额外写一份参数记录 json（默认不写）")] = False,
    timeout: Annotated[float, typer.Option("--timeout", help="单次请求超时秒数")] = 300.0,
    max_retries: Annotated[int, typer.Option("--max-retries")] = 2,
    fmt: FormatOption = None,
) -> None:
    output_format = resolve_format(ctx, fmt)

    def produce() -> Output:
        request = build_request(
            prompt=prompt, model=config.value(config.OPENAI_MODEL, model) or "", size=size, quality=quality,
            background=background, number=number,
        )
        client = api.make_client(timeout=timeout, max_retries=max_retries)
        response = api.generate(client, request)
        return _saved_output(
            save_response(
                response, request, output_path,
                endpoint="/images/generations", save_params=save_params,
            )
        )

    run(output_format, produce)


@image_app.command("edit", epilog=EDIT_EPILOG, help="按提示词编辑/融合本地图片（最多 16 张），返回落盘的图片路径列表。")
def edit(
    ctx: typer.Context,
    prompt: Annotated[str, typer.Option("--prompt", help="编辑指令")],
    input_image: Annotated[list[Path], typer.Option("--input-image", help="本地参考图，可重复；按 base64 传入接口")],
    model: Annotated[str | None, typer.Option("--model", help=MODEL_HELP, show_default=False)] = None,
    size: Annotated[str, typer.Option("--size")] = "auto",
    quality: Annotated[str, typer.Option("--quality")] = "auto",
    background: Annotated[str, typer.Option("--background", help="背景，原样透传给接口")] = "auto",
    number: Annotated[str, typer.Option("--number", help="生成张数")] = "1",
    output_path: Annotated[Path, typer.Option("--output-path", help="图片落盘目录")] = Path("."),
    save_params: Annotated[bool, typer.Option("--save-params/--no-save-params", help="是否额外写一份参数记录 json（默认不写）")] = False,
    timeout: Annotated[float, typer.Option("--timeout", help="单次请求超时秒数")] = 300.0,
    max_retries: Annotated[int, typer.Option("--max-retries")] = 2,
    fmt: FormatOption = None,
) -> None:
    output_format = resolve_format(ctx, fmt)

    def produce() -> Output:
        request = with_images(
            build_request(
                prompt=prompt, model=config.value(config.OPENAI_MODEL, model) or "", size=size, quality=quality,
                background=background, number=number,
            ),
            tuple(input_image),
        )
        client = api.make_client(timeout=timeout, max_retries=max_retries)
        response = api.edit(client, request)
        return _saved_output(
            save_response(
                response, request, output_path,
                endpoint="/images/edits", save_params=save_params,
            )
        )

    run(output_format, produce)


def _saved_output(saved: Saved) -> Output:
    data = {
        "images": [str(p) for p in saved.images],
        "sidecar": str(saved.sidecar) if saved.sidecar else None,
        "failures": list(saved.failures),
    }
    lines = [str(p) for p in saved.images]
    for failure in saved.failures:
        note(f"failed: {failure}")
    return Output(data, lines, ExitCode.PARTIAL if saved.failures else ExitCode.OK)
