"""
Обработчики удаления ключей и синхронизации удалённых ключей (админка).
"""
from aiogram import Router, F
from aiogram.types import CallbackQuery
import logging
import json

from bot.utils.text import safe_edit_or_send
from bot.keyboards.admin_users import (
    key_delete_confirm_kb,
    sync_deleted_menu_kb,
    sync_deleted_panel_confirm_kb,
    sync_deleted_db_confirm_kb,
    user_view_kb,
    users_menu_kb
)
from database.requests import (
    get_vpn_key_by_id,
    delete_vpn_key,
    get_user_vpn_keys,
    get_active_servers,
    get_all_servers,
    get_users_stats
)
from bot.services.vpn_api import get_client_from_server_data

logger = logging.getLogger(__name__)
router = Router()


# ──────────────────────────── Удаление одного ключа ────────────────────────────

@router.callback_query(F.data.startswith('admin_key_delete_ask:'))
async def on_key_delete_ask(callback: CallbackQuery):
    """Подтверждение удаления отдельного ключа."""
    key_id = int(callback.data.split(':')[1])
    key = get_vpn_key_by_id(key_id)

    if not key:
        await callback.answer("❌ Ключ не найден.", show_alert=True)
        return

    user_telegram_id = key.get('telegram_id', 0)

    await safe_edit_or_send(
        callback.message,
        f"⚠️ <b>Внимание!</b>\n\nВы действительно хотите удалить ключ <code>#{key_id}</code>?\n"
        f"Он будет безвозвратно удален из БД и навсегда удален с VPN-сервера.",
        reply_markup=key_delete_confirm_kb(key_id, user_telegram_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith('admin_key_delete_confirm:'))
async def on_key_delete_confirm(callback: CallbackQuery):
    """Удаление ключа: сначала из БД, потом с панели."""
    key_id = int(callback.data.split(':')[1])
    key = get_vpn_key_by_id(key_id)

    if not key:
        await callback.answer("❌ Ключ уже был удален.", show_alert=True)
        return

    user_telegram_id = key.get('telegram_id', 0)

    # 1. Сначала удаляем из БД — это главный источник правды
    delete_vpn_key(key_id)
    logger.info(f"Ключ #{key_id} удалён из БД (админ)")

    # 2. Затем пытаемся удалить с панели, если ключ был привязан
    panel_deleted = await _delete_key_from_panel(key)

    status = "✅ Ключ удалён из БД и с панели" if panel_deleted else "✅ Ключ удалён из БД"
    await callback.answer(status, show_alert=True)

    # Возвращаемся в профиль пользователя
    if user_telegram_id:
        from database.requests import get_user_by_telegram_id
        user = get_user_by_telegram_id(user_telegram_id)
        if user:
            keys = get_user_vpn_keys(user['id'])
            await safe_edit_or_send(
                callback.message,
                f"👤 Профиль пользователя: <b>{user.get('username', 'Нет')}</b> (ID: <code>{user['telegram_id']}</code>)",
                reply_markup=user_view_kb(
                    user['telegram_id'],
                    keys,
                    bool(user.get('is_banned', 0)),
                    user.get('balance_cents', 0),
                    user.get('referral_coefficient', 1.0)
                )
            )
            return

    # Фолбэк — меню пользователей
    stats = get_users_stats()
    await safe_edit_or_send(
        callback.message,
        "👥 <b>Управление пользователями</b>",
        reply_markup=users_menu_kb(stats)
    )


async def _delete_key_from_panel(key: dict) -> bool:
    """Удаляет ключ с панели, учитывая subscription-ключи во всех inbound."""
    key_id = key.get('id')
    if not (key.get('server_active') and key.get('host')):
        return False

    is_subscription_key = bool(key.get('sub_id') and key.get('panel_email'))
    is_single_key = bool(key.get('panel_inbound_id') and key.get('client_uuid'))
    if not (is_subscription_key or is_single_key):
        return False

    try:
        server_data = _build_server_data(key)
        client = get_client_from_server_data(server_data)

        if is_subscription_key:
            deleted_count = await client.delete_clients_by_email_on_server(key['panel_email'])
            if deleted_count > 0:
                logger.info(
                    f"Ключ #{key_id} удалён с панели {key.get('server_name')} "
                    f"по panel_email={key.get('panel_email')} ({deleted_count} inbound/client)"
                )
                return True
            logger.warning(
                f"Ключ #{key_id} не найден на панели {key.get('server_name')} "
                f"по panel_email={key.get('panel_email')}"
            )
            return False

        await client.delete_client(key['panel_inbound_id'], key['client_uuid'])
        logger.info(f"Ключ #{key_id} удалён с панели {key.get('server_name')}")
        return True
    except Exception as e:
        logger.warning(f"Не удалось удалить ключ #{key_id} с панели: {e}")
        return False


# ──────────────────── Синхронизация удалённых ключей ────────────────────────

@router.callback_query(F.data == 'admin_sync_deleted_menu')
async def on_sync_deleted_menu(callback: CallbackQuery):
    """Подменю синхронизации удаленных ключей."""
    await safe_edit_or_send(
        callback.message,
        "🗑️ <b>Синхронизация удалённых ключей</b>\n\n"
        "Выберите действие:\n"
        "1. <b>Очистить панель</b>: удаляет с VPN-серверов ключи, которых <b>нет в нашей базе</b>.\n"
        "2. <b>Очистить базу</b>: удаляет из нашей БД ключи, которых <b>нет на VPN-серверах</b>.\n\n"
        "⚠️ <b>Внимание: обе операции необратимы!</b>",
        reply_markup=sync_deleted_menu_kb()
    )
    await callback.answer()


# ──────────────── Очистка панели (удалить сирот с серверов) ─────────────────

@router.callback_query(F.data == 'admin_sync_deleted_panel_ask')
async def on_sync_deleted_panel_ask(callback: CallbackQuery):
    """Спрашиваем подтверждение на очистку панели."""
    await safe_edit_or_send(
        callback.message,
        "🧹 <b>Очистка панели</b>\n\n"
        "Вы собираетесь удалить с VPN-серверов ключи (почты которых начинаются на <code>user_</code>), "
        "которых нет в базе данных этого бота.\n\n"
        "Вы уверены?",
        reply_markup=sync_deleted_panel_confirm_kb()
    )
    await callback.answer()


@router.callback_query(F.data == 'admin_sync_deleted_panel_confirm')
async def on_sync_deleted_panel_confirm(callback: CallbackQuery):
    """Удаление 'осиротевших' ключей с VPN-серверов."""
    await safe_edit_or_send(
        callback.message,
        "⏳ <b>Очистка панели: собираю данные...</b>\n\nПожалуйста, подождите."
    )

    servers = get_active_servers()

    # Собираем ВСЕ panel_email из БД (включая ключи без server_id)
    from database.connection import get_db
    with get_db() as conn:
        rows = conn.execute(
            "SELECT LOWER(panel_email) as email FROM vpn_keys WHERE panel_email IS NOT NULL"
        ).fetchall()
    db_emails_all = {r['email'] for r in rows}

    deleted_count = 0
    errors_count = 0
    failed_servers = []

    for server in servers:
        try:
            client = get_client_from_server_data(server)
            inbounds = await client.get_inbounds()

            for inbound in inbounds:
                inbound_id = inbound['id']
                settings = json.loads(inbound.get('settings', '{}'))
                clients = settings.get('clients', [])

                for cl in clients:
                    cl_email = cl.get('email', '')
                    # Только ключи нашего бота (user_*), которых нет в БД
                    if cl_email.lower().startswith('user_') and cl_email.lower() not in db_emails_all:
                        try:
                            client_uuid = cl.get('id') or cl.get('password')
                            await client.delete_client(inbound_id, client_uuid)
                            deleted_count += 1
                            logger.info(f"Очистка панели: удалён сирота {cl_email} с {server['name']}")
                        except Exception as e:
                            logger.error(f"Очистка панели: ошибка удаления {cl_email}: {e}")
                            errors_count += 1

        except Exception as e:
            logger.error(f"Очистка панели: ошибка связи с {server['name']}: {e}")
            errors_count += 1
            failed_servers.append({'id': server['id'], 'name': server['name']})

    text_append = ""
    if failed_servers:
        failed_names = ", ".join([f"<b>{fs['name']}</b>" for fs in failed_servers])
        text_append = f"\n\n⚠️ <b>Не удалось подключиться к серверам:</b> {failed_names}"

    await safe_edit_or_send(
        callback.message,
        f"✅ <b>Очистка панели завершена</b>\n\n"
        f"🗑 Удалено ключей-сирот: <b>{deleted_count}</b>\n"
        f"❌ Ошибок: <b>{errors_count}</b>{text_append}",
        reply_markup=sync_deleted_menu_kb()
    )
    await callback.answer()


# ───────────── Очистка базы (сканирование + покатегорийное удаление) ──────────

@router.callback_query(F.data == 'admin_sync_deleted_db_ask')
async def on_sync_deleted_db_ask(callback: CallbackQuery):
    """Предлагаем запустить сканирование БД."""
    await safe_edit_or_send(
        callback.message,
        "🔍 <b>Сканирование базы данных</b>\n\n"
        "Бот проверит все серверы и покажет отчёт:\n"
        "• Ключи без привязки к серверу\n"
        "• Ключи удалённых серверов\n"
        "• Ключи, отсутствующие на панелях\n"
        "• Ключи недоступных серверов\n\n"
        "<b>Ничего не будет удалено автоматически</b> — вы сами выберете, что удалять.\n\n"
        "Начать сканирование?",
        reply_markup=sync_deleted_db_confirm_kb()
    )
    await callback.answer()


@router.callback_query(F.data == 'admin_sync_deleted_db_confirm')
async def on_sync_deleted_db_scan(callback: CallbackQuery):
    """Сканирование БД и серверов — показ отчёта-меню с категориями."""
    await safe_edit_or_send(
        callback.message,
        "⏳ <b>Сканирование: проверяю серверы...</b>\n\nПожалуйста, подождите."
    )

    report = await _scan_db_keys()
    text = _build_scan_report_text(report)

    from bot.keyboards.admin_users import sync_deleted_db_report_kb
    await safe_edit_or_send(
        callback.message,
        text,
        reply_markup=sync_deleted_db_report_kb(report)
    )
    await callback.answer()


# ──────────── Категория 1: ключи без сервера (server_id = NULL) ─────────────

@router.callback_query(F.data == 'admin_sync_db_orphans_ask')
async def on_sync_db_orphans_ask(callback: CallbackQuery):
    """Подтверждение удаления ключей без сервера."""
    from database.connection import get_db
    with get_db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM vpn_keys WHERE server_id IS NULL"
        ).fetchone()[0]
        unconfigured = conn.execute(
            "SELECT COUNT(*) FROM vpn_keys WHERE server_id IS NULL AND panel_email IS NULL"
        ).fetchone()[0]

    if total == 0:
        await callback.answer("✅ Ключей без сервера не найдено.", show_alert=True)
        return

    warning = ""
    if unconfigured > 0:
        warning = (
            f"\n\n⚠️ <b>Внимание:</b> среди них <b>{unconfigured}</b> ненастроенных — "
            f"это купленные, но ещё не активированные пользователями ключи! "
            f"Удаление лишит их возможности настроить оплаченный ключ."
        )

    from bot.keyboards.admin_users import sync_db_orphans_confirm_kb
    await safe_edit_or_send(
        callback.message,
        f"🗑️ <b>Удаление ключей без сервера</b>\n\n"
        f"Будет удалено <b>{total}</b> ключей, у которых не указан сервер.{warning}\n\n"
        f"Продолжить?",
        reply_markup=sync_db_orphans_confirm_kb()
    )
    await callback.answer()


