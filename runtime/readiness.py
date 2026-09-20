"""One activation fence for adapters, direct services and database writes."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import sqlite3
import threading
from typing import Iterator

from core.results import CoreError

_managed = False
_active = False
_initializing: ContextVar[tuple[int, object] | None] = ContextVar('runtime_initialization', default=None)
_WRITE_ACTIONS = frozenset(getattr(sqlite3, name) for name in (
    'SQLITE_INSERT', 'SQLITE_UPDATE', 'SQLITE_DELETE', 'SQLITE_CREATE_INDEX',
    'SQLITE_CREATE_TABLE', 'SQLITE_CREATE_TEMP_INDEX', 'SQLITE_CREATE_TEMP_TABLE',
    'SQLITE_CREATE_TEMP_TRIGGER', 'SQLITE_CREATE_TEMP_VIEW', 'SQLITE_CREATE_TRIGGER',
    'SQLITE_CREATE_VIEW', 'SQLITE_DROP_INDEX', 'SQLITE_DROP_TABLE', 'SQLITE_DROP_TEMP_INDEX',
    'SQLITE_DROP_TEMP_TABLE', 'SQLITE_DROP_TEMP_TRIGGER', 'SQLITE_DROP_TEMP_VIEW',
    'SQLITE_DROP_TRIGGER', 'SQLITE_DROP_VIEW', 'SQLITE_ALTER_TABLE', 'SQLITE_REINDEX',
    'SQLITE_CREATE_VTABLE', 'SQLITE_DROP_VTABLE',
))


def _owner() -> tuple[int, object]:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.get_ident(), task


def is_active() -> bool:
    """Standalone maintenance keeps its existing contract; managed ingress waits."""
    return not _managed or _active


def require_active() -> None:
    if not is_active():
        raise CoreError('temporarily_unavailable', retryable=True)


def begin_startup() -> None:
    global _managed, _active
    if _managed:
        raise RuntimeError('A runtime already owns this process')
    _managed, _active = True, False


def activate() -> None:
    global _active
    if not _managed:
        raise RuntimeError('Runtime startup has not begun')
    _active = True


def deactivate() -> None:
    global _active
    _active = False


def release_runtime() -> None:
    """Release process-local ownership after every server/task has stopped."""
    global _managed, _active
    _managed, _active = False, False


@contextmanager
def initialization_writes() -> Iterator[None]:
    """Permit only the bootstrap task, never an inherited HTTP/worker context."""
    if not _managed or _active:
        raise RuntimeError('Initialization is only allowed before activation')
    token = _initializing.set(_owner())
    try:
        yield
    finally:
        _initializing.reset(token)


def sqlite_authorizer(action, first, second, database, trigger) -> int:
    if action in _WRITE_ACTIONS and not is_active() and _initializing.get() != _owner():
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


class RuntimeConnection(sqlite3.Connection):
    """Also fence cached statements prepared during initialization/activation."""

    def _fence(self) -> None:
        if not is_active():
            self.set_authorizer(sqlite_authorizer)

    def execute(self, *args, **kwargs):
        return self.cursor().execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        return self.cursor().executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        return self.cursor().executescript(*args, **kwargs)

    def cursor(self, factory=None):
        return super().cursor(factory or RuntimeCursor)


class RuntimeCursor(sqlite3.Cursor):
    def execute(self, *args, **kwargs):
        self.connection._fence()
        return super().execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        self.connection._fence()
        return super().executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        self.connection._fence()
        return super().executescript(*args, **kwargs)
