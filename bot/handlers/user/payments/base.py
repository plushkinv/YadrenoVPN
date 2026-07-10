import logging
from typing import Optional
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from bot.utils.page_flow import build_page_flow_context
from bot.utils.text import escape_html, safe_edit_or_send
from config import ADMIN_IDS

logger = logging.getLogger(__name__)
router = Router()

PAYMENT_DEEPLINK_PREFIX = 'pay_'
PAYMENT_DEEPLINK_PROVIDERS = {'yookassa', 'wata', 'platega', 'cardlink'}
QR_PAYMENT_PAGE_KEY = 'qr_payment'


def parse_payment_deeplink(start_param: str) -> Optional[dict]:
    """
    Разбирает единый deep-link возврата из платёжной формы.

    Формат: pay_{provider}_{order_id}
    """
    if not start_param or not start_param.startswith(PAYMENT_DEEPLINK_PREFIX):
        return None

    payload = start_param[len(PAYMENT_DEEPLINK_PREFIX):]
    provider, separator, order_id = payload.partition('_')
    if not separator or provider not in PAYMENT_DEEPLINK_PROVIDERS or not order_id:
        return None

    return {
        'provider': provider,
        'order_id': order_id,
    }


async def handle_payment_deeplink(
    message: Message,
    state: FSMContext,
    start_param: str,
    user_internal_id: int,
    telegram_id: int,
) -> bool:
    """
    Обрабатывает платёжные deep-link из /start.

    Возвращает True, если параметр относится к платежам и дальнейшая обработка /start не нужна.
    """
    if not start_param:
        return False

    async def _show_deeplink_status(title_html: str, body_text: str, provider_title: str = '') -> None:
        from bot.handlers.user.payments.status_page import show_payment_status_message
        from bot.keyboards.admin import home_only_kb

        await show_payment_status_message(
            message,
            title_html=title_html,
            body_text=body_text,
            payment_provider_title=provider_title,
            reply_markup=home_only_kb(),
            force_new=True,
        )

    if start_param.startswith(PAYMENT_DEEPLINK_PREFIX):
        parsed = parse_payment_deeplink(start_param)
        if not parsed:
            await _show_deeplink_status(
                '⚠️ <b>Платёжная ссылка устарела или повреждена</b>',
                'Откройте оплату заново из бота и попробуйте ещё раз.',
                'Платёжная ссылка',
            )
            return True

        provider = parsed['provider']
        order_id = parsed['order_id']

        if provider == 'yookassa':
            from bot.handlers.user.payments.yookassa import _run_yookassa_check
            await _run_yookassa_check(
                message, state, order_id=order_id,
                telegram_id=telegram_id, callback=None
            )
        elif provider == 'wata':
            from bot.handlers.user.payments.wata import _run_wata_check
            await _run_wata_check(
                message, state, order_id=order_id,
                telegram_id=telegram_id, callback=None
            )
        elif provider == 'platega':
            from bot.handlers.user.payments.platega import _run_platega_check
            await _run_platega_check(
                message, state, order_id=order_id,
                telegram_id=telegram_id, callback=None
            )
        elif provider == 'cardlink':
            from bot.handlers.user.payments.cardlink import _run_cardlink_check
            await _run_cardlink_check(
                message, state, order_id=order_id,
                telegram_id=telegram_id, callback=None
            )
        return True

    # Совместимость со старыми ссылками Cardlink из настроек магазина.
    if start_param.startswith('cl_'):
        from database.requests import find_latest_pending_cardlink_order_for_user
        from bot.handlers.user.payments.cardlink import _run_cardlink_check

        order = find_latest_pending_cardlink_order_for_user(user_internal_id)
        if not order:
            await _show_deeplink_status(
                '⚠️ <b>Активная оплата Cardlink не найдена</b>',
                (
                    'Возможно, платёж уже обработан или ещё не создан.\n'
                    'Откройте «Купить ключ» и попробуйте снова.'
                ),
                'Cardlink',
            )
            return True

        await _run_cardlink_check(
            message, state, order_id=order['order_id'],
            telegram_id=telegram_id, callback=None
        )
        return True

    return False


