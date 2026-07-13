"""Formatting UTC dates from SQLite to display in the bot's time zone."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
import re
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from database.db_settings import DEFAULT_DISPLAY_TIMEZONE, get_display_timezone

_UTC_OFFSET_RE = re.compile(r'^UTC([+-])(\d{2}):(\d{2})$')


def _timezone_from_setting(value: str) -> tzinfo:
    """Returns tzinfo for the normalized setting value."""
    match = _UTC_OFFSET_RE.match(value)
    if match:
        sign, hours_raw, minutes_raw = match.groups()
        minutes = int(hours_raw) * 60 + int(minutes_raw)
        if sign == '-':
            minutes = -minutes
        return timezone(timedelta(minutes=minutes), name=value)

    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError:
        if value == DEFAULT_DISPLAY_TIMEZONE:
            return timezone(timedelta(hours=3), name='MSK')
        return timezone(timedelta(hours=3), name='MSK')


def get_display_tzinfo() -> tzinfo:
    """Returns tzinfo to the current setting, safely falling back to Moscow."""
    try:
        timezone_name = get_display_timezone()
    except Exception:
        timezone_name = DEFAULT_DISPLAY_TIMEZONE
    return _timezone_from_setting(timezone_name)


def _parse_utc_datetime(value: Any) -> Optional[datetime]:
    if value is None or value == '':
        return None

    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(get_display_tzinfo())


def format_datetime_for_display(value: Any, fallback: str = '—') -> str:
    """Formats the date and time from UTC to the display time zone."""
    dt = _parse_utc_datetime(value)
    if dt is None:
        return fallback if value is None or value == '' else str(value)
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def format_date_for_display(value: Any, fallback: str = '—') -> str:
    """Formats only the date after converting UTC to the display time zone."""
    dt = _parse_utc_datetime(value)
    if dt is None:
        return fallback if value is None or value == '' else str(value)
    return dt.strftime('%Y-%m-%d')
