"""`--multi`：把一个输入节点（通常是 LoadImage）按连线复制 N 份，接到多图口上。

思路就是模仿人在网页里的手工操作——"再拖一个 LoadImage 进来，连到下一个空口"：

1. 从源节点往下游走。碰到"能多接的口"就停下，记为扩口点；
   碰到普通口就把那个节点也算进这张图的"私有上游"，继续往下走。
2. 私有上游只保留"最终通向扩口点"的那部分（比如 LoadImage → ImageScale → 多图口），
   只通向别处的支路（比如拿第一张图的尺寸去建 latent）不复制，仍由原节点独占。
3. 每多一张图，复制一份私有上游，再在每个扩口点所在那一族里，
   取原来那个口之后的第一个空名字，接上新线。

"能多接的口"以服务端 /object_info 为准，两类：
- Autogrow（自动生长口）：key 形如 `images.image_1`，名字表来自 template.names 或 prefix+max；
  可以嵌在 DynamicCombo 里，此时 key 形如 `model.images.image_1`。
- 老式固定编号口：同一层里同类型的 `image1, image2, image3`。

拿不到 schema 时（离线 --dry-run），退回到猜：key 带 `.` 且以数字结尾才当作自动生长口。
串联式（ReferenceLatent 链）不支持，请在工作流里换成 ReferenceMultiLatent / BatchImagesNode。

全是纯函数：schema 以查询函数的形式注入，不碰网络。
"""

from __future__ import annotations

import copy
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Mapping, Sequence

from ..errors import input_error
from .values import Assignment

AUTOGROW = "COMFY_AUTOGROW_V3"
DYNAMIC_COMBO = "COMFY_DYNAMICCOMBO_V3"
GUESS_LIMIT = 100  # 与 ComfyUI Autogrow._MaxNames 一致

# class_type -> /object_info 里该节点的条目（含 "input"）；不认识返回 None
Schema = Callable[[str], Mapping[str, Any] | None]

_TRAILING_NUMBER = re.compile(r"^(.*?)(\d+)$")


@dataclass(frozen=True)
class Expansion:
    """一次复制的结果：copies 第一个是原节点，后面依次是副本；wired 是新接的 (节点, 口)。"""

    source: str
    copies: tuple[str, ...]
    wired: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _Edge:
    consumer: str
    key: str
    source: str
    index: int


# ---------------------------------------------------------------- 入口


def plan_multi(
    workflow: Mapping[str, Any],
    items: Sequence[Assignment],
    schema: Schema | None,
) -> tuple[dict[str, Any], list[Assignment], list[Expansion]]:
    """把 --multi 赋值按节点分组，复制节点，再把第 i 个值改写成指向第 i 份副本的普通赋值。

    同一节点可以有多个字段一起 --multi（比如 image 和 upload），但每个字段的个数必须相同。
    """
    groups: dict[str, dict[tuple[str, ...], list[Assignment]]] = defaultdict(lambda: defaultdict(list))
    for item in items:
        if len(item.path) < 2:
            raise input_error(
                "invalid_multi",
                f"--multi 的路径至少要有 节点id.字段：{item.source!r}",
                multi=item.source,
            )
        groups[item.path[0]][item.path[1:]].append(item)

    result: dict[str, Any] = dict(workflow)
    rewritten: list[Assignment] = []
    expansions: list[Expansion] = []
    for node_id, fields in groups.items():
        counts = {rest: len(values) for rest, values in fields.items()}
        if len(set(counts.values())) > 1:
            raise input_error(
                "multi_count_mismatch",
                f"节点 {node_id} 的多个 --multi 字段个数不一致："
                + "，".join(f"{'.'.join(k)}={v}" for k, v in counts.items()),
                node=node_id,
            )
        count = next(iter(counts.values()))
        result, expansion = expand(result, node_id, count - 1, schema)
        expansions.append(expansion)
        for values in fields.values():
            for copy_id, item in zip(expansion.copies, values):
                rewritten.append(Assignment((copy_id, *item.path[1:]), item.value, item.source))
    return result, rewritten, expansions


