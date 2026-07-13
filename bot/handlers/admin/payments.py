"""
Router of the “Payments” section.

Processes:
- Main payment screen
- Toggle for Stars/Crypto
- Setting up crypto payments
- Editing crypto settings
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext

from config import ADMIN_IDS
from database.requests import (
    get_setting,
    set_setting,
    is_crypto_enabled,
    is_stars_enabled,
    is_cards_enabled,
    is_yookassa_qr_enabled,
    is_demo_payment_enabled,
    is_wata_enabled,
    is_platega_enabled,
    is_cardlink_enabled,
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
    cards_management_kb,
    qr_management_kb,
    wata_management_kb,
    platega_management_kb,
    cardlink_management_kb,
    back_and_home_kb
)
from bot.utils.text import escape_html, safe_edit_or_send

logger = logging.getLogger(__name__)


router = Router()


# ============================================================================
# AUXILIARY FUNCTIONS
# ============================================================================


def has_crypto_data() -> bool:
    """Checks whether crypto payment data is filled in the database."""
    url = get_setting('crypto_item_url', '')
    secret = get_setting('crypto_secret_key', '')
    return bool(url and secret)


def parse_item_id_from_url(url: str) -> str:
    """
    Retrieves the item_id from the Ya.Seller product link.
    
    Format: https://t.me/Ya_SellerBot?start=item-{item_id}...
    """
    try:
        if '?start=item-' in url:
            start_part = url.split('?start=item-')[1]
            # item_id is the first part up to the next hyphen or end of line
            item_id = start_part.split('-')[0]
            return item_id
        elif '?start=item0-' in url:
            # Test mode
            start_part = url.split('?start=item0-')[1]
            item_id = start_part.split('-')[0]
            return item_id
    except Exception:
        pass
    return ""


# ============================================================================
# MAIN PAYMENT SCREEN
# ============================================================================

@router.callback_query(F.data == "admin_payments")
async def show_payments_menu(callback: CallbackQuery, state: FSMContext):
    """Shows the main screen of the payment section."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.payments_menu)

    stars = is_stars_enabled()
    crypto = is_crypto_enabled()
    cards = is_cards_enabled()
    qr = is_yookassa_qr_enabled()
    demo = is_demo_payment_enabled()
    wata = is_wata_enabled()
    platega = is_platega_enabled()
    cardlink = is_cardlink_enabled()

    text = (
        "💳 <b>Настройки оплаты</b>\n\n"
        "Здесь можно включить/выключить способы оплаты и настроить их.\n\n"
    )

    if stars:
        text += "🟢 <b>Telegram Stars</b>\n"
    else:
        text += "⚪ <b>Telegram Stars</b>\n"

    if crypto:
        item_url = get_setting('crypto_item_url', '')
        if item_url:
            text += f"🟢 <b>Крипто (@Ya_SellerBot)</b>\n<a href=\"{item_url}\">Ссылка на товар</a>\n"
        else:
            text += "🟢 <b>Крипто (@Ya_SellerBot)</b>\n"
    else:
        text += "⚪ <b>Крипто (@Ya_SellerBot)</b>\n"

    if cards:
        text += "🟢 <b>TG payments</b>\n"
    else:
        text += "⚪ <b>TG payments</b>\n"

    if qr:
        shop_id = get_setting('yookassa_shop_id', '')
        text += f"🟢 <b>ЮКасса</b> | Shop ID: <code>{shop_id or '—'}</code>\n"
    else:
        text += "⚪ <b>ЮКасса</b>\n"

    if wata:
        text += "🟢 <b>WATA</b>\n"
    else:
        text += "⚪ <b>WATA</b>\n"

    if platega:
        text += "🟢 <b>Platega</b>\n"
    else:
        text += "⚪ <b>Platega</b>\n"

    if cardlink:
        text += "🟢 <b>Cardlink</b>\n"
    else:
        text += "⚪ <b>Cardlink</b>\n"

    if demo:
        text += "🟢 <b>Демо оплата (РФ)</b>\n"
    else:
        text += "⚪ <b>Демо оплата (РФ)</b>\n"

    monthly_reset = get_setting('monthly_traffic_reset_enabled', '0') == '1'
    notify = get_setting('payment_notifications_enabled', '0') == '1'

    await safe_edit_or_send(callback.message,
        text,
        reply_markup=payments_menu_kb(stars, crypto, cards, qr, monthly_reset, demo, wata, platega, cardlink, notify_enabled=notify)
    )
    await callback.answer()


# ============================================================================
# TOGGLE MONTHLY RESET
# ============================================================================

@router.callback_query(F.data == "admin_toggle_monthly_reset")
async def toggle_monthly_reset(callback: CallbackQuery, state: FSMContext):
    """Switching traffic auto-reset on the 1st."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    current = get_setting('monthly_traffic_reset_enabled', '0')
    new_val = '0' if current == '1' else '1'
    set_setting('monthly_traffic_reset_enabled', new_val)
    
    # Redrawing the payment menu
    await show_payments_menu(callback, state)


@router.callback_query(F.data == "admin_toggle_payment_notify")
async def toggle_payment_notify(callback: CallbackQuery, state: FSMContext):
    """Switch payment notifications."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    current = get_setting('payment_notifications_enabled', '0')
    new_val = '0' if current == '1' else '1'
    set_setting('payment_notifications_enabled', new_val)
    
    status = "включены 🔔" if new_val == '1' else "выключены"
    await callback.answer(f"Уведомления об оплатах {status}")
    
    # Redrawing the payment menu
    await show_payments_menu(callback, state)


# ============================================================================
# TOGGLE STARS
# ============================================================================

@router.callback_query(F.data == "admin_payments_toggle_stars")
async def toggle_stars(callback: CallbackQuery, state: FSMContext):
    """Switches Telegram Stars."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    current = is_stars_enabled()
    new_value = '0' if current else '1'
    set_setting('stars_enabled', new_value)
    
    status = "включены ⭐" if new_value == '1' else "выключены"
    await callback.answer(f"Telegram Stars {status}")
    
    # Refresh the screen
    await show_payments_menu(callback, state)


# ============================================================================
# TOGGLE DEMO PAYMENT
# ============================================================================

@router.callback_query(F.data == "admin_payments_toggle_demo")
async def toggle_demo(callback: CallbackQuery, state: FSMContext):
    """Switches demo payment by RF card."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    current = is_demo_payment_enabled()
    new_value = '0' if current else '1'
    set_setting('demo_payment_enabled', new_value)
    
    status = "включена" if new_value == '1' else "выключена"
    await callback.answer(f"Демо оплата {status}")
    
    # Refresh the screen
    await show_payments_menu(callback, state)


# ============================================================================
# TOGGLE CRYPTO
# ============================================================================

