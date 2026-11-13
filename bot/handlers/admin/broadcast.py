"""
Handlers for the broadcast section in the admin panel.

Functional:
- Sending messages and Telegram-native polls with recipient filters
- Setting up auto-notifications about key expiration
"""

from localization import text as _tr
import asyncio
import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest

from database.requests import (
    get_setting, set_setting,
    get_users_for_broadcast, count_users_for_broadcast,
    mark_user_bot_blocked, set_broadcast_filters_with_revision,
    BroadcastFilterError, normalize_broadcast_filters,
)
from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.utils.placeholder_help import BROADCAST_PLACEHOLDER_HINT, EDITOR_PLACEHOLDER_HELP
from bot.keyboards.admin import (
    broadcast_main_kb, broadcast_confirm_kb,
    broadcast_stop_kb, broadcast_notifications_kb, broadcast_back_kb,
    broadcast_notify_back_kb, broadcast_poll_mode_kb, broadcast_result_kb,
    home_only_kb,
    BROADCAST_FILTERS
)

logger = logging.getLogger(__name__)

from bot.utils.text import escape_html, safe_edit_or_send
from bot.utils.delivery import is_bot_blocked_error
from bot.utils.event_placeholders import render_event_message_text
from bot.services.broadcast_content import (
    BROADCAST_KIND_POLL,
    POLL_MODE_CLEAN,
    POLL_MODE_PRESERVE,
    create_poll_draft,
    is_broadcast_content_ready,
    load_broadcast_content,
    poll_metadata,
    poll_type_label,
    prepare_poll_delivery,
    preview_poll,
    save_message_content,
    send_poll_to_recipient,
    validate_poll_message,
)
from bot.services.broadcast_audience import broadcast_filter_status
from bot.services.broadcast_editor import (
    BroadcastEditorError,
    broadcast_material_hash,
    consume_valid_broadcast_confirmation,
    create_broadcast_confirmation,
    load_broadcast_style_profile,
    style_profile_summary,
)
from bot.services.broadcast_validation import BroadcastValidationError

router = Router()

BROADCAST_IN_PROGRESS_KEY = 'broadcast_in_progress'
BROADCAST_STOP_REQUESTED_KEY = 'broadcast_stop_requested'
BROADCAST_STOP_REQUESTED = 'stop_requested'
BROADCAST_STALE_RESET = 'stale_reset'
BROADCAST_IDLE = 'idle'

_broadcast_runtime_active = False
_broadcast_state_lock = asyncio.Lock()


# ============================================================================
# AUXILIARY FUNCTIONS
# ============================================================================




def get_broadcast_message() -> dict | None:
    """
    Receives a saved message for distribution.
    
    Returns:
        Normalized text/photo or poll payload, or None.
    """
    return load_broadcast_content()


def save_broadcast_message(text: str, photo_file_id: str | None = None) -> None:
    """Saves the message for distribution."""
    save_message_content(text, photo_file_id)


async def render_broadcast_message_text(text: str, telegram_id: int | None, *, bot: Bot) -> str:
    """Renders the mailing text in the event context of a specific recipient."""
    return await render_event_message_text(text, 'broadcast', bot=bot, telegram_id=telegram_id)


def get_broadcast_filters() -> tuple[str, ...]:
    """Load and validate the canonical working audience selection."""
    return normalize_broadcast_filters(
        get_setting('broadcast_filter'),
        allow_legacy=False,
    )


def is_broadcast_in_progress() -> bool:
    """Checks whether the mailing is currently in progress."""
    return get_setting(BROADCAST_IN_PROGRESS_KEY, '0') == '1'


def set_broadcast_in_progress(value: bool) -> None:
    """Sets the broadcast flag."""
    set_setting(BROADCAST_IN_PROGRESS_KEY, '1' if value else '0')


def is_broadcast_stop_requested() -> bool:
    """Checks whether the administrator has requested to stop the current distribution."""
    return get_setting(BROADCAST_STOP_REQUESTED_KEY, '0') == '1'


def set_broadcast_stop_requested(value: bool) -> None:
    """Sets the soft stop flag for broadcasting."""
    set_setting(BROADCAST_STOP_REQUESTED_KEY, '1' if value else '0')


