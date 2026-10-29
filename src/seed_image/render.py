"""命令结果的唯一输出出口。

规则（给 agent 用的 CLI 必须守住）：
- stdout 只放最终结果；--format json 时 stdout 恰好是一个可解析的 JSON 对象。
- 进度、日志、提示一律走 stderr。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .errors import AppError, ExitCode

TEXT = "text"
JSON = "json"
FORMATS = (TEXT, JSON)


@dataclass(frozen=True)
class Output:
    """一次成功的命令结果：data 供 json 格式，lines 供 text 格式。

    exit_code 非 0 表示“有结果但不完全顺利”（例如部分产物失败、等待超时），
    这类情况仍然要把已得到的结果交给调用方，而不是只丢一个错误。
    """

    data: Mapping[str, Any] = field(default_factory=dict)
    lines: Sequence[str] = ()
    exit_code: int = ExitCode.OK


def emit_output(out: Output, fmt: str) -> None:
    if fmt == JSON:
        _write(json.dumps({"ok": True, **out.data}, ensure_ascii=False, indent=2))
    else:
        for line in out.lines:
            _write(line)


def emit_error(err: AppError, fmt: str) -> None:
    if fmt == JSON:
        _write(json.dumps({"ok": False, "error": err.as_dict()}, ensure_ascii=False, indent=2))
    else:
        note(f"error[{err.code}] {err.message}")
        if err.details:
            note(json.dumps(dict(err.details), ensure_ascii=False, indent=2))


def note(message: str) -> None:
    """进度/诊断信息。永远走 stderr，绝不污染 stdout。"""
    print(message, file=sys.stderr, flush=True)


def _write(text: str) -> None:
    print(text, file=sys.stdout, flush=True)
