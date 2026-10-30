import httpx
import httpx2

from seed_image.netenv import normalize_no_proxy, sanitize_proxy_env


def test_normalize_strips_ipv6_brackets():
    assert normalize_no_proxy("127.0.0.1, ::1,[::1],[fe80::]/10,localhost") == (
        "127.0.0.1,::1,fe80::/10,localhost"
    )


def test_normalize_drops_unsupported_bracket_forms():
    assert normalize_no_proxy("[::1]:8080,[bad,localhost") == "localhost"


def test_sanitize_only_touches_no_proxy():
    env = {"NO_PROXY": "::1,[::1]", "HTTPS_PROXY": "http://[::1]:7890"}
    sanitize_proxy_env(env)
    assert env == {"NO_PROXY": "::1", "HTTPS_PROXY": "http://[::1]:7890"}


def test_clients_build_after_sanitize(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("NO_PROXY", "127.,*.local,localhost,<local>,127.0.0.1,::1,[::1]")
    sanitize_proxy_env()
    httpx.Client().close()
    httpx2.Client().close()
