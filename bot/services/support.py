import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from config import ADMIN_IDS
from database.requests import (
    get_support_admin_notifications,
    get_support_claim_cleanup_mode,
    mark_support_admin_notifications_inactive,
    save_support_admin_notification_delivery,
    set_support_message_delivery,
)
from bot.keyboards.support import admin_support_reply_kb
from bot.utils.page_renderer import (
    PreparedPageRender,
    prepare_page_render,
)
from bot.utils.text import escape_html, get_message_text_for_storage, send_media_or_text

logger = logging.getLogger(__name__)

SUPPORTED_SUPPORT_MEDIA_TYPES = {"text", "photo", "video", "animation"}
SUPPORT_REPLY_PAGE_KEY = "support_reply"


@dataclass
class _SupportThreadLock:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


_SUPPORT_THREAD_LOCKS: Dict[int, _SupportThreadLock] = {}


@asynccontextmanager
async def support_thread_operation(thread_id: int):
    """Serializes message writes/delivery and status changes for one thread."""
    if isinstance(thread_id, bool) or not isinstance(thread_id, int) or thread_id <= 0:
        raise ValueError("thread_id must be a positive integer")
    entry = _SUPPORT_THREAD_LOCKS.setdefault(thread_id, _SupportThreadLock())
    entry.users += 1
    try:
        async with entry.lock:
            yield
    finally:
        entry.users -= 1
        if entry.users == 0 and _SUPPORT_THREAD_LOCKS.get(thread_id) is entry:
            _SUPPORT_THREAD_LOCKS.pop(thread_id, None)


def extract_support_payload(message: Message) -> Optional[Dict[str, Any]]:
    """Retrieves a supported support message without downloading files."""
    media_type = "text"
    media_file_id = None

    if message.animation:
        media_type = "animation"
        media_file_id = message.animation.file_id
    elif message.video:
        media_type = "video"
        media_file_id = message.video.file_id
    elif message.photo:
        media_type = "photo"
        media_file_id = message.photo[-1].file_id
    elif message.text:
        media_type = "text"
    else:
        return None

    text_html = get_message_text_for_storage(message, "html")
    return {
        "media_type": media_type,
        "media_file_id": media_file_id,
        "text_html": text_html,
        "source_chat_id": message.chat.id,
        "source_message_id": message.message_id,
    }


def support_unsupported_text() -> str:
    return (
        "❌ <b>Формат не поддерживается</b>\n\n"
        "Отправьте текст, фото, видео или GIF."
    )


def format_support_user_line(user: Dict[str, Any]) -> str:
    """Generates a short username for support cards."""
    username = user.get("username")
    if username:
        return f"@{escape_html(username)}"

    parts = []
    if user.get("first_name"):
        parts.append(str(user["first_name"]))
    if user.get("last_name"):
        parts.append(str(user["last_name"]))
    if parts:
        return escape_html(" ".join(parts))
    return f"ID: {user['telegram_id']}" if user.get('telegram_id') is not None else f"Аккаунт: {user['id']}"


def support_identity_line(thread):
    """Show the real identity without inventing a Telegram account."""
    if thread.get('user_telegram_id') is not None:
        return f"📱 Telegram ID: <code>{thread['user_telegram_id']}</code>"
    return f"🌐 Аккаунт сайта: <code>{thread['user_id']}</code>"


def format_admin_support_card(
    *,
    title: str,
    thread: Dict[str, Any],
    user: Dict[str, Any],
    assigned_admin_id: Optional[int],
) -> str:
    """A request card that is sent to the admin after a copy of the message."""
    lines = [
        f"💬 <b>{escape_html(title)}</b>",
        "",
        f"👤 Пользователь: {format_support_user_line(user)}",
        support_identity_line(thread),
        f"🧵 Диалог: <code>{thread['id']}</code>",
    ]
    if assigned_admin_id:
        lines.append(f"👨‍💻 Закреплён за: <code>{assigned_admin_id}</code>")
    else:
        lines.append("👨‍💻 Диалог пока не закреплён. Первый ответ закрепит его за вами.")
    lines.extend(["", "Выше сообщение пользователя."])
    return "\n".join(lines)


async def copy_support_message(
    bot: Bot,
    *,
    chat_id: int,
    source_message: Message,
    reply_markup=None,
) -> Optional[int]:
    """Copies the original message and returns the ID of the copy."""
    result = await bot.copy_message(
        chat_id=chat_id,
        from_chat_id=source_message.chat.id,
        message_id=source_message.message_id,
        reply_markup=reply_markup,
    )
    return getattr(result, "message_id", None)


