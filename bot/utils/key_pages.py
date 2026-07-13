"""Assembling HTML blocks for editable key pages."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from bot.utils.datetime_format import format_date_for_display
from bot.utils.text import escape_html


KEY_INFO_PLACEHOLDER = '%ключ_информация%'
KEY_HISTORY_PLACEHOLDER = '%ключ_история_операций%'
SCREEN_DATA_PLACEHOLDER = '%экран_данные%'
REPLACE_DATA_PLACEHOLDER = '%замена_ключа_данные%'
KEY_DATA_PLACEHOLDER = '%ключ_переименование_данные%'


def _safe(value: Any, fallback: str = '—') -> str:
    """Escapes a dynamic value for HTML."""
    if value is None or value == '':
        return escape_html(fallback)
    return escape_html(str(value))


def keyboard_rows(markup) -> list:
    """Returns rows of buttons from a finished InlineKeyboardMarkup."""
    if not markup:
        return []
    return list(getattr(markup, 'inline_keyboard', []) or [])


def build_key_details_replacements(
    key: Mapping[str, Any],
    payments: Iterable[Mapping[str, Any]],
    *,
    status: str,
    traffic_info: str,
    inbound_name: str,
    protocol: str,
    prepend_html: str = '',
) -> dict[str, str]:
    """Prepares key card placeholders."""
    info_lines: list[str] = []
    if prepend_html:
        info_lines.extend([prepend_html, ''])

    server = key.get('server_name') or 'Не выбран'
    expires = format_date_for_display(key.get('expires_at'))
    info_lines.extend([
        f"🔑 <b>{_safe(key.get('display_name'), 'VPN-ключ')}</b>",
        '',
        f"<b>Статус:</b> {_safe(status)}",
        f"<b>Сервер:</b> {_safe(server)}",
        f"<b>Протокол:</b> {_safe(inbound_name)} ({_safe(protocol)})",
        f"<b>Трафик:</b> {_safe(traffic_info)}",
        f"<b>Действует до:</b> {_safe(expires)}",
    ])

    key_info = '\n'.join(info_lines)
    key_history = build_key_history_block(payments)
    return {
        '%key_info%': key_info,
        '%key_history%': key_history,
        KEY_INFO_PLACEHOLDER: key_info,
        KEY_HISTORY_PLACEHOLDER: key_history,
    }


def build_key_history_block(payments: Iterable[Mapping[str, Any]]) -> str:
    """Collects a block of the key's operation history."""
    payment_rows = list(payments or [])
    if not payment_rows:
        return ''

    lines = ['', '📜 <b>История операций:</b>']
    for payment in payment_rows:
        date = format_date_for_display(payment.get('paid_at'))
        if payment.get('history_type') == 'key_operation':
            delta_days = int(payment.get('delta_days') or 0)
            reason = payment.get('reason') or 'Начисление дней'
            if delta_days > 0:
                lines.append(f"   • {_safe(date)}: {_safe(reason)} (+{_safe(delta_days)} дн.)")
            else:
                lines.append(f"   • {_safe(date)}: {_safe(reason)}")
            continue
        tariff = payment.get('tariff_name') or 'Тариф'
        ptype = payment.get('payment_type')
        if ptype == 'stars':
            stars = payment.get('final_amount_stars') if payment.get('final_amount_stars') is not None else payment.get('amount_stars') or 0
            amount = f"{_safe(stars)} ⭐"
        elif ptype == 'crypto':
            cents = payment.get('final_amount_cents') if payment.get('final_amount_cents') is not None else payment.get('amount_cents') or 0
            amount_val = cents / 100
            amount_str = f'{amount_val:g}'.replace('.', ',')
            amount = f'${_safe(amount_str)}'
        elif ptype in ('cards', 'yookassa_qr', 'wata', 'platega', 'cardlink', 'balance', 'promo_free'):
            rub = ((payment.get('final_amount_cents') or 0) / 100) if payment.get('final_amount_cents') is not None else payment.get('price_rub') or 0
            rub_str = f'{rub:g}'.replace('.', ',')
            amount = f'{_safe(rub_str)} ₽'
        else:
            amount = '?'
        promo = f", 🎟 {_safe(payment.get('promo_code'))}" if payment.get('promo_code') else ""
        lines.append(f"   • {_safe(date)}: {_safe(tariff)} ({amount}{promo})")
    return '\n'.join(lines)


def build_replace_server_select_data() -> str:
    """Description of the key replacement start screen."""
    return (
        "Вы можете пересоздать ключ на другом или том же сервере.\n"
        "Старый ключ будет удалён, но срок действия сохранится."
    )


def build_server_screen_data(server: Mapping[str, Any]) -> str:
    """Prepares a block with the selected server."""
    return f"<b>Сервер:</b> {_safe(server.get('name'), 'Не выбран')}"


def build_replace_confirm_data(
    key: Mapping[str, Any],
    server: Mapping[str, Any],
    *,
    subscription_mode: bool,
) -> str:
    """Prepares a key replacement confirmation block."""
    lines = [
        f"Ключ: <b>{_safe(key.get('display_name'), 'VPN-ключ')}</b>",
        f"Новый сервер: <b>{_safe(server.get('name'), 'Не выбран')}</b>",
        '',
    ]
    if subscription_mode:
        lines.extend([
            "Подписка будет пересоздана на новом сервере (со всеми протоколами).",
            "Старая ссылка перестанет работать — нужно будет обновить её в приложении.",
        ])
    else:
        lines.extend([
            "Старый ключ будет удалён и перестанет работать.",
            "Вам нужно будет обновить настройки в приложении.",
        ])
    return '\n'.join(lines)


def build_key_rename_data(key: Mapping[str, Any]) -> str:
    """Prepares the current key name block for renaming."""
    return f"Текущее имя: <b>{_safe(key.get('display_name'), 'VPN-ключ')}</b>"


def build_new_key_server_select_data() -> str:
    """Description of server selection after payment."""
    return "🔑 Теперь выберите сервер для вашего нового ключа."


def build_new_key_server_back_data() -> str:
    """Description of server selection when returning from the next step."""
    return "🔑 Выберите сервер для вашего нового ключа."
