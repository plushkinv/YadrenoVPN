"""Ordinary message ingress for explicitly requested extension input."""
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.states.user_states import ExtensionInput
from bot.utils.extension_input_runtime import accept_input_message
from bot.utils.page_renderer import render_page
from bot.utils.user_pages import render_access_blocked_page
from database.requests import is_user_banned

router = Router()


@router.message(ExtensionInput.waiting)
async def extension_input_handler(
    message: Message, state: FSMContext, extension_input_turn=None, extension_input_pending=None,
) -> None:
    if extension_input_turn is not None and not extension_input_turn.current():
        return
    if is_user_banned(message.from_user.id):
        await state.clear()
        if extension_input_turn is not None:
            extension_input_turn.pending = None
        await render_access_blocked_page(message, force_new=True)
        return
    if extension_input_turn is None:
        await state.clear()
        await render_page(message, 'action_unavailable', force_new=True)
        return
    await accept_input_message(message, extension_input_turn, extension_input_pending)