def is_broadcast_runtime_active() -> bool:
    """Returns True if the mailing loop is alive in the current bot process."""
    return _broadcast_runtime_active


def _set_broadcast_runtime_active(value: bool) -> None:
    """Updates the in-memory flag of live mailing."""
    global _broadcast_runtime_active
    _broadcast_runtime_active = value


def reset_broadcast_state() -> None:
    """Resets all distribution status flags."""
    set_broadcast_in_progress(False)
    set_broadcast_stop_requested(False)


async def try_mark_broadcast_started() -> bool:
    """Atomically reserves the right to launch a newsletter."""
    async with _broadcast_state_lock:
        if is_broadcast_runtime_active() or is_broadcast_in_progress():
            return False

        _set_broadcast_runtime_active(True)
        set_broadcast_in_progress(True)
        set_broadcast_stop_requested(False)
        return True


async def finish_broadcast_state() -> None:
    """Removes the runtime flag and resets the flags after the broadcast is completed."""
    async with _broadcast_state_lock:
        _set_broadcast_runtime_active(False)
        reset_broadcast_state()


async def request_broadcast_stop_or_reset() -> str:
    """
    Requests to stop live broadcasting or resets a stuck DB flag.

    Returns:
        One of BROADCAST_STOP_REQUESTED, BROADCAST_STALE_RESET, BROADCAST_IDLE.
    """
    async with _broadcast_state_lock:
        if is_broadcast_runtime_active():
            set_broadcast_stop_requested(True)
            return BROADCAST_STOP_REQUESTED

        if is_broadcast_in_progress() or is_broadcast_stop_requested():
            reset_broadcast_state()
            return BROADCAST_STALE_RESET

        return BROADCAST_IDLE


def get_broadcast_menu_text(in_progress: bool = False) -> str:
    """Generates the text of the main mailing screen."""
    text = (
        _tr('broadcast.get_broadcast_menu_text.1')
    )

    if in_progress:
        text += _tr('broadcast.get_broadcast_menu_text.2')

    return text


async def render_broadcast_menu(
    message: Message,
    current_filters: object | None = None,
    force_new: bool = False,
) -> None:
    """Shows the current mailing main screen."""
    msg_data = get_broadcast_message()
    has_message = is_broadcast_content_ready(msg_data)
    filter_error = False
    try:
        selected_filters = (
            get_broadcast_filters()
            if current_filters is None
            else normalize_broadcast_filters(current_filters)
        )
    except BroadcastFilterError:
        logger.exception("Stored broadcast filter selection is invalid")
        selected_filters = ()
        filter_error = True
    in_progress = is_broadcast_in_progress()
    user_count = (
        0
        if filter_error
        else count_users_for_broadcast(selected_filters)
    )
    if not msg_data:
        material_label = _tr('broadcast.render_broadcast_menu.1')
    elif msg_data.get("kind") == BROADCAST_KIND_POLL:
        material_label = poll_type_label(msg_data)
    elif msg_data.get("photo_file_id"):
        material_label = _tr('broadcast.render_broadcast_menu.2')
    else:
        material_label = _tr('broadcast.render_broadcast_menu.3')
    style_summary = style_profile_summary(load_broadcast_style_profile())
    filter_status = (
        _tr('broadcast.render_broadcast_menu.4')
        if filter_error
        else broadcast_filter_status(selected_filters)
    )
    menu_text = (
        get_broadcast_menu_text(in_progress)
        + _tr('broadcast.render_broadcast_menu.5')
        + _tr('broadcast.render_broadcast_menu.6', p0=f'{escape_html(material_label)}')
        + f"• {escape_html(filter_status)}\n"
        + _tr('broadcast.render_broadcast_menu.7', p0=f'{user_count}')
        + _tr('broadcast.render_broadcast_menu.8', p0=f'{escape_html(style_summary)}')
        + _tr('broadcast.render_broadcast_menu.9')
    )

    await safe_edit_or_send(
        message,
        menu_text,
        reply_markup=broadcast_main_kb(
            has_message,
            selected_filters,
            in_progress,
            user_count,
            content_kind=msg_data.get('kind') if msg_data else None,
        ),
        force_new=force_new,
    )


