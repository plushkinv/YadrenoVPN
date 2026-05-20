from aiogram import Router

from .start import router as start_router
from .keys import router as keys_router
from .trial import router as trial_router
from .tariffs import router as tariffs_router

# These are packages/modules that were explicitly standalone
from .referral import router as referral_router
from .payments import router as payments_router
from bot.middlewares.page_context_reset import ResetAdminPageContextMiddleware

router = Router()
router.message.outer_middleware(ResetAdminPageContextMiddleware())
router.callback_query.outer_middleware(ResetAdminPageContextMiddleware())

# Порядок важен: специфичные роутеры с deep_link должны идти перед общим start_router
router.include_router(payments_router)
router.include_router(referral_router)
router.include_router(start_router)
router.include_router(keys_router)
router.include_router(trial_router)
router.include_router(tariffs_router)

__all__ = ["router"]