@router.callback_query(F.data == "admin_payments_toggle_crypto")
async def toggle_crypto(callback: CallbackQuery, state: FSMContext):
    """Opens the settings or menu for managing crypto payments."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    # Checking if there is data in the database
    if has_crypto_data():
        # If there is data → control menu
        await show_crypto_management_menu(callback, state)
    else:
        # If there is no data → settings dialog
        await start_crypto_setup(callback, state)


# ============================================================================
# SETTING UP CRYPTO PAYMENTS
# ============================================================================

async def start_crypto_setup(callback: CallbackQuery, state: FSMContext):
    """Starts the crypto payment settings dialog."""
    await state.set_state(AdminStates.crypto_setup_url)
    await state.update_data(crypto_data={}, crypto_step=1)
    
    # Getting the username of the bot for instructions
    bot_username = callback.bot.my_username if hasattr(callback.bot, 'my_username') else "YOUR_BOT"
    callback_url = f"https://t.me/{bot_username}"
    
    instructions = (
        "1️⃣ В @Ya_SellerBot выберите «Управление» → «Товары» → «Добавить»\n"
        "2️⃣ Выберите тип позиции: <b>Счет</b>\n\n"
        "🎬 <b>Актуальная инструкция как добавлять:</b>\n"
        "<a href=\"https://youtu.be/cK0wX2LKxcs\">Смотреть видео</a>\n\n"
        "⚠️ <b>ВАЖНО:</b>\n"
        "• Тип позиции — именно <b>Счет</b>, а НЕ <b>Товар</b>!\n"
        "• Тарифы добавлять к позиции <b>НЕ нужно</b> — бот сам сформирует сумму оплаты.\n\n"
    )

    text = (
        "💰 <b>Настройка крипто-платежей</b>\n\n"
        "Для приёма криптовалюты мы используем @Ya_SellerBot.\n\n"
        f"{instructions}"
        "🔗 *Теперь скопируйте ссылку на позицию из бота и отправьте её мне:*"
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=crypto_setup_kb(1)
    )


@router.message(AdminStates.crypto_setup_url)
async def process_crypto_url(message: Message, state: FSMContext):
    """Processes the input of a link to a product."""
    from bot.utils.text import get_message_text_for_storage
    
    url = get_message_text_for_storage(message, 'plain')
    
    # Validation
    param = get_crypto_param_by_index(0)
    if not param['validate'](url):
        await safe_edit_or_send(message,
            f"❌ {param['error']}\n\nПопробуйте ещё раз:"
        )
        return
    
    # Delete the message
    try:
        await message.delete()
    except:
        pass
    
    # Checking the mode
    data = await state.get_data()
    edit_mode = data.get('edit_mode', False)
    
    if edit_mode:
        # Editing mode - save and return to the menu
        set_setting('crypto_item_url', url)
        await state.update_data(edit_mode=False)
        
        await safe_edit_or_send(message,
            f"✅ Ссылка обновлена!\n<a href=\"{url}\">{escape_html(url)}</a>",
            force_new=True
        )
        
        # Create a fake callback to display the menu
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
        # Setting mode - saving to temporary data
        crypto_data = data.get('crypto_data', {})
        crypto_data['crypto_item_url'] = url
        await state.update_data(crypto_data=crypto_data, crypto_step=2)
        
        # Let's proceed to entering the secret key
        await state.set_state(AdminStates.crypto_setup_secret)
        
        bot_username = message.bot.my_username if hasattr(message.bot, 'my_username') else "YOUR_BOT"
        callback_url = f"https://t.me/{bot_username}"

        await safe_edit_or_send(message,
            f"✅ Ссылка принята!\n<a href=\"{url}\">{escape_html(url)}</a>\n\n"
            "🔔 <b>Настройка уведомлений:</b>\n"
            "В @Ya_SellerBot зайдите в настройки вашей созданной позиции → <code>Уведомления</code> → <code>Обратная ссылка</code> и укажите этот адрес:\n"
            f"<code>{callback_url}</code>\n\n"
            "🔑 <b>Ожидаю ввода секретного ключа:</b>\n"
            "Найти его можно в @Ya_SellerBot: <code>Профиль</code> → <code>Ключ подписи</code>.",
            reply_markup=crypto_setup_kb(2),
            force_new=True
        )


@router.message(AdminStates.crypto_setup_secret)
async def process_crypto_secret(message: Message, state: FSMContext):
    """Processes the entry of a secret key."""
    from bot.utils.text import get_message_text_for_storage
    
    secret = get_message_text_for_storage(message, 'plain')
    
    # Validation
    param = get_crypto_param_by_index(1)
    if not param['validate'](secret):
        await safe_edit_or_send(message,
            f"❌ {param['error']}\n\nПопробуйте ещё раз:"
        )
        return
    
    # We delete the message (there is a secret key!)
    try:
        await message.delete()
    except:
        pass
    
    # Checking the mode
    data = await state.get_data()
    edit_mode = data.get('edit_mode', False)
    
    if edit_mode:
        # Editing mode - save and return to the menu
        set_setting('crypto_secret_key', secret)
        await state.update_data(edit_mode=False)
        await safe_edit_or_send(message, "✅ Секретный ключ обновлён!", force_new=True)
        
        # Create a fake callback to display the menu
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
        # Setting mode - saving to temporary data
        crypto_data = data.get('crypto_data', {})
        crypto_data['crypto_secret_key'] = secret
        await state.update_data(crypto_data=crypto_data)
        
        # Let's move on to confirmation
        await state.set_state(AdminStates.payments_menu)
        
        item_url = crypto_data.get('crypto_item_url', '')
        
        await safe_edit_or_send(message,
            "✅ <b>Все данные введены!</b>\n\n"
            f"📦 Товар: <a href=\"{item_url}\">{escape_html(item_url)}</a>\n"
            f"🔐 Ключ: <code>{'•' * 16}</code>\n\n"
            "Сохранить и включить крипто-платежи?",
            reply_markup=crypto_setup_confirm_kb(),
            force_new=True
        )


@router.callback_query(F.data == "admin_crypto_setup_back")
async def crypto_setup_back(callback: CallbackQuery, state: FSMContext):
    """Return to the previous crypto setup step."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    step = data.get('crypto_step', 1)
    
    if step <= 1:
        # Return to payment menu
        await show_payments_menu(callback, state)
    else:
        # Return to URL entry
        await state.set_state(AdminStates.crypto_setup_url)
        await state.update_data(crypto_step=1)
        await start_crypto_setup(callback, state)
    
    await callback.answer()


