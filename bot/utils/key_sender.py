"""
Утилита для отправки VPN-ключей пользователю.
"""
import logging
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services.vpn_api import get_client
from bot.utils.key_generator import generate_link, generate_json, generate_qr_code
from bot.utils.text import escape_html

logger = logging.getLogger(__name__)

KEY_COPY_PLACEHOLDER = '%ключ%'
KEY_LINK_PLACEHOLDER = '%ссылка%'
KEY_DELIVERY_PAGE = 'key_delivery'


# Дефолтный текст выдачи ключа в формате HTML
DEFAULT_KEY_DELIVERY_TEXT = (
    "✅ <b>Ваш VPN-ключ!</b>\n\n"
    f"{KEY_COPY_PLACEHOLDER}\n"
    "☝️ Нажмите, чтобы скопировать.\n\n"
    "📱 <b>Инструкция:</b>\n"
    "1. Скопируйте ссылку или отсканируйте QR-код.\n"
    "2. Импортируйте в свой клиент. Какой именно клиент подходит, смотри в инструкции по кнопке ниже.\n"
    "3. Нажмите подключиться!"
)


def format_key_copy_value(raw_value: str) -> str:
    """Форматирует ключ/подписку как копируемый моноширинный фрагмент."""
    return f"<code>{escape_html(raw_value)}</code>"


def format_key_plain_link(raw_value: str) -> str:
    """
    Возвращает чистую ссылку без code/pre.

    HTTP/HTTPS subscription-ссылки Telegram показывает кликабельными. Для
    custom-схем вроде vless:// ссылка остаётся обычным текстом, если клиент
    Telegram не поддерживает такой переход.
    """
    return escape_html(raw_value)


def build_key_delivery_text(template: str, raw_value: str) -> str:
    """Подставляет плейсхолдеры выдачи ключа в редактируемый текст."""
    return (
        template
        .replace(KEY_COPY_PLACEHOLDER, format_key_copy_value(raw_value))
        .replace(KEY_LINK_PLACEHOLDER, format_key_plain_link(raw_value))
    )


def build_compact_delivery_text(
    title: str,
    raw_value: str,
    copy_label: str,
    qr_hint: str,
) -> str:
    """Фоллбэк для caption Telegram: сначала пробуем оставить оба варианта ссылки."""
    compact = (
        f"{title}\n\n"
        f"👇 <b>{copy_label}:</b>\n"
        f"{format_key_copy_value(raw_value)}\n\n"
        "🔗 <b>Чистая ссылка:</b>\n"
        f"{format_key_plain_link(raw_value)}\n\n"
        f"{qr_hint}"
    )
    if len(compact) <= 1024:
        return compact

    return (
        f"{title}\n\n"
        f"👇 <b>{copy_label}:</b>\n"
        f"{format_key_copy_value(raw_value)}\n\n"
        f"{qr_hint}"
    )