def _format_price_compact(cents: int) -> str:
    """Форматирование цены в компактном виде."""
    if cents >= 10000:
        return f'{cents // 100} ₽'
    else:
        return f'{cents / 100:.2f} ₽'.replace('.', ',')

def _is_cards_via_yookassa_direct() -> bool:
    """
    Проверяет, используется ли прямой сценарий ЮKassa для доплаты.
    
    Returns:
        True если прямой сценарий ЮKassa доступен от 1 ₽,
        False если используется Telegram Payments API с минимумом около 100 ₽
    """
    from database.requests import get_setting
    return get_setting('cards_via_yookassa_direct', '0') == '1'


async def complete_promo_free_payment(
    callback: CallbackQuery,
    state: FSMContext,
    order_id: str,
    telegram_id: int,
) -> None:
    """Завершает заказ с 100% скидкой без создания счёта у провайдера."""
    from database.requests import update_payment_type
    from bot.services.billing import complete_payment_flow

    update_payment_type(order_id, 'promo_free')
    await complete_payment_flow(
        order_id=order_id,
        message=callback.message,
        state=state,
        telegram_id=telegram_id,
        payment_type='promo_free',
        referral_amount=0,
    )

@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout: PreCheckoutQuery):
    """Подтверждение pre-checkout для Telegram Stars."""
    await pre_checkout.answer(ok=True)

@router.message(F.successful_payment)
async def successful_payment_handler(message: Message, state: FSMContext):
    """
    Обработка успешной оплаты Stars или TG payments.
    
    Делегирует общую post-payment логику в complete_payment_flow().
    """
    from bot.services.billing import complete_payment_flow
    payment = message.successful_payment
    payload = payment.invoice_payload
    currency = payment.currency
    payment_type = 'stars' if currency == 'XTR' else 'cards'
    logger.info(f'Успешная оплата {payment_type}: {payload}, charge_id={payment.telegram_payment_charge_id}')
    
    if payload.startswith('renew:'):
        order_id = payload.split(':')[1]
    elif payload.startswith('vpn_key:'):
        order_id = payload.split(':')[1]
    else:
        order_id = payload
    
    await complete_payment_flow(
        order_id=order_id,
        message=message,
        state=state,
        telegram_id=message.from_user.id,
        payment_type=payment_type,
        referral_amount=payment.total_amount
    )

async def finalize_payment_ui(message: Message, state: FSMContext, text: str, order: dict, user_id: int):
    """
    Завершает UI после успешной оплаты.
    Показывает сообщение и либо перекидывает на настройку (draft), либо на главную.
    """
    from bot.keyboards.admin import home_only_kb
    from database.requests import get_key_details_for_user, get_user_by_id
    import logging
    logger = logging.getLogger(__name__)
    from bot.handlers.user.payments.keys_config import start_new_key_config
    key_id = order.get('vpn_key_id')
    logger.info(f"finalize_payment_ui: Order={order.get('order_id')}, Key={key_id}, User={user_id}")
    is_draft = False
    if key_id:
        key = get_key_details_for_user(key_id, user_id)
        if key:
            logger.info(f"Key details found: ID={key['id']}, ServerID={key.get('server_id')}")
            if not key.get('server_id'):
                is_draft = True
        else:
            logger.warning(f'Key {key_id} not found for user {user_id} via details check!')
    else:
        logger.info('No key_id in order object.')
    logger.info(f'Result: is_draft={is_draft}')
    if is_draft:
        from bot.handlers.user.payments.status_page import show_payment_status_message

        title_html, body_html = _parse_success_payment_status_text(text)
        await show_payment_status_message(
            message,
            title_html=title_html,
            body_html=body_html,
            payment_provider_title='Оплата',
            force_new=True,
        )
        owner_internal_id = order.get('user_id')
        if not owner_internal_id:
            raise RuntimeError(f"У заказа {order.get('order_id')} не указан владелец")
        owner = get_user_by_id(owner_internal_id)
        if not owner:
            raise RuntimeError(f"Владелец заказа {order.get('order_id')} не найден")
        if owner.get('telegram_id') != user_id:
            raise RuntimeError(
                f"Владелец заказа {order.get('order_id')} не совпадает с payment flow user_id={user_id}"
            )
        owner_username = owner.get('username')
        await start_new_key_config(
            message,
            state,
            order['order_id'],
            key_id,
            owner_telegram_id=user_id,
            owner_username=owner_username,
        )
    else:
        from bot.handlers.user.keys import show_key_details
        await show_key_details(telegram_id=user_id, key_id=key_id, message=message, is_callback=False, prepend_text=text)


