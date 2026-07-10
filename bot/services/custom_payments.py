"""Core flow для кастомных платёжных провайдеров расширений."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from bot.utils.payment_provider_registry import (
    create_payment,
    check_payment,
    get_payment_provider,
    handle_payment_webhook,
    is_payment_provider_enabled,
)
from database.requests import (
    create_pending_order,
    find_order_by_order_id,
    find_payment_provider_order_by_external_id,
    get_payment_provider_order,
    get_open_payment_provider_orders,
    save_payment_provider_order,
    update_payment_provider_order_status,
)
from bot.services.promotions import prepare_order_pricing

logger = logging.getLogger(__name__)


async def create_custom_payment_order(
    provider_id: str,
    *,
    user_id: int,
    telegram_id: int,
    tariff: Mapping[str, Any],
    action: str,
    vpn_key_id: int | None = None,
    key: Mapping[str, Any] | None = None,
    bot_username: str | None = None,
) -> dict[str, Any]:
    """Создаёт core order, рассчитывает quote и вызывает create_payment provider-а."""
    provider = get_payment_provider(provider_id)
    if provider is None:
        raise ValueError('payment provider не зарегистрирован')
    if not is_payment_provider_enabled(provider.provider_id, {'user_id': user_id, 'telegram_id': telegram_id}):
        return {'ok': False, 'reason': 'Способ оплаты временно недоступен.'}

    (_, order_id) = create_pending_order(
        user_id=user_id,
        tariff_id=int(tariff['id']),
        payment_type=provider.payment_type,
        vpn_key_id=vpn_key_id,
    )
    quote = prepare_order_pricing(
        order_id=order_id,
        user_id=user_id,
        tariff=dict(tariff),
        payment_type=provider.payment_type,
        action=action,
    )
    if not quote.get('ok'):
        return {'ok': False, 'order_id': order_id, 'quote': quote, 'reason': quote.get('unavailable_reason')}
    if quote.get('is_free'):
        return {'ok': True, 'order_id': order_id, 'quote': quote, 'is_free': True}

    context = {
        'provider_id': provider.provider_id,
        'payment_type': provider.payment_type,
        'order_id': order_id,
        'user_id': user_id,
        'telegram_id': telegram_id,
        'tariff': dict(tariff),
        'action': action,
        'vpn_key_id': vpn_key_id,
        'key': dict(key or {}),
        'amount_cents': int(quote['final_amount']),
        'currency': 'RUB',
        'quote': dict(quote),
        'bot_username': bot_username,
        'description': _payment_description(tariff, action=action, key=key),
    }
    result = await create_payment(provider.provider_id, context)
    save_payment_provider_order(
        order_id=order_id,
        provider_id=provider.provider_id,
        payment_type=provider.payment_type,
        provider_payment_id=result.get('provider_payment_id'),
        payment_url=result.get('payment_url'),
        status=result.get('status') or 'pending',
        metadata=result.get('metadata') or {},
    )
    return {
        'ok': True,
        'order_id': order_id,
        'provider': provider,
        'provider_order': get_payment_provider_order(order_id),
        'payment_url': result['payment_url'],
        'quote': quote,
        'is_free': False,
    }


async def check_custom_payment_order(provider_id: str, order: Mapping[str, Any]) -> dict[str, Any]:
    """Проверяет внешний статус кастомного платежа и обновляет provider-order."""
    provider = get_payment_provider(provider_id)
    if provider is None:
        raise ValueError('payment provider не зарегистрирован')

    provider_order = get_payment_provider_order(str(order.get('order_id') or ''))
    if not provider_order or provider_order.get('provider_id') != provider.provider_id:
        raise ValueError('payment provider order не найден')

    result = await check_payment(
        provider.provider_id,
        {
            'provider_id': provider.provider_id,
            'payment_type': provider.payment_type,
            'order': dict(order),
            'provider_order': dict(provider_order),
            'order_id': order.get('order_id'),
            'provider_payment_id': provider_order.get('provider_payment_id'),
            'payment_url': provider_order.get('payment_url'),
            'amount_cents': order.get('final_amount_cents') if order.get('final_amount_cents') is not None else order.get('amount_cents'),
            'currency': 'RUB',
        },
    )
    update_payment_provider_order_status(
        str(order.get('order_id') or ''),
        result['status'],
        provider_payment_id=result.get('provider_payment_id'),
        payment_url=result.get('payment_url'),
        metadata=result.get('metadata'),
    )
    return result


async def complete_custom_payment_order(
    order_id: str,
    *,
    bot: Any = None,
    notify_user: bool = False,
) -> dict[str, Any]:
    """Завершает custom payment через core billing без UI/FSM."""
    from bot.services.billing import process_payment_order, process_referral_reward

    success, text, order = await process_payment_order(
        order_id,
        bot=bot,
        process_referrals=False,
    )
    result = {
        'ok': success,
        'text': text,
        'order': order,
        'processed_now': bool(order and order.get('_payment_processed_now', True)),
        'referral_processed': False,
        'admin_notified': False,
        'user_notified': False,
    }
    if not success or not order:
        return result

    if not result['processed_now']:
        return result

    user_internal_id = order['user_id']
    days = order.get('period_days') or order.get('duration_days') or 30
    referral_amount = _order_referral_amount(order)

    try:
        await process_referral_reward(
            user_internal_id,
            days,
            referral_amount,
            str(order.get('payment_type') or ''),
            bot=bot,
            order=order,
        )
        result['referral_processed'] = True
    except Exception as e:
        logger.warning("Ошибка реферальной обработки custom payment order=%s: %s", order_id, e)

    if bot is not None:
        try:
            from bot.services.notifications import notify_admins_payment

            await notify_admins_payment(bot, order)
            result['admin_notified'] = True
        except Exception as e:
            logger.warning("Ошибка уведомления администраторов о custom payment order=%s: %s", order_id, e)

    if bot is not None and notify_user:
        result['user_notified'] = await _notify_custom_payment_user(bot, order)

    return result


async def auto_check_custom_payment_orders(
    *,
    bot: Any = None,
    limit: int = 50,
) -> dict[str, int]:
    """Фоново проверяет открытые custom payment orders и закрывает оплаченные через core billing."""
    summary = {
        'queued': 0,
        'checked': 0,
        'pending': 0,
        'completed': 0,
        'canceled': 0,
        'skipped': 0,
        'errors': 0,
    }

    provider_orders = get_open_payment_provider_orders(limit=limit)
    summary['queued'] = len(provider_orders)
    for provider_order in provider_orders:
        order_id = str(provider_order.get('order_id') or '')
        if not order_id:
            summary['skipped'] += 1
            continue

        try:
            order = find_order_by_order_id(order_id)
            if not order or order.get('status') != 'pending':
                summary['skipped'] += 1
                continue

            try:
                provider = get_payment_provider(str(provider_order.get('provider_id') or ''))
            except ValueError:
                provider = None
            if provider is None:
                summary['skipped'] += 1
                continue

            if provider_order.get('status') == 'succeeded':
                completed = await complete_custom_payment_order(order_id, bot=bot, notify_user=True)
                if completed.get('ok'):
                    if completed.get('processed_now'):
                        summary['completed'] += 1
                    else:
                        summary['skipped'] += 1
                else:
                    summary['errors'] += 1
                continue

            if not _is_auto_check_due(provider_order, provider.auto_check_interval_seconds):
                summary['skipped'] += 1
                continue

            summary['checked'] += 1
            check_result = await check_custom_payment_order(provider.provider_id, order)
            status = check_result['status']
            if status == 'succeeded':
                completed = await complete_custom_payment_order(order_id, bot=bot, notify_user=True)
                if completed.get('ok'):
                    if completed.get('processed_now'):
                        summary['completed'] += 1
                    else:
                        summary['skipped'] += 1
                else:
                    summary['errors'] += 1
            elif status == 'canceled':
                summary['canceled'] += 1
            else:
                summary['pending'] += 1
        except Exception as e:
            summary['errors'] += 1
            logger.warning("Ошибка автопроверки custom payment order=%s: %s", order_id, e)
            if order_id and provider_order.get('status') == 'pending':
                try:
                    update_payment_provider_order_status(order_id, 'pending')
                except Exception:
                    pass

    return summary


async def process_custom_payment_webhook(
    provider_id: str,
    request_context: Mapping[str, Any],
    *,
    bot: Any = None,
) -> dict[str, Any]:
    """Обрабатывает webhook custom payment provider-а через декларативный contract."""
    try:
        provider = get_payment_provider(provider_id)
    except ValueError:
        provider = None
    if provider is None:
        return {'ok': False, 'reason': 'provider_not_found', 'http_status': 404}

    try:
        webhook_result = await handle_payment_webhook(provider.provider_id, request_context)
    except ValueError as e:
        return {'ok': False, 'reason': str(e), 'http_status': 400}

    if webhook_result.get('ignored'):
        return {
            'ok': True,
            'ignored': True,
            'reason': webhook_result.get('reason'),
            'status': 'ignored',
        }

    provider_order = _find_provider_order_for_webhook(provider.provider_id, webhook_result)
    if not provider_order:
        return {'ok': False, 'reason': 'provider_order_not_found', 'http_status': 404}
    if provider_order.get('provider_id') != provider.provider_id:
        return {'ok': False, 'reason': 'provider_order_mismatch', 'http_status': 404}

    order_id = str(provider_order.get('order_id') or '')
    order = find_order_by_order_id(order_id)
    if not order:
        return {'ok': False, 'reason': 'order_not_found', 'http_status': 404}

    status = str(webhook_result['status'])
    update_payment_provider_order_status(
        order_id,
        status,
        provider_payment_id=webhook_result.get('provider_payment_id'),
        payment_url=webhook_result.get('payment_url'),
        metadata=webhook_result.get('metadata'),
    )

    response: dict[str, Any] = {
        'ok': True,
        'order_id': order_id,
        'provider_id': provider.provider_id,
        'status': status,
        'completed': False,
        'processed_now': False,
    }
    if status == 'succeeded':
        completed = await complete_custom_payment_order(order_id, bot=bot, notify_user=True)
        response['completed'] = bool(completed.get('ok'))
        response['processed_now'] = bool(completed.get('processed_now'))
    return response


def _payment_description(
    tariff: Mapping[str, Any],
    *,
    action: str,
    key: Mapping[str, Any] | None = None,
) -> str:
    tariff_name = str(tariff.get('name') or 'VPN')
    days = int(tariff.get('duration_days') or 0)
    if action == 'renewal' and key:
        key_name = str(key.get('display_name') or 'VPN-ключ')
        return f"Продление ключа «{key_name}»: «{tariff_name}» ({days} дн.)"
    return f"Покупка «{tariff_name}» — {days} дней"


def _find_provider_order_for_webhook(
    provider_id: str,
    webhook_result: Mapping[str, Any],
) -> dict[str, Any] | None:
    order_id = webhook_result.get('order_id')
    if order_id:
        provider_order = get_payment_provider_order(str(order_id))
        if provider_order:
            return provider_order

    provider_payment_id = webhook_result.get('provider_payment_id')
    if provider_payment_id:
        return find_payment_provider_order_by_external_id(provider_id, str(provider_payment_id))
    return None


def _order_referral_amount(order: Mapping[str, Any]) -> int:
    try:
        if order.get('final_amount_cents') is not None:
            return int(order.get('final_amount_cents') or 0)
        return int(order.get('amount_cents') or 0)
    except (TypeError, ValueError):
        return 0


def _is_auto_check_due(provider_order: Mapping[str, Any], interval_seconds: int | None) -> bool:
    if interval_seconds is None or int(interval_seconds or 0) <= 0:
        return False
    updated_at = _parse_db_timestamp(provider_order.get('updated_at'))
    if updated_at is None:
        return True
    return datetime.utcnow() - updated_at >= timedelta(seconds=int(interval_seconds))


def _parse_db_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        parsed = None
        for candidate in (text, text.replace(' ', 'T')):
            try:
                parsed = datetime.fromisoformat(candidate)
                break
            except ValueError:
                continue
        if parsed is None:
            return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


async def _notify_custom_payment_user(bot: Any, order: Mapping[str, Any]) -> bool:
    try:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        from database.requests import get_user_by_id, mark_user_bot_blocked
        from bot.utils.delivery import is_bot_blocked_error

        user = get_user_by_id(int(order.get('user_id') or 0))
        telegram_id = int((user or {}).get('telegram_id') or 0)
        if not telegram_id:
            return False

        reply_markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='🔑 Мои ключи', callback_data='my_keys')],
            [InlineKeyboardButton(text='🈴 На главную', callback_data='start')],
        ])
        text = (
            "✅ <b>Оплата получена</b>\n\n"
            "Платёж обработан автоматически. Откройте «Мои ключи», чтобы настроить или посмотреть доступ."
        )
        try:
            await bot.send_message(
                telegram_id,
                text,
                parse_mode='HTML',
                reply_markup=reply_markup,
            )
            return True
        except Exception as e:
            if is_bot_blocked_error(e):
                mark_user_bot_blocked(telegram_id)
            logger.warning("Не удалось отправить уведомление о custom payment пользователю %s: %s", telegram_id, e)
            return False
    except Exception as e:
        logger.warning("Ошибка подготовки уведомления о custom payment order=%s: %s", order.get('order_id'), e)
        return False


__all__ = [
    'auto_check_custom_payment_orders',
    'check_custom_payment_order',
    'complete_custom_payment_order',
    'create_custom_payment_order',
    'process_custom_payment_webhook',
]
