"""Secure registry hooks/guards for page builder routes."""
from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

from aiogram.types import InlineKeyboardButton

logger = logging.getLogger(__name__)


@dataclass
class PageGuardResult:
    """The result of checking access to route/page."""

    allowed: bool
    message: str = ''
    show_alert: bool = True


@dataclass
class PageHookResult:
    """Data that the hook adds to render_page()."""

    context: dict[str, Any] = field(default_factory=dict)
    text_replacements: dict[str, Any] = field(default_factory=dict)
    visibility: dict[str, bool] = field(default_factory=dict)
    prepend_buttons: list[list[InlineKeyboardButton]] | None = None
    append_buttons: list[list[InlineKeyboardButton]] | None = None


PageGuard = Callable[
    [Any, Mapping[str, Any]],
    PageGuardResult | bool | Mapping[str, Any] | Awaitable[PageGuardResult | bool | Mapping[str, Any]],
]
PageHook = Callable[[Any, Mapping[str, Any]], PageHookResult | Mapping[str, Any] | Awaitable[PageHookResult | Mapping[str, Any]]]

PAGE_GUARDS: dict[str, PageGuard] = {}
PAGE_HOOKS: dict[str, PageHook] = {}


def register_page_guard(name: str, func: PageGuard, *, replace: bool = False) -> None:
    """Registers an enabled guard by name."""
    normalized = _normalize_registered_name(name)
    _require_bool(replace, 'replace')
    if not callable(func):
        raise ValueError('page guard должен быть callable')
    if normalized in PAGE_GUARDS and not replace:
        raise ValueError(f"page guard '{normalized}' уже зарегистрирован")
    PAGE_GUARDS[normalized] = func


def register_page_hook(name: str, func: PageHook, *, replace: bool = False) -> None:
    """Registers an allowed before-render hook by name."""
    normalized = _normalize_registered_name(name)
    _require_bool(replace, 'replace')
    if not callable(func):
        raise ValueError('page hook должен быть callable')
    if normalized in PAGE_HOOKS and not replace:
        raise ValueError(f"page hook '{normalized}' уже зарегистрирован")
    PAGE_HOOKS[normalized] = func


def parse_registry_names(raw: Any) -> list[str]:
    """Parses a JSON array of hooks/guards names from the database."""
    if raw is None or raw == '':
        return []
    if isinstance(raw, (list, tuple)):
        values = raw
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                values = parsed
            elif isinstance(parsed, str):
                values = [parsed]
            else:
                values = [raw]
        except json.JSONDecodeError:
            values = [raw]
    else:
        return []
    return [_normalize_registry_name(value) for value in values if isinstance(value, str) and value.strip()]


def build_page_flow_context(target: Any, **values: Any) -> dict[str, Any]:
    """
    Collects the base context for guards/hooks route/page transitions.

    Hooks are executed before render_page(), so they need the same minimum
    common values, which the renderer will later add for placeholders.
    """
    context = dict(values)
    if 'telegram_id' not in context:
        user = getattr(target, 'from_user', None)
        if user and not getattr(user, 'is_bot', False):
            context['telegram_id'] = user.id

    bot = getattr(target, 'bot', None)
    if bot is None:
        message = getattr(target, 'message', None)
        bot = getattr(message, 'bot', None)
    bot_username = (
        getattr(bot, 'my_username', None)
        or getattr(bot, 'username', None)
        or ''
    )
    if bot_username:
        context.setdefault('bot_username', bot_username)
    return context


async def run_page_guards(
    guard_names: list[str],
    target: Any,
    context: Mapping[str, Any],
) -> PageGuardResult:
    """Performs guards. An unknown guard is blocking the route."""
    base_context = _require_context_mapping(context)
    for name in guard_names:
        guard = PAGE_GUARDS.get(name)
        if guard is None:
            logger.warning("Неизвестный page guard '%s' — переход заблокирован", name)
            return PageGuardResult(False, "⚠️ Страница временно недоступна")
        try:
            result = await _maybe_await(guard(target, dict(base_context)))
            normalized = _normalize_guard_result(result)
        except Exception as e:
            logger.exception("Ошибка page guard '%s': %s", name, e)
            return PageGuardResult(False, "⚠️ Страница временно недоступна")
        if not normalized.allowed:
            return normalized
    return PageGuardResult(True)


