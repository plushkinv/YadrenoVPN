"""Translate configured text labels once, before command and input routing."""
from aiogram import BaseMiddleware
from aiogram.types import Message, MessageEntity

from bot.utils.reply_keyboard import active_config
from bot.utils.text import get_message_text_for_storage


class ReplyKeyboardMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message) and event.text:
            label = get_message_text_for_storage(event, 'plain')
            # Native commands, including arguments and bot mentions, keep their semantics.
            if not label.startswith('/'):
                config = active_config()
                if config:
                    command = next((
                        button['command'] for row in config['rows'] for button in row
                        if button['label'] == label
                    ), None)
                    if command:
                        event = event.model_copy(update={
                            'text': command,
                            'entities': [MessageEntity(type='bot_command', offset=0, length=len(command))],
                        })
        return await handler(event, data)
