"""
Optional proxy configuration for scraping (career, Indeed, etc.).

Proxies are OFF unless PROXY_URLS or PROXY_URL is set in the environment.
When enabled, uses ProxyManager for rotation across a comma-separated pool.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import zipfile
from typing import List, Optional
from urllib.parse import urlparse

from app.core.config import settings
from app.core.proxy_manager import ProxyManager, get_proxy_manager, reset_proxy_manager

logger = logging.getLogger(__name__)


def get_configured_proxy_urls() -> List[str]:
    """Read proxy URLs from settings (PROXY_URLS comma-separated, or legacy PROXY_URL)."""
    proxy_urls: List[str] = []

    urls_str = (getattr(settings, "PROXY_URLS", None) or "").strip()
    if urls_str:
        proxy_urls = [u.strip() for u in urls_str.split(",") if u.strip()]

    if not proxy_urls:
        legacy = (getattr(settings, "PROXY_URL", None) or "").strip()
        if legacy:
            proxy_urls = [legacy]

    return proxy_urls


def proxies_enabled() -> bool:
    return bool(get_configured_proxy_urls())


def _rotation_interval() -> int:
    return int(getattr(settings, "PROXY_ROTATION_INTERVAL", 240) or 240)


def get_proxy_manager_instance() -> Optional[ProxyManager]:
    """Return shared ProxyManager when proxies are configured, else None."""
    urls = get_configured_proxy_urls()
    if not urls:
        return None
    try:
        return get_proxy_manager(urls, _rotation_interval())
    except ValueError as exc:
        logger.warning("Invalid proxy configuration: %s", exc)
        return None


def get_current_proxy_url(*, rotate_if_due: bool = True) -> Optional[str]:
    """
    Active proxy URL for this process, or None for direct connection.

    Args:
        rotate_if_due: Advance to next proxy when rotation interval elapsed.
    """
    manager = get_proxy_manager_instance()
    if not manager:
        return None
    if rotate_if_due and manager.should_rotate():
        manager.rotate_proxy()
    return manager.get_current_proxy()


def get_requests_proxies(proxy_url: Optional[str] = None) -> Optional[dict]:
    """requests/urllib3 proxies dict, or None for direct connection."""
    url = proxy_url or get_current_proxy_url()
    if not url:
        return None
    return {"http": url, "https": url}


def configure_requests_session(session) -> Optional[str]:
    """Attach proxies to a requests.Session. Returns proxy URL if set."""
    proxies = get_requests_proxies()
    if proxies:
        session.proxies.update(proxies)
        return proxies["http"]
    return None


def build_proxy_auth_extension(proxy_url: str) -> str:
    """
    Chrome extension zip for authenticated HTTP proxies (headless-safe).

    Returns path to proxy_auth_extension.zip inside a temp directory.
    """
    parsed = urlparse(proxy_url)
    if not parsed.hostname or not parsed.port:
        raise ValueError("Invalid proxy URL; must include host and port")

    username = parsed.username or ""
    password = parsed.password or ""
    host = parsed.hostname
    port = int(parsed.port)

    manifest = {
        "version": "1.0",
        "manifest_version": 2,
        "name": "ProxyAuth",
        "permissions": [
            "proxy",
            "tabs",
            "unlimitedStorage",
            "storage",
            "<all_urls>",
            "webRequest",
            "webRequestBlocking",
        ],
        "background": {"scripts": ["background.js"]},
        "minimum_chrome_version": "88.0",
    }

    background_js = f"""
    const config = {{
      mode: "fixed_servers",
      rules: {{
        singleProxy: {{ scheme: "http", host: "{host}", port: {port} }},
        bypassList: ["localhost", "127.0.0.1"]
      }}
    }};
    chrome.proxy.settings.set({{ value: config, scope: "regular" }}, function() {{}});

    function callbackFn(details) {{
      return {{ authCredentials: {{ username: "{username}", password: "{password}" }} }};
    }}
    chrome.webRequest.onAuthRequired.addListener(
      callbackFn,
      {{ urls: ["<all_urls>"] }},
      ["blocking"]
    );
    """

    temp_dir = tempfile.mkdtemp(prefix="career_proxy_ext_")
    manifest_path = os.path.join(temp_dir, "manifest.json")
    bg_path = os.path.join(temp_dir, "background.js")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f)
    with open(bg_path, "w", encoding="utf-8") as f:
        f.write(background_js)

    zip_path = os.path.join(temp_dir, "proxy_auth_extension.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(manifest_path, arcname="manifest.json")
        zf.write(bg_path, arcname="background.js")
    return zip_path


def _remove_chrome_argument(chrome_options, flag: str) -> None:
    """Remove a CLI flag from Selenium Chrome Options if present."""
    try:
        args = chrome_options.arguments
        if flag in args:
            chrome_options.arguments.remove(flag)
    except Exception:
        pass


def apply_proxy_to_chrome_options(chrome_options) -> Optional[str]:
    """
    Apply optional proxy to Selenium Chrome options.

    - Authenticated proxy: Chrome extension (removes --disable-extensions).
    - No auth: --proxy-server.

    Returns proxy URL when applied, else None.
    """
    proxy_url = get_current_proxy_url()
    if not proxy_url:
        return None

    manager = get_proxy_manager_instance()
    parsed = urlparse(proxy_url)
    masked = manager._mask_proxy(proxy_url) if manager else "***"

    try:
        if parsed.username and parsed.password:
            ext_zip = build_proxy_auth_extension(proxy_url)
            ext_dir = os.path.dirname(ext_zip)
            _remove_chrome_argument(chrome_options, "--disable-extensions")
            chrome_options.add_argument(f"--load-extension={ext_dir}")
            chrome_options.add_argument(f"--disable-extensions-except={ext_dir}")
            logger.info("Career scrape using authenticated proxy (extension): %s", masked)
        elif parsed.hostname and parsed.port:
            scheme = parsed.scheme or "http"
            chrome_options.add_argument(
                f"--proxy-server={scheme}://{parsed.hostname}:{parsed.port}"
            )
            logger.info("Career scrape using proxy: %s", masked)
        else:
            logger.warning("Invalid proxy URL skipped: %s", masked)
            return None
    except Exception as exc:
        logger.warning("Failed to apply proxy to Chrome: %s", exc)
        if manager:
            manager.mark_proxy_failure(proxy_url)
        return None

    return proxy_url


def mark_proxy_success(proxy_url: Optional[str] = None) -> None:
    manager = get_proxy_manager_instance()
    if manager:
        manager.mark_proxy_success(proxy_url)


def mark_proxy_failure(proxy_url: Optional[str] = None) -> None:
    manager = get_proxy_manager_instance()
    if manager:
        manager.mark_proxy_failure(proxy_url)


def reset_proxy_state() -> None:
    """Reset global manager (tests)."""
    reset_proxy_manager()
