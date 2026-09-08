"""Limited core facade for custom extensions."""
from __future__ import annotations

from typing import Any

from bot.utils.extension_finance import ExtensionFinanceAPI


class ExtensionCoreAPI(ExtensionFinanceAPI):
    """Safe kernel read/command operations for one extension_id."""

    def __init__(self, extension_id: str):
        self.extension_id = extension_id

    def get_current_user(self) -> dict[str, Any] | None:
        """Returns the safe user snapshot bound to the current extension call."""
        from bot.utils.custom_extensions import _get_current_extension_telegram_id

        telegram_id = _get_current_extension_telegram_id()
        if telegram_id is None:
            raise RuntimeError(
                'get_current_user() requires an extension runtime with a current user'
            )
        telegram_id = _normalize_positive_int(telegram_id, 'telegram_id')
        from bot.services.extension_user_snapshot import build_extension_user_snapshot

        return build_extension_user_snapshot(telegram_id)

    def get_user_by_telegram_id(self, telegram_id: int) -> dict[str, Any] | None:
        """Returns a secure user profile without secrets and service fields."""
        telegram_id = _normalize_positive_int(telegram_id, 'telegram_id')
        from database.requests import get_user_by_telegram_id

        user = get_user_by_telegram_id(telegram_id)
        if not user:
            return None
        return {
            'id': user.get('id'),
            'telegram_id': user.get('telegram_id'),
            'username': user.get('username'),
            'first_name': user.get('first_name'),
            'last_name': user.get('last_name'),
            'created_at': user.get('created_at'),
            'is_banned': bool(user.get('is_banned')),
            'is_bot_blocked': bool(user.get('is_bot_blocked')),
            'personal_balance': user.get('personal_balance') or 0,
        }

    async def send_user_page(
        self,
        page_key: str,
        *,
        user_id: int | None = None,
        telegram_id: int | None = None,
    ) -> dict[str, Any]:
        """Send one stored page; the extension owns retries and deduplication."""
        _ensure_new_mutation_allowed('send_user_page')
        from bot.utils.custom_extensions import (
            _get_current_extension_bot,
            _get_current_extension_telegram_id,
        )
        from bot.services.extension_pages import send_extension_user_page

        if user_id is not None and telegram_id is not None:
            raise ValueError('pass only user_id or telegram_id')
        if user_id is not None:
            from database.requests import get_user_by_id

            user = get_user_by_id(_normalize_positive_int(user_id, 'user_id'))
            if user is None:
                return {'ok': False, 'status': 'not_sent'}
            telegram_id = user['telegram_id']
        if telegram_id is None:
            telegram_id = _get_current_extension_telegram_id()
            if telegram_id is None:
                raise RuntimeError('send_user_page requires a recipient or current user')
        return await send_extension_user_page(
            _get_current_extension_bot(),
            telegram_id=_normalize_positive_int(telegram_id, 'telegram_id'),
            page_key=page_key,
        )

    async def send_current_user_page(self, page_key: str) -> dict[str, Any]:
        """Compatibility shorthand for sending a page to the current user."""
        return await self.send_user_page(page_key)

    async def schedule_task(
        self, name: str, *, idempotency_key: str, delay_seconds=None, run_at=None,
        payload=None, user_id: int | None = None, telegram_id: int | None = None,
    ) -> dict[str, Any]:
        """Persist one owner-local invocation; success acknowledges scheduling only."""
        _ensure_new_mutation_allowed('schedule_task')
        from bot.utils.action_origin_context import normalize_completion_handler_name
        from bot.utils.custom_extensions import _get_current_extension_telegram_id
        from bot.utils.extension_task_registry import EXTENSION_TASK_HANDLERS
        from database.requests import schedule_extension_task

        name = normalize_completion_handler_name(name)
        if user_id is None and telegram_id is None:
            telegram_id = _get_current_extension_telegram_id()
        return schedule_extension_task(
            extension_id=self.extension_id, handler_name=name,
            handler_registered=f'{self.extension_id}.{name}' in EXTENSION_TASK_HANDLERS,
            idempotency_key=idempotency_key, delay_seconds=delay_seconds,
            run_at=run_at, payload=payload, user_id=user_id, telegram_id=telegram_id,
        )

    def get_user_keys(self, telegram_id: int) -> list[dict[str, Any]]:
        """Returns display data of the user's keys without VPN secrets."""
        telegram_id = _normalize_positive_int(telegram_id, 'telegram_id')
        from database.requests import get_user_keys_for_display

        allowed = {
            'id',
            'display_name',
            'custom_name',
            'expires_at',
            'is_active',
            'traffic_used',
            'traffic_limit',
            'server_name',
            'tariff_name',
        }
        return [
            {key: value for key, value in dict(item).items() if key in allowed}
            for item in get_user_keys_for_display(telegram_id)
        ]

    def list_current_user_key_summaries(self) -> list[dict[str, Any]]:
        """Return composition-safe summaries for the current extension user."""
        owner_user_id = _resolve_current_extension_user_id()
        from bot.services.subscription_composition import (
            list_subscription_key_summaries,
        )

        return list_subscription_key_summaries(owner_user_id=owner_user_id)

    def list_key_subscription_bindings(
        self,
        *,
        key_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return the current user's exact graph without panel identities."""
        owner_user_id = _resolve_current_extension_user_id()
        from bot.services.subscription_composition import (
            list_key_subscription_bindings,
        )

        return list_key_subscription_bindings(
            owner_user_id=owner_user_id,
            key_id=key_id,
        )

    async def bind_key_subscription(
        self,
        host_key_id: int,
        component_key_id: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Persist one current-user composition relation idempotently."""
        _ensure_new_mutation_allowed('bind_key_subscription')
        return await _apply_subscription_operation(
            extension_id=self.extension_id,
            idempotency_key=idempotency_key,
            operation='bind_key_subscription',
            owner_user_id=_resolve_current_extension_user_id(),
            host_key_id=_normalize_positive_int(host_key_id, 'host_key_id'),
            component_key_id=_normalize_positive_int(
                component_key_id,
                'component_key_id',
            ),
        )

    async def unbind_key_subscription(
        self,
        host_key_id: int,
        component_key_id: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Remove one exact current-user relation idempotently."""
        _ensure_new_mutation_allowed('unbind_key_subscription')
        return await _apply_subscription_operation(
            extension_id=self.extension_id,
            idempotency_key=idempotency_key,
            operation='unbind_key_subscription',
            owner_user_id=_resolve_current_extension_user_id(),
            host_key_id=_normalize_positive_int(host_key_id, 'host_key_id'),
            component_key_id=_normalize_positive_int(
                component_key_id,
                'component_key_id',
            ),
        )

    async def request_key_subscription_reconcile(
        self,
        host_key_id: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Wake one owner-verified host without exposing panel operations."""
        _ensure_new_mutation_allowed('request_key_subscription_reconcile')
        return await _apply_subscription_operation(
            extension_id=self.extension_id,
            idempotency_key=idempotency_key,
            operation='request_key_subscription_reconcile',
            owner_user_id=_resolve_current_extension_user_id(),
            host_key_id=_normalize_positive_int(host_key_id, 'host_key_id'),
        )

    async def grant_days_to_first_active_key(
        self,
        *,
        days: int,
        reason: str,
        idempotency_key: str,
        user_id: int | None = None,
        telegram_id: int | None = None,
    ) -> dict[str, Any]:
        """Accrues days to the user's first active key via core-log."""
        _ensure_mutation_allowed('grant_days_to_first_active_key')
        target_user_id = _resolve_user_id(user_id=user_id, telegram_id=telegram_id)
        return await _apply_core_operation(
            extension_id=self.extension_id,
            idempotency_key=idempotency_key,
            operation='grant_days_to_first_active_key',
            target_user_id=target_user_id,
            amount=_normalize_positive_int(days, 'days'),
            reason=reason,
        )

    async def add_balance_bonus(
        self,
        *,
        cents: int,
        reason: str,
        idempotency_key: str,
        user_id: int | None = None,
        telegram_id: int | None = None,
    ) -> dict[str, Any]:
        """Credits a bonus to the user's balance via core-log."""
        _ensure_mutation_allowed('add_balance_bonus')
        target_user_id = _resolve_user_id(user_id=user_id, telegram_id=telegram_id)
        return await _apply_core_operation(
            extension_id=self.extension_id,
            idempotency_key=idempotency_key,
            operation='add_balance_bonus',
            target_user_id=target_user_id,
            amount=_normalize_positive_int(cents, 'cents'),
            reason=reason,
        )

    async def debit_balance(
        self,
        amount_minor: int,
        reason: str,
        idempotency_key: str,
        user_id: int | None = None,
        telegram_id: int | None = None,
    ) -> dict[str, Any]:
        """Debits the current or explicitly selected user's balance."""
        _ensure_new_mutation_allowed('debit_balance')
        normalized_amount = _normalize_positive_int(amount_minor, 'amount_minor')
        from database.requests import (
            normalize_extension_core_idempotency_key,
            normalize_extension_core_reason,
        )

        normalized_reason = normalize_extension_core_reason(reason)
        normalized_key = normalize_extension_core_idempotency_key(idempotency_key)
        target_user_id = _resolve_new_mutation_target(
            user_id=user_id,
            telegram_id=telegram_id,
            default_to_current=True,
        )
        if target_user_id is None:
            return _user_not_found_result(
                'debit_balance',
                amount_minor=normalized_amount,
            )
        from bot.utils.custom_extensions import _get_current_extension_telegram_id

        performed_by = _get_current_extension_telegram_id()
        return await _apply_core_operation(
            extension_id=self.extension_id,
            idempotency_key=normalized_key,
            operation='debit_balance',
            target_user_id=target_user_id,
            amount=normalized_amount,
            reason=normalized_reason,
            performed_by=performed_by,
        )

    async def create_current_user_support_ticket(
        self,
        text_html: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Creates a built-in support ticket from the current user."""
        _ensure_new_mutation_allowed('create_current_user_support_ticket')
        _require_bound_bot_runtime()
        normalized_text = _normalize_support_text(text_html)
        from database.requests import normalize_extension_core_idempotency_key

        normalized_key = normalize_extension_core_idempotency_key(idempotency_key)
        target_user_id = _resolve_new_mutation_target(
            user_id=None,
            telegram_id=None,
            default_to_current=True,
        )
        if target_user_id is None:
            return _user_not_found_result('create_current_user_support_ticket')
        return await _create_support_ticket(
            extension_id=self.extension_id,
            idempotency_key=normalized_key,
            operation='create_current_user_support_ticket',
            target_user_id=target_user_id,
            text_html=normalized_text,
        )

    async def create_outbound_support_ticket(
        self,
        text_html: str,
        idempotency_key: str,
        user_id: int | None = None,
        telegram_id: int | None = None,
    ) -> dict[str, Any]:
        """Creates a built-in admin-side support ticket for an explicit user."""
        _ensure_new_mutation_allowed('create_outbound_support_ticket')
        _require_bound_bot_runtime()
        normalized_text = _normalize_support_text(text_html)
        from database.requests import normalize_extension_core_idempotency_key

        normalized_key = normalize_extension_core_idempotency_key(idempotency_key)
        target_user_id = _resolve_new_mutation_target(
            user_id=user_id,
            telegram_id=telegram_id,
            default_to_current=False,
        )
        if target_user_id is None:
            return _user_not_found_result('create_outbound_support_ticket')
        return await _create_support_ticket(
            extension_id=self.extension_id,
            idempotency_key=normalized_key,
            operation='create_outbound_support_ticket',
            target_user_id=target_user_id,
            text_html=normalized_text,
        )

    def list_support_ticket_sessions(
        self,
        status: str | None = None,
        user_id: int | None = None,
        telegram_id: int | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Returns an administrator-only local page of support sessions."""
        _ensure_support_ticket_read_allowed('list_support_ticket_sessions')
        if user_id is not None and telegram_id is not None:
            raise ValueError('pass only user_id or telegram_id')
        normalized_status = _normalize_support_status_filter(status)
        normalized_user_id = (
            _normalize_positive_int(user_id, 'user_id')
            if user_id is not None
            else None
        )
        normalized_telegram_id = (
            _normalize_positive_int(telegram_id, 'telegram_id')
            if telegram_id is not None
            else None
        )
        from bot.services.extension_support import (
            list_extension_support_ticket_sessions,
        )

        return list_extension_support_ticket_sessions(
            status=normalized_status,
            user_id=normalized_user_id,
            telegram_id=normalized_telegram_id,
            limit=_normalize_bounded_limit(limit, default=20),
            offset=_normalize_non_negative_int(offset, 'offset'),
        )

    def get_support_ticket_history(
        self,
        thread_id: int,
        limit: int = 50,
        before_message_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Returns an administrator-only local page of ticket messages."""
        _ensure_support_ticket_read_allowed('get_support_ticket_history')
        normalized_before = (
            _normalize_positive_int(before_message_id, 'before_message_id')
            if before_message_id is not None
            else None
        )
        from bot.services.extension_support import (
            get_extension_support_ticket_history,
        )

        return get_extension_support_ticket_history(
            thread_id=_normalize_positive_int(thread_id, 'thread_id'),
            limit=_normalize_bounded_limit(limit, default=50),
            before_message_id=normalized_before,
        )

    async def set_support_ticket_status(
        self,
        thread_id: int,
        status: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Changes one ticket workflow status from an administrator action."""
        actor_telegram_id = _ensure_support_ticket_status_mutation_allowed(
            'set_support_ticket_status'
        )
        from database.requests import normalize_extension_core_idempotency_key

        normalized_key = normalize_extension_core_idempotency_key(idempotency_key)
        normalized_status = _normalize_new_support_ticket_status(status)
        from bot.services.extension_support import set_extension_support_ticket_status

        return await set_extension_support_ticket_status(
            extension_id=self.extension_id,
            idempotency_key=normalized_key,
            thread_id=_normalize_positive_int(thread_id, 'thread_id'),
            status=normalized_status,
            actor_telegram_id=actor_telegram_id,
        )

    async def check_telegram_chat_member(
        self,
        chat_id: int | str,
        telegram_id: int | None = None,
    ) -> dict[str, Any]:
        """Checks Telegram chat membership through the current bot runtime context."""
        if telegram_id is None:
            from bot.utils.custom_extensions import _get_current_extension_telegram_id

            telegram_id = _get_current_extension_telegram_id()
        if telegram_id is None:
            raise ValueError('telegram_id is required')
        from bot.services.telegram_membership import check_telegram_chat_member
        from bot.utils.custom_extensions import _get_current_extension_bot

        return await check_telegram_chat_member(
            _get_current_extension_bot(),
            chat_id=chat_id,
            telegram_id=_normalize_positive_int(telegram_id, 'telegram_id'),
        )


def _resolve_user_id(*, user_id: int | None, telegram_id: int | None) -> int:
    if user_id is not None and telegram_id is not None:
        raise ValueError('передайте только user_id или только telegram_id')
    if user_id is not None:
        return _normalize_positive_int(user_id, 'user_id')
    if telegram_id is None:
        raise ValueError('нужно передать user_id или telegram_id')
    telegram_id = _normalize_positive_int(telegram_id, 'telegram_id')
    from database.requests import get_user_internal_id

    resolved = get_user_internal_id(telegram_id)
    if not resolved:
        raise ValueError('пользователь не найден')
    return int(resolved)


async def _apply_core_operation(**kwargs: Any) -> dict[str, Any]:
    from bot.services.extension_core_ops import apply_extension_core_operation

    return await apply_extension_core_operation(**kwargs)


async def _create_support_ticket(**kwargs: Any) -> dict[str, Any]:
    from bot.services.extension_support import create_extension_support_ticket
    from bot.utils.custom_extensions import _get_current_extension_bot

    bot = _get_current_extension_bot()
    if bot is None:
        raise RuntimeError('support ticket operations require a bound bot runtime')
    return await create_extension_support_ticket(bot=bot, **kwargs)


async def _apply_subscription_operation(**kwargs: Any) -> dict[str, Any]:
    from bot.services.extension_subscription_composition import (
        apply_extension_subscription_operation,
    )

    return await apply_extension_subscription_operation(**kwargs)


def _require_bound_bot_runtime() -> None:
    from bot.utils.custom_extensions import _get_current_extension_bot

    if _get_current_extension_bot() is None:
        raise RuntimeError('support ticket operations require a bound bot runtime')


def _ensure_mutation_allowed(operation: str) -> None:
    from bot.utils.action_policy import ensure_action_policy_read_only

    ensure_action_policy_read_only(operation)


def _ensure_new_mutation_allowed(operation: str) -> None:
    _ensure_mutation_allowed(operation)
    from bot.utils.custom_extensions import _get_current_extension_invocation_kind

    invocation_kind = _get_current_extension_invocation_kind()
    if invocation_kind not in {
        'callback',
        'command',
        'lifecycle_hook',
        'completion_handler',
        'event_handler',
        'task_handler',
    }:
        raise RuntimeError(
            f'{operation} is allowed only in extension callbacks, commands, or '
            'lifecycle hooks; durable completion handlers are also supported'
        )


def _ensure_support_ticket_read_allowed(operation: str) -> int:
    from bot.utils.admin import is_admin
    from bot.utils.custom_extensions import (
        _get_current_extension_invocation_kind,
        _get_current_extension_telegram_id,
    )

    invocation_kind = _get_current_extension_invocation_kind()
    telegram_id = _get_current_extension_telegram_id()
    if invocation_kind not in {
        'guard',
        'page_hook',
        'callback',
        'command',
        'action_policy',
    }:
        raise PermissionError(
            f'{operation} requires an interactive extension invocation'
        )
    if telegram_id is None or not is_admin(int(telegram_id)):
        raise PermissionError(
            f'{operation} requires a current Telegram administrator'
        )
    return int(telegram_id)


def _ensure_support_ticket_status_mutation_allowed(operation: str) -> int:
    _ensure_mutation_allowed(operation)
    from bot.utils.custom_extensions import _get_current_extension_invocation_kind

    if _get_current_extension_invocation_kind() not in {'callback', 'command'}:
        raise PermissionError(
            f'{operation} is allowed only in administrator extension callbacks or commands'
        )
    return _ensure_support_ticket_read_allowed(operation)


def _resolve_new_mutation_target(
    *,
    user_id: int | None,
    telegram_id: int | None,
    default_to_current: bool,
) -> int | None:
    if user_id is not None and telegram_id is not None:
        raise ValueError('pass only user_id or telegram_id')
    if user_id is not None:
        return _normalize_positive_int(user_id, 'user_id')
    if telegram_id is None:
        if not default_to_current:
            raise ValueError('user_id or telegram_id is required')
        from bot.utils.custom_extensions import _get_current_extension_telegram_id

        telegram_id = _get_current_extension_telegram_id()
        if telegram_id is None:
            raise RuntimeError('operation requires an extension runtime with a current user')
    telegram_id = _normalize_positive_int(telegram_id, 'telegram_id')
    from database.requests import get_user_internal_id

    resolved = get_user_internal_id(telegram_id)
    return int(resolved) if resolved else None


def _resolve_current_extension_user_id() -> int:
    owner_user_id = _resolve_new_mutation_target(
        user_id=None,
        telegram_id=None,
        default_to_current=True,
    )
    if owner_user_id is None:
        raise RuntimeError('current extension user is not registered')
    return owner_user_id


def _normalize_support_text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError('text_html must be a string')
    text = value.strip()
    if not text:
        raise ValueError('text_html must not be empty')
    from bot.utils.text import TELEGRAM_TEXT_LIMIT, html_to_plain_text

    visible_text = html_to_plain_text(text)
    if not visible_text:
        raise ValueError('text_html must contain visible text')
    if len(visible_text) > TELEGRAM_TEXT_LIMIT:
        raise ValueError(f'text_html must not exceed {TELEGRAM_TEXT_LIMIT} visible characters')
    return text


def _normalize_support_status_filter(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('status must be a string or None')
    if not value:
        raise ValueError('status must not be empty')
    return value


def _normalize_new_support_ticket_status(value: Any) -> str:
    from database.requests import normalize_support_ticket_status

    return normalize_support_ticket_status(value)


def _normalize_bounded_limit(value: Any, *, default: int) -> int:
    if value is None:
        value = default
    limit = _normalize_positive_int(value, 'limit')
    if limit > 100:
        raise ValueError('limit must not exceed 100')
    return limit


def _normalize_non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f'{field} must be a non-negative integer')
    return value


def _user_not_found_result(
    operation: str,
    *,
    amount_minor: int | None = None,
) -> dict[str, Any]:
    result = {
        'ok': False,
        'status': 'user_not_found',
        'stored_status': 'no_op',
        'applied': False,
        'already_applied': False,
        'operation': operation,
        'target_user_id': None,
        'metadata': {'status': 'user_not_found'},
    }
    if amount_minor is not None:
        result['amount'] = amount_minor
        result['amount_minor'] = amount_minor
    return result


def _normalize_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f'{field} должен быть положительным integer')
    return value


__all__ = ['ExtensionCoreAPI']
