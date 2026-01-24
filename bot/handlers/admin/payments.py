"""
Роутер раздела «Оплаты».

Обрабатывает:
- Главный экран оплат
- Toggle для Stars/Crypto
- Настройка крипто-платежей
- Редактирование крипто-настроек
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from config import ADMIN_IDS
from database.requests import (
    get_setting,
    set_setting,
    is_crypto_enabled,
    is_stars_enabled
)
from bot.states.admin_states import (
    AdminStates,
    CRYPTO_PARAMS,
    get_crypto_param_by_index,
    get_total_crypto_params
)
from bot.utils.admin import is_admin
from bot.keyboards.admin import (
    payments_menu_kb,
    crypto_setup_kb,
    crypto_setup_confirm_kb,
    edit_crypto_kb,
    crypto_management_kb,
    back_and_home_kb
)

logger = logging.getLogger(__name__)

router = Router()


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================


def has_crypto_data() -> bool:
    """Проверяет, заполнены ли данные крипто-платежей в БД."""
    url = get_setting('crypto_item_url', '')
    secret = get_setting('crypto_secret_key', '')
    return bool(url and secret)


def parse_item_id_from_url(url: str) -> str:
    """
    Извлекает item_id из ссылки на товар Ya.Seller.
    
    Формат: https://t.me/Ya_SellerBot?start=item-{item_id}...
    """
    try:
        if '?start=item-' in url:
            start_part = url.split('?start=item-')[1]
            # item_id — это первая часть до следующего дефиса или конца строки
            item_id = start_part.split('-')[0]
            return item_id
        elif '?start=item0-' in url:
            # Тестовый режим
            start_part = url.split('?start=item0-')[1]
            item_id = start_part.split('-')[0]
            return item_id
    except Exception:
        pass
    return ""


# ============================================================================
# ГЛАВНЫЙ ЭКРАН ОПЛАТ
# ============================================================================

@router.callback_query(F.data == "admin_payments")
async def show_payments_menu(callback: CallbackQuery, state: FSMContext):
    """Показывает главный экран раздела оплат."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.payments_menu)
    
    stars = is_stars_enabled()
    crypto = is_crypto_enabled()
    
    text = (
        "💳 *Настройки оплаты*\n\n"
        "Здесь можно включить/выключить способы оплаты и настроить их.\n\n"
    )
    
    if stars:
        text += "🟢 *Telegram Stars*\n"
    else:
        text += "⚪ *Telegram Stars*\n"
    
    if crypto:
        item_url = get_setting('crypto_item_url', '')
        if item_url:
            text += f"🟢 *Крипто (Ya.Seller)*\n[Ссылка на товар]({item_url})\n"
        else:
            text += "🟢 *Крипто (Ya.Seller)*\n"
    else:
        text += "⚪ *Крипто (Ya.Seller)*\n"
    
    await callback.message.edit_text(
        text,
        reply_markup=payments_menu_kb(stars, crypto),
        parse_mode="Markdown",
        disable_web_page_preview=True
    )
    await callback.answer()


# ============================================================================
# TOGGLE STARS
# ============================================================================

@router.callback_query(F.data == "admin_payments_toggle_stars")
async def toggle_stars(callback: CallbackQuery, state: FSMContext):
    """Переключает Telegram Stars."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    current = is_stars_enabled()
    new_value = '0' if current else '1'
    set_setting('stars_enabled', new_value)
    
    status = "включены ⭐" if new_value == '1' else "выключены"
    await callback.answer(f"Telegram Stars {status}")
    
    # Обновляем экран
    await show_payments_menu(callback, state)


# ============================================================================
# TOGGLE CRYPTO
# ============================================================================

@router.callback_query(F.data == "admin_payments_toggle_crypto")
async def toggle_crypto(callback: CallbackQuery, state: FSMContext):
    """Открывает настройку или меню управления крипто-платежами."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    # Проверяем, есть ли данные в БД
    if has_crypto_data():
        # Если данные есть → меню управления
        await show_crypto_management_menu(callback, state)
    else:
        # Если данных нет → диалог настройки
        await start_crypto_setup(callback, state)


# ============================================================================
# НАСТРОЙКА КРИПТО-ПЛАТЕЖЕЙ
# ============================================================================

