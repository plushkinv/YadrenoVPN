"""Borrowed Telegram access and lifecycle ownership for installed extensions."""
from __future__ import annotations

import copy
import inspect
from functools import wraps
from typing import Any

from aiogram import Bot
from aiogram.client.context_controller import BotContextController
from aiogram.methods import TelegramMethod
from pydantic import BaseModel


BLOCKED_TELEGRAM_METHODS = frozenset({
    'getUpdates', 'setWebhook', 'deleteWebhook', 'close', 'logOut',
})
_BLOCKED_METHOD_KEYS = frozenset(name.casefold() for name in BLOCKED_TELEGRAM_METHODS)
_BLOCKED_NAMES = frozenset({'get_updates', 'set_webhook', 'delete_webhook', 'close', 'log_out'})
_CONTROL_ATTRIBUTES = frozenset({'token', 'session', 'default', 'context'})


def in_extension_context() -> bool:
    from bot.utils.custom_extensions import _CURRENT_EXTENSION
    return _CURRENT_EXTENSION.get() is not None


def deny_transport_control(operation: str) -> None:
    raise PermissionError(f'Telegram transport is managed by core; {operation} is unavailable to extensions')


def check_transport_control(operation: str) -> None:
    if in_extension_context():
        deny_transport_control(operation)


def check_telegram_method(method: TelegramMethod, *, borrowed: bool = False) -> None:
    if (borrowed or in_extension_context()) and method.__api_method__.casefold() in _BLOCKED_METHOD_KEYS:
        deny_transport_control(method.__api_method__)


def _require_active() -> None:
    from runtime.readiness import require_active
    require_active()


class _ImmutableAccess:
    __slots__ = ()

    def __setattr__(self, name, value):
        deny_transport_control(f'assign {name}')

    def __delattr__(self, name):
        deny_transport_control(f'delete {name}')


def _borrow_result(value: Any, client: ExtensionBot, memo: dict[int, Any]) -> Any:
    """Copy model trees without deep-copying their original bot/session."""
    if id(value) in memo:
        return memo[id(value)]
    if isinstance(value, BaseModel):
        result = value.model_copy()
        memo[id(value)] = result
        for name in type(value).model_fields:
            object.__setattr__(result, name, _borrow_result(getattr(value, name), client, memo))
        if value.model_extra is not None:
            object.__setattr__(result, '__pydantic_extra__', _borrow_result(value.model_extra, client, memo))
        if isinstance(result, BotContextController):
            result.as_(client)
        return result
    if isinstance(value, list):
        result = []
        memo[id(value)] = result
        result.extend(_borrow_result(item, client, memo) for item in value)
        return result
    if isinstance(value, dict):
        result = {}
        memo[id(value)] = result
        result.update((key, _borrow_result(item, client, memo)) for key, item in value.items())
        return result
    if isinstance(value, tuple):
        result = tuple(_borrow_result(item, client, memo) for item in value)
        memo[id(value)] = result
        return result
    return value


class ExtensionBot(_ImmutableAccess):
    """Aiogram operations on a borrowed bot, without owning its transport."""
    __slots__ = ('__bot',)

    def __init__(self, bot: Bot):
        object.__setattr__(self, '_ExtensionBot__bot', bot)

    @property
    def id(self) -> int:
        return self.__bot.id

    async def __call__(self, method: TelegramMethod, request_timeout: int | None = None):
        if not isinstance(method, TelegramMethod):
            raise TypeError('method must be an aiogram TelegramMethod')
        check_telegram_method(method, borrowed=True)
        _require_active()
        return _borrow_result(await self.__bot(method, request_timeout=request_timeout), self, {})

    def __getattr__(self, name: str):
        if name in _CONTROL_ATTRIBUTES or name in _BLOCKED_NAMES:
            deny_transport_control(name)
        member = getattr(Bot, name, None) if not name.startswith('_') else None
        if not inspect.iscoroutinefunction(member):
            raise AttributeError(name)

        @wraps(member)
        async def invoke(*args, **kwargs):
            _require_active()
            result = await getattr(self.__bot, name)(*args, **kwargs)
            return _borrow_result(result, self, {})

        return invoke

    async def __aenter__(self):
        deny_transport_control('bot context manager')

    async def __aexit__(self, *args):
        deny_transport_control('bot context manager')


class _BorrowedSession(_ImmutableAccess):
    """Compatibility view for a legacy target's session; no lifecycle access."""
    __slots__ = ('__bot', '__session')

    def __init__(self, bot: Bot, session):
        object.__setattr__(self, '_BorrowedSession__bot', bot)
        object.__setattr__(self, '_BorrowedSession__session', session)

    @property
    def api(self):
        return self.__session.api

    @property
    def timeout(self):
        return self.__session.timeout

    async def __call__(self, bot, method, timeout=None):
        check_telegram_method(method, borrowed=True)
        return await self.__bot(method, request_timeout=timeout)

    async def make_request(self, bot, method, timeout=None):
        return await self(bot, method, timeout)

    async def stream_content(self, *args, **kwargs):
        async for chunk in self.__session.stream_content(*args, **kwargs):
            yield chunk

    def __getattr__(self, name):
        deny_transport_control(f'session.{name}')

    async def __aenter__(self):
        deny_transport_control('session context manager')

    async def __aexit__(self, *args):
        deny_transport_control('session context manager')


class ManagedBot(Bot):
    """The single application Bot, retaining legacy target identity and methods."""
    _TRANSPORT_FIELDS = frozenset({
        'session', 'default', 'token', '_Bot__token', '_managed_session', '_managed_default',
        'runtime_application', 'background_tasks',
    })

    def __setattr__(self, name, value):
        if name in self._TRANSPORT_FIELDS:
            check_transport_control(f'bot.{name}')
        super().__setattr__(name, value)

    def __delattr__(self, name):
        if name in self._TRANSPORT_FIELDS:
            check_transport_control(f'bot.{name}')
        super().__delattr__(name)

    def __getattribute__(self, name):
        if name in {'runtime_application', 'background_tasks'}:
            check_transport_control(f'bot.{name}')
        return super().__getattribute__(name)

    @property
    def session(self):
        session = self._managed_session
        return _BorrowedSession(self, session) if in_extension_context() else session

    @session.setter
    def session(self, value):
        self._managed_session = value

    @property
    def default(self):
        defaults = self._managed_default
        return copy.deepcopy(defaults) if in_extension_context() else defaults

    @default.setter
    def default(self, value):
        self._managed_default = value

    async def __call__(self, method, request_timeout=None):
        check_telegram_method(method)
        return await self._managed_session(self, method, timeout=request_timeout)


def telegram_capabilities() -> dict:
    return {
        'contract_version': 1, 'factory': 'get_bot', 'library': 'aiogram',
        'transport': 'shared', 'recipient': 'telegram_chat_id_or_username',
        'blocked_methods': sorted(BLOCKED_TELEGRAM_METHODS),
        'transport_control': False, 'token_access': False,
    }
