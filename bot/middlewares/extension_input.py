"""Cancel only extension-owned input before command/callback routing and guards."""
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from bot.utils.extension_input_runtime import (
    CURRENT_INPUT_TURN, WAITING, begin_input_turn, get_input_turn, release_input_turn,
)


class ExtensionInputMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        state = data.get('state')
        if state is None:
            return await handler(event, data)
        is_command = isinstance(event, Message) and (event.text or event.caption or '').lstrip().startswith('/')
        starts_turn = isinstance(event, CallbackQuery) or is_command
        if starts_turn:
            turn = await begin_input_turn(state)
            if not turn.current():
                return None
        else:
            turn = get_input_turn(state)
            if await state.get_state() != WAITING:
                turn = None
        # FSM middleware read this before cancellation; filters need the current value.
        data['raw_state'] = await state.get_state()
        data['extension_input_turn'] = turn
        data['extension_input_pending'] = turn.pending if turn else None
        if turn is not None:
            turn.active_updates += 1
        token = CURRENT_INPUT_TURN.set(turn)
        try:
            return await handler(event, data)
        finally:
            CURRENT_INPUT_TURN.reset(token)
            if turn is not None:
                turn.active_updates -= 1
                release_input_turn(turn)