async def start_crypto_setup(callback: CallbackQuery, state: FSMContext):
    """Начинает диалог настройки крипто-платежей."""
    await state.set_state(AdminStates.crypto_setup_url)
    await state.update_data(crypto_data={}, crypto_step=1)
    
    # Получаем username бота для инструкции
    bot_username = callback.bot.my_username if hasattr(callback.bot, 'my_username') else "YOUR_BOT"
    callback_url = f"https://t.me/{bot_username}"
    
    text = (
        "💰 *Настройка крипто-платежей*\n\n"
        "Для приёма криптовалюты мы используем @Ya\\_SellerBot.\n\n"
        "*Инструкция:*\n"
        "1️⃣ Создайте товар в @Ya\\_SellerBot\n"
        "2️⃣ Добавьте тарифы (помните номера 1-9!)\n"
        "3️⃣ Перейдите: Позиция → Уведомления → Обратная ссылка\n"
        "4️⃣ Вставьте туда эту ссылку:\n\n"
        f"`{callback_url}`\n\n"
        "💡 _Ya.Seller автоматически добавит параметры платежа:_\n"
        f"`{callback_url}?start=bill1-данные-подпись`\n\n"
        "5️⃣ Скопируйте ссылку на товар из бота\n\n"
        "📚 [Подробная документация](https://yadreno.ru/seller/integration.php)\n\n"
        "---\n\n"
        "Теперь *вставьте ссылку на товар* из @Ya\\_SellerBot:"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=crypto_setup_kb(1),
        parse_mode="Markdown",
        disable_web_page_preview=True
    )


