"""Telegram adapters for the shared new-key setup service."""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.services.new_key_setup import (
    NewKeySetupResult,
    NewKeySetupStatus,
    provision_new_key,
    resolve_new_key_setup,
)

logger = logging.getLogger(__name__)
router = Router()


@dataclass
class BackgroundKeyFlowTarget:
    """Background transport state; business decisions stay in the shared service."""

    bot: Any
    telegram_id: int
    username: str | None = None
    message: Message | None = None

    @property
    def is_background_delivery(self) -> bool:
        """Tell page preparation to use the real Bot as its background target."""
        return True

    @property
    def from_user(self):
        return SimpleNamespace(
            id=self.telegram_id,
            username=self.username,
            is_bot=False,
        )


def _is_callback_target(target: Any) -> bool:
    return isinstance(target, CallbackQuery)


def _target_message(target: Any) -> Message | None:
    if isinstance(target, BackgroundKeyFlowTarget):
        return target.message
    if isinstance(target, CallbackQuery):
        return target.message
    return target if isinstance(target, Message) else getattr(target, "message", None)


def _target_user(target: Any):
    return getattr(target, "from_user", None)


def _target_with_message(target: Any, message: Message | None, from_user=None):
    if isinstance(target, BackgroundKeyFlowTarget):
        if message is not None:
            target.message = message
        return target
    if isinstance(target, CallbackQuery):
        return target
    if message is None:
        return target
    return SimpleNamespace(
        message=message,
        from_user=from_user or _target_user(target),
        bot=getattr(target, "bot", None) or getattr(message, "bot", None),
    )


def _owner_from_order(order: dict | None) -> tuple[int | None, str | None]:
    """Resolve a key owner from the persisted order, never from a bot message."""
    if not order or not order.get("user_id"):
        return None, None
    try:
        from database.requests import get_user_by_id

        user = get_user_by_id(int(order["user_id"]))
    except Exception as error:
        logger.warning(
            "Failed to resolve owner for order=%s user=%s: %s",
            order.get("order_id"),
            order.get("user_id"),
            error,
        )
        return None, None
    if not user:
        return None, None
    return int(user.get("telegram_id") or 0) or None, user.get("username")


def _resolve_new_key_owner(
    target,
    order: dict | None,
    *,
    owner_telegram_id: int | None = None,
    owner_username: str | None = None,
    state_data: dict | None = None,
) -> tuple[int | None, str | None]:
    """Compatibility helper for existing callers and tests."""
    state_data = state_data or {}
    telegram_id = owner_telegram_id or state_data.get("new_key_owner_telegram_id")
    username = (
        owner_username
        if owner_username is not None
        else state_data.get("new_key_owner_username")
    )
    if telegram_id:
        return int(telegram_id), username
    return _owner_from_order(order)


def _owner_user_stub(telegram_id: int | None, username: str | None):
    if not telegram_id:
        return None
    return SimpleNamespace(id=telegram_id, username=username, is_bot=False)


async def _state_data(state: FSMContext | None) -> dict[str, Any]:
    if state is None:
        return {}
    return dict(await state.get_data())


async def _update_state(state: FSMContext | None, **values: Any) -> None:
    if state is not None:
        await state.update_data(**values)


async def _clear_state(state: FSMContext | None) -> None:
    if state is not None:
        await state.clear()


async def _set_state(state: FSMContext | None, value: Any) -> None:
    if state is not None:
        await state.set_state(value)


async def _render_background_page(
    target: BackgroundKeyFlowTarget,
    page_key: str,
    *,
    context: dict[str, Any],
) -> Message | None:
    from bot.utils.background_page_delivery import send_background_page

    message = await send_background_page(
        target.bot,
        telegram_id=target.telegram_id,
        page_key=page_key,
        context=context,
    )
    if message is not None:
        target.message = message
    return message


