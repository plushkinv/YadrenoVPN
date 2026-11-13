"""
Router of the “Payments” section.

Processes:
- Main payment screen
- Toggle for Stars/Crypto
- Setting up crypto payments
- Editing crypto settings
"""

from localization import text as _tr
from core.payment_provider_specs import INTERNATIONAL_PROVIDERS, REGIONAL_CURRENCY_EXPONENTS
from database.requests import is_payment_admin_visible, is_gateway_enabled
import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext

from config import ADMIN_IDS
from database.requests import (
    get_setting,
    set_setting,
    get_base_currency,
    get_currency_rate,
    set_currency_rate,
    is_crypto_enabled,
    is_cryptobot_enabled,
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
from bot.utils.telegram_links import build_telegram_link
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
    payment_rates_kb,
    base_currency_switch_input_kb,
    base_currency_switch_confirm_kb,
    back_and_home_kb
)
from bot.utils.text import escape_html, safe_edit_or_send
from bot.services.base_currency import (
    BaseCurrencySwitchBlocked,
    build_base_currency_switch_preview,
    switch_base_currency,
)
from bot.services.money import format_money_minor

logger = logging.getLogger(__name__)


router = Router()
_RATE_MESSAGES: dict[int, Message] = {}


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
    
    Format: https://<telegram_link_domain>/Ya_SellerBot?start=item-{item_id}...
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    await state.set_state(AdminStates.payments_menu)

    stars = is_stars_enabled()
    crypto = is_crypto_enabled()
    cryptobot = is_cryptobot_enabled()
    cards = is_cards_enabled()
    qr = is_yookassa_qr_enabled()
    demo = is_demo_payment_enabled()
    wata = is_wata_enabled()
    platega = is_platega_enabled()
    cardlink = is_cardlink_enabled()

    text = (
        _tr('payments.show_payments_menu.1')
    )

    rows = [
        ('stars', stars, 'Telegram Stars'), ('cryptobot', cryptobot, 'Crypto Pay (@CryptoBot)'),
        ('crypto', crypto, 'Ya.Seller'), ('cards', cards, 'TG Payments'),
        ('yookassa_qr', qr, _tr('payment_provider_adapters.module.1')),
        ('wata', wata, 'WATA'), ('platega', platega, 'Platega'), ('cardlink', cardlink, 'Cardlink'),
        ('demo', demo, _tr('payment_provider_adapters.module.2')),
        *((pid, is_gateway_enabled(pid), title) for pid, (title, _, _) in INTERNATIONAL_PROVIDERS.items()),
    ]
    for pid, enabled, title in rows:
        if is_payment_admin_visible(pid):
            if pid == 'crypto' and enabled and get_setting('crypto_item_url', ''):
                text += _tr('payments.show_payments_menu.2', p0=escape_html(get_setting('crypto_item_url', '')))
            elif pid == 'yookassa_qr' and enabled:
                text += _tr('payments.show_payments_menu.5', p0=escape_html(get_setting('yookassa_shop_id', '') or '—'))
            else:
                text += f'{"🟢" if enabled else "⚪"} <b>{escape_html(title)}</b>\n'

    notify = get_setting('payment_notifications_enabled', '0') == '1'

    await safe_edit_or_send(callback.message,
        text,
        reply_markup=payments_menu_kb(
            stars,
            crypto,
            cards,
            qr,
            demo,
            wata,
            platega,
            cardlink,
            notify_enabled=notify,
            cryptobot_enabled=cryptobot,
        )
    )
    await callback.answer()


@router.callback_query(F.data == 'admin_payment_rates')
async def show_payment_rates(callback: CallbackQuery, state: FSMContext):
    """Shows the global base currency and fixed rates for new invoices."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    await state.set_state(AdminStates.payments_menu)
    await _render_payment_rates(callback.message)
    await callback.answer()


@router.callback_query(F.data.startswith('admin_payment_rate_edit:'))
async def edit_payment_rate(callback: CallbackQuery, state: FSMContext):
    """Starts one Decimal rate edit while keeping the source message id."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    target_currency = callback.data.split(':', 1)[1].upper()
    labels = {
        **{currency: currency for currency in REGIONAL_CURRENCY_EXPONENTS},
        'RUB': 'RUB',
        'USD': 'USD',
        'USDT': 'USDT',
        'XTR': 'Stars',
    }
    base_currency = get_base_currency()
    if target_currency not in labels or target_currency == base_currency:
        await callback.answer(_tr('payments.edit_payment_rate.1'), show_alert=True)
        return
    _RATE_MESSAGES[callback.from_user.id] = callback.message
    await state.set_state(AdminStates.payment_rate_value)
    await state.update_data(
        payment_rate_currency=target_currency,
        payment_rate_message_id=callback.message.message_id,
        payment_rate_chat_id=callback.message.chat.id,
    )
    await safe_edit_or_send(
        callback.message,
        (
            _tr('payments.edit_payment_rate.2', p0=f'{labels[target_currency]}', p1=f'{labels[target_currency]}', p2=f'{base_currency}')
        ),
        reply_markup=back_and_home_kb('admin_payment_rates'),
    )
    await callback.answer()


