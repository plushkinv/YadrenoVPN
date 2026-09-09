"""Collection of dynamic data for placeholders of user pages."""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from bot.utils.datetime_format import format_date_for_display
from bot.utils.tariff_prices import (
    format_tariff_price_display,
    load_tariff_price_display_config,
)
from bot.utils.text import escape_html
from bot.utils.user_ui_texts import render_ui_text

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
    """Formats current base minor units compactly."""
    from bot.services.money import format_money_minor

    return format_money_minor(cents)


def _format_effective_referral_percent(
    level_percent: Any,
    coefficient: Any,
) -> str:
    """Formats the user's effective core referral rate without trailing zeroes."""
    try:
        value = Decimal(str(level_percent)) * Decimal(str(coefficient))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("Invalid referral percentage or coefficient") from error
    if not value.is_finite():
        raise ValueError("Referral percentage and coefficient must be finite")
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def build_tariff_text(*, group_id: int | None = None, include_title: bool = False) -> str:
    """Generates an HTML block for the tariff list placeholder."""
    from database.requests import (
        get_all_tariffs,
        get_tariffs_by_group,
    )

    group_id = _optional_int(group_id)
    if group_id is not None:
        if group_id <= 0:
            return ''
        tariffs = get_tariffs_by_group(group_id)
    else:
        tariffs = get_all_tariffs()
    if not tariffs:
        return ''

    price_config = load_tariff_price_display_config()
    lines: list[str] = []
    for tariff in tariffs:
        price_display = format_tariff_price_display(
            tariff,
            config=price_config,
        )
        lines.append(f"• {escape_html(tariff['name'])} — {price_display}")

    return '\n'.join(lines)


def build_referral_stats_text(user_internal_id: int) -> str:
    """Generates referral statistics with the user's effective core rates."""
    from database.requests import (
        get_referral_levels,
        get_referral_reward_type,
        get_referral_stats,
        get_user_balance,
        get_user_referral_coefficient,
    )

    reward_type = get_referral_reward_type()
    levels = get_referral_levels()
    stats = get_referral_stats(user_internal_id)
    balance = get_user_balance(user_internal_id)
    coefficient = get_user_referral_coefficient(user_internal_id)

    stats_by_level = {s['level']: s for s in stats} if stats else {}

    lines: list[str] = []
    visible_levels = [
        level for level in levels
        if bool(level.get('enabled')) and level.get('level_number') in (1, 2, 3)
    ]

    if not visible_levels:
        lines.append(render_ui_text("referral.no_levels"))
    for level in visible_levels:
        level_num = level['level_number']
        percent = _format_effective_referral_percent(
            level['percent'],
            coefficient,
        )
        level_stat = stats_by_level.get(level_num)
        count = level_stat['count'] if level_stat else 0

        if reward_type == 'days':
            total_reward = level_stat['total_reward_days'] if level_stat else 0
            reward_display = render_ui_text("format.days_short", days=total_reward)
        else:
            total_reward = (
                level_stat.get('total_reward_minor', level_stat.get('total_reward_cents', 0))
                if level_stat else 0
            )
            reward_currency = level_stat.get('reward_currency') if level_stat else None
            from bot.services.money import format_money_minor

            reward_display = format_money_minor(total_reward, reward_currency)

        lines.append(
            render_ui_text(
                "referral.level_row",
                level=level_num,
                percent=percent,
                referrals_count=count,
                earned=reward_display,
            )
        )

    if reward_type == 'balance':
        if lines:
            lines.append("")
        lines.append(render_ui_text(
            "referral.balance_line",
            balance=format_price_compact(balance),
        ))

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
    """Returns data-only context for the native support input pages."""
    thread_id = _optional_int(thread_id)
    return {"support_thread_id": thread_id} if thread_id else {}


