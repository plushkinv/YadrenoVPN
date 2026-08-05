from aiogram import Router
from .base import router as base_router
from .legacy import router as legacy_router
from .keys_config import router as keys_config_router
from .intent import router as intent_router
from .topup import router as topup_router

router = Router()
router.include_router(intent_router)
router.include_router(topup_router)
router.include_router(base_router)
router.include_router(legacy_router)
router.include_router(keys_config_router)
