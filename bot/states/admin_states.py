"""
FSM status for the admin panel.

Managing multi-step administrator dialogs.
"""

from localization import text as _tr
from aiogram.fsm.state import State, StatesGroup

from bot.utils.telegram_links import is_telegram_bot_start_link


class AdminStates(StatesGroup):
    """Admin panel states."""
    
    # ========== Main menu ==========
    admin_menu = State()  # Admin main screen
    yadreno_waiting_api_key = State()  # Entering personal api_key Yadreno Admin
    yadreno_chat = State()  # Dialogue with agent Yadreno Admin
    yadreno_waiting_task = State()  # Local contextual invitation before Hub admission
    custom_reset_confirm_phrase = State()  # Hidden customization reset confirmation phrase
    web_verification_value = State()  # Private phone verification provider settings
    
    # ========== Server management ==========
    servers_list = State()           # Server list
    server_view = State()            # View a specific server
    
    # ========== Adding a server (step-by-step dialogue) ==========
    add_server_auth_method = State() # Authentication method selection
    add_server_name = State()        # Step 1: Title
    add_server_url = State()         # Step 2: Panel URL
    add_server_inbound_group = State()  # Step 3: Optional virtual inbound group
    add_server_api_token = State()   # Step 4: 3X-UI API token
    add_server_login = State()       # Step 4: Login
    add_server_password = State()    # Step 5: Password
    add_server_confirm = State()     # Confirmation after verification
    
    # ========== Editing the server ==========
    edit_server = State()            # Editing with navigation through parameters
    
    # ========== Deleting a server ==========
    delete_server_confirm = State()  # Deletion confirmation
    
    # ========== Section “Payments” ==========
    payments_menu = State()          # Main payment screen
    cards_setup_token = State()      # Entering the Telegram Payments provider token
    gateway_setting_value = State()  # Entering a hosted provider credential
    
    # ========== Setting up crypto payments ==========
    crypto_setup_url = State()       # Entering a link to a product
    crypto_setup_secret = State()    # Entering the secret key
    edit_crypto = State()            # Editing crypto settings
    cryptobot_setup_token = State()  # Entering the Crypto Pay API token

    # ========== Setting up QR payment YuKassa ==========
    qr_setup_shop_id = State()       # Entering Shop ID
    qr_setup_secret_key = State()    # Enter Secret Key

    # ========== WATA setup ==========
    wata_setup_token = State()       # Entering the WATA JWT token

    # ========== Setting up Platega ==========
    platega_setup_merchant = State()  # Enter Merchant ID Platega
    platega_setup_secret = State()    # Enter the Platega API key

    # ========== Cardlink setup ==========
    cardlink_setup_shop_id = State()    # Enter Shop ID Cardlink
    cardlink_setup_api_token = State()  # Entering Cardlink API token

    # ========== Referral system ==========
    referral_menu = State()          # Main menu of the referral system
    referral_level_edit = State()    # Editing a Level
    waiting_balance_amount = State()    # Entering the balance amount
    waiting_coefficient = State()        # Entering the coefficient
    support_waiting_message = State()    # Entering a message to the user/response in support
    promocode_add_code = State()
    promocode_add_discount = State()
    promocode_add_expires = State()
    promocode_add_limit = State()
    promocode_edit_value = State()
    coupon_setting_value = State()
    coupon_generate_discount = State()
    coupon_generate_lifetime = State()
    coupon_generate_count = State()
    
    # ========== Editing messages (universal editor) ==========
    waiting_for_message = State()    # Waiting for a new message (same for all editors)
    waiting_for_link_url = State()   # Waiting for link URL to be entered
    waiting_for_link_button_name = State()  # Waiting for the link button name to be entered
    extension_setting_value = State()  # Entering a custom extension setting value
    
    # ========== Tariff management ==========
    tariffs_list = State()           # List of tariffs
    tariff_view = State()            # View a specific tariff
    
    # ========== Adding a tariff (step-by-step dialogue) ==========
    add_tariff_name = State()        # Step 1: Title
    add_tariff_price = State()       # Step 2: Price in the current base currency
    add_tariff_duration = State()    # Step 3: Duration
    add_tariff_traffic_limit = State() # Step 4: Data Limit (GB)
    add_tariff_max_ips = State()     # Step 5: Device limit
    add_tariff_confirm = State()     # Confirmation
    payment_rate_value = State()     # Stablecoin/Stars RUB rate input
    base_currency_transition_rate = State()
    base_currency_switch_confirm = State()

    # ========== Edit tariff ==========
    edit_tariff = State()            # Editing with navigation through parameters
    
    # ========== Newsletter ==========
    broadcast_menu = State()         # Mailing main screen
    broadcast_waiting_message = State()      # Waiting for a message to be sent
    broadcast_waiting_notify_days = State()  # Waiting for number of days to notify
    
    # ========== Section “Users” ==========
    users_menu = State()             # Section main screen
    users_list = State()             # List of users with pagination
    user_view = State()              # View a specific user
    waiting_user_id = State()        # Waiting for telegram_id input
    
    # ========== VPN Key Management ==========
    key_view = State()               # View a specific key
    key_extend_days = State()        # Entering the number of days to extend
    key_plan_custom_traffic = State()
    key_plan_custom_days = State()
    key_plan_custom_devices = State()
    key_plan_custom_confirm = State()
    
    # ========== Adding a key by administrator ==========
    add_key_group = State()          # Tariff group selection
    add_key_server = State()         # Server selection
    add_key_traffic = State()        # Entering traffic limit (GB)
    add_key_days = State()           # Enter validity period (days)
    add_key_devices = State()        # Enter device limit
    add_key_confirm = State()        # Creation Confirmation
    
    # ========== Management of tariff groups ==========
    group_add_name = State()         # Entering a new group name
    group_edit_name = State()        # Entering a new group name
    group_subscription_parent = State()  # Selecting the subscription host group
    tariff_select_group = State()    # Selecting a group when adding a tariff
    server_select_group = State()    # Selecting a group when adding a server


