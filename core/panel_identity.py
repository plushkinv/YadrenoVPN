"""Recover durable site-to-Telegram name changes in the existing scheduler."""
from __future__ import annotations

import json
import logging
import time

from core.results import CoreError
from database import requests as db
from runtime.readiness import require_active

logger = logging.getLogger(__name__)


def physical_panel_key(server: dict) -> str:
    path = str(server.get('web_base_path') or '').strip('/')
    return (f"{str(server.get('protocol') or 'https').casefold()}://"
            f"{str(server['host']).casefold()}:{int(server['port'])}" + ('/' + path if path else ''))


async def _resume(operation: dict) -> None:
    from bot.services.panels.identity import inspect_client_identity
    from bot.services.vpn_api import get_client_from_server_data
    key = db.get_vpn_key_by_id(operation['key_id'])
    if not key or any(key[field] != operation[field] for field in ('user_id', 'server_id', 'sub_id')):
        raise CoreError('panel_identity_changed')
    if key['panel_email'] != operation['old_email']:
        raise CoreError('panel_identity_changed')
    server = db.get_server_by_id(operation['server_id'])
    if server is None:
        raise CoreError('panel_unavailable', retryable=True)
    endpoint = physical_panel_key(server)
    server_ids = [item['id'] for item in db.get_all_servers() if physical_panel_key(item) == endpoint]
    if db.get_panel_binding_conflicts(server_ids, [operation['old_email'], operation['new_email']],
                                      operation['sub_id'], operation['key_id']):
        raise CoreError('panel_identity_conflict')
    client = get_client_from_server_data(server)
    if operation['snapshot_json']:
        snapshot = json.loads(operation['snapshot_json'])
    else:
        snapshot = await inspect_client_identity(client, operation['old_email'])
        if snapshot['sub_id'] != operation['sub_id']:
            raise CoreError('panel_identity_changed')
        snapshot['endpoint'] = endpoint
        snapshot = db.save_panel_identity_snapshot(operation['id'], snapshot)
    if snapshot.get('endpoint') != endpoint:
        raise CoreError('panel_identity_changed')
    await client.rename_client_identity(operation['old_email'], operation['new_email'], snapshot)
    db.finish_panel_identity_rename(operation['id'], int(time.time()))


async def process_panel_identity_renames(*, key_id: int | None = None) -> dict:
    require_active()
    from bot.services.panel_sync_coordinator import panel_sync_coordinator
    stats = {'seen': 0, 'done': 0, 'pending': 0}
    operations = ([db.get_pending_panel_identity(key_id)] if key_id is not None
                  else db.get_due_panel_identity_renames(int(time.time())))
    operations = [item for item in operations if item is not None]
    if not operations:
        return stats
    async with panel_sync_coordinator.try_manual() as acquired:
        if not acquired:
            stats['pending'] = len(operations)
            return stats
        for operation in operations:
            stats['seen'] += 1
            try:
                await _resume(operation)
            except Exception as error:
                code = error.code if isinstance(error, CoreError) else 'panel_unavailable'
                db.defer_panel_identity_rename(operation['id'], code, int(time.time()))
                logger.warning('Panel identity recovery deferred operation=%s code=%s', operation['id'], code)
                stats['pending'] += 1
            else:
                stats['done'] += 1
    return stats
