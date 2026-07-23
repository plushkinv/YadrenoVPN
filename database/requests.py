"""
Database query module.

The only access point to the database for all handlers.
Direct SQL is prohibited in handlers - use functions from this module.
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
from database.db_payment_auto_checks import *
from database.db_payment_intents import *
from database.db_currency import *
from database.db_broadcast_editor import *
from database.db_backup import *
from database.db_customization_reset import *
from database.db_support import *
from database.db_promotions import *
from database.db_user_ui_texts import *
