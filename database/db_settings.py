import sqlite3
import logging
import json
import secrets
import string
import datetime
import re
from typing import Optional, List, Dict, Any, Tuple
from .connection import get_db

logger = logging.getLogger(__name__)

__all__ = [
    'get_setting',
    'set_setting',
    'delete_setting',
    'is_update_notifications_enabled',
    'get_display_timezone',
    'set_display_timezone',
    'normalize_display_timezone',
    'get_yadreno_admin_api_key',
    'set_yadreno_admin_api_key',
    'delete_yadreno_admin_api_key',
    'get_yadreno_admin_server_ip',
    'set_yadreno_admin_server_ip',
    'delete_yadreno_admin_server_ip',
    'is_yadreno_admin_customization_enabled',
    'is_yadreno_admin_core_changes_enabled',
    'get_yadreno_admin_active_request_id',
    'set_yadreno_admin_active_request_id',
    'clear_yadreno_admin_active_request_id',
    'list_yadreno_admin_active_requests',
    'get_yadreno_admin_last_request_id',
    'set_yadreno_admin_last_request_id',
    'clear_yadreno_admin_last_request_id',
    'mark_yadreno_admin_tool_call_started',
    'clear_yadreno_admin_tool_call_started',
    'set_yadreno_admin_tool_runtime',
    'clear_yadreno_admin_tool_runtime',
    'list_yadreno_admin_tool_runtime',
    'is_crypto_enabled',
    'is_stars_enabled',
    'is_crypto_configured',
    'is_cards_enabled',
    'is_cards_configured',
    'is_yookassa_qr_enabled',
    'is_yookassa_qr_configured',
    'get_yookassa_credentials',
    'is_wata_enabled',
    'is_wata_configured',
    'get_wata_token',
    'is_platega_enabled',
    'is_platega_configured',
    'get_platega_credentials',
    'is_cardlink_enabled',
    'is_cardlink_configured',
    'get_cardlink_credentials',
    'is_trial_enabled',
    'get_trial_tariff_id',
    'is_demo_payment_enabled',
]

DEFAULT_DISPLAY_TIMEZONE = 'Europe/Moscow'
DISPLAY_TIMEZONE_SETTING = 'display_timezone'
UPDATE_NOTIFICATIONS_ENABLED_SETTING = 'update_notifications_enabled'

_TIMEZONE_ALIASES = {
    'москва': DEFAULT_DISPLAY_TIMEZONE,
    'мск': DEFAULT_DISPLAY_TIMEZONE,
    'moscow': DEFAULT_DISPLAY_TIMEZONE,
    'msk': DEFAULT_DISPLAY_TIMEZONE,
    'utc': 'UTC',
    'gmt': 'UTC',
}
_UTC_OFFSET_RE = re.compile(r'^(?:utc|gmt)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?$')

def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    """
    Gets the setting value.
    
    Args:
        key: Setting key
        default: Default value
        
    Returns:
        Setting value or default
    """
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,)
        )
        row = cursor.fetchone()
        return row['value'] if row else default

