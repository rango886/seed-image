"""用户配置的取值优先级与报错。"""

from __future__ import annotations

import pytest

from seed_image import config
from seed_image.errors import AppError, ExitCode


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    monkeypatch.setenv(config.CONFIG_ENV, str(path))
    for setting in config.SETTINGS:
        if setting.env:
            monkeypatch.delenv(setting.env, raising=False)
    return path


def test_missing_file_falls_back_to_default(cfg):
    found = config.lookup(config.COMFYUI_SERVER)
    assert (found.value, found.source) == ("http://127.0.0.1:8188", config.DEFAULT)
    assert config.lookup(config.OPENAI_API_KEY).source == config.UNSET


def test_file_beats_default_and_env_beats_file(cfg, monkeypatch):
    cfg.write_text('[comfyui]\nserver = "http://h:1"\n[openai]\nmodel = "m"\n', encoding="utf-8")
    assert config.lookup(config.COMFYUI_SERVER).value == "http://h:1"
    assert config.value(config.OPENAI_MODEL) == "m"
    monkeypatch.setenv("COMFYUI_SERVER", "http://env:2")
    assert config.lookup(config.COMFYUI_SERVER).source == config.ENV
    assert config.value(config.COMFYUI_SERVER, "http://arg:3") == "http://arg:3"


def test_broken_file_is_an_input_error(cfg):
    cfg.write_text("[comfyui\n", encoding="utf-8")
    with pytest.raises(AppError) as exc:
        config.lookup(config.COMFYUI_SERVER)
    assert exc.value.code == "invalid_config" and exc.value.exit_code == ExitCode.INPUT


def test_non_string_value_is_rejected(cfg):
    cfg.write_text("[comfyui]\nserver = 8188\n", encoding="utf-8")
    with pytest.raises(AppError, match="comfyui.server"):
        config.lookup(config.COMFYUI_SERVER)


def test_mask_hides_secret():
    assert config.mask("sk-1234567890abcd") == "sk-***abcd"
    assert config.mask("short") == "***"
