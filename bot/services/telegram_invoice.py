"""The existing Telegram invoice contract shared by bot and Mini App adapters."""
import json
from aiogram.types import LabeledPrice
from bot.utils.payment_invoice import clamp_invoice_text
from database.requests import get_setting


def invoice_arguments(intent, quote, *, bot_name, provider_id):
    amount = int(quote.raw.get('final_amount') or 0)
    kwargs = {
        'title': clamp_invoice_text(bot_name, 32),
        'description': clamp_invoice_text(intent.description, 255),
        'payload': intent.order_id,
        'currency': quote.charge_currency,
        'prices': [LabeledPrice(label=clamp_invoice_text(intent.description, 80), amount=amount)],
    }
    if provider_id == 'cards':
        kwargs['provider_token'] = get_setting('cards_provider_token', '')
        kwargs['provider_data'] = json.dumps({
            'receipt': {
                'customer': {'email': f'user_{intent.order_id}@t.me'},
                'items': [{
                    'description': clamp_invoice_text(intent.description, 128),
                    'quantity': '1.00',
                    'amount': {
                        'value': f'{quote.charge_amount:.2f}',
                        'currency': 'RUB',
                    },
                    'vat_code': 1,
                    'payment_mode': 'full_prepayment',
                    'payment_subject': 'service',
                }],
            },
        }, ensure_ascii=False)
    return kwargs
