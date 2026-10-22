"""Invalidate admin monitoring on every incoming message or callback."""
from aiogram import BaseMiddleware

from bot.utils.admin import is_admin
from bot.utils.admin_monitoring_view import (
    CURRENT_MONITORING_TURN, begin_monitoring_turn, release_monitoring_turn,
)


class AdminMonitoringMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        state = data.get('state')
        user = event.from_user
        if state is None or user is None or not is_admin(user.id):
            return await handler(event, data)
        turn = await begin_monitoring_turn(state)
        if not turn.current():
            return None
        turn.handling = True
        token = CURRENT_MONITORING_TURN.set(turn)
        try:
            return await handler(event, data)
        finally:
            CURRENT_MONITORING_TURN.reset(token)
            turn.handling = False
            release_monitoring_turn(turn)
