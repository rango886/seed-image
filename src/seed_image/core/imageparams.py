"""OpenAI 图片接口的请求体构造。纯函数。

不校验取值（model/size/quality/background 由上游负责报错），只把 CLI 的字符串参数
装配成请求体，参数原样透传。
"""

from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..errors import input_error


@dataclass(frozen=True)
class ImageRequest:
    """一次生成/编辑请求：body 直接发给接口。"""

    body: Mapping[str, Any]


def build_request(
    *,
    prompt: str,
    model: str,
    size: str,
    quality: str,
    background: str,
    number: str,
) -> ImageRequest:
    body = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "quality": quality,
        "background": background,
        "n": _as_count(number),
    }
    return ImageRequest(body)


def with_images(request: ImageRequest, paths: tuple[Path, ...]) -> ImageRequest:
    """给编辑请求补上 images：本地文件读成 base64 data URL。"""
    images = [{"image_url": data_url(p)} for p in paths]
    return ImageRequest({**request.body, "images": images})


def data_url(path: Path) -> str:
    if not path.is_file():
        raise input_error("input_image_missing", f"输入图片不存在：{path}")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def _as_count(number: str) -> int:
    try:
        return int(number)
    except ValueError:
        raise input_error("invalid_number", f"--number 需要整数，实际得到 {number!r}") from None
