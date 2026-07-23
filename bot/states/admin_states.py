"""
FSM status for the admin panel.

Managing multi-step administrator dialogs.
"""
from aiogram.fsm.state import State, StatesGroup

from bot.utils.telegram_links import is_telegram_bot_start_link


class AdminStates(StatesGroup):
    """Admin panel states."""
    
    # ========== Main menu ==========
    admin_menu = State()  # Admin main screen
    yadreno_waiting_api_key = State()  # Entering personal api_key Yadreno Admin
    yadreno_chat = State()  # Dialogue with agent Yadreno Admin
    custom_reset_confirm_phrase = State()  # Hidden customization reset confirmation phrase
    
    # ========== Server management ==========
    servers_list = State()           # Server list
    server_view = State()            # View a specific server
    
    # ========== Adding a server (step-by-step dialogue) ==========
    add_server_auth_method = State() # Authentication method selection
    add_server_name = State()        # Step 1: Title
    add_server_url = State()         # Step 2: Panel URL
    add_server_api_token = State()   # Step 3: 3X-UI API token
    add_server_login = State()       # Step 3: Login
    add_server_password = State()    # Step 4: Password
    add_server_confirm = State()     # Confirmation after verification
    
    # ========== Editing the server ==========
    edit_server = State()            # Editing with navigation through parameters
    
    # ========== Deleting a server ==========
    delete_server_confirm = State()  # Deletion confirmation
    
    # ========== Section “Payments” ==========
    payments_menu = State()          # Main payment screen
    cards_setup_token = State()      # Entering the YuKassa token
    
    # ========== Setting up crypto payments ==========
    crypto_setup_url = State()       # Entering a link to a product
    crypto_setup_secret = State()    # Entering the secret key
    edit_crypto = State()            # Editing crypto settings

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
    add_tariff_price_rub = State()   # Step 2: Price in the current base currency
    add_tariff_duration = State()    # Step 3: Duration
    add_tariff_traffic_limit = State() # Step 4: Data Limit (GB)
    add_tariff_max_ips = State()     # Step 5: Device Limit (IP)
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
    key_change_traffic = State()     # Entering a new traffic limit
    
    # ========== Adding a key by administrator ==========
    add_key_server = State()         # Server selection
    add_key_inbound = State()        # Selecting inbound (protocol)
    add_key_traffic = State()        # Entering traffic limit (GB)
    add_key_days = State()           # Enter validity period (days)
    add_key_confirm = State()        # Creation Confirmation
    
    # ========== Management of tariff groups ==========
    group_add_name = State()         # Entering a new group name
    group_edit_name = State()        # Entering a new group name
    tariff_select_group = State()    # Selecting a group when adding a tariff
    server_select_group = State()    # Selecting a group when adding a server


# ============================================================================
# SERVER SETTINGS
# ============================================================================

SERVER_COMMON_PARAMS = [
    {
        "key": "name",
        "label": "Название",
        "hint": "например: Server-DE, Германия-1",
        "validate": lambda x: len(x) >= 2,
        "error": "Название должно быть минимум 2 символа"
    },
    {
        "key": "panel_url",
        "label": "URL панели",
        "hint": "например: https://192.168.1.1:2053/secretpath/ или просто 192.168.1.1:2053",
        "validate": lambda x: len(x.strip()) >= 5 and ":" in x,
        "error": "Введите корректную ссылку с портом, например: https://123.45.67.89:2053/api/"
    },
]

SERVER_API_TOKEN_PARAM = {
    "key": "api_token",
    "label": "API-ключ",
    "hint": "создайте отдельный токен в настройках безопасности 3X-UI и отправьте его сюда",
    "validate": lambda x: len(x.strip()) >= 8,
    "error": "API-ключ должен содержать минимум 8 символов"
}

SERVER_LOGIN_PARAMS = [
    {
        "key": "login",
        "label": "Логин",
        "hint": "логин для входа в панель",
        "validate": lambda x: len(x) >= 1,
        "error": "Введите логин"
    },
    {
        "key": "password",
        "label": "Пароль",
        "hint": "пароль для входа в панель",
        "validate": lambda x: len(x) >= 1,
        "error": "Введите пароль"
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
        "label": "Название",
        "hint": "например: Месяц, Полгода, Год",
        "validate": lambda x: 1 <= len(x) <= 50,
        "error": "Название от 1 до 50 символов"
    },
    {
        "key": "price_minor",
        "label": "Цена в базовой валюте",
        "hint": "положительное число, не более двух знаков после запятой",
        "validate": _valid_base_price,
        "error": "Введите положительную цену не более чем с двумя знаками после запятой",
        "convert": _convert_base_price,
        "format": _format_base_price,
        "help": "Курс Stars и USDT применяется автоматически в настройках оплаты."
    },
    {
        "key": "duration_days",
        "label": "Длительность",
        "hint": "в днях (1-99999)",
        "validate": lambda x: x.isdigit() and 1 <= int(x) <= 99999,
        "error": "Длительность от 1 до 99999 дней",
        "convert": int,
        "format": lambda x: f"{x} дн."
    },
    {
        "key": "traffic_limit_gb",
        "label": "Лимит трафика (ГБ)",
        "hint": "0 = безлимит, иначе число ГБ",
        "validate": lambda x: x.isdigit() and 0 <= int(x) <= 99999,
        "error": "Введите число от 0 до 99999 (0 = безлимит)",
        "convert": int,
        "format": lambda x: f"{x} ГБ" if x > 0 else "Безлимит"
    },
    {
        "key": "max_ips",
        "label": "Лимит устройств (IP)",
        "hint": "Минимум 1 (ограничение по IP адресам)",
        "validate": lambda x: x.isdigit() and 1 <= int(x) <= 999,
        "error": "Введите число от 1 до 999",
        "convert": int,
        "format": lambda x: f"{x} устр."
    },
    {
        "key": "display_order",
        "label": "Порядок отображения",
        "hint": "меньше = выше в списке (0-99)",
        "validate": lambda x: x.isdigit() and 0 <= int(x) <= 99,
        "error": "Порядок от 0 до 99",
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
        "label": "Ссылка на товар",
        "hint": "скопируйте из @Ya\\_SellerBot",
        "validate": lambda x: is_telegram_bot_start_link(
            x,
            bot_username="Ya_SellerBot",
            start_prefix="item",
        ),
        "error": "Ссылка должна вести на @Ya\\_SellerBot и содержать start=item"
    },
    {
        "key": "crypto_secret_key",
        "label": "Секретный ключ",
        "hint": "Профиль → Ключ подписи в @Ya\\_SellerBot",
        "validate": lambda x: len(x) >= 16,
        "error": "Ключ должен быть минимум 16 символов"
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

