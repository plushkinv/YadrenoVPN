"""Process-local coordination for VPN key and panel mutations."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any, AsyncIterator, Callable, TypeVar


_AsyncCallable = TypeVar("_AsyncCallable", bound=Callable[..., Any])


class PanelSyncCoordinator:
    """Writer-priority gate for regular mutations and manual synchronization.

    Regular operations may run concurrently. A manual synchronization waits for
    already running regular operations, prevents new ones from starting, and
    then runs exclusively. Context-local reentrancy lets existing services call
    guarded lower-level helpers without deadlocking.
    """

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._active_regular = 0
        self._manual_waiting = False
        self._manual_active = False
        self._lease_mode: ContextVar[tuple[str, object] | None] = ContextVar(
            "panel_sync_lease_mode",
            default=None,
        )

    def _current_mode(self) -> str | None:
        """Return a lease only to the task that acquired it.

        Context variables are copied into child tasks. Binding the value to the
        current task prevents a spawned background task from accidentally
        inheriting and bypassing its parent's gate.
        """
        lease = self._lease_mode.get()
        task = asyncio.current_task()
        if lease is None or task is None or lease[1] is not task:
            return None
        return lease[0]

    @property
    def manual_active(self) -> bool:
        return self._manual_active

    @property
    def manual_pending(self) -> bool:
        return self._manual_waiting or self._manual_active

    @asynccontextmanager
    async def regular(self) -> AsyncIterator[None]:
        """Enter a regular key/panel mutation lease."""
        current = self._current_mode()
        if current in {"regular", "manual"}:
            yield
            return

        async with self._condition:
            while self._manual_waiting or self._manual_active:
                await self._condition.wait()
            self._active_regular += 1

        token = self._lease_mode.set(("regular", asyncio.current_task()))
        try:
            yield
        finally:
            self._lease_mode.reset(token)
            async with self._condition:
                self._active_regular -= 1
                self._condition.notify_all()

    @asynccontextmanager
    async def try_manual(self) -> AsyncIterator[bool]:
        """Try to enter the single manual writer lease without queueing writers."""
        current = self._current_mode()
        if current == "manual":
            yield True
            return
        if current == "regular":
            raise RuntimeError("Cannot upgrade a regular panel-sync lease to manual")

        async with self._condition:
            if self._manual_waiting or self._manual_active:
                acquired = False
            else:
                self._manual_waiting = True
                acquired = True

        if not acquired:
            yield False
            return

        try:
            async with self._condition:
                while self._active_regular:
                    await self._condition.wait()
                self._manual_waiting = False
                self._manual_active = True

            token = self._lease_mode.set(("manual", asyncio.current_task()))
            try:
                yield True
            finally:
                self._lease_mode.reset(token)
                async with self._condition:
                    self._manual_active = False
                    self._condition.notify_all()
        except BaseException:
            async with self._condition:
                if self._manual_waiting:
                    self._manual_waiting = False
                    self._condition.notify_all()
            raise


panel_sync_coordinator = PanelSyncCoordinator()


def regular_panel_operation(func: _AsyncCallable) -> _AsyncCallable:
    """Wrap a complete async key mutation in one regular coordinator lease."""
    @wraps(func)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        async with panel_sync_coordinator.regular():
            return await func(*args, **kwargs)

    return wrapped  # type: ignore[return-value]


__all__ = [
    "PanelSyncCoordinator",
    "panel_sync_coordinator",
    "regular_panel_operation",
]