@router.callback_query(F.data == "admin_crypto_setup_save")
async def crypto_setup_save(callback: CallbackQuery, state: FSMContext):
    """Saves crypto settings and enables them."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    data = await state.get_data()
    crypto_data = data.get('crypto_data', {})
    
    if not crypto_data.get('crypto_item_url') or not crypto_data.get('crypto_secret_key'):
        await callback.answer("❌ Данные не полные", show_alert=True)
        return
    
    # Save
    set_setting('crypto_item_url', crypto_data['crypto_item_url'])
    set_setting('crypto_secret_key', crypto_data['crypto_secret_key'])
    set_setting('crypto_enabled', '1')
    
    await callback.answer("✅ Крипто-платежи включены!")
    
    await safe_edit_or_send(callback.message, 
        "✅ <b>Крипто-платежи настроены и включены!</b>\n\n"
        "Теперь пользователи смогут оплачивать криптовалютой."
    )
    
    # Showing the payment menu
    await show_payments_menu(callback, state)


# ============================================================================
# CRYPTO PAYMENT MANAGEMENT MENU
# ============================================================================

async def show_crypto_management_menu(callback: CallbackQuery, state: FSMContext):
    """Shows the menu for managing crypto payments."""
    await state.set_state(AdminStates.payments_menu)
    
    is_enabled = is_crypto_enabled()
    item_url = get_setting('crypto_item_url', '')
    
    status_emoji = "🟢" if is_enabled else "⚪"
    status_text = "включены" if is_enabled else "выключены"
    
    info_text = (
        "ℹ️ Бот генерирует ссылку на оплату с указанием точной суммы в долларах (из настроек тарифа).\n\n"
        "⚠️ <b>ВАЖНО:</b> В Ya.Seller позиция обязательно должна иметь тип <b>«Счет»</b>. "
        "Тарифы к позиции добавлять не нужно — бот сам указывает сумму.\n\n"
    )
    
    if item_url:
        safe_url = escape_html(item_url)
        text = (
            "💰 <b>Управление крипто-платежами</b>\n\n"
            f"{status_emoji} Статус: <b>{status_text}</b>\n"
            f"📦 Ссылка: <a href=\"{item_url}\">{safe_url}</a>\n\n"
            f"{info_text}"
            "Выберите действие:"
        )
    else:
        text = (
            "💰 <b>Управление крипто-платежами</b>\n\n"
            f"{status_emoji} Статус: <b>{status_text}</b>\n"
            "📦 Ссылка: —\n\n"
            f"{info_text}"
            "Выберите действие:"
        )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=crypto_management_kb(is_enabled)
    )
    await callback.answer()




async def _set_crypto_enabled(callback: CallbackQuery, state: FSMContext, target_enabled: bool):
    """Sets the status of crypto payments without data loss."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    current = is_crypto_enabled()
    if current == target_enabled:
        status = "уже включены" if target_enabled else "уже выключены"
        await callback.answer(f"Крипто-платежи {status}")
        return

    if target_enabled and not has_crypto_data():
        await callback.answer("❌ Сначала укажите ссылку на товар и секретный ключ!", show_alert=True)
        return

    new_value = '1' if target_enabled else '0'
    set_setting('crypto_enabled', new_value)

    status = "включены ✅" if new_value == '1' else "выключены"
    await callback.answer(f"Крипто-платежи {status}")
    await show_crypto_management_menu(callback, state)


@router.callback_query(F.data.startswith("admin_crypto_mgmt_set:"))
async def crypto_mgmt_set(callback: CallbackQuery, state: FSMContext):
    """Enables or disables crypto payments with the selected state."""
    target_enabled = callback.data.rsplit(":", 1)[1] == "1"
    await _set_crypto_enabled(callback, state, target_enabled)


@router.callback_query(F.data == "admin_crypto_mgmt_toggle")
async def crypto_mgmt_toggle(callback: CallbackQuery, state: FSMContext):
    """Compatible toggle for old posts."""
    await _set_crypto_enabled(callback, state, not is_crypto_enabled())


@router.callback_query(F.data == "admin_crypto_mgmt_edit_url")
async def crypto_mgmt_edit_url(callback: CallbackQuery, state: FSMContext):
    """Starts editing the product link."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.crypto_setup_url)
    await state.update_data(edit_mode=True)
    
    current_url = get_setting('crypto_item_url', '')
    
    bot_username = callback.bot.my_username if hasattr(callback.bot, 'my_username') else "YOUR_BOT"
    callback_url = f"https://t.me/{bot_username}"
    
    instructions = (
        "1️⃣ В @Ya_SellerBot выберите «Управление» → «Товары» → «Добавить»\n"
        "2️⃣ Выберите тип позиции: <b>Счет</b>\n\n"
        "🎬 <b>Актуальная инструкция как добавлять:</b>\n"
        "<a href=\"https://youtu.be/cK0wX2LKxcs\">Смотреть видео</a>\n\n"
        "⚠️ <b>ВАЖНО:</b>\n"
        "• Тип позиции — именно <b>Счет</b>, а НЕ <b>Товар</b>!\n"
        "• Тарифы добавлять к позиции <b>НЕ нужно</b> — бот сам сформирует сумму оплаты.\n\n"
    )
    
    if current_url:
        safe_url = escape_html(current_url)
        text = (
            "🔗 <b>Изменение ссылки</b>\n\n"
            f"{instructions}"
            f"Текущая ссылка: <a href=\"{current_url}\">{safe_url}</a>\n\n"
            "🔗 <b>Введите новую ссылку из @Ya_SellerBot:</b>"
        )
    else:
        text = (
            "🔗 <b>Изменение ссылки</b>\n\n"
            f"{instructions}"
            "Текущая ссылка: —\n\n"
            "🔗 <b>Введите новую ссылку из @Ya_SellerBot:</b>"
        )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=back_and_home_kb("admin_crypto_management")
    )
    await callback.answer()


@router.callback_query(F.data == "admin_crypto_mgmt_edit_secret")
async def crypto_mgmt_edit_secret(callback: CallbackQuery, state: FSMContext):
    """Begins editing the secret key."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.crypto_setup_secret)
    await state.update_data(edit_mode=True)
    
    bot_username = callback.bot.my_username if hasattr(callback.bot, 'my_username') else "YOUR_BOT"
    callback_url = f"https://t.me/{bot_username}"

    text = (
        "🔐 <b>Изменение секретного ключа</b>\n\n"
        "🔔 <b>Настройка уведомлений:</b>\n"
        "В @Ya_SellerBot зайдите в настройки вашей созданной позиции → <code>Уведомления</code> → <code>Обратная ссылка</code> и укажите этот адрес:\n"
        f"<code>{callback_url}</code>\n\n"
        "🔑 <b>Ожидаю ввода нового секретного ключа:</b>\n"
        "Найти его можно в @Ya_SellerBot: <code>Профиль</code> → <code>Ключ подписи</code>."
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=back_and_home_kb("admin_crypto_management")
    )
    await callback.answer()


@router.callback_query(F.data == "admin_crypto_management")
async def back_to_crypto_management(callback: CallbackQuery, state: FSMContext):
    """Return to the crypto payment management menu."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await show_crypto_management_menu(callback, state)


# ============================================================================
# EDITING CRYPTO SETTINGS
# ============================================================================

@router.callback_query(F.data == "admin_payments_crypto_settings")
async def start_edit_crypto(callback: CallbackQuery, state: FSMContext):
    """Starts editing crypto settings."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.edit_crypto)
    await state.update_data(edit_crypto_param=0)
    
    await show_crypto_edit_screen(callback, state, 0)


async def show_crypto_edit_screen(callback: CallbackQuery, state: FSMContext, param_index: int):
    """Shows the crypto setting editing screen."""
    param = get_crypto_param_by_index(param_index)
    total = get_total_crypto_params()
    
    current_value = get_setting(param['key'], '')
    
    # Masking the secret key
    if param['key'] == 'crypto_secret_key' and current_value:
        display_value = '•' * min(len(current_value), 16)
    else:
        display_value = current_value or '—'
    
    text = (
        f"⚙️ <b>Настройки крипто-платежей</b> ({param_index + 1}/{total})\n\n"
        f"📌 Параметр: <b>{param['label']}</b>\n"
        f"📝 Текущее значение: <code>{display_value}</code>\n\n"
        f"Введите новое значение или используйте кнопки навигации:\n"
        f"({param['hint']})"
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=edit_crypto_kb(param_index, total)
    )