# ============================================================================
# SERVER SETTINGS
# ============================================================================

SQLITE_MAX_INTEGER = 9_223_372_036_854_775_807


def _valid_inbound_group_id(value: str) -> bool:
    normalized = str(value or '').strip()
    return bool(
        normalized
        and normalized.isascii()
        and normalized.isdigit()
        and 1 <= int(normalized) <= SQLITE_MAX_INTEGER
    )


SERVER_COMMON_PARAMS = [
    {
        "key": "name",
        "label": _tr('admin_states.module.1'),
        "hint": _tr('admin_states.module.2'),
        "validate": lambda x: len(x) >= 2,
        "error": _tr('admin_states.module.3')
    },
    {
        "key": "panel_url",
        "label": _tr('admin_states.module.4'),
        "hint": _tr('admin_states.module.5'),
        "validate": lambda x: len(x.strip()) >= 5 and ":" in x,
        "error": _tr('admin_states.module.6')
    },
    {
        "key": "inbound_group_id",
        "label": _tr('admin_states.module.7'),
        "hint": _tr('admin_states.module.8'),
        "validate": _valid_inbound_group_id,
        "convert": lambda x: int(str(x).strip()),
        "error": _tr('admin_states.module.9'),
    },
]

SERVER_API_TOKEN_PARAM = {
    "key": "api_token",
    "label": _tr('servers.build_server_view.6'),
    "hint": _tr('admin_states.module.10'),
    "validate": lambda x: len(x.strip()) >= 8,
    "error": _tr('admin_states.module.11')
}

SERVER_LOGIN_PARAMS = [
    {
        "key": "login",
        "label": _tr('admin_states.module.12'),
        "hint": _tr('admin_states.module.13'),
        "validate": lambda x: len(x) >= 1,
        "error": _tr('admin_states.module.14')
    },
    {
        "key": "password",
        "label": _tr('admin_states.module.15'),
        "hint": _tr('admin_states.module.16'),
        "validate": lambda x: len(x) >= 1,
        "error": _tr('admin_states.module.17')
    },
]

SERVER_PARAMS = SERVER_COMMON_PARAMS + SERVER_LOGIN_PARAMS


def get_server_params(auth_method: str = "login_password") -> list:
    """Returns server fields for the selected authentication method."""
    if auth_method == "api_token":
        return SERVER_COMMON_PARAMS + [SERVER_API_TOKEN_PARAM]
    return SERVER_COMMON_PARAMS + SERVER_LOGIN_PARAMS


def get_param_by_index(index: int, auth_method: str = "login_password") -> dict:
    """Gets the server parameter by index."""
    params = get_server_params(auth_method)
    if 0 <= index < len(params):
        return params[index]
    return params[0]


def get_total_params(auth_method: str = "login_password") -> int:
    """Returns the total number of server parameters."""
    return len(get_server_params(auth_method))