def build_payment_coupon_context_values(
    order_id: str | None,
    telegram_id: int | None,
) -> dict[str, Any]:
    """Returns the stable coupon fragment issued by one completed payment."""
    normalized_order_id = order_id.strip() if isinstance(order_id, str) else ''
    viewer_telegram_id = int(telegram_id or 0)
    if not normalized_order_id or not viewer_telegram_id:
        return {}

    try:
        from database.requests import (
            find_order_by_order_id,
            get_promo_code_by_source,
            get_user_by_id,
        )

        order = find_order_by_order_id(normalized_order_id)
        user_id = int((order or {}).get('user_id') or 0)
        if not user_id:
            return {
                'payment_coupon_html': '',
                'payment_coupon_fields': {},
            }
        owner = get_user_by_id(user_id)
        if int((owner or {}).get('telegram_id') or 0) != viewer_telegram_id:
            return {
                'payment_coupon_html': '',
                'payment_coupon_fields': {},
            }
        coupon = get_promo_code_by_source(
            f'auto_payment:{normalized_order_id}',
            issued_to_user_id=user_id,
        )
        from bot.services.promotions import (
            build_auto_coupon_display_fields,
            format_auto_coupon_fragment,
        )

        return {
            'payment_coupon_html': format_auto_coupon_fragment(coupon),
            'payment_coupon_fields': build_auto_coupon_display_fields(coupon),
        }
    except Exception as error:
        logger.warning(
            "Failed to build automatic coupon context for order %s: %s",
            normalized_order_id,
            error,
        )
        return {
            'payment_coupon_html': '',
            'payment_coupon_fields': {},
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


def build_user_context_values(telegram_id: int | None) -> dict[str, Any]:
    """Return the common recipient identity and balance without loading keys."""
    if isinstance(telegram_id, bool) or not isinstance(telegram_id, int):
        return {}

    from database.requests import get_user_balance, get_user_by_telegram_id

    user = get_user_by_telegram_id(telegram_id)
    if not user:
        return {'telegram_id': telegram_id}

    user_id = int(user.get('id') or 0)
    balance = get_user_balance(user_id) if user_id else 0
    return {
        'telegram_id': telegram_id,
        'user_display_name': _format_user_display_name(user),
        'user_username': _format_username(user.get('username')),
        'user_registered_at': format_date_for_display(user.get('created_at')),
        'user_balance_text': format_price_compact(balance),
    }


def build_user_profile_context_values(telegram_id: int | None) -> dict[str, Any]:
    """Returns context values of profile widgets placeholders."""
    telegram_id = _optional_int(telegram_id)
    if not telegram_id:
        return {}
    identity = build_user_context_values(telegram_id)
    if 'user_display_name' not in identity:
        return {}

    from database.requests import get_user_keys_for_display

    keys = get_user_keys_for_display(telegram_id)
    total_keys = len(keys)
    active_keys = _count_active_keys(keys)
    return {
        **{key: value for key, value in identity.items() if key != 'telegram_id'},
        'keys_total_count': total_keys,
        'keys_active_count': active_keys,
        'keys_expired_count': max(total_keys - active_keys, 0),
    }


async def build_my_keys_render_data(telegram_id: int):
    """Prepares a list of keys and dynamic buttons for the `my_keys` page."""
    telegram_id = _required_int(telegram_id, 'telegram_id')
    from database.requests import get_setting, get_user_keys_for_display, is_traffic_exhausted
    from bot.services.vpn_api import format_traffic
    from bot.utils.my_keys_page import (
        MY_KEYS_ITEM_TEMPLATE_SETTING,
        build_my_keys_item_text,
        build_my_keys_list_text,
    )
    from bot.utils.page_button_items import build_key_button_items

    keys = get_user_keys_for_display(telegram_id)
    item_template = get_setting(MY_KEYS_ITEM_TEMPLATE_SETTING)
    if item_template is None:
        raise RuntimeError(f"Missing required setting: {MY_KEYS_ITEM_TEMPLATE_SETTING}")
    items = []

    for key in keys:
        traffic_exhausted = is_traffic_exhausted(key)
        if traffic_exhausted:
            status_text = render_ui_text('key.status.traffic_exhausted')
        elif key['is_active']:
            status_text = render_ui_text('key.status.active')
        else:
            status_text = render_ui_text('key.status.expired')

        traffic_used = key.get('traffic_used', 0) or 0
        traffic_limit = key.get('traffic_limit', 0) or 0
        used_str = format_traffic(traffic_used)
        if not key.get('server_id'):
            traffic_text = render_ui_text('key.traffic.needs_setup')
        elif traffic_limit > 0:
            limit_str = format_traffic(traffic_limit)
            percent = traffic_used / traffic_limit * 100
            traffic_text = render_ui_text(
                'key.traffic.limited',
                used=used_str,
                limit=limit_str,
                percent=f'{percent:.1f}',
            )
        elif traffic_used > 0:
            traffic_text = render_ui_text('key.traffic.used_unlimited', used=used_str)
        else:
            traffic_text = render_ui_text('key.traffic.unlimited')

        items.append(
            build_my_keys_item_text(
                key,
                template=item_template,
                status=status_text,
                traffic_text=traffic_text,
            )
        )
    return keys, build_my_keys_list_text(items), build_key_button_items(keys)


async def build_my_keys_context_values(telegram_id: int | None) -> dict[str, Any]:
    """Returns context values of a list of keys without runtime buttons."""
    telegram_id = _optional_int(telegram_id)
    if not telegram_id:
        return {}
    _, keys_list_html, _ = await build_my_keys_render_data(telegram_id)
    return {'keys_list_html': keys_list_html}