@router.message(AdminStates.payment_rate_value, F.text)
async def payment_rate_value_input(message: Message, state: FSMContext):
    """Stores a positive decimal string and redraws the original admin message."""
    from bot.utils.text import get_message_text_for_storage

    raw = get_message_text_for_storage(message, 'plain').strip().replace(',', '.')
    normalized = _normalize_positive_decimal(raw)
    try:
        await message.delete()
    except Exception:
        pass
    data = await state.get_data()
    target_currency = data.get('payment_rate_currency')
    source_message = _RATE_MESSAGES.get(message.from_user.id)
    if not target_currency or normalized is None:
        await safe_edit_or_send(
            source_message or message,
            (
                _tr('payments.payment_rate_value_input.1')
            ),
            reply_markup=back_and_home_kb('admin_payment_rates'),
            force_new=source_message is None,
        )
        return
    set_currency_rate(str(target_currency), normalized)
    await state.set_state(AdminStates.payments_menu)
    _RATE_MESSAGES.pop(message.from_user.id, None)
    await _render_payment_rates(source_message or message, force_new=source_message is None)


async def _render_payment_rates(message: Message, *, force_new: bool = False) -> None:
    base = get_base_currency()
    rows = [_tr('payments.render_payment_rates.1', p0=f'{base}'), ""]
    for target, icon, label in (
        ('RUB', '₽', 'RUB'),
        ('USD', '💵', 'USD'),
        ('USDT', '🪙', 'USDT'),
        ('XTR', '⭐', 'Stars'),
        *((currency, '💱', currency) for currency in REGIONAL_CURRENCY_EXPONENTS),
    ):
        if target == base:
            continue
        rate = get_currency_rate(target, base_currency=base)
        rendered = (
            escape_html(_format_rate_for_display(rate)) if rate else _tr('payments.render_payment_rates.2')
        )
        rows.append(f"{icon} 1 {base} = <code>{rendered} {label}</code>")
    await safe_edit_or_send(
        message,
        _tr('payments.render_payment_rates.3')
        + "\n".join(rows)
        + _tr('payments.render_payment_rates.4'),
        reply_markup=payment_rates_kb(base),
        force_new=force_new,
    )


