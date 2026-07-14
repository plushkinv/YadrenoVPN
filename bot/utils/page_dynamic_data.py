"""Collection of dynamic data for placeholders of user pages."""
from __future__ import annotations

import logging
from typing import Any

from aiogram.types import InlineKeyboardButton

from bot.utils.datetime_format import format_date_for_display
from bot.utils.text import escape_html

logger = logging.getLogger(__name__)


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _required_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} должен быть int")
    return value


def format_price_compact(cents: int) -> str:
    """Formats kopecks into a compact ruble string."""
    if cents >= 10000:
        return f"{cents // 100} ₽"
    return f"{cents / 100:.2f} ₽".replace(".", ",")


def build_tariff_text(*, group_id: int | None = None, include_title: bool = True) -> str:
    """Generates an HTML block for the tariff list placeholder."""
    from database.requests import (
        get_all_tariffs,
        get_tariffs_by_group,
        is_cardlink_configured,
        is_cards_enabled,
        is_crypto_configured,
        is_demo_payment_enabled,
        is_platega_configured,
        is_stars_enabled,
        is_wata_configured,
        is_yookassa_qr_configured,
    )

    crypto_enabled = is_crypto_configured()
    stars_enabled = is_stars_enabled()
    cards_enabled = is_cards_enabled()
    yookassa_qr_enabled = is_yookassa_qr_configured()
    wata_enabled = is_wata_configured()
    platega_enabled = is_platega_configured()
    cardlink_enabled = is_cardlink_configured()
    demo_enabled = is_demo_payment_enabled()

    group_id = _optional_int(group_id)
    if group_id is not None:
        if group_id <= 0:
            return ''
        tariffs = get_tariffs_by_group(group_id)
    else:
        tariffs = get_all_tariffs()
    if not tariffs:
        return ''

    lines = ['📋 <b>Тарифы:</b>'] if include_title else []
    for tariff in tariffs:
        prices = []
        if crypto_enabled:
            price_usd = tariff['price_cents'] / 100
            price_str = f'{price_usd:g}'.replace('.', ',')
            prices.append(f'${escape_html(price_str)}')
        if stars_enabled:
            prices.append(f"{tariff['price_stars']} ⭐")
        if (
            cards_enabled
            or yookassa_qr_enabled
            or wata_enabled
            or platega_enabled
            or cardlink_enabled
            or demo_enabled
        ) and tariff.get('price_rub', 0) > 0:
            prices.append(f"{int(tariff['price_rub'])} ₽")
        price_display = ' / '.join(prices) if prices else 'Цена не установлена'
        lines.append(f"• {escape_html(tariff['name'])} — {price_display}")

    return '\n'.join(lines)


def build_referral_stats_text(user_internal_id: int) -> str:
    """Generates an HTML block for the referral statistics placeholder."""
    from database.requests import (
        get_referral_levels,
        get_referral_reward_type,
        get_referral_stats,
        get_user_balance,
    )

    reward_type = get_referral_reward_type()
    levels = get_referral_levels()
    stats = get_referral_stats(user_internal_id)
    balance = get_user_balance(user_internal_id)

    stats_by_level = {s['level']: s for s in stats} if stats else {}

    lines = ["📊 <b>Ваша статистика:</b>", ""]
    visible_levels = [
        level for level in levels
        if bool(level.get('enabled')) and level.get('level_number') in (1, 2, 3)
    ]

    if not visible_levels:
        lines.append("Пока нет активных уровней реферальной программы.")
    for level in visible_levels:
        level_num = level['level_number']
        percent = level['percent']
        level_stat = stats_by_level.get(level_num)
        count = level_stat['count'] if level_stat else 0

        if reward_type == 'days':
            total_reward = level_stat['total_reward_days'] if level_stat else 0
            reward_display = escape_html(f"{total_reward} дн.")
        else:
            total_reward = level_stat['total_reward_cents'] if level_stat else 0
            reward_display = escape_html(format_price_compact(total_reward))

        lines.append(
            f"✅ Уровень {escape_html(str(level_num))} "
            f"({escape_html(str(percent))}%): "
            f"{escape_html(str(count))} чел. — {reward_display}"
        )
    lines.append("")

    if reward_type == 'balance':
        lines.append("━━━━━━━━━━━━━━━")
        lines.append(f"💰 <b>Ваш баланс:</b> {escape_html(format_price_compact(balance))}")
        lines.append("")

    return "\n".join(lines)


def build_referral_context_values(
    telegram_id: int | None,
    bot_username: str | None,
) -> dict[str, str]:
    """Returns the context values of the page's referral placeholders."""
    telegram_id = _optional_int(telegram_id)
    bot_username = bot_username if isinstance(bot_username, str) else ''
    if not telegram_id or not bot_username:
        return {}

    from database.requests import (
        ensure_user_referral_code,
        get_user_internal_id,
        is_referral_enabled,
    )

    if not is_referral_enabled():
        return {}

    user_internal_id = get_user_internal_id(telegram_id)
    if not user_internal_id:
        return {}

    referral_code = ensure_user_referral_code(user_internal_id)
    from bot.utils.telegram_links import build_telegram_link

    return {
        'referral_link': build_telegram_link(bot_username, f"ref_{referral_code}"),
        'referral_stats_html': build_referral_stats_text(user_internal_id),
    }


def build_support_context_values(*, thread_id: int | None = None) -> dict[str, str]:
    """Returns the context values of the underlying native support block."""
    thread_id = _optional_int(thread_id)
    title = "Ответ в поддержку" if thread_id else "Поддержка"
    return {
        "support_title_html": f"💬 <b>{escape_html(title)}</b>",
        "support_instruction_html": (
            "Отправьте сообщение для администратора.\n\n"
            "Можно отправить текст, фото, видео или GIF."
        ),
    }