@router.callback_query(F.data == 'admin_sync_db_orphans_confirm')
async def on_sync_db_orphans_confirm(callback: CallbackQuery):
    """Удаление ключей без сервера."""
    from database.connection import get_db
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id FROM vpn_keys WHERE server_id IS NULL"
        ).fetchall()

    deleted = 0
    for row in rows:
        try:
            delete_vpn_key(row['id'])
            deleted += 1
        except Exception as e:
            logger.error(f"Очистка БД (orphans): ошибка удаления ключа {row['id']}: {e}")

    await safe_edit_or_send(
        callback.message,
        f"✅ <b>Удалено ключей без сервера:</b> <b>{deleted}</b>",
        reply_markup=sync_deleted_menu_kb()
    )
    await callback.answer()


# ──────── Категория 2: ключи удалённого сервера (нет в таблице servers) ─────────

@router.callback_query(F.data.startswith('admin_sync_db_gone_ask:'))
async def on_sync_db_gone_ask(callback: CallbackQuery):
    """Подтверждение удаления ключей удалённого сервера."""
    server_id = int(callback.data.split(':')[1])

    from database.connection import get_db
    with get_db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM vpn_keys WHERE server_id = ?", (server_id,)
        ).fetchone()[0]

    if count == 0:
        await callback.answer("✅ Ключей не найдено.", show_alert=True)
        return

    from bot.keyboards.admin_users import sync_db_gone_confirm_kb
    await safe_edit_or_send(
        callback.message,
        f"👻 <b>Удаление ключей удалённого сервера</b>\n\n"
        f"Сервер с ID <b>{server_id}</b> больше не существует в базе данных бота.\n\n"
        f"Будет удалено <b>{count}</b> ключей, привязанных к этому серверу.\n\n"
        f"Продолжить?",
        reply_markup=sync_db_gone_confirm_kb(server_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith('admin_sync_db_gone_confirm:'))
async def on_sync_db_gone_confirm(callback: CallbackQuery):
    """Удаление ключей удалённого сервера."""
    server_id = int(callback.data.split(':')[1])

    from database.connection import get_db
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id FROM vpn_keys WHERE server_id = ?", (server_id,)
        ).fetchall()

    deleted = 0
    for row in rows:
        try:
            delete_vpn_key(row['id'])
            deleted += 1
        except Exception as e:
            logger.error(f"Очистка БД (gone): ошибка удаления ключа {row['id']}: {e}")

    await safe_edit_or_send(
        callback.message,
        f"✅ <b>Удалено ключей удалённого сервера ID {server_id}:</b> <b>{deleted}</b>",
        reply_markup=sync_deleted_menu_kb()
    )
    await callback.answer()


