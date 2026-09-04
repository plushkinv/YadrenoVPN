"""Process-local lifecycle coordination for one Satellite conversation lane."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal


RequestLaneKey = tuple[int, int]
LanePhase = Literal["starting", "posting", "polling", "tool", "cancelling"]


@dataclass
class SatelliteLaneCycle:
    """The single cycle currently owning a ``(user, topic)`` lane."""

    key: RequestLaneKey
    phase: LanePhase = "starting"
    request_id: int | None = None
    cancel_requested: asyncio.Event = field(default_factory=asyncio.Event)
    poll_stop_requested: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass(frozen=True)
class SatelliteLaneCancelTarget:
    """Local cycle selected by the lane-scoped Cancel command."""

    status: Literal["idle", "active"]
    request_id: int | None = None
    phase: LanePhase | None = None


@dataclass
class _SatelliteLaneState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    waiting: int = 0
    current: SatelliteLaneCycle | None = None


class SatelliteLaneController:
    """Own locks, cancellation and poll wake-up for all local Satellite lanes.

    Telegram controls are intentionally lane-scoped. A Cancel button always
    targets the cycle that is current when the command is handled; cards do not
    carry process-local request identities.
    """

    def __init__(self) -> None:
        self._lanes: dict[RequestLaneKey, _SatelliteLaneState] = {}

    def _state(self, key: RequestLaneKey) -> _SatelliteLaneState:
        return self._lanes.setdefault(key, _SatelliteLaneState())

    def _discard_idle(self, key: RequestLaneKey, state: _SatelliteLaneState) -> None:
        if (
            self._lanes.get(key) is state
            and state.current is None
            and state.waiting == 0
            and not state.lock.locked()
        ):
            self._lanes.pop(key, None)

    def current(self, key: RequestLaneKey) -> SatelliteLaneCycle | None:
        """Return the current process-local cycle without creating lane state."""
        state = self._lanes.get(key)
        return state.current if state is not None else None

    def is_active(self, key: RequestLaneKey) -> bool:
        """Return whether a cycle owns or is waiting for this lane."""
        state = self._lanes.get(key)
        return bool(
            state is not None
            and (state.current is not None or state.waiting > 0 or state.lock.locked())
        )

    def is_starting(self, key: RequestLaneKey) -> bool:
        """Return whether local work exists but has no accepted Hub id yet."""
        state = self._lanes.get(key)
        if state is None:
            return False
        current = state.current
        return bool(
            current is not None
            and current.request_id is None
            and current.phase in {"starting", "posting", "cancelling"}
        )

    @asynccontextmanager
    async def cycle(self, key: RequestLaneKey) -> AsyncIterator[SatelliteLaneCycle]:
        """Serialize one complete request cycle and release it on every exit."""
        state = self._state(key)
        state.waiting += 1
        waiting_counted = True
        acquired = False
        cycle: SatelliteLaneCycle | None = None
        try:
            await state.lock.acquire()
            acquired = True
            state.waiting -= 1
            waiting_counted = False

            cycle = SatelliteLaneCycle(key=key)
            state.current = cycle
            yield cycle
        finally:
            if waiting_counted:
                state.waiting = max(0, state.waiting - 1)
            if cycle is not None and state.current is cycle:
                state.current = None
            if acquired:
                state.lock.release()
            self._discard_idle(key, state)

    @asynccontextmanager
    async def new_chat(self, key: RequestLaneKey) -> AsyncIterator[bool]:
        """Run Session reset exclusively, or report that local work is busy."""
        state = self._state(key)
        if state.current is not None or state.waiting > 0 or state.lock.locked():
            try:
                yield False
            finally:
                self._discard_idle(key, state)
            return

        await state.lock.acquire()
        try:
            yield True
        finally:
            state.lock.release()
            self._discard_idle(key, state)

    def request_cancel(self, key: RequestLaneKey) -> SatelliteLaneCancelTarget:
        """Request cancellation of the cycle that is current for the lane."""
        state = self._lanes.get(key)
        if state is None:
            return SatelliteLaneCancelTarget(status="idle")

        cycle = state.current
        if cycle is not None:
            phase = cycle.phase
            cycle.cancel_requested.set()
            if cycle.request_id is None:
                cycle.phase = "cancelling"
            return SatelliteLaneCancelTarget(
                status="active",
                request_id=cycle.request_id,
                phase=phase,
            )

        return SatelliteLaneCancelTarget(status="idle")

    def signal_poll_stop(
        self,
        key: RequestLaneKey,
        request_id: int,
    ) -> bool:
        """Wake only the current poll when it owns the expected Hub request."""
        cycle = self.current(key)
        if cycle is None or cycle.request_id != int(request_id):
            return False
        cycle.poll_stop_requested.set()
        return True


satellite_lane_controller = SatelliteLaneController()
