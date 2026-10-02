from aiogram.fsm.state import State, StatesGroup

class UserStates(StatesGroup):
    # Example state
    waiting_for_vpn_key = State()

class SupportUserStates(StatesGroup):
    waiting_for_message = State()

class RenameKey(StatesGroup):
    waiting_for_name = State()

class ReplaceKey(StatesGroup):
    users_server = State()
    confirm = State()

class NewKeyConfig(StatesGroup):
    waiting_for_server = State()

class PromoInput(StatesGroup):
    waiting_for_code = State()

class PaymentTopup(StatesGroup):
    waiting_for_amount = State()

class ExtensionInput(StatesGroup):
    waiting = State()