def _format_username(username: Any) -> str:
    if not username:
        return ''
    value = str(username).strip()
    if not value:
        return ''
    return value if value.startswith('@') else f'@{value}'


def _format_user_display_name(user: dict[str, Any]) -> str:
    parts = [
        str(user.get('first_name') or '').strip(),
        str(user.get('last_name') or '').strip(),
    ]
    full_name = ' '.join(part for part in parts if part)
    if full_name:
        return full_name
    username = _format_username(user.get('username'))
    if username:
        return username
    return f"ID {user.get('telegram_id')}"


def _count_active_keys(keys: list[dict[str, Any]]) -> int:
    return sum(1 for key in keys if bool(key.get('is_active')))


def build_user_profile_context_values(telegram_id: int | None) -> dict[str, Any]:
    """Returns context values of profile widgets placeholders."""
    telegram_id = _optional_int(telegram_id)
    if not telegram_id:
        return {}

    from database.requests import get_user_balance, get_user_by_telegram_id, get_user_keys_for_display

    user = get_user_by_telegram_id(telegram_id)
    if not user:
        return {}

    keys = get_user_keys_for_display(telegram_id)
    total_keys = len(keys)
    active_keys = _count_active_keys(keys)
    expired_keys = max(total_keys - active_keys, 0)
    balance_cents = get_user_balance(int(user['id']))
    balance_text = format_price_compact(balance_cents)
    username = _format_username(user.get('username'))
    display_name = _format_user_display_name(user)
    created_at = format_date_for_display(user.get('created_at'))

    profile_lines = [
        "👤 <b>Профиль</b>",
        f"Имя: <b>{escape_html(display_name)}</b>",
        f"Telegram ID: <code>{escape_html(str(user.get('telegram_id') or telegram_id))}</code>",
    ]
    if username:
        profile_lines.append(f"Username: {escape_html(username)}")
    profile_lines.extend([
        f"Дата регистрации: {escape_html(created_at)}",
        f"Баланс: <b>{escape_html(balance_text)}</b>",
    ])

    if total_keys:
        keys_summary_html = (
            "🔑 <b>Ключи</b>\n"
            f"Всего: <b>{escape_html(str(total_keys))}</b>\n"
            f"Активных: <b>{escape_html(str(active_keys))}</b>\n"
            f"Истёкших: <b>{escape_html(str(expired_keys))}</b>"
        )
    else:
        keys_summary_html = "🔑 <b>Ключи</b>\nПока нет ключей."

    return {
        'user_profile_html': '\n'.join(profile_lines),
        'user_display_name': display_name,
        'user_username': username,
        'user_registered_at': created_at,
        'user_balance_text': balance_text,
        'keys_summary_html': keys_summary_html,
        'keys_total_count': total_keys,
        'keys_active_count': active_keys,
        'keys_expired_count': expired_keys,
    }


async def build_my_keys_render_data(telegram_id: int):
    """Prepares a list of keys and dynamic buttons for the `my_keys` page."""
    telegram_id = _required_int(telegram_id, 'telegram_id')
    from database.requests import get_setting, get_user_keys_for_display, is_traffic_exhausted
    from bot.services.vpn_api import format_traffic, get_client
    from bot.utils.my_keys_page import (
        DEFAULT_MY_KEYS_ITEM_TEMPLATE,
        MY_KEYS_ITEM_TEMPLATE_SETTING,
        build_my_keys_item_text,
        build_my_keys_list_text,
    )

    keys = get_user_keys_for_display(telegram_id)
    item_template = get_setting(
        MY_KEYS_ITEM_TEMPLATE_SETTING,
        DEFAULT_MY_KEYS_ITEM_TEMPLATE,
    )
    if item_template is None:
        item_template = DEFAULT_MY_KEYS_ITEM_TEMPLATE
    items = []
    key_buttons = []

    for key in keys:
        traffic_exhausted = is_traffic_exhausted(key)
        if key['is_active'] and not traffic_exhausted:
            status_emoji = '🟢'
        else:
            status_emoji = '🔴'

        traffic_used = key.get('traffic_used', 0) or 0
        traffic_limit = key.get('traffic_limit', 0) or 0
        used_str = format_traffic(traffic_used)
        limit_str = format_traffic(traffic_limit) if traffic_limit > 0 else '∞'
        traffic_text = f'{used_str} / {limit_str}'

        protocol = 'VLESS'
        inbound_name = 'VPN'
        if key.get('sub_id'):
            protocol = 'SUBSCRIPTION'
            inbound_name = 'Все протоколы'
        elif key.get('server_id') and key.get('panel_email'):
            try:
                client = await get_client(key['server_id'])
                stats = await client.get_client_stats(key['panel_email'])
                if stats:
                    protocol = stats['protocol'].upper()
                    inbound_name = stats.get('remark', 'VPN') or 'VPN'
            except Exception as e:
                logger.warning("Не удалось получить протокол для ключа %s: %s", key['id'], e)

        items.append(
            build_my_keys_item_text(
                key,
                template=item_template,
                status=status_emoji,
                traffic_text=traffic_text,
                inbound_name=inbound_name,
                protocol=protocol,
            )
        )
        key_buttons.append([
            InlineKeyboardButton(
                text=f"{status_emoji} {key['display_name']}",
                callback_data=f"key:{key['id']}",
            )
        ])

    return keys, build_my_keys_list_text(items), key_buttons


async def build_my_keys_context_values(telegram_id: int | None) -> dict[str, Any]:
    """Returns context values of a list of keys without runtime buttons."""
    telegram_id = _optional_int(telegram_id)
    if not telegram_id:
        return {}
    _, keys_list_html, _ = await build_my_keys_render_data(telegram_id)
    return {'keys_list_html': keys_list_html}
