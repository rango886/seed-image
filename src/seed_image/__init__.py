"""seed-image 入口。"""

from __future__ import annotations

import sys

from .cli.app import app
from .netenv import sanitize_proxy_env


def main() -> None:
    _force_utf8()
    sanitize_proxy_env()
    app()


def _force_utf8() -> None:
    """Windows 控制台默认不是 UTF-8，中文提示词会在输出时炸掉。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    main()
