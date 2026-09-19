"""Process-local first-input context, separate from an accepted Hub conversation."""
from __future__ import annotations

from dataclasses import dataclass

from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.services.page_context import PageContext
from bot.states.admin_states import AdminStates
from bot.keyboards.admin_yadreno import yadreno_admin_input_kb
from bot.services.yadreno_admin import YadrenoAdminError
from bot.utils.text import safe_edit_or_send
from bot.utils.yadreno_admin_errors import format_yadreno_admin_error

PENDING_YAA_INPUT_KEY = "yadreno_pending_input"


@dataclass(eq=False)
class PendingYaaInput:
    """One invitation in the bot's MemoryStorage; never persisted or sent to Hub."""

    topic_id: int
    page_context: PageContext | None
    return_state: str | None
    prompt_message: Message | None = None
    submissions: int = 0


async def get_pending_yaa_input(state: FSMContext) -> PendingYaaInput | None:
    """Ignore old data after navigation has left the local input state."""
    get_state = getattr(state, "get_state", None)
    if not callable(get_state) or await get_state() != AdminStates.yadreno_waiting_task.state:
        return None
    pending = (await state.get_data()).get(PENDING_YAA_INPUT_KEY)
    return pending if isinstance(pending, PendingYaaInput) else None


async def accept_yaa_input(state: FSMContext, pending: PendingYaaInput) -> None:
    """Transition only the invitation that belongs to this accepted request."""
    if await get_pending_yaa_input(state) is pending:
        await state.update_data(**{PENDING_YAA_INPUT_KEY: None, "yadreno_topic_id": pending.topic_id})
        await state.set_state(AdminStates.yadreno_chat)


async def close_yaa_input(state: FSMContext, pending: PendingYaaInput) -> None:
    """Close an unsent local invitation without resetting a Hub session."""
    if await get_pending_yaa_input(state) is pending:
        await state.update_data(**{PENDING_YAA_INPUT_KEY: None})
        await state.set_state(pending.return_state)


async def render_yaa_input_error(
    message: Message, state: FSMContext, pending: PendingYaaInput | None,
    error: YadrenoAdminError, keyboard, *, force_new: bool = False,
) -> Message:
    """Keep local retry/exit available without losing Hub busy or key controls."""
    if pending is not None and await get_pending_yaa_input(state) is pending:
        if error.kind != "configuration":
            keyboard = yadreno_admin_input_kb(keyboard if error.cancel_button_text else None)
        message = pending.prompt_message or message
        force_new = False
    rendered = await safe_edit_or_send(
        message, format_yadreno_admin_error(error), reply_markup=keyboard, force_new=force_new,
    )
    if pending is not None:
        pending.prompt_message = rendered
    return rendered
