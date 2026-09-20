"""Mini App transport for the shared Telegram invoice and settlement contract."""
from core.results import CoreError
from database import requests as db


async def create_invoice_link(account, intent, price):
    if account.source != 'mini_app' or account.telegram_id is None:
        raise CoreError('payment_method_unavailable')
    from aiogram import Bot
    from aiogram.client.session.aiohttp import AiohttpSession
    from config import BOT_TOKEN
    from bot.services.telegram_invoice import invoice_arguments
    from bot.services.payment_intents import _decimal_text
    bot = Bot(BOT_TOKEN, session=AiohttpSession(timeout=15))
    try:
        me = await bot.get_me()
        kwargs = invoice_arguments(intent, price, bot_name=me.username, provider_id=price.payment_type)
        link = await bot.create_invoice_link(**kwargs)
    finally:
        await bot.session.close()
    if not db.save_payment_provider_order(
            order_id=intent.order_id, provider_id=price.payment_type, payment_type=price.payment_type,
            provider_payment_id=intent.order_id, payment_url=link, status='pending', metadata={},
            purpose=intent.purpose, charge_amount=_decimal_text(price.charge_amount), charge_currency=price.charge_currency):
        raise CoreError('payment_preparation_pending', retryable=True)
