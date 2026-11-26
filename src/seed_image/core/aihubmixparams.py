"""AIHubMix 原生图片协议（/ai/v1/images/generations）的请求装配与 Schema 摘要。纯函数。"""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from ..errors import AppError, ExitCode, input_error
from .geminiparams import sniff_mime

# AIHubMix 文档限制整个 HTTP 请求体不超过 32 MiB。
MAX_REQUEST_BYTES = 32 * 1024 * 1024
GENERATION_PATH = "/ai/v1/images/generations"
IMAGE_FIELDS = ("auto", "image", "images")

_MIME_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}


def parse_kv(spec: str, *, option: str) -> tuple[tuple[str, ...], Any]:
    """把 `key=value`（支持 `a.b=value`）解析成 (路径元组, JSON/字符串值)。"""
    raw_key, sep, raw_val = spec.partition("=")
    if not sep:
        raise input_error(
            "invalid_param",
            f"{option} 缺少 '='：期望 key=value，实际得到 {spec!r}",
            value=spec,
        )
    key = raw_key.strip()
    if not key:
        raise input_error("invalid_param", f"{option} 的参数名为空：{spec!r}", value=spec)
    parts = tuple(seg.strip() for seg in key.split("."))
    if any(not seg for seg in parts):
        raise input_error("invalid_param", f"{option} 的路径格式不对：{spec!r}", value=spec)
    return parts, _parse_value(raw_val)


