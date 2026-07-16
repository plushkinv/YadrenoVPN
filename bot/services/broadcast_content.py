"""Broadcast draft persistence and Telegram-native poll delivery helpers."""

from __future__ import annotations

import json
import inspect
from dataclasses import dataclass
from typing import Any, Optional

from aiogram import Bot
from aiogram.types import InputPollOption, Message, Poll

from bot.services.broadcast_validation import (
    validate_broadcast_message,
    validate_generated_poll,
)
from database.requests import get_setting, set_broadcast_content_with_revision


BROADCAST_CONTENT_SETTING = "broadcast_message"
BROADCAST_KIND_MESSAGE = "message"
BROADCAST_KIND_POLL = "poll"
POLL_MODE_CLEAN = "clean"
POLL_MODE_PRESERVE = "preserve"
POLL_SOURCE_GENERATED = "generated"
_SEND_POLL_SUPPORTS_CORRECT_OPTION_IDS = (
    "correct_option_ids" in inspect.signature(Bot.send_poll).parameters
)


class BroadcastContentError(ValueError):
    """Raised when a broadcast draft can't be prepared or delivered."""


@dataclass(frozen=True)
class PollDeliveryReference:
    """Telegram message used as the common source for all poll forwards."""

    chat_id: int
    message_id: int
    can_close: bool


def _message_id(result: Any) -> int:
    value = getattr(result, "message_id", None)
    if value is None:
        raise BroadcastContentError("Telegram не вернул идентификатор сообщения опроса.")
    return int(value)


def normalize_broadcast_content(data: Any) -> Optional[dict[str, Any]]:
    """Normalizes saved content and keeps legacy text/photo JSON compatible."""
    if not isinstance(data, dict):
        return None

    kind = data.get("kind")
    if kind is None and "text" in data:
        normalized = dict(data)
        normalized["kind"] = BROADCAST_KIND_MESSAGE
        return normalized

    if kind == BROADCAST_KIND_MESSAGE:
        return dict(data)

    if kind != BROADCAST_KIND_POLL:
        return None

    if data.get("poll_source") == POLL_SOURCE_GENERATED:
        try:
            return validate_generated_poll(data)
        except ValueError:
            return None

    try:
        normalized = dict(data)
        normalized["draft_chat_id"] = int(data["draft_chat_id"])
        normalized["draft_message_id"] = int(data["draft_message_id"])
    except (KeyError, TypeError, ValueError):
        return None

    if normalized.get("delivery_mode") not in {POLL_MODE_CLEAN, POLL_MODE_PRESERVE}:
        return None
    return normalized


def load_broadcast_content() -> Optional[dict[str, Any]]:
    """Loads the current broadcast draft from settings."""
    raw = get_setting(BROADCAST_CONTENT_SETTING)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return normalize_broadcast_content(data)


def save_broadcast_content(data: dict[str, Any]) -> None:
    """Persists normalized broadcast content in the existing setting."""
    normalized = normalize_broadcast_content(data)
    if normalized is None:
        raise BroadcastContentError("Некорректные данные черновика рассылки.")
    if normalized.get("kind") == BROADCAST_KIND_MESSAGE:
        normalized["text"] = validate_broadcast_message(
            normalized.get("text"),
            has_photo=bool(normalized.get("photo_file_id")),
        )
    set_broadcast_content_with_revision(
        json.dumps(normalized, ensure_ascii=False, separators=(",", ":")),
    )


def save_message_content(text: str, photo_file_id: Optional[str] = None) -> None:
    """Saves a text or photo broadcast draft."""
    save_broadcast_content({
        "kind": BROADCAST_KIND_MESSAGE,
        "text": text,
        "photo_file_id": photo_file_id,
    })


def is_broadcast_content_ready(content: Optional[dict[str, Any]]) -> bool:
    """Returns whether the stored draft has enough data for delivery."""
    if not content:
        return False
    if content.get("kind") == BROADCAST_KIND_POLL:
        if content.get("poll_source") == POLL_SOURCE_GENERATED:
            return bool(content.get("question") and content.get("options"))
        return bool(content.get("draft_chat_id") and content.get("draft_message_id"))
    return bool(content.get("text"))


def poll_metadata(poll: Poll) -> dict[str, Any]:
    """Returns the stable poll fields needed by the admin interface."""
    return {
        "question": poll.question,
        "poll_type": poll.type,
        "is_anonymous": bool(poll.is_anonymous),
        "allows_multiple_answers": bool(poll.allows_multiple_answers),
        "total_voter_count": int(poll.total_voter_count),
    }


def validate_poll_message(message: Message) -> Optional[str]:
    """Returns a user-facing validation error for an imported poll."""
    poll = message.poll
    if poll is None:
        return (
            "❌ <b>Материал не поддерживается</b>\n\n"
            "Отправьте текст, фото с подписью или нативный опрос Telegram."
        )
    if message.has_protected_content:
        return (
            "❌ <b>Опрос защищён</b>\n\n"
            "Создайте новый опрос без защиты от копирования и пересылки."
        )
    if poll.is_closed:
        return (
            "❌ <b>Опрос уже закрыт</b>\n\n"
            "Закрытый опрос нельзя подготовить для новой рассылки. Создайте открытый опрос."
        )
    return None