# ───────── Категория 3: ключи, отсутствующие на отвечающем сервере ──────────

@router.callback_query(F.data.startswith('admin_sync_db_missing_ask:'))
async def on_sync_db_missing_ask(callback: CallbackQuery):
    """Подтверждение удаления ключей, отсутствующих на панели (пересканирование)."""
    server_id = int(callback.data.split(':')[1])
    from database.requests import get_server_by_id
    server = get_server_by_id(server_id)

    if not server:
        await callback.answer("❌ Сервер не найден.", show_alert=True)
        return

    await safe_edit_or_send(
        callback.message,
        f"⏳ <b>Проверяю сервер {server['name']}...</b>"
    )

    try:
        client = get_client_from_server_data(server)
        inbounds = await client.get_inbounds()

        panel_emails = set()
        for inbound in inbounds:
            settings = json.loads(inbound.get('settings', '{}'))
            for cl in settings.get('clients', []):
                panel_emails.add(cl.get('email', '').lower())

        from database.connection import get_db
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, panel_email FROM vpn_keys WHERE server_id = ? AND panel_email IS NOT NULL",
                (server_id,)
            ).fetchall()

        missing_count = sum(1 for r in rows if r['panel_email'].lower() not in panel_emails)

        if missing_count == 0:
            await safe_edit_or_send(
                callback.message,
                f"✅ <b>{server['name']}</b>: все ключи на месте!",
                reply_markup=sync_deleted_menu_kb()
            )
            await callback.answer()
            return

        from bot.keyboards.admin_users import sync_db_missing_confirm_kb
        await safe_edit_or_send(
            callback.message,
            f"🗑️ <b>Удаление ключей с {server['name']}</b>\n\n"
            f"На сервере <b>{server['name']}</b> не найдено <b>{missing_count}</b> ключей.\n"
            f"Они есть в БД бота, но отсутствуют на панели 3X-UI.\n\n"
            f"Скорее всего они были удалены вручную через веб-интерфейс панели.\n\n"
            f"Удалить эти записи из БД?",
            reply_markup=sync_db_missing_confirm_kb(server_id)
        )
    except Exception as e:
        logger.error(f"Ошибка при пересканировании {server['name']}: {e}")
        await safe_edit_or_send(
            callback.message,
            f"❌ Не удалось подключиться к серверу <b>{server['name']}</b>.\n\n"
            f"Возможно, сервер стал недоступен после сканирования.",
            reply_markup=sync_deleted_menu_kb()
        )
    await callback.answer()