def _parse_success_payment_status_text(text: str) -> tuple[str, str]:
    """Преобразует legacy success-текст оплаты в title/body для payment_status."""
    body_html = str(text or '').strip()
    title_html = '✅ <b>Оплата принята</b>'

    success_prefix = '✅ Оплата прошла успешно!'
    accepted_prefix = '✅ Оплата принята'
    duplicate_prefix = '✅ Этот платёж уже был обработан ранее.'

    if body_html.startswith(success_prefix):
        title_html = '✅ <b>Оплата прошла успешно</b>'
        body_html = body_html[len(success_prefix):].lstrip()
    elif body_html.startswith(duplicate_prefix):
        title_html = '✅ <b>Платёж уже обработан</b>'
        body_html = ''
    elif body_html.startswith(accepted_prefix):
        title_html = '✅ <b>Оплата принята</b>'
        body_html = body_html[len(accepted_prefix):].lstrip(' .\n')

    return title_html, body_html


async def send_telegram_invoice_or_status(
    callback: CallbackQuery,
    *,
    provider_title: str,
    log_context: str,
    **invoice_kwargs,
) -> bool:
    """
    Отправляет Telegram invoice и показывает page-backed ошибку, если Telegram API
    не принял технический запрос на создание счёта.
    """
    message = getattr(callback, 'message', None)
    if message is None:
        await callback.answer('❌ Не удалось создать счёт', show_alert=True)
        return False

    try:
        await message.answer_invoice(**invoice_kwargs)
        return True
    except Exception as e:
        from bot.handlers.user.payments.status_page import show_payment_status_message
        from bot.keyboards.admin import home_only_kb

        error_text = str(e)
        if (
            'CURRENCY_TOTAL_AMOUNT_INVALID' in error_text
            or 'PRICE_TOTAL_AMOUNT_INVALID' in error_text
        ):
            logger.warning(
                "Telegram invoice rejected by amount limit (%s): %s",
                log_context,
                e,
            )
            body_html = (
                'Сумма тарифа меньше допустимого лимита платёжной системы.\n'
                'Выберите другой тариф или способ оплаты.'
            )
        else:
            logger.exception("Не удалось создать Telegram invoice (%s).", log_context)
            body_html = (
                'Платёжная система не приняла запрос на создание счёта.\n'
                'Попробуйте другой способ оплаты или обратитесь в поддержку.'
            )

        await show_payment_status_message(
            message,
            title_html='❌ <b>Не удалось создать счёт</b>',
            body_html=body_html,
            payment_provider_title=provider_title,
            reply_markup=home_only_kb(),
        )
        await callback.answer()
        return False

@router.callback_query(F.data.startswith('renew_invoice_cancel:'))
async def renew_invoice_cancel_handler(callback: CallbackQuery):
    """Отмена инвойса и возврат к выбору способа оплаты."""
    from database.requests import get_key_details_for_user
    from bot.handlers.user.keys import show_renew_payment_page
    parts = callback.data.split(':')
    key_id = int(parts[1])
    telegram_id = callback.from_user.id

    key = get_key_details_for_user(key_id, telegram_id)
    if not key:
        await callback.answer('❌ Ключ не найден', show_alert=True)
        return

    await show_renew_payment_page(callback, key, key_id, force_new=True)
    await callback.answer()


# ============================================================================
# ОБЩИЕ ФУНКЦИИ ДЛЯ QR-ПЛАТЁЖНЫХ ПРОВАЙДЕРОВ (wata, platega, cardlink, yookassa)
# ============================================================================


