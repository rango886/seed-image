"""AIHubMix 原生图片协议（/ai/v1/images/*）的 HTTP 调用。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlparse

import httpx

from .. import config
from ..core import aihubmixparams as ap
from ..errors import AppError, ExitCode, input_error
from ..render import note

RETRY_STATUS = {408, 429, 500, 502, 503, 504}
# 文档明确指出：这些 5xx/502 码代表请求可能已进上游或状态未知，不得自动重复提交。
NO_RETRY_CODES = frozenset({
    "image_not_generated",
    "task_status_unavailable",
    "upstream_bad_response",
    "sync_timeout",
})


@dataclass(frozen=True)
class Target:
    base_url: str
    api_key: str | None


def target(*, require_key: bool = True) -> Target:
    loaded = config.load_config()
    base_url = (config.lookup(config.AIHUBMIX_BASE_URL, loaded).value or "").rstrip("/")
    api_key = config.lookup(config.AIHUBMIX_API_KEY, loaded).value
    if require_key and not api_key:
        s = config.AIHUBMIX_API_KEY
        raise input_error(
            "missing_credentials",
            f"缺少 AIHubMix 密钥，请设置环境变量 {s.env} 或配置项 {s.key}（配置文件：{config.config_path()}）",
        )
    return Target(base_url, api_key)


def make_client(t: Target, *, timeout: float) -> httpx.Client:
    headers: dict[str, str] = {}
    if t.api_key:
        headers["Authorization"] = f"Bearer {t.api_key}"
    return httpx.Client(base_url=t.base_url, headers=headers, timeout=timeout)


def create_generation(http: httpx.Client, body: Mapping[str, Any], *, max_retries: int = 2) -> dict[str, Any]:
    path = ap.GENERATION_PATH
    attempt = 0
    while True:
        try:
            response = http.post(path, json=dict(body))
        except httpx.ConnectError as exc:
            if attempt < max_retries:
                attempt += 1
                _backoff(attempt, f"连接失败 {type(exc).__name__}")
                continue
            raise AppError("request_failed", f"连接 AIHubMix 接口失败：{exc}", ExitCode.API, {"endpoint": path}) from None
        except httpx.HTTPError as exc:
            # 读超时等不重提交，避免重复生成/扣费
            raise AppError("request_failed", f"调用 AIHubMix 接口失败：{exc}", ExitCode.API, {"endpoint": path}) from None

        if response.status_code >= 400:
            err = _status_error(path, response)
            err_code = _error_code_of(response)
            if response.status_code in RETRY_STATUS and err_code not in NO_RETRY_CODES and attempt < max_retries:
                attempt += 1
                _backoff(attempt, f"HTTP {response.status_code}" + (f" ({err_code})" if err_code else ""))
                continue
            raise err
        return _json_object(path, response)


def get_image_task(http: httpx.Client, task_id: str, *, max_retries: int = 2) -> dict[str, Any]:
    return _get_json(http, f"/ai/v1/images/{task_id}", max_retries=max_retries)


def list_image_tasks(
    http: httpx.Client,
    *,
    limit: int = 20,
    after: str | None = None,
    order: str = "desc",
    max_retries: int = 2,
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit, "order": order}
    if after:
        params["after"] = after
    return _get_json(http, "/ai/v1/images", params=params, max_retries=max_retries)


def list_models(
    http: httpx.Client,
    *,
    model_type: str = "image_generation",
    schema_checked: bool = True,
    sort_by: str = "order",
    max_retries: int = 2,
) -> dict[str, Any]:
    params = {
        "type": model_type,
        "schema_checked": str(schema_checked).lower(),
        "sort_by": sort_by,
    }
    return _get_json(http, "/api/v1/models", params=params, max_retries=max_retries)


def get_model_endpoints(http: httpx.Client, model_id: str, *, max_retries: int = 2) -> dict[str, Any]:
    return _get_json(http, f"/call/schema/models/{model_id}/endpoints", max_retries=max_retries)


def download_content(
    http: httpx.Client,
    url: str,
    *,
    base_url: str,
    max_retries: int = 2,
) -> tuple[bytes, str | None]:
    """下载任务输出图；同域携带 Bearer Token，跨域不带认证头避免密钥外泄。"""
    same_origin = not url.startswith(("http://", "https://")) or (
        urlparse(url).netloc == urlparse(base_url).netloc
    )
    if same_origin:
        return _get_bytes(http, url, max_retries=max_retries)
    with httpx.Client(timeout=http.timeout) as bare:
        return _get_bytes(bare, url, max_retries=max_retries)


def _get_json(
    http: httpx.Client,
    path: str,
    *,
    params: Mapping[str, Any] | None = None,
    max_retries: int = 2,
) -> dict[str, Any]:
    attempt = 0
    while True:
        try:
            response = http.get(path, params=params)
        except httpx.HTTPError as exc:
            if attempt < max_retries:
                attempt += 1
                _backoff(attempt, f"网络错误 {type(exc).__name__}")
                continue
            raise AppError("request_failed", f"请求 {path} 失败：{exc}", ExitCode.API, {"endpoint": path}) from None

        if response.status_code in RETRY_STATUS and attempt < max_retries:
            attempt += 1
            _backoff(attempt, f"HTTP {response.status_code}")
            continue
        if response.status_code >= 400:
            raise _status_error(path, response)
        return _json_object(path, response)


def _get_bytes(http: httpx.Client, url: str, *, max_retries: int = 2) -> tuple[bytes, str | None]:
    attempt = 0
    while True:
        try:
            response = http.get(url)
        except httpx.HTTPError as exc:
            if attempt < max_retries:
                attempt += 1
                _backoff(attempt, f"下载网络错误 {type(exc).__name__}")
                continue
            raise AppError("download_failed", f"下载图片失败：{exc}", ExitCode.API, {"url": url}) from None

        if response.status_code in RETRY_STATUS and attempt < max_retries:
            attempt += 1
            _backoff(attempt, f"下载 HTTP {response.status_code}")
            continue
        if response.status_code >= 400:
            raise _status_error(url, response)
        return response.content, response.headers.get("content-type")


def _backoff(attempt: int, reason: str) -> None:
    delay = min(2 ** (attempt - 1), 8)
    note(f"{reason}，{delay}s 后重试（第 {attempt} 次）")
    time.sleep(delay)


def _error_code_of(response: httpx.Response) -> str | None:
    body = _body_of(response)
    if isinstance(body, Mapping):
        err = body.get("error")
        if isinstance(err, Mapping) and err.get("code"):
            return str(err["code"])
    return None


def _status_error(endpoint: str, response: httpx.Response) -> AppError:
    body = _body_of(response)
    code = f"http_{response.status_code}"
    message = str(body)[:500]
    details: dict[str, Any] = {"endpoint": endpoint, "status": response.status_code, "response": ap.without_payload(body)}
    if isinstance(body, Mapping):
        err = body.get("error")
        if isinstance(err, Mapping):
            if err.get("code"):
                code = str(err["code"])
            message = str(err.get("message") or err)[:500]
            if err.get("tid"):
                details["tid"] = err["tid"]
        elif err:
            message = str(err)[:500]
    return AppError(code, f"AIHubMix 接口返回 {response.status_code}：{message}", ExitCode.API, details)


def _json_object(endpoint: str, response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError):
        data = None
    if not isinstance(data, dict):
        raise AppError(
            "unexpected_response",
            f"{endpoint} 没有返回 JSON 对象（Content-Type: {response.headers.get('content-type')}）",
            ExitCode.API,
            {"endpoint": endpoint, "response": response.text[:300]},
        )
    return data


def _body_of(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text[:2000]
