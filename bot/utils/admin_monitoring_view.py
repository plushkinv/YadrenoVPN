"""Own one-shot admin monitoring delivery without owning panel requests."""
from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from dataclasses import dataclass, field

from bot.utils.text import safe_edit_or_send

logger = logging.getLogger(__name__)


@dataclass(eq=False)
class MonitoringTurn:
    key: tuple
    chat_id: int
    delivery_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    valid: bool = True
    handling: bool = False
    message_id: int | None = None
    waiter: asyncio.Task | None = None

    def current(self) -> bool:
        return not _stopping and self.valid and _turns.get(self.key) is self


_turns: dict[tuple, MonitoringTurn] = {}
_waiters: set[asyncio.Task] = set()
_stopping = False
CURRENT_MONITORING_TURN: ContextVar[MonitoringTurn | None] = ContextVar(
    'admin_monitoring_turn', default=None,
)


def release_monitoring_turn(turn: MonitoringTurn) -> None:
    if turn.current() and not turn.handling and turn.waiter is None:
        _turns.pop(turn.key, None)


async def begin_monitoring_turn(state) -> MonitoringTurn:
    # Any subsequent input from this administrator invalidates the old opening,
    # including input in another chat. The actual delivery also binds chat/message.
    key = (state.key.bot_id, state.key.user_id)
    if _stopping:
        return MonitoringTurn(key, state.key.chat_id, valid=False)
    old = _turns.get(key)
    turn = MonitoringTurn(key, state.key.chat_id)
    if old is not None:
        old.valid = False
        turn.delivery_lock = old.delivery_lock
    _turns[key] = turn
    async with turn.delivery_lock:
        # A Telegram edit already in flight finishes before the new handler runs.
        if old is not None and old.waiter is not None:
            old.waiter.cancel()
    return turn


async def monitoring_turn(state) -> MonitoringTurn:
    """Keep the original input's turn when helpers run after asynchronous work."""
    turn = CURRENT_MONITORING_TURN.get()
    return turn if turn is not None else await begin_monitoring_turn(state)


async def show_monitoring_screen(message, turn, poll, render, *, deliver=safe_edit_or_send):
    """Send local data, bind the returned message, then own one background waiter."""
    async with turn.delivery_lock:
        if not turn.current():
            return
        snapshot = poll.local_snapshot()
        payload = render(snapshot)
        if payload is None:
            return
        text, keyboard = payload
        shown = await deliver(message, text, reply_markup=keyboard)
        turn.chat_id = shown.chat.id
        turn.message_id = shown.message_id

    if not turn.current() or not snapshot['loading']:
        release_monitoring_turn(turn)
        return

    async def refresh():
        try:
            while turn.current():
                await poll.collect()
                async with turn.delivery_lock:
                    if not turn.current():
                        return
                    # Read again after acquiring the lock: another administrator
                    # may have edited/deleted a server while delivery was queued.
                    snapshot = poll.resolved_snapshot()
                    if snapshot is None:
                        continue
                    payload = render(snapshot)
                    if payload is not None:
                        if (shown.chat.id, shown.message_id) != (turn.chat_id, turn.message_id):
                            return
                        text, keyboard = payload
                        await deliver(shown, text, reply_markup=keyboard, edit_only=True)
                    return
        except Exception:
            logger.warning('Could not refresh admin monitoring message', exc_info=True)
        finally:
            turn.waiter = None
            release_monitoring_turn(turn)

    turn.waiter = asyncio.create_task(refresh(), name='admin_monitoring_view')
    _waiters.add(turn.waiter)
    turn.waiter.add_done_callback(_waiters.discard)


async def stop_monitoring_views() -> None:
    """Finish all owned waiters before closing either transport."""
    global _stopping
    _stopping = True
    for turn in _turns.values():
        turn.valid = False
    tasks = list(_waiters)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _waiters.clear()
    _turns.clear()


def start_monitoring_views() -> None:
    global _stopping
    _stopping = False
