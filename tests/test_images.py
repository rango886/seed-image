"""OpenAI 图片命令回归测试：从 CLI 到 HTTP 请求体，不访问真实接口。"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest
from openai import OpenAI
from typer.testing import CliRunner

from seed_image.cli import images
from seed_image.cli.app import app

runner = CliRunner()


@pytest.mark.parametrize("command", ["generate", "edit"])
@pytest.mark.parametrize("complexity", [None, "low", "medium", "high"])
def test_cli_complexity_request_and_sidecar(command, complexity, tmp_path, monkeypatch):
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
            "openai-image", command, "--prompt", "test",
            "--model", "gpt-image-2.6-flare", "--size", "1536x864", "--quality", "low",
            "--output-path", str(tmp_path / "out"), "--save-params", "--format", "json",
        ]
        if command == "edit":
            source = tmp_path / "input.png"
            source.write_bytes(b"input image bytes")
            args += ["--input-image", str(source)]
        if complexity is not None:
            args += ["--complexity", complexity]
        result = runner.invoke(app, args)

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    endpoint, body = calls[0]
    assert endpoint == ("/v1/images/generations" if command == "generate" else "/v1/images/edits")
    assert body["size"] == "1536x864"
    assert body["quality"] == "low"
    if command == "edit":
        assert body["images"][0]["image_url"].startswith("data:image/png;base64,")

    output = json.loads(result.stdout)
    saved_request = json.loads(Path(output["sidecar"]).read_text(encoding="utf-8"))["request"]
    for recorded in (body, saved_request):
        if complexity is None:
            assert "complexity" not in recorded
        else:
            assert recorded["complexity"] == complexity


@pytest.mark.parametrize("command", ["generate", "edit"])
def test_complexity_requires_a_value(command):
    result = runner.invoke(app, ["openai-image", command, "--prompt", "test", "--complexity"])
    assert result.exit_code == 2
    assert "--complexity" in result.output