async def run_page_hooks(
    hook_names: list[str],
    target: Any,
    context: Mapping[str, Any],
) -> PageHookResult:
    """Executes hooks. Each next hook sees the context of previous hooks."""
    merged = PageHookResult()
    current_context = _require_context_mapping(context)
    for name in hook_names:
        hook = PAGE_HOOKS.get(name)
        if hook is None:
            logger.warning("Неизвестный page hook '%s' — пропускаем", name)
            continue
        try:
            result = await _maybe_await(hook(target, dict(current_context)))
            normalized = _normalize_hook_result(result)
            _merge_hook_result(merged, normalized)
        except Exception as e:
            logger.exception("Ошибка page hook '%s': %s", name, e)
            continue
        current_context.update(normalized.context)
    return merged


def _normalize_registry_name(name: Any) -> str:
    return str(name).strip().casefold()


def _normalize_registered_name(name: Any) -> str:
    if not isinstance(name, str):
        raise ValueError("registry name должен быть строкой")
    return _normalize_registry_name(name)


def _require_context_mapping(context: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(context, Mapping):
        raise ValueError("context должен быть mapping")
    return dict(context)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _normalize_guard_result(result: PageGuardResult | bool | Mapping[str, Any] | None) -> PageGuardResult:
    if isinstance(result, PageGuardResult):
        return PageGuardResult(
            _require_bool(result.allowed, 'allowed'),
            _optional_text(result.message, 'message') or '',
            _require_bool(result.show_alert, 'show_alert'),
        )
    if isinstance(result, bool):
        return PageGuardResult(result)
    if isinstance(result, Mapping):
        if 'allowed' not in result:
            raise ValueError("page guard mapping должен содержать поле allowed")
        message = _optional_text(result.get('message'), 'message') or ''
        return PageGuardResult(
            _require_bool(result.get('allowed'), 'allowed'),
            message,
            _require_bool(result.get('show_alert', True), 'show_alert'),
        )
    if result is None:
        return PageGuardResult(False)
    raise ValueError("page guard должен вернуть PageGuardResult, bool или mapping с allowed")


def _require_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"page guard field {field_name} должен быть bool")


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"page guard field {field_name} должен быть строкой")
    return value


def _normalize_hook_result(result: PageHookResult | Mapping[str, Any] | None) -> PageHookResult:
    if isinstance(result, PageHookResult):
        return PageHookResult(
            context=_require_mapping_dict(result.context, 'context'),
            text_replacements=_require_mapping_dict(result.text_replacements, 'text_replacements'),
            visibility=_require_visibility_dict(result.visibility),
            prepend_buttons=_normalize_button_rows(result.prepend_buttons, 'prepend_buttons'),
            append_buttons=_normalize_button_rows(result.append_buttons, 'append_buttons'),
        )
    if not isinstance(result, Mapping):
        return PageHookResult()
    return PageHookResult(
        context=_require_mapping_dict(result.get('context'), 'context'),
        text_replacements=_require_mapping_dict(result.get('text_replacements'), 'text_replacements'),
        visibility=_require_visibility_dict(result.get('visibility')),
        prepend_buttons=_normalize_button_rows(result.get('prepend_buttons'), 'prepend_buttons'),
        append_buttons=_normalize_button_rows(result.get('append_buttons'), 'append_buttons'),
    )


def _require_mapping_dict(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"page hook field {field_name} должен быть mapping")
    return dict(value)


def _require_visibility_dict(value: Any) -> dict[str, bool]:
    raw = _require_mapping_dict(value, 'visibility')
    result: dict[str, bool] = {}
    for button_id, visible in raw.items():
        if not isinstance(button_id, str):
            raise ValueError("page hook visibility keys должны быть строками")
        if not isinstance(visible, bool):
            raise ValueError("page hook visibility values должны быть bool")
        result[button_id] = visible
    return result


def _normalize_button_rows(value: Any, field_name: str) -> list[list[InlineKeyboardButton]] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"page hook field {field_name} должен быть списком рядов кнопок")
    rows: list[list[InlineKeyboardButton]] = []
    for row in value:
        if not isinstance(row, (list, tuple)):
            raise ValueError(f"page hook field {field_name} должен содержать только ряды кнопок")
        buttons: list[InlineKeyboardButton] = []
        for button in row:
            if not isinstance(button, InlineKeyboardButton):
                raise ValueError(f"page hook field {field_name} должен содержать только InlineKeyboardButton")
            buttons.append(button)
        rows.append(buttons)
    return rows


