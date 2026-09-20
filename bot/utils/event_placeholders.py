"""Compatibility adapters and recipient preparation for event message templates."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import tzinfo
from typing import Any, Literal

from bot.utils.placeholders import (
    CANONICAL_EVENT_PLACEHOLDERS,
    EVENT_TYPES,
    EventType,
    apply_page_placeholders,
    normalize_event_placeholder_context,
)


EventPlaceholderMode = Literal['html', 'plain', 'url']


def build_key_event_context(key: Mapping[str, Any], *, display_tz: tzinfo | None = None) -> dict[str, Any]:
    """Build the complete safe key display context and released event values."""
    from bot.utils.key_pages import build_key_page_context
    from bot.utils.placeholders import KEY_FIELDS_CONTEXT_KEY
    from database.requests import is_key_active

    key_id = key.get('id', key.get('vpn_key_id'))
    display_key = {
        **key,
        'id': key_id,
        'display_name': key.get('custom_name') or f'#{key_id}',
        'is_active': is_key_active(key),
    }
    context: dict[str, Any] = build_key_page_context(display_key, display_tz=display_tz)
    fields = context[KEY_FIELDS_CONTEXT_KEY]
    context['key_name'] = fields['name']
    if 'days_left' in key:
        context['key_days_left'] = fields['days_left']
    return context


def build_user_event_context(telegram_id: int | None) -> dict[str, Any]:
    """Preserve the released user-context helper through the shared data builder."""
    from bot.utils.page_dynamic_data import build_user_context_values

    return build_user_context_values(telegram_id)


def render_event_placeholders(
    text: str | None,
    event_type: str,
    context: Mapping[str, Any] | None = None,
    *,
    mode: EventPlaceholderMode = 'html',
) -> str:
    """Render supplied event values using the page engine and legacy URL mode."""
    return apply_page_placeholders(
        text,
        context=context,
        mode='url_component' if mode == 'url' else mode,
        event_type=event_type,
    )


async def render_event_message_text(
    text: str | None,
    event_type: str,
    *,
    bot: Any,
    telegram_id: int | None,
    context: Mapping[str, Any] | None = None,
) -> str:
    """Enrich only requested data for the explicit recipient, then render once."""
    from bot.utils.page_placeholder_context import enrich_page_placeholder_context

    runtime_context = normalize_event_placeholder_context(event_type, context)
    # Event snapshots must not override general data owned by the recipient.
    for key in (
        'user_display_name', 'user_name', 'user_username', 'username',
        'user_registered_at', 'user_balance_text', 'balance_text',
        'referral_link', 'referral_stats_html', 'user_profile_html',
        'keys_summary_html', 'keys_total_count', 'keys_active_count',
        'keys_expired_count', 'keys_list_html',
    ):
        runtime_context.pop(key, None)
    runtime_context['telegram_id'] = telegram_id
    runtime_context['bot_username'] = (
        getattr(bot, 'my_username', None) or getattr(bot, 'username', None) or ''
    )
    runtime_context = await enrich_page_placeholder_context(
        '', {'text': text or '', 'buttons': []}, runtime_context,
    )
    return render_event_placeholders(text, event_type, runtime_context)


__all__ = [
    'CANONICAL_EVENT_PLACEHOLDERS',
    'EVENT_TYPES',
    'EventType',
    'build_key_event_context',
    'build_user_event_context',
    'render_event_placeholders',
    'render_event_message_text',
]