@router.callback_query(F.data.startswith('admin_base_currency_select:'))
async def select_base_currency(callback: CallbackQuery, state: FSMContext):
    """Starts the protected base-currency conversion wizard."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    target = callback.data.split(':', 1)[1].upper()
    source = get_base_currency()
    if target not in {'RUB', 'USD'} or target == source:
        await callback.answer(_tr('payments.select_base_currency.1'), show_alert=True)
        return
    _RATE_MESSAGES[callback.from_user.id] = callback.message
    await state.set_state(AdminStates.base_currency_transition_rate)
    await state.update_data(
        base_currency_from=source,
        base_currency_to=target,
    )
    await safe_edit_or_send(
        callback.message,
        (
            _tr('payments.select_base_currency.2', p0=f'{source}', p1=f'{target}', p2=f'{source}', p3=f'{target}', p4=f'{target}', p5=f'{source}')
        ),
        reply_markup=base_currency_switch_input_kb(),
    )
    await callback.answer()


@router.message(AdminStates.base_currency_transition_rate, F.text)
async def base_currency_transition_rate_input(message: Message, state: FSMContext):
    """Builds and displays a non-mutating switch preview."""
    from bot.utils.text import get_message_text_for_storage

    raw = get_message_text_for_storage(message, 'plain').strip().replace(',', '.')
    data = await state.get_data()
    source = str(data.get('base_currency_from') or '')
    target = str(data.get('base_currency_to') or '')
    prompt = _RATE_MESSAGES.get(message.from_user.id)
    try:
        preview = build_base_currency_switch_preview(target, raw)
    except (TypeError, ValueError) as error:
        try:
            await message.delete()
        except Exception:
            pass
        await safe_edit_or_send(
            prompt or message,
            _tr('payments.base_currency_transition_rate_input.1'),
            reply_markup=base_currency_switch_input_kb(),
            force_new=prompt is None,
        )
        return
    try:
        await message.delete()
    except Exception:
        pass

    tariff_lines = []
    for tariff in preview.get('tariffs', [])[:12]:
        tariff_lines.append(
            f"• {escape_html(tariff['name'])}: "
            f"{format_money_minor(tariff['before_minor'], source)} → "
            f"{format_money_minor(tariff['after_minor'], target)}"
        )
    if len(preview.get('tariffs', [])) > 12:
        tariff_lines.append(_tr('payments.base_currency_transition_rate_input.2', p0=f"{len(preview['tariffs']) - 12}"))
    blocking = int(preview.get('blocking_intents') or 0)
    warning = (
        _tr('payments.base_currency_transition_rate_input.3', p0=f'{blocking}')
        if blocking
        else ''
    )
    text = (
        _tr('payments.base_currency_transition_rate_input.4', p0=f'{source}', p1=f'{target}', p2=f'{target}', p3=f"{escape_html(preview['old_units_per_new'])}", p4=f'{source}')
        + ("\n".join(tariff_lines) or _tr('payments.base_currency_transition_rate_input.5'))
        + _tr('payments.base_currency_transition_rate_input.6', p0=f"{format_money_minor(preview['balance_before_minor'], source)}", p1=f"{format_money_minor(preview['balance_after_minor'], target)}", p2=f"{format_money_minor(preview['referral_before_minor'], source)}", p3=f"{format_money_minor(preview['referral_after_minor'], target)}", p4=f"{preview['cancelable_intents']}", p5=f'{warning}')
    )
    await state.set_state(AdminStates.base_currency_switch_confirm)
    await state.update_data(base_currency_transition_rate=preview['old_units_per_new'])
    await safe_edit_or_send(
        prompt or message,
        text,
        reply_markup=(
            base_currency_switch_input_kb()
            if blocking
            else base_currency_switch_confirm_kb(target)
        ),
        force_new=prompt is None,
    )


@router.callback_query(AdminStates.base_currency_switch_confirm, F.data == 'admin_base_currency_confirm')
async def confirm_base_currency_switch(callback: CallbackQuery, state: FSMContext):
    """Creates a backup and commits the confirmed accounting conversion."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    data = await state.get_data()
    source = str(data.get('base_currency_from') or '')
    target = str(data.get('base_currency_to') or '')
    rate = data.get('base_currency_transition_rate')
    await safe_edit_or_send(
        callback.message,
        _tr('payments.confirm_base_currency_switch.1'),
        reply_markup=base_currency_switch_input_kb(),
    )
    try:
        result = await switch_base_currency(
            expected_from_currency=source,
            target_currency=target,
            old_units_per_new=rate,
            admin_telegram_id=callback.from_user.id,
        )
    except BaseCurrencySwitchBlocked:
        await safe_edit_or_send(
            callback.message,
            _tr('payments.confirm_base_currency_switch.2'),
            reply_markup=base_currency_switch_input_kb(),
        )
        await callback.answer()
        return
    except Exception as error:
        logger.exception('Base currency switch failed: %s', error)
        await safe_edit_or_send(
            callback.message,
            _tr('payments.confirm_base_currency_switch.3'),
            reply_markup=base_currency_switch_input_kb(),
        )
        await callback.answer()
        return
    await state.set_state(AdminStates.payments_menu)
    _RATE_MESSAGES.pop(callback.from_user.id, None)
    await safe_edit_or_send(
        callback.message,
        (
            _tr('payments.confirm_base_currency_switch.4', p0=f'{target}', p1=f"{result['converted_tariffs']}", p2=f"{result['converted_balances']}", p3=f"{result['converted_referral_rows']}", p4=f"{result['canceled_intents']}", p5=f"{escape_html(result['backup_path'])}")
        ),
        reply_markup=payment_rates_kb(target),
    )
    await callback.answer(_tr('payments.confirm_base_currency_switch.5'))


def _normalize_positive_decimal(value: str) -> str | None:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite() or parsed <= 0 or parsed > Decimal('1000000'):
        return None
    rendered = format(parsed, 'f')
    if '.' in rendered:
        rendered = rendered.rstrip('0').rstrip('.')
    return rendered or None


