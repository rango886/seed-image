"""OpenAI 与 Gemini 图片命令回归测试：从 CLI 到 HTTP 请求体，不访问真实接口。"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest
from openai import OpenAI
from typer.testing import CliRunner

from seed_image.cli import gemini, images
from seed_image.cli.app import app

runner = CliRunner()
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452")


@pytest.mark.parametrize("with_input", [False, True])
@pytest.mark.parametrize("complexity", [None, "low", "medium", "high"])
def test_cli_complexity_request_and_sidecar(with_input, complexity, tmp_path, monkeypatch):
    calls = []

    def handle(request):
        calls.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={
            "data": [{"b64_json": base64.b64encode(b"image bytes").decode()}],
            "output_format": "png",
        })

    with OpenAI(
        api_key="test-key", base_url="https://example.test/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    ) as client:
        monkeypatch.setattr(images.api, "make_client", lambda **kwargs: client)
        args = [
            "openai-image", "generate", "--prompt", "test",
            "--model", "gpt-image-2.6-flare", "--size", "1536x864", "--quality", "low",
            "--output-path", str(tmp_path / "out"), "--save-params", "--format", "json",
        ]
        if with_input:
            source = tmp_path / "input.png"
            source.write_bytes(b"input image bytes")
            args += ["--input-image", str(source)]
        if complexity is not None:
            args += ["--complexity", complexity]
        result = runner.invoke(app, args)

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    endpoint, body = calls[0]
    assert endpoint == ("/v1/images/edits" if with_input else "/v1/images/generations")
    assert body["size"] == "1536x864"
    assert body["quality"] == "low"
    if with_input:
        assert body["images"][0]["image_url"].startswith("data:image/png;base64,")

    output = json.loads(result.stdout)
    saved_request = json.loads(Path(output["sidecar"]).read_text(encoding="utf-8"))["request"]
    for recorded in (body, saved_request):
        if complexity is None:
            assert "complexity" not in recorded
        else:
            assert recorded["complexity"] == complexity


def test_complexity_requires_a_value():
    result = runner.invoke(app, ["openai-image", "generate", "--prompt", "test", "--complexity"])
    assert result.exit_code == 2
    assert "--complexity" in result.output


@pytest.mark.parametrize("with_input", [False, True])
@pytest.mark.parametrize("moderation", [None, "low", "auto"])
def test_cli_moderation_request_and_sidecar(with_input, moderation, tmp_path, monkeypatch):
    calls = []

    def handle(request):
        calls.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={
            "data": [{"b64_json": base64.b64encode(b"image bytes").decode()}],
            "output_format": "png",
        })

    with OpenAI(
        api_key="test-key", base_url="https://example.test/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    ) as client:
        monkeypatch.setattr(images.api, "make_client", lambda **kwargs: client)
        args = [
            "openai-image", "generate", "--prompt", "test",
            "--output-path", str(tmp_path / "out"), "--save-params", "--format", "json",
        ]
        if with_input:
            source = tmp_path / "input.png"
            source.write_bytes(b"input image bytes")
            args += ["--input-image", str(source)]
        if moderation is not None:
            args += ["--moderation", moderation]
        result = runner.invoke(app, args)

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    _, body = calls[0]
    expected = "low" if moderation is None else moderation
    output = json.loads(result.stdout)
    saved_request = json.loads(Path(output["sidecar"]).read_text(encoding="utf-8"))["request"]
    assert body["moderation"] == expected
    assert saved_request["moderation"] == expected


@pytest.mark.parametrize("with_input", [False, True])
def test_gemini_generate_supports_optional_input_image(with_input, tmp_path, monkeypatch):
    calls = []

    def handle(request):
        calls.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={
            "candidates": [{
                "finishReason": "STOP",
                "content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": base64.b64encode(PNG).decode()}}]},
            }],
        })

    monkeypatch.setattr(gemini.api, "target", lambda: gemini.api.Target("https://example.test/gemini", "k"))
    monkeypatch.setattr(
        gemini.api,
        "make_client",
        lambda t, timeout: httpx.Client(transport=httpx.MockTransport(handle), timeout=timeout),
    )
    args = [
        "gemini-image", "generate", "--prompt", "test",
        "--model", "gemini-3.1-flash-image",
        "--output-path", str(tmp_path / "out"), "--format", "json",
    ]
    if with_input:
        source = tmp_path / "ref.png"
        source.write_bytes(PNG)
        args += ["--input-image", str(source)]
    result = runner.invoke(app, args)

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    parts = calls[0][1]["contents"][0]["parts"]
    assert len(parts) == (2 if with_input else 1)
    assert parts[-1] == {"text": "test"}


@pytest.mark.parametrize("group", ["openai-image", "gemini-image"])
def test_edit_subcommand_is_removed(group):
    result = runner.invoke(app, [group, "edit", "--prompt", "test"])
    assert result.exit_code == 2
