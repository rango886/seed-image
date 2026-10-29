"""`seed-image config` 子命令：查看配置最终生效的值和来源。"""

from __future__ import annotations

from typing import Any

import typer

from .. import config
from ..render import Output
from .context import FormatOption, resolve_format, run

config_app = typer.Typer(no_args_is_help=True, help="查看用户配置（文件默认在 ~/.config/seed-image/config.toml）。")

TEMPLATE = """\
[comfyui]
server = "http://127.0.0.1:8188"

[openai]
base_url = "https://api.openai.com/v1"
api_key  = "sk-..."
model    = "gpt-image-2.5-flare"

[gemini]
base_url = "https://generativelanguage.googleapis.com"   # 不含 /v1beta
api_key  = "..."
model    = "gemini-3.1-flash-image"
"""


@config_app.command("show", help="列出每个设置项最终生效的值与来源（参数之外：环境变量 > 配置文件 > 默认值）。")
def show(ctx: typer.Context, fmt: FormatOption = None) -> None:
    output_format = resolve_format(ctx, fmt)

    def produce() -> Output:
        path = config.config_path()
        loaded = config.load_config(path)
        settings: dict[str, Any] = {}
        lines = [f"# config: {path}" + ("" if path.exists() else "（不存在）")]
        for setting in config.SETTINGS:
            found = config.lookup(setting, loaded)
            shown = config.mask(found.value) if setting.secret and found.value else found.value
            settings[setting.key] = {
                "value": shown, "source": found.source, "env": setting.env, "default": setting.default,
            }
            where = f"{found.source} {found.origin}" if found.source == config.ENV else found.source
            lines.append(f"{setting.key} = {shown if shown is not None else '<未设置>'}  ({where})")
        return Output({"path": str(path), "exists": path.exists(), "settings": settings}, lines)

    run(output_format, produce)


@config_app.command("path", help="打印配置文件路径；加 --template 额外打印一份配置模板。")
def path_cmd(
    ctx: typer.Context,
    template: bool = typer.Option(False, "--template", help="同时输出配置模板"),
    fmt: FormatOption = None,
) -> None:
    output_format = resolve_format(ctx, fmt)

    def produce() -> Output:
        path = config.config_path()
        data: dict[str, Any] = {"path": str(path), "exists": path.exists()}
        lines = [str(path)]
        if template:
            data["template"] = TEMPLATE
            lines.append(TEMPLATE)
        return Output(data, lines)

    run(output_format, produce)