async def send_key_with_qr(
    messageable,
    key_data: dict,
    key_manage_markup: InlineKeyboardMarkup = None,
    is_new: bool = False
):
    """
    Отправляет пользователю ключ с QR-кодом и файлом конфигурации.

    Использует единый HTML-контракт для текстов из редактора.

    В режиме subscription (key_data['sub_id'] не пустой И is_subscription_mode):
    выдаёт subscription URL и QR этой ссылки; JSON-файл не отправляется.

    Args:
        messageable: Объект Message или CallbackQuery, куда отвечать
        key_data: Данные ключа из БД (должны содержать server_id, panel_email, client_uuid)
        key_manage_markup: Клавиатура управления ключом
        is_new: Является ли ключ только что созданным
    """
    from bot.services.vpn_api import is_subscription_mode, get_subscription_url_for_key
    from bot.utils.message_editor import get_message_data

    try:
        # Проверяем наличие необходимых данных
        if not key_data.get('server_id') or not key_data.get('panel_email'):
             await _send_error(messageable, "Неполные данные ключа", key_manage_markup)
             return

        # === Subscription mode: выдаём subscription URL + QR этой ссылки ===
        if key_data.get('sub_id') and is_subscription_mode():
            sub_url = await get_subscription_url_for_key(key_data)
            if not sub_url:
                await _send_error(messageable,
                    "Не удалось получить subscription URL. "
                    "Проверьте, что на панели 3X-UI включена подписка "
                    "(Settings → Subscription → Enable).",
                    key_manage_markup)
                return

            delivery_data = get_message_data(KEY_DELIVERY_PAGE, DEFAULT_KEY_DELIVERY_TEXT)
            base_caption = delivery_data.get('text', DEFAULT_KEY_DELIVERY_TEXT)
            caption = build_key_delivery_text(base_caption, sub_url)
            if len(caption) > 1024:
                title = "✅ <b>Ваша подписка!</b>" if is_new else "📋 <b>Ваша подписка</b>"
                caption = build_compact_delivery_text(
                    title=title,
                    raw_value=sub_url,
                    copy_label="Ваша subscription-ссылка",
                    qr_hint="📸 Отсканируйте QR-код, чтобы импортировать подписку в клиент.",
                )

            qr_bytes = generate_qr_code(sub_url)
            photo = BufferedInputFile(qr_bytes, filename="subscription_qr.png")
            send_func = messageable.answer_photo if hasattr(messageable, 'answer_photo') else messageable.message.answer_photo

            await send_func(
                photo=photo,
                caption=caption,
                reply_markup=key_manage_markup,
                parse_mode="HTML",
            )

            # Удаляем старое сообщение для CallbackQuery
            if hasattr(messageable, 'message'):
                try:
                    await messageable.message.delete()
                except Exception:
                    pass
            return

        # === Keys-mode: текущая логика (ссылка + QR + JSON) ===

        # 1. Получаем конфигурацию с сервера
        try:
            client = await get_client(key_data['server_id'])
            config = await client.get_client_config(key_data['panel_email'])
        except Exception as e:
            logger.error(f"Failed to get client config: {e}")
            config = None
            
        if not config:
            # Если не удалось получить конфиг (например, сервер недоступен),
            # отправляем просто UUID (как раньше)
            uuid = key_data.get('client_uuid', 'Unknown')
            text = (
                f"📋 <b>Ваш VPN-ключ</b>\n\n"
                f"{format_key_copy_value(uuid)}\n\n"
                "☝️ Нажмите на ключ, чтобы скопировать.\n"
                "⚠️ Не удалось получить полную конфигурацию (сервер недоступен).\n"
                "Попробуйте позже."
            )
            await _send_text(messageable, text, key_manage_markup)
            return

        # 2. Генерируем данные
        logger.info(f"Generating key for {key_data.get('panel_email')} (protocol: {config.get('protocol', 'vless')})")
        link = generate_link(config)
            
        json_config = generate_json(config)
        qr_bytes = generate_qr_code(link)
        
        # 3. Формируем сообщение через единый helper
        from bot.utils.message_editor import get_message_data
        
        delivery_data = get_message_data(KEY_DELIVERY_PAGE, DEFAULT_KEY_DELIVERY_TEXT)
        base_caption = delivery_data.get('text', DEFAULT_KEY_DELIVERY_TEXT)
        
        caption = build_key_delivery_text(base_caption, link)
        
        # Если caption слишком длинный (Telegram limit 1024), сокращаем
        if len(caption) > 1024:
            title = "✅ <b>Ваш новый VPN-ключ!</b>" if is_new else "📋 <b>Ваш VPN-ключ</b>"
            caption = build_compact_delivery_text(
                title=title,
                raw_value=link,
                copy_label="Ваша ссылка доступа",
                qr_hint="📸 Отсканируйте QR-код для быстрого подключения.",
            )

        # 4. Отправляем фото с QR и ссылкой
        photo = BufferedInputFile(qr_bytes, filename="qrcode.png")
        
        # Определяем функцию отправки
        send_func = messageable.answer_photo if hasattr(messageable, 'answer_photo') else messageable.message.answer_photo
        
        # Отправляем JSON конфиг файлом
        config_file = BufferedInputFile(json_config.encode('utf-8'), filename=f"vpn_config_{key_data.get('id', 'new')}.json")
        
        await send_func(
            photo=photo,
            caption=caption,
            parse_mode="HTML"
        )
        
        # Отправляем файл и клавиатуру отдельным сообщением
        if hasattr(messageable, 'message'): # Это CallbackQuery
            try:
                await messageable.message.delete()
            except:
                pass
            answer_func = messageable.message.answer_document
        else: # Это Message
            answer_func = messageable.answer_document

        await answer_func(
            document=config_file,
            caption="📂 <b>Файл конфигурации</b> (для ручного импорта)",
            reply_markup=key_manage_markup,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Error sending key: {e}")
        await _send_error(messageable, f"Ошибка отправки ключа: {e}", key_manage_markup)


async def _send_error(messageable, text, markup):
    """Отправляет сообщение об ошибке."""
    from bot.utils.text import safe_edit_or_send
    msg_text = f"❌ {text}"
    # Определяем Message для safe_edit_or_send
    if hasattr(messageable, 'text') or hasattr(messageable, 'photo'):
        # Это Message
        await safe_edit_or_send(messageable, msg_text, reply_markup=markup)
    elif hasattr(messageable, 'message'):
        # Это CallbackQuery
        await safe_edit_or_send(messageable.message, msg_text, reply_markup=markup)
    else:
        func = messageable.answer if hasattr(messageable, 'answer') else messageable.message.answer
        await func(msg_text, reply_markup=markup)


async def _send_text(messageable, text, markup):
    """Отправляет текстовое сообщение (fallback при отсутствии фото). HTML."""
    from bot.utils.text import safe_edit_or_send
    if hasattr(messageable, 'text') or hasattr(messageable, 'photo'):
        await safe_edit_or_send(messageable, text, reply_markup=markup)
    elif hasattr(messageable, 'message'):
        await safe_edit_or_send(messageable.message, text, reply_markup=markup)
    else:
        func = messageable.answer if hasattr(messageable, 'answer') else messageable.message.answer
        await func(text, reply_markup=markup, parse_mode="HTML")