def _format_rate_for_display(value: object, *, significant_digits: int = 6) -> str:
    """Formats a stored rate compactly without losing very small non-zero values."""
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    if not parsed.is_finite() or parsed == 0 or significant_digits < 1:
        return str(value)
    quantum = Decimal(1).scaleb(parsed.copy_abs().adjusted() - significant_digits + 1)
    try:
        rounded = parsed.quantize(quantum, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        rounded = parsed
    rendered = format(rounded, 'f')
    if '.' in rendered:
        rendered = rendered.rstrip('0').rstrip('.')
    return rendered or '0'


@router.callback_query(F.data == "admin_toggle_payment_notify")
async def toggle_payment_notify(callback: CallbackQuery, state: FSMContext):
    """Switch payment notifications."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    current = get_setting('payment_notifications_enabled', '0')
    new_val = '0' if current == '1' else '1'
    set_setting('payment_notifications_enabled', new_val)
    
    status = _tr('payments.toggle_payment_notify.1') if new_val == '1' else _tr('payments.toggle_payment_notify.2')
    await callback.answer(_tr('payments.toggle_payment_notify.3', p0=f'{status}'))
    
    # Redrawing the payment menu
    await show_payments_menu(callback, state)


# ============================================================================
# TOGGLE STARS
# ============================================================================

@router.callback_query(F.data == "admin_payments_toggle_stars")
async def toggle_stars(callback: CallbackQuery, state: FSMContext):
    """Switches Telegram Stars."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    current = is_stars_enabled()
    new_value = '0' if current else '1'
    set_setting('stars_enabled', new_value)
    
    status = _tr('payments.toggle_stars.1') if new_value == '1' else _tr('payments.toggle_payment_notify.2')
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    current = is_demo_payment_enabled()
    new_value = '0' if current else '1'
    set_setting('demo_payment_enabled', new_value)
    
    status = _tr('payments.toggle_demo.1') if new_value == '1' else _tr('payments.toggle_demo.2')
    await callback.answer(_tr('payments.toggle_demo.3', p0=f'{status}'))
    
    # Refresh the screen
    await show_payments_menu(callback, state)


# ============================================================================
# TOGGLE CRYPTO
# ============================================================================

@router.callback_query(F.data == "admin_payments_toggle_crypto")
async def toggle_crypto(callback: CallbackQuery, state: FSMContext):
    """Opens the settings or menu for managing crypto payments."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
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
    callback_url = build_telegram_link(bot_username)
    
    instructions = (
        _tr('payments.start_crypto_setup.1')
    )

    text = (
        _tr('payments.start_crypto_setup.2', p0=f'{instructions}')
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
            _tr('payments.process_crypto_url.1', p0=f"{param['error']}")
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
            _tr('payments.process_crypto_url.2', p0=f'{url}', p1=f'{escape_html(url)}'),
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
        callback_url = build_telegram_link(bot_username)

        await safe_edit_or_send(message,
            _tr('payments.process_crypto_url.3', p0=f'{url}', p1=f'{escape_html(url)}', p2=f'{callback_url}'),
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
            _tr('payments.process_crypto_url.1', p0=f"{param['error']}")
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
        await safe_edit_or_send(message, _tr('payments.process_crypto_secret.1'), force_new=True)
        
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
            _tr('payments.process_crypto_secret.2', p0=f'{item_url}', p1=f'{escape_html(item_url)}', p2=f"{'•' * 16}"),
            reply_markup=crypto_setup_confirm_kb(),
            force_new=True
        )


@router.callback_query(F.data == "admin_crypto_setup_back")
async def crypto_setup_back(callback: CallbackQuery, state: FSMContext):
    """Return to the previous crypto setup step."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    data = await state.get_data()
    crypto_data = data.get('crypto_data', {})
    
    if not crypto_data.get('crypto_item_url') or not crypto_data.get('crypto_secret_key'):
        await callback.answer(_tr('payments.crypto_setup_save.1'), show_alert=True)
        return
    
    # Save
    set_setting('crypto_item_url', crypto_data['crypto_item_url'])
    set_setting('crypto_secret_key', crypto_data['crypto_secret_key'])
    set_setting('crypto_enabled', '1')
    
    await callback.answer(_tr('payments.crypto_setup_save.2'))
    
    await safe_edit_or_send(callback.message, 
        _tr('payments.crypto_setup_save.3')
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
    status_text = _tr('payments.show_crypto_management_menu.1') if is_enabled else _tr('payments.toggle_payment_notify.2')
    
    info_text = (
        _tr('payments.show_crypto_management_menu.2')
    )
    
    if item_url:
        safe_url = escape_html(item_url)
        text = (
            _tr('payments.show_crypto_management_menu.3', p0=f'{status_emoji}', p1=f'{status_text}', p2=f'{item_url}', p3=f'{safe_url}', p4=f'{info_text}')
        )
    else:
        text = (
            _tr('payments.show_crypto_management_menu.4', p0=f'{status_emoji}', p1=f'{status_text}', p2=f'{info_text}')
        )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=crypto_management_kb(is_enabled)
    )
    await callback.answer()




async def _set_crypto_enabled(callback: CallbackQuery, state: FSMContext, target_enabled: bool):
    """Sets the status of crypto payments without data loss."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    current = is_crypto_enabled()
    if current == target_enabled:
        status = _tr('payments.set_crypto_enabled.1') if target_enabled else _tr('payments.set_crypto_enabled.2')
        await callback.answer(_tr('payments.set_crypto_enabled.3', p0=f'{status}'))
        return

    if target_enabled and not has_crypto_data():
        await callback.answer(_tr('payments.set_crypto_enabled.4'), show_alert=True)
        return

    new_value = '1' if target_enabled else '0'
    set_setting('crypto_enabled', new_value)

    status = _tr('payments.set_crypto_enabled.5') if new_value == '1' else _tr('payments.toggle_payment_notify.2')
    await callback.answer(_tr('payments.set_crypto_enabled.3', p0=f'{status}'))
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    await state.set_state(AdminStates.crypto_setup_url)
    await state.update_data(edit_mode=True)
    
    current_url = get_setting('crypto_item_url', '')
    
    bot_username = callback.bot.my_username if hasattr(callback.bot, 'my_username') else "YOUR_BOT"
    callback_url = build_telegram_link(bot_username)
    
    instructions = (
        _tr('payments.start_crypto_setup.1')
    )
    
    if current_url:
        safe_url = escape_html(current_url)
        text = (
            _tr('payments.crypto_mgmt_edit_url.1', p0=f'{instructions}', p1=f'{current_url}', p2=f'{safe_url}')
        )
    else:
        text = (
            _tr('payments.crypto_mgmt_edit_url.2', p0=f'{instructions}')
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    await state.set_state(AdminStates.crypto_setup_secret)
    await state.update_data(edit_mode=True)
    
    bot_username = callback.bot.my_username if hasattr(callback.bot, 'my_username') else "YOUR_BOT"
    callback_url = build_telegram_link(bot_username)

    text = (
        _tr('payments.crypto_mgmt_edit_secret.1', p0=f'{callback_url}')
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    await show_crypto_management_menu(callback, state)


# ============================================================================
# EDITING CRYPTO SETTINGS
# ============================================================================

@router.callback_query(F.data == "admin_payments_crypto_settings")
async def start_edit_crypto(callback: CallbackQuery, state: FSMContext):
    """Starts editing crypto settings."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
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
        _tr('payments.show_crypto_edit_screen.1', p0=f'{param_index + 1}', p1=f'{total}', p2=f"{param['label']}", p3=f'{display_value}', p4=f"{param['hint']}")
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=edit_crypto_kb(param_index, total)
    )