async def _render_key_flow_page(
    target: Any,
    page_key: str,
    *,
    context: dict[str, Any] | None = None,
    force_new: bool = False,
) -> Message | None:
    render_context = dict(context or {})
    target_user = _target_user(target)
    if target_user is not None:
        render_context.setdefault("telegram_id", getattr(target_user, "id", None))
    if isinstance(target, BackgroundKeyFlowTarget):
        return await _render_background_page(target, page_key, context=render_context)

    from bot.utils.page_renderer import render_page

    render_target = target if isinstance(target, (Message, CallbackQuery)) else _target_message(target)
    if render_target is None:
        return None
    return await render_page(
        render_target,
        page_key=page_key,
        context=render_context,
        force_new=force_new or isinstance(render_target, Message) and not isinstance(target, CallbackQuery),
    )


def _base_context(result: NewKeySetupResult) -> dict[str, Any]:
    return {
        "telegram_id": result.telegram_id,
        "order_id": result.order_id,
        "key_id": result.key_id,
    }


async def run_new_key_setup_flow(
    target: Any,
    order_id: str,
    *,
    state: FSMContext | None = None,
    owner_telegram_id: int | None = None,
    owner_username: str | None = None,
    server_id: int | None = None,
    inbound_id: int | None = None,
    force_new: bool = False,
) -> NewKeySetupResult:
    """Render and execute one step using the same domain service for every source."""
    expected_telegram_id = owner_telegram_id
    if expected_telegram_id is None and isinstance(target, BackgroundKeyFlowTarget):
        expected_telegram_id = target.telegram_id
    if expected_telegram_id is None and isinstance(target, CallbackQuery):
        expected_telegram_id = target.from_user.id

    result = await resolve_new_key_setup(
        order_id,
        expected_telegram_id=expected_telegram_id,
        server_id=server_id,
        inbound_id=inbound_id,
    )
    await _update_state(
        state,
        new_key_order_id=result.order_id,
        new_key_id=result.key_id,
        new_key_owner_telegram_id=result.telegram_id or owner_telegram_id,
        new_key_owner_username=result.username or owner_username,
    )

    if result.status is NewKeySetupStatus.AWAITING_SERVER:
        from bot.states.user_states import NewKeyConfig
        from bot.utils.page_button_items import build_server_button_items

        await _set_state(state, NewKeyConfig.waiting_for_server)
        rendered = await _render_key_flow_page(
            target,
            result.page_key or "new_key_server_select",
            context={
                **_base_context(result),
                "server_button_items": build_server_button_items(
                    result.servers,
                    callback_prefix=f"new_key_server:{result.order_id}",
                ),
            },
            force_new=force_new,
        )
        if isinstance(target, BackgroundKeyFlowTarget) and rendered is None:
            return replace(
                result,
                status=NewKeySetupStatus.RETRYABLE_FAILURE,
                page_key="key_operation_failed",
                error_code="selection_delivery_failed",
            )
        return result

    if result.status is NewKeySetupStatus.AWAITING_INBOUND:
        from bot.states.user_states import NewKeyConfig
        from bot.utils.page_button_items import build_protocol_button_items

        await _set_state(state, NewKeyConfig.waiting_for_inbound)
        await _update_state(state, new_key_server_id=result.server_id)
        rendered = await _render_key_flow_page(
            target,
            result.page_key or "new_key_inbound_select",
            context={
                **_base_context(result),
                "protocol_button_items": build_protocol_button_items(
                    result.inbounds,
                    callback_prefix=(
                        f"new_key_inbound:{result.order_id}:{result.server_id}"
                    ),
                ),
                "key_flow_back_callback": f"new_key_back:{result.order_id}",
                "selected_server_name": result.server_name,
            },
            force_new=force_new,
        )
        if isinstance(target, BackgroundKeyFlowTarget) and rendered is None:
            return replace(
                result,
                status=NewKeySetupStatus.RETRYABLE_FAILURE,
                page_key="key_operation_failed",
                error_code="selection_delivery_failed",
            )
        return result

    if result.status is NewKeySetupStatus.PROVISIONING:
        await _update_state(state, new_key_server_id=result.server_id)
        progress_message = await _render_key_flow_page(
            target,
            "key_progress",
            context=_base_context(result),
            force_new=force_new,
        )
        if isinstance(target, BackgroundKeyFlowTarget) and progress_message is None:
            return replace(
                result,
                status=NewKeySetupStatus.RETRYABLE_FAILURE,
                page_key="key_operation_failed",
                error_code="progress_delivery_failed",
            )
        result = await provision_new_key(
            result,
            expected_telegram_id=expected_telegram_id,
        )
        if result.status is not NewKeySetupStatus.READY:
            await _render_key_flow_page(
                target,
                result.page_key or "key_operation_failed",
                context=_base_context(result),
                force_new=False,
            )
            return result
        target = _target_with_message(
            target,
            progress_message,
            from_user=_owner_user_stub(result.telegram_id, result.username),
        )

    if result.status is NewKeySetupStatus.READY:
        await _clear_state(state)
        delivery_target = target
        if isinstance(target, BackgroundKeyFlowTarget) and target.message is None:
            anchor = await _render_key_flow_page(
                target,
                "key_progress",
                context=_base_context(result),
            )
            if anchor is None:
                return replace(
                    result,
                    status=NewKeySetupStatus.RETRYABLE_FAILURE,
                    page_key="key_operation_failed",
                    error_code="key_delivery_failed",
                )
            delivery_target = _target_with_message(target, anchor)
        if _target_message(delivery_target) is not None:
            from bot.utils.key_sender import KeyDeliveryError, send_key_with_qr

            try:
                await send_key_with_qr(
                    delivery_target,
                    dict(result.key_data or {}),
                    is_new=True,
                    order_id=result.order_id,
                    raise_on_error=True,
                )
            except KeyDeliveryError as error:
                logger.warning(
                    "Configured key delivery failed order=%s key=%s: %s",
                    result.order_id,
                    result.key_id,
                    error,
                )
                return replace(
                    result,
                    status=NewKeySetupStatus.RETRYABLE_FAILURE,
                    page_key="key_delivery_failed",
                    error_code="key_delivery_failed",
                    error=str(error),
                )
        return result

    await _render_key_flow_page(
        target,
        result.page_key or "key_operation_failed",
        context=_base_context(result),
        force_new=force_new,
    )
    return result


