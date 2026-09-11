"""Process-local polling ownership for requests already accepted by the Hub."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator

RequestLaneKey = tuple[int, int]


@dataclass
class SatelliteLaneCycle:
    """One accepted request; only the matching poll may be stopped."""

    key: RequestLaneKey
    request_id: int
    poll_stop_requested: asyncio.Event = field(default_factory=asyncio.Event)


class SatelliteLaneController:
    """Track polling without queueing submissions or deciding admission."""

    def __init__(self) -> None:
        self._cycles: dict[RequestLaneKey, SatelliteLaneCycle] = {}

    def is_active(self, key: RequestLaneKey) -> bool:
        return key in self._cycles

    @asynccontextmanager
    async def cycle(
        self, key: RequestLaneKey, request_id: int,
    ) -> AsyncIterator[SatelliteLaneCycle | None]:
        current = self._cycles.get(key)
        if current is not None and current.request_id == request_id:
            yield None
            return
        cycle = SatelliteLaneCycle(key, request_id)
        self._cycles[key] = cycle
        try:
            yield cycle
        finally:
            if self._cycles.get(key) is cycle:
                self._cycles.pop(key)

    def signal_poll_stop(self, key: RequestLaneKey, request_id: int) -> bool:
        cycle = self._cycles.get(key)
        if cycle is None or cycle.request_id != request_id:
            return False
        cycle.poll_stop_requested.set()
        return True


satellite_lane_controller = SatelliteLaneController()