def default_qr_payment_page_text() -> str:
    """Дефолтный текст технической страницы QR-оплаты."""
    return (
        "%платеж_провайдер%\n\n"
        "%платеж_ключ_строка%"
        "💳 <b>Тариф:</b> %платеж_тариф%\n"
        "💰 <b>Сумма:</b> %платеж_сумма%\n"
        "⏳ <b>%платеж_срок_тип%:</b> %платеж_срок%\n"
        "%платеж_скидка_строка%"
        "\n%платеж_инструкция%\n\n"
        "<i>%платеж_подсказка%</i>"
    )


def _format_payment_link(qr_url: str) -> str:
    return f'<a href="{escape_html(str(qr_url))}">ссылке на оплату</a>'


def _format_payment_discount_line(promo_lines: str | None) -> str:
    discount = (promo_lines or '').strip('\n')
    return f'{discount}\n' if discount else ''


def build_qr_payment_page_context(
    *,
    title: str,
    tariff_name: str,
    price_str: str,
    days: int,
    qr_url: str,
    key_name: str | None,
    hint_text: str | None,
    instruction_text: str | None,
    promo_lines: str | None,
    telegram_id: int | None = None,
    bot_username: str | None = None,
) -> dict:
    payment_link = _format_payment_link(qr_url)
    if instruction_text is None:
        instruction_html = f"Отсканируйте QR код для перехода по {payment_link}."
    else:
        instruction_html = instruction_text.format(payment_link=payment_link)

    if hint_text is None:
        hint_text = 'После оплаты нажмите «✅ Я оплатил».'

    context = {
        'payment_provider_title_html': title,
        'payment_key_line_html': f"🔑 <b>Ключ:</b> {key_name}\n" if key_name else '',
        'payment_tariff_html': tariff_name,
        'payment_amount_text': price_str,
        'payment_term_label': 'Продление' if key_name else 'Срок',
        'payment_term_text': f'+{days} дней' if key_name else f'{days} дней',
        'payment_url': str(qr_url),
        'payment_link_html': payment_link,
        'payment_instruction_html': instruction_html,
        'payment_hint_text': hint_text,
        'payment_discount_line_html': _format_payment_discount_line(promo_lines),
    }
    if telegram_id:
        context['telegram_id'] = telegram_id
    if bot_username:
        context['bot_username'] = bot_username
    return context


def _render_qr_payment_page_text(context: dict) -> str:
    try:
        from bot.utils.page_renderer import render_page_text

        text = render_page_text(QR_PAYMENT_PAGE_KEY, context=context)
        if text is not None:
            return text
    except Exception as e:
        logger.warning("Не удалось отрендерить страницу %s: %s", QR_PAYMENT_PAGE_KEY, e)

    from bot.utils.placeholders import apply_page_placeholders

    fallback_context = {'page_key': QR_PAYMENT_PAGE_KEY}
    fallback_context.update(context)
    return apply_page_placeholders(
        default_qr_payment_page_text(),
        context=fallback_context,
        mode='html',
    ) or '(пусто)'


def build_qr_payment_reply_markup(context: dict, append_buttons=None):
    """
    Собирает клавиатуру QR-экрана: custom-кнопки из pages + runtime-кнопки оплаты.
    """
    from aiogram.types import InlineKeyboardMarkup

    try:
        from bot.utils.page_renderer import build_page_keyboard

        markup = build_page_keyboard(
            QR_PAYMENT_PAGE_KEY,
            context=context,
            append_buttons=append_buttons,
        )
        if markup is not None:
            return markup
        if append_buttons:
            return InlineKeyboardMarkup(inline_keyboard=append_buttons)
    except Exception as e:
        logger.warning("Не удалось собрать клавиатуру %s: %s", QR_PAYMENT_PAGE_KEY, e)
        if append_buttons:
            return InlineKeyboardMarkup(inline_keyboard=append_buttons)
    return None