@router.message(AdminStates.crypto_setup_url)
async def process_crypto_url(message: Message, state: FSMContext):
    """Обрабатывает ввод ссылки на товар."""
    url = message.text.strip()
    
    # Валидация
    param = get_crypto_param_by_index(0)
    if not param['validate'](url):
        await message.answer(
            f"❌ {param['error']}\n\nПопробуйте ещё раз:",
            parse_mode="Markdown"
        )
        return
    
    # Удаляем сообщение
    try:
        await message.delete()
    except:
        pass
    
    # Проверяем режим
    data = await state.get_data()
    edit_mode = data.get('edit_mode', False)
    
    if edit_mode:
        # Режим редактирования - сохраняем и возвращаемся в меню
        set_setting('crypto_item_url', url)
        await state.update_data(edit_mode=False)
        
        await message.answer(
            f"✅ Ссылка обновлена!\n[Ссылка на товар]({url})",
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
        
        # Создаём фейковый callback для показа меню
        class FakeCallback:
            def __init__(self, msg, user):
                self.message = msg
                self.from_user = user
                self.bot = msg.bot
            async def answer(self, *args, **kwargs):
                pass
        
        fake = FakeCallback(message, message.from_user)
        await show_crypto_management_menu(fake, state)
    else:
        # Режим настройки - сохраняем во временные данные
        crypto_data = data.get('crypto_data', {})
        crypto_data['crypto_item_url'] = url
        await state.update_data(crypto_data=crypto_data, crypto_step=2)
        
        # Переходим к вводу секретного ключа
        await state.set_state(AdminStates.crypto_setup_secret)
        
        await message.answer(
            f"✅ Ссылка принята!\n[Ссылка на товар]({url})\n\n"
            "Теперь введите *Секретный ключ*:\n"
            "Найти его можно в @Ya\\_SellerBot: Профиль → Ключ подписи",
            reply_markup=crypto_setup_kb(2),
            disable_web_page_preview=True,
            parse_mode="Markdown"
        )


@router.message(AdminStates.crypto_setup_secret)
async def process_crypto_secret(message: Message, state: FSMContext):
    """Обрабатывает ввод секретного ключа."""
    secret = message.text.strip()
    
    # Валидация
    param = get_crypto_param_by_index(1)
    if not param['validate'](secret):
        await message.answer(
            f"❌ {param['error']}\n\nПопробуйте ещё раз:",
            parse_mode="Markdown"
        )
        return
    
    # Удаляем сообщение (там секретный ключ!)
    try:
        await message.delete()
    except:
        pass
    
    # Проверяем режим
    data = await state.get_data()
    edit_mode = data.get('edit_mode', False)
    
    if edit_mode:
        # Режим редактирования - сохраняем и возвращаемся в меню
        set_setting('crypto_secret_key', secret)
        await state.update_data(edit_mode=False)
        await message.answer("✅ Секретный ключ обновлён!")
        
        # Создаём фейковый callback для показа меню
        class FakeCallback:
            def __init__(self, msg, user):
                self.message = msg
                self.from_user = user
                self.bot = msg.bot
            async def answer(self, *args, **kwargs):
                pass
        
        fake = FakeCallback(message, message.from_user)
        await show_crypto_management_menu(fake, state)
    else:
        # Режим настройки - сохраняем во временные данные
        crypto_data = data.get('crypto_data', {})
        crypto_data['crypto_secret_key'] = secret
        await state.update_data(crypto_data=crypto_data)
        
        # Переходим к подтверждению
        await state.set_state(AdminStates.payments_menu)
        
        item_url = crypto_data.get('crypto_item_url', '')
        
        await message.answer(
            "✅ *Все данные введены!*\n\n"
            f"📦 Товар: [Ссылка на товар]({item_url})\n"
            f"🔐 Ключ: `{'•' * 16}`\n\n"
            "Сохранить и включить крипто-платежи?",
            reply_markup=crypto_setup_confirm_kb(),
            parse_mode="Markdown",
            disable_web_page_preview=True
        )


@router.callback_query(F.data == "admin_crypto_setup_back")
async def crypto_setup_back(callback: CallbackQuery, state: FSMContext):
    """Возврат на предыдущий шаг настройки крипто."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    step = data.get('crypto_step', 1)
    
    if step <= 1:
        # Возврат к меню оплат
        await show_payments_menu(callback, state)
    else:
        # Возврат к вводу URL
        await state.set_state(AdminStates.crypto_setup_url)
        await state.update_data(crypto_step=1)
        await start_crypto_setup(callback, state)
    
    await callback.answer()


@router.callback_query(F.data == "admin_crypto_setup_save")
async def crypto_setup_save(callback: CallbackQuery, state: FSMContext):
    """Сохраняет настройки крипто и включает их."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    crypto_data = data.get('crypto_data', {})
    
    if not crypto_data.get('crypto_item_url') or not crypto_data.get('crypto_secret_key'):
        await callback.answer("❌ Данные не полные", show_alert=True)
        return
    
    # Сохраняем
    set_setting('crypto_item_url', crypto_data['crypto_item_url'])
    set_setting('crypto_secret_key', crypto_data['crypto_secret_key'])
    set_setting('crypto_enabled', '1')
    
    await callback.answer("✅ Крипто-платежи включены!")
    
    await callback.message.edit_text(
        "✅ *Крипто-платежи настроены и включены!*\n\n"
        "Теперь пользователи смогут оплачивать криптовалютой.\n"
        "Не забудьте добавить тарифы с указанием ID тарифа (1-9)!",
        parse_mode="Markdown"
    )
    
    # Показываем меню оплат
    await show_payments_menu(callback, state)


# ============================================================================
# МЕНЮ УПРАВЛЕНИЯ КРИПТО-ПЛАТЕЖАМИ
# ============================================================================

async def show_crypto_management_menu(callback: CallbackQuery, state: FSMContext):
    """Показывает меню управления крипто-платежами."""
    await state.set_state(AdminStates.payments_menu)
    
    is_enabled = is_crypto_enabled()
    item_url = get_setting('crypto_item_url', '')
    
    status_emoji = "🟢" if is_enabled else "⚪"
    status_text = "включены" if is_enabled else "выключены"
    
    if item_url:
        text = (
            "💰 *Управление крипто-платежами*\n\n"
            f"{status_emoji} Статус: *{status_text}*\n"
            f"📦 Товар: [Ссылка на товар]({item_url})\n\n"
            "Выберите действие:"
        )
    else:
        text = (
            "💰 *Управление крипто-платежами*\n\n"
            f"{status_emoji} Статус: *{status_text}*\n"
            "📦 Товар: —\n\n"
            "Выберите действие:"
        )
    
    await callback.message.edit_text(
        text,
        reply_markup=crypto_management_kb(is_enabled),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_crypto_mgmt_toggle")
async def crypto_mgmt_toggle(callback: CallbackQuery, state: FSMContext):
    """Включает/выключает крипто-платежи (без потери данных)."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    current = is_crypto_enabled()
    new_value = '0' if current else '1'
    set_setting('crypto_enabled', new_value)
    
    status = "включены ✅" if new_value == '1' else "выключены"
    await callback.answer(f"Крипто-платежи {status}")
    
    # Обновляем меню
    await show_crypto_management_menu(callback, state)


@router.callback_query(F.data == "admin_crypto_mgmt_edit_url")
async def crypto_mgmt_edit_url(callback: CallbackQuery, state: FSMContext):
    """Начинает редактирование ссылки на товар."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.crypto_setup_url)
    await state.update_data(edit_mode=True)
    
    current_url = get_setting('crypto_item_url', '')
    
    if current_url:
        text = (
            "🔗 *Изменение ссылки на товар*\n\n"
            f"Текущая ссылка: [Ссылка на товар]({current_url})\n\n"
            "Введите новую ссылку на товар из @Ya\\_SellerBot:"
        )
    else:
        text = (
            "🔗 *Изменение ссылки на товар*\n\n"
            "Текущая ссылка: —\n\n"
            "Введите новую ссылку на товар из @Ya\\_SellerBot:"
        )
    
    await callback.message.edit_text(
        text,
        reply_markup=back_and_home_kb("admin_crypto_management"),
        parse_mode="Markdown",
        disable_web_page_preview=True
    )
    await callback.answer()


@router.callback_query(F.data == "admin_crypto_mgmt_edit_secret")
async def crypto_mgmt_edit_secret(callback: CallbackQuery, state: FSMContext):
    """Начинает редактирование секретного ключа."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.crypto_setup_secret)
    await state.update_data(edit_mode=True)
    
    text = (
        "🔐 *Изменение секретного ключа*\n\n"
        "Введите новый секретный ключ:\n"
        "Найти его можно в @Ya\\_SellerBot: Профиль → Ключ подписи"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=back_and_home_kb("admin_crypto_management"),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_crypto_management")
async def back_to_crypto_management(callback: CallbackQuery, state: FSMContext):
    """Возврат в меню управления крипто-платежами."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await show_crypto_management_menu(callback, state)


# ============================================================================
# РЕДАКТИРОВАНИЕ КРИПТО-НАСТРОЕК
# ============================================================================

@router.callback_query(F.data == "admin_payments_crypto_settings")
async def start_edit_crypto(callback: CallbackQuery, state: FSMContext):
    """Начинает редактирование крипто-настроек."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.edit_crypto)
    await state.update_data(edit_crypto_param=0)
    
    await show_crypto_edit_screen(callback, state, 0)


async def show_crypto_edit_screen(callback: CallbackQuery, state: FSMContext, param_index: int):
    """Показывает экран редактирования крипто-настройки."""
    param = get_crypto_param_by_index(param_index)
    total = get_total_crypto_params()
    
    current_value = get_setting(param['key'], '')
    
    # Маскируем секретный ключ
    if param['key'] == 'crypto_secret_key' and current_value:
        display_value = '•' * min(len(current_value), 16)
    else:
        display_value = current_value or '—'
    
    text = (
        f"⚙️ *Настройки крипто-платежей* ({param_index + 1}/{total})\n\n"
        f"📌 Параметр: *{param['label']}*\n"
        f"📝 Текущее значение: `{display_value}`\n\n"
        f"Введите новое значение или используйте кнопки навигации:\n"
        f"({param['hint']})"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=edit_crypto_kb(param_index, total),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin_crypto_edit_prev")
async def crypto_edit_prev(callback: CallbackQuery, state: FSMContext):
    """Предыдущий параметр крипто-настроек."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    current = data.get('edit_crypto_param', 0)
    new_param = max(0, current - 1)
    await state.update_data(edit_crypto_param=new_param)
    
    await show_crypto_edit_screen(callback, state, new_param)
    await callback.answer()


@router.callback_query(F.data == "admin_crypto_edit_next")
async def crypto_edit_next(callback: CallbackQuery, state: FSMContext):
    """Следующий параметр крипто-настроек."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    current = data.get('edit_crypto_param', 0)
    total = get_total_crypto_params()
    new_param = min(total - 1, current + 1)
    await state.update_data(edit_crypto_param=new_param)
    
    await show_crypto_edit_screen(callback, state, new_param)
    await callback.answer()


@router.message(AdminStates.edit_crypto)
async def edit_crypto_value(message: Message, state: FSMContext):
    """Обрабатывает ввод нового значения крипто-настройки."""
    data = await state.get_data()
    param_index = data.get('edit_crypto_param', 0)
    
    param = get_crypto_param_by_index(param_index)
    value = message.text.strip()
    
    # Валидация
    if not param['validate'](value):
        await message.answer(
            f"❌ {param['error']}",
            parse_mode="Markdown"
        )
        return
    
    # Сохраняем в БД
    set_setting(param['key'], value)
    
    # Удаляем сообщение
    try:
        await message.delete()
    except:
        pass
    
    # Показываем обновлённый экран
    await message.answer(
        f"✅ *{param['label']}* обновлено!",
        parse_mode="Markdown"
    )
    
    # Создаём фейковый callback для показа экрана
    # Это хак, но работает
    class FakeCallback:
        def __init__(self, msg, user):
            self.message = msg
            self.from_user = user
            self.bot = msg.bot
        
        async def answer(self, *args, **kwargs):
            pass
    
    fake = FakeCallback(message, message.from_user)
    await show_crypto_edit_screen(fake, state, param_index)


@router.callback_query(F.data == "admin_crypto_edit_done")
async def crypto_edit_done(callback: CallbackQuery, state: FSMContext):
    """Завершение редактирования крипто-настроек."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await callback.answer("✅ Настройки сохранены")
    await show_payments_menu(callback, state)