def set_setting(key: str, value: str) -> None:
    """
    Sets the setting value.
    
    Args:
        key: Setting key
        value: Setting value
    """
    with get_db() as conn:
        conn.execute("""
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (key, value))
        logger.info(f"Настройка обновлена: {key}")

def delete_setting(key: str) -> bool:
    """
    Removes a setting.
    
    Args:
        key: Setting key
        
    Returns:
        True if the setting was removed
    """
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        return cursor.rowcount > 0


def is_update_notifications_enabled() -> bool:
    """Returns the state of hidden new version notifications."""
    return get_setting(UPDATE_NOTIFICATIONS_ENABLED_SETTING, '1') == '1'


def normalize_display_timezone(value: Optional[str]) -> str:
    """Normalizes the hidden time zone setting for displaying dates."""
    raw = (value or '').strip()
    if not raw:
        return DEFAULT_DISPLAY_TIMEZONE

    key = raw.lower().replace('ё', 'е')
    compact_key = key.replace(' ', '')
    if key in _TIMEZONE_ALIASES:
        return _TIMEZONE_ALIASES[key]
    if compact_key in _TIMEZONE_ALIASES:
        return _TIMEZONE_ALIASES[compact_key]

    match = _UTC_OFFSET_RE.match(compact_key)
    if match:
        sign, hours_raw, minutes_raw = match.groups()
        hours = int(hours_raw)
        minutes = int(minutes_raw or '0')
        if hours <= 23 and minutes <= 59:
            return f'UTC{sign}{hours:02d}:{minutes:02d}'

    if '/' in raw and all(part for part in raw.split('/')):
        return raw

    return DEFAULT_DISPLAY_TIMEZONE


def get_display_timezone() -> str:
    """Returns the time zone in which the bot displays dates to users and admins."""
    return normalize_display_timezone(
        get_setting(DISPLAY_TIMEZONE_SETTING, DEFAULT_DISPLAY_TIMEZONE)
    )


def set_display_timezone(value: str) -> str:
    """Preserves the display time zone and returns a normalized value."""
    timezone_value = normalize_display_timezone(value)
    set_setting(DISPLAY_TIMEZONE_SETTING, timezone_value)
    return timezone_value


YADRENO_ADMIN_API_KEY_SETTING = 'yadreno_admin_api_key'
YADRENO_ADMIN_SERVER_IP_SETTING = 'yadreno_admin_server_ip'
YADRENO_ADMIN_CUSTOMIZATION_ENABLED_SETTING = 'yadreno_admin_customization_enabled'
YADRENO_ADMIN_CORE_CHANGES_ENABLED_SETTING = 'yadreno_admin_core_changes_enabled'
YADRENO_ADMIN_REQUEST_SETTING_PREFIX = 'yadreno_admin_request'
YADRENO_ADMIN_TOOL_CALL_SETTING_PREFIX = 'yadreno_admin_tool_call'
YADRENO_ADMIN_TOOL_RUNTIME_SETTING_PREFIX = 'yadreno_admin_tool_runtime'


def get_yadreno_admin_api_key() -> Optional[str]:
    """Returns the general Yadreno Admin api_key for this Telegram bot."""
    return get_setting(YADRENO_ADMIN_API_KEY_SETTING)


def set_yadreno_admin_api_key(api_key: str) -> None:
    """Saves the general api_key Yadreno Admin in settings."""
    set_setting(YADRENO_ADMIN_API_KEY_SETTING, api_key)


def delete_yadreno_admin_api_key() -> bool:
    """Removes the general api_key Yadreno Admin from settings."""
    return delete_setting(YADRENO_ADMIN_API_KEY_SETTING)


def get_yadreno_admin_server_ip() -> str:
    """Returns the saved public IP of the server for Yadreno Admin."""
    return get_setting(YADRENO_ADMIN_SERVER_IP_SETTING, '') or ''


def set_yadreno_admin_server_ip(server_ip: str) -> None:
    """Saves the public IP of the server for Yadreno Admin in settings."""
    set_setting(YADRENO_ADMIN_SERVER_IP_SETTING, server_ip.strip())


def delete_yadreno_admin_server_ip() -> bool:
    """Removes the saved public IP of the Yadreno Admin server from settings."""
    return delete_setting(YADRENO_ADMIN_SERVER_IP_SETTING)


def is_yadreno_admin_customization_enabled() -> bool:
    """Return the hidden flag that shows the YadrenoVPN customization entry."""
    return get_setting(YADRENO_ADMIN_CUSTOMIZATION_ENABLED_SETTING, '0') == '1'


def is_yadreno_admin_core_changes_enabled() -> bool:
    """Return the hidden flag that allows core/source changes via Yadreno Admin."""
    return get_setting(YADRENO_ADMIN_CORE_CHANGES_ENABLED_SETTING, '0') == '1'


def _yadreno_admin_request_key(kind: str, telegram_id: int, topic_id: int) -> str:
    """Settings key for request_id in lane Yadreno Admin."""
    return (
        f'{YADRENO_ADMIN_REQUEST_SETTING_PREFIX}:'
        f'{kind}:{int(telegram_id)}:{int(topic_id)}'
    )


def _get_yadreno_admin_request_id(kind: str, telegram_id: int, topic_id: int) -> Optional[int]:
    """Reads request_id Yadreno Admin from settings."""
    raw = get_setting(_yadreno_admin_request_key(kind, telegram_id, topic_id))
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def get_yadreno_admin_active_request_id(telegram_id: int, topic_id: int) -> Optional[int]:
    """Returns active request_id Yadreno Admin from settings."""
    return _get_yadreno_admin_request_id('active', telegram_id, topic_id)


def set_yadreno_admin_active_request_id(telegram_id: int, topic_id: int, request_id: int) -> None:
    """Saves active request_id Yadreno Admin in settings."""
    set_setting(
        _yadreno_admin_request_key('active', telegram_id, topic_id),
        str(int(request_id)),
    )


def clear_yadreno_admin_active_request_id(telegram_id: int, topic_id: int) -> bool:
    """Removes active request_id Yadreno Admin from settings."""
    return delete_setting(_yadreno_admin_request_key('active', telegram_id, topic_id))


def list_yadreno_admin_active_requests() -> List[Dict[str, int]]:
    """Returns all saved active request_ids by lane."""
    prefix = f'{YADRENO_ADMIN_REQUEST_SETTING_PREFIX}:active:'
    with get_db() as conn:
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE ?",
            (f'{prefix}%',),
        ).fetchall()

    result: List[Dict[str, int]] = []
    for row in rows:
        suffix = str(row['key'])[len(prefix):]
        parts = suffix.split(':')
        if len(parts) != 2:
            continue
        try:
            result.append({
                'telegram_id': int(parts[0]),
                'topic_id': int(parts[1]),
                'request_id': int(row['value']),
            })
        except (TypeError, ValueError):
            continue
    return result


def get_yadreno_admin_last_request_id(telegram_id: int, topic_id: int) -> Optional[int]:
    """Returns last request_id Yadreno Admin from settings."""
    return _get_yadreno_admin_request_id('last', telegram_id, topic_id)


def set_yadreno_admin_last_request_id(telegram_id: int, topic_id: int, request_id: int) -> None:
    """Saves last request_id Yadreno Admin in settings."""
    set_setting(
        _yadreno_admin_request_key('last', telegram_id, topic_id),
        str(int(request_id)),
    )


def clear_yadreno_admin_last_request_id(telegram_id: int, topic_id: int) -> bool:
    """Removes last request_id Yadreno Admin from settings."""
    return delete_setting(_yadreno_admin_request_key('last', telegram_id, topic_id))


def _yadreno_admin_tool_call_key(request_id: int, tool_call_id: str) -> str:
    """The settings key for a locally started tool_call."""
    return (
        f'{YADRENO_ADMIN_TOOL_CALL_SETTING_PREFIX}:'
        f'{int(request_id)}:{tool_call_id}'
    )


def mark_yadreno_admin_tool_call_started(request_id: int, tool_call_id: str) -> bool:
    """
    Marks tool_call as started.

    True = record has been created now and can be executed.
    False = the recording has already been made and cannot be repeated.
    """
    key = _yadreno_admin_tool_call_key(request_id, tool_call_id)
    if get_setting(key):
        return False
    set_setting(key, datetime.datetime.utcnow().isoformat(timespec='seconds'))
    return True


def clear_yadreno_admin_tool_call_started(request_id: int, tool_call_id: str) -> bool:
    """Removes the started flag from tool_call after the result has been successfully sent."""
    return delete_setting(_yadreno_admin_tool_call_key(request_id, tool_call_id))


def _yadreno_admin_tool_runtime_key(request_id: int, tool_call_id: str) -> str:
    """The settings key for the runtime state of the running tool_call."""
    return (
        f'{YADRENO_ADMIN_TOOL_RUNTIME_SETTING_PREFIX}:'
        f'{int(request_id)}:{tool_call_id}'
    )


def set_yadreno_admin_tool_runtime(
    request_id: int,
    tool_call_id: str,
    tool: str,
    *,
    topic_id: int = 0,
    pid: Optional[int] = None,
    started_at: Optional[str] = None,
) -> None:
    """Stores a local runtime token tool_call for safe cancellation."""
    payload = {
        'request_id': int(request_id),
        'tool_call_id': str(tool_call_id),
        'tool': str(tool),
        'topic_id': int(topic_id),
        'pid': int(pid) if pid is not None else None,
        'started_at': started_at or datetime.datetime.utcnow().isoformat(timespec='seconds'),
    }
    set_setting(
        _yadreno_admin_tool_runtime_key(request_id, tool_call_id),
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
    )


def clear_yadreno_admin_tool_runtime(request_id: int, tool_call_id: str) -> bool:
    """Removes the tool_call runtime marker after the result is sent normally."""
    return delete_setting(_yadreno_admin_tool_runtime_key(request_id, tool_call_id))


def list_yadreno_admin_tool_runtime(
    request_id: Optional[int] = None,
    topic_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Returns runtime markers of locally executed tool_calls."""
    prefix = f'{YADRENO_ADMIN_TOOL_RUNTIME_SETTING_PREFIX}:'
    with get_db() as conn:
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE ?",
            (f'{prefix}%',),
        ).fetchall()

    result: List[Dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row['value'])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        try:
            payload_request_id = int(payload.get('request_id') or 0)
            payload_topic_id = int(payload.get('topic_id') or 0)
        except (TypeError, ValueError):
            continue
        if request_id is not None and payload_request_id != int(request_id):
            continue
        if topic_id is not None and payload_topic_id != int(topic_id):
            continue
        payload['request_id'] = payload_request_id
        payload['topic_id'] = payload_topic_id
        result.append(payload)
    return result