@router.callback_query(F.data == "admin_crypto_edit_prev")
async def crypto_edit_prev(callback: CallbackQuery, state: FSMContext):
    """Previous crypto settings parameter."""
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
    """The next parameter is crypto settings."""
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
    """Processes the entry of a new crypto setting value."""
    if not is_admin(message.from_user.id):
        return
    
    from bot.utils.text import get_message_text_for_storage
    
    data = await state.get_data()
    param_index = data.get('edit_crypto_param', 0)
    
    param = get_crypto_param_by_index(param_index)
    value = get_message_text_for_storage(message, 'plain')
    
    # Validation
    if not param['validate'](value):
        await safe_edit_or_send(message,
            f"❌ {param['error']}"
        )
        return
    
    # Saving in the database
    set_setting(param['key'], value)
    
    # Delete the message
    try:
        await message.delete()
    except:
        pass
    
    # Showing the updated screen
    await safe_edit_or_send(message,
        f"✅ <b>{param['label']}</b> обновлено!",
        force_new=True
    )
    
    # Creating a fake callback to show the screen
    # It's a hack, but it works
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
    """Finish editing crypto settings."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await callback.answer("✅ Настройки сохранены")
    await show_payments_menu(callback, state)


# ============================================================================
# TG PAYMENTS MANAGEMENT
# ============================================================================

@router.callback_query(F.data == "admin_payments_cards")
async def show_cards_management_menu(callback: CallbackQuery, state: FSMContext):
    """Shows the TG payments management menu."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminStates.payments_menu)
    
    is_enabled = is_cards_enabled()
    token = get_setting('cards_provider_token', '')
    
    status_emoji = "🟢" if is_enabled else "⚪"
    status_text = "включено" if is_enabled else "выключено"
    
    if token:
        # Masking the token: first 4 and last 4 characters
        masked_token = f"{token[:4]}...{token[-4:]}"
        token_display = f"Установлен ✅ (<code>{masked_token}</code>)"
    else:
        token_display = "Не установлен ❌"
    
    text = (
        "💳 <b>TG payments</b>\n\n"
        "Для работы этого способа необходимо настроить Telegram Payments через провайдера ЮКасса.\n\n"
        "❗️ <b>ШАГ 1: РЕГИСТРАЦИЯ</b>\n"
        "Обязательно <a href=\"https://yookassa.ru/joinups/?source=sva\">зарегистрируйте магазин в ЮКассе по этой ссылке</a>\n\n"
        "После проверки документов ЮКассой переходите к настройке токена.\n\n"
        f"{status_emoji} Статус: <b>{status_text}</b>\n"
        f"🔑 Provider Token: <b>{token_display}</b>\n\n"
        "Выберите действие:"
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=cards_management_kb(is_enabled)
    )
    await callback.answer()


async def _set_cards_enabled(callback: CallbackQuery, state: FSMContext, target_enabled: bool):
    """Sets the status of TG payments."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    current = is_cards_enabled()
    if current == target_enabled:
        status = "уже включены" if target_enabled else "уже выключены"
        await callback.answer(f"TG payments {status}")
        return

    # Cannot be enabled if there is no token
    if target_enabled and not get_setting('cards_provider_token', ''):
        await callback.answer("❌ Сначала укажите Provider Token!", show_alert=True)
        return

    new_value = '1' if target_enabled else '0'
    set_setting('cards_enabled', new_value)

    status = "включена ✅" if new_value == '1' else "выключена"
    await callback.answer(f"TG payments {status}")
    await show_cards_management_menu(callback, state)


@router.callback_query(F.data.startswith("admin_cards_mgmt_set:"))
async def cards_mgmt_set(callback: CallbackQuery, state: FSMContext):
    """Enables or disables TG payments with the selected state."""
    target_enabled = callback.data.rsplit(":", 1)[1] == "1"
    await _set_cards_enabled(callback, state, target_enabled)


@router.callback_query(F.data == "admin_cards_mgmt_toggle")
async def cards_mgmt_toggle(callback: CallbackQuery, state: FSMContext):
    """Compatible toggle for old posts."""
    await _set_cards_enabled(callback, state, not is_cards_enabled())


@router.callback_query(F.data == "admin_cards_mgmt_edit_token")
async def cards_mgmt_edit_token(callback: CallbackQuery, state: FSMContext):
    """Begins editing the YuKassa token."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await state.set_state(AdminStates.cards_setup_token)
    # Save the message ID so you can edit it later
    await state.update_data(last_menu_msg_id=callback.message.message_id)
    
    text = (
        "🔗 <b>Установка Provider Token</b>\n\n"
        "❗️ <b>ШАГ 1: РЕГИСТРАЦИЯ В ЮКАССЕ</b>\n"
        "Обязательно <a href=\"https://yookassa.ru/joinups/?source=sva\">зарегистрируйтесь по этой ссылке</a>\n\n"
        "<b>ШАГ 2: ПОЛУЧЕНИЕ ТОКЕНА В @BotFather</b>\n"
        "1. Отправьте команду <code>/mybots</code> и выберите бота.\n"
        "2. Нажмите <code>Payments</code> → <code>YooKassa</code>.\n"
        "3. Подключите магазин в боте провайдера и <b>обязательно вернитесь в @BotFather</b>.\n"
        "4. В BotFather снова откройте <code>Payments</code>, там появится токен.\n\n"
        "Отправьте полученный токен ответом на это сообщение:"
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=back_and_home_kb("admin_payments_cards")
    )
    await callback.answer()


@router.message(AdminStates.cards_setup_token)
async def cards_setup_token_value(message: Message, state: FSMContext):
    """Processes the input of the YuKassa token."""
    from bot.utils.text import get_message_text_for_storage
    
    data = await state.get_data()
    last_menu_msg_id = data.get('last_menu_msg_id')

    token = get_message_text_for_storage(message, 'plain')
    
    if len(token) < 20 or ':' not in token:
        await safe_edit_or_send(message, "❌ Неверный формат токена. Попробуйте ещё раз:")
        return
    
    set_setting('cards_provider_token', token)
    
    try:
        await message.delete()
    except:
        pass
    
    # If we have a menu message ID, use it to edit
    menu_message = message
    if last_menu_msg_id:
        try:
            # Create a message object with the required ID for editing
            menu_message = await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=last_menu_msg_id,
                text="⌛ Сохранение..."
            )
        except Exception:
            # If it doesn’t work out (for example, the message is deleted), we will respond with new ones
            menu_message = await safe_edit_or_send(message, "⌛ Сохранение...", force_new=True)

    # Returning to the menu via FakeCallback
    class FakeCallback:
        def __init__(self, msg, user, success_msg=None):
            self.message = msg
            self.from_user = user
            self.bot = msg.bot
            self.data = "admin_payments_cards"
            self.success_msg = success_msg
        
        async def answer(self, text=None, show_alert=False, *args, **kwargs):
            # If you passed the text for the popup, remember it (it will be shown when the buttons are pressed)
            # But since AIOGram's answerCallbackQuery only works for real instances,
            # we'll just print the information to the console or skip it.
            # For the user, we will add text to the message itself.
            pass

    fake = FakeCallback(menu_message, message.from_user)
    await show_cards_management_menu(fake, state)


# ============================================================================
# SETTING UP YUKASSA (direct API)
# ============================================================================