@router.callback_query(F.data == "admin_crypto_edit_prev")
async def crypto_edit_prev(callback: CallbackQuery, state: FSMContext):
    """Previous crypto settings parameter."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
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
        _tr('payments.edit_crypto_value.1', p0=f"{param['label']}"),
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    await callback.answer(_tr('payments.crypto_edit_done.1'))
    await show_payments_menu(callback, state)


# ============================================================================
# TG PAYMENTS MANAGEMENT
# ============================================================================

@router.callback_query(F.data == "admin_payments_cards")
async def show_cards_management_menu(callback: CallbackQuery, state: FSMContext):
    """Shows the TG payments management menu."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    await state.set_state(AdminStates.payments_menu)
    
    is_enabled = is_cards_enabled()
    token = get_setting('cards_provider_token', '')
    
    status_emoji = "🟢" if is_enabled else "⚪"
    status_text = _tr('coupons.render_purchase_settings.1') if is_enabled else _tr('coupons.render_purchase_settings.2')
    
    if token:
        # Masking the token: first 4 and last 4 characters
        masked_token = f"{token[:4]}...{token[-4:]}"
        token_display = _tr('payments.show_cards_management_menu.1', p0=f'{masked_token}')
    else:
        token_display = _tr('payments.show_cards_management_menu.2')
    
    text = (
        _tr('payments.show_cards_management_menu.3', p0=f'{status_emoji}', p1=f'{status_text}', p2=f'{token_display}')
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=cards_management_kb(is_enabled)
    )
    await callback.answer()


async def _set_cards_enabled(callback: CallbackQuery, state: FSMContext, target_enabled: bool):
    """Sets the status of TG payments."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    current = is_cards_enabled()
    if current == target_enabled:
        status = _tr('payments.set_crypto_enabled.1') if target_enabled else _tr('payments.set_crypto_enabled.2')
        await callback.answer(f"TG payments {status}")
        return

    # Cannot be enabled if there is no token
    if target_enabled and not get_setting('cards_provider_token', ''):
        await callback.answer(_tr('payments.set_cards_enabled.1'), show_alert=True)
        return

    new_value = '1' if target_enabled else '0'
    set_setting('cards_enabled', new_value)

    status = _tr('payments.set_cards_enabled.2') if new_value == '1' else _tr('payments.toggle_demo.2')
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
    """Begins editing the Telegram Payments provider token."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    await state.set_state(AdminStates.cards_setup_token)
    # Save the message ID so you can edit it later
    await state.update_data(last_menu_msg_id=callback.message.message_id)
    
    text = (
        _tr('payments.cards_mgmt_edit_token.1')
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=back_and_home_kb("admin_payments_cards")
    )
    await callback.answer()


