"""
Обработчики раздела «Пробная подписка» в админ-панели.

Управление функцией пробного периода:
- Включение/выключение
- Редактирование текста страницы
- Выбор тарифа (включая неактивные, кроме Admin Tariff)
"""
import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter

from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.utils.text import escape_md

logger = logging.getLogger(__name__)

router = Router()


# ============================================================================
# ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ: ОТОБРАЖЕНИЕ МЕНЮ
# ============================================================================

async def show_trial_menu(callback: CallbackQuery):
    """Показывает меню настроек пробной подписки."""
    from database.requests import (
        get_setting, is_trial_enabled, get_trial_tariff_id, get_tariff_by_id
    )
    from bot.keyboards.admin import trial_settings_kb

    enabled = is_trial_enabled()
    tariff_id = get_trial_tariff_id()
    tariff_name = None

    if tariff_id:
        tariff = get_tariff_by_id(tariff_id)
        if tariff:
            status = "🟢" if tariff['is_active'] else "🔴"
            tariff_name = f"{status} {tariff['name']} ({tariff['duration_days']} дн.)"

    status_text = "✅ Включена" if enabled else "❌ Выключена"
    tariff_text = tariff_name if tariff_name else "_не задан_"

    text = (
        "🎁 *Пробная подписка*\n\n"
        "Управление функцией пробного доступа для новых пользователей\\.\n\n"
        f"📌 *Статус:* {escape_md(status_text)}\n"
        f"📋 *Тариф:* {tariff_text}\n\n"
        "❓ *Как работает:*\n"
        "• Если включено и тариф задан — кнопка «🎁 Пробная подписка» появляется на главной у пользователей, которые ещё не использовали пробный период\\.\n"
        "• При активации — пользователю выдаётся ключ с выбранным тарифом\\.\n"
        "• Каждый пользователь может активировать пробный период только один раз\\."
    )

    await callback.message.edit_text(
        text,
        reply_markup=trial_settings_kb(enabled, tariff_name),
        parse_mode="MarkdownV2"
    )
    await callback.answer()


# ============================================================================
# ГЛАВНЫЙ ЭКРАН ПРОБНОЙ ПОДПИСКИ
# ============================================================================

@router.callback_query(F.data == "admin_trial")
async def admin_trial_menu(callback: CallbackQuery):
    """Показывает меню управления пробной подпиской."""
    if not is_admin(callback.from_user.id):
        return
    await show_trial_menu(callback)


# ============================================================================
# ВКЛЮЧЕНИЕ / ВЫКЛЮЧЕНИЕ
# ============================================================================

@router.callback_query(F.data == "admin_trial_toggle")
async def admin_trial_toggle(callback: CallbackQuery):
    """Переключает статус пробной подписки."""
    if not is_admin(callback.from_user.id):
        return

    from database.requests import get_setting, set_setting, is_trial_enabled

    current = is_trial_enabled()
    new_value = '0' if current else '1'
    set_setting('trial_enabled', new_value)

    action = "включена" if new_value == '1' else "выключена"
    logger.info(f"Пробная подписка {action} (admin: {callback.from_user.id})")

    await show_trial_menu(callback)


# ============================================================================
# РЕДАКТИРОВАНИЕ ТЕКСТА
# ============================================================================

@router.callback_query(F.data == "admin_trial_edit_text")
async def admin_trial_edit_text_start(callback: CallbackQuery, state: FSMContext):
    """Начинает редактирование текста страницы пробной подписки."""
    if not is_admin(callback.from_user.id):
        return

    from bot.states.admin_states import AdminStates
    from bot.keyboards.admin import trial_edit_text_cancel_kb

    await state.set_state(AdminStates.waiting_for_trial_text)

    await callback.message.edit_text(
        "✏️ *Редактирование текста пробной подписки*\n\n"
        "Отправьте новый текст для страницы\\.\n\n"
        "⚠️ Используйте *MarkdownV2* для форматирования\\.\n"
        "Специальные символы `\\. \\! \\( \\) \\- \\_` нужно экранировать обратным слешем\\.",
        reply_markup=trial_edit_text_cancel_kb(),
        parse_mode="MarkdownV2"
    )
    await callback.answer()


@router.message(StateFilter(AdminStates.waiting_for_trial_text), F.text)
async def admin_trial_edit_text_save(message: Message, state: FSMContext):
    """Сохраняет новый текст страницы пробной подписки."""
    if not is_admin(message.from_user.id):
        return

    from database.requests import set_setting
    from bot.keyboards.admin import trial_settings_kb, back_and_home_kb
    from database.requests import is_trial_enabled, get_trial_tariff_id, get_tariff_by_id

    new_text = message.text
    set_setting('trial_page_text', new_text)

    await state.clear()

    enabled = is_trial_enabled()
    tariff_id = get_trial_tariff_id()
    tariff_name = None
    if tariff_id:
        tariff = get_tariff_by_id(tariff_id)
        if tariff:
            status = "🟢" if tariff['is_active'] else "🔴"
            tariff_name = f"{status} {tariff['name']} ({tariff['duration_days']} дн.)"

    await message.answer(
        "✅ *Текст страницы пробной подписки обновлён\\!*",
        parse_mode="MarkdownV2",
        reply_markup=trial_settings_kb(enabled, tariff_name)
    )

    logger.info(f"Текст пробной подписки обновлён (admin: {message.from_user.id})")


# ============================================================================
# ВЫБОР ТАРИФА
# ============================================================================

@router.callback_query(F.data == "admin_trial_select_tariff")
async def admin_trial_select_tariff(callback: CallbackQuery):
    """Показывает список тарифов для выбора пробного периода."""
    if not is_admin(callback.from_user.id):
        return

    from database.requests import get_all_tariffs, get_trial_tariff_id
    from bot.keyboards.admin import trial_tariff_select_kb

    # Получаем ВСЕ тарифы включая неактивные
    tariffs = get_all_tariffs(include_hidden=True)
    selected_id = get_trial_tariff_id()

    # Фильтруем Admin Tariff
    available = [t for t in tariffs if t.get('name') != 'Admin Tariff']

    if not available:
        await callback.answer("❌ Нет доступных тарифов", show_alert=True)
        return

    await callback.message.edit_text(
        "📋 *Выбор тарифа для пробной подписки*\n\n"
        "Выберите тариф, который будет выдаваться пользователям\\.\n"
        "Отображаются все тарифы, включая неактивные для покупки\\.\n\n"
        "🟢 — активный тариф  \\|  🔴 — неактивный тариф\n"
        "🔘 — текущий выбор",
        reply_markup=trial_tariff_select_kb(available, selected_id),
        parse_mode="MarkdownV2"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_trial_set_tariff:"))
async def admin_trial_set_tariff(callback: CallbackQuery):
    """Устанавливает выбранный тариф для пробной подписки."""
    if not is_admin(callback.from_user.id):
        return

    from database.requests import set_setting, get_tariff_by_id

    tariff_id = int(callback.data.split(":")[1])
    tariff = get_tariff_by_id(tariff_id)

    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return

    set_setting('trial_tariff_id', str(tariff_id))
    logger.info(
        f"Тариф пробной подписки изменён на ID={tariff_id} "
        f"({tariff['name']}) (admin: {callback.from_user.id})"
    )

    await callback.answer(f"✅ Тариф «{tariff['name']}» выбран", show_alert=False)
    await show_trial_menu(callback)