@router.callback_query(F.data == "admin_payments_qr")
async def show_qr_management_menu(callback: CallbackQuery, state: FSMContext):
    """Shows the YuKassa QR payment management menu."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminStates.payments_menu)

    from database.requests import is_yookassa_qr_enabled
    is_enabled = is_yookassa_qr_enabled()
    shop_id = get_setting('yookassa_shop_id', '')
    secret_key = get_setting('yookassa_secret_key', '')

    status_emoji = "🟢" if is_enabled else "⚪"
    status_text = "включено" if is_enabled else "выключено"

    shop_display = f"<code>{shop_id}</code>" if shop_id else "❌ Не задан"
    secret_display = f"Установлен ✅ (<code>{secret_key[:4]}...{secret_key[-4:]}</code>)" if len(secret_key) >= 8 else "❌ Не задан"

    text = (
        "📱 <b>ЮКасса</b>\n\n"
        "Позволяет принимать оплату картами и через СБП по QR-коду,\n"
        "без Telegram Payments.\n\n"
        "📋 <b>Как получить доступ:</b>\n"
        "1. Зарегистрируйте магазин: <a href=\"https://yookassa.ru/joinups/?source=sva\">yookassa.ru</a>\n"
        "2. Перейдите: Настройки → API-интеграция\n"
        "3. Скопируйте Shop ID и сгенерируйте новый Secret Key\n\n"
        f"{status_emoji} Статус: <b>{status_text}</b>\n"
        f"🏪 Shop ID: {shop_display}\n"
        f"🔑 Secret Key: {secret_display}\n\n"
        "Выберите действие:"
    )

    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=qr_management_kb(is_enabled)
    )
    await callback.answer()


async def _set_qr_enabled(callback: CallbackQuery, state: FSMContext, target_enabled: bool):
    """Sets the status of the YuKass QR payment."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    from database.requests import is_yookassa_qr_enabled

    current = is_yookassa_qr_enabled()
    if current == target_enabled:
        status = "уже включена" if target_enabled else "уже выключена"
        await callback.answer(f"ЮКасса {status}")
        return

    # Cannot be enabled without details
    if target_enabled:
        shop_id = get_setting('yookassa_shop_id', '')
        secret_key = get_setting('yookassa_secret_key', '')
        if not shop_id or not secret_key:
            await callback.answer("❌ Сначала укажите Shop ID и Secret Key!", show_alert=True)
            return

    new_value = '1' if target_enabled else '0'
    set_setting('yookassa_qr_enabled', new_value)

    status = "включена ✅" if new_value == '1' else "выключена"
    await callback.answer(f"ЮКасса {status}")
    await show_qr_management_menu(callback, state)


@router.callback_query(F.data.startswith("admin_qr_mgmt_set:"))
async def qr_mgmt_set(callback: CallbackQuery, state: FSMContext):
    """Enables or disables YuKass QR payment with the selected state."""
    target_enabled = callback.data.rsplit(":", 1)[1] == "1"
    await _set_qr_enabled(callback, state, target_enabled)


@router.callback_query(F.data == "admin_qr_mgmt_toggle")
async def qr_mgmt_toggle(callback: CallbackQuery, state: FSMContext):
    """Compatible toggle for old posts."""
    from database.requests import is_yookassa_qr_enabled
    await _set_qr_enabled(callback, state, not is_yookassa_qr_enabled())


@router.callback_query(F.data == "admin_qr_edit_shop_id")
async def qr_edit_shop_id(callback: CallbackQuery, state: FSMContext):
    """Requests YuKass Shop ID."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminStates.qr_setup_shop_id)
    await state.update_data(last_menu_msg_id=callback.message.message_id)

    current = get_setting('yookassa_shop_id', '')
    current_display = f"\nТекущий: <code>{current}</code>" if current else ""

    await safe_edit_or_send(callback.message, 
        f"🏪 <b>Введите Shop ID ЮКасса</b>{current_display}\n\n"
        "Найдите в разделе: <b>Настройки → API-интеграция</b> вашего магазина.\n"
        "(Это числовой ID, например: <code>123456</code>)",
        reply_markup=back_and_home_kb("admin_payments_qr")
    )
    await callback.answer()


@router.message(AdminStates.qr_setup_shop_id)
async def qr_setup_shop_id_handler(message: Message, state: FSMContext):
    """Processes Shop ID input."""
    from bot.utils.text import get_message_text_for_storage
    
    shop_id = get_message_text_for_storage(message, 'plain')

    if not shop_id.isdigit() or len(shop_id) < 3:
        await safe_edit_or_send(message, "❌ Некорректный Shop ID. Должен быть числом (например, <code>123456</code>).")
        return

    try:
        await message.delete()
    except Exception:
        pass

    set_setting('yookassa_shop_id', shop_id)

    data = await state.get_data()
    last_menu_msg_id = data.get('last_menu_msg_id')

    class FakeCallback:
        def __init__(self, msg, user):
            self.message = msg
            self.from_user = user
            self.bot = msg.bot
        async def answer(self, *args, **kwargs):
            pass

    menu_message = message
    if last_menu_msg_id:
        try:
            menu_message = await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=last_menu_msg_id,
                text="⌛"
            )
        except Exception:
            menu_message = await safe_edit_or_send(message, "⌛", force_new=True)

    fake = FakeCallback(menu_message, message.from_user)
    await show_qr_management_menu(fake, state)



@router.callback_query(F.data == "admin_qr_edit_secret")
async def qr_edit_secret(callback: CallbackQuery, state: FSMContext):
    """UKassa requests Secret Key."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminStates.qr_setup_secret_key)
    await state.update_data(last_menu_msg_id=callback.message.message_id)

    await safe_edit_or_send(callback.message, 
        "🔐 <b>Введите Secret Key ЮКасса</b>\n\n"
        "Найдите в разделе: <b>Настройки → API-интеграция</b> вашего магазина.\n"
        "_(Секретный ключ будет скрыт после сохранения)_",
        reply_markup=back_and_home_kb("admin_payments_qr")
    )
    await callback.answer()


@router.message(AdminStates.qr_setup_secret_key)
async def qr_setup_secret_key_handler(message: Message, state: FSMContext):
    """Processes Secret Key input."""
    from bot.utils.text import get_message_text_for_storage
    
    secret_key = get_message_text_for_storage(message, 'plain')

    if len(secret_key) < 16:
        await safe_edit_or_send(message, "❌ Слишком короткий ключ. Попробуйте ещё раз.")
        return

    try:
        await message.delete()
    except Exception:
        pass

    set_setting('yookassa_secret_key', secret_key)

    data = await state.get_data()
    last_menu_msg_id = data.get('last_menu_msg_id')

    class FakeCallback:
        def __init__(self, msg, user):
            self.message = msg
            self.from_user = user
            self.bot = msg.bot
        async def answer(self, *args, **kwargs):
            pass

    menu_message = message
    if last_menu_msg_id:
        try:
            menu_message = await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=last_menu_msg_id,
                text="⌛"
            )
        except Exception:
            menu_message = await safe_edit_or_send(message, "⌛", force_new=True)

    fake = FakeCallback(menu_message, message.from_user)
    await show_qr_management_menu(fake, state)


# ============================================================================
# WATA SETUP
# ============================================================================

