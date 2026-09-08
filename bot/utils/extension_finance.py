"""Public financial reads and commands mixed into the controlled Core facade."""
from __future__ import annotations

from typing import Any


class ExtensionFinanceAPI:
    """Financial extension surface; domain validation and writes stay in core."""

    extension_id: str

    def preview_payment_price(
        self, *, purpose: str = 'key_purchase', tariff_id: int | None = None,
        key_id: int | None = None, nominal_amount_minor: int | None = None,
        user_id: int | None = None, telegram_id: int | None = None,
    ) -> dict[str, Any]:
        """Read a base price without creating an order or reserving a code."""
        from bot.services.payment_pricing import preview_payment_price

        return preview_payment_price(
            user_id=_target(user_id, telegram_id), purpose=purpose,
            tariff_id=tariff_id, key_id=key_id, nominal_amount_minor=nominal_amount_minor,
        )

    def get_promo_code(self, code: str) -> dict[str, Any] | None:
        """Read any installed promo or coupon, irrespective of its creator."""
        from database.requests import get_extension_promo_code

        return get_extension_promo_code(code)

    def list_promo_codes(
        self, *, code_type: str | None = None, include_inactive: bool = True,
        cursor: str | None = None, limit: int = 50,
    ) -> dict[str, Any]:
        from database.requests import list_extension_promo_codes

        return list_extension_promo_codes(
            code_type=code_type, include_inactive=include_inactive, cursor=cursor, limit=limit,
        )

    def check_promo_code(
        self, code: str, *, user_id: int | None = None, telegram_id: int | None = None,
    ) -> dict[str, Any]:
        from database.requests import check_extension_promo_code

        return check_extension_promo_code(code, user_id=_target(user_id, telegram_id))

    async def create_promo_code(
        self, *, discount_percent: int, idempotency_key: str,
        code: str | None = None, code_type: str = 'promo',
        expires_at: str | None = None, lifetime_days: int | None = None,
        activation_limit: int | None = None, is_active: bool = True,
        issued_to_user_id: int | None = None,
    ) -> dict[str, Any]:
        """Create a native promo/coupon; a replay returns the original code."""
        return await _promo_operation(self.extension_id, 'create_promo_code', idempotency_key, {
            'discount_percent': discount_percent, 'code': code, 'code_type': code_type,
            'expires_at': expires_at, 'lifetime_days': lifetime_days,
            'activation_limit': activation_limit, 'is_active': is_active,
            'issued_to_user_id': issued_to_user_id,
        })

    async def update_promo_code(
        self, promo_code_id: int, *, idempotency_key: str,
        discount_percent: int | None = None, expires_at: Any = '__unchanged__',
        activation_limit: Any = '__unchanged__', is_active: bool | None = None,
    ) -> dict[str, Any]:
        """Change allowed conditions while retaining code identity and history."""
        values: dict[str, Any] = {'promo_code_id': promo_code_id}
        if discount_percent is not None:
            values['discount_percent'] = discount_percent
        if expires_at != '__unchanged__':
            values['expires_at'] = expires_at
        if activation_limit != '__unchanged__':
            values['activation_limit'] = activation_limit
        if is_active is not None:
            values['is_active'] = is_active
        return await _promo_operation(self.extension_id, 'update_promo_code', idempotency_key, values)

    async def set_promo_code_active(
        self, promo_code_id: int, is_active: bool, *, idempotency_key: str,
    ) -> dict[str, Any]:
        if not isinstance(is_active, bool):
            raise ValueError('is_active must be bool')
        return await self.update_promo_code(
            promo_code_id, is_active=is_active, idempotency_key=idempotency_key,
        )

    async def activate_promo_code(
        self, code: str, *, idempotency_key: str,
        user_id: int | None = None, telegram_id: int | None = None,
    ) -> dict[str, Any]:
        """Select the user's next native discount, replacing the active slot."""
        return await _promo_operation(
            self.extension_id, 'activate_promo_code', idempotency_key, {'code': code},
            user_id=_target(user_id, telegram_id),
        )

    async def clear_active_promo_code(
        self, *, idempotency_key: str,
        user_id: int | None = None, telegram_id: int | None = None,
    ) -> dict[str, Any]:
        """Clear the active slot without changing an issued invoice reservation."""
        return await _promo_operation(
            self.extension_id, 'clear_active_promo_code', idempotency_key, {},
            user_id=_target(user_id, telegram_id),
        )

    def get_payment(self, order_id: str) -> dict[str, Any] | None:
        """Read a safe payment snapshot for any user on this installation."""
        from database.requests import get_extension_payment

        return get_extension_payment(order_id)

    def list_user_payments(
        self, *, user_id: int | None = None, telegram_id: int | None = None,
        purpose: str | None = None, status: str | None = None,
        since: str | None = None, until: str | None = None,
        cursor: str | None = None, limit: int = 50,
    ) -> dict[str, Any]:
        """Read one page, newest first; date bounds concern creation time."""
        from database.requests import list_extension_user_payments

        return list_extension_user_payments(
            _target(user_id, telegram_id), purpose=purpose, status=status,
            since=since, until=until, cursor=cursor, limit=limit,
        )

    def get_user_payment_summary(
        self, *, user_id: int | None = None, telegram_id: int | None = None,
        purpose: str | None = None, status: str | None = None,
        since: str | None = None, until: str | None = None,
    ) -> dict[str, Any]:
        """Read counts and paid sums by base currency without conversion."""
        from database.requests import get_extension_user_payment_summary

        return get_extension_user_payment_summary(
            _target(user_id, telegram_id), purpose=purpose, status=status,
            since=since, until=until,
        )


async def _promo_operation(extension_id, operation, idempotency_key, payload, *, user_id=None):
    from bot.utils.extension_core import _ensure_new_mutation_allowed
    from database.requests import apply_extension_promo_operation

    _ensure_new_mutation_allowed(operation)
    if user_id is None:
        return apply_extension_promo_operation(
            extension_id=extension_id, operation=operation,
            idempotency_key=idempotency_key, payload=payload,
        )
    from bot.services.user_locks import user_locks

    async with user_locks[user_id]:
        return apply_extension_promo_operation(
            extension_id=extension_id, operation=operation,
            idempotency_key=idempotency_key, payload=payload, user_id=user_id,
        )


def _target(user_id: int | None, telegram_id: int | None) -> int:
    from bot.utils.extension_core import _resolve_new_mutation_target
    from database.requests import get_extension_user_identity

    value = _resolve_new_mutation_target(
        user_id=user_id, telegram_id=telegram_id, default_to_current=True,
    )
    if value is None or get_extension_user_identity(user_id=value) is None:
        raise ValueError('user not found')
    return value
