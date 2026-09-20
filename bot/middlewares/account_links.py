"""Consume account-link service messages before user registration and page hooks."""
from aiogram import BaseMiddleware
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.keyboards.account_links import account_link_confirmation_kb
from bot.utils.text import safe_edit_or_send
from bot.utils.user_ui_texts import render_ui_text
from core.account_links import telegram_action
from core.results import CoreError


class AccountLinkMiddleware(BaseMiddleware):
    def __init__(self):
        self._start = Command('start')

    async def __call__(self, handler, event, data):
        action, token = None, None
        if isinstance(event, Message):
            command = await self._start(event, data['bot'])
            args = command['command'].args if command else None
            if args and args.startswith('link_'):
                action, token = 'inspect', args[5:]
        elif isinstance(event, CallbackQuery) and (event.data or '').startswith('account_link:'):
            parts = event.data.split(':', 2)
            action, token = (parts[1], parts[2]) if len(parts) == 3 else ('invalid', '')
        if action is None:
            return await handler(event, data)
        sender = data.get('event_from_user')
        message = event.message if isinstance(event, CallbackQuery) else event
        keyboard = None
        try:
            if sender is None or sender.is_bot or not isinstance(message, Message) or message.chat.type != 'private':
                raise CoreError('link_invalid')
            result = telegram_action(token, sender.model_dump(), bot_id=data['bot'].id, action=action)
            if action == 'inspect':
                text = render_ui_text('account.link.prompt', account_id=result['account_id'])
                keyboard = account_link_confirmation_kb(token)
            else:
                text = render_ui_text('account.link.confirmed' if action == 'confirm' else 'account.link.cancelled')
        except CoreError as error:
            key = {'link_conflict': 'conflict', 'link_unavailable': 'unavailable',
                   'temporarily_unavailable': 'unavailable'}.get(error.code, 'invalid')
            text = render_ui_text('account.link.' + key)
        if isinstance(message, Message):
            await safe_edit_or_send(message, text, reply_markup=keyboard)
        if isinstance(event, CallbackQuery):
            await event.answer()
        return None