async def create_poll_draft(
    bot: Bot,
    *,
    source_chat_id: int,
    source_message_id: int,
    target_chat_id: int,
    metadata: dict[str, Any],
    delivery_mode: str,
) -> dict[str, Any]:
    """Creates a stable bot-side poll draft and returns its persisted payload."""
    if delivery_mode == POLL_MODE_CLEAN:
        result = await bot.copy_message(
            chat_id=target_chat_id,
            from_chat_id=source_chat_id,
            message_id=source_message_id,
        )
    elif delivery_mode == POLL_MODE_PRESERVE:
        result = await bot.forward_message(
            chat_id=target_chat_id,
            from_chat_id=source_chat_id,
            message_id=source_message_id,
        )
    else:
        raise BroadcastContentError("Неизвестный режим подготовки опроса.")

    content = {
        "kind": BROADCAST_KIND_POLL,
        "delivery_mode": delivery_mode,
        "draft_chat_id": int(target_chat_id),
        "draft_message_id": _message_id(result),
        **metadata,
    }
    save_broadcast_content(content)
    return content


async def preview_poll(bot: Bot, content: dict[str, Any], *, chat_id: int) -> int:
    """Forwards the stable draft as a live common poll preview."""
    if content.get("poll_source") == POLL_SOURCE_GENERATED:
        result = await bot.send_poll(chat_id=chat_id, **_generated_poll_kwargs(content))
        return _message_id(result)
    result = await bot.forward_message(
        chat_id=chat_id,
        from_chat_id=int(content["draft_chat_id"]),
        message_id=int(content["draft_message_id"]),
    )
    return _message_id(result)


async def prepare_poll_delivery(
    bot: Bot,
    content: dict[str, Any],
    *,
    master_chat_id: Optional[int] = None,
) -> PollDeliveryReference:
    """Creates a clean launch master or reuses the preserved common poll."""
    if content.get("poll_source") == POLL_SOURCE_GENERATED:
        if master_chat_id is None:
            raise BroadcastContentError("Для запуска нового опроса нужен чат администратора.")
        result = await bot.send_poll(
            chat_id=int(master_chat_id),
            **_generated_poll_kwargs(content),
        )
        return PollDeliveryReference(
            chat_id=int(master_chat_id),
            message_id=_message_id(result),
            can_close=True,
        )

    chat_id = int(content["draft_chat_id"])
    message_id = int(content["draft_message_id"])

    if content.get("delivery_mode") == POLL_MODE_CLEAN:
        result = await bot.copy_message(
            chat_id=chat_id,
            from_chat_id=chat_id,
            message_id=message_id,
        )
        return PollDeliveryReference(
            chat_id=chat_id,
            message_id=_message_id(result),
            can_close=True,
        )

    result = await bot.forward_message(
        chat_id=chat_id,
        from_chat_id=chat_id,
        message_id=message_id,
    )
    return PollDeliveryReference(
        chat_id=chat_id,
        message_id=_message_id(result),
        can_close=False,
    )


async def send_poll_to_recipient(
    bot: Bot,
    reference: PollDeliveryReference,
    *,
    chat_id: int,
) -> None:
    """Forwards the same poll master so every recipient shares its results."""
    await bot.forward_message(
        chat_id=chat_id,
        from_chat_id=reference.chat_id,
        message_id=reference.message_id,
    )


def _generated_poll_kwargs(content: dict[str, Any]) -> dict[str, Any]:
    """Build aiogram send_poll kwargs from a validated generated payload."""
    normalized = validate_generated_poll(content)
    kwargs: dict[str, Any] = {
        "question": normalized["question"],
        "options": [InputPollOption(text=option) for option in normalized["options"]],
        "type": normalized["poll_type"],
        "is_anonymous": normalized["is_anonymous"],
        "allows_multiple_answers": normalized["allows_multiple_answers"],
    }
    if normalized["poll_type"] == "quiz":
        correct_option_id = normalized["correct_option_id"]
        if _SEND_POLL_SUPPORTS_CORRECT_OPTION_IDS:
            kwargs["correct_option_ids"] = [correct_option_id]
        else:
            kwargs["correct_option_id"] = correct_option_id
        if normalized.get("explanation"):
            kwargs["explanation"] = normalized["explanation"]
    return kwargs


def poll_type_label(content: dict[str, Any]) -> str:
    """Returns a concise Russian poll type label for confirmation screens."""
    if content.get("poll_type") == "quiz":
        return "Викторина"
    if content.get("allows_multiple_answers"):
        return "Опрос с несколькими ответами"
    return "Обычный опрос"