async def start_new_key_config(
    message: Message,
    state: FSMContext | None,
    order_id: str,
    key_id: int | None = None,
    owner_telegram_id: int | None = None,
    owner_username: str | None = None,
) -> NewKeySetupResult:
    """Compatibility entry that now delegates to the shared state machine."""
    del key_id
    return await run_new_key_setup_flow(
        message,
        order_id,
        state=state,
        owner_telegram_id=owner_telegram_id,
        owner_username=owner_username,
        force_new=True,
    )


async def start_new_key_config_background(
    bot: Any,
    *,
    telegram_id: int,
    username: str | None,
    order_id: str,
    anchor_message: Message | None = None,
) -> NewKeySetupResult:
    """Background transport adapter for the same key setup state machine."""
    target = BackgroundKeyFlowTarget(
        bot=bot,
        telegram_id=int(telegram_id),
        username=username,
        message=anchor_message,
    )
    return await run_new_key_setup_flow(
        target,
        order_id,
        owner_telegram_id=int(telegram_id),
        owner_username=username,
        force_new=True,
    )


def _parse_positive_int(value: str | None) -> int | None:
    try:
        parsed = int(str(value or ""))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


async def _legacy_order_id(state: FSMContext) -> str | None:
    data = await _state_data(state)
    value = str(data.get("new_key_order_id") or "").strip()
    return value or None