def is_crypto_enabled() -> bool:
    """Checks if crypto payments are enabled."""
    return get_setting('crypto_enabled', '0') == '1'

def is_stars_enabled() -> bool:
    """Checks if Telegram Stars is enabled."""
    return get_setting('stars_enabled', '0') == '1'

def is_crypto_configured() -> bool:
    """
    Checks whether crypto payments are fully configured.
    
    Returns:
        True if crypto is included AND there is a link to the product (for standard mode) or just included
    """
    if not is_crypto_enabled():
        return False
    crypto_item_url = get_setting('crypto_item_url')
    return bool(crypto_item_url and crypto_item_url.strip())



def is_cards_enabled() -> bool:
    """Checks if TG payments are enabled."""
    return get_setting('cards_enabled', '0') == '1'

def is_cards_configured() -> bool:
    """
    Checks if TG payments are configured.
    
    Returns:
        True if TG payments are enabled AND there is a provider_token
    """
    if not is_cards_enabled():
        return False
    token = get_setting('cards_provider_token')
    return bool(token and token.strip())

def is_yookassa_qr_enabled() -> bool:
    """Checks whether direct payment through YuKassa is enabled."""
    return get_setting('yookassa_qr_enabled', '0') == '1'

def is_yookassa_qr_configured() -> bool:
    """
    Checks whether direct payment through YuKassa is fully configured.

    Returns:
        True if YuKassa is enabled AND there is shop_id and secret_key
    """
    if not is_yookassa_qr_enabled():
        return False
    shop_id = get_setting('yookassa_shop_id', '')
    secret_key = get_setting('yookassa_secret_key', '')
    return bool(shop_id and shop_id.strip() and secret_key and secret_key.strip())

