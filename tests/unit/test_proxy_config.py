"""Unit tests for optional proxy configuration."""
import os
from unittest.mock import patch

import pytest

from app.core.proxy_config import (
    apply_proxy_to_chrome_options,
    get_configured_proxy_urls,
    get_requests_proxies,
    proxies_enabled,
    reset_proxy_state,
)
from selenium.webdriver.chrome.options import Options


@pytest.fixture(autouse=True)
def _reset_proxy():
    reset_proxy_state()
    yield
    reset_proxy_state()


def test_proxies_disabled_by_default():
    with patch("app.core.proxy_config.settings") as mock_settings:
        mock_settings.PROXY_URLS = ""
        mock_settings.PROXY_URL = ""
        assert get_configured_proxy_urls() == []
        assert not proxies_enabled()
        assert get_requests_proxies() is None


def test_proxy_urls_from_comma_separated():
    with patch("app.core.proxy_config.settings") as mock_settings:
        mock_settings.PROXY_URLS = "http://a:1@h1:80, http://b:2@h2:80"
        mock_settings.PROXY_URL = ""
        urls = get_configured_proxy_urls()
        assert len(urls) == 2
        assert urls[0].startswith("http://a")


def test_legacy_proxy_url_fallback():
    with patch("app.core.proxy_config.settings") as mock_settings:
        mock_settings.PROXY_URLS = ""
        mock_settings.PROXY_URL = "http://user:pass@host:8080"
        assert get_configured_proxy_urls() == ["http://user:pass@host:8080"]


def test_apply_proxy_noop_when_unconfigured():
    options = Options()
    options.add_argument("--disable-extensions")
    assert apply_proxy_to_chrome_options(options) is None
    assert "--proxy-server" not in " ".join(options.arguments)


def test_apply_proxy_server_without_auth():
    with patch("app.core.proxy_config.settings") as mock_settings:
        mock_settings.PROXY_URLS = "http://public.proxy:3128"
        mock_settings.PROXY_URL = ""
        mock_settings.PROXY_ROTATION_INTERVAL = 240
        options = Options()
        url = apply_proxy_to_chrome_options(options)
        assert url == "http://public.proxy:3128"
        assert any("proxy-server" in a for a in options.arguments)