async def send_user_message_to_admins(
    bot: Bot,
    *,
    thread: Dict[str, Any],
    user: Dict[str, Any],
    source_message: Message,
) -> Dict[str, int]:
    """
    Sends a user message to admins.

    For an unpinned chain, sends to all admins and saves notifications
    for subsequent cleaning of the buttons. For assigned - only to the assigned admin.
    """
    assigned_admin_id = thread.get("assigned_admin_id")
    if assigned_admin_id:
        recipients = [int(assigned_admin_id)]
        title = "Ответ пользователя"
        save_notification = False
    else:
        recipients = [int(admin_id) for admin_id in ADMIN_IDS]
        title = "Новое обращение в поддержку"
        save_notification = True

    sent = 0
    failed = 0
    for admin_id in recipients:
        try:
            copy_message_id = await copy_support_message(
                bot,
                chat_id=admin_id,
                source_message=source_message,
            )
            card = await send_media_or_text(
                bot,
                chat_id=admin_id,
                text=format_admin_support_card(
                    title=title,
                    thread=thread,
                    user=user,
                    assigned_admin_id=assigned_admin_id,
                ),
                reply_markup=admin_support_reply_kb(thread["id"]),
            )
            if save_notification:
                from database.requests import record_support_admin_notification

                record_support_admin_notification(
                    thread["id"],
                    admin_id,
                    card_message_id=getattr(card, "message_id", None),
                    copy_message_id=copy_message_id,
                )
            sent += 1
        except Exception as e:
            logger.warning(
                "Не удалось отправить сообщение поддержки админу %s по цепочке %s: %s",
                admin_id,
                thread["id"],
                e,
            )
            failed += 1

    return {"sent": sent, "failed": failed}


async def send_generated_user_message_to_admins(
    bot: Bot,
    *,
    thread: Dict[str, Any],
    user: Dict[str, Any],
    support_message_id: int,
    text_html: str,
    _respect_assignment: bool = False,
) -> Dict[str, int | str]:
    """Delivers an extension-origin user message without duplicating completed sends."""
    existing = get_support_admin_notifications(
        int(thread["id"]),
        active_only=False,
        support_message_id=int(support_message_id),
    )
    deliveries = {
        int(item["admin_telegram_id"]): item
        for item in existing
    }
    assigned = thread.get('assigned_admin_id') if _respect_assignment else None
    recipients = [int(assigned)] if assigned else sorted({int(admin_id) for admin_id in ADMIN_IDS})
    sent = 0
    failed = 0

    for admin_id in recipients:
        delivery = deliveries.get(admin_id, {})
        copy_message_id = delivery.get("copy_message_id")
        card_message_id = delivery.get("card_message_id")
        try:
            if not copy_message_id:
                delivered = await send_media_or_text(
                    bot,
                    chat_id=admin_id,
                    text=text_html,
                )
                copy_message_id = getattr(delivered, "message_id", None)
                delivery = save_support_admin_notification_delivery(
                    int(thread["id"]),
                    admin_id,
                    support_message_id=int(support_message_id),
                    copy_message_id=copy_message_id,
                )

            if not card_message_id:
                card = await send_media_or_text(
                    bot,
                    chat_id=admin_id,
                    text=format_admin_support_card(
                        title="Новое обращение в поддержку",
                        thread=thread,
                        user=user,
                        assigned_admin_id=assigned,
                    ),
                    reply_markup=admin_support_reply_kb(int(thread["id"])),
                )
                card_message_id = getattr(card, "message_id", None)
                delivery = save_support_admin_notification_delivery(
                    int(thread["id"]),
                    admin_id,
                    support_message_id=int(support_message_id),
                    card_message_id=card_message_id,
                    copy_message_id=copy_message_id,
                )

            if delivery.get("copy_message_id") and delivery.get("card_message_id"):
                sent += 1
            else:
                failed += 1
        except Exception as exc:
            logger.warning(
                "Failed to deliver generated support message %s to admin %s: %s",
                support_message_id,
                admin_id,
                exc,
            )
            failed += 1

    return {
        "sent": sent,
        "failed": failed,
        "status": _delivery_status(sent=sent, failed=failed),
    }


async def _prepare_support_reply_markup(
    bot: Bot,
    *,
    thread_id: int,
    user_telegram_id: int,
):
    prepared = await prepare_page_render(
        bot,
        SUPPORT_REPLY_PAGE_KEY,
        context={
            "support_thread_id": thread_id,
            "telegram_id": user_telegram_id,
        },
    )
    if isinstance(prepared, PreparedPageRender) and prepared.page_key == SUPPORT_REPLY_PAGE_KEY:
        return prepared.reply_markup
    return None


