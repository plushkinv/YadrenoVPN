from aiogram import Router
from .base import router as base_router
from .balance import router as balance_router
from .yookassa import router as yookassa_router
from .wata import router as wata_router
from .platega import router as platega_router
from .cardlink import router as cardlink_router
from .stars import router as stars_router
from .crypto import router as crypto_router
from .keys_config import router as keys_config_router
from .demo import router as demo_router
from .custom import router as custom_router

router = Router()
router.include_router(base_router)
router.include_router(balance_router)
router.include_router(yookassa_router)
router.include_router(wata_router)
router.include_router(platega_router)
router.include_router(cardlink_router)
router.include_router(stars_router)
router.include_router(crypto_router)
router.include_router(keys_config_router)
router.include_router(demo_router)
router.include_router(custom_router)
