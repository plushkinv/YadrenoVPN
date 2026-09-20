"""Optional Telegram transport owned by the single application lifecycle."""
import asyncio
import logging

_bot = None
_pending = set()


def get_bot():
    return _bot


def set_bot(bot):
    global _bot
    _bot = bot


def dispatch(operation, *, bot=None):
    """Run best-effort notifications independently from an HTTP business response."""
    transport = bot if bot is not None else get_bot()
    if transport is None:
        return
    async def deliver():
        try:
            await asyncio.wait_for(operation(transport), timeout=30)
        except Exception as error:
            logging.getLogger(__name__).warning('Optional Telegram delivery failed type=%s', type(error).__name__)
    task = asyncio.create_task(deliver(), name='optional_telegram_delivery')
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def stop_pending():
    tasks = tuple(_pending)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def notify_new_referral(user_id):
    async def deliver(bot):
        from bot.services.notifications import notify_referrers_new_referral
        await notify_referrers_new_referral(bot, user_id)
    dispatch(deliver)
