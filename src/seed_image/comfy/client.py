"""ComfyUI HTTP 接口的薄封装（对应 V:/code/AI/ComfyUI/API.md）。

每个函数接收一个 httpx.Client，只做一次调用并把响应翻译成普通 dict，
或者抛出带上游报文的 AppError。不含重试、不含轮询、不含编排。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import httpx

from ..errors import AppError, ExitCode, input_error


def make_client(server: str, timeout: float = 60.0) -> httpx.Client:
    """建立指向某个 ComfyUI 服务端的连接。调用方负责关闭。"""
    return httpx.Client(base_url=server.rstrip("/"), timeout=timeout)


def upload_file(client: httpx.Client, path: Path, *, subfolder: str = "") -> str:
    """上传一个本地资源到 input 目录，返回工作流里该用的引用名。

    /api/upload/image 对字节内容不做类型限制，图片/视频/音频/文本走同一入口；
    服务端按 (文件名, 内容哈希) 去重：同名同内容直接复用，同名不同内容自动改名。
    返回值是 `name` 或 `subfolder/name`，可直接填进 LoadImage 之类的节点。
    """
    if not path.is_file():
        raise input_error("upload_file_missing", f"待上传的文件不存在：{path}")
    with path.open("rb") as handle:
        response = _send(
            client,
            "POST",
            "/api/upload/image",
            files={"image": (path.name, handle, _content_type(path))},
            data={"type": "input", "subfolder": subfolder},
        )
    body = _as_json(response)
    name = body.get("name")
    if not name:
        raise AppError("upload_failed", f"上传 {path} 后服务端未返回文件名", ExitCode.API, {"response": body})
    folder = body.get("subfolder") or ""
    return f"{folder}/{name}" if folder else str(name)


def submit_prompt(
    client: httpx.Client,
    workflow: Mapping[str, Any],
    *,
    job_id: str,
    client_id: str,
) -> dict[str, Any]:
    """把 API 格式 workflow 入队。job_id 由调用方生成，因此响应丢失也不会丢任务。"""
    payload = {"prompt": dict(workflow), "prompt_id": job_id, "client_id": client_id}
    return _as_json(_send(client, "POST", "/api/prompt", json=payload))


def get_job(client: httpx.Client, job_id: str) -> dict[str, Any]:
    """查询单个任务。终态任务会带上 outputs。"""
    return _as_json(_send(client, "GET", f"/api/jobs/{job_id}"))


def list_jobs(client: httpx.Client, **params: Any) -> dict[str, Any]:
    """列出任务。支持 status/limit/offset/sort_by/sort_order 过滤。"""
    query = {k: v for k, v in params.items() if v is not None}
    return _as_json(_send(client, "GET", "/api/jobs", params=query))


def cancel_job(client: httpx.Client, job_id: str) -> dict[str, Any]:
    """请求取消。幂等：已完成或未知 id 返回 {"cancelled": false}，不算错误。"""
    return _as_json(_send(client, "POST", f"/api/jobs/{job_id}/cancel"))


def fetch_view(client: httpx.Client, *, filename: str, subfolder: str, type_: str) -> bytes:
    """按输出条目取回文件字节。"""
    params = {"filename": filename, "subfolder": subfolder, "type": type_}
    return _send(client, "GET", "/api/view", params=params).content


def _content_type(path: Path) -> str:
    import mimetypes

    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _send(client: httpx.Client, method: str, url: str, **kwargs: Any) -> httpx.Response:
    try:
        response = client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise AppError(
            "server_unreachable",
            f"连接 ComfyUI 失败（{client.base_url}）：{exc}",
            ExitCode.API,
            {"url": url},
        ) from None
    if response.is_success:
        return response
    raise _http_error(response, url)


def _http_error(response: httpx.Response, url: str) -> AppError:
    body = _try_json(response)
    detail = body if body is not None else response.text[:2000]
    message = _message_of(detail) or f"{response.status_code} {response.reason_phrase}"
    return AppError(
        f"http_{response.status_code}",
        f"ComfyUI 返回 {response.status_code}：{message}",
        ExitCode.API,
        {"url": url, "response": detail},
    )


def _message_of(detail: Any) -> str | None:
    if not isinstance(detail, Mapping):
        return None
    error = detail.get("error")
    if isinstance(error, Mapping):
        return str(error.get("message") or error)
    return str(error) if error else None


def _as_json(response: httpx.Response) -> dict[str, Any]:
    body = _try_json(response)
    if not isinstance(body, dict):
        raise AppError(
            "unexpected_response",
            f"期望 JSON 对象，实际得到 {type(body).__name__}",
            ExitCode.API,
            {"response": response.text[:2000]},
        )
    return body


def _try_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError):
        return None