@router.callback_query(F.data.startswith("new_key_server:"))
async def process_new_key_server_selection(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Select a server using an order-bound callback or the legacy FSM payload."""
    parts = str(callback.data or "").split(":")
    if len(parts) == 3:
        order_id = parts[1]
        server_id = _parse_positive_int(parts[2])
    elif len(parts) == 2:
        order_id = await _legacy_order_id(state)
        server_id = _parse_positive_int(parts[1])
    else:
        order_id = None
        server_id = None
    if not order_id or not server_id:
        await _render_key_flow_page(
            callback,
            "payment_order_unavailable",
            context={"telegram_id": callback.from_user.id},
        )
        await callback.answer()
        return
    await run_new_key_setup_flow(
        callback,
        order_id,
        state=state,
        owner_telegram_id=callback.from_user.id,
        server_id=server_id,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("new_key_inbound:"))
async def process_new_key_inbound_selection(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Select an inbound using an order-bound callback or legacy FSM state."""
    parts = str(callback.data or "").split(":")
    if len(parts) == 4:
        order_id = parts[1]
        server_id = _parse_positive_int(parts[2])
        inbound_id = _parse_positive_int(parts[3])
    elif len(parts) == 2:
        data = await _state_data(state)
        order_id = str(data.get("new_key_order_id") or "").strip() or None
        server_id = _parse_positive_int(data.get("new_key_server_id"))
        inbound_id = _parse_positive_int(parts[1])
    else:
        order_id = None
        server_id = None
        inbound_id = None
    if not order_id or not server_id or not inbound_id:
        await _render_key_flow_page(
            callback,
            "payment_order_unavailable",
            context={"telegram_id": callback.from_user.id},
        )
        await callback.answer()
        return
    await run_new_key_setup_flow(
        callback,
        order_id,
        state=state,
        owner_telegram_id=callback.from_user.id,
        server_id=server_id,
        inbound_id=inbound_id,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("new_key_back:"))
@router.callback_query(F.data == "back_to_server_select")
async def back_to_server_select(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Return to the order-bound server selection step."""
    if str(callback.data or "").startswith("new_key_back:"):
        order_id = str(callback.data).split(":", 1)[1].strip() or None
    else:
        order_id = await _legacy_order_id(state)
    if not order_id:
        await _render_key_flow_page(
            callback,
            "payment_order_unavailable",
            context={"telegram_id": callback.from_user.id},
        )
        await callback.answer()
        return
    await run_new_key_setup_flow(
        callback,
        order_id,
        state=state,
        owner_telegram_id=callback.from_user.id,
    )
    await callback.answer()


async def process_new_key_subscription_final(
    target: Any,
    state: FSMContext,
    server_id: int,
) -> NewKeySetupResult:
    """Legacy callable adapter; provisioning remains in the shared service."""
    data = await _state_data(state)
    order_id = str(data.get("new_key_order_id") or "").strip()
    return await run_new_key_setup_flow(
        target,
        order_id,
        state=state,
        owner_telegram_id=data.get("new_key_owner_telegram_id"),
        server_id=server_id,
    )


async def process_new_key_final(
    target: Any,
    state: FSMContext,
    server_id: int,
    inbound_id: int,
) -> NewKeySetupResult:
    """Legacy callable adapter; provisioning remains in the shared service."""
    data = await _state_data(state)
    order_id = str(data.get("new_key_order_id") or "").strip()
    return await run_new_key_setup_flow(
        target,
        order_id,
        state=state,
        owner_telegram_id=data.get("new_key_owner_telegram_id"),
        server_id=server_id,
        inbound_id=inbound_id,
    )


__all__ = [
    "BackgroundKeyFlowTarget",
    "back_to_server_select",
    "process_new_key_final",
    "process_new_key_inbound_selection",
    "process_new_key_server_selection",
    "process_new_key_subscription_final",
    "run_new_key_setup_flow",
    "start_new_key_config",
    "start_new_key_config_background",
]