@router.callback_query(F.data.startswith('admin_sync_db_missing_confirm:'))
async def on_sync_db_missing_confirm(callback: CallbackQuery):
    """Удаление ключей, отсутствующих на панели (с повторной проверкой)."""
    server_id = int(callback.data.split(':')[1])
    from database.requests import get_server_by_id
    server = get_server_by_id(server_id)

    if not server:
        await callback.answer("❌ Сервер не найден.", show_alert=True)
        return

    await safe_edit_or_send(
        callback.message,
        f"⏳ <b>Удаляю ключи с {server['name']}...</b>"
    )

    try:
        client = get_client_from_server_data(server)
        inbounds = await client.get_inbounds()

        panel_emails = set()
        for inbound in inbounds:
            settings = json.loads(inbound.get('settings', '{}'))
            for cl in settings.get('clients', []):
                panel_emails.add(cl.get('email', '').lower())

        from database.connection import get_db
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, panel_email FROM vpn_keys WHERE server_id = ? AND panel_email IS NOT NULL",
                (server_id,)
            ).fetchall()

        deleted = 0
        for row in rows:
            if row['panel_email'].lower() not in panel_emails:
                try:
                    delete_vpn_key(row['id'])
                    deleted += 1
                    logger.info(f"Очистка БД: удалён {row['panel_email']} — нет на панели {server['name']}")
                except Exception as e:
                    logger.error(f"Очистка БД (missing): ошибка удаления ключа {row['id']}: {e}")

        await safe_edit_or_send(
            callback.message,
            f"✅ <b>Операция завершена</b>\n\n"
            f"🗑 Удалено с {server['name']}: <b>{deleted}</b>",
            reply_markup=sync_deleted_menu_kb()
        )
    except Exception as e:
        logger.error(f"Ошибка при удалении с {server['name']}: {e}")
        await safe_edit_or_send(
            callback.message,
            f"❌ Не удалось подключиться к серверу <b>{server['name']}</b>.",
            reply_markup=sync_deleted_menu_kb()
        )
    await callback.answer()


