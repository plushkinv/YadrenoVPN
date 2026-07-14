"""Runtime helpers for browser-facing Telegram links."""
from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, quote, urlparse

logger = logging.getLogger(__name__)

DEFAULT_TELEGRAM_LINK_DOMAIN = "telegram.me"
TELEGRAM_LINK_DOMAIN_SETTING = "telegram_link_domain"

_DOMAIN_RE = re.compile(
    r"(?=.{1,253}\Z)"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)
_LEGACY_TELEGRAM_LINK_DOMAINS = frozenset({"t.me", DEFAULT_TELEGRAM_LINK_DOMAIN})

_telegram_link_domain = DEFAULT_TELEGRAM_LINK_DOMAIN


def normalize_telegram_link_domain(value: object) -> str:
    """Returns a safe bare domain for Telegram links."""
    raw = str(value or "").strip()
    if not raw:
        return DEFAULT_TELEGRAM_LINK_DOMAIN

    if "://" in raw:
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"}:
            return DEFAULT_TELEGRAM_LINK_DOMAIN
        if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
            return DEFAULT_TELEGRAM_LINK_DOMAIN
        candidate = parsed.netloc
    else:
        candidate = raw.strip("/")
        if "/" in candidate or "?" in candidate or "#" in candidate:
            return DEFAULT_TELEGRAM_LINK_DOMAIN

    candidate = candidate.lower().rstrip(".")
    if _DOMAIN_RE.fullmatch(candidate):
        return candidate
    return DEFAULT_TELEGRAM_LINK_DOMAIN


def load_telegram_link_domain() -> str:
    """Loads the Telegram link domain from settings into runtime memory."""
    from database.requests import get_setting

    raw = get_setting(TELEGRAM_LINK_DOMAIN_SETTING, DEFAULT_TELEGRAM_LINK_DOMAIN)
    domain = normalize_telegram_link_domain(raw)
    if domain == DEFAULT_TELEGRAM_LINK_DOMAIN and str(raw or "").strip() not in {
        "",
        DEFAULT_TELEGRAM_LINK_DOMAIN,
        f"https://{DEFAULT_TELEGRAM_LINK_DOMAIN}",
        f"https://{DEFAULT_TELEGRAM_LINK_DOMAIN}/",
    }:
        logger.warning(
            "Invalid %s value %r, using %s",
            TELEGRAM_LINK_DOMAIN_SETTING,
            raw,
            DEFAULT_TELEGRAM_LINK_DOMAIN,
        )

    global _telegram_link_domain
    _telegram_link_domain = domain
    logger.info("Telegram link domain loaded: %s", _telegram_link_domain)
    return _telegram_link_domain


def get_telegram_link_domain() -> str:
    """Returns the cached Telegram link domain."""
    return _telegram_link_domain


def get_telegram_link_base_url() -> str:
    """Returns the cached Telegram HTTPS link base URL without trailing slash."""
    return f"https://{get_telegram_link_domain()}"


def build_telegram_link(username: object | None = None, start: object | None = None) -> str:
    """Builds a browser-facing Telegram URL using the cached domain."""
    base_url = get_telegram_link_base_url()
    if not username:
        return base_url

    clean_username = str(username).strip().lstrip("@").strip("/")
    if not clean_username:
        return base_url

    url = f"{base_url}/{quote(clean_username, safe='')}"
    start_value = str(start or "").strip()
    if start_value:
        url = f"{url}?start={quote(start_value, safe='_-')}"
    return url


def is_telegram_bot_start_link(
    value: object,
    *,
    bot_username: str,
    start_prefix: str,
) -> bool:
    """Checks that a URL points to a Telegram bot with the required start prefix."""
    if not isinstance(value, str):
        return False

    parsed = urlparse(value.strip())
    if parsed.scheme != "https":
        return False

    allowed_domains = set(_LEGACY_TELEGRAM_LINK_DOMAINS)
    allowed_domains.add(get_telegram_link_domain())
    if parsed.netloc.lower() not in allowed_domains:
        return False

    path_username = parsed.path.strip("/")
    if path_username.lower() != bot_username.strip("@").lower():
        return False

    start_values = parse_qs(parsed.query, keep_blank_values=True).get("start") or []
    return any(start_value.startswith(start_prefix) for start_value in start_values)
