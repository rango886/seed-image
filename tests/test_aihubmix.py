"""AIHubMix 纯函数层与 CLI 端到端回归测试，不访问真实接口。"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from seed_image.cli import aihubmix
from seed_image.cli.app import app
from seed_image.core import aihubmixparams as ap
from seed_image.errors import AppError, ExitCode

runner = CliRunner()
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452")


def test_build_request_omits_unset_fields_and_handles_images(tmp_path):
    img1 = tmp_path / "a.png"
    img2 = tmp_path / "b.png"
    img1.write_bytes(PNG)
    img2.write_bytes(PNG)

    body = ap.build_request(prompt="hello", model="qwen-image-3.0")
    assert body == {"model": "qwen-image-3.0", "prompt": "hello"}

    single = ap.build_request(
        prompt="edit",
        model="doubao-seedream-5-5",
        input_images=[str(img1)],
        extras=["watermark=false", "optimize_prompt_options.mode=standard"],
        params=["custom_flag=true"],
    )
    assert single["image"].startswith("data:image/png;base64,")
    assert "images" not in single
    assert single["extra"] == {"watermark": False, "optimize_prompt_options": {"mode": "standard"}}
    assert single["custom_flag"] is True

    multi = ap.build_request(
        prompt="mix",
        model="grok-imagine-image-2.0",
        input_images=[str(img1), "https://example.test/b.png"],
        aspect_ratio="16:9",
        resolution="2K",
    )
    assert "image" not in multi
    assert len(multi["images"]) == 2
    assert multi["images"][1] == "https://example.test/b.png"
    assert multi["aspect_ratio"] == "16:9"
    assert multi["resolution"] == "2K"


def test_image_field_image_rejects_multiple_inputs(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(PNG)
    with pytest.raises(AppError) as caught:
        ap.build_request(prompt="p", model="m", input_images=[str(img), str(img)], image_field="image")
    assert caught.value.code == "too_many_images"
    assert caught.value.exit_code == ExitCode.INPUT


def test_dry_run_outputs_masked_request_without_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("AIHUBMIX_API_KEY", raising=False)
    img = tmp_path / "a.png"
    img.write_bytes(PNG)
    result = runner.invoke(app, [
        "aihubmix", "generate",
        "--model", "doubao-seedream-5-5",
        "--prompt", "test",
        "--input-image", str(img),
        "--extra", "watermark=false",
        "--dry-run",
        "--format", "json",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["request"]["model"] == "doubao-seedream-5-5"
    assert data["request"]["extra"] == {"watermark": False}
    assert data["request"]["image"].startswith("<data:image/png;base64,")


def test_sync_generate_downloads_content_url_and_saves_sidecar(tmp_path, monkeypatch):
    seen_auth: list[str | None] = []
    seen_bodies: list[dict] = []

    def handle(request):
        seen_auth.append(request.headers.get("authorization"))
        if request.url.path == "/ai/v1/images/generations":
            seen_bodies.append(json.loads(request.content))
            return httpx.Response(200, json={
                "id": "task_123",
                "object": "image",
                "model": "doubao-seedream-5-5",
                "status": "completed",
                "output": [{
                    "index": 0,
                    "type": "file",
                    "b64_json": None,
                    "content_url": "https://aihubmix.test/ai/v1/images/task_123/content/res_1",
                }],
            })
        if request.url.path == "/ai/v1/images/task_123/content/res_1":
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        raise AssertionError(f"unexpected path: {request.url.path}")

    _patch_client(monkeypatch, handle)
    result = runner.invoke(app, [
        "aihubmix", "generate",
        "--model", "doubao-seedream-5-5",
        "--prompt", "花店阳光",
        "--size", "2048x2048",
        "--extra", "watermark=false",
        "--output-path", str(tmp_path / "out"),
        "--save-params",
        "--format", "json",
    ])
    assert result.exit_code == 0, result.output
    assert seen_auth == ["Bearer test-key", "Bearer test-key"]
    assert seen_bodies[0]["extra"] == {"watermark": False}
    output = json.loads(result.stdout)
    assert len(output["images"]) == 1
    assert Path(output["images"][0]).read_bytes() == PNG
    sidecar = json.loads(Path(output["sidecar"]).read_text(encoding="utf-8"))
    assert sidecar["task_id"] == "task_123"
    assert sidecar["request"]["size"] == "2048x2048"


def test_poll_mode_and_async_task_wait(tmp_path, monkeypatch):
    polls = {"count": 0}

    def handle(request):
        if request.method == "POST" and request.url.path == "/ai/v1/images/generations":
            body = json.loads(request.content)
            assert body["async"] is True
            return httpx.Response(200, json={"id": "task_poll_1", "model": body["model"], "status": "in_progress", "output": []})
        if request.method == "GET" and request.url.path == "/ai/v1/images/task_poll_1":
            polls["count"] += 1
            if polls["count"] < 2:
                return httpx.Response(200, json={"id": "task_poll_1", "model": "grok-imagine-image-2.0", "status": "in_progress", "output": []})
            return httpx.Response(200, json={
                "id": "task_poll_1",
                "model": "grok-imagine-image-2.0",
                "status": "completed",
                "output": [{"index": 0, "type": "file", "b64_json": base64.b64encode(PNG).decode()}],
            })
        raise AssertionError(f"unexpected {request.method} {request.url.path}")

    _patch_client(monkeypatch, handle)
    poll_res = runner.invoke(app, [
        "aihubmix", "generate",
        "--model", "grok-imagine-image-2.0",
        "--prompt", "neon",
        "--aspect-ratio", "16:9",
        "--resolution", "2K",
        "--mode", "poll",
        "--poll", "0.01",
        "--output-path", str(tmp_path / "out"),
        "--format", "json",
    ])
    assert poll_res.exit_code == 0, poll_res.output
    assert polls["count"] == 2
    assert len(json.loads(poll_res.stdout)["images"]) == 1

    async_res = runner.invoke(app, [
        "aihubmix", "generate",
        "--model", "grok-imagine-image-2.0",
        "--prompt", "neon",
        "--mode", "async",
        "--format", "json",
    ])
    assert async_res.exit_code == 0, async_res.output
    tid = json.loads(async_res.stdout)["task_id"]
    assert tid == "task_poll_1"

    wait_res = runner.invoke(app, [
        "aihubmix", "task", "wait", tid,
        "--poll", "0.01",
        "--output-path", str(tmp_path / "out2"),
        "--format", "json",
    ])
    assert wait_res.exit_code == 0, wait_res.output
    assert len(json.loads(wait_res.stdout)["images"]) == 1


def test_502_image_not_generated_is_not_retried(monkeypatch):
    posts = {"count": 0}

    def handle(request):
        posts["count"] += 1
        return httpx.Response(502, json={
            "error": {
                "code": "image_not_generated",
                "message": "The model provider completed the request but did not return an image.",
                "tid": "req_999",
            }
        })

    _patch_client(monkeypatch, handle)
    res = runner.invoke(app, [
        "aihubmix", "generate",
        "--model", "doubao-seedream-5-5",
        "--prompt", "test",
        "--max-retries", "2",
        "--format", "json",
    ])
    assert res.exit_code == ExitCode.API
    assert posts["count"] == 1
    payload = json.loads(res.stdout)
    assert payload["error"]["code"] == "image_not_generated"
    assert payload["error"]["details"]["tid"] == "req_999"


def test_model_list_and_inspect(monkeypatch):
    def handle(request):
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json={
                "data": [
                    {"model_id": "doubao-seedream-5-5", "model_name": "Doubao 5.5", "vendor": "bytedance", "input_modalities": "text,image", "output_modalities": "image", "desc": "Seedream"},
                    {"model_id": "grok-imagine-image-2.0", "model_name": "Grok 2.0", "vendor": "xai", "input_modalities": "text,image", "output_modalities": "image", "desc": "Grok"},
                ]
            })
        if request.url.path == "/call/schema/models/doubao-seedream-5-5/endpoints":
            return httpx.Response(200, json={
                "endpoints": [{
                    "path": "/ai/v1/images/generations",
                    "request": {
                        "schema": {
                            "required": ["model"],
                            "properties": {
                                "model": {"type": "string"},
                                "size": {"type": "string", "default": "2048x2048"},
                                "extra": {"type": "object", "properties": {"watermark": {"type": "boolean", "default": True}}},
                            },
                        }
                    },
                }]
            })
        raise AssertionError(f"unexpected {request.url.path}")

    _patch_client(monkeypatch, handle, require_key=False)
    list_res = runner.invoke(app, ["aihubmix", "model", "list", "--filter", "grok", "--format", "json"])
    assert list_res.exit_code == 0, list_res.output
    models = json.loads(list_res.stdout)["models"]
    assert [m["model_id"] for m in models] == ["grok-imagine-image-2.0"]

    inspect_res = runner.invoke(app, ["aihubmix", "model", "inspect", "doubao-seedream-5-5", "--format", "json"])
    assert inspect_res.exit_code == 0, inspect_res.output
    inspected = json.loads(inspect_res.stdout)
    assert [f["name"] for f in inspected["fields"]] == ["model", "size"]
    assert [f["name"] for f in inspected["extra_fields"]] == ["watermark"]


def _patch_client(monkeypatch, handle, *, require_key: bool = True):
    target = aihubmix.api.Target("https://aihubmix.test", "test-key" if require_key else None)
    monkeypatch.setattr(aihubmix.api, "target", lambda require_key=True: target)
    monkeypatch.setattr(
        aihubmix.api,
        "make_client",
        lambda t, timeout: httpx.Client(
            base_url=t.base_url,
            headers={"Authorization": f"Bearer {t.api_key}"} if t.api_key else {},
            transport=httpx.MockTransport(handle),
            timeout=timeout,
        ),
    )
