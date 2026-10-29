"""Gemini generateContent 的请求体构造与响应解析。纯函数，不碰网络。

参照 docs/gemini-image-api.md：
- 图片一律 base64 内联（不带 data: 前缀），**图在前、指令在后**；
- 出图在 candidates[*].content.parts[*].inlineData（兼容 inline_data），可能夹着 text part；
- aspectRatio / imageSize 不传就交给上游默认（跟随输入图比例 / 1K）。
"""

from __future__ import annotations

import base64
import binascii
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..errors import AppError, ExitCode, input_error

MAX_IMAGES = 14
# 请求体是整段 JSON 内联 base64，太大既慢又容易被网关拒；超过就让调用方先缩图。
MAX_INLINE_BYTES = 18 * 1024 * 1024
IMAGE_ONLY = ("IMAGE",)
TEXT_AND_IMAGE = ("TEXT", "IMAGE")

_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}

# 可调的安全类别一律关到最松（OFF）。这是写死的策略，不暴露成 CLI 参数。
# 注意：服务端的硬性拦截（PROHIBITED_CONTENT、输出图的 IMAGE_SAFETY 等）不受这里控制。
SAFETY_CATEGORIES = (
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
    "HARM_CATEGORY_CIVIC_INTEGRITY",
)
SAFETY_THRESHOLD = "OFF"
SAFETY_SETTINGS = tuple({"category": c, "threshold": SAFETY_THRESHOLD} for c in SAFETY_CATEGORIES)


@dataclass(frozen=True)
class InlineImage:
    mime: str
    data: str  # base64，无前缀


@dataclass(frozen=True)
class Parsed:
    images: tuple[tuple[str, bytes], ...]  # (mime, 字节)
    texts: tuple[str, ...]
    finish_reasons: tuple[str, ...]
    block_reason: str | None


def endpoint(base_url: str, model: str) -> str:
    """base_url 按 SDK 习惯不含版本号；万一用户写了 /v1beta 也照常工作。"""
    base = base_url.rstrip("/")
    if not base.rsplit("/", 1)[-1].startswith("v1"):
        base += "/v1beta"
    return f"{base}/models/{model}:generateContent"


def sniff_mime(payload: bytes) -> str | None:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    if payload[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def read_images(paths: Sequence[Path]) -> tuple[InlineImage, ...]:
    if len(paths) > MAX_IMAGES:
        raise input_error("too_many_images", f"Gemini 最多 {MAX_IMAGES} 张参考图，实际给了 {len(paths)} 张")
    images: list[InlineImage] = []
    total = 0
    for path in paths:
        if not path.is_file():
            raise input_error("input_image_missing", f"输入图片不存在：{path}")
        payload = path.read_bytes()
        mime = sniff_mime(payload) or mimetypes.guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(payload).decode("ascii")
        total += len(encoded)
        images.append(InlineImage(mime, encoded))
    if total > MAX_INLINE_BYTES:
        raise input_error(
            "request_too_large",
            f"输入图片 base64 后共 {total / 1024 / 1024:.1f}MB，超过 {MAX_INLINE_BYTES // 1024 // 1024}MB，请先缩小图片",
        )
    return tuple(images)


def build_body(
    *,
    prompt: str,
    images: Sequence[InlineImage] = (),
    aspect_ratio: str | None = None,
    image_size: str | None = None,
    modalities: Sequence[str] = IMAGE_ONLY,
) -> dict[str, Any]:
    parts: list[dict[str, Any]] = [
        {"inline_data": {"mime_type": image.mime, "data": image.data}} for image in images
    ]
    parts.append({"text": prompt})
    image_config = {
        key: value
        for key, value in (("aspectRatio", aspect_ratio), ("imageSize", image_size))
        if value
    }
    config: dict[str, Any] = {"responseModalities": list(modalities)}
    if image_config:
        config["imageConfig"] = image_config
    return {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": config,
        "safetySettings": [dict(s) for s in SAFETY_SETTINGS],
    }


def with_modalities(body: Mapping[str, Any], modalities: Sequence[str]) -> dict[str, Any]:
    config = dict(body.get("generationConfig") or {})
    config["responseModalities"] = list(modalities)
    return {**body, "generationConfig": config}


def is_modality_error(status: int, response_text: str) -> bool:
    """responseModalities 只接受有限组合，传错是 400 且报文里会提到 modality。"""
    return status == 400 and "modalit" in response_text.lower()


def parse_response(response: Mapping[str, Any]) -> Parsed:
    images: list[tuple[str, bytes]] = []
    texts: list[str] = []
    reasons: list[str] = []
    for candidate in _list(response.get("candidates")):
        if not isinstance(candidate, Mapping):
            continue
        if candidate.get("finishReason"):
            reasons.append(str(candidate["finishReason"]))
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, Mapping) else None
        for part in _list(parts):
            if not isinstance(part, Mapping):
                continue
            if part.get("thought"):  # 思考过程的草图/文字不算产物
                continue
            inline = part.get("inlineData") or part.get("inline_data")
            if isinstance(inline, Mapping) and inline.get("data"):
                mime = str(inline.get("mimeType") or inline.get("mime_type") or "image/png")
                images.append((mime, _b64(str(inline["data"]))))
            elif isinstance(part.get("text"), str) and part["text"].strip():
                texts.append(part["text"].strip())
    feedback = response.get("promptFeedback")
    block = feedback.get("blockReason") if isinstance(feedback, Mapping) else None
    return Parsed(tuple(images), tuple(texts), tuple(reasons), str(block) if block else None)


def ext_for(mime: str) -> str:
    return _EXT.get(mime) or (mimetypes.guess_extension(mime) or ".png").lstrip(".")


def without_payload(value: Any) -> Any:
    """把请求/响应里的 base64 图片换成占位符，用于报错与 sidecar。"""
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            if key in ("inlineData", "inline_data") and isinstance(item, Mapping):
                data = item.get("data")
                out[key] = {k: v for k, v in item.items() if k != "data"} | (
                    {"data": f"<{len(data)} 字符 base64>"} if isinstance(data, str) else {}
                )
            else:
                out[key] = without_payload(item)
        return out
    if isinstance(value, list):
        return [without_payload(item) for item in value]
    return value


def _b64(text: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        # 少数网关会用 urlsafe 编码或丢掉补位
        try:
            return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
        except (binascii.Error, ValueError) as exc:
            raise AppError("bad_b64_payload", f"inlineData 解码失败：{exc}", ExitCode.API) from None


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
