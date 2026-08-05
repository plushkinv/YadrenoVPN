"""
Handlers for the “Trial subscription” section in the admin panel.

Trial feature management:
- On/off
- Editing page text
- Select a tariff (including inactive ones, except Admin Tariff)
"""
import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter

from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.utils.text import escape_html, safe_edit_or_send

logger = logging.getLogger(__name__)

router = Router()


# ============================================================================
# AUXILIARY FUNCTION: DISPLAYING MENU
# ============================================================================

async def show_trial_menu(callback: CallbackQuery):
    """Shows the trial subscription settings menu."""
    from database.requests import (
        get_tariff_by_id, get_trial_tariff_id, is_trial_enabled
    )
    from bot.keyboards.admin import trial_settings_kb

    enabled = is_trial_enabled()
    tariff_id = get_trial_tariff_id()
    tariff_name = None

    if tariff_id:
        tariff = get_tariff_by_id(tariff_id)
        if tariff:
            status = "🟢" if tariff['is_active'] else "⚪"
            duration = 'Без срока' if int(tariff['duration_days']) == 0 else f"{tariff['duration_days']} дн."
            tariff_name = f"{status} {tariff['name']} ({duration})"

    status_text = "🟢 Включена" if enabled else "⚪ Выключена"
    tariff_text = escape_html(tariff_name) if tariff_name else "<i>не задан</i>"

    text = (
        "🎁 <b>Пробная подписка</b>\n\n"
        "Настройки основного пробного предложения.\n\n"
        f"📌 <b>Статус:</b> {escape_html(status_text)}\n"
        f"📋 <b>Тариф:</b> {tariff_text}\n\n"
        "❓ <b>Как работает:</b>\n"
        "• Если предложение включено и тариф задан — штатная кнопка появляется у пользователей, которым оно доступно.\n"
        "• При активации — пользователю выдаётся ключ с выбранным тарифом.\n"
        "• Переключатель управляет только основным предложением; дополнительные настраиваются через Yadreno Admin.\n"
        "• Редактируемая страница подтверждения общая для всех пробных предложений."
    )

    await safe_edit_or_send(callback.message,
        text,
        reply_markup=trial_settings_kb(enabled, tariff_name)
    )
    await callback.answer()


# ============================================================================
# MAIN SCREEN FOR TRIAL SUBSCRIPTION
# ============================================================================

@router.callback_query(F.data == "admin_trial")
async def admin_trial_menu(callback: CallbackQuery):
    """Shows the trial subscription management menu."""
    if not is_admin(callback.from_user.id):
        return
    await show_trial_menu(callback)


# ============================================================================
# ON/OFF
# ============================================================================

async def _set_trial_enabled(callback: CallbackQuery, target_enabled: bool):
    """Sets the trial subscription status."""
    if not is_admin(callback.from_user.id):
        return

    from database.requests import is_trial_enabled, set_primary_trial_enabled

    current = is_trial_enabled()
    if current == target_enabled:
        status = "уже включена" if target_enabled else "уже выключена"
        await callback.answer(f"Пробная подписка {status}")
        return

    set_primary_trial_enabled(target_enabled)

    action = "включена" if target_enabled else "выключена"
    logger.info(f"Пробная подписка {action} (admin: {callback.from_user.id})")

    await show_trial_menu(callback)


@router.callback_query(F.data.startswith("admin_trial_set:"))
async def admin_trial_set(callback: CallbackQuery):
    """Enables or disables the trial subscription with the selected state."""
    target_enabled = callback.data.rsplit(":", 1)[1] == "1"
    await _set_trial_enabled(callback, target_enabled)


@router.callback_query(F.data == "admin_trial_toggle")
async def admin_trial_toggle(callback: CallbackQuery):
    """Compatible toggle for old posts."""
    from database.requests import is_trial_enabled
    await _set_trial_enabled(callback, not is_trial_enabled())


# ============================================================================
# TEXT EDITING
# ============================================================================

@router.callback_query(F.data == "admin_trial_edit_text")
async def admin_trial_edit_text_start(callback: CallbackQuery, state: FSMContext):
    """Starts editing the text of the trial subscription through the universal editor."""
    if not is_admin(callback.from_user.id):
        return

    from bot.handlers.admin.message_editor import show_message_editor

    await show_message_editor(
        callback.message, state,
        key='trial',
        back_callback='admin_trial',
        allowed_types=['text', 'photo', 'video', 'animation'],
    )
    await callback.answer()



# ============================================================================
# CHOICE OF TARIFF
# ============================================================================

@router.callback_query(F.data == "admin_trial_select_tariff")
async def admin_trial_select_tariff(callback: CallbackQuery):
    """Shows a list of tariffs for selecting a trial period."""
    if not is_admin(callback.from_user.id):
        return

    from database.requests import get_all_tariffs, get_trial_tariff_id
    from bot.keyboards.admin import trial_tariff_select_kb

    # We receive ALL tariffs including inactive ones
    tariffs = get_all_tariffs(include_hidden=True)
    selected_id = get_trial_tariff_id()

    available = [t for t in tariffs if t.get('system_type') is None]

    if not available:
        await callback.answer("❌ Нет доступных тарифов", show_alert=True)
        return

    await safe_edit_or_send(callback.message,
        "📋 <b>Выбор тарифа для пробной подписки</b>\n\n"
        "Выберите тариф, который будет выдаваться пользователям.\n"
        "Отображаются все тарифы, включая неактивные для покупки.\n\n"
        "🟢 — активный тариф  |  ⚪ — неактивный тариф\n"
        "🔘 — текущий выбор",
        reply_markup=trial_tariff_select_kb(available, selected_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_trial_set_tariff:"))
async def admin_trial_set_tariff(callback: CallbackQuery):
    """Sets the selected rate for the trial subscription."""
    if not is_admin(callback.from_user.id):
        return

    from database.requests import get_tariff_by_id, set_primary_trial_tariff

    tariff_id = int(callback.data.split(":")[1])
    tariff = get_tariff_by_id(tariff_id)

    if not tariff or tariff.get('system_type') is not None:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return

    set_primary_trial_tariff(tariff_id)
    logger.info(
        f"Тариф пробной подписки изменён на ID={tariff_id} "
        f"({tariff['name']}) (admin: {callback.from_user.id})"
    )

    await callback.answer(f"✅ Тариф «{tariff['name']}» выбран", show_alert=False)
    await show_trial_menu(callback)
