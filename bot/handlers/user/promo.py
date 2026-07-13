import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.states.user_states import PromoInput
from bot.utils.page_renderer import render_page, render_page_text
from bot.utils.text import escape_html, get_message_text_for_storage
from database.requests import get_user_internal_id, has_available_promo_codes, is_base62_code

logger = logging.getLogger(__name__)

router = Router()

PROMO_ENTER_PAGE_KEY = "promo_enter"
PROMO_STATUS_PAGE_KEY = "promo_status"


def default_promo_enter_page_text() -> str:
    """Default text for entering a promotional code or coupon."""
    return (
        "🎟 <b>Промокод</b>\n\n"
        "Отправьте промокод или одноразовый купон одним сообщением.\n\n"
        "Ручной ввод заменит промокод, который мог быть сохранён по промо-ссылке."
    )


def render_promo_enter_page_text() -> str:
    """Renders the promo_enter text from pages with fallback set to default."""
    return render_page_text(PROMO_ENTER_PAGE_KEY) or default_promo_enter_page_text()


def build_promo_status_page_context(title_html: str, body_html: str) -> dict:
    """Generates the runtime context of the promo code result."""
    return {
        'promo_status_title_html': title_html,
        'promo_status_body_html': body_html,
    }


def _promo_return_kb(key_id: int | None = None) -> InlineKeyboardMarkup:
    callback = f"key_renew:{key_id}" if key_id else "buy_key"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 К оплате", callback_data=callback)],
            [InlineKeyboardButton(text="🈴 На главную", callback_data="start")],
        ]
    )


def _promo_cancel_kb(key_id: int | None = None) -> InlineKeyboardMarkup:
    callback = f"key_renew:{key_id}" if key_id else "buy_key"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=callback)],
            [InlineKeyboardButton(text="🈴 На главную", callback_data="start")],
        ]
    )


async def render_promo_status_page(
    target,
    title_html: str,
    body_html: str | None = None,
    body_text: str | None = None,
    *,
    append_buttons: list[list[InlineKeyboardButton]] | None = None,
    force_new: bool = False,
) -> None:
    """Renders the page-backed result of processing a promotional code or coupon."""
    if body_html is None:
        body_html = escape_html('' if body_text is None else str(body_text))

    await render_page(
        target,
        page_key=PROMO_STATUS_PAGE_KEY,
        context=build_promo_status_page_context(title_html, body_html),
        append_buttons=append_buttons,
        force_new=force_new,
    )


@router.callback_query(F.data == "promo_enter")
@router.callback_query(F.data.startswith("promo_enter:"))
async def promo_enter_handler(callback: CallbackQuery, state: FSMContext):
    """Requests a promotional code or coupon from the user."""
    key_id = None
    if ":" in callback.data:
        try:
            key_id = int(callback.data.split(":", 1)[1])
        except (TypeError, ValueError):
            key_id = None

    if not has_available_promo_codes():
        await render_promo_status_page(
            callback,
            title_html="🎟 <b>Промокод</b>",
            body_text="Сейчас нет доступных промокодов или купонов. Вернитесь к оплате и выберите другой способ.",
            append_buttons=_promo_return_kb(key_id).inline_keyboard,
        )
        await callback.answer()
        return

    await state.update_data(
        promo_key_id=key_id,
        promo_message_id=callback.message.message_id,
        promo_chat_id=callback.message.chat.id,
    )
    await state.set_state(PromoInput.waiting_for_code)

    await render_page(
        callback,
        page_key=PROMO_ENTER_PAGE_KEY,
        context={"promo_key_id": key_id},
        append_buttons=_promo_cancel_kb(key_id).inline_keyboard,
        fallback_text=default_promo_enter_page_text(),
    )
    await callback.answer()


@router.message(PromoInput.waiting_for_code, F.text, ~F.text.startswith("/"))
async def promo_code_input_handler(message: Message, state: FSMContext):
    """Saves the user's active promo code."""
    from bot.services.promotions import activate_promo_code_for_user

    data = await state.get_data()
    key_id = data.get("promo_key_id")
    code = get_message_text_for_storage(message, "plain").strip()

    try:
        await message.delete()
    except Exception:
        pass

    if not is_base62_code(code):
        await render_promo_status_page(
            message,
            title_html="🎟 <b>Промокод</b>",
            body_html=(
                "Код может содержать только символы <code>0-9</code>, <code>A-Z</code> "
                "и <code>a-z</code>.\n\n"
                "Отправьте код ещё раз."
            ),
            append_buttons=_promo_cancel_kb(key_id).inline_keyboard,
            force_new=True,
        )
        return

    user_id = get_user_internal_id(message.from_user.id)
    if not user_id:
        await render_promo_status_page(
            message,
            title_html="❌ <b>Ошибка</b>",
            body_text="Пользователь не найден. Откройте главное меню и попробуйте снова.",
            append_buttons=_promo_cancel_kb(key_id).inline_keyboard,
            force_new=True,
        )
        await state.clear()
        return

    result = activate_promo_code_for_user(user_id, code)
    if not result["ok"]:
        await render_promo_status_page(
            message,
            title_html="🎟 <b>Промокод</b>",
            body_text=(
                f"{result['message']}\n\n"
                "Проверьте код и отправьте его ещё раз."
            ),
            append_buttons=_promo_cancel_kb(key_id).inline_keyboard,
            force_new=True,
        )
        return

    await state.clear()
    promo = result["promo"]
    await render_promo_status_page(
        message,
        title_html="✅ <b>Промокод применён</b>",
        body_html=(
            f"Код: <b>{escape_html(promo['code'])}</b>\n"
            f"Скидка: <b>{int(promo.get('discount_percent') or 0)}%</b>\n\n"
            "Теперь выберите способ оплаты и тариф заново."
        ),
        append_buttons=_promo_return_kb(key_id).inline_keyboard,
        force_new=True,
    )
