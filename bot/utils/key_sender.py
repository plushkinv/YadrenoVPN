"""
Утилита для отправки VPN-ключей пользователю.
"""
import logging
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services.vpn_api import get_client
from bot.utils.key_generator import generate_vless_link, generate_vless_json, generate_qr_code

logger = logging.getLogger(__name__)

async def send_key_with_qr(
    messageable, 
    key_data: dict, 
    key_manage_markup: InlineKeyboardMarkup = None,
    is_new: bool = False
):
    """
    Отправляет пользователю ключ с QR-кодом и файлом конфигурации.
    
    Args:
        messageable: Объект Message или CallbackQuery, куда отвечать
        key_data: Данные ключа из БД (должны содержать server_id, panel_email, client_uuid)
        key_manage_markup: Клавиатура управления ключом
        is_new: Является ли ключ только что созданным
    """
    try:
        # Проверяем наличие необходимых данных
        if not key_data.get('server_id') or not key_data.get('panel_email'):
             await _send_error(messageable, "Неполные данные ключа", key_manage_markup)
             return

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
                f"📋 *Ваш VPN-ключ*\n\n"
                f"```\n{uuid}\n```\n\n"
                "☝️ Нажмите на ключ, чтобы скопировать.\n"
                "⚠️ Не удалось получить полную конфигурацию (сервер недоступен).\n"
                "Попробуйте позже."
            )
            await _send_text(messageable, text, key_manage_markup)
            return

        # 2. Генерируем данные
        logger.info(f"Generating manual VLESS key for {key_data.get('panel_email')}")
        vless_link = generate_vless_link(config)
            
        vless_json = generate_vless_json(config)
        qr_bytes = generate_qr_code(vless_link)
        
        # 3. Формируем сообщение
        title = "✅ *Ваш новый VPN-ключ!*" if is_new else "📋 *Ваш VPN-ключ*"
        caption = (
            f"{title}\n\n"
            f"```\n{vless_link}\n```\n"
            "☝️ Нажмите на ссылку, чтобы скопировать.\n\n"
            "📱 *Инструкция:*\n"
            "1. Скопируйте ссылку или отсканируйте QR-код.\n"
            "2. Импортируйте в V2RayNG / Hiddify / Streisand.\n"
            "3. Нажмите подключиться!"
        )
        
        # Если caption слишком длинный (Telegram limit 1024), сокращаем
        if len(caption) > 1024:
             caption = (
                f"{title}\n\n"
                "👇 *Ваша ссылка доступа (нажмите для копирования):*\n"
                f"`{vless_link}`\n\n"
                "📸 Отсканируйте QR-код для быстрого подключения."
             )

        # 4. Отправляем фото с QR и ссылкой
        photo = BufferedInputFile(qr_bytes, filename="qrcode.png")
        
        # Определяем функцию отправки
        send_func = messageable.answer_photo if hasattr(messageable, 'answer_photo') else messageable.message.answer_photo
        
        # Отправляем JSON конфиг файлом
        config_file = BufferedInputFile(vless_json.encode('utf-8'), filename=f"vpn_config_{key_data.get('id', 'new')}.json")
        
        await send_func(
            photo=photo,
            caption=caption,
            parse_mode="Markdown"
        )
        
        # Отправляем файл и клавиатуру отдельным сообщением, если это callback
        # Или тем же, если позволяет контекст. 
        # Но answer_photo не поддерживает редактирование предыдущего текстового сообщения в фото.
        # Поэтому если мы пришли из callback (кнопка "Показать"), старое сообщение лучше удалить или изменить.
        
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
            caption="📂 *Файл конфигурации* (для ручного импорта)",
            reply_markup=key_manage_markup,
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Error sending key: {e}")
        await _send_error(messageable, f"Ошибка отправки ключа: {e}", key_manage_markup)


async def _send_error(messageable, text, markup):
    """Отправляет сообщение об ошибке."""
    msg_text = f"❌ {text}"
    if hasattr(messageable, 'edit_text'):
        await messageable.edit_text(msg_text, reply_markup=markup)
    elif hasattr(messageable, 'message') and hasattr(messageable.message, 'edit_text'):
         await messageable.message.edit_text(msg_text, reply_markup=markup)
    else:
        func = messageable.answer if hasattr(messageable, 'answer') else messageable.message.answer
        await func(msg_text, reply_markup=markup)


async def _send_text(messageable, text, markup):
    """Отправляет текстовое сообщение (fallback при отсутствии фото)."""
    if hasattr(messageable, 'edit_text'):
        await messageable.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    elif hasattr(messageable, 'message') and hasattr(messageable.message, 'edit_text'):
         await messageable.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        func = messageable.answer if hasattr(messageable, 'answer') else messageable.message.answer
        await func(text, reply_markup=markup, parse_mode="Markdown")
