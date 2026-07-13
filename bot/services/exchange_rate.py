"""
Exchange rate service.

Receiving the USD/RUB exchange rate from the Central Bank of the Russian Federation with fallback in settings.
"""
import logging
import aiohttp

from database.requests import get_setting, set_setting

logger = logging.getLogger(__name__)

DEFAULT_USD_RUB_RATE = '10000'


async def get_usd_rub_rate() -> int:
    """
    Get the USD/RUB rate in kopecks.
    First, the Central Bank of the Russian Federation tries, and if there is an error, it takes it from the settings (fallback).

    Returns:
        USD/RUB exchange rate in kopecks (for example, 9500 = 95.00 rubles)
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                'https://www.cbr-xml-daily.ru/daily_json.js',
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                data = await resp.json(content_type=None)
                rate = data['Valute']['USD']['Value']
                rate_cents = int(rate * 100)
                set_setting('usd_rub_rate', str(rate_cents))
                return rate_cents
    except Exception as e:
        logger.error(f"Failed to get exchange rate from CB: {e}")
        val = get_setting('usd_rub_rate', DEFAULT_USD_RUB_RATE)
        try:
            return int(val)
        except (ValueError, TypeError):
            logger.error(f"Некорректное значение курса в settings: {val}")
            return int(DEFAULT_USD_RUB_RATE)