# ────────── Категория 4: ключи недоступного сервера (force delete) ──────────

@router.callback_query(F.data.startswith('admin_sync_db_unreach_ask:'))
async def on_sync_db_unreach_ask(callback: CallbackQuery):
    """Подтверждение принудительного удаления ключей недоступного сервера."""
    server_id = int(callback.data.split(':')[1])
    all_servers = get_all_servers()
    server_name = next((s['name'] for s in all_servers if s['id'] == server_id), f"ID {server_id}")

    from database.connection import get_db
    with get_db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM vpn_keys WHERE server_id = ?", (server_id,)
        ).fetchone()[0]

    if count == 0:
        await callback.answer("✅ Ключей не найдено.", show_alert=True)
        return

    from bot.keyboards.admin_users import sync_db_unreach_confirm_kb
    await safe_edit_or_send(
        callback.message,
        f"⚠️ <b>Внимание!</b>\n\n"
        f"Сервер <b>{server_name}</b> недоступен.\n\n"
        f"Вы действительно хотите удалить <b>ВСЕ {count}</b> ключей в базе данных, "
        f"которые привязаны к этому серверу?\n\n"
        f"🚨 <b>Чем это грозит:</b> Если сервер просто временно упал, перезагружается "
        f"или заблокирован РКН, вы безвозвратно удалите абсолютно рабочие ключи пользователей! "
        f"Они потеряют доступ к конфигурациям в боте и подписка может сломаться.",
        reply_markup=sync_db_unreach_confirm_kb(server_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith('admin_sync_db_unreach_confirm:'))
async def on_sync_db_unreach_confirm(callback: CallbackQuery):
    """Принудительное удаление ключей недоступного сервера."""
    server_id = int(callback.data.split(':')[1])

    await safe_edit_or_send(
        callback.message,
        "⏳ <b>Удаление ключей...</b>\n\nПожалуйста, подождите."
    )

    from database.connection import get_db
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id FROM vpn_keys WHERE server_id = ?", (server_id,)
        ).fetchall()

    deleted = 0
    for row in rows:
        try:
            delete_vpn_key(row['id'])
            deleted += 1
        except Exception as e:
            logger.error(f"Очистка БД (unreach): ошибка удаления ключа {row['id']}: {e}")

    await safe_edit_or_send(
        callback.message,
        f"✅ <b>Операция завершена</b>\n\n"
        f"🗑 Принудительно удалено ключей: <b>{deleted}</b>",
        reply_markup=sync_deleted_menu_kb()
    )
    await callback.answer()


# ──────────────────────── Утилиты ────────────────────────

