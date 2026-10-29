"""从任务快照里取出产物，并落盘。

ComfyUI 的 outputs 形如 {node_id: {media_type: [item, ...]}}：
item 是 {filename, subfolder, type} 的文件条目，也可能是 text 下的裸字符串。
两者都不丢：文件下载到本地，文本直接进结果。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, NamedTuple

import httpx

from ..core.naming import output_name, unique_path
from . import client as api

# 'animated' 是布尔标记而非产物列表。
_NON_ITEM_KEYS = frozenset({"animated"})


class FileOutput(NamedTuple):
    node_id: str
    media_type: str
    filename: str
    subfolder: str
    type: str


class TextOutput(NamedTuple):
    node_id: str
    text: str


@dataclass(frozen=True)
class Products:
    files: tuple[FileOutput, ...] = ()
    texts: tuple[TextOutput, ...] = ()


def collect(job: Mapping[str, Any]) -> Products:
    files: list[FileOutput] = []
    texts: list[TextOutput] = []
    outputs = job.get("outputs") or {}
    for node_id, node_outputs in outputs.items():
        if not isinstance(node_outputs, Mapping):
            continue
        for media_type, items in node_outputs.items():
            if media_type in _NON_ITEM_KEYS or not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, Mapping) and item.get("filename"):
                    files.append(
                        FileOutput(
                            str(node_id),
                            str(media_type),
                            str(item["filename"]),
                            str(item.get("subfolder") or ""),
                            str(item.get("type") or "output"),
                        )
                    )
                elif isinstance(item, str):
                    texts.append(TextOutput(str(node_id), item))
    return Products(tuple(files), tuple(texts))


def download(
    http: httpx.Client,
    job_id: str,
    files: tuple[FileOutput, ...],
    directory: Path,
    *,
    concurrency: int = 4,
) -> list[Path]:
    """并发下载产物，返回落盘路径（顺序与 files 一致）。"""
    if not files:
        return []
    directory.mkdir(parents=True, exist_ok=True)
    targets = [unique_path(directory, output_name(job_id, f.node_id, f.filename)) for f in files]
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(lambda pair: _save(http, *pair), zip(files, targets)))
    return targets


def _save(http: httpx.Client, item: FileOutput, target: Path) -> None:
    payload = api.fetch_view(
        http, filename=item.filename, subfolder=item.subfolder, type_=item.type
    )
    target.write_bytes(payload)