def remember_qr_payment_page_context(
    telegram_id: int,
    message,
    context: dict,
    reply_markup=None,
    append_buttons=None,
) -> None:
    """Сохраняет техническую QR-страницу для контекстной команды /yaa."""
    if telegram_id not in ADMIN_IDS or message is None:
        return
    try:
        from bot.services.page_context import remember_page_context

        render_context = {'page_key': QR_PAYMENT_PAGE_KEY}
        render_context.update(context)
        runtime_rows = append_buttons
        if runtime_rows is None:
            runtime_rows = getattr(reply_markup, 'inline_keyboard', None)
        remember_page_context(
            telegram_id,
            page_key=QR_PAYMENT_PAGE_KEY,
            message=message,
            context=render_context,
            append_buttons=runtime_rows,
        )
    except Exception as e:
        logger.warning("Не удалось сохранить контекст QR-оплаты для /yaa: %s", e)


def _message_photo_file_id(message) -> str | None:
    photos = getattr(message, 'photo', None) or []
    if not photos:
        return None
    return getattr(photos[-1], 'file_id', None)


async def rerender_qr_payment_page_context(page_context, viewer_id: int) -> bool:
    """Перерисовывает сохранённый QR-экран оплаты после изменения через /yaa."""
    from bot.utils.text import safe_edit_or_send

    context = dict(page_context.context or {})
    if not context:
        return False

    text = _render_qr_payment_page_text(context)
    reply_markup = build_qr_payment_reply_markup(context, page_context.append_buttons)
    photo_file_id = _message_photo_file_id(page_context.message)

    rendered_message = await safe_edit_or_send(
        page_context.message,
        text,
        media=photo_file_id,
        media_type='photo' if photo_file_id else None,
        reply_markup=reply_markup,
    )
    remember_qr_payment_page_context(
        viewer_id,
        rendered_message,
        context,
        reply_markup,
        append_buttons=page_context.append_buttons,
    )
    return True


def format_qr_payment_text(
    title: str,
    tariff_name: str,
    price_str: str,
    days: int,
    qr_url: str,
    key_name: str = None,
    hint_text: str = None,
    instruction_text: str = None,
    promo_lines: str = None,
    telegram_id: int | None = None,
    bot_username: str | None = None,
) -> str:
    """
    Формирует текст сообщения с QR-кодом оплаты.

    Единая точка для всех провайдеров. Сначала пытается взять текст страницы
    qr_payment из pages, а если страница ещё не создана миграцией — использует
    встроенный дефолт.

    Args:
        title: Заголовок (напр. '📱 <b>ЮКасса</b>')
        tariff_name: Название тарифа (уже экранировано через escape_html)
        price_str: Отформатированная цена (напр. '100 ₽' или '50.00 ₽')
        days: Количество дней
        qr_url: Ссылка на оплату
        key_name: Название ключа при продлении (уже экранировано); None для покупки
        hint_text: Пользовательская подсказка внизу; если None — стандартная
        instruction_text: Пользовательская инструкция со ссылкой; поддерживает
            плейсхолдер {payment_link}
        promo_lines: HTML-блок скидки/ценовой политики из describe_quote_lines()
        telegram_id: ID пользователя для общих плейсхолдеров конструктора.
        bot_username: username бота для общих плейсхолдеров конструктора.
    """
    context = build_qr_payment_page_context(
        title=title,
        tariff_name=tariff_name,
        price_str=price_str,
        days=days,
        qr_url=qr_url,
        key_name=key_name,
        hint_text=hint_text,
        instruction_text=instruction_text,
        promo_lines=promo_lines,
        telegram_id=telegram_id,
        bot_username=bot_username,
    )
    return _render_qr_payment_page_text(context)