def expand(
    workflow: Mapping[str, Any],
    source: str,
    extra: int,
    schema: Schema | None,
) -> tuple[dict[str, Any], Expansion]:
    """把 source 节点（连同它的私有上游）再复制 extra 份，接到它下游的多图口上。"""
    if source not in workflow:
        raise input_error(
            "multi_node_not_found",
            f"--multi 指向的节点 {source} 在 workflow 中不存在",
            node=source,
            available=sorted(map(str, workflow))[:40],
        )
    if extra <= 0:
        return dict(workflow), Expansion(source, (source,), ())

    expansions, private = _trace(workflow, source, schema)
    result = copy.deepcopy(dict(workflow))
    copies = [source]
    wired: list[tuple[str, str]] = []
    for k in range(2, extra + 2):
        mapping = {node_id: _fresh_id(node_id, k, result) for node_id in sorted(private)}
        for node_id, new_id in mapping.items():
            result[new_id] = _clone(workflow[node_id], mapping)
        copies.append(mapping[source])
        for edge, family in expansions:
            inputs = result[edge.consumer]["inputs"]
            name = _next_free(family, edge, inputs, workflow)
            inputs[name] = [mapping[edge.source], edge.index]
            wired.append((edge.consumer, name))
    return result, Expansion(source, tuple(copies), tuple(wired))


# ---------------------------------------------------------------- 图遍历


def _trace(
    workflow: Mapping[str, Any], source: str, schema: Schema | None
) -> tuple[list[tuple[_Edge, list[str]]], set[str]]:
    """返回 (扩口点及其所在族, 私有上游节点集合)。"""
    outgoing: dict[str, list[_Edge]] = defaultdict(list)
    for edge in _edges(workflow):
        outgoing[edge.source].append(edge)

    reached = {source}
    expansions: list[tuple[_Edge, list[str]]] = []
    queue = [source]
    while queue:
        node_id = queue.pop()
        for edge in outgoing[node_id]:
            consumer = workflow[edge.consumer]
            family = slot_family(consumer, edge.key, schema)
            if family is not None:
                expansions.append((edge, family))
            elif edge.consumer not in reached:
                reached.add(edge.consumer)
                queue.append(edge.consumer)

    if not expansions:
        raise input_error(
            "multi_no_slot",
            f"节点 {source} 往下游找不到能多接的口（Autogrow 口，或 image1/image2/... 这种编号口）。"
            "可能这个工作流本来只吃一张图；如果多图是串联式（比如 ReferenceLatent 一个接一个），"
            "请在工作流里换成 ReferenceMultiLatent / BatchImagesNode 这类多图节点。",
            node=source,
            downstream=sorted(edge.consumer for edge in outgoing[source]),
        )

    # 只保留最终通向扩口点的那部分：从扩口点的上游出发，在 reached 里倒着走
    private: set[str] = set()
    stack = [edge.source for edge, _ in expansions]
    while stack:
        node_id = stack.pop()
        if node_id in private:
            continue
        private.add(node_id)
        stack.extend(
            value[0]
            for value in _inputs(workflow[node_id]).values()
            if _is_link(value) and value[0] in reached
        )

    tangled = sorted({edge.consumer for edge, _ in expansions} & private)
    if tangled:
        raise input_error(
            "multi_ambiguous",
            f"节点 {', '.join(tangled)} 既是多图口所在的节点，又在 {source} 的私有上游里，"
            "不知道该复制它还是给它加口。请简化这段连线。",
            node=source,
            tangled=tangled,
        )
    return expansions, private


def _edges(workflow: Mapping[str, Any]) -> Iterator[_Edge]:
    for node_id, node in workflow.items():
        for key, value in _inputs(node).items():
            if _is_link(value) and value[0] in workflow:
                yield _Edge(str(node_id), str(key), value[0], int(value[1]))


def _inputs(node: Any) -> Mapping[str, Any]:
    inputs = node.get("inputs") if isinstance(node, Mapping) else None
    return inputs if isinstance(inputs, Mapping) else {}


def _is_link(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)


def _clone(node: Mapping[str, Any], mapping: Mapping[str, str]) -> dict[str, Any]:
    cloned = copy.deepcopy(dict(node))
    inputs = cloned.get("inputs")
    if isinstance(inputs, dict):
        for key, value in inputs.items():
            if _is_link(value) and value[0] in mapping:
                inputs[key] = [mapping[value[0]], value[1]]
    return cloned


