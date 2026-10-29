"""把图片接口的响应落盘：图片文件 + 一份同源的参数记录（sidecar）。"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..core.imageparams import ImageRequest
from ..core.naming import image_name, short_uid, sidecar_name
from ..errors import AppError, ExitCode


@dataclass(frozen=True)
class Saved:
    """一次请求的落盘结果。failures 非空表示部分成功。"""

    images: tuple[Path, ...]
    sidecar: Path | None
    failures: tuple[str, ...] = ()


def save_response(
    response: Mapping[str, Any],
    request: ImageRequest,
    directory: Path,
    *,
    endpoint: str,
    save_params: bool = False,
) -> Saved:
    """写出图片；save_params 为真时额外写一份 sidecar。sidecar 里绝不包含 api key。"""
    items = response.get("data")
    if not isinstance(items, list) or not items:
        raise AppError(
            "empty_response",
            "图片接口没有返回任何图片",
            ExitCode.API,
            {"response": _without_payload(response)},
        )
    directory.mkdir(parents=True, exist_ok=True)
    prompt = str(request.body.get("prompt", ""))
    uid = short_uid()
    ext = str(response.get("output_format") or "png")

    paths: list[Path] = []
    failures: list[str] = []
    for index, item in enumerate(items, start=1):
        try:
            payload = _decode(item)
        except AppError as exc:
            failures.append(f"第 {index} 张：{exc.message}")
            continue
        paths.append(write_image(directory, prompt, index, uid, ext, payload))

    if not save_params:
        return Saved(tuple(paths), None, tuple(failures))

    sidecar = write_sidecar(
        directory, prompt, uid,
        {
            "endpoint": endpoint,
            "request": dict(request.body) | _hide_images(request.body),
            "response": _without_payload(response),
            "images": [p.name for p in paths],
            "failures": failures,
        },
    )
    return Saved(tuple(paths), sidecar, tuple(failures))


def write_image(directory: Path, prompt: str, index: int, uid: str, ext: str, payload: bytes) -> Path:
    """按 `提示词前20字-序号-uid.ext` 落盘一张图。"""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / image_name(prompt, index, uid, ext)
    target.write_bytes(payload)
    return target


def write_sidecar(directory: Path, prompt: str, uid: str, record: Mapping[str, Any]) -> Path:
    """与一批图片同源的参数记录。调用方负责保证 record 里没有 api key 和 base64 正文。"""
    directory.mkdir(parents=True, exist_ok=True)
    sidecar = directory / sidecar_name(prompt, uid)
    sidecar.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return sidecar


def _decode(item: Any) -> bytes:
    if not isinstance(item, Mapping) or not item.get("b64_json"):
        raise AppError("no_b64_payload", f"返回项缺少 b64_json：{str(item)[:200]}", ExitCode.API)
    try:
        return base64.b64decode(str(item["b64_json"]), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AppError("bad_b64_payload", f"b64_json 解码失败：{exc}", ExitCode.API) from None


def _hide_images(body: Mapping[str, Any]) -> dict[str, Any]:
    """输入图片是几 MB 的 base64，只在 sidecar 里记数量。"""
    images = body.get("images")
    if not isinstance(images, list):
        return {}
    return {"images": f"<{len(images)} 张 base64 输入图片>"}


def _without_payload(response: Mapping[str, Any]) -> dict[str, Any]:
    """去掉 base64 正文的响应，用于记录与报错。"""
    data = response.get("data")
    trimmed = {k: v for k, v in response.items() if k != "data"}
    if isinstance(data, list):
        trimmed["data"] = [
            {k: v for k, v in item.items() if k != "b64_json"} if isinstance(item, Mapping) else item
            for item in data
        ]
    return trimmed