async def create_qr_payment_flow(
    callback: CallbackQuery,
    state: FSMContext,
    tariff: dict,
    price_rub: float,
    payment_type: str,
    create_func,
    save_func,
    result_key: str,
    title: str,
    check_prefix: str,
    error_name: str,
    qr_filename: str,
    back_callback: str,
    loading_text: str = '⏳ Создаём ссылку на оплату...',
    key: dict = None,
    vpn_key_id: int = None,
    hint_text: str = None,
    instruction_text: str = None,
) -> None:
    """
    Универсальный flow создания QR-счёта для любого провайдера.

    Выполняет: валидацию пользователя → создание ордера → вызов API провайдера →
    сохранение ID платежа → формирование текста → отправку QR-фото.

    Args:
        callback: Callback от кнопки выбора тарифа
        tariff: Словарь тарифа (уже валидирован)
        price_rub: Сумма к оплате в рублях
        payment_type: Тип платежа ('wata', 'platega', 'cardlink', 'yookassa_qr')
        create_func: Async-функция создания платежа (amount_rub, order_id, description, bot_name)
        save_func: Функция сохранения ID платежа в ордере (order_id, payment_id)
        result_key: Ключ в dict результата API (напр. 'wata_link_id')
        title: Заголовок сообщения (напр. '🌊 <b>Оплата WATA</b>')
        check_prefix: Префикс callback проверки (напр. 'check_wata')
        error_name: Название провайдера для ошибок (напр. 'WATA')
        qr_filename: Имя файла QR-картинки (напр. 'wata.png')
        back_callback: Callback кнопки «Назад» на экране QR
        loading_text: Текст загрузки
        key: Словарь ключа при продлении (None для покупки)
        vpn_key_id: ID ключа при продлении (None для покупки)
        hint_text: Пользовательская подсказка (None → стандартная)
        instruction_text: Пользовательская инструкция со ссылкой на оплату
    """
    from database.requests import get_user_internal_id, create_pending_order
    from bot.keyboards.user import qr_payment_kb
    from bot.keyboards.admin import home_only_kb
    from bot.services.promotions import describe_quote_lines, format_amount, prepare_order_pricing
    from bot.handlers.user.payments.status_page import (
        show_payment_status_message,
        show_payment_unavailable_status,
    )

    # Валидация пользователя
    user_id = get_user_internal_id(callback.from_user.id)
    if not user_id:
        await callback.answer('❌ Пользователь не найден', show_alert=True)
        return

    # Создание ордера
    (_, order_id) = create_pending_order(
        user_id=user_id, tariff_id=tariff['id'],
        payment_type=payment_type, vpn_key_id=vpn_key_id
    )

    quote = prepare_order_pricing(
        order_id=order_id,
        user_id=user_id,
        tariff=tariff,
        payment_type=payment_type,
        action='renewal' if vpn_key_id else 'new_key',
    )
    if not quote['ok']:
        await show_payment_unavailable_status(
            callback.message,
            quote['unavailable_reason'],
            payment_provider_title=error_name,
        )
        await callback.answer()
        return
    if quote['is_free']:
        await complete_promo_free_payment(callback, state, order_id, callback.from_user.id)
        await callback.answer()
        return

    price_rub = quote['final_amount'] / 100

    await show_payment_status_message(
        callback.message,
        title_html=escape_html(loading_text),
        body_html='',
        payment_provider_title=error_name,
    )

    try:
        bot_info = await callback.bot.get_me()
        bot_name = bot_info.username

        # Описание для провайдера
        if key:
            description = (
                f"Продление Ключа «{key['display_name']}»: "
                f"«{tariff['name']}» ({tariff['duration_days']} дн.)"
            )
        else:
            description = f"Покупка «{tariff['name']}» — {tariff['duration_days']} дней"

        # Вызов API провайдера
        create_kwargs = {
            'amount_rub': price_rub,
            'order_id': order_id,
            'description': description,
            'bot_name': bot_name,
        }
        try:
            import inspect
            signature = inspect.signature(create_func)
            accepts_kwargs = any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in signature.parameters.values()
            )
            if accepts_kwargs or 'user_telegram_id' in signature.parameters:
                create_kwargs['user_telegram_id'] = callback.from_user.id
        except (TypeError, ValueError):
            pass
        result = await create_func(**create_kwargs)
        save_func(order_id, result[result_key])

        qr_image_data = result.get('qr_image_data')
        qr_url = result.get('qr_url', '')

        if not qr_image_data or not qr_url:
            await show_payment_status_message(
                callback.message,
                title_html=f'❌ <b>{escape_html(error_name)} не вернул данные для оплаты</b>',
                body_text='Попробуйте позже.',
                payment_provider_title=error_name,
                reply_markup=home_only_kb()
            )
            return

        # Формирование текста
        promo_lines = describe_quote_lines(quote)
        payment_context = build_qr_payment_page_context(
            title=title,
            tariff_name=escape_html(tariff['name']),
            price_str=format_amount(quote['final_amount'], payment_type),
            days=tariff['duration_days'],
            qr_url=qr_url,
            key_name=escape_html(key['display_name']) if key else None,
            hint_text=hint_text,
            instruction_text=instruction_text,
            promo_lines=promo_lines,
        )
        payment_context.setdefault('bot_username', bot_name)
        payment_context = build_page_flow_context(callback, **payment_context)
        text = _render_qr_payment_page_text(payment_context)

        # Отправка QR-фото
        from aiogram.types import BufferedInputFile
        photo = BufferedInputFile(qr_image_data, filename=qr_filename)
        runtime_markup = qr_payment_kb(order_id, check_prefix, back_callback, qr_url)
        runtime_rows = getattr(runtime_markup, 'inline_keyboard', None)
        reply_markup = build_qr_payment_reply_markup(payment_context, runtime_rows) or runtime_markup
        rendered_message = await safe_edit_or_send(
            callback.message, text, photo=photo,
            reply_markup=reply_markup,
            force_new=True
        )
        remember_qr_payment_page_context(
            callback.from_user.id,
            rendered_message,
            payment_context,
            reply_markup,
            append_buttons=runtime_rows,
        )
    except (ValueError, RuntimeError) as e:
        logger.error(f'Ошибка создания {error_name}-счёта: {e}')
        await show_payment_status_message(
            callback.message,
            title_html='❌ <b>Ошибка создания платежа</b>',
            body_html=(
                f'<i>{escape_html(str(e))}</i>\n\n'
                'Попробуйте другой способ оплаты.'
            ),
            payment_provider_title=error_name,
            reply_markup=home_only_kb()
        )

    await callback.answer()


