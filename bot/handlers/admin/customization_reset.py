"""Hidden admin-only customization reset command."""
from __future__ import annotations

from localization import text as _tr

import asyncio
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.admin_settings import (
    custom_reset_cancel_kb,
    custom_reset_done_kb,
    custom_reset_preview_kb,
)
from bot.services.customization_reset import (
    CUSTOM_RESET_CONFIRMATION_PHRASE,
    PRESERVED_DATA_LABELS,
    CustomizationResetError,
    CustomizationResetReport,
    run_customization_reset_for_bot,
)
from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.utils.text import escape_html, get_message_text_for_storage, safe_edit_or_send

logger = logging.getLogger(__name__)
router = Router()

_CUSTOM_RESET_LOCK = asyncio.Lock()


def _format_action_block(title: str, actions: list[str], *, limit: int = 18) -> list[str]:
    if not actions:
        return []
    lines = [f"<b>{escape_html(title)}:</b>"]
    visible = actions[:limit]
    lines.extend(f"• <code>{escape_html(action)}</code>" for action in visible)
    hidden_count = len(actions) - len(visible)
    if hidden_count > 0:
        lines.append(_tr('customization_reset.format_action_block.1', p0=f'{hidden_count}'))
    return lines


def _format_backup_paths(report: CustomizationResetReport) -> list[str]:
    if not report.backup_paths:
        return []
    lines = [_tr('customization_reset.format_backup_paths.1')]
    for path in report.backup_paths:
        try:
            display_path = Path(path).as_posix()
        except TypeError:
            display_path = str(path)
        lines.append(f"• <code>{escape_html(display_path)}</code>")
    return lines


def _format_custom_reset_report(report: CustomizationResetReport) -> str:
    title = _tr('customization_reset.format_custom_reset_report.1')
    mode = _tr('customization_reset.format_custom_reset_report.2') if report.error else _tr('customization_reset.format_custom_reset_report.3') if report.dry_run else _tr('customization_reset.format_custom_reset_report.4')
    lines = [
        title,
        "",
        _tr('customization_reset.format_custom_reset_report.5', p0=f'{mode}'),
        "",
        _tr('customization_reset.format_custom_reset_report.6'),
        "",
        _tr('customization_reset.format_custom_reset_report.7'),
    ]
    lines.extend(f"• <code>{escape_html(label)}</code>" for label in PRESERVED_DATA_LABELS)
    lines.append("")
    lines.extend(_format_action_block(_tr('customization_reset.format_custom_reset_report.8'), report.db_actions))
    if report.web_actions:
        lines.append("")
        lines.extend(_format_action_block("Web/Mini App", report.web_actions))
    if report.file_actions:
        lines.append("")
        lines.extend(_format_action_block(_tr('customization_reset.format_custom_reset_report.9'), report.file_actions))
    if report.runtime_actions:
        lines.append("")
        lines.extend(_format_action_block("Runtime", report.runtime_actions))
    backup_lines = _format_backup_paths(report)
    if backup_lines:
        lines.append("")
        lines.extend(backup_lines)
    if report.error:
        lines.extend(["", _tr('customization_reset.format_custom_reset_report.10', p0=f'{escape_html(report.error)}'),
                      _tr('customization_reset.format_custom_reset_report.11')])
    if report.dry_run:
        lines.extend([
            "",
            _tr('customization_reset.format_custom_reset_report.12'),
            _tr('customization_reset.format_custom_reset_report.13'),
        ])
    return "\n".join(lines)


async def _deny(target: Message | CallbackQuery) -> None:
    text = _tr('customization_reset.deny.1')
    if isinstance(target, CallbackQuery):
        await target.answer(_tr('broadcast.show_broadcast_menu.1'), show_alert=True)
        return
    await safe_edit_or_send(target, text, force_new=True)


@router.message(Command("custom_reset"))
async def show_custom_reset_preview(message: Message, state: FSMContext):
    """Shows dry-run information for the hidden customization reset."""
    if not message.from_user or not is_admin(message.from_user.id):
        await _deny(message)
        return

    await state.clear()
    try:
        report = await run_customization_reset_for_bot(dry_run=True)
    except Exception as exc:
        logger.exception("Failed to build customization reset preview")
        await safe_edit_or_send(
            message,
            _tr('customization_reset.show_custom_reset_preview.1', p0=f'{escape_html(str(exc))}'),
            force_new=True,
        )
        return

    await safe_edit_or_send(
        message,
        _format_custom_reset_report(report),
        reply_markup=custom_reset_preview_kb(),
        force_new=True,
    )


@router.callback_query(F.data == "admin_custom_reset_confirm")
async def ask_custom_reset_phrase(callback: CallbackQuery, state: FSMContext):
    """Asks for the manual confirmation phrase."""
    if not callback.from_user or not is_admin(callback.from_user.id):
        await _deny(callback)
        return

    await state.set_state(AdminStates.custom_reset_confirm_phrase)
    await safe_edit_or_send(
        callback.message,
        (
            _tr('customization_reset.ask_custom_reset_phrase.1', p0=f'{escape_html(CUSTOM_RESET_CONFIRMATION_PHRASE)}')
        ),
        reply_markup=custom_reset_cancel_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_custom_reset_cancel")
async def cancel_custom_reset(callback: CallbackQuery, state: FSMContext):
    """Cancels the hidden customization reset flow."""
    if not callback.from_user or not is_admin(callback.from_user.id):
        await _deny(callback)
        return

    await state.clear()
    await safe_edit_or_send(
        callback.message,
        _tr('customization_reset.cancel_custom_reset.1'),
        reply_markup=custom_reset_done_kb(),
    )
    await callback.answer()


@router.message(AdminStates.custom_reset_confirm_phrase, F.text)
async def apply_custom_reset(message: Message, state: FSMContext):
    """Applies the hidden customization reset after the confirmation phrase."""
    if not message.from_user or not is_admin(message.from_user.id):
        await state.clear()
        await _deny(message)
        return

    phrase = get_message_text_for_storage(message, "plain")
    if phrase != CUSTOM_RESET_CONFIRMATION_PHRASE:
        await safe_edit_or_send(
            message,
            (
                _tr('customization_reset.apply_custom_reset.1', p0=f'{escape_html(CUSTOM_RESET_CONFIRMATION_PHRASE)}')
            ),
            reply_markup=custom_reset_cancel_kb(),
            force_new=True,
        )
        return

    if _CUSTOM_RESET_LOCK.locked():
        await safe_edit_or_send(
            message,
            _tr('customization_reset.apply_custom_reset.2'),
            force_new=True,
        )
        return

    progress = await safe_edit_or_send(
        message,
        _tr('customization_reset.apply_custom_reset.3'),
        force_new=True,
    )

    async with _CUSTOM_RESET_LOCK:
        try:
            report = await run_customization_reset_for_bot(
                dry_run=False,
                bot=getattr(message, "bot", None),
            )
        except Exception as exc:
            logger.exception("Customization reset failed")
            await state.clear()
            await safe_edit_or_send(
                progress,
                (_format_custom_reset_report(exc.report) if isinstance(exc, CustomizationResetError)
                 else _tr('customization_reset.apply_custom_reset.4', p0=f'{escape_html(str(exc))}')),
                reply_markup=custom_reset_done_kb(),
            )
            return

    await state.clear()
    await safe_edit_or_send(
        progress,
        _format_custom_reset_report(report),
        reply_markup=custom_reset_done_kb(),
    )