def _parse_value(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def set_nested(target: dict[str, Any], path: Sequence[str], value: Any, *, option: str) -> None:
    cursor: dict[str, Any] = target
    for key in path[:-1]:
        existing = cursor.get(key)
        if existing is None:
            child: dict[str, Any] = {}
            cursor[key] = child
            cursor = child
        elif isinstance(existing, dict):
            cursor = existing
        else:
            raise input_error(
                "invalid_param",
                f"{option} 无法在非对象字段 {key!r} 下设置子键 {'.'.join(path)}",
            )
    cursor[path[-1]] = value


def media_ref(item: str) -> str:
    """把 URL / data URI 原样保留，把本地图片文件读成 data URL。"""
    if item.startswith(("http://", "https://", "data:")):
        return item
    path = Path(item).expanduser()
    if not path.is_file():
        raise input_error("input_image_missing", f"输入图片不存在：{item}")
    payload = path.read_bytes()
    mime = sniff_mime(payload) or mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def build_request(
    *,
    prompt: str,
    model: str,
    input_images: Sequence[str] = (),
    image_field: str = "auto",
    mask: str | None = None,
    size: str | None = None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    quality: str | None = None,
    background: str | None = None,
    seed: int | None = None,
    negative_prompt: str | None = None,
    output_format: str | None = None,
    response_format: str | None = None,
    number: int | None = None,
    async_mode: bool = False,
    extras: Sequence[str] = (),
    params: Sequence[str] = (),
) -> dict[str, Any]:
    """装配 AIHubMix /ai/v1/images/generations 请求体。可选字段不传则不放入请求体。"""
    if not model:
        raise input_error("missing_model", "缺少模型名，请传 --model 或配置 aihubmix.model")
    if image_field not in IMAGE_FIELDS:
        raise input_error(
            "invalid_image_field",
            f"--image-field 只能是 {' / '.join(IMAGE_FIELDS)}，实际得到 {image_field!r}",
        )

    body: dict[str, Any] = {"model": model, "prompt": prompt}
    optional: list[tuple[str, Any]] = [
        ("n", number),
        ("size", size),
        ("aspect_ratio", aspect_ratio),
        ("resolution", resolution),
        ("quality", quality),
        ("background", background),
        ("seed", seed),
        ("negative_prompt", negative_prompt),
        ("output_format", output_format),
        ("response_format", response_format),
    ]
    for key, value in optional:
        if value is not None:
            body[key] = value
    if async_mode:
        body["async"] = True

    if mask is not None:
        body["mask"] = media_ref(mask)

    if input_images:
        refs = [media_ref(item) for item in input_images]
        if image_field == "image":
            if len(refs) > 1:
                raise input_error("too_many_images", "--image-field image 时只能传 1 张输入图片")
            body["image"] = refs[0]
        elif image_field == "images":
            body["images"] = refs
        else:
            if len(refs) == 1:
                body["image"] = refs[0]
            else:
                body["images"] = refs

    if extras:
        extra_obj: dict[str, Any] = {}
        for spec in extras:
            path, value = parse_kv(spec, option="--extra")
            set_nested(extra_obj, path, value, option="--extra")
        body["extra"] = extra_obj

    for spec in params:
        path, value = parse_kv(spec, option="--param")
        set_nested(body, path, value, option="--param")

    encoded_size = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    if encoded_size > MAX_REQUEST_BYTES:
        mb = encoded_size / (1024 * 1024)
        raise input_error(
            "request_too_large",
            f"请求体约 {mb:.1f} MiB，超过 AIHubMix 32 MiB 上限，请缩小或减少输入图片",
        )
    return body


def without_payload(value: Any) -> Any:
    """脱敏请求/响应里的 base64 数据与 data URI，用于 sidecar / dry-run / 报错。"""
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key == "b64_json" and isinstance(item, str) and item:
                out[key] = f"<{len(item)} 字符 base64>"
            else:
                out[key] = without_payload(item)
        return out
    if isinstance(value, list):
        return [without_payload(item) for item in value]
    if isinstance(value, str) and value.startswith("data:") and ";base64," in value:
        header, _, payload = value.partition(";base64,")
        return f"<{header};base64, {len(payload)} 字符>"
    return value


def ext_for_output(
    *,
    content_type: str | None = None,
    content_url: str | None = None,
    fallback: str | None = None,
) -> str:
    if content_type:
        mime = content_type.split(";", 1)[0].strip().lower()
        if mime in _MIME_EXT:
            return _MIME_EXT[mime]
        guessed = mimetypes.guess_extension(mime)
        if guessed:
            return guessed.lstrip(".")
    if content_url:
        suffix = Path(urlparse(content_url).path).suffix.lstrip(".").lower()
        if suffix in ("png", "jpg", "jpeg", "webp", "gif"):
            return "jpg" if suffix == "jpeg" else suffix
    if fallback:
        cleaned = fallback.lower().lstrip(".")
        return "jpg" if cleaned == "jpeg" else cleaned
    return "png"


def summarize_models(payload: Mapping[str, Any], *, keyword: str | None = None) -> list[dict[str, Any]]:
    """精简 /api/v1/models 返回，仅保留挑选模型所需的字段。"""
    items = payload.get("data")
    if not isinstance(items, list):
        return []
    needle = keyword.lower() if keyword else None
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        model_id = str(item.get("model_id") or "")
        model_name = str(item.get("model_name") or "")
        vendor = item.get("vendor")
        desc = str(item.get("desc") or "").strip().replace("\n", " ")
        if needle:
            haystack = f"{model_id} {model_name} {vendor or ''} {desc}".lower()
            if needle not in haystack:
                continue
        out.append({
            "model_id": model_id,
            "model_name": model_name,
            "vendor": vendor,
            "input_modalities": item.get("input_modalities"),
            "output_modalities": item.get("output_modalities"),
            "desc": desc[:140] + ("..." if len(desc) > 140 else ""),
        })
    return out


def extract_generation_schema(
    payload: Mapping[str, Any],
    model_id: str,
    *,
    keyword: str | None = None,
) -> dict[str, Any]:
    """从 /call/schema/models/{id}/endpoints 中摘出 /ai/v1/images/generations 的请求参数摘要。"""
    endpoints = payload.get("endpoints")
    if not isinstance(endpoints, list):
        raise AppError(
            "unexpected_response",
            f"模型 {model_id} 的端点数据格式异常",
            ExitCode.API,
            {"model": model_id},
        )
    matched = next(
        (ep for ep in endpoints if isinstance(ep, Mapping) and ep.get("path") == GENERATION_PATH),
        None,
    )
    if not matched:
        available = [ep.get("path") for ep in endpoints if isinstance(ep, Mapping)]
        raise AppError(
            "endpoint_not_found",
            f"模型 {model_id} 没有原生接口 {GENERATION_PATH}（可用端点：{available}）",
            ExitCode.API,
            {"model": model_id, "available_paths": available},
        )
    req = matched.get("request")
    schema = req.get("schema") if isinstance(req, Mapping) else None
    if not isinstance(schema, Mapping):
        raise AppError(
            "schema_missing",
            f"模型 {model_id} 未提供 {GENERATION_PATH} 的 request.schema",
            ExitCode.API,
            {"model": model_id},
        )

    required = set(schema.get("required") or ())
    props = schema.get("properties")
    if not isinstance(props, Mapping):
        props = {}

    needle = keyword.lower() if keyword else None
    fields: list[dict[str, Any]] = []
    extra_fields: list[dict[str, Any]] = []
    for name, spec in props.items():
        if not isinstance(spec, Mapping):
            continue
        if name == "extra" and isinstance(spec.get("properties"), Mapping):
            for ename, espec in spec["properties"].items():
                if not isinstance(espec, Mapping):
                    continue
                if needle and not _match_schema_field(ename, espec, needle):
                    continue
                extra_fields.append(_describe_field(ename, espec, required=False))
            continue
        if needle and not _match_schema_field(name, spec, needle):
            continue
        fields.append(_describe_field(name, spec, required=name in required))

    return {
        "model": model_id,
        "endpoint": GENERATION_PATH,
        "fields": fields,
        "extra_fields": extra_fields,
        "raw_schema": dict(schema),
    }


def _match_schema_field(name: str, spec: Mapping[str, Any], needle: str) -> bool:
    desc = str(spec.get("description") or "")
    return needle in name.lower() or needle in desc.lower()


def _describe_field(name: str, spec: Mapping[str, Any], *, required: bool) -> dict[str, Any]:
    info: dict[str, Any] = {
        "name": name,
        "required": required,
        "type": _field_type(spec),
    }
    if "default" in spec:
        info["default"] = spec["default"]
    enums = _collect_enums(spec)
    if enums:
        info["enum"] = enums
    bounds = _collect_bounds(spec)
    if bounds:
        info["constraints"] = bounds
    desc = str(spec.get("description") or "").strip().replace("\n", " ")
    if desc:
        info["description"] = desc[:180] + ("..." if len(desc) > 180 else "")
    return info


def _field_type(spec: Mapping[str, Any]) -> str:
    if "type" in spec:
        raw = spec["type"]
        return "/".join(raw) if isinstance(raw, list) else str(raw)
    variants = spec.get("anyOf") or spec.get("oneOf")
    if isinstance(variants, list):
        types: list[str] = []
        for item in variants:
            if isinstance(item, Mapping):
                t = item.get("type")
                if t and str(t) not in types:
                    types.append(str(t))
        if types:
            return "/".join(types)
    return "any"


def _collect_enums(spec: Mapping[str, Any]) -> list[Any]:
    out: list[Any] = []
    if isinstance(spec.get("enum"), list):
        out.extend(v for v in spec["enum"] if v is not None)
    if "const" in spec and spec["const"] is not None:
        out.append(spec["const"])
    for key in ("anyOf", "oneOf"):
        variants = spec.get(key)
        if isinstance(variants, list):
            for item in variants:
                if isinstance(item, Mapping):
                    for v in _collect_enums(item):
                        if v not in out:
                            out.append(v)
    return out


def _collect_bounds(spec: Mapping[str, Any]) -> dict[str, Any]:
    bounds: dict[str, Any] = {}
    for key in ("minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "pattern"):
        if key in spec:
            bounds[key] = spec[key]
    if isinstance(spec.get("x-sizing"), Mapping) and "constraints" in spec["x-sizing"]:
        bounds["sizing"] = spec["x-sizing"]["constraints"]
    for key in ("anyOf", "oneOf"):
        variants = spec.get(key)
        if isinstance(variants, list):
            for item in variants:
                if isinstance(item, Mapping):
                    sub = _collect_bounds(item)
                    for sk, sv in sub.items():
                        bounds.setdefault(sk, sv)
    return bounds
