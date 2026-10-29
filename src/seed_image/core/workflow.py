"""API 格式 workflow 的加载、校验与自省。纯函数（load 只读文件）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator, Mapping, NamedTuple

from ..errors import input_error


class InputSlot(NamedTuple):
    """workflow 里一个可以被 --values 直接赋值的字面量输入。"""

    path: str
    class_type: str
    value: Any


def load_workflow(path: Path) -> dict[str, Any]:
    """读入并校验 API 格式 workflow。UI 导出格式会被明确拒绝。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise input_error("workflow_unreadable", f"无法读取 workflow 文件 {path}: {exc}") from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise input_error("workflow_invalid_json", f"{path} 不是合法 JSON：{exc}") from None
    return validate_workflow(data, path)


def validate_workflow(data: Any, path: Path) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise input_error("workflow_invalid", f"{path} 顶层应为对象（节点 id → 节点），实际为 {type(data).__name__}")
    if "nodes" in data and "links" in data:
        raise input_error(
            "workflow_format_ui",
            f"{path} 是 UI 导出格式（含 nodes/links）。请在 ComfyUI 中用“导出 (API)”重新导出。",
        )
    if not data:
        raise input_error("workflow_invalid", f"{path} 里没有任何节点")
    for node_id, node in data.items():
        if not isinstance(node, Mapping) or "class_type" not in node:
            raise input_error(
                "workflow_invalid",
                f"{path} 节点 {node_id} 缺少 class_type，看起来不是 API 格式 workflow",
            )
    return dict(data)


def inspect_inputs(workflow: Mapping[str, Any]) -> Iterator[InputSlot]:
    """列出所有字面量输入槽（跳过 ["3", 0] 形式的节点连线）。"""
    for node_id, node in workflow.items():
        class_type = str(node.get("class_type", "?"))
        inputs = node.get("inputs")
        if not isinstance(inputs, Mapping):
            continue
        for name, value in inputs.items():
            if _is_link(value):
                continue
            yield InputSlot(f"{node_id}.inputs.{name}", class_type, value)


def _is_link(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)
