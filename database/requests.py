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
from database.db_backup import *