def _merge_hook_result(target: PageHookResult, source: PageHookResult) -> None:
    target.context.update(source.context)
    target.text_replacements.update(source.text_replacements)
    target.visibility.update(source.visibility)
    if source.prepend_buttons:
        if target.prepend_buttons is None:
            target.prepend_buttons = []
        target.prepend_buttons.extend(source.prepend_buttons)
    if source.append_buttons:
        if target.append_buttons is None:
            target.append_buttons = []
        target.append_buttons.extend(source.append_buttons)


def _context_int(context: Mapping[str, Any], key: str) -> int | None:
    value = context.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _context_text(context: Mapping[str, Any], key: str) -> str:
    value = context.get(key)
    return value if isinstance(value, str) else ''


def _missing_context_values(
    context: Mapping[str, Any],
    values: Mapping[str, Any],
) -> dict[str, Any]:
    return {key: value for key, value in values.items() if key not in context}


def _not_banned_guard(target: Any, context: Mapping[str, Any]) -> PageGuardResult:
    telegram_id = _context_int(context, 'telegram_id')
    if telegram_id is None:
        return PageGuardResult(False, "⚠️ Страница недоступна")

    from database.requests import is_user_banned

    if is_user_banned(telegram_id):
        return PageGuardResult(False, "⛔ Доступ заблокирован")
    return PageGuardResult(True)


def _referral_enabled_guard(target: Any, context: Mapping[str, Any]) -> PageGuardResult:
    from database.requests import is_referral_enabled

    if not is_referral_enabled():
        return PageGuardResult(False, "❌ Реферальная система недоступна")
    return PageGuardResult(True)


def _widget_tariffs_hook(target: Any, context: Mapping[str, Any]) -> PageHookResult:
    """Adds tariff-list context if it is not already passed."""
    if 'tariffs_html' in context:
        return PageHookResult()

    from bot.utils.page_dynamic_data import build_tariff_text

    return PageHookResult(context={'tariffs_html': build_tariff_text()})


def _widget_referral_hook(target: Any, context: Mapping[str, Any]) -> PageHookResult:
    """Adds the context of referral placeholders if it has not already been sent."""
    if 'referral_link' in context and 'referral_stats_html' in context:
        return PageHookResult()

    from bot.utils.page_dynamic_data import build_referral_context_values

    values = build_referral_context_values(
        _context_int(context, 'telegram_id'),
        _context_text(context, 'bot_username'),
    )
    return PageHookResult(context=_missing_context_values(context, values))


def _widget_support_hook(target: Any, context: Mapping[str, Any]) -> PageHookResult:
    """Adds login context to native support."""
    if 'support_title_html' in context and 'support_instruction_html' in context:
        return PageHookResult()

    from bot.utils.page_dynamic_data import build_support_context_values

    thread_id = _context_int(context, 'support_thread_id') or _context_int(context, 'thread_id')
    values = build_support_context_values(thread_id=thread_id)
    return PageHookResult(context=_missing_context_values(context, values))


def _widget_profile_hook(target: Any, context: Mapping[str, Any]) -> PageHookResult:
    """Adds profile, balance and key summary context."""
    if 'user_profile_html' in context and 'keys_summary_html' in context:
        return PageHookResult()

    from bot.utils.page_dynamic_data import build_user_profile_context_values

    values = build_user_profile_context_values(_context_int(context, 'telegram_id'))
    return PageHookResult(context=_missing_context_values(context, values))


async def _widget_my_keys_hook(target: Any, context: Mapping[str, Any]) -> PageHookResult:
    """Adds context to a text list of keys."""
    if 'keys_list_html' in context:
        return PageHookResult()

    from bot.utils.page_dynamic_data import build_my_keys_context_values

    values = await build_my_keys_context_values(_context_int(context, 'telegram_id'))
    return PageHookResult(context=_missing_context_values(context, values))


register_page_guard('not_banned', _not_banned_guard)
register_page_guard('referral_enabled', _referral_enabled_guard)
register_page_hook('widget_tariffs', _widget_tariffs_hook)
register_page_hook('widget_referral', _widget_referral_hook)
register_page_hook('widget_support', _widget_support_hook)
register_page_hook('widget_profile', _widget_profile_hook)
register_page_hook('widget_my_keys', _widget_my_keys_hook)