# ============================================================================
# MAIN NEWSLETTER SCREEN
# ============================================================================

@router.callback_query(F.data == "admin_broadcast")
async def show_broadcast_menu(callback: CallbackQuery, state: FSMContext):
    """Shows the main screen of the mailing section."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    await state.update_data(broadcast_pending_poll=None)
    await state.set_state(AdminStates.broadcast_menu)
    await render_broadcast_menu(callback.message)
    await callback.answer()


@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    """An empty handler for the separator."""
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    await callback.answer()


# ============================================================================
# EDITING A MESSAGE
# ============================================================================

@router.callback_query(F.data == "broadcast_edit_message")
async def broadcast_edit_message(callback: CallbackQuery, state: FSMContext):
    """Starts editing the message for distribution."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    await state.update_data(broadcast_pending_poll=None)
    await state.set_state(AdminStates.broadcast_waiting_message)
    
    text = (
        _tr('broadcast.broadcast_edit_message.1', p0=f'{BROADCAST_PLACEHOLDER_HINT}')
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=broadcast_back_kb()
    )
    await callback.answer()


@router.message(AdminStates.broadcast_waiting_message)
async def broadcast_save_message(message: Message, state: FSMContext, bot: Bot):
    """Saves the message for distribution."""
    if not is_admin(message.from_user.id):
        return

    await state.update_data(broadcast_pending_poll=None)
    from bot.utils.text import get_message_text_for_storage, safe_edit_or_send
    
    text = None
    photo_file_id = None

    if message.poll:
        validation_error = validate_poll_message(message)
        if validation_error:
            await safe_edit_or_send(
                message,
                validation_error,
                reply_markup=broadcast_back_kb(),
                force_new=True,
            )
            return

        metadata = poll_metadata(message.poll)
        if metadata["total_voter_count"] > 0:
            await state.update_data(
                broadcast_pending_poll={
                    "source_chat_id": message.chat.id,
                    "source_message_id": message.message_id,
                    "metadata": metadata,
                }
            )
            await safe_edit_or_send(
                message,
                _tr('broadcast.broadcast_save_message.1', p0=f"{escape_html(metadata['question'])}", p1=f"{metadata['total_voter_count']}"),
                reply_markup=broadcast_poll_mode_kb(),
                force_new=True,
            )
            return

        try:
            await create_poll_draft(
                bot,
                source_chat_id=message.chat.id,
                source_message_id=message.message_id,
                target_chat_id=message.chat.id,
                metadata=metadata,
                delivery_mode=POLL_MODE_CLEAN,
            )
        except TelegramAPIError as error:
            logger.warning("Не удалось создать чистый черновик опроса: %s", error)
            await safe_edit_or_send(
                message,
                _tr('broadcast.broadcast_save_message.2'),
                reply_markup=broadcast_back_kb(),
                force_new=True,
            )
            return

        try:
            await message.delete()
        except Exception:
            pass
        await state.set_state(AdminStates.broadcast_menu)
        await render_broadcast_menu(message, force_new=True)
        return

    if message.photo:
        photo_file_id = message.photo[-1].file_id
        text = get_message_text_for_storage(message, 'html')
    elif message.text:
        text = get_message_text_for_storage(message, 'html')
    else:
        await safe_edit_or_send(message,
            _tr('broadcast.broadcast_save_message.3'),
            reply_markup=broadcast_back_kb()
        )
        return
    
    try:
        save_broadcast_message(text, photo_file_id)
    except BroadcastValidationError as error:
        await safe_edit_or_send(
            message,
            _tr('broadcast.broadcast_save_message.4') + escape_html(str(error)),
            reply_markup=broadcast_back_kb(),
            force_new=True,
        )
        return
    
    await safe_edit_or_send(message,
        _tr('broadcast.broadcast_save_message.5')
    )
    
    # Returning to the mailing menu
    await state.set_state(AdminStates.broadcast_menu)
    
    await render_broadcast_menu(message, force_new=True)


