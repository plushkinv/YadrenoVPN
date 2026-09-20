"""Own-account facade for shared modules; privileged admin operations stay private."""
from core.context import get_account_context


def _actor():
    actor = get_account_context()
    if actor is None:
        raise RuntimeError('a bound account operation is required')
    return actor


class SharedCoreFacade:
    def __init__(self, module_id):
        self._module_id = module_id

    def get_current_user(self):
        from bot.services.extension_user_snapshot import build_extension_account_snapshot
        return build_extension_account_snapshot(_actor().account_id)

    def list_current_user_key_summaries(self):
        from bot.utils.extension_core import ExtensionCoreAPI
        return ExtensionCoreAPI(self._module_id).list_current_user_key_summaries()

    def preview_payment_price(self, *, purpose, tariff_id=None, key_id=None, nominal_amount_minor=None):
        from bot.services.payment_pricing import preview_payment_price
        return preview_payment_price(user_id=_actor().account_id, purpose=purpose, tariff_id=tariff_id,
                                     key_id=key_id, nominal_amount_minor=nominal_amount_minor)

    async def add_balance_bonus(self, *, amount_minor, reason, idempotency_key):
        from bot.utils.extension_core import ExtensionCoreAPI
        return await ExtensionCoreAPI(self._module_id).add_balance_bonus(
            user_id=_actor().account_id, cents=amount_minor, reason=reason, idempotency_key=idempotency_key)

    async def debit_balance(self, *, amount_minor, reason, idempotency_key):
        from bot.utils.extension_core import ExtensionCoreAPI
        return await ExtensionCoreAPI(self._module_id).debit_balance(
            user_id=_actor().account_id, amount_minor=amount_minor, reason=reason, idempotency_key=idempotency_key)

    async def grant_days_to_first_active_key(self, *, days, reason, idempotency_key):
        from bot.utils.extension_core import _ensure_new_mutation_allowed, _normalize_positive_int
        from bot.services.extension_core_ops import apply_extension_core_operation
        _ensure_new_mutation_allowed('grant_days_to_first_active_key')
        return await apply_extension_core_operation(
            extension_id=self._module_id, idempotency_key=idempotency_key,
            operation='grant_days_to_first_active_key', target_user_id=_actor().account_id,
            amount=_normalize_positive_int(days, 'days'), reason=reason, _atomic_days=True)

    async def create_quote(self, inputs):
        from bot.utils.extension_core import _ensure_new_mutation_allowed
        from core.payment_offers import create_offer
        _ensure_new_mutation_allowed('create_quote')
        return await create_offer(_actor(), inputs)

    async def prepare_order(self, quote_id, idempotency_key):
        from bot.utils.extension_core import _ensure_new_mutation_allowed
        from core.payment_offers import create_order
        _ensure_new_mutation_allowed('prepare_order')
        return await create_order(_actor(), quote_id, idempotency_key)

    def get_order_status(self, order_id):
        from core.payments import order_status
        return order_status(_actor(), order_id)

    async def send_current_user_page(self, page_key):
        from bot.utils.extension_core import ExtensionCoreAPI
        return await ExtensionCoreAPI(self._module_id).send_current_user_page(page_key)

    def list_support_threads(self, *, limit=20, offset=0):
        from core.support import list_threads
        return list_threads(_actor(), self._module_id, limit=limit, offset=offset)

    def get_support_history(self, thread_id, *, limit=50, before_message_id=None):
        from core.support import history
        return history(_actor(), self._module_id, thread_id, limit=limit, before_message_id=before_message_id)

    async def create_support_thread(self, *, text, idempotency_key):
        from bot.utils.extension_core import _ensure_new_mutation_allowed
        from core.support import write_message
        _ensure_new_mutation_allowed('create_support_thread')
        return await write_message(_actor(), self._module_id, text, idempotency_key)

    async def reply_support_thread(self, thread_id, *, text, idempotency_key):
        from bot.utils.extension_core import _ensure_new_mutation_allowed
        from core.support import write_message
        _ensure_new_mutation_allowed('reply_support_thread')
        return await write_message(_actor(), self._module_id, text, idempotency_key, thread_id=thread_id)
