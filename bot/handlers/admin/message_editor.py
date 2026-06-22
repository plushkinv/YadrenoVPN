"""
Роутер универсального редактора сообщений.

Обрабатывает:
- Входящие сообщения в состоянии waiting_for_message
- Callback кнопки справки (msg_editor_show_help)
- Callback кнопки возврата к превью (msg_editor_back_to_preview)
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
    """Показывает превью сообщения с кнопками редактора.
    
    Превью = сообщение ровно так, как оно будет выглядеть для пользователя.
    Без заголовков, рамок и инструкций.
    
    Использует send_editor_message() для рендера — единый контракт HTML.
    Сохраняет контекст в FSM data.
    
    Args:
        message: Сообщение для редактирования (callback.message или результат answer)
        state: FSM контекст
        key: Ключ настройки в settings
        back_callback: callback_data для кнопки «Назад»
        help_text: Текст справки (опционально)
        allowed_types: Допустимые типы медиа (по умолчанию все)
    
    Returns:
        Объект Message после рендера (для сохранения в FSM)
    """
    if allowed_types is None:
        allowed_types = ['text', 'photo', 'video', 'animation']
    
    message_data = get_message_data(key)
    media_type = message_data.get('media_type')
    can_delete_media = bool(message_data.get('media_file_id')) and media_type in allowed_types

    # Формируем клавиатуру редактора
    kb = editor_kb(
        back_callback,
        has_help=bool(help_text),
        can_delete_media=can_delete_media,
    )
    
    # Показываем превью через send_editor_message (единый HTML helper)
    result = await send_editor_message(
        message,
        data=message_data,
        reply_markup=kb,
    )
    
    # Сохраняем контекст в FSM
    await state.set_state(AdminStates.waiting_for_message)
    await state.update_data(
        editing_key=key,
        editor_message=result,  # Message объект для перерисовки
        back_callback=back_callback,
        allowed_types=allowed_types,
        help_text=help_text,
    )
    
    return result


# ============================================================================
# CALLBACK: СПРАВКА РЕДАКТОРА
# ============================================================================

@router.callback_query(F.data == "msg_editor_show_help")
async def show_editor_help(callback: CallbackQuery, state: FSMContext):
    """Показывает справку редактора (если help_text передан)."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    help_text = data.get('help_text', '')
    
    if not help_text:
        await callback.answer()
        return
    
    # Справка — это служебный текст (не из редактора), отправляем через safe_edit_or_send
    result = await safe_edit_or_send(
        callback.message,
        help_text,
        reply_markup=editor_help_kb()
    )
    
    # Обновляем сохранённое сообщение
    await state.update_data(editor_message=result)
    await callback.answer()

@router.callback_query(F.data == "msg_editor_noop_alert")
async def show_editor_noop_alert(callback: CallbackQuery):
    """Показывает всплывающее пояснение, если нет отдельной справки."""
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
    """Удаляет медиа из текущего редактируемого сообщения."""
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
    """Возврат к превью из справки."""
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
    
    # Перерисовываем превью
    await show_message_editor(
        callback.message, state,
        key=key,
        back_callback=back_callback,
        help_text=help_text,
        allowed_types=allowed_types,
    )
    await callback.answer()


# ============================================================================
# MESSAGE HANDLER: ПРИЁМ НОВОГО СООБЩЕНИЯ
# ============================================================================

@router.message(AdminStates.waiting_for_message, ~F.text.startswith('/'))
async def handle_editor_input(message: Message, state: FSMContext):
    """
    Обрабатывает входящее сообщение при редактировании.
    
    1. Проверяет тип сообщения vs allowed_types
    2. Сохраняет в БД через save_message_data()
    3. Удаляет сообщение пользователя
    4. Перерисовывает превью (без уведомления «Сохранено»)
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
    
    # Проверяем тип сообщения
    msg_type = detect_message_type(message)
    if msg_type not in allowed_types:
        # Молча удаляем неподходящее сообщение 
        try:
            await message.delete()
        except Exception:
            pass
        return
    
    # Сохраняем в БД
    save_message_data(key, message, allowed_types)
    
    # Удаляем сообщение пользователя (паттерн из AGENTS.md)
    try:
        await message.delete()
    except Exception:
        pass
    
    # Перерисовываем превью на месте старого сообщения
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
    
    # Фоллбэк: отправляем новое сообщение
    result = await show_message_editor(
        message, state,
        key=key,
        back_callback=back_callback,
        help_text=help_text,
        allowed_types=allowed_types,
    )
