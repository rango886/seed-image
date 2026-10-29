"""Gemini generateContent 的 HTTP 调用。

只做：鉴权、单次请求（含有限重试与 responseModalities 自动降级）、并发跑 N 次。
请求体装配与响应解析在 core/geminiparams.py。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence, TypeVar

import httpx

from .. import config
from ..core import geminiparams as gp
from ..errors import AppError, ExitCode, input_error
from ..render import note

RETRY_STATUS = {408, 429, 500, 502, 503, 504}
T = TypeVar("T")


@dataclass(frozen=True)
class Target:
    base_url: str
    api_key: str


@dataclass(frozen=True)
class Reply:
    """一次成功调用：实际发出的请求体（可能已降级 modality）+ 响应。"""

    body: Mapping[str, Any]
    response: Mapping[str, Any]


def target() -> Target:
    loaded = config.load_config()
    base_url = config.lookup(config.GEMINI_BASE_URL, loaded).value or ""
    api_key = config.lookup(config.GEMINI_API_KEY, loaded).value
    if not api_key:
        s = config.GEMINI_API_KEY
        raise input_error(
            "missing_credentials",
            f"缺少 Gemini 密钥，请设置环境变量 {s.env} 或配置项 {s.key}（配置文件：{config.config_path()}）",
        )
    return Target(base_url, api_key)


def make_client(t: Target, *, timeout: float) -> httpx.Client:
    return httpx.Client(headers={"x-goog-api-key": t.api_key}, timeout=timeout)


def call(http: httpx.Client, url: str, body: Mapping[str, Any], *, max_retries: int) -> Reply:
    """发一次 generateContent。只要 IMAGE 被拒时自动改成 TEXT+IMAGE 再试一次。"""
    try:
        return Reply(body, _post(http, url, body, max_retries=max_retries))
    except _ModalityRejected:
        note("responseModalities=[IMAGE] 被拒，改用 [TEXT, IMAGE] 重试")
        fallback = gp.with_modalities(body, gp.TEXT_AND_IMAGE)
        try:
            return Reply(fallback, _post(http, url, fallback, max_retries=max_retries))
        except _ModalityRejected as exc:
            raise exc.error from None


def run_parallel(tasks: Sequence[Callable[[], T]], concurrency: int) -> list[T | AppError]:
    """并发执行，保持顺序；AppError 作为结果返回（部分成功要用到），其它异常原样抛出。

    用 daemon 线程而不是 ThreadPoolExecutor：Ctrl+C 时进程能立刻退出，
    不用等还在路上的生图请求。
    """
    results: list[Any] = [None] * len(tasks)
    pending = list(range(len(tasks)))
    lock = threading.Lock()

    def worker() -> None:
        while True:
            with lock:
                if not pending:
                    return
                index = pending.pop(0)
            try:
                results[index] = tasks[index]()
            except AppError as err:
                results[index] = err
            except BaseException as exc:  # 交给调用方原样抛出
                results[index] = _Crash(exc)

    threads = [
        threading.Thread(target=worker, daemon=True, name=f"gemini-{i}")
        for i in range(max(1, min(concurrency, len(tasks))))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for item in results:
        if isinstance(item, _Crash):
            raise item.exc
    return results


@dataclass(frozen=True)
class _Crash:
    exc: BaseException


class _ModalityRejected(Exception):
    def __init__(self, error: AppError) -> None:
        self.error = error


def _post(http: httpx.Client, url: str, body: Mapping[str, Any], *, max_retries: int) -> dict[str, Any]:
    attempt = 0
    while True:
        try:
            response = http.post(url, json=dict(body))
        except httpx.HTTPError as exc:
            if attempt < max_retries:
                attempt += 1
                _backoff(attempt, f"网络错误 {type(exc).__name__}")
                continue
            raise AppError("request_failed", f"调用 Gemini 接口失败：{exc}", ExitCode.API, {"endpoint": url}) from None

        if response.status_code in RETRY_STATUS and attempt < max_retries:
            attempt += 1
            _backoff(attempt, f"HTTP {response.status_code}")
            continue
        if response.status_code >= 400:
            error = _status_error(url, response)
            if gp.is_modality_error(response.status_code, response.text):
                raise _ModalityRejected(error)
            raise error
        return _json_object(url, response)


def _backoff(attempt: int, reason: str) -> None:
    delay = min(2 ** (attempt - 1), 8)
    note(f"{reason}，{delay}s 后重试（第 {attempt} 次）")
    time.sleep(delay)


def _status_error(url: str, response: httpx.Response) -> AppError:
    body = _body_of(response)
    message = body
    if isinstance(body, Mapping):
        err = body.get("error")
        message = err.get("message") if isinstance(err, Mapping) else (err or body)
    return AppError(
        f"http_{response.status_code}",
        f"Gemini 接口返回 {response.status_code}：{str(message)[:500]}",
        ExitCode.API,
        {"endpoint": url, "response": gp.without_payload(body)},
    )


def _json_object(url: str, response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError):
        data = None
    if not isinstance(data, dict):
        # 中转站路径写错时常见：200 返回的是网站首页 HTML
        raise AppError(
            "unexpected_response",
            f"{url} 没有返回 JSON 对象（Content-Type: {response.headers.get('content-type')}），"
            "多半是 base_url 不对：中转站通常要带前缀，例如 https://<host>/gemini",
            ExitCode.API,
            {"endpoint": url, "response": response.text[:300]},
        )
    return data


def _body_of(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text[:2000]
