"""用户配置：默认在 ~/.config/seed-image/config.toml。

取值优先级：命令行参数 > 环境变量 > 配置文件 > 内置默认值。
配置文件不存在不算错；存在但写坏了就报 invalid_config（退出码 3），
不静默忽略 —— 否则会拿着默认值去连错的服务器，排查更难。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import input_error

CONFIG_ENV = "SEED_IMAGE_CONFIG"

ENV = "env"
FILE = "config"
DEFAULT = "default"
UNSET = "unset"


@dataclass(frozen=True)
class Setting:
    key: str  # "section.name"，对应 toml 里的 [section] name = ...
    env: str | None = None
    default: str | None = None
    secret: bool = False


COMFYUI_SERVER = Setting("comfyui.server", "COMFYUI_SERVER", "http://127.0.0.1:8188")
# 环境变量一律用官方 SDK 的名字；走中转站就在配置文件里写 base_url / api_key。
OPENAI_BASE_URL = Setting("openai.base_url", "OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_API_KEY = Setting("openai.api_key", "OPENAI_API_KEY", secret=True)
OPENAI_MODEL = Setting("openai.model", default="gpt-image-2.5-flare")
# 与 google-genai SDK 一致：base_url 不含版本号，请求时自动接 /v1beta。
GEMINI_BASE_URL = Setting("gemini.base_url", "GOOGLE_GEMINI_BASE_URL", "https://generativelanguage.googleapis.com")
GEMINI_API_KEY = Setting("gemini.api_key", "GEMINI_API_KEY", secret=True)
GEMINI_MODEL = Setting("gemini.model", default="gemini-3.1-flash-image")
SETTINGS = (
    COMFYUI_SERVER,
    OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL,
    GEMINI_BASE_URL, GEMINI_API_KEY, GEMINI_MODEL,
)


@dataclass(frozen=True)
class Resolved:
    value: str | None
    source: str  # env / config / default / unset
    origin: str | None = None  # 来源细节：环境变量名或配置文件路径


def config_path() -> Path:
    custom = os.environ.get(CONFIG_ENV)
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".config" / "seed-image" / "config.toml"


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = path or config_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise input_error("config_unreadable", f"读不了配置文件 {path}：{exc}", path=str(path)) from None
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise input_error("invalid_config", f"配置文件 {path} 格式不对：{exc}", path=str(path)) from None


def lookup(setting: Setting, config: Mapping[str, Any] | None = None) -> Resolved:
    """按 环境变量 > 配置文件 > 默认值 找一个设置项（命令行参数由调用方先判断）。"""
    if setting.env and (value := os.environ.get(setting.env)):
        return Resolved(value, ENV, setting.env)
    if config is None:
        config = load_config()
    section, _, name = setting.key.partition(".")
    table = config.get(section)
    if isinstance(table, Mapping) and name in table:
        value = table[name]
        if not isinstance(value, str) or not value:
            raise input_error(
                "invalid_config",
                f"配置项 {setting.key} 必须是非空字符串，实际得到 {value!r}",
                path=str(config_path()),
            )
        return Resolved(value, FILE, str(config_path()))
    if setting.default is not None:
        return Resolved(setting.default, DEFAULT)
    return Resolved(None, UNSET)


def value(setting: Setting, override: str | None = None) -> str | None:
    return override or lookup(setting).value


def mask(secret: str) -> str:
    if len(secret) <= 10:
        return "***"
    return f"{secret[:3]}***{secret[-4:]}"
