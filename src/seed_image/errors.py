"""失败的统一表示。

约定：CLI 里任何可预期的失败都表现为一个 AppError，由 cli.run 在最外层
翻译成 (结构化输出 + 退出码)。除此之外的异常都是 bug，允许它带栈崩出来。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


class ExitCode:
    """退出码契约 —— 调用方（agent）靠它分支，不必解析文本。"""

    OK = 0
    USAGE = 2  # Typer 保留：命令行参数用法错误
    INPUT = 3  # 本地输入有问题（文件不存在、路径写错、workflow 格式不对）
    API = 4  # 上游接口返回错误
    TIMEOUT = 5  # 等待超时，任务本身仍然存在
    JOB_FAILED = 6  # 任务在服务端执行失败
    JOB_CANCELED = 7  # 任务被取消
    PARTIAL = 8  # 部分成功（例如 n=4 只落盘了 3 张）
    INTERRUPTED = 130  # 用户 Ctrl+C（128 + SIGINT，与 shell 惯例一致）


@dataclass(frozen=True)
class AppError(Exception):
    """一次失败：机器可读的 code、给人看的 message、以及排错所需的上下文。"""

    code: str
    message: str
    exit_code: int = ExitCode.API
    details: Mapping[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - 仅用于未捕获时的可读性
        return f"{self.code}: {self.message}"

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = dict(self.details)
        return payload


def input_error(code: str, message: str, **details: Any) -> AppError:
    return AppError(code, message, ExitCode.INPUT, details)
