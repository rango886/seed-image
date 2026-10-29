"""Gemini 请求体装配 / 响应解析的单元测试（纯函数，不碰网络）。"""

from __future__ import annotations

import base64

import pytest

from seed_image.core import geminiparams as gp
from seed_image.errors import AppError

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452")


def test_endpoint_appends_version_only_when_missing():
    assert gp.endpoint("https://h/gemini", "m") == "https://h/gemini/v1beta/models/m:generateContent"
    assert gp.endpoint("https://h/gemini/v1beta/", "m") == "https://h/gemini/v1beta/models/m:generateContent"


def test_images_come_before_prompt_and_config_is_optional():
    body = gp.build_body(prompt="p", images=[gp.InlineImage("image/png", "AAA")])
    parts = body["contents"][0]["parts"]
    assert parts[0] == {"inline_data": {"mime_type": "image/png", "data": "AAA"}}
    assert parts[-1] == {"text": "p"}
    assert body["generationConfig"] == {"responseModalities": ["IMAGE"]}
    assert {s["threshold"] for s in body["safetySettings"]} == {"OFF"}
    assert len(body["safetySettings"]) == len(gp.SAFETY_CATEGORIES)

    sized = gp.build_body(prompt="p", aspect_ratio="16:9", image_size="2K")
    assert sized["generationConfig"]["imageConfig"] == {"aspectRatio": "16:9", "imageSize": "2K"}
    assert gp.with_modalities(sized, gp.TEXT_AND_IMAGE)["generationConfig"]["responseModalities"] == ["TEXT", "IMAGE"]


def test_parse_collects_all_image_parts_and_texts():
    data = base64.b64encode(PNG).decode()
    parsed = gp.parse_response({
        "candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": "说点什么"},
            {"inlineData": {"mimeType": "image/png", "data": data}},
            {"inline_data": {"mime_type": "image/jpeg", "data": data}},
            {"thought": True, "inlineData": {"mimeType": "image/png", "data": data}},
        ]}}],
    })
    assert [m for m, _ in parsed.images] == ["image/png", "image/jpeg"]
    assert parsed.images[0][1] == PNG
    assert parsed.texts == ("说点什么",)
    assert parsed.finish_reasons == ("STOP",)


def test_parse_reports_block_reason():
    parsed = gp.parse_response({"promptFeedback": {"blockReason": "SAFETY"}})
    assert parsed.images == () and parsed.block_reason == "SAFETY"


def test_sniff_mime_and_ext():
    assert gp.sniff_mime(PNG) == "image/png"
    assert gp.sniff_mime(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert gp.sniff_mime(b"RIFF\0\0\0\0WEBPVP8 ") == "image/webp"
    assert gp.ext_for("image/jpeg") == "jpg"


def test_read_images_limits(tmp_path):
    with pytest.raises(AppError, match="不存在"):
        gp.read_images([tmp_path / "nope.png"])
    with pytest.raises(AppError, match="最多"):
        gp.read_images([tmp_path / "a.png"] * (gp.MAX_IMAGES + 1))
    image = tmp_path / "x.bin"
    image.write_bytes(PNG)
    assert gp.read_images([image])[0].mime == "image/png"


def test_without_payload_hides_base64():
    trimmed = gp.without_payload({"a": [{"inlineData": {"mimeType": "image/png", "data": "QUJD"}}]})
    assert trimmed["a"][0]["inlineData"] == {"mimeType": "image/png", "data": "<4 字符 base64>"}


def test_modality_error_detection():
    assert gp.is_modality_error(400, "does not support the requested response modalities")
    assert not gp.is_modality_error(500, "modalities")