@router.message(AdminStates.cards_setup_token)
async def cards_setup_token_value(message: Message, state: FSMContext):
    """Processes the input of the Telegram Payments provider token."""
    from bot.utils.text import get_message_text_for_storage
    
    data = await state.get_data()
    last_menu_msg_id = data.get('last_menu_msg_id')

    token = get_message_text_for_storage(message, 'plain')
    
    if len(token) < 20 or ':' not in token:
        await safe_edit_or_send(message, _tr('payments.cards_setup_token_value.1'))
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
                text=_tr('payments.cards_setup_token_value.2')
            )
        except Exception:
            # If it doesn’t work out (for example, the message is deleted), we will respond with new ones
            menu_message = await safe_edit_or_send(message, _tr('payments.cards_setup_token_value.2'), force_new=True)

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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    await state.set_state(AdminStates.payments_menu)

    from database.requests import is_yookassa_qr_enabled
    is_enabled = is_yookassa_qr_enabled()
    shop_id = get_setting('yookassa_shop_id', '')
    secret_key = get_setting('yookassa_secret_key', '')

    status_emoji = "🟢" if is_enabled else "⚪"
    status_text = _tr('coupons.render_purchase_settings.1') if is_enabled else _tr('coupons.render_purchase_settings.2')

    shop_display = f"<code>{shop_id}</code>" if shop_id else _tr('cryptobot.masked_token.1')
    secret_display = _tr('cryptobot.masked_token.3', p0=f'{secret_key[:4]}', p1=f'{secret_key[-4:]}') if len(secret_key) >= 8 else _tr('cryptobot.masked_token.1')

    text = (
        _tr('payments.show_qr_management_menu.1', p0=f'{status_emoji}', p1=f'{status_text}', p2=f'{shop_display}', p3=f'{secret_display}')
    )

    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=qr_management_kb(is_enabled)
    )
    await callback.answer()


