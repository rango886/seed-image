"""启动时整理代理相关的环境变量。

httpx / httpx2 解析 NO_PROXY 时认不出带方括号的 IPv6（比如 `[::1]`），
会把它拼成 `all://*[::1]`，创建 Client 时直接抛 InvalidURL。
这里在任何 HTTP 客户端创建之前，把 `[addr]` 改成 `addr`。
"""

from __future__ import annotations

import os

_NO_PROXY_KEYS = ("NO_PROXY", "no_proxy")


def normalize_no_proxy(value: str) -> str:
    """去掉每一项外面的 IPv6 方括号，保留 `/前缀长度`，顺带去重。"""
    items: list[str] = []
    for raw in value.split(","):
        item = raw.strip()
        if item.startswith("["):
            addr, sep, rest = item[1:].partition("]")
            # 只认 `[addr]` 和 `[addr]/nn`；`[addr]:port` 这类 httpx 本来就不支持，整项跳过
            if sep and (rest == "" or rest.startswith("/")):
                item = addr + rest
            else:
                continue
        if item and item not in items:
            items.append(item)
    return ",".join(items)


def sanitize_proxy_env(environ: os._Environ[str] | dict[str, str] | None = None) -> None:
    env = os.environ if environ is None else environ
    for key in _NO_PROXY_KEYS:
        value = env.get(key)
        if value and "[" in value:
            env[key] = normalize_no_proxy(value)
