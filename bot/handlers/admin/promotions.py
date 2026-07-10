import datetime
import html
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.admin import (
    coupons_menu_kb,
    promocode_detail_kb,
    promocodes_list_kb,
    promotion_cancel_kb,
)
from bot.states.admin_states import AdminStates
from bot.utils.admin import is_admin
from bot.utils.text import escape_html, get_message_text_for_storage, safe_edit_or_send
from database.requests import (
    create_coupon_batch,
    create_promo_code,
    get_coupon_auto_discount_percent,
    get_coupon_auto_enabled,
    get_coupon_auto_lifetime_days,
    get_promo_code_by_code,
    get_promo_code_by_id,
    get_promo_codes,
    is_base62_code,
    set_coupon_auto_discount_percent,
    set_coupon_auto_enabled,
    set_coupon_auto_lifetime_days,
    set_promo_code_active,
    update_promo_code,
)

logger = logging.getLogger(__name__)
router = Router()


def _format_date(value) -> str:
    return value or "без срока"


def _parse_expires(value: str):
    value = value.strip()
    if value in {"0", "-", "нет", "Нет"}:
        return None
    dt = datetime.datetime.strptime(value, "%Y-%m-%d")
    return dt.replace(hour=23, minute=59, second=59)


async def _delete_input(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


def _promocode_text(promo: dict, bot_username: str | None = None) -> str:
    limit = promo.get("activation_limit")
    limit_text = str(limit) if limit else "без лимита"
    usage = int(promo.get("usage_count") or 0)
    status = "включён" if promo.get("is_active") else "выключен"
    link_line = ""
    if bot_username:
        link_line = f"\n🔗 Промо-ссылка: <code>https://t.me/{escape_html(bot_username)}?start=pr_{escape_html(promo['code'])}</code>"
    return (
        "🎟 <b>Промокод</b>\n\n"
        f"Код: <b>{escape_html(promo['code'])}</b>\n"
        f"Скидка: <b>{int(promo.get('discount_percent') or 0)}%</b>\n"
        f"Срок: <b>{escape_html(_format_date(promo.get('expires_at')))}</b>\n"
        f"Лимит: <b>{escape_html(limit_text)}</b>\n"
        f"Активаций: <b>{usage}</b>\n"
        f"Статус: <b>{status}</b>"
        f"{link_line}\n\n"
        "Промокод можно вводить в поле оплаты или использовать как промо-ссылку."
    )


@router.callback_query(F.data == "admin_promocodes")
async def admin_promocodes(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await state.set_state(AdminStates.admin_menu)
    promocodes = get_promo_codes("promo")
    text = (
        "🎟 <b>Промокоды</b>\n\n"
        "Промокоды многоразовые. Их можно вводить вручную при оплате, а ещё каждый промокод работает как промо-ссылка формата "
        "<code>https://t.me/&lt;bot&gt;?start=pr_CODE</code>.\n\n"
        "Промо-ссылки удобно использовать в рекламе и партнёрских размещениях: бот сохранит код пользователю, а успешная покупка попадёт в аналитику."
    )
    await safe_edit_or_send(callback.message, text, reply_markup=promocodes_list_kb(promocodes))
    await callback.answer()


@router.callback_query(F.data == "admin_promocode_add")
async def admin_promocode_add(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    await state.set_state(AdminStates.promocode_add_code)
    await safe_edit_or_send(
        callback.message,
        "➕ <b>Новый промокод</b>\n\nВведите имя промокода. Можно использовать только <code>0-9</code>, <code>A-Z</code>, <code>a-z</code>.",
        reply_markup=promotion_cancel_kb("admin_promocodes"),
    )
    await callback.answer()


@router.message(AdminStates.promocode_add_code, F.text, ~F.text.startswith("/"))
async def promocode_add_code(message: Message, state: FSMContext):
    await _delete_input(message)
    code = get_message_text_for_storage(message, "plain").strip()
    if not is_base62_code(code):
        await safe_edit_or_send(message, "❌ Код должен быть в base62: <code>0-9</code>, <code>A-Z</code>, <code>a-z</code>.", reply_markup=promotion_cancel_kb("admin_promocodes"), force_new=True)
        return
    if get_promo_code_by_code(code):
        await safe_edit_or_send(message, "❌ Такой код уже существует. Введите другой код.", reply_markup=promotion_cancel_kb("admin_promocodes"), force_new=True)
        return
    await state.update_data(promocode_code=code)
    await state.set_state(AdminStates.promocode_add_discount)
    await safe_edit_or_send(message, "📊 <b>Скидка</b>\n\nВведите размер скидки от 0 до 100%.", reply_markup=promotion_cancel_kb("admin_promocodes"), force_new=True)


@router.message(AdminStates.promocode_add_discount, F.text, ~F.text.startswith("/"))
async def promocode_add_discount(message: Message, state: FSMContext):
    await _delete_input(message)
    value = get_message_text_for_storage(message, "plain").strip()
    if not value.isdigit() or not 0 <= int(value) <= 100:
        await safe_edit_or_send(message, "❌ Введите число от 0 до 100.", reply_markup=promotion_cancel_kb("admin_promocodes"), force_new=True)
        return
    await state.update_data(promocode_discount=int(value))
    await state.set_state(AdminStates.promocode_add_expires)
    await safe_edit_or_send(message, "⏳ <b>Срок действия</b>\n\nВведите дату в формате <code>YYYY-MM-DD</code> или <code>0</code>, если срок не ограничен.", reply_markup=promotion_cancel_kb("admin_promocodes"), force_new=True)


@router.message(AdminStates.promocode_add_expires, F.text, ~F.text.startswith("/"))
async def promocode_add_expires(message: Message, state: FSMContext):
    await _delete_input(message)
    raw = get_message_text_for_storage(message, "plain").strip()
    try:
        expires_at = _parse_expires(raw)
    except ValueError:
        await safe_edit_or_send(message, "❌ Неверная дата. Введите <code>YYYY-MM-DD</code> или <code>0</code>.", reply_markup=promotion_cancel_kb("admin_promocodes"), force_new=True)
        return
    await state.update_data(promocode_expires=expires_at)
    await state.set_state(AdminStates.promocode_add_limit)
    await safe_edit_or_send(message, "🔢 <b>Лимит активаций</b>\n\nВведите количество применений или <code>0</code> для многоразового промокода без лимита.", reply_markup=promotion_cancel_kb("admin_promocodes"), force_new=True)


@router.message(AdminStates.promocode_add_limit, F.text, ~F.text.startswith("/"))
async def promocode_add_limit(message: Message, state: FSMContext):
    await _delete_input(message)
    raw = get_message_text_for_storage(message, "plain").strip()
    if not raw.isdigit():
        await safe_edit_or_send(message, "❌ Введите целое число.", reply_markup=promotion_cancel_kb("admin_promocodes"), force_new=True)
        return
    data = await state.get_data()
    promo_id = create_promo_code(
        code=data["promocode_code"],
        discount_percent=data["promocode_discount"],
        expires_at=data.get("promocode_expires"),
        activation_limit=int(raw),
        created_by_admin_id=message.from_user.id,
        source="admin",
        code_type="promo",
    )
    await state.clear()
    promo = get_promo_code_by_id(promo_id)
    bot_info = await message.bot.get_me()
    await safe_edit_or_send(message, _promocode_text(promo, bot_info.username), reply_markup=promocode_detail_kb(promo), force_new=True)


@router.callback_query(F.data.startswith("admin_promocode_view:"))
async def admin_promocode_view(callback: CallbackQuery, state: FSMContext):
    promo_id = int(callback.data.split(":")[1])
    promo = get_promo_code_by_id(promo_id)
    if not promo:
        await callback.answer("Промокод не найден", show_alert=True)
        return
    bot_info = await callback.bot.get_me()
    await safe_edit_or_send(callback.message, _promocode_text(promo, bot_info.username), reply_markup=promocode_detail_kb(promo))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_promocode_toggle:"))
async def admin_promocode_toggle(callback: CallbackQuery):
    promo_id = int(callback.data.split(":")[1])
    promo = get_promo_code_by_id(promo_id)
    if not promo:
        await callback.answer("Промокод не найден", show_alert=True)
        return
    set_promo_code_active(promo_id, not bool(promo.get("is_active")))
    promo = get_promo_code_by_id(promo_id)
    bot_info = await callback.bot.get_me()
    await safe_edit_or_send(callback.message, _promocode_text(promo, bot_info.username), reply_markup=promocode_detail_kb(promo))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_promocode_edit_"))
async def admin_promocode_edit_start(callback: CallbackQuery, state: FSMContext):
    action, promo_id = callback.data.rsplit(":", 1)
    field = action.replace("admin_promocode_edit_", "")
    await state.update_data(promocode_edit_id=int(promo_id), promocode_edit_field=field)
    await state.set_state(AdminStates.promocode_edit_value)
    hints = {
        "discount": "Введите скидку от 0 до 100%.",
        "expires": "Введите дату <code>YYYY-MM-DD</code> или <code>0</code> без срока.",
        "limit": "Введите лимит применений или <code>0</code> без лимита.",
    }
    await safe_edit_or_send(callback.message, f"✏️ <b>Редактирование</b>\n\n{hints[field]}", reply_markup=promotion_cancel_kb(f"admin_promocode_view:{promo_id}"))
    await callback.answer()


@router.message(AdminStates.promocode_edit_value, F.text, ~F.text.startswith("/"))
async def admin_promocode_edit_value(message: Message, state: FSMContext):
    await _delete_input(message)
    data = await state.get_data()
    promo_id = int(data["promocode_edit_id"])
    field = data["promocode_edit_field"]
    raw = get_message_text_for_storage(message, "plain").strip()
    try:
        if field == "discount":
            if not raw.isdigit() or not 0 <= int(raw) <= 100:
                raise ValueError()
            update_promo_code(promo_id, discount_percent=int(raw))
        elif field == "expires":
            update_promo_code(promo_id, expires_at=_parse_expires(raw))
        elif field == "limit":
            if not raw.isdigit():
                raise ValueError()
            update_promo_code(promo_id, activation_limit=int(raw))
    except ValueError:
        await safe_edit_or_send(message, "❌ Значение не принято. Проверьте формат и попробуйте ещё раз.", reply_markup=promotion_cancel_kb(f"admin_promocode_view:{promo_id}"), force_new=True)
        return
    await state.clear()
    promo = get_promo_code_by_id(promo_id)
    bot_info = await message.bot.get_me()
    await safe_edit_or_send(message, _promocode_text(promo, bot_info.username), reply_markup=promocode_detail_kb(promo), force_new=True)


@router.callback_query(F.data == "admin_coupons")
async def admin_coupons(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await state.set_state(AdminStates.admin_menu)
    text = (
        "🎫 <b>Купоны</b>\n\n"
        "Купон — это одноразовый промокод. Его можно ввести в том же поле оплаты, что и обычный промокод. Купон можно подарить, разыграть в канале или передать другому человеку.\n\n"
        "Авто выдача при покупке помогает удерживать клиента: после каждой платной покупки бот выдаёт купон на следующую покупку. У купона фиксируются скидка и срок жизни на момент генерации, поэтому старые купоны не меняются при новых настройках."
    )
    await safe_edit_or_send(
        callback.message,
        text,
        reply_markup=coupons_menu_kb(
            get_coupon_auto_enabled(),
            get_coupon_auto_discount_percent(),
            get_coupon_auto_lifetime_days(),
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_coupons_toggle_auto")
async def admin_coupons_toggle_auto(callback: CallbackQuery, state: FSMContext):
    set_coupon_auto_enabled(not get_coupon_auto_enabled())
    await admin_coupons(callback, state)


@router.callback_query(F.data == "admin_coupons_edit_discount")
@router.callback_query(F.data == "admin_coupons_edit_lifetime")
async def admin_coupon_setting_start(callback: CallbackQuery, state: FSMContext):
    field = "discount" if callback.data.endswith("discount") else "lifetime"
    await state.update_data(coupon_setting_field=field)
    await state.set_state(AdminStates.coupon_setting_value)
    prompt = "Введите скидку от 0 до 100%." if field == "discount" else "Введите время жизни купона в днях."
    await safe_edit_or_send(callback.message, f"🎫 <b>Настройка купонов</b>\n\n{prompt}", reply_markup=promotion_cancel_kb("admin_coupons"))
    await callback.answer()


@router.message(AdminStates.coupon_setting_value, F.text, ~F.text.startswith("/"))
async def admin_coupon_setting_save(message: Message, state: FSMContext):
    await _delete_input(message)
    data = await state.get_data()
    raw = get_message_text_for_storage(message, "plain").strip()
    try:
        if data.get("coupon_setting_field") == "discount":
            if not raw.isdigit() or not 0 <= int(raw) <= 100:
                raise ValueError()
            set_coupon_auto_discount_percent(int(raw))
        else:
            if not raw.isdigit() or int(raw) <= 0:
                raise ValueError()
            set_coupon_auto_lifetime_days(int(raw))
    except ValueError:
        await safe_edit_or_send(message, "❌ Значение не принято. Введите корректное число.", reply_markup=promotion_cancel_kb("admin_coupons"), force_new=True)
        return
    await state.clear()
    await safe_edit_or_send(
        message,
        "✅ <b>Настройка сохранена</b>",
        reply_markup=coupons_menu_kb(get_coupon_auto_enabled(), get_coupon_auto_discount_percent(), get_coupon_auto_lifetime_days()),
        force_new=True,
    )


@router.callback_query(F.data == "admin_coupons_generate")
async def admin_coupons_generate(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.coupon_generate_discount)
    await safe_edit_or_send(callback.message, "🎲 <b>Генератор купонов</b>\n\nВведите размер скидки от 0 до 100%.", reply_markup=promotion_cancel_kb("admin_coupons"))
    await callback.answer()


@router.message(AdminStates.coupon_generate_discount, F.text, ~F.text.startswith("/"))
async def admin_coupons_generate_discount(message: Message, state: FSMContext):
    await _delete_input(message)
    raw = get_message_text_for_storage(message, "plain").strip()
    if not raw.isdigit() or not 0 <= int(raw) <= 100:
        await safe_edit_or_send(message, "❌ Введите число от 0 до 100.", reply_markup=promotion_cancel_kb("admin_coupons"), force_new=True)
        return
    await state.update_data(coupon_generate_discount=int(raw))
    await state.set_state(AdminStates.coupon_generate_lifetime)
    await safe_edit_or_send(message, "⏳ <b>Срок жизни</b>\n\nВведите количество дней.", reply_markup=promotion_cancel_kb("admin_coupons"), force_new=True)


@router.message(AdminStates.coupon_generate_lifetime, F.text, ~F.text.startswith("/"))
async def admin_coupons_generate_lifetime(message: Message, state: FSMContext):
    await _delete_input(message)
    raw = get_message_text_for_storage(message, "plain").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await safe_edit_or_send(message, "❌ Введите количество дней больше 0.", reply_markup=promotion_cancel_kb("admin_coupons"), force_new=True)
        return
    await state.update_data(coupon_generate_lifetime=int(raw))
    await state.set_state(AdminStates.coupon_generate_count)
    await safe_edit_or_send(message, "🔢 <b>Количество</b>\n\nВведите количество купонов. За один раз можно создать до 500.", reply_markup=promotion_cancel_kb("admin_coupons"), force_new=True)


@router.message(AdminStates.coupon_generate_count, F.text, ~F.text.startswith("/"))
async def admin_coupons_generate_count(message: Message, state: FSMContext):
    await _delete_input(message)
    raw = get_message_text_for_storage(message, "plain").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= 500:
        await safe_edit_or_send(message, "❌ Введите число от 1 до 500.", reply_markup=promotion_cancel_kb("admin_coupons"), force_new=True)
        return
    data = await state.get_data()
    coupons = create_coupon_batch(
        discount_percent=data["coupon_generate_discount"],
        lifetime_days=data["coupon_generate_lifetime"],
        count=int(raw),
        source="admin_generated",
        created_by_admin_id=message.from_user.id,
    )
    await state.clear()
    codes = "\n".join(coupon["code"] for coupon in coupons)
    text = (
        "✅ <b>Купоны сгенерированы</b>\n\n"
        f"Скидка: <b>{data['coupon_generate_discount']}%</b>\n"
        f"Срок жизни: <b>{data['coupon_generate_lifetime']} дн.</b>\n"
        f"Количество: <b>{len(coupons)}</b>\n\n"
        f"<pre>{html.escape(codes)}</pre>"
    )
    await safe_edit_or_send(message, text, reply_markup=promotion_cancel_kb("admin_coupons"), force_new=True)
