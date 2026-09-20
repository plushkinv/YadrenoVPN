"""Identity linking for the originating browser session and this installation's bot."""
from __future__ import annotations

import re
import secrets
import time

from core.auth import secret_hash
from core.results import CoreError
from database import requests as db
from runtime.readiness import require_active

_TOKEN = re.compile(r'[A-Za-z0-9_-]{32}\Z')


def installation_bot_id() -> int:
    from config import BOT_TOKEN
    try:
        return int(BOT_TOKEN.partition(':')[0])
    except (AttributeError, ValueError):
        raise CoreError('link_unavailable') from None


def token_hash(token: str) -> str:
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        raise CoreError('link_invalid')
    return secret_hash(token)


def start_link(session: dict) -> dict:
    require_active()
    from bot.utils.telegram_links import build_telegram_link
    bot_id = installation_bot_id()
    username = db.get_setting('web_bot_username', '')
    if db.get_setting('web_bot_id', '') != str(bot_id) or not re.fullmatch(r'[A-Za-z0-9_]{5,32}', username):
        raise CoreError('link_unavailable', retryable=True)
    now = int(time.time())
    if not db.consume_auth_limits([(f"link:{session['user_id']}", 10, 600)], now):
        raise CoreError('rate_limited', retryable=True)
    token = secrets.token_urlsafe(24)
    db.create_account_link(token_hash=token_hash(token), session_hash=session['token_hash'],
                           user_id=session['user_id'], bot_id=bot_id, now=now)
    return {'token': token, 'telegram_url': build_telegram_link(username, 'link_' + token),
            'expires_at': now + 600}


def telegram_action(token: str, actor: dict, *, bot_id: int, action: str) -> dict:
    require_active()
    if bot_id != installation_bot_id() or type(actor.get('id')) is not int or actor['id'] <= 0:
        raise CoreError('link_invalid')
    args = {'token_hash': token_hash(token), 'bot_id': bot_id, 'now': int(time.time())}
    if action == 'confirm':
        return db.confirm_account_link_telegram(**args, actor=actor)
    if action == 'cancel':
        db.cancel_account_link(**args, telegram_id=actor['id'])
        return {'state': 'cancelled'}
    if action == 'inspect':
        return db.inspect_account_link(**args, telegram_id=actor['id'])
    raise CoreError('link_invalid')


def link_status(session: dict, token: str) -> dict:
    return db.get_account_link_status(token_hash=token_hash(token), session_hash=session['token_hash'],
                                      bot_id=installation_bot_id(), now=int(time.time()))


def finish_link(session: dict, token: str, telegram_id: int) -> dict:
    require_active()
    if type(telegram_id) is not int or telegram_id <= 0:
        raise CoreError('invalid_request')
    return db.finish_account_link(token_hash=token_hash(token), session_hash=session['token_hash'],
                                   bot_id=installation_bot_id(), telegram_id=telegram_id, now=int(time.time()))