async def _set_qr_enabled(callback: CallbackQuery, state: FSMContext, target_enabled: bool):
    """Sets the status of the YuKass QR payment."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    from database.requests import is_yookassa_qr_enabled

    current = is_yookassa_qr_enabled()
    if current == target_enabled:
        status = _tr('coupons.set_coupon_scenario_enabled.3') if target_enabled else _tr('coupons.set_coupon_scenario_enabled.4')
        await callback.answer(_tr('payments.set_qr_enabled.1', p0=f'{status}'))
        return

    # Cannot be enabled without details
    if target_enabled:
        shop_id = get_setting('yookassa_shop_id', '')
        secret_key = get_setting('yookassa_secret_key', '')
        if not shop_id or not secret_key:
            await callback.answer(_tr('payments.set_qr_enabled.2'), show_alert=True)
            return

    new_value = '1' if target_enabled else '0'
    set_setting('yookassa_qr_enabled', new_value)

    status = _tr('payments.set_cards_enabled.2') if new_value == '1' else _tr('payments.toggle_demo.2')
    await callback.answer(_tr('payments.set_qr_enabled.1', p0=f'{status}'))
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    await state.set_state(AdminStates.qr_setup_shop_id)
    await state.update_data(last_menu_msg_id=callback.message.message_id)

    current = get_setting('yookassa_shop_id', '')
    current_display = _tr('payments.qr_edit_shop_id.1', p0=f'{current}') if current else ""

    await safe_edit_or_send(callback.message, 
        _tr('payments.qr_edit_shop_id.2', p0=f'{current_display}'),
        reply_markup=back_and_home_kb("admin_payments_qr")
    )
    await callback.answer()


@router.message(AdminStates.qr_setup_shop_id)
async def qr_setup_shop_id_handler(message: Message, state: FSMContext):
    """Processes Shop ID input."""
    from bot.utils.text import get_message_text_for_storage
    
    shop_id = get_message_text_for_storage(message, 'plain')

    if not shop_id.isdigit() or len(shop_id) < 3:
        await safe_edit_or_send(message, _tr('payments.qr_setup_shop_id_handler.1'))
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    await state.set_state(AdminStates.qr_setup_secret_key)
    await state.update_data(last_menu_msg_id=callback.message.message_id)

    await safe_edit_or_send(callback.message, 
        _tr('payments.qr_edit_secret.1'),
        reply_markup=back_and_home_kb("admin_payments_qr")
    )
    await callback.answer()


@router.message(AdminStates.qr_setup_secret_key)
async def qr_setup_secret_key_handler(message: Message, state: FSMContext):
    """Processes Secret Key input."""
    from bot.utils.text import get_message_text_for_storage
    
    secret_key = get_message_text_for_storage(message, 'plain')

    if len(secret_key) < 16:
        await safe_edit_or_send(message, _tr('payments.qr_setup_secret_key_handler.1'))
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    await state.set_state(AdminStates.payments_menu)

    is_enabled = is_wata_enabled()
    token = get_setting('wata_jwt_token', '') or ''

    status_emoji = "🟢" if is_enabled else "⚪"
    status_text = _tr('coupons.render_purchase_settings.1') if is_enabled else _tr('coupons.render_purchase_settings.2')

    if len(token) >= 12:
        token_display = _tr('cryptobot.masked_token.3', p0=f'{escape_html(token[:6])}', p1=f'{escape_html(token[-4:])}')
    elif token:
        token_display = _tr('cryptobot.masked_token.2')
    else:
        token_display = _tr('cryptobot.masked_token.1')

    text = (
        _tr('payments.show_wata_management_menu.1', p0=f'{status_emoji}', p1=f'{status_text}', p2=f'{token_display}')
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    current = is_wata_enabled()
    if current == target_enabled:
        status = _tr('coupons.set_coupon_scenario_enabled.3') if target_enabled else _tr('coupons.set_coupon_scenario_enabled.4')
        await callback.answer(_tr('payments.set_wata_enabled.1', p0=f'{status}'))
        return

    if target_enabled:
        token = get_setting('wata_jwt_token', '')
        if not token or not token.strip():
            await callback.answer(_tr('payments.set_wata_enabled.2'), show_alert=True)
            return

    new_value = '1' if target_enabled else '0'
    set_setting('wata_enabled', new_value)

    status = _tr('payments.set_cards_enabled.2') if new_value == '1' else _tr('payments.toggle_demo.2')
    await callback.answer(_tr('payments.set_wata_enabled.1', p0=f'{status}'))
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    await state.set_state(AdminStates.wata_setup_token)
    await state.update_data(last_menu_msg_id=callback.message.message_id)

    await safe_edit_or_send(
        callback.message,
        _tr('payments.wata_edit_token.1'),
        reply_markup=back_and_home_kb("admin_payments_wata"),
    )
    await callback.answer()


@router.message(AdminStates.wata_setup_token)
async def wata_setup_token_handler(message: Message, state: FSMContext):
    """Processes WATA JWT token input."""
    from bot.utils.text import get_message_text_for_storage

    token = get_message_text_for_storage(message, 'plain').strip()

    if len(token) < 20:
        await safe_edit_or_send(message, _tr('payments.wata_setup_token_handler.1'))
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    await state.set_state(AdminStates.payments_menu)

    is_enabled = is_platega_enabled()
    merchant_id = get_setting('platega_merchant_id', '') or ''
    secret = get_setting('platega_secret', '') or ''

    status_emoji = "🟢" if is_enabled else "⚪"
    status_text = _tr('coupons.render_purchase_settings.1') if is_enabled else _tr('coupons.render_purchase_settings.2')

    if merchant_id:
        if len(merchant_id) >= 8:
            merchant_display = _tr('cryptobot.masked_token.3', p0=f'{escape_html(merchant_id[:4])}', p1=f'{escape_html(merchant_id[-4:])}')
        else:
            merchant_display = _tr('cryptobot.masked_token.2')
    else:
        merchant_display = _tr('cryptobot.masked_token.1')

    if secret:
        if len(secret) >= 12:
            secret_display = _tr('cryptobot.masked_token.3', p0=f'{escape_html(secret[:4])}', p1=f'{escape_html(secret[-4:])}')
        else:
            secret_display = _tr('cryptobot.masked_token.2')
    else:
        secret_display = _tr('cryptobot.masked_token.1')

    text = (
        _tr('payments.show_platega_management_menu.1', p0=f'{status_emoji}', p1=f'{status_text}', p2=f'{merchant_display}', p3=f'{secret_display}')
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    current = is_platega_enabled()
    if current == target_enabled:
        status = _tr('coupons.set_coupon_scenario_enabled.3') if target_enabled else _tr('coupons.set_coupon_scenario_enabled.4')
        await callback.answer(_tr('payments.set_platega_enabled.1', p0=f'{status}'))
        return

    if target_enabled:
        merchant_id = get_setting('platega_merchant_id', '')
        secret = get_setting('platega_secret', '')
        if not merchant_id or not merchant_id.strip() or not secret or not secret.strip():
            await callback.answer(_tr('payments.set_platega_enabled.2'), show_alert=True)
            return

    new_value = '1' if target_enabled else '0'
    set_setting('platega_enabled', new_value)

    status = _tr('payments.set_cards_enabled.2') if new_value == '1' else _tr('payments.toggle_demo.2')
    await callback.answer(_tr('payments.set_platega_enabled.1', p0=f'{status}'))
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    await state.set_state(AdminStates.platega_setup_merchant)
    await state.update_data(last_menu_msg_id=callback.message.message_id)

    await safe_edit_or_send(
        callback.message,
        _tr('payments.platega_edit_merchant.1'),
        reply_markup=back_and_home_kb("admin_payments_platega"),
    )
    await callback.answer()


@router.message(AdminStates.platega_setup_merchant)
async def platega_setup_merchant_handler(message: Message, state: FSMContext):
    """Processes Platega Merchant ID input."""
    from bot.utils.text import get_message_text_for_storage

    merchant_id = get_message_text_for_storage(message, 'plain').strip()

    if len(merchant_id) < 4:
        await safe_edit_or_send(message, _tr('payments.platega_setup_merchant_handler.1'))
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
    """Requests the Platega API key."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    await state.set_state(AdminStates.platega_setup_secret)
    await state.update_data(last_menu_msg_id=callback.message.message_id)

    await safe_edit_or_send(
        callback.message,
        _tr('payments.platega_edit_secret.1'),
        reply_markup=back_and_home_kb("admin_payments_platega"),
    )
    await callback.answer()