@router.callback_query(F.data == "admin_payments_wata")
async def show_wata_management_menu(callback: CallbackQuery, state: FSMContext):
    """Shows the WATA payment management menu."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminStates.payments_menu)

    is_enabled = is_wata_enabled()
    token = get_setting('wata_jwt_token', '') or ''

    status_emoji = "🟢" if is_enabled else "⚪"
    status_text = "включено" if is_enabled else "выключено"

    if len(token) >= 12:
        token_display = f"Установлен ✅ (<code>{escape_html(token[:6])}...{escape_html(token[-4:])}</code>)"
    elif token:
        token_display = "Установлен ✅"
    else:
        token_display = "❌ Не задан"

    text = (
        "🌊 <b>WATA</b>\n\n"
        "Приём платежей через WATA — российский эквайринг (карты + СБП).\n"
        "Минимальная сумма платежа: <b>10 ₽</b>.\n\n"
        "📋 <b>Инструкция по подключению:</b>\n\n"
        "<b>1. Что добавить в бот перед подачей на проверку:</b>\n"
        "• <b>Документы:</b> Пользовательское соглашение и Политику конфиденциальности (ссылки доступны в руководстве пользователя ADMIN_GUIDE.md).\n"
        "• <b>Техподдержка:</b> Кнопка поддержки должна вести в личку (вашу или саппорта), а не в канал.\n"
        "• <b>Тарифы:</b> Прописать тарифы в рублях (можно просто текстом в описании бота).\n"
        "• <b>Новостной канал:</b> Убрать ссылку на канал Плюшкина или заменить на свой новостной канал.\n\n"
        "<b>2. Верификационный платёж (разовый):</b>\n"
        "• Стандарт: 500$ (50.000₽)\n"
        "• <b>По рекомендации YadrenoVPN: 150$ (15.000₽)</b>\n"
        "• <i>Платить только после одобрения проекта!</i>\n\n"
        "<b>3. Какие документы нужны:</b>\n"
        "• Никаких. Паспорта, ИП, самозанятость, банковские счета - не требуются.\n"
        "• Достаточно: Email и кошелёк TRC20 (USDT).\n\n"
        "<b>4. Подключение:</b>\n"
        "• Сайт: <a href=\"https://wata.pro/\">wata.pro</a>\n"
        "• Менеджер в Telegram: @Nikita_WATA (напишите, что вы от <b>YadrenoVPN</b> для снижения платежа до 150$).\n"
        "• В личном кабинете создайте JWT-токен (Профиль → API) и укажите его здесь.\n\n"
        f"{status_emoji} Статус: <b>{status_text}</b>\n"
        f"🔑 JWT-токен: {token_display}\n\n"
        "Выберите действие:"
    )

    await safe_edit_or_send(
        callback.message,
        text,
        reply_markup=wata_management_kb(is_enabled),
    )
    await callback.answer()


async def _set_wata_enabled(callback: CallbackQuery, state: FSMContext, target_enabled: bool):
    """Sets the payment status via WATA."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    current = is_wata_enabled()
    if current == target_enabled:
        status = "уже включена" if target_enabled else "уже выключена"
        await callback.answer(f"WATA-оплата {status}")
        return

    if target_enabled:
        token = get_setting('wata_jwt_token', '')
        if not token or not token.strip():
            await callback.answer("❌ Сначала укажите JWT-токен!", show_alert=True)
            return

    new_value = '1' if target_enabled else '0'
    set_setting('wata_enabled', new_value)

    status = "включена ✅" if new_value == '1' else "выключена"
    await callback.answer(f"WATA-оплата {status}")
    await show_wata_management_menu(callback, state)


@router.callback_query(F.data.startswith("admin_wata_mgmt_set:"))
async def wata_mgmt_set(callback: CallbackQuery, state: FSMContext):
    """Enables or disables WATA by the selected state."""
    target_enabled = callback.data.rsplit(":", 1)[1] == "1"
    await _set_wata_enabled(callback, state, target_enabled)


@router.callback_query(F.data == "admin_wata_mgmt_toggle")
async def wata_mgmt_toggle(callback: CallbackQuery, state: FSMContext):
    """Compatible toggle for old posts."""
    await _set_wata_enabled(callback, state, not is_wata_enabled())


@router.callback_query(F.data == "admin_wata_mgmt_edit_token")
async def wata_edit_token(callback: CallbackQuery, state: FSMContext):
    """Requests a WATA JWT token."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminStates.wata_setup_token)
    await state.update_data(last_menu_msg_id=callback.message.message_id)

    await safe_edit_or_send(
        callback.message,
        "🔑 <b>Введите JWT-токен WATA</b>\n\n"
        "Найдите в личном кабинете <a href=\"https://wata.pro\">wata.pro</a>: "
        "<b>Профиль → API</b>.\n\n"
        "<i>Токен будет частично скрыт после сохранения.</i>",
        reply_markup=back_and_home_kb("admin_payments_wata"),
    )
    await callback.answer()


@router.message(AdminStates.wata_setup_token)
async def wata_setup_token_handler(message: Message, state: FSMContext):
    """Processes WATA JWT token input."""
    from bot.utils.text import get_message_text_for_storage

    token = get_message_text_for_storage(message, 'plain').strip()

    if len(token) < 20:
        await safe_edit_or_send(message, "❌ Слишком короткий токен. Попробуйте ещё раз.")
        return

    try:
        await message.delete()
    except Exception:
        pass

    set_setting('wata_jwt_token', token)

    data = await state.get_data()
    last_menu_msg_id = data.get('last_menu_msg_id')

    class FakeCallback:
        def __init__(self, msg, user):
            self.message = msg
            self.from_user = user
            self.bot = msg.bot
        async def answer(self, *args, **kwargs):
            pass

    menu_message = message
    if last_menu_msg_id:
        try:
            menu_message = await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=last_menu_msg_id,
                text="⌛"
            )
        except Exception:
            menu_message = await safe_edit_or_send(message, "⌛", force_new=True)

    fake = FakeCallback(menu_message, message.from_user)
    await show_wata_management_menu(fake, state)


# ============================================================================
# SETTING UP PLATEGA
# ============================================================================

@router.callback_query(F.data == "admin_payments_platega")
async def show_platega_management_menu(callback: CallbackQuery, state: FSMContext):
    """Shows the payment management menu through Platega."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminStates.payments_menu)

    is_enabled = is_platega_enabled()
    merchant_id = get_setting('platega_merchant_id', '') or ''
    secret = get_setting('platega_secret', '') or ''

    status_emoji = "🟢" if is_enabled else "⚪"
    status_text = "включено" if is_enabled else "выключено"

    if merchant_id:
        if len(merchant_id) >= 8:
            merchant_display = f"Установлен ✅ (<code>{escape_html(merchant_id[:4])}...{escape_html(merchant_id[-4:])}</code>)"
        else:
            merchant_display = "Установлен ✅"
    else:
        merchant_display = "❌ Не задан"

    if secret:
        if len(secret) >= 12:
            secret_display = f"Установлен ✅ (<code>{escape_html(secret[:4])}...{escape_html(secret[-4:])}</code>)"
        else:
            secret_display = "Установлен ✅"
    else:
        secret_display = "❌ Не задан"

    text = (
        "💸 <b>Platega</b>\n\n"
        "Приём платежей через Platega.\n"
        "Минимальная сумма платежа: <b>10 ₽</b>.\n\n"
        "📋 <b>Как получить доступ:</b>\n"
        "1. Напишите менеджеру <b>@platega_connect_manager</b>\n"
        "2. Скажите, что вы от <b>@plushkinva</b> — получите скидку на подключение.\n"
        "3. По умолчанию подключается <b>SBP</b>; другие методы оплаты подключаются через менеджера Platega.\n"
        "4. После подключения скопируйте <b>Merchant ID</b> и <b>Secret</b> из ЛК.\n"
        "5. Укажите их в кнопках ниже.\n\n"
        f"{status_emoji} Статус: <b>{status_text}</b>\n"
        f"🆔 Merchant ID: {merchant_display}\n"
        f"🔐 Secret: {secret_display}\n\n"
        "Выберите действие:"
    )

    await safe_edit_or_send(
        callback.message,
        text,
        reply_markup=platega_management_kb(is_enabled),
    )
    await callback.answer()