async def check_qr_payment_flow(
    message,
    state: FSMContext,
    order_id: str,
    telegram_id: int,
    payment_type: str,
    payment_id_field: str,
    check_func,
    check_arg_is_order_id: bool = False,
    rate_limit_seconds: int = 0,
    rate_limit_prefix: str = '',
    pending_hint: str = None,
    callback: CallbackQuery = None,
    referral_override_func=None,
) -> None:
    """
    Универсальный flow проверки статуса QR-платежа.

    Выполняет: поиск ордера → проверку владельца → проверку «уже оплачено» →
    валидацию payment_id → rate-limiting → вызов API проверки → обработку результата.

    Args:
        message: Объект сообщения (callback.message или Message из deep-link)
        state: FSM-контекст
        order_id: ID ордера
        telegram_id: Telegram ID пользователя
        payment_type: Тип платежа ('wata', 'platega', 'cardlink', 'yookassa_qr')
        payment_id_field: Имя поля ID платежа в ордере ('wata_link_id', 'cardlink_bill_id', ...)
        check_func: Async-функция проверки статуса (payment_id) -> str ('succeeded'/'canceled'/...)
        check_arg_is_order_id: True если check_func принимает order_id вместо payment_id (WATA)
        rate_limit_seconds: Интервал rate-limit (0 — без ограничения)
        rate_limit_prefix: Префикс ключа rate-limit в FSM ('wata', 'platega', ...)
        pending_hint: Дополнительная подсказка в статусе «ожидание» (None → стандарт)
        callback: CallbackQuery (None при deep-link вызове)
        referral_override_func: Функция(order, state) -> int для нестандартного
                                расчёта реферального вознаграждения (yookassa с балансом)
    """
    import time
    from database.requests import (
        find_order_by_order_id, get_user_internal_id,
        is_order_already_paid, update_payment_type
    )
    from bot.handlers.user.payments.status_page import show_payment_status_message
    from bot.services.billing import complete_payment_flow
    from bot.keyboards.admin import home_only_kb

    async def _show_order_not_found() -> None:
        if callback:
            await callback.answer('❌ Ордер не найден', show_alert=True)
        else:
            await show_payment_status_message(
                message,
                title_html='❌ <b>Ордер не найден</b>',
                body_text='Откройте оплату заново из бота и попробуйте ещё раз.',
                reply_markup=home_only_kb(),
                send_func=safe_edit_or_send,
            )

    # 1. Поиск ордера
    order = find_order_by_order_id(order_id)
    if not order:
        await _show_order_not_found()
        return

    # 2. Проверка владельца ордера
    owner_user_id = get_user_internal_id(telegram_id)
    if not owner_user_id or int(order.get('user_id') or 0) != int(owner_user_id):
        logger.warning(
            'Попытка проверить чужой QR-платёж: order=%s, telegram_id=%s, owner=%s',
            order_id, telegram_id, order.get('user_id')
        )
        await _show_order_not_found()
        return

    # 3. Уже оплачено?
    if order.get('status') == 'paid' or is_order_already_paid(order_id):
        await finalize_payment_ui(
            message, state,
            '✅ Оплата уже была обработана ранее.',
            order, user_id=telegram_id
        )
        if callback:
            await callback.answer()
        return

    # 4. Валидация payment_id
    payment_id = order.get(payment_id_field)
    if not payment_id:
        if callback:
            await callback.answer('⚠️ Нет данных о платеже. Попробуйте чуть позже.', show_alert=True)
        else:
            await show_payment_status_message(
                message,
                title_html='⚠️ <b>Нет данных о платеже</b>',
                body_text='Попробуйте чуть позже или откройте оплату заново.',
                reply_markup=home_only_kb(),
                send_func=safe_edit_or_send,
            )
        return

    # 5. Rate-limiting
    if rate_limit_seconds > 0:
        state_data = await state.get_data()
        last_check_key = f'{rate_limit_prefix}_last_check_{order_id}'
        last_check = state_data.get(last_check_key, 0)
        now = time.time()
        elapsed = now - last_check
        if last_check and elapsed < rate_limit_seconds:
            wait = int(rate_limit_seconds - elapsed)
            if callback:
                await callback.answer(
                    f'⏳ Подождите {wait} сек. перед повторной проверкой.',
                    show_alert=True
                )
            return
        await state.update_data({last_check_key: now})

    # 6. Уведомление о проверке
    if callback:
        await callback.answer('🔍 Проверяем платёж...')

    # 7. Вызов API проверки
    try:
        check_arg = order_id if check_arg_is_order_id else payment_id
        status = await check_func(check_arg)
    except Exception as e:
        logger.error(f'Ошибка проверки статуса {payment_type} {order_id}: {e}')
        await show_payment_status_message(
            message,
            title_html='❌ <b>Не удалось проверить статус платежа</b>',
            body_text='Попробуйте позже.',
            reply_markup=home_only_kb(),
            force_new=True,
            send_func=safe_edit_or_send,
        )
        return

    # 8. Обработка результата
    if status == 'succeeded':
        update_payment_type(order_id, payment_type)

        # Реферальное вознаграждение
        if referral_override_func:
            referral_amount = await referral_override_func(order, state)
        else:
            if order.get('final_amount_cents') is not None:
                referral_amount = int(order.get('final_amount_cents') or 0)
            else:
                from database.requests import get_tariff_by_id
                _tariff = get_tariff_by_id(order.get('tariff_id'))
                referral_amount = int((_tariff.get('price_rub', 0) or 0) * 100) if _tariff else 0

        logger.info(f"{payment_type} referral: order={order_id}, referral_amount={referral_amount}")

        # Удаляем QR-фото
        try:
            await message.delete()
        except Exception:
            pass

        await complete_payment_flow(
            order_id=order_id,
            message=message,
            state=state,
            telegram_id=telegram_id,
            payment_type=payment_type,
            referral_amount=referral_amount
        )
    elif status == 'canceled':
        await show_payment_status_message(
            message,
            title_html='❌ <b>Платёж отменён</b>',
            body_text='Похоже, платёж был отменён.\nПопробуйте снова выбрать тариф.',
            reply_markup=home_only_kb(),
            force_new=True,
            send_func=safe_edit_or_send,
        )
    else:
        pending_body = 'Оплатите по ссылке и нажмите «✅ Я оплатил» снова.'
        if pending_hint:
            pending_body += f'\n\n<i>{escape_html(pending_hint)}</i>'
        await show_payment_status_message(
            message,
            title_html='⏳ <b>Платёж ещё не поступил</b>',
            body_html=pending_body,
            force_new=True,
            send_func=safe_edit_or_send,
        )
