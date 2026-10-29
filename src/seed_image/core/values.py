"""`--values "path.to.key=值"` 的解析与应用。全是纯函数。

值的类型推断：先按 JSON 字面量解析（`42` → int，`true` → bool，`[1,2]` → list），
解析失败则原样当字符串。ComfyUI 的 seed/steps 需要真数字，字符串会被节点拒绝，
所以推断是必需的而不是便利功能。

需要上传的本地文件用标记包围：`@upload{./cat.png}`。标记可以出现在任意层级
（裸值里、JSON 数组里），解析后由上层统一替换成服务端返回的引用名。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from ..errors import input_error

MARKER_OPEN = "@upload{"
MARKER_CLOSE = "}"

PathKey = str
NodePath = tuple[PathKey, ...]


@dataclass(frozen=True)
class UploadRequest:
    """一个待上传的本地文件占位符。"""

    local_path: Path


@dataclass(frozen=True)
class Assignment:
    """一条赋值：把 workflow 中 path 处的值替换为 value。"""

    path: NodePath
    value: Any
    source: str  # 原始 --values 文本，用于报错时定位


def parse_assignment(spec: str) -> Assignment:
    """把 `"75:74.inputs.text=一只柯基"` 解析成 Assignment。"""
    key, sep, raw = spec.partition("=")
    if not sep:
        raise input_error(
            "invalid_values",
            f"--values 缺少 '='：期望 path=value，实际得到 {spec!r}",
            value=spec,
        )
    return Assignment(parse_path(key.strip(), spec), parse_value(raw), spec)


def parse_path(key: str, source: str) -> NodePath:
    """`75:74.inputs.text` → ('75:74', 'inputs', 'text')；支持 `inputs.a[0]`。

    节点 id 里的 `:` 不是分隔符；只有 `.` 和 `[i]` 是。
    """
    if not key:
        raise input_error("invalid_values", f"--values 的 path 为空：{source!r}", value=source)
    segments: list[str] = []
    for part in key.split("."):
        head, *indexes = part.replace("]", "").split("[")
        if head:
            segments.append(head)
        segments.extend(indexes)
    return tuple(segments)


def parse_value(raw: str) -> Any:
    """JSON 字面量优先，失败降级为字符串；再把上传标记转成 UploadRequest。"""
    text = raw.strip()
    marked = _as_upload(text)
    if marked is not None:
        return marked
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    return map_leaves(parsed, lambda leaf: _as_upload(leaf) or leaf)


def _as_upload(text: Any) -> UploadRequest | None:
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if stripped.startswith(MARKER_OPEN) and stripped.endswith(MARKER_CLOSE):
        inner = stripped[len(MARKER_OPEN) : -len(MARKER_CLOSE)].strip()
        if not inner:
            raise input_error("invalid_values", f"上传标记为空：{text!r}", value=text)
        return UploadRequest(Path(inner).expanduser())
    return None


def map_leaves(value: Any, fn: Callable[[Any], Any]) -> Any:
    """对 dict/list 结构里的每个非容器叶子应用 fn，返回新结构。"""
    if isinstance(value, Mapping):
        return {k: map_leaves(v, fn) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [map_leaves(v, fn) for v in value]
    return fn(value)


def uploads_in(assignments: Sequence[Assignment]) -> Iterator[UploadRequest]:
    """列出所有赋值里出现的上传占位符（可能重复）。"""
    for item in assignments:
        yield from _walk_uploads(item.value)


def _walk_uploads(value: Any) -> Iterator[UploadRequest]:
    if isinstance(value, UploadRequest):
        yield value
    elif isinstance(value, Mapping):
        for v in value.values():
            yield from _walk_uploads(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _walk_uploads(v)


def resolve_uploads(item: Assignment, refs: Mapping[Path, str]) -> Assignment:
    """把赋值里的 UploadRequest 替换成 refs 给出的服务端引用名。"""

    def swap(leaf: Any) -> Any:
        if isinstance(leaf, UploadRequest):
            return refs[leaf.local_path]
        return leaf

    return Assignment(item.path, _map_all(item.value, swap), item.source)


def _map_all(value: Any, fn: Callable[[Any], Any]) -> Any:
    if isinstance(value, Mapping):
        return {k: _map_all(v, fn) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_map_all(v, fn) for v in value]
    return fn(value)


def apply_assignments(data: Mapping[str, Any], assignments: Sequence[Assignment]) -> dict[str, Any]:
    """依次应用赋值，返回新的 workflow；不修改入参。

    严格模式：路径必须已存在。打错字静默新建一个键，只会生成一张“没改过”的图，
    比直接报错糟糕得多。
    """
    result: Any = data
    for item in assignments:
        result = _set_path(result, item.path, item.value, item)
    return result


def _set_path(data: Any, path: NodePath, value: Any, item: Assignment) -> Any:
    if not path:
        return value
    key, rest = path[0], path[1:]
    if isinstance(data, Mapping):
        if key not in data:
            raise _path_error(item, key, sorted(map(str, data.keys())))
        return {**data, key: _set_path(data[key], rest, value, item)}
    if isinstance(data, list):
        index = _as_index(key, item)
        if not -len(data) <= index < len(data):
            raise _path_error(item, key, [f"0..{len(data) - 1}"])
        updated = list(data)
        updated[index] = _set_path(data[index], rest, value, item)
        return updated
    raise _path_error(item, key, [f"<{type(data).__name__} 不能继续下探>"])


def _as_index(key: str, item: Assignment) -> int:
    try:
        return int(key)
    except ValueError:
        raise _path_error(item, key, ["<需要整数下标>"]) from None


def _path_error(item: Assignment, key: str, available: Sequence[str]) -> Exception:
    return input_error(
        "path_not_found",
        f"路径 {'.'.join(item.path)} 在 workflow 中不存在（卡在 {key!r}）。"
        f" 用 `seed-image comfyui workflow inspect <workflow.json>` 查看可用路径。",
        values=item.source,
        stuck_at=key,
        available=list(available)[:40],
    )
