"""Compatibility adapters and recipient preparation for event message templates."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from bot.utils.placeholders import (
    CANONICAL_EVENT_PLACEHOLDERS,
    EVENT_TYPES,
    EventType,
    apply_page_placeholders,
    normalize_event_placeholder_context,
)


EventPlaceholderMode = Literal['html', 'plain', 'url']


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
    'build_user_event_context',
    'render_event_placeholders',
    'render_event_message_text',
]
