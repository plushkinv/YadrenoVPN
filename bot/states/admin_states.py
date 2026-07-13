"""
FSM status for the admin panel.

Managing multi-step administrator dialogs.
"""
from aiogram.fsm.state import State, StatesGroup


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
    add_server_name = State()        # Step 1: Title
    add_server_url = State()         # Step 2: Panel URL
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
    platega_setup_secret = State()    # Enter Secret Platega

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
    add_tariff_price_cents = State() # Step 2: Price in cents
    add_tariff_price_stars = State() # Step 3: Price in stars
    add_tariff_price_rub = State()   # Step 4: Price in rubles (cards)
    add_tariff_duration = State()    # Step 5: Duration
    add_tariff_traffic_limit = State() # Step 6: Data Limit (GB)
    add_tariff_max_ips = State()     # Step 7: Device Limit (IP)
    add_tariff_confirm = State()     # Confirmation

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

SERVER_PARAMS = [
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


def get_param_by_index(index: int) -> dict:
    """Gets the server parameter by index."""
    if 0 <= index < len(SERVER_PARAMS):
        return SERVER_PARAMS[index]
    return SERVER_PARAMS[0]


def get_total_params() -> int:
    """Returns the total number of server parameters."""
    return len(SERVER_PARAMS)


# ============================================================================
# TARIFF PARAMETERS
# ============================================================================

TARIFF_PARAMS = [
    {
        "key": "name",
        "label": "Название",
        "hint": "например: Месяц, Полгода, Год",
        "validate": lambda x: 1 <= len(x) <= 50,
        "error": "Название от 1 до 50 символов"
    },
    {
        "key": "price_cents",
        "label": "Цена (USDT)",
        "hint": "в долларах: 3.00, 5.50, 10",
        "validate": lambda x: (
            x.replace('.', '', 1).replace(',', '', 1).isdigit() and 
            0.01 <= float(x.replace(',', '.')) <= 1000.00
        ),
        "error": "Цена от $0.01 до $1000.00",
        "convert": lambda x: int(float(x.replace(',', '.')) * 100),
        "format": lambda x: f"${(x / 100):g}".replace('.', ',')
    },
    {
        "key": "price_stars",
        "label": "Цена (Stars)",
        "hint": "в Telegram Stars (1-100000)",
        "validate": lambda x: x.isdigit() and 1 <= int(x) <= 100000,
        "error": "Цена от 1 до 100000 Stars",
        "convert": int,
        "format": lambda x: f"⭐ {x}"
    },
    {
        "key": "price_rub",
        "label": "Цена (₽)",
        "hint": "в целых рублях: минимум ~100 руб",
        "validate": lambda x: x.isdigit() and 0 <= int(x) <= 100000,
        "error": "Цена от 0 до 100000 рублей (целое число)",
        "convert": int,
        "format": lambda x: f"{x} ₽",
        "help": "⚠️ *Важно:* Telegram не позволяет проводить платежи меньше $1. Минимальная цена в рублях должна быть не менее ~100 руб, иначе бот вернет ошибку. Чтобы скрыть тариф из раздела оплат картами - установите 0."
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
        "validate": lambda x: x.startswith("https://t.me/Ya_SellerBot?start=item"),
        "error": "Ссылка должна начинаться с https://t.me/Ya\\_SellerBot?start=item"
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

