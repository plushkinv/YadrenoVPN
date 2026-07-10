"""
FSM состояния для админ-панели.

Управление многошаговыми диалогами администратора.
"""
from aiogram.fsm.state import State, StatesGroup


class AdminStates(StatesGroup):
    """Состояния админ-панели."""
    
    # ========== Главное меню ==========
    admin_menu = State()  # Главный экран админки
    yadreno_waiting_api_key = State()  # Ввод персонального api_key Yadreno Admin
    yadreno_chat = State()  # Диалог с агентом Yadreno Admin
    
    # ========== Управление серверами ==========
    servers_list = State()           # Список серверов
    server_view = State()            # Просмотр конкретного сервера
    
    # ========== Добавление сервера (пошаговый диалог) ==========
    add_server_name = State()        # Шаг 1: Название
    add_server_url = State()         # Шаг 2: URL панели
    add_server_login = State()       # Шаг 3: Логин
    add_server_password = State()    # Шаг 4: Пароль
    add_server_confirm = State()     # Подтверждение после проверки
    
    # ========== Редактирование сервера ==========
    edit_server = State()            # Редактирование с навигацией по параметрам
    
    # ========== Удаление сервера ==========
    delete_server_confirm = State()  # Подтверждение удаления
    
    # ========== Раздел «Оплаты» ==========
    payments_menu = State()          # Главный экран оплат
    cards_setup_token = State()      # Ввод токена ЮКасса
    
    # ========== Настройка крипто-платежей ==========
    crypto_setup_url = State()       # Ввод ссылки на товар
    crypto_setup_secret = State()    # Ввод секретного ключа
    edit_crypto = State()            # Редактирование крипто-настроек

    # ========== Настройка QR-оплаты ЮКасса ==========
    qr_setup_shop_id = State()       # Ввод Shop ID
    qr_setup_secret_key = State()    # Ввод Secret Key

    # ========== Настройка WATA ==========
    wata_setup_token = State()       # Ввод JWT-токена WATA

    # ========== Настройка Platega ==========
    platega_setup_merchant = State()  # Ввод Merchant ID Platega
    platega_setup_secret = State()    # Ввод Secret Platega

    # ========== Настройка Cardlink ==========
    cardlink_setup_shop_id = State()    # Ввод Shop ID Cardlink
    cardlink_setup_api_token = State()  # Ввод API-токена Cardlink

    # ========== Реферальная система ==========
    referral_menu = State()          # Главное меню реферальной системы
    referral_level_edit = State()    # Редактирование уровня
    waiting_balance_amount = State()    # Ввод суммы баланса
    waiting_coefficient = State()        # Ввод коэффициента
    support_waiting_message = State()    # Ввод сообщения пользователю/ответа в поддержку
    promocode_add_code = State()
    promocode_add_discount = State()
    promocode_add_expires = State()
    promocode_add_limit = State()
    promocode_edit_value = State()
    coupon_setting_value = State()
    coupon_generate_discount = State()
    coupon_generate_lifetime = State()
    coupon_generate_count = State()
    
    # ========== Редактирование сообщений (универсальный редактор) ==========
    waiting_for_message = State()    # Ожидание нового сообщения (единое для всех редакторов)
    waiting_for_link_url = State()   # Ожидание ввода URL ссылки
    waiting_for_link_button_name = State()  # Ожидание ввода названия кнопки-ссылки
    
    # ========== Управление тарифами ==========
    tariffs_list = State()           # Список тарифов
    tariff_view = State()            # Просмотр конкретного тарифа
    
    # ========== Добавление тарифа (пошаговый диалог) ==========
    add_tariff_name = State()        # Шаг 1: Название
    add_tariff_price_cents = State() # Шаг 2: Цена в центах
    add_tariff_price_stars = State() # Шаг 3: Цена в звёздах
    add_tariff_price_rub = State()   # Шаг 4: Цена в рублях (карты)
    add_tariff_duration = State()    # Шаг 5: Длительность
    add_tariff_traffic_limit = State() # Шаг 6: Лимит трафика (ГБ)
    add_tariff_max_ips = State()     # Шаг 7: Лимит устройств (IP)
    add_tariff_confirm = State()     # Подтверждение

    # ========== Редактирование тарифа ==========
    edit_tariff = State()            # Редактирование с навигацией по параметрам
    
    # ========== Рассылка ==========
    broadcast_menu = State()         # Главный экран рассылки
    broadcast_waiting_message = State()      # Ожидание сообщения для рассылки
    broadcast_waiting_notify_days = State()  # Ожидание числа дней для уведомления
    
    # ========== Раздел «Пользователи» ==========
    users_menu = State()             # Главный экран раздела
    users_list = State()             # Список пользователей с пагинацией
    user_view = State()              # Просмотр конкретного пользователя
    waiting_user_id = State()        # Ожидание ввода telegram_id
    
    # ========== Управление VPN-ключом ==========
    key_view = State()               # Просмотр конкретного ключа
    key_extend_days = State()        # Ввод количества дней для продления
    key_change_traffic = State()     # Ввод нового лимита трафика
    
    # ========== Добавление ключа администратором ==========
    add_key_server = State()         # Выбор сервера
    add_key_inbound = State()        # Выбор inbound (протокола)
    add_key_traffic = State()        # Ввод лимита трафика (ГБ)
    add_key_days = State()           # Ввод срока действия (дней)
    add_key_confirm = State()        # Подтверждение создания
    
    # ========== Управление группами тарифов ==========
    group_add_name = State()         # Ввод названия новой группы
    group_edit_name = State()        # Ввод нового названия группы
    tariff_select_group = State()    # Выбор группы при добавлении тарифа
    server_select_group = State()    # Выбор группы при добавлении сервера


# ============================================================================
# ПАРАМЕТРЫ СЕРВЕРОВ
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
    """Получает параметр сервера по индексу."""
    if 0 <= index < len(SERVER_PARAMS):
        return SERVER_PARAMS[index]
    return SERVER_PARAMS[0]


def get_total_params() -> int:
    """Возвращает общее количество параметров сервера."""
    return len(SERVER_PARAMS)


# ============================================================================
# ПАРАМЕТРЫ ТАРИФОВ
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
    Получает параметр тарифа по индексу.
    
    Args:
        index: Индекс параметра
    """
    if 0 <= index < len(TARIFF_PARAMS):
        return TARIFF_PARAMS[index]
    return TARIFF_PARAMS[0]


def get_tariff_params_list() -> list:
    """Возвращает список параметров тарифа."""
    return TARIFF_PARAMS


def get_total_tariff_params() -> int:
    """Возвращает общее количество параметров тарифа."""
    return len(TARIFF_PARAMS)


# ============================================================================
# ПАРАМЕТРЫ КРИПТО-НАСТРОЕК
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
    """Получает параметр крипто-настроек по индексу."""
    if 0 <= index < len(CRYPTO_PARAMS):
        return CRYPTO_PARAMS[index]
    return CRYPTO_PARAMS[0]


def get_total_crypto_params() -> int:
    """Возвращает общее количество параметров крипто-настроек."""
    return len(CRYPTO_PARAMS)

