"""提交一个 workflow 所需的编排：上传引用的本地资源 → 打补丁 → 入队。

这一层是 client（单次调用）与 CLI（参数与输出）之间的唯一胶水。
上传动作以函数形式注入，所以 --dry-run 只要换一个“假上传”就能复用整条路径。
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import httpx

from ..core.fanout import Expansion, Schema, plan_multi
from ..core.values import Assignment, apply_assignments, resolve_uploads, uploads_in
from . import client as api

Upload = Callable[[Path], str]


@dataclass(frozen=True)
class Prepared:
    """打好补丁、可直接提交的 workflow，以及上传映射（本地路径 → 服务端引用名）。"""

    workflow: Mapping[str, Any]
    uploaded: Mapping[str, str]
    expanded: Sequence[Expansion] = ()


def uploader(http: httpx.Client) -> Upload:
    """真实上传器：把本地文件送进 ComfyUI 的 input 目录。"""
    return partial(api.upload_file, http)


def schema_from(http: httpx.Client) -> Schema:
    """按需查 /object_info，同一类型只查一次。"""
    cache: dict[str, Mapping[str, Any] | None] = {}

    def lookup(class_type: str) -> Mapping[str, Any] | None:
        if class_type not in cache:
            cache[class_type] = api.get_object_info(http, class_type)
        return cache[class_type]

    return lookup


def naming_only_uploader(path: Path) -> str:
    """--dry-run 用的假上传：不产生副作用，只标出会被替换成什么。"""
    return f"<upload:{path.name}>"


def prepare(
    workflow: Mapping[str, Any],
    assignments: Sequence[Assignment],
    upload: Upload,
    *,
    multi: Sequence[Assignment] = (),
    schema: Schema | None = None,
    concurrency: int = 4,
) -> Prepared:
    """按 --multi 复制节点 → 上传 @upload{...} 标记的文件 → 把所有赋值应用到 workflow。

    --multi 先展开成指向各个副本的普通赋值，排在 --values 前面，
    所以 --values 仍可以覆盖副本上的字段（比如 `470_2.inputs.xxx`）。
    """
    expansions: list[Expansion] = []
    if multi:
        workflow, rewritten, expansions = plan_multi(workflow, multi, schema)
        assignments = [*rewritten, *assignments]
    refs = _upload_all(assignments, upload, concurrency)
    resolved = [resolve_uploads(item, refs) for item in assignments]
    return Prepared(
        apply_assignments(workflow, resolved),
        {str(k): v for k, v in refs.items()},
        tuple(expansions),
    )


def submit(http: httpx.Client, workflow: Mapping[str, Any]) -> str:
    """入队并返回 job id。id 由本地生成，所以响应丢失也追得回任务。"""
    job_id = str(uuid.uuid4())
    api.submit_prompt(http, workflow, job_id=job_id, client_id=str(uuid.uuid4()))
    return job_id


def _upload_all(
    assignments: Sequence[Assignment], upload: Upload, concurrency: int
) -> dict[Path, str]:
    wanted = sorted({request.local_path for request in uploads_in(assignments)})
    if not wanted:
        return {}
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        return dict(zip(wanted, pool.map(upload, wanted)))
