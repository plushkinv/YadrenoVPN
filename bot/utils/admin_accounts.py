"""Explicit account selectors keep old numeric Telegram callbacks unambiguous."""
from database import requests as db


def account_selector(user):
    return user['telegram_id'] if user.get('telegram_id') is not None else 'account_' + str(user.get('user_id', user.get('id')))


def selected_user(selector):
    if isinstance(selector, str) and selector.startswith('account_'):
        value = selector.removeprefix('account_')
        return db.get_user_by_id(int(value)) if value.isdecimal() else None
    try:
        return db.get_user_by_telegram_id(int(selector))
    except (ValueError, TypeError):
        return None


def identity_line(user):
    if user.get('telegram_id') is not None:
        return f"📱 ID: <code>{user['telegram_id']}</code>"
    return f"🌐 Аккаунт: <code>{user['id']}</code>"