@router.callback_query(F.data.startswith("broadcast_poll_mode:"))
async def broadcast_choose_poll_mode(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Saves an imported voted poll using the selected delivery mode."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    delivery_mode = callback.data.split(":", 1)[1]
    if delivery_mode not in {POLL_MODE_CLEAN, POLL_MODE_PRESERVE}:
        await callback.answer(_tr('broadcast.broadcast_choose_poll_mode.1'), show_alert=True)
        return

    state_data = await state.get_data()
    pending = state_data.get("broadcast_pending_poll")
    if not isinstance(pending, dict):
        await callback.answer(_tr('broadcast.broadcast_choose_poll_mode.2'), show_alert=True)
        return

    try:
        await create_poll_draft(
            bot,
            source_chat_id=int(pending["source_chat_id"]),
            source_message_id=int(pending["source_message_id"]),
            target_chat_id=callback.message.chat.id,
            metadata=dict(pending["metadata"]),
            delivery_mode=delivery_mode,
        )
    except (KeyError, TypeError, ValueError, TelegramAPIError) as error:
        logger.warning("Не удалось сохранить импортированный опрос: %s", error)
        await callback.answer(
            _tr('broadcast.broadcast_choose_poll_mode.3'),
            show_alert=True,
        )
        return


    try:
        await bot.delete_message(
            chat_id=int(pending["source_chat_id"]),
            message_id=int(pending["source_message_id"]),
        )
    except Exception:
        pass

    await state.update_data(broadcast_pending_poll=None)
    await state.set_state(AdminStates.broadcast_menu)
    await render_broadcast_menu(callback.message)
    await callback.answer(_tr('broadcast.broadcast_choose_poll_mode.4'))


# ============================================================================
# PREVIEW MESSAGE
# ============================================================================

@router.callback_query(F.data == "broadcast_preview")
async def broadcast_preview(callback: CallbackQuery, bot: Bot):
    """Shows a preview of the message for the newsletter."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    msg_data = get_broadcast_message()

    if not is_broadcast_content_ready(msg_data):
        await callback.answer(_tr('broadcast.broadcast_preview.1'), show_alert=True)
        return

    if msg_data.get('kind') == BROADCAST_KIND_POLL:
        try:
            await preview_poll(bot=bot, content=msg_data, chat_id=callback.message.chat.id)
        except TelegramAPIError as error:
            logger.warning("Не удалось показать превью опроса: %s", error)
            await callback.answer(
                _tr('broadcast.broadcast_preview.2'),
                show_alert=True,
            )
            return
        await callback.answer(_tr('broadcast.broadcast_preview.3'))
        return

    await callback.answer(_tr('broadcast.broadcast_preview.4'))
    
    preview_text = await render_broadcast_message_text(
        msg_data.get('text', ''),
        callback.from_user.id,
        bot=bot,
    )

    # Send the preview as a separate message
    if msg_data.get('photo_file_id'):
        await safe_edit_or_send(callback.message,
            photo=msg_data['photo_file_id'],
            text=preview_text,
            force_new=True
        )
    else:
        await safe_edit_or_send(callback.message,
            text=preview_text,
            force_new=True
        )


# ============================================================================
# FILTERS
# ============================================================================

@router.callback_query(F.data.startswith("broadcast_filter:"))
async def broadcast_set_filter(callback: CallbackQuery):
    """Toggles one recipient condition in the canonical filter set."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    filter_key = callback.data.split(":", 1)[1]
    
    if filter_key not in BROADCAST_FILTERS:
        await callback.answer(_tr('broadcast.broadcast_set_filter.1'), show_alert=True)
        return

    try:
        selected = set(get_broadcast_filters())
    except BroadcastFilterError:
        # An explicit administrator click safely replaces damaged stored state.
        selected = set()
    enabled = filter_key not in selected
    if enabled:
        selected.add(filter_key)
    else:
        selected.remove(filter_key)
    canonical = normalize_broadcast_filters(selected)
    set_broadcast_filters_with_revision(canonical)

    await render_broadcast_menu(callback.message, current_filters=canonical)
    await callback.answer(
        (
            _tr('broadcast.broadcast_set_filter.2')
            if enabled
            else _tr('broadcast.broadcast_set_filter.3')
        )
        + BROADCAST_FILTERS[filter_key]
    )


@router.callback_query(F.data == "broadcast_empty_audience")
async def broadcast_empty_audience(callback: CallbackQuery):
    """Explains why a zero-recipient broadcast cannot start."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    await callback.answer(
        _tr('broadcast.broadcast_empty_audience.1'),
        show_alert=True,
    )


# ============================================================================
# LAUNCH NEWSLETTER
# ============================================================================

@router.callback_query(F.data == "broadcast_start")
async def broadcast_start(callback: CallbackQuery):
    """Shows confirmation of mailing."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    # Checking to see if the mailing is already in progress
    if is_broadcast_in_progress():
        await callback.answer(_tr('broadcast.broadcast_start.1'), show_alert=True)
        return
    
    # Checking for prepared content
    msg_data = get_broadcast_message()
    if not is_broadcast_content_ready(msg_data):
        await callback.answer(_tr('broadcast.broadcast_start.2'), show_alert=True)
        return
    
    try:
        current_filters = get_broadcast_filters()
    except BroadcastFilterError:
        await callback.answer(
            _tr('broadcast.broadcast_start.3'),
            show_alert=True,
        )
        return
    user_count = count_users_for_broadcast(current_filters)
    
    if user_count == 0:
        await callback.answer(
            _tr('broadcast.broadcast_start.4'),
            show_alert=True,
        )
        return
    
    try:
        confirmation = create_broadcast_confirmation(callback.from_user.id)
    except BroadcastEditorError as error:
        await callback.answer(str(error), show_alert=True)
        return

    confirmed_filters = normalize_broadcast_filters(confirmation["filters"])
    filter_status = broadcast_filter_status(confirmed_filters)
    user_count = int(confirmation["recipient_count"])
    
    content_lines = []
    if msg_data.get('kind') == BROADCAST_KIND_POLL:
        content_lines.extend([
            _tr('broadcast.broadcast_start.5', p0=f'{poll_type_label(msg_data)}'),
            _tr('broadcast.broadcast_start.6', p0=f"{escape_html(msg_data.get('question', ''))}"),
            _tr('broadcast.broadcast_start.7') + (
                _tr('broadcast.broadcast_start.8') if msg_data.get('delivery_mode') == POLL_MODE_CLEAN
                else _tr('broadcast.broadcast_start.9')
            ),
        ])
        if not msg_data.get('is_anonymous', True):
            content_lines.extend([
                "",
                _tr('broadcast.broadcast_start.10'),
            ])
        if msg_data.get('delivery_mode') == POLL_MODE_PRESERVE:
            content_lines.extend([
                "",
                _tr('broadcast.broadcast_start.11'),
            ])
    else:
        content_lines.append(_tr('broadcast.broadcast_start.12'))

    text = (
        _tr('broadcast.broadcast_start.13')
        + "\n".join(content_lines)
        + "\n\n"
        + _tr('broadcast.broadcast_start.14', p0=f'{escape_html(filter_status)}', p1=f'{user_count}')
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=broadcast_confirm_kb(user_count, str(confirmation["token"]))
    )
    await callback.answer()


@router.callback_query(F.data == "broadcast_in_progress")
async def broadcast_in_progress_callback(callback: CallbackQuery):
    """Notification that the mailing is already underway."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    await callback.answer(_tr('broadcast.broadcast_in_progress_callback.1'), show_alert=True)


@router.callback_query(F.data == "broadcast_stop")
async def broadcast_stop(callback: CallbackQuery):
    """Stops the current broadcast or resets a stuck flag."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    result = await request_broadcast_stop_or_reset()

    if result == BROADCAST_STOP_REQUESTED:
        await safe_edit_or_send(
            callback.message,
            _tr('broadcast.broadcast_stop.1'),
        )
        await callback.answer(_tr('broadcast.broadcast_stop.2'))
        return

    if result == BROADCAST_STALE_RESET:
        await render_broadcast_menu(callback.message)
        await callback.answer(_tr('broadcast.broadcast_stop.3'), show_alert=True)
        return

    await render_broadcast_menu(callback.message)
    await callback.answer(_tr('broadcast.broadcast_stop.4'), show_alert=True)


@router.callback_query(F.data.startswith("broadcast_confirm"))
async def broadcast_confirm(callback: CallbackQuery, bot: Bot):
    """Launches a newsletter."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    if is_broadcast_in_progress() or is_broadcast_runtime_active():
        await callback.answer(_tr('broadcast.broadcast_start.1'), show_alert=True)
        return
    
    _, separator, token = str(callback.data or "").partition(":")
    confirmation = (
        consume_valid_broadcast_confirmation(callback.from_user.id, token)
        if separator and token
        else None
    )
    if confirmation is None:
        try:
            fresh = create_broadcast_confirmation(callback.from_user.id)
        except BroadcastEditorError:
            await render_broadcast_menu(callback.message)
            await callback.answer(
                _tr('broadcast.broadcast_confirm.1'),
                show_alert=True,
            )
            return
        await safe_edit_or_send(
            callback.message,
            _tr('broadcast.broadcast_confirm.2'),
            reply_markup=broadcast_confirm_kb(
                int(fresh["recipient_count"]),
                str(fresh["token"]),
            ),
        )
        await callback.answer(_tr('broadcast.broadcast_confirm.3'), show_alert=True)
        return

    msg_data = get_broadcast_message()
    if not msg_data:
        await callback.answer(_tr('broadcast.broadcast_confirm.4'), show_alert=True)
        return
    
    try:
        current_filters = get_broadcast_filters()
    except BroadcastFilterError:
        await render_broadcast_menu(callback.message)
        await callback.answer(
            _tr('broadcast.broadcast_confirm.5'),
            show_alert=True,
        )
        return
    user_ids = get_users_for_broadcast(current_filters)

    try:
        current_revision = int(get_setting('broadcast_config_revision', '0') or 0)
    except (TypeError, ValueError):
        current_revision = 0
    confirmation_is_current = (
        list(confirmation.get("filters") or []) == list(current_filters)
        and int(confirmation.get("recipient_count") or 0) == len(user_ids)
        and int(confirmation.get("config_revision") or 0) == current_revision
        and str(confirmation.get("material_hash") or "") == broadcast_material_hash(msg_data)
    )
    if not confirmation_is_current:
        try:
            fresh = create_broadcast_confirmation(callback.from_user.id)
        except BroadcastEditorError:
            await render_broadcast_menu(callback.message)
            await callback.answer(
                _tr('broadcast.broadcast_confirm.1'),
                show_alert=True,
            )
            return
        await safe_edit_or_send(
            callback.message,
            _tr('broadcast.broadcast_confirm.2'),
            reply_markup=broadcast_confirm_kb(
                int(fresh["recipient_count"]),
                str(fresh["token"]),
            ),
        )
        await callback.answer(_tr('broadcast.broadcast_confirm.3'), show_alert=True)
        return

    if not user_ids:
        await callback.answer(_tr('broadcast.broadcast_confirm.6'), show_alert=True)
        return
    
    if not await try_mark_broadcast_started():
        await callback.answer(_tr('broadcast.broadcast_start.1'), show_alert=True)
        return
    
    total = len(user_ids)
    sent = 0
    blocked = 0
    failed = 0
    stopped = False
    unexpected_error = None
    callback_answered = False

    is_poll = msg_data.get('kind') == BROADCAST_KIND_POLL
    poll_reference = None
    text = msg_data.get('text', '')
    photo_file_id = msg_data.get('photo_file_id')

    try:
        if is_poll:
            poll_reference = await prepare_poll_delivery(
                bot,
                msg_data,
                master_chat_id=callback.message.chat.id,
            )

        await safe_edit_or_send(
            callback.message,
            _tr('broadcast.broadcast_confirm.7', p0=f'{total}'),
            reply_markup=broadcast_stop_kb(),
        )
        await callback.answer()
        callback_answered = True

        for user_id in user_ids:
            if is_broadcast_stop_requested():
                stopped = True
                break

            try:
                if is_poll:
                    await send_poll_to_recipient(
                        bot,
                        poll_reference,
                        chat_id=int(user_id),
                    )
                elif photo_file_id:
                    rendered_text = await render_broadcast_message_text(text, int(user_id), bot=bot)
                    await bot.send_photo(
                        chat_id=user_id,
                        photo=photo_file_id,
                        caption=rendered_text,
                        parse_mode="HTML"
                    )
                else:
                    rendered_text = await render_broadcast_message_text(text, int(user_id), bot=bot)
                    await bot.send_message(
                        chat_id=user_id,
                        text=rendered_text,
                        parse_mode="HTML"
                    )
                sent += 1
            except Exception as e:
                if is_bot_blocked_error(e):
                    mark_user_bot_blocked(user_id)
                    blocked += 1
                elif isinstance(e, TelegramBadRequest):
                    logger.warning(f"Ошибка отправки {user_id}: {e}")
                    failed += 1
                else:
                    logger.error(f"Неожиданная ошибка отправки {user_id}: {e}")
                    failed += 1

            processed = sent + blocked + failed

            # We update progress every 10 processed recipients.
            if processed % 10 == 0 or processed == total:
                try:
                    await safe_edit_or_send(
                        callback.message,
                        _tr('broadcast.broadcast_confirm.8', p0=f'{sent}', p1=f'{total}', p2=f'{blocked}', p3=f'{failed}'),
                        reply_markup=broadcast_stop_kb(),
                    )
                except TelegramBadRequest:
                    pass  # The message has not changed

            if processed < total and is_broadcast_stop_requested():
                stopped = True
                break

            if processed < total:
                await asyncio.sleep(0.5)
    except Exception as e:
        unexpected_error = e
        logger.exception("Техническая ошибка во время рассылки")
    finally:
        await finish_broadcast_state()

    processed = sent + blocked + failed
    poll_close_callback = None
    preserved_poll_notice = ""
    if poll_reference and poll_reference.can_close:
        poll_close_callback = (
            f"broadcast_poll_close:{poll_reference.chat_id}:{poll_reference.message_id}"
        )
    elif is_poll and msg_data.get('delivery_mode') == POLL_MODE_PRESERVE:
        preserved_poll_notice = (
            _tr('broadcast.broadcast_confirm.9')
        )

    result_markup = broadcast_result_kb(poll_close_callback)

    if unexpected_error is not None:
        if not callback_answered:
            try:
                await callback.answer(_tr('broadcast.broadcast_confirm.10'), show_alert=True)
            except Exception:
                pass

        try:
            await safe_edit_or_send(
                callback.message,
                _tr('broadcast.broadcast_confirm.11', p0=f'{sent}', p1=f'{blocked}', p2=f'{failed}', p3=f'{processed}', p4=f'{total}', p5=f'{preserved_poll_notice}'),
                reply_markup=result_markup,
            )
        except Exception as report_error:
            logger.error(f"Не удалось показать отчёт о прерванной рассылке: {report_error}")
        return

    if stopped:
        remaining = max(total - processed, 0)
        await safe_edit_or_send(
            callback.message,
            _tr('broadcast.broadcast_confirm.12', p0=f'{sent}', p1=f'{blocked}', p2=f'{failed}', p3=f'{remaining}', p4=f'{preserved_poll_notice}'),
            reply_markup=result_markup,
        )
        return

    await safe_edit_or_send(
        callback.message,
        _tr('broadcast.broadcast_confirm.13', p0=f'{sent}', p1=f'{blocked}', p2=f'{failed}', p3=f'{preserved_poll_notice}'),
        reply_markup=result_markup,
    )


@router.callback_query(F.data.startswith("broadcast_poll_close:"))
async def broadcast_close_poll(callback: CallbackQuery, bot: Bot):
    """Closes a bot-owned common poll from a persisted result button."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return

    try:
        _, chat_id_raw, message_id_raw = callback.data.split(":", 2)
        chat_id = int(chat_id_raw)
        message_id = int(message_id_raw)
    except (TypeError, ValueError):
        await callback.answer(_tr('broadcast.broadcast_close_poll.1'), show_alert=True)
        return

    already_closed = False
    try:
        await bot.stop_poll(chat_id=chat_id, message_id=message_id)
    except TelegramBadRequest as error:
        error_text = str(error).lower()
        if "already closed" in error_text or "poll has already been closed" in error_text:
            already_closed = True
        else:
            logger.warning("Не удалось закрыть опрос %s/%s: %s", chat_id, message_id, error)
            await callback.answer(
                _tr('broadcast.broadcast_close_poll.2'),
                show_alert=True,
            )
            return
    except TelegramAPIError as error:
        logger.warning("Ошибка Telegram при закрытии опроса %s/%s: %s", chat_id, message_id, error)
        await callback.answer(
            _tr('broadcast.broadcast_close_poll.3'),
            show_alert=True,
        )
        return

    report_text = callback.message.html_text or callback.message.text or _tr('broadcast.broadcast_close_poll.4')
    if _tr('broadcast.broadcast_close_poll.5').strip() not in report_text:
        report_text += _tr('broadcast.broadcast_close_poll.5')
    await safe_edit_or_send(
        callback.message,
        report_text,
        reply_markup=home_only_kb(),
    )
    await callback.answer(_tr('broadcast.broadcast_close_poll.6') if already_closed else _tr('broadcast.broadcast_close_poll.7'))


# ============================================================================
# AUTO NOTIFICATION SETTINGS
# ============================================================================

@router.callback_query(F.data == "broadcast_notifications")
async def broadcast_notifications(callback: CallbackQuery, state: FSMContext):
    """Shows auto notification settings."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    days = int(get_setting('notification_days', '3'))
    
    text = (
        _tr('broadcast.broadcast_notifications.1', p0=f'{days}')
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=broadcast_notifications_kb(days)
    )
    await callback.answer()


@router.callback_query(F.data == "broadcast_notify_days")
async def broadcast_notify_days(callback: CallbackQuery, state: FSMContext):
    """Begins entering the number of days."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    await state.set_state(AdminStates.broadcast_waiting_notify_days)
    
    current_days = get_setting('notification_days', '3')
    
    text = (
        _tr('broadcast.broadcast_notify_days.1', p0=f'{current_days}')
    )
    
    await safe_edit_or_send(callback.message, 
        text,
        reply_markup=broadcast_notify_back_kb()
    )
    await callback.answer()


@router.message(AdminStates.broadcast_waiting_notify_days)
async def broadcast_save_notify_days(message: Message, state: FSMContext):
    """Stores the number of days for notification."""
    if not is_admin(message.from_user.id):
        return
    
    if not message.text or not message.text.isdigit():
        await safe_edit_or_send(message,
            _tr('broadcast.broadcast_save_notify_days.1'),
            reply_markup=broadcast_notify_back_kb()
        )
        return
    
    days = int(message.text)
    if not 1 <= days <= 30:
        await safe_edit_or_send(message,
            _tr('broadcast.broadcast_save_notify_days.2'),
            reply_markup=broadcast_notify_back_kb()
        )
        return
    
    set_setting('notification_days', str(days))
    
    await safe_edit_or_send(message,
        _tr('broadcast.broadcast_save_notify_days.3', p0=f'{days}')
    )
    
    # Returning to notification settings
    await state.set_state(AdminStates.broadcast_menu)
    
    text = (
        _tr('broadcast.broadcast_notifications.1', p0=f'{days}')
    )
    
    await safe_edit_or_send(message,
        text,
        reply_markup=broadcast_notifications_kb(days),
        force_new=True
    )


@router.callback_query(F.data == "broadcast_notify_text")
async def broadcast_notify_text(callback: CallbackQuery, state: FSMContext):
    """Shows/edits notification text through a universal editor."""
    if not is_admin(callback.from_user.id):
        await callback.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    
    from bot.handlers.admin.message_editor import show_message_editor
    
    await show_message_editor(
        callback.message, state,
        key='notification_text',
        back_callback='broadcast_notifications',
        help_text=EDITOR_PLACEHOLDER_HELP['notification_text'],
        allowed_types=['text', 'photo', 'video', 'animation'],
    )
    await callback.answer()