def _fresh_id(node_id: str, k: int, taken: Mapping[str, Any]) -> str:
    """副本 id：470 → 470_2。不用 `.`（--values 的路径分隔符），也不用 `:`（子图前缀）。"""
    candidate = f"{node_id}_{k}"
    suffix = 2
    while candidate in taken:
        candidate = f"{node_id}_{k}_{suffix}"
        suffix += 1
    return candidate


def _next_free(
    family: Sequence[str], edge: _Edge, inputs: Mapping[str, Any], workflow: Mapping[str, Any]
) -> str:
    """原来那个口之后、第一个空着的名字。放在后面，是为了保证命令行顺序 = 图的编号顺序。"""
    start = family.index(edge.key) + 1
    for name in family[start:]:
        if name not in inputs:
            return name
    class_type = workflow[edge.consumer].get("class_type", "?")
    raise input_error(
        "multi_slot_full",
        f"节点 {edge.consumer}（{class_type}）的 {edge.key} 那一族口已经接满了，最多 {len(family)} 个",
        node=edge.consumer,
        slot=edge.key,
        capacity=len(family),
    )


# ---------------------------------------------------------------- 口的分族


def slot_family(node: Mapping[str, Any], key: str, schema: Schema | None) -> list[str] | None:
    """key 能多接时，返回它所在那一族的全部合法名字（有序）；否则返回 None。"""
    info = schema(str(node.get("class_type"))) if schema is not None else None
    if not info:
        return _guess_family(key)
    specs = dict(_walk_specs(info.get("input") or {}, None, _inputs(node)))
    for full_id, (type_name, options) in specs.items():
        if type_name == AUTOGROW:
            names = [f"{full_id}.{name}" for name in _autogrow_names(options)]
            if key in names:
                return names
    return _numbered_family(specs, key)


def _walk_specs(
    section: Mapping[str, Any], prefix: str | None, node_inputs: Mapping[str, Any]
) -> Iterator[tuple[str, tuple[str, Mapping[str, Any]]]]:
    """展开 required/optional，下钻到 DynamicCombo 当前选中的那一项里。"""
    for group in ("required", "optional"):
        for input_id, spec in (section.get(group) or {}).items():
            full_id = f"{prefix}.{input_id}" if prefix else str(input_id)
            type_name, options = _spec_parts(spec)
            yield full_id, (type_name, options)
            if type_name == DYNAMIC_COMBO:
                selected = node_inputs.get(full_id)
                for option in options.get("options") or []:
                    if selected is None or option.get("key") == selected:
                        yield from _walk_specs(option.get("inputs") or {}, full_id, node_inputs)


def _spec_parts(spec: Any) -> tuple[str, Mapping[str, Any]]:
    if not isinstance(spec, (list, tuple)) or not spec:
        return "?", {}
    type_name = spec[0] if isinstance(spec[0], str) else "COMBO"
    options = spec[1] if len(spec) > 1 and isinstance(spec[1], Mapping) else {}
    return type_name, options


def _autogrow_names(options: Mapping[str, Any]) -> list[str]:
    template = options.get("template") or {}
    if "names" in template:
        return [str(name) for name in template["names"]]
    if "prefix" in template:
        return [f"{template['prefix']}{i}" for i in range(int(template.get("max", 10)))]
    return []


def _numbered_family(specs: Mapping[str, tuple[str, Mapping[str, Any]]], key: str) -> list[str] | None:
    """老式口：同一层、同类型、同前缀加数字的一组（至少两个才算一族）。"""
    match = _TRAILING_NUMBER.match(key)
    if match is None or key not in specs:
        return None
    base, type_name = match.group(1), specs[key][0]
    members = []
    for full_id, (other_type, _) in specs.items():
        other = _TRAILING_NUMBER.match(full_id)
        if other and other.group(1) == base and other_type == type_name:
            members.append((int(other.group(2)), full_id))
    return [name for _, name in sorted(members)] if len(members) > 1 else None


def _guess_family(key: str) -> list[str] | None:
    """没有 schema 时的退路：只认 `组名.名字+数字` 这种自动生长口的样子。"""
    match = _TRAILING_NUMBER.match(key)
    if match is None or "." not in key:
        return None
    base, number = match.group(1), int(match.group(2))
    return [f"{base}{i}" for i in range(number, number + GUESS_LIMIT)]