@router.message(AdminStates.platega_setup_secret)
async def platega_setup_secret_handler(message: Message, state: FSMContext):
    """Processes Platega API key input."""
    from bot.utils.text import get_message_text_for_storage

    secret = get_message_text_for_storage(message, 'plain').strip()

    if len(secret) < 8:
        await safe_edit_or_send(message, _tr('payments.platega_setup_secret_handler.1'))
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    await state.set_state(AdminStates.payments_menu)

    is_enabled = is_cardlink_enabled()
    shop_id = get_setting('cardlink_shop_id', '') or ''
    api_token = get_setting('cardlink_api_token', '') or ''

    status_emoji = "🟢" if is_enabled else "⚪"
    status_text = _tr('coupons.render_purchase_settings.1') if is_enabled else _tr('coupons.render_purchase_settings.2')

    if shop_id:
        if len(shop_id) >= 8:
            shop_display = _tr('cryptobot.masked_token.3', p0=f'{escape_html(shop_id[:4])}', p1=f'{escape_html(shop_id[-4:])}')
        else:
            shop_display = _tr('cryptobot.masked_token.2')
    else:
        shop_display = _tr('cryptobot.masked_token.1')

    if api_token:
        if len(api_token) >= 12:
            token_display = _tr('cryptobot.masked_token.3', p0=f'{escape_html(api_token[:4])}', p1=f'{escape_html(api_token[-4:])}')
        else:
            token_display = _tr('cryptobot.masked_token.2')
    else:
        token_display = _tr('cryptobot.masked_token.1')

    # bot_name for return links
    try:
        bot_info = await callback.bot.get_me()
        bot_username = bot_info.username or "your_bot"
    except Exception:
        bot_username = "your_bot"

    text = (
        _tr('payments.show_cardlink_management_menu.1', p0=f"{build_telegram_link(bot_username, 'pay_cardlink_ORDER_ID')}", p1=f'{status_emoji}', p2=f'{status_text}', p3=f'{shop_display}', p4=f'{token_display}')
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    current = is_cardlink_enabled()
    if current == target_enabled:
        status = _tr('coupons.set_coupon_scenario_enabled.3') if target_enabled else _tr('coupons.set_coupon_scenario_enabled.4')
        await callback.answer(_tr('payments.set_cardlink_enabled.1', p0=f'{status}'))
        return

    if target_enabled:
        shop_id = get_setting('cardlink_shop_id', '')
        api_token = get_setting('cardlink_api_token', '')
        if not shop_id or not shop_id.strip() or not api_token or not api_token.strip():
            await callback.answer(_tr('payments.set_cardlink_enabled.2'), show_alert=True)
            return

    new_value = '1' if target_enabled else '0'
    set_setting('cardlink_enabled', new_value)

    status = _tr('payments.set_cards_enabled.2') if new_value == '1' else _tr('payments.toggle_demo.2')
    await callback.answer(_tr('payments.set_cardlink_enabled.1', p0=f'{status}'))
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    await state.set_state(AdminStates.cardlink_setup_shop_id)
    await state.update_data(last_menu_msg_id=callback.message.message_id)

    await safe_edit_or_send(
        callback.message,
        _tr('payments.cardlink_edit_shop_id.1'),
        reply_markup=back_and_home_kb("admin_payments_cardlink"),
    )
    await callback.answer()


@router.message(AdminStates.cardlink_setup_shop_id)
async def cardlink_setup_shop_id_handler(message: Message, state: FSMContext):
    """Processes Shop ID Cardlink input."""
    from bot.utils.text import get_message_text_for_storage

    shop_id = get_message_text_for_storage(message, 'plain').strip()

    if len(shop_id) < 4:
        await safe_edit_or_send(message, _tr('payments.cardlink_setup_shop_id_handler.1'))
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
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    await state.set_state(AdminStates.cardlink_setup_api_token)
    await state.update_data(last_menu_msg_id=callback.message.message_id)

    await safe_edit_or_send(
        callback.message,
        _tr('payments.cardlink_edit_api_token.1'),
        reply_markup=back_and_home_kb("admin_payments_cardlink"),
    )
    await callback.answer()


@router.message(AdminStates.cardlink_setup_api_token)
async def cardlink_setup_api_token_handler(message: Message, state: FSMContext):
    """Handles Cardlink API token input."""
    from bot.utils.text import get_message_text_for_storage

    api_token = get_message_text_for_storage(message, 'plain').strip()

    if len(api_token) < 8:
        await safe_edit_or_send(message, _tr('payments.wata_setup_token_handler.1'))
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


