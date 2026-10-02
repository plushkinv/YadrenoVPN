"""Process-local input turns, FSM ownership and cancellation-aware page delivery."""
from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from uuid import uuid4

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.states.user_states import ExtensionInput
from bot.utils.extension_inputs import (
    EXTENSION_INPUT_HANDLERS, dispatch_extension_input, input_handler_key,
    normalize_input_request,
)
from bot.utils.page_renderer import (
    PageRenderMissing, PreparedPageRender, deliver_page_prepare_outcome, prepare_page_render, render_page,
)
from bot.utils.text import get_message_text_for_storage, safe_edit_or_send
from database.requests import get_page_route, resolve_renderable_page

logger = logging.getLogger(__name__)
WAITING = ExtensionInput.waiting.state


@dataclass
class PendingInput:
    extension_id: str
    request: dict
    prompt: dict
    input_id: str = field(default_factory=lambda: uuid4().hex)
    claimed: bool = False


@dataclass
class InputTurn:
    state: FSMContext
    delivery_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: PendingInput | None = None
    valid: bool = True
    active_updates: int = 0
    callback_answered: bool = False

    @property
    def key(self):
        return self.state.storage, self.state.key

    def current(self) -> bool:
        return self.valid and _TURNS.get(self.key) is self


_TURNS: dict[tuple, InputTurn] = {}
CURRENT_INPUT_TURN: ContextVar[InputTurn | None] = ContextVar('extension_input_turn', default=None)


def get_input_turn(state: FSMContext) -> InputTurn | None:
    return _TURNS.get((state.storage, state.key))


async def begin_input_turn(state: FSMContext) -> InputTurn:
    """Invalidate first; finish an already-started send before the new action runs."""
    old = get_input_turn(state)
    if old is not None:
        old.valid = False
    turn = InputTurn(state, delivery_lock=old.delivery_lock if old else asyncio.Lock())
    _TURNS[turn.key] = turn
    async with turn.delivery_lock:
        if turn.current() and await state.get_state() == WAITING:
            await state.clear()
    return turn


def release_input_turn(turn: InputTurn) -> None:
    if turn.current() and turn.pending is None and turn.active_updates == 0:
        _TURNS.pop(turn.key, None)


async def _clear_waiting(turn: InputTurn) -> None:
    async with turn.delivery_lock:
        if turn.current():
            turn.pending = None
            if await turn.state.get_state() == WAITING:
                await turn.state.clear()


def _sender(turn: InputTurn):
    async def send(*args, **kwargs):
        async with turn.delivery_lock:
            if not turn.current():
                return None
            return await safe_edit_or_send(*args, **kwargs)
    return send


async def _unavailable(target, turn: InputTurn) -> None:
    await _clear_waiting(turn)
    if turn.current():
        await render_page(
            target, 'action_unavailable', force_new=isinstance(target, Message),
            send_func=_sender(turn),
        )


async def _show_result(target, result: dict, extension_id: str, turn: InputTurn) -> bool:
    """Use the typed render outcome so denial/fallback can never arm a prompt."""
    if not turn.current():
        return False
    page_key, route_key = result.get('page_key'), result.get('route_key')
    if route_key:
        route = get_page_route(route_key)
        if not route or not route.get('is_enabled'):
            raise LookupError('input route is unavailable')
        page_key = route.get('page_key')
    if not page_key:
        return False
    if not resolve_renderable_page(page_key, warn_unknown=True):
        raise LookupError('input page is unavailable')
    context = {
        **deepcopy(result.get('context') or {}),
        'telegram_id': target.from_user.id,
        'extension_id': extension_id,
    }
    outcome = await prepare_page_render(target, page_key, route_key=route_key, context=context)
    if not turn.current():
        return False
    if isinstance(outcome, PageRenderMissing):
        raise LookupError('input page is unavailable')
    delivery = await deliver_page_prepare_outcome(
        target, outcome, force_new=isinstance(target, Message),
        send_func=_sender(turn), fallback_context=context,
    )
    turn.callback_answered |= delivery.callback_answered
    return isinstance(outcome, PreparedPageRender) and delivery.message is not None and turn.current()


async def apply_input_result(target, result: dict, extension_id: str, state: FSMContext) -> bool:
    """Apply only the new input branch of existing interactive result contracts."""
    turn = CURRENT_INPUT_TURN.get()
    if turn is None or turn.key != (state.storage, state.key):
        raise RuntimeError('input requires an interactive dispatcher context')
    try:
        normalize_input_request(result)
        request = result.get('input')
        if request is not None and input_handler_key(extension_id, request['handler']) not in EXTENSION_INPUT_HANDLERS:
            raise LookupError('input handler is unavailable')
        await _clear_waiting(turn)
        shown = await _show_result(target, result, extension_id, turn)
        if shown and request is not None:
            async with turn.delivery_lock:
                if turn.current():
                    # Store detached prompt data in the existing in-memory FSM.
                    pending = PendingInput(extension_id, deepcopy(request), deepcopy(result))
                    await state.set_state(ExtensionInput.waiting)
                    await state.update_data(extension_input={
                        'extension_id': extension_id, 'input_id': pending.input_id,
                        'request': deepcopy(request), 'prompt': deepcopy(result),
                    })
                    turn.pending = pending
        return shown
    except Exception as exc:
        logger.warning('Extension input result failed type=%s', type(exc).__name__)
        await _unavailable(target, turn)
        return False
    finally:
        if isinstance(target, CallbackQuery) and turn.current() and not turn.callback_answered:
            await target.answer()
            turn.callback_answered = True


async def accept_input_message(message: Message, turn: InputTurn, pending: PendingInput | None) -> None:
    """Claim the captured step once; cancellation fences all later UI results."""
    if not turn.current() or pending is None or turn.pending is not pending or pending.claimed:
        return
    pending.claimed = True
    try:
        if input_handler_key(pending.extension_id, pending.request['handler']) not in EXTENSION_INPUT_HANDLERS:
            raise LookupError('input handler is unavailable')
        if message.text is None:
            shown = await _show_result(message, pending.prompt, pending.extension_id, turn)
            if shown and turn.current():
                pending.claimed = False
            else:
                await _clear_waiting(turn)
            return
        result = await dispatch_extension_input(
            pending.extension_id, pending.request['handler'], {
                'contract_version': 1, 'extension_id': pending.extension_id,
                'telegram_id': message.from_user.id, 'input_id': pending.input_id,
                'text': get_message_text_for_storage(message, 'plain'),
                'payload': deepcopy(pending.request['payload']),
            }, bot=message.bot,
        )
        if turn.current():
            await apply_input_result(message, result, pending.extension_id, turn.state)
    except Exception as exc:
        logger.warning('Extension input handler failed type=%s', type(exc).__name__)
        await _unavailable(message, turn)
    except asyncio.CancelledError:
        await _clear_waiting(turn)
        raise
