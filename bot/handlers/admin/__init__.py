"""
Connecting admin panel routers.
"""
from aiogram import Router

from bot.handlers.admin.main import router as main_router
from bot.handlers.admin.message_editor import router as message_editor_router
from bot.handlers.admin.servers import router as servers_router
from bot.handlers.admin.payments import router as payments_router
from bot.handlers.admin.tariffs import router as tariffs_router
from bot.handlers.admin.broadcast import router as broadcast_router
from bot.handlers.admin.users_list import router as users_list_router
from bot.handlers.admin.users_manage import router as users_manage_router
from bot.handlers.admin.users_keys import router as users_keys_router
from bot.handlers.admin.users_keys_deleted import router as users_keys_deleted_router
from bot.handlers.admin.system import router as system_router
from bot.handlers.admin.trial import router as trial_router
from bot.handlers.admin.referral import router as referral_router
from bot.handlers.admin.promotions import router as promotions_router
from bot.handlers.admin.groups import router as groups_router
from bot.handlers.admin.yadreno_admin import router as yadreno_admin_router
from bot.handlers.admin.support import router as support_router

admin_router = Router()

admin_router.include_router(main_router)
admin_router.include_router(message_editor_router)
admin_router.include_router(servers_router)
admin_router.include_router(payments_router)
admin_router.include_router(tariffs_router)
admin_router.include_router(groups_router)
admin_router.include_router(broadcast_router)
admin_router.include_router(users_list_router)
admin_router.include_router(users_manage_router)
admin_router.include_router(support_router)
admin_router.include_router(users_keys_router)
admin_router.include_router(users_keys_deleted_router)
admin_router.include_router(system_router)
admin_router.include_router(trial_router)
admin_router.include_router(referral_router)
admin_router.include_router(promotions_router)
admin_router.include_router(yadreno_admin_router)

