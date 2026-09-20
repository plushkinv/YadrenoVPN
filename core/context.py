"""Trusted adapter identity; never construct it from a client-supplied actor."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator, Literal

AccountSource = Literal['telegram', 'site', 'mini_app', 'system']


@dataclass(frozen=True, slots=True)
class AccountContext:
    """One internal owner, with an optional real Telegram identity."""

    account_id: int
    telegram_id: int | None
    source: AccountSource
    operation_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.account_id) is not int or self.account_id <= 0:
            raise ValueError('account_id must be a positive integer')
        if self.telegram_id is not None and (
            type(self.telegram_id) is not int or self.telegram_id <= 0
        ):
            raise ValueError('telegram_id must be a positive integer or None')
        if self.source not in {'telegram', 'site', 'mini_app', 'system'}:
            raise ValueError('unsupported account source')
        if self.source in {'telegram', 'mini_app'} and self.telegram_id is None:
            raise ValueError('Telegram adapters require a real Telegram identity')
        if self.operation_id is not None and (
            not isinstance(self.operation_id, str) or not self.operation_id
        ):
            raise ValueError('operation_id must be a non-empty string or None')


_CURRENT_ACCOUNT: ContextVar[AccountContext | None] = ContextVar(
    'core_account_context', default=None,
)


def get_account_context() -> AccountContext | None:
    """Return only identity bound by an authenticated adapter or core operation."""
    return _CURRENT_ACCOUNT.get()


@contextmanager
def bind_account_context(context: AccountContext | None) -> Iterator[None]:
    """Keep identity scoped across awaits, exceptions and nested operations."""
    if context is not None and not isinstance(context, AccountContext):
        raise TypeError('context must be AccountContext or None')
    token = _CURRENT_ACCOUNT.set(context)
    try:
        yield
    finally:
        _CURRENT_ACCOUNT.reset(token)