async def _set_platega_enabled(callback: CallbackQuery, state: FSMContext, target_enabled: bool):
    """Sets the payment status through Platega."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    current = is_platega_enabled()
    if current == target_enabled:
        status = "уже включена" if target_enabled else "уже выключена"
        await callback.answer(f"Platega-оплата {status}")
        return

    if target_enabled:
        merchant_id = get_setting('platega_merchant_id', '')
        secret = get_setting('platega_secret', '')
        if not merchant_id or not merchant_id.strip() or not secret or not secret.strip():
            await callback.answer("❌ Сначала укажите Merchant ID и Secret!", show_alert=True)
            return

    new_value = '1' if target_enabled else '0'
    set_setting('platega_enabled', new_value)

    status = "включена ✅" if new_value == '1' else "выключена"
    await callback.answer(f"Platega-оплата {status}")
    await show_platega_management_menu(callback, state)


@router.callback_query(F.data.startswith("admin_platega_mgmt_set:"))
async def platega_mgmt_set(callback: CallbackQuery, state: FSMContext):
    """Enables or disables Platega with the selected state."""
    target_enabled = callback.data.rsplit(":", 1)[1] == "1"
    await _set_platega_enabled(callback, state, target_enabled)


@router.callback_query(F.data == "admin_platega_mgmt_toggle")
async def platega_mgmt_toggle(callback: CallbackQuery, state: FSMContext):
    """Compatible toggle for old posts."""
    await _set_platega_enabled(callback, state, not is_platega_enabled())


@router.callback_query(F.data == "admin_platega_mgmt_edit_merchant")
async def platega_edit_merchant(callback: CallbackQuery, state: FSMContext):
    """Requests Merchant ID Platega."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminStates.platega_setup_merchant)
    await state.update_data(last_menu_msg_id=callback.message.message_id)

    await safe_edit_or_send(
        callback.message,
        "🆔 <b>Введите Merchant ID Platega</b>\n\n"
        "Найдите его в личном кабинете Platega после подключения.\n\n"
        "<i>Значение будет частично скрыто после сохранения.</i>",
        reply_markup=back_and_home_kb("admin_payments_platega"),
    )
    await callback.answer()


@router.message(AdminStates.platega_setup_merchant)
async def platega_setup_merchant_handler(message: Message, state: FSMContext):
    """Processes Platega Merchant ID input."""
    from bot.utils.text import get_message_text_for_storage

    merchant_id = get_message_text_for_storage(message, 'plain').strip()

    if len(merchant_id) < 4:
        await safe_edit_or_send(message, "❌ Слишком короткий Merchant ID. Попробуйте ещё раз.")
        return

    try:
        await message.delete()
    except Exception:
        pass

    set_setting('platega_merchant_id', merchant_id)

    data = await state.get_data()
    last_menu_msg_id = data.get('last_menu_msg_id')

    class FakeCallback:
        def __init__(self, msg, user):
            self.message = msg
            self.from_user = user
            self.bot = msg.bot
        async def answer(self, *args, **kwargs):
            pass

    menu_message = message
    if last_menu_msg_id:
        try:
            menu_message = await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=last_menu_msg_id,
                text="⌛"
            )
        except Exception:
            menu_message = await safe_edit_or_send(message, "⌛", force_new=True)

    fake = FakeCallback(menu_message, message.from_user)
    await show_platega_management_menu(fake, state)


@router.callback_query(F.data == "admin_platega_mgmt_edit_secret")
async def platega_edit_secret(callback: CallbackQuery, state: FSMContext):
    """Requests Secret Platega."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminStates.platega_setup_secret)
    await state.update_data(last_menu_msg_id=callback.message.message_id)

    await safe_edit_or_send(
        callback.message,
        "🔐 <b>Введите Secret Platega</b>\n\n"
        "Найдите его в личном кабинете Platega после подключения.\n\n"
        "<i>Значение будет частично скрыто после сохранения.</i>",
        reply_markup=back_and_home_kb("admin_payments_platega"),
    )
    await callback.answer()


@router.message(AdminStates.platega_setup_secret)
async def platega_setup_secret_handler(message: Message, state: FSMContext):
    """Processes Secret Platega input."""
    from bot.utils.text import get_message_text_for_storage

    secret = get_message_text_for_storage(message, 'plain').strip()

    if len(secret) < 8:
        await safe_edit_or_send(message, "❌ Слишком короткий Secret. Попробуйте ещё раз.")
        return

    try:
        await message.delete()
    except Exception:
        pass

    set_setting('platega_secret', secret)

    data = await state.get_data()
    last_menu_msg_id = data.get('last_menu_msg_id')

    class FakeCallback:
        def __init__(self, msg, user):
            self.message = msg
            self.from_user = user
            self.bot = msg.bot
        async def answer(self, *args, **kwargs):
            pass

    menu_message = message
    if last_menu_msg_id:
        try:
            menu_message = await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=last_menu_msg_id,
                text="⌛"
            )
        except Exception:
            menu_message = await safe_edit_or_send(message, "⌛", force_new=True)

    fake = FakeCallback(menu_message, message.from_user)
    await show_platega_management_menu(fake, state)


# ============================================================================
# CARDLINK SETUP
# ============================================================================

@router.callback_query(F.data == "admin_payments_cardlink")
async def show_cardlink_management_menu(callback: CallbackQuery, state: FSMContext):
    """Shows the Cardlink payment management menu."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminStates.payments_menu)

    is_enabled = is_cardlink_enabled()
    shop_id = get_setting('cardlink_shop_id', '') or ''
    api_token = get_setting('cardlink_api_token', '') or ''

    status_emoji = "🟢" if is_enabled else "⚪"
    status_text = "включено" if is_enabled else "выключено"

    if shop_id:
        if len(shop_id) >= 8:
            shop_display = f"Установлен ✅ (<code>{escape_html(shop_id[:4])}...{escape_html(shop_id[-4:])}</code>)"
        else:
            shop_display = "Установлен ✅"
    else:
        shop_display = "❌ Не задан"

    if api_token:
        if len(api_token) >= 12:
            token_display = f"Установлен ✅ (<code>{escape_html(api_token[:4])}...{escape_html(api_token[-4:])}</code>)"
        else:
            token_display = "Установлен ✅"
    else:
        token_display = "❌ Не задан"

    # bot_name for return links
    try:
        bot_info = await callback.bot.get_me()
        bot_username = bot_info.username or "your_bot"
    except Exception:
        bot_username = "your_bot"

    text = (
        "🔗 <b>Cardlink</b>\n\n"
        "Приём платежей картой и через СБП по прямой интеграции с "
        "<a href=\"https://cardlink.link/\">cardlink.link</a> "
        "(без webhook — проверка по кнопке «Я оплатил» и через возвратные ссылки).\n"
        "Минимальная сумма платежа: <b>10 ₽</b>.\n\n"
        "👉 <b>Почему это рекомендуемый метод:</b> Ниже комиссии и максимально нативная интеграция "
        "с обратным переходом в бота после оплаты и автоматической проверкой платежа.\n\n"
        "📋 <b>Как получить доступ:</b>\n"
        "1. Зарегистрируйтесь на <a href=\"https://cardlink.link/\">cardlink.link</a>.\n"
        "2. После одобрения скопируйте <b>Shop ID</b> и <b>API-токен</b> из ЛК.\n"
        "3. Укажите их в кнопках ниже.\n\n"
        "🔁 <b>Возврат в бота</b>\n"
        f"• В каждый счёт бот передаёт точную ссылку вида "
        f"<code>https://t.me/{bot_username}?start=pay_cardlink_ORDER_ID</code>\n"
        "• Если в магазине уже указаны старые статические ссылки, их можно оставить "
        "как fallback:\n"
        f"<code>https://t.me/{bot_username}?start=cl_Success</code>\n"
        f"<code>https://t.me/{bot_username}?start=cl_Fail</code>\n"
        f"<code>https://t.me/{bot_username}?start=cl_Result</code>\n\n"
        f"{status_emoji} Статус: <b>{status_text}</b>\n"
        f"🆔 Shop ID: {shop_display}\n"
        f"🔐 API-токен: {token_display}\n\n"
        "Выберите действие:"
    )

    await safe_edit_or_send(
        callback.message,
        text,
        reply_markup=cardlink_management_kb(is_enabled),
    )
    await callback.answer()


