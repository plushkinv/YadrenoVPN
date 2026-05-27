"""Сборка HTML-блоков для редактируемых страниц ключей."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from bot.utils.text import escape_html


KEY_INFO_PLACEHOLDER = '%информацияключа%'
KEY_HISTORY_PLACEHOLDER = '%историяопераций%'
SCREEN_DATA_PLACEHOLDER = '%данныеэкрана%'
REPLACE_DATA_PLACEHOLDER = '%данныезамены%'
KEY_DATA_PLACEHOLDER = '%данныеключа%'


def _safe(value: Any, fallback: str = '—') -> str:
    """Экранирует динамическое значение для HTML."""
    if value is None or value == '':
        return escape_html(fallback)
    return escape_html(str(value))


def keyboard_rows(markup) -> list:
    """Возвращает ряды кнопок из готовой InlineKeyboardMarkup."""
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
    """Готовит плейсхолдеры карточки ключа."""
    info_lines: list[str] = []
    if prepend_html:
        info_lines.extend([prepend_html, ''])

    server = key.get('server_name') or 'Не выбран'
    expires = key.get('expires_at')[:10] if key.get('expires_at') else '—'
    info_lines.extend([
        f"🔑 <b>{_safe(key.get('display_name'), 'VPN-ключ')}</b>",
        '',
        f"<b>Статус:</b> {_safe(status)}",
        f"<b>Сервер:</b> {_safe(server)}",
        f"<b>Протокол:</b> {_safe(inbound_name)} ({_safe(protocol)})",
        f"<b>Трафик:</b> {_safe(traffic_info)}",
        f"<b>Действует до:</b> {_safe(expires)}",
    ])

    return {
        KEY_INFO_PLACEHOLDER: '\n'.join(info_lines),
        KEY_HISTORY_PLACEHOLDER: build_key_history_block(payments),
    }


def build_key_history_block(payments: Iterable[Mapping[str, Any]]) -> str:
    """Собирает блок истории операций ключа."""
    payment_rows = list(payments or [])
    if not payment_rows:
        return ''

    lines = ['', '📜 <b>История операций:</b>']
    for payment in payment_rows:
        date = payment.get('paid_at')[:10] if payment.get('paid_at') else '—'
        tariff = payment.get('tariff_name') or 'Тариф'
        if payment.get('payment_type') == 'stars':
            amount = f"{_safe(payment.get('amount_stars') or 0)} ⭐"
        else:
            amount_val = (payment.get('amount_cents') or 0) / 100
            amount_str = f'{amount_val:g}'.replace('.', ',')
            amount = f'${_safe(amount_str)}'
        lines.append(f"   • {_safe(date)}: {_safe(tariff)} ({amount})")
    return '\n'.join(lines)


def build_replace_server_select_data() -> str:
    """Описание стартового экрана замены ключа."""
    return (
        "Вы можете пересоздать ключ на другом или том же сервере.\n"
        "Старый ключ будет удалён, но срок действия сохранится."
    )


def build_server_screen_data(server: Mapping[str, Any]) -> str:
    """Готовит блок с выбранным сервером."""
    return f"<b>Сервер:</b> {_safe(server.get('name'), 'Не выбран')}"


def build_replace_confirm_data(
    key: Mapping[str, Any],
    server: Mapping[str, Any],
    *,
    subscription_mode: bool,
) -> str:
    """Готовит блок подтверждения замены ключа."""
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
    """Готовит блок текущего имени ключа для переименования."""
    return f"Текущее имя: <b>{_safe(key.get('display_name'), 'VPN-ключ')}</b>"


def build_new_key_server_select_data() -> str:
    """Описание выбора сервера после оплаты."""
    return "🔑 Теперь выберите сервер для вашего нового ключа."


def build_new_key_server_back_data() -> str:
    """Описание выбора сервера при возврате со следующего шага."""
    return "🔑 Выберите сервер для вашего нового ключа."
