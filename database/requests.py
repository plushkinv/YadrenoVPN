"""
Модуль запросов к базе данных.

Единственная точка доступа к БД для всех хендлеров.
Прямой SQL в хендлерах запрещён — используйте функции из этого модуля.
"""

from database.db_users import *
from database.db_keys import *
from database.db_payments import *
from database.db_servers import *
from database.db_tariffs import *
from database.db_stats import *
from database.db_groups import *
from database.db_settings import *
from database.db_pages import *
from database.db_page_routes import *
from database.db_extensions import *
from database.db_extension_core import *
from database.db_business_operations import *
from database.db_key_lifecycle import *
from database.db_payment_providers import *
from database.db_backup import *
from database.db_support import *
from database.db_promotions import *