async def _set_cardlink_enabled(callback: CallbackQuery, state: FSMContext, target_enabled: bool):
    """Sets the payment status via Cardlink."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    current = is_cardlink_enabled()
    if current == target_enabled:
        status = "уже включена" if target_enabled else "уже выключена"
        await callback.answer(f"Cardlink-оплата {status}")
        return

    if target_enabled:
        shop_id = get_setting('cardlink_shop_id', '')
        api_token = get_setting('cardlink_api_token', '')
        if not shop_id or not shop_id.strip() or not api_token or not api_token.strip():
            await callback.answer("❌ Сначала укажите Shop ID и API-токен!", show_alert=True)
            return

    new_value = '1' if target_enabled else '0'
    set_setting('cardlink_enabled', new_value)

    status = "включена ✅" if new_value == '1' else "выключена"
    await callback.answer(f"Cardlink-оплата {status}")
    await show_cardlink_management_menu(callback, state)


@router.callback_query(F.data.startswith("admin_cardlink_mgmt_set:"))
async def cardlink_mgmt_set(callback: CallbackQuery, state: FSMContext):
    """Enables or disables Cardlink with the selected state."""
    target_enabled = callback.data.rsplit(":", 1)[1] == "1"
    await _set_cardlink_enabled(callback, state, target_enabled)


@router.callback_query(F.data == "admin_cardlink_mgmt_toggle")
async def cardlink_mgmt_toggle(callback: CallbackQuery, state: FSMContext):
    """Compatible toggle for old posts."""
    await _set_cardlink_enabled(callback, state, not is_cardlink_enabled())


@router.callback_query(F.data == "admin_cardlink_mgmt_edit_shop_id")
async def cardlink_edit_shop_id(callback: CallbackQuery, state: FSMContext):
    """Requests Shop ID Cardlink."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminStates.cardlink_setup_shop_id)
    await state.update_data(last_menu_msg_id=callback.message.message_id)

    await safe_edit_or_send(
        callback.message,
        "🆔 <b>Введите Shop ID Cardlink</b>\n\n"
        "Найдите его в личном кабинете на <a href=\"https://cardlink.link/\">cardlink.link</a>.\n\n"
        "<i>Значение будет частично скрыто после сохранения.</i>",
        reply_markup=back_and_home_kb("admin_payments_cardlink"),
    )
    await callback.answer()


@router.message(AdminStates.cardlink_setup_shop_id)
async def cardlink_setup_shop_id_handler(message: Message, state: FSMContext):
    """Processes Shop ID Cardlink input."""
    from bot.utils.text import get_message_text_for_storage

    shop_id = get_message_text_for_storage(message, 'plain').strip()

    if len(shop_id) < 4:
        await safe_edit_or_send(message, "❌ Слишком короткий Shop ID. Попробуйте ещё раз.")
        return

    try:
        await message.delete()
    except Exception:
        pass

    set_setting('cardlink_shop_id', shop_id)

    data = await state.get_data()
    last_menu_msg_id = data.get('last_menu_msg_id')

    class FakeCallback:
        def __init__(self, msg, user):
            self.message = msg
            self.from_user = user
            self.bot = msg.bot
        async def answer(self, *args, **kwargs):
            pass

    menu_message = message
    if last_menu_msg_id:
        try:
            menu_message = await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=last_menu_msg_id,
                text="⌛"
            )
        except Exception:
            menu_message = await safe_edit_or_send(message, "⌛", force_new=True)

    fake = FakeCallback(menu_message, message.from_user)
    await show_cardlink_management_menu(fake, state)


@router.callback_query(F.data == "admin_cardlink_mgmt_edit_api_token")
async def cardlink_edit_api_token(callback: CallbackQuery, state: FSMContext):
    """Requests a Cardlink API token."""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminStates.cardlink_setup_api_token)
    await state.update_data(last_menu_msg_id=callback.message.message_id)

    await safe_edit_or_send(
        callback.message,
        "🔐 <b>Введите API-токен Cardlink</b>\n\n"
        "Сгенерируйте токен в личном кабинете на <a href=\"https://cardlink.link/\">cardlink.link</a>.\n\n"
        "<i>Значение будет частично скрыто после сохранения.</i>",
        reply_markup=back_and_home_kb("admin_payments_cardlink"),
    )
    await callback.answer()


@router.message(AdminStates.cardlink_setup_api_token)
async def cardlink_setup_api_token_handler(message: Message, state: FSMContext):
    """Handles Cardlink API token input."""
    from bot.utils.text import get_message_text_for_storage

    api_token = get_message_text_for_storage(message, 'plain').strip()

    if len(api_token) < 8:
        await safe_edit_or_send(message, "❌ Слишком короткий токен. Попробуйте ещё раз.")
        return

    try:
        await message.delete()
    except Exception:
        pass

    set_setting('cardlink_api_token', api_token)

    data = await state.get_data()
    last_menu_msg_id = data.get('last_menu_msg_id')

    class FakeCallback:
        def __init__(self, msg, user):
            self.message = msg
            self.from_user = user
            self.bot = msg.bot
        async def answer(self, *args, **kwargs):
            pass

    menu_message = message
    if last_menu_msg_id:
        try:
            menu_message = await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=last_menu_msg_id,
                text="⌛"
            )
        except Exception:
            menu_message = await safe_edit_or_send(message, "⌛", force_new=True)

    fake = FakeCallback(menu_message, message.from_user)
    await show_cardlink_management_menu(fake, state)


