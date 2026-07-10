from aiogram import Router

from .start import router as start_router
from .keys import router as keys_router
from .trial import router as trial_router
from .tariffs import router as tariffs_router
from .custom_pages import router as custom_pages_router
from .page_routes import router as page_routes_router
from .extension_callbacks import router as extension_callbacks_router
from .support import router as support_router
from .promo import router as promo_router

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
router.include_router(support_router)
router.include_router(promo_router)
router.include_router(extension_callbacks_router)
router.include_router(page_routes_router)
router.include_router(start_router)
router.include_router(custom_pages_router)
router.include_router(keys_router)
router.include_router(trial_router)
router.include_router(tariffs_router)

__all__ = ["router"]