# ============================================================================
# TARIFF PARAMETERS
# ============================================================================

def _valid_base_price(value: str) -> bool:
    try:
        from bot.services.money import parse_major_to_minor

        amount = parse_major_to_minor(value)
    except (TypeError, ValueError):
        return False
    return 1 <= amount <= 10_000_000


def _convert_base_price(value: str) -> int:
    from bot.services.money import parse_major_to_minor

    return parse_major_to_minor(value)


def _format_base_price(value: int) -> str:
    from bot.services.money import format_money_minor

    return format_money_minor(value)


TARIFF_PARAMS = [
    {
        "key": "name",
        "label": _tr('admin_states.module.1'),
        "hint": _tr('admin_states.module.18'),
        "validate": lambda x: 1 <= len(x) <= 50,
        "error": _tr('admin_states.module.19')
    },
    {
        "key": "price_minor",
        "label": _tr('admin_states.module.20'),
        "hint": _tr('admin_states.module.21'),
        "validate": _valid_base_price,
        "error": _tr('admin_states.module.22'),
        "convert": _convert_base_price,
        "format": _format_base_price,
        "help": _tr('admin_states.module.23')
    },
    {
        "key": "duration_days",
        "label": _tr('admin_states.module.24'),
        "hint": _tr('admin_states.module.25'),
        "validate": lambda x: x.isdigit() and 0 <= int(x) <= 99999,
        "error": _tr('admin_states.module.26'),
        "convert": int,
        "format": lambda x: _tr('tariffs.admin_duration_text.1', p0=f'{x}', count=x) if x > 0 else _tr('key_plans.duration_text.1')
    },
    {
        "key": "traffic_limit_gb",
        "label": _tr('admin_states.module.27'),
        "hint": _tr('admin_states.module.28'),
        "validate": lambda x: x.isdigit() and 0 <= int(x) <= 99999,
        "error": _tr('admin_states.module.29'),
        "convert": int,
        "format": lambda x: _tr('key_plans.traffic_text.2', p0=f'{x}') if x > 0 else _tr('key_plans.traffic_text.1')
    },
    {
        "key": "max_ips",
        "label": _tr('admin_states.module.30'),
        "hint": _tr('admin_states.module.31'),
        "validate": lambda x: x.isdigit() and 1 <= int(x) <= 999,
        "error": _tr('admin_states.module.32'),
        "convert": int,
        "format": lambda x: _tr('tariffs.render_tariff_view.6', p0=f'{x}')
    },
    {
        "key": "display_order",
        "label": _tr('admin_states.module.33'),
        "hint": _tr('admin_states.module.34'),
        "validate": lambda x: x.isdigit() and 0 <= int(x) <= 99,
        "error": _tr('admin_states.module.35'),
        "convert": int
    },
]


def get_tariff_param_by_index(index: int) -> dict:
    """
    Gets the tariff parameter by index.
    
    Args:
        index: Parameter index
    """
    if 0 <= index < len(TARIFF_PARAMS):
        return TARIFF_PARAMS[index]
    return TARIFF_PARAMS[0]


def get_tariff_params_list() -> list:
    """Returns a list of tariff parameters."""
    return TARIFF_PARAMS


def get_total_tariff_params() -> int:
    """Returns the total number of tariff parameters."""
    return len(TARIFF_PARAMS)


# ============================================================================
# CRYPTO SETTINGS PARAMETERS
# ============================================================================

CRYPTO_PARAMS = [
    {
        "key": "crypto_item_url",
        "label": _tr('admin_states.module.36'),
        "hint": _tr('admin_states.module.37'),
        "validate": lambda x: is_telegram_bot_start_link(
            x,
            bot_username="Ya_SellerBot",
            start_prefix="item",
        ),
        "error": _tr('admin_states.module.38')
    },
    {
        "key": "crypto_secret_key",
        "label": _tr('admin_states.module.39'),
        "hint": _tr('admin_states.module.40'),
        "validate": lambda x: len(x) >= 16,
        "error": _tr('admin_states.module.41')
    },
]


def get_crypto_param_by_index(index: int) -> dict:
    """Gets the crypto settings parameter by index."""
    if 0 <= index < len(CRYPTO_PARAMS):
        return CRYPTO_PARAMS[index]
    return CRYPTO_PARAMS[0]


def get_total_crypto_params() -> int:
    """Returns the total number of crypto settings parameters."""
    return len(CRYPTO_PARAMS)

