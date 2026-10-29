"""文件命名。纯函数。

Windows 是主要目标平台，所以非法字符、保留名、路径长度都必须处理；
中文保留（截断按字符数，不按字节）。
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
SLUG_LIMIT = 20


def short_uid() -> str:
    """8 位随机标识。全长 uuid + 中文文件名容易顶到 Windows 260 字符上限。"""
    return uuid.uuid4().hex[:8]


def slug(text: str, limit: int = SLUG_LIMIT) -> str:
    """取 text 前 limit 个字符做文件名片段，去掉不适合文件名的字符。"""
    cleaned = _ILLEGAL.sub("", text).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)[:limit].strip().strip(".")
    if not cleaned or cleaned.upper() in _RESERVED:
        return f"_{cleaned}" if cleaned else "untitled"
    return cleaned


def image_name(prompt: str, index: int, uid: str, ext: str) -> str:
    """`生成个城市夜景-01-1a2b3c4d.png`（index 从 1 开始）。"""
    return f"{slug(prompt)}-{index:02d}-{uid}.{ext.lstrip('.')}"


def sidecar_name(prompt: str, uid: str) -> str:
    """与一批图片同源的参数记录文件名。"""
    return f"{slug(prompt)}-{uid}.json"


def output_name(job_id: str, node_id: str, filename: str) -> str:
    """ComfyUI 输出落盘名：带 job/node 前缀，避免不同任务同名互相覆盖。"""
    return f"{job_id[:8]}-{node_id}-{slug(Path(filename).stem, 60)}{Path(filename).suffix}"


def unique_path(directory: Path, name: str) -> Path:
    """在 directory 下取一个不存在的路径；重名时追加 -1、-2……"""
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for i in range(1, 1000):
        candidate = directory / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"{directory} 下 {name} 的候选名已用尽")