def get_yookassa_credentials() -> tuple[str, str]:
    """
    Returns YuKass credentials for the direct API.

    Returns:
        Tuple (shop_id, secret_key)
    """
    shop_id = get_setting('yookassa_shop_id', '')
    secret_key = get_setting('yookassa_secret_key', '')
    return shop_id, secret_key

def is_wata_enabled() -> bool:
    """Checks whether payment via WATA is enabled."""
    return get_setting('wata_enabled', '0') == '1'

def is_wata_configured() -> bool:
    """
    Checks whether payment via WATA is fully configured.

    Returns:
        True if WATA is enabled AND a JWT token is specified
    """
    if not is_wata_enabled():
        return False
    token = get_setting('wata_jwt_token', '')
    return bool(token and token.strip())

def get_wata_token() -> str:
    """
    Returns the JWT token for the WATA API.

    Returns:
        JWT token string (or empty string)
    """
    return get_setting('wata_jwt_token', '') or ''

def is_platega_enabled() -> bool:
    """Checks if payment via Platega is enabled."""
    return get_setting('platega_enabled', '0') == '1'

def is_platega_configured() -> bool:
    """
    Checks whether payment via Platega is fully configured.

    Returns:
        True if Platega is enabled AND merchant_id and secret are specified
    """
    if not is_platega_enabled():
        return False
    merchant_id = get_setting('platega_merchant_id', '')
    secret = get_setting('platega_secret', '')
    return bool(merchant_id and merchant_id.strip() and secret and secret.strip())

def get_platega_credentials() -> tuple[str, str]:
    """
    Returns Platega credentials for the direct API.

    Returns:
        Tuple (merchant_id, secret)
    """
    merchant_id = get_setting('platega_merchant_id', '')
    secret = get_setting('platega_secret', '')
    return merchant_id, secret

def is_cardlink_enabled() -> bool:
    """Checks if payment via Cardlink is enabled."""
    return get_setting('cardlink_enabled', '0') == '1'

def is_cardlink_configured() -> bool:
    """
    Checks whether payment via Cardlink is fully configured.

    Returns:
        True if Cardlink is enabled AND shop_id and api_token are specified
    """
    if not is_cardlink_enabled():
        return False
    shop_id = get_setting('cardlink_shop_id', '')
    token = get_setting('cardlink_api_token', '')
    return bool(shop_id and shop_id.strip() and token and token.strip())

def get_cardlink_credentials() -> tuple[str, str]:
    """
    Returns Cardlink credentials for the direct API.

    Returns:
        Tuple (shop_id, api_token)
    """
    shop_id = get_setting('cardlink_shop_id', '')
    token = get_setting('cardlink_api_token', '')
    return shop_id, token

def is_trial_enabled() -> bool:
    """Is the trial subscription feature enabled?"""
    return get_setting('trial_enabled', '0') == '1'

def get_trial_tariff_id() -> Optional[int]:
    """
    Returns the tariff ID for a trial subscription.
    
    Returns:
        Rate ID or None if no rate is specified
    """
    val = get_setting('trial_tariff_id', '')
    return int(val) if val and val.isdigit() else None

def is_demo_payment_enabled() -> bool:
    """Is demo payment by RF card included?"""
    return get_setting('demo_payment_enabled', '0') == '1'