async def send_admin_message_to_user(
    bot: Bot,
    *,
    thread: Dict[str, Any],
    source_message: Message,
) -> Optional[int]:
    """Sends a copy of the admin message to the user with a reply button."""
    if thread.get('channel') == 'web' or thread.get('user_telegram_id') is None:
        return None
    thread_id = int(thread["id"])
    user_telegram_id = int(thread["user_telegram_id"])
    reply_markup = await _prepare_support_reply_markup(
        bot,
        thread_id=thread_id,
        user_telegram_id=user_telegram_id,
    )

    return await copy_support_message(
        bot,
        chat_id=user_telegram_id,
        source_message=source_message,
        reply_markup=reply_markup,
    )


async def send_generated_admin_message_to_user(
    bot: Bot,
    *,
    thread: Dict[str, Any],
    message: Dict[str, Any],
) -> Dict[str, int | str]:
    """Delivers one extension-origin admin-side message to its support user."""
    if thread.get('channel') == 'web' or thread.get('user_telegram_id') is None:
        return {'sent': 0, 'failed': 0, 'status': 'not_sent'}
    delivered_message_id = message.get("delivered_message_id")
    if delivered_message_id:
        return {"sent": 1, "failed": 0, "status": "sent"}

    thread_id = int(thread["id"])
    user_telegram_id = int(thread["user_telegram_id"])
    reply_markup = await _prepare_support_reply_markup(
        bot,
        thread_id=thread_id,
        user_telegram_id=user_telegram_id,
    )
    try:
        delivered = await send_media_or_text(
            bot,
            chat_id=user_telegram_id,
            text=str(message.get("text_html") or ""),
            reply_markup=reply_markup,
        )
        telegram_message_id = getattr(delivered, "message_id", None)
        if telegram_message_id is None:
            return {"sent": 0, "failed": 1, "status": "failed"}
        set_support_message_delivery(int(message["id"]), int(telegram_message_id))
        return {"sent": 1, "failed": 0, "status": "sent"}
    except Exception as exc:
        logger.warning(
            "Failed to deliver generated support message %s to user %s: %s",
            message.get("id"),
            user_telegram_id,
            exc,
        )
        return {"sent": 0, "failed": 1, "status": "failed"}


def _delivery_status(*, sent: int, failed: int) -> str:
    if sent and failed:
        return "partial"
    if sent:
        return "sent"
    return "failed"


async def cleanup_claimed_admin_notifications(
    bot: Bot,
    *,
    thread_id: int,
    claimed_admin_id: int,
) -> None:
    """Removes the reply or message button from admins who did not accept the dialogue."""
    notifications = get_support_admin_notifications(
        thread_id,
        exclude_admin_id=claimed_admin_id,
        active_only=True,
    )
    if not notifications:
        return

    mode = get_support_claim_cleanup_mode()
    affected_admin_ids = []

    for notification in notifications:
        admin_id = int(notification["admin_telegram_id"])
        affected_admin_ids.append(admin_id)
        card_message_id = notification.get("card_message_id")
        copy_message_id = notification.get("copy_message_id")

        if mode == "delete_message":
            for message_id in (card_message_id, copy_message_id):
                if not message_id:
                    continue
                try:
                    await bot.delete_message(chat_id=admin_id, message_id=int(message_id))
                except TelegramBadRequest as e:
                    logger.debug(
                        "Не удалось удалить уведомление поддержки %s у админа %s: %s",
                        message_id,
                        admin_id,
                        e,
                    )
                except Exception as e:
                    logger.warning(
                        "Ошибка удаления уведомления поддержки %s у админа %s: %s",
                        message_id,
                        admin_id,
                        e,
                    )
            continue

        if card_message_id:
            try:
                await bot.edit_message_reply_markup(
                    chat_id=admin_id,
                    message_id=int(card_message_id),
                    reply_markup=None,
                )
            except TelegramBadRequest as e:
                logger.debug(
                    "Не удалось убрать кнопку поддержки у админа %s: %s",
                    admin_id,
                    e,
                )
            except Exception as e:
                logger.warning(
                    "Ошибка очистки кнопки поддержки у админа %s: %s",
                    admin_id,
                    e,
                )

    mark_support_admin_notifications_inactive(thread_id, sorted(set(affected_admin_ids)))
