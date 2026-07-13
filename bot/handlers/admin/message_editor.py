"""
Universal message editor router.

Processes:
- Incoming messages in the waiting_for_message state
- Callback help buttons (msg_editor_show_help)
- Callback buttons to return to preview (msg_editor_back_to_preview)
"""
import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext

from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.utils.text import safe_edit_or_send
from bot.utils.message_editor import (
    get_message_data, save_message_data, delete_message_media, detect_message_type,
    editor_kb, editor_help_kb, send_editor_message,
)

logger = logging.getLogger(__name__)

router = Router()


async def show_message_editor(
    message: Message,
    state: FSMContext,
    key: str,
    back_callback: str,
    help_text: str = None,
    allowed_types: list = None,
) -> Message:
    """Shows a preview of the message with editor buttons.
    
    Preview = message exactly as it will look to the user.
    No headings, borders or instructions.
    
    Uses send_editor_message() for rendering - a single HTML contract.
    Saves the context to FSM data.
    
    Args:
        message: Message to edit (callback.message or answer result)
        state: FSM context
        key: Settings key in settings
        back_callback: callback_data for the back button
        help_text: Help text (optional)
        allowed_types: Allowed media types (default all)
    
    Returns:
        Message object after rendering (to be saved in FSM)
    """
    if allowed_types is None:
        allowed_types = ['text', 'photo', 'video', 'animation']
    
    message_data = get_message_data(key)
    media_type = message_data.get('media_type')
    can_delete_media = bool(message_data.get('media_file_id')) and media_type in allowed_types

    # Forming the editor keyboard
    kb = editor_kb(
        back_callback,
        has_help=bool(help_text),
        can_delete_media=can_delete_media,
    )
    
    # Show preview via send_editor_message (single HTML helper)
    result = await send_editor_message(
        message,
        data=message_data,
        reply_markup=kb,
    )
    
    # Saving context in FSM
    await state.set_state(AdminStates.waiting_for_message)
    await state.update_data(
        editing_key=key,
        editor_message=result,  # Message object to redraw
        back_callback=back_callback,
        allowed_types=allowed_types,
        help_text=help_text,
    )
    
    return result


# ============================================================================
# CALLBACK: EDITOR HELP
# ============================================================================

@router.callback_query(F.data == "msg_editor_show_help")
async def show_editor_help(callback: CallbackQuery, state: FSMContext):
    """Shows editor help (if help_text is passed)."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    help_text = data.get('help_text', '')
    
    if not help_text:
        await callback.answer()
        return
    
    # Help is a service text (not from the editor), sent via safe_edit_or_send
    result = await safe_edit_or_send(
        callback.message,
        help_text,
        reply_markup=editor_help_kb()
    )
    
    # Updating a saved message
    await state.update_data(editor_message=result)
    await callback.answer()

@router.callback_query(F.data == "msg_editor_noop_alert")
async def show_editor_noop_alert(callback: CallbackQuery):
    """Shows a pop-up explanation if there is no separate help."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
        
    await callback.answer(
        "📝 Чтобы изменить текст, просто отправьте боту новое сообщение.\n\n"
        "Вы можете прикрепить фото, видео или GIF. Если медиа уже есть, новый текст сохранит его.",
        show_alert=True
    )


@router.callback_query((F.data == "msg_editor_delete_media") | (F.data == "msg_editor_delete_photo"))
async def delete_editor_media(callback: CallbackQuery, state: FSMContext):
    """Removes media from the currently edited message."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    data = await state.get_data()
    key = data.get('editing_key')
    back_callback = data.get('back_callback')
    help_text = data.get('help_text')
    allowed_types = data.get('allowed_types', ['text', 'photo', 'video', 'animation'])

    if not key:
        await callback.answer("❌ Ошибка состояния", show_alert=True)
        return

    if not any(media_type in allowed_types for media_type in ['photo', 'video', 'animation']):
        await callback.answer()
        return

    delete_message_media(key)

    await show_message_editor(
        callback.message, state,
        key=key,
        back_callback=back_callback,
        help_text=help_text,
        allowed_types=allowed_types,
    )
    await callback.answer()

@router.callback_query(F.data == "msg_editor_back_to_preview")
async def back_to_preview(callback: CallbackQuery, state: FSMContext):
    """Return to preview from help."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    key = data.get('editing_key')
    back_callback = data.get('back_callback')
    help_text = data.get('help_text')
    allowed_types = data.get('allowed_types')
    
    if not key:
        await callback.answer("❌ Ошибка состояния", show_alert=True)
        return
    
    # Redrawing the preview
    await show_message_editor(
        callback.message, state,
        key=key,
        back_callback=back_callback,
        help_text=help_text,
        allowed_types=allowed_types,
    )
    await callback.answer()


# ============================================================================
# MESSAGE HANDLER: RECEIVING A NEW MESSAGE
# ============================================================================

@router.message(AdminStates.waiting_for_message, ~F.text.startswith('/'))
async def handle_editor_input(message: Message, state: FSMContext):
    """
    Processes an incoming message when editing.
    
    1. Checks message type vs allowed_types
    2. Saves to the database via save_message_data()
    3. Deletes a user's message
    4. Redraws the preview (without the “Saved” notification)
    """
    if not is_admin(message.from_user.id):
        return
    
    data = await state.get_data()
    key = data.get('editing_key')
    back_callback = data.get('back_callback')
    help_text = data.get('help_text')
    allowed_types = data.get('allowed_types', ['text', 'photo', 'video', 'animation'])
    editor_message = data.get('editor_message')
    
    if not key:
        await state.clear()
        await safe_edit_or_send(message, "❌ Ошибка состояния.")
        return
    
    # Checking the message type
    msg_type = detect_message_type(message)
    if msg_type not in allowed_types:
        # Silently delete an inappropriate message
        try:
            await message.delete()
        except Exception:
            pass
        return
    
    # Saving in the database
    save_message_data(key, message, allowed_types)
    
    # Delete the user's message (pattern from AGENTS.md)
    try:
        await message.delete()
    except Exception:
        pass
    
    # Redrawing the preview in place of the old message
    if editor_message:
        try:
            result = await show_message_editor(
                editor_message, state,
                key=key,
                back_callback=back_callback,
                help_text=help_text,
                allowed_types=allowed_types,
            )
            return
        except Exception as e:
            logger.warning(f"Ошибка перерисовки превью: {e}")
    
    # Fallback: send a new message
    result = await show_message_editor(
        message, state,
        key=key,
        back_callback=back_callback,
        help_text=help_text,
        allowed_types=allowed_types,
    )
