"""CLI 层的公共约定：全局选项的解析、以及命令的统一收尾。

每个命令体只做三件事：解析参数 → 调用下层 → 交给 run() 输出。
错误路径只有一个出口，因此不可能出现某个子命令忘了处理 --format 或退出码。
"""

from __future__ import annotations

import threading
from typing import Annotated, Any, Callable

import typer

from ..errors import AppError, ExitCode
from ..render import FORMATS, JSON, TEXT, Output, emit_error, emit_output, note

FormatOption = Annotated[
    str | None,
    typer.Option("--format", help=f"输出格式：{'/'.join(FORMATS)}（默认 text）", show_default=False),
]
ServerOption = Annotated[
    str | None,
    typer.Option("--server", help="ComfyUI 服务器地址", show_default=False),
]


def store(ctx: typer.Context, **values: object) -> None:
    """把上层命令的全局选项存进 ctx，供子命令回退取用。"""
    ctx.ensure_object(dict)
    ctx.obj.update({k: v for k, v in values.items() if v is not None})


def resolve_format(ctx: typer.Context, override: str | None) -> str:
    """--format 可以写在任意一层；就近者优先。"""
    fmt = override or (ctx.obj or {}).get("format") or TEXT
    if fmt not in FORMATS:
        emit_error(AppError("invalid_format", f"--format 只能是 {'/'.join(FORMATS)}，实际得到 {fmt!r}", ExitCode.USAGE), TEXT)
        raise typer.Exit(ExitCode.USAGE)
    return str(fmt)


def resolve(ctx: typer.Context, key: str, override: str | None, default: str) -> str:
    return override or (ctx.obj or {}).get(key) or default


def run(fmt: str, produce: Callable[[], Output]) -> None:
    """执行命令主体，把结果、失败或中断翻译成 (stdout 输出 + 退出码)。"""
    try:
        out = _await(produce)
    except AppError as err:
        emit_error(err, fmt)
        raise typer.Exit(err.exit_code) from None
    except KeyboardInterrupt:
        note("interrupted：已提交的任务仍在服务端执行；用 `seed-image comfyui job list` 找回 job id")
        raise typer.Exit(ExitCode.INTERRUPTED) from None
    emit_output(out, fmt)
    raise typer.Exit(out.exit_code)


def _await(produce: Callable[[], Output]) -> Output:
    """在后台线程里跑主体，主线程只等待 —— 这样 Ctrl+C 一定能立刻生效。

    Windows 上阻塞的 socket 读取要等到超时才会抛 KeyboardInterrupt，等待一个
    daemon 线程则是可中断的；进程退出时该线程随之消失。
    """
    box: dict[str, Any] = {}
    done = threading.Event()

    def target() -> None:
        try:
            box["out"] = produce()
        except BaseException as exc:  # 原样带回主线程，由上面统一翻译
            box["exc"] = exc
        finally:
            done.set()

    threading.Thread(target=target, daemon=True, name="seed-image-main").start()
    while not done.wait(0.2):  # 带超时的等待才能在 Windows 上被 Ctrl+C 打断
        pass
    if "exc" in box:
        raise box["exc"]
    return box["out"]


__all__ = ["FormatOption", "ServerOption", "JSON", "Output", "resolve", "resolve_format", "run", "store"]