async def _scan_db_keys() -> dict:
    """
    Сканирует все ключи в БД и серверы, классифицирует по категориям.
    
    Returns:
        Словарь с результатами сканирования по категориям
    """
    all_servers = get_all_servers()
    all_server_ids = {s['id'] for s in all_servers}

    from database.connection import get_db
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, panel_email, server_id FROM vpn_keys"
        ).fetchall()
    all_keys = [dict(r) for r in rows]

    # Категория 1: server_id = NULL
    null_server_keys = [k for k in all_keys if k.get('server_id') is None]
    null_total = len(null_server_keys)
    null_unconfigured = len([k for k in null_server_keys if not k.get('panel_email')])

    # Категория 2: server_id указывает на несуществующий сервер
    deleted_srv_keys = {}
    for key in all_keys:
        sid = key.get('server_id')
        if sid is not None and sid not in all_server_ids:
            if sid not in deleted_srv_keys:
                deleted_srv_keys[sid] = 0
            deleted_srv_keys[sid] += 1

    # Категории 3-5: результаты по серверам
    server_results = []
    for server in all_servers:
        sid = server['id']
        keys_on_server = [k for k in all_keys
                          if k.get('server_id') == sid and k.get('panel_email')]
        if not keys_on_server:
            continue

        result = {
            'server_id': sid,
            'name': server['name'],
            'is_active': bool(server.get('is_active')),
            'total_keys': len(keys_on_server),
        }

        try:
            client = get_client_from_server_data(server)
            inbounds = await client.get_inbounds()

            panel_emails = set()
            for inbound in inbounds:
                settings = json.loads(inbound.get('settings', '{}'))
                for cl in settings.get('clients', []):
                    panel_emails.add(cl.get('email', '').lower())

            missing = 0
            ok = 0
            for key in keys_on_server:
                email = (key.get('panel_email') or '').lower()
                if email not in panel_emails:
                    missing += 1
                else:
                    ok += 1

            result['status'] = 'reachable'
            result['missing_count'] = missing
            result['ok_count'] = ok
        except Exception as e:
            logger.error(f"Сканирование: ошибка связи с {server['name']}: {e}")
            result['status'] = 'unreachable'

        server_results.append(result)

    return {
        'null_total': null_total,
        'null_unconfigured': null_unconfigured,
        'deleted_srv_keys': deleted_srv_keys,
        'server_results': server_results,
    }


def _build_scan_report_text(report: dict) -> str:
    """Формирует текст отчёта из результатов сканирования."""
    lines = ["🔍 <b>Результаты сканирования</b>\n"]
    has_issues = False

    # Категория 1: без сервера
    if report['null_total'] > 0:
        has_issues = True
        text = f"📋 Ключи без сервера: <b>{report['null_total']}</b>"
        if report['null_unconfigured'] > 0:
            text += f"\n  ⚠️ Из них <b>{report['null_unconfigured']}</b> — ненастроенные (купленные, не активированные)"
        lines.append(text)

    # Категория 2: удалённые серверы
    for sid, count in report['deleted_srv_keys'].items():
        has_issues = True
        lines.append(f"\n👻 Удалённый сервер (ID {sid}): <b>{count}</b> ключей")

    # Категории 3-5: по серверам
    for r in report['server_results']:
        if r['status'] == 'reachable':
            if r.get('missing_count', 0) > 0:
                has_issues = True
                lines.append(
                    f"\n🟢 <b>{r['name']}</b>: <b>{r['missing_count']}</b> не найдено на панели, "
                    f"{r['ok_count']} ОК"
                )
            else:
                lines.append(f"\n✅ <b>{r['name']}</b>: все {r['ok_count']} ключей на месте")
        elif r['status'] == 'unreachable':
            has_issues = True
            active_text = "" if r['is_active'] else " (деактивирован)"
            emoji = "🔴" if r['is_active'] else "⏸️"
            lines.append(
                f"\n{emoji} <b>{r['name']}</b>{active_text}: не отвечает "
                f"({r['total_keys']} ключей)"
            )

    if not has_issues:
        lines.append("\n✅ Всё чисто — удалять нечего!")

    return "\n".join(lines)


def _build_server_data(key: dict) -> dict:
    """Формирует server_data из данных ключа для get_client_from_server_data."""
    return {
        'id': key.get('server_id'),
        'name': key.get('server_name'),
        'host': key.get('host'),
        'port': key.get('port'),
        'web_base_path': key.get('web_base_path', ''),
        'login': key.get('login'),
        'password': key.get('password'),
        'protocol': key.get('protocol', 'https')
    }
