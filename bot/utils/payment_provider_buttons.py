"""Project registered payment providers into ordinary editable page buttons."""
from __future__ import annotations

from bot.utils.payment_provider_registry import get_payment_provider, list_payment_providers


_BUTTON_PREFIX = 'btn_intent_provider_'
_METHOD_PAGES = frozenset({
    'payment_method_select',
    'payment_method_select_renewal',
    'payment_method_select_surcharge',
    'payment_method_select_topup',
})


def get_custom_payment_button_provider_id(button_id: str) -> str | None:
    """Resolve only currently registered custom provider button IDs."""
    if not isinstance(button_id, str) or not button_id.startswith(_BUTTON_PREFIX):
        return None
    try:
        provider = get_payment_provider(button_id[len(_BUTTON_PREFIX):])
    except ValueError:
        return None
    return provider.provider_id if provider is not None else None


def prepare_payment_provider_buttons(page_key: str | None, defaults: list[dict]) -> list[dict]:
    """Add runtime defaults before merging saved whole-button overrides."""
    if page_key not in _METHOD_PAGES:
        return defaults
    existing_ids = {button.get('id') for button in defaults if isinstance(button.get('id'), str)}
    providers = [
        provider for provider in list_payment_providers()
        if f'{_BUTTON_PREFIX}{provider.provider_id}' not in existing_ids
    ]
    if not providers:
        return defaults

    provider_rows = [
        button['row'] for button in defaults
        if isinstance(button.get('id'), str) and button['id'].startswith(_BUTTON_PREFIX)
        and isinstance(button.get('row'), int) and not isinstance(button['row'], bool)
    ]
    insertion_row = max(provider_rows, default=-1) + 1
    prepared = []
    for button in defaults:
        item = dict(button)
        row = item.get('row')
        if isinstance(row, int) and not isinstance(row, bool) and row >= insertion_row:
            item['row'] = row + len(providers)
        prepared.append(item)
    for index, provider in enumerate(providers):
        prepared.append({
            'id': f'{_BUTTON_PREFIX}{provider.provider_id}',
            'label': provider.label,
            'color': 'secondary',
            'row': insertion_row + index,
            'col': 0,
            'is_hidden': False,
            'action_type': 'system',
            'action_value': None,
        })
    return prepared
