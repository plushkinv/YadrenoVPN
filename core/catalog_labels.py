"""Project saved payment button captions without running Telegram page flows."""
from bot.services.payment_intents import format_base_minor
from bot.services.payment_provider_adapters import get_payment_provider_adapter
from bot.utils.page_button_data import _merge_buttons_by_id
from bot.utils.page_placeholder_context import enrich_page_placeholder_context_sync
from bot.utils.placeholders import apply_page_placeholders, contains_placeholder
from database import requests as db


def payment_method_buttons(purpose):
    page_key = 'payment_method_select_renewal' if purpose == 'key_renewal' else 'payment_method_select'
    row = db.get_page(page_key) or {}
    buttons = _merge_buttons_by_id(row.get('buttons_default', '[]'), row.get('buttons_custom'), page_key=page_key)
    return page_key, {button['id']: button for button in buttons if isinstance(button.get('id'), str)}


def label_payment_methods(methods, page_key, buttons, *, account, tariff, price, key):
    """Only captions are shared; provider eligibility remains a business decision."""
    context = {
        'telegram_id': account.telegram_id,
        'payment_tariff_name': tariff['name'],
        'payment_amount_text': format_base_minor(price['payable_amount_minor'], price['base_currency']),
        'payment_nominal_text': format_base_minor(price['nominal_amount_minor'], price['base_currency']),
        'payment_remaining_text': format_base_minor(price['payable_amount_minor'], price['base_currency']),
        'payment_balance_deduct_text': format_base_minor(0, price['base_currency']),
        'payment_base_currency': price['base_currency'],
        'key_fields': {'id': key['id'], 'name': key.get('display_name') or f"#{key['id']}"} if key else {},
    }
    selected = []
    for method in methods:
        provider_id = method['id'].removeprefix('ext_')
        button_id = 'btn_intent_balance' if provider_id == 'balance' else 'btn_intent_provider_' + provider_id
        selected.append(buttons.get(button_id, {}))
    context = enrich_page_placeholder_context_sync(page_key, {'buttons': selected}, context)
    result = []
    for method, button in zip(methods, selected):
        raw = button.get('label')
        label = apply_page_placeholders(raw, context=context, mode='button_label').strip() if isinstance(raw, str) else ''
        if not label or contains_placeholder(label):
            adapter = get_payment_provider_adapter(method['id'].removeprefix('ext_'))
            label = adapter.label if adapter else method['id']
        result.append({**method, 'label': label})
    return result
