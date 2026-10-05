"""Attach a panel subscription by renaming its clients into ordinary account keys."""
from __future__ import annotations

import time
from urllib.parse import unquote, urlsplit

from core.context import AccountContext
from core.panel_identity import physical_panel_key, resume_panel_identity_renames
from core.results import CoreError
from database import requests as db
from runtime.readiness import require_active


def _url(value: str) -> tuple[str, str, int, str]:
    try:
        parsed = urlsplit(value)
        if (not isinstance(value, str) or len(value) > 2048 or
                any(ord(char) < 33 for char in value) or parsed.scheme not in {'http', 'https'} or
                not parsed.hostname or parsed.username is not None or parsed.password is not None or
                parsed.query or parsed.fragment):
            raise ValueError()
        return (parsed.scheme, parsed.hostname.casefold(), parsed.port or
                (443 if parsed.scheme == 'https' else 80), parsed.path)
    except (AttributeError, TypeError, ValueError):
        raise CoreError('subscription_url_invalid') from None


async def _matching_sub_id(client, value: str) -> str | None:
    supplied = _url(value)
    settings = await client.get_panel_settings()
    if not settings or not client._api_bool(settings.get('subEnable'), False):
        return None
    marker = 'web_core_subscription_identifier'
    bases = [await client.build_subscription_url(marker)]
    # The advertised reverse-proxy alias and configured native endpoint both
    # identify this panel. They are never used as network request destinations.
    tls = bool(settings.get('subCertFile') and settings.get('subKeyFile'))
    scheme = 'https' if tls else 'http'
    port = int(settings.get('subPort') or (443 if tls else 80))
    path = '/' + str(settings.get('subPath') or '').strip('/') + '/'
    path = path.replace('//', '/')
    for host in {str(settings.get('subDomain') or ''), client.host} - {''}:
        if ':' in host and not host.startswith('['):
            host = '[' + host + ']'
        bases.append(f'{scheme}://{host}:{port}{path}{marker}')
    for base in bases:
        if not base:
            continue
        expected = _url(base)
        prefix = expected[3][:-len(marker)]
        if supplied[:3] != expected[:3] or not supplied[3].startswith(prefix):
            continue
        candidate = unquote(supplied[3][len(prefix):], errors='strict')
        if (not candidate or len(candidate) > 256 or any(ord(char) < 33 for char in candidate) or
                any(char in candidate for char in '/?#%\\')):
            continue
        return candidate
    return None


async def _group_candidates(endpoint, members):
    from bot.services.vpn_api import get_client_from_server_data
    servers = [server for server in db.get_all_servers()
               if server['is_active'] and physical_panel_key(server) == endpoint]
    placements = [{item['inbound_id'] for item in member['placements']} for member in members]
    scoped = {}
    for server in servers:
        inbounds = await get_client_from_server_data(server).get_inbounds(include_ignored=True)
        scoped[server['id']] = {int(item['id']) for item in inbounds}
    result = {}
    for group in db.get_all_groups():
        available = [server for server in servers if group['id'] in db.get_server_group_ids(server['id'])]
        selected = []
        for ids in placements:
            matching = [server for server in available if ids and ids <= scoped[server['id']]]
            if not matching:
                break
            selected.append(min(matching, key=lambda server: (len(scoped[server['id']]), server['id'])))
        if len(selected) == len(members) and db.get_admin_custom_tariff(group['id']):
            result[group['id']] = {'id': group['id'], 'name': group['name'], 'servers': selected}
    return result


def _validate_members(snapshot):
    """Only accept terms representable by the ordinary key model."""
    if len(snapshot['sub_ids']) != 1:
        raise CoreError('subscription_ambiguous')
    mode = db.get_device_limit_mode()
    field = 'limitHwid' if mode == db.DEVICE_LIMIT_MODE_HWID else 'limitIp'
    for member in snapshot['members']:
        record = member['record']
        limit = int(record.get(field) or 0)
        if not 0 <= limit <= 999 or int(record.get('totalGB') or 0) < 0 or member['traffic_used'] < 0:
            raise CoreError('subscription_ambiguous')
        if not member['placements'] or any(
                any(item['client'].get(name, 0) != record.get(name, 0)
                    for name in ('subId', 'expiryTime', 'totalGB', 'limitIp', 'limitHwid'))
                for item in member['placements']):
            raise CoreError('subscription_ambiguous')
        member['device_limit'] = limit


def _owned_keys(user_id, endpoint, sub_ids, names=()):
    keys = db.get_panel_subscription_keys(endpoint, sub_ids, names)
    if any(key['user_id'] != user_id for key in keys):
        raise CoreError('subscription_owned')
    return keys


async def import_subscription(context: AccountContext, subscription_url: str, group_id: int | None = None) -> dict:
    require_active()
    if not isinstance(context, AccountContext):
        raise CoreError('authentication_required')
    _url(subscription_url)
    if group_id is not None and (type(group_id) is not int or group_id <= 0):
        raise CoreError('invalid_request')
    user = db.get_user_by_id(context.account_id)
    if not user or user['is_banned']:
        raise CoreError('account_unavailable')
    if not db.consume_auth_limits([(f'subscription_import:{context.account_id}', 10, 60)], int(time.time())):
        raise CoreError('rate_limited', retryable=True)
    from bot.services.panels.subscription_import import inspect_subscription_group
    from bot.services.panel_sync_coordinator import panel_sync_coordinator
    from bot.services.vpn_api import get_client_from_server_data
    servers = {}
    for server in db.get_all_servers():
        servers.setdefault(physical_panel_key(server), server)
    async with panel_sync_coordinator.try_manual() as acquired:
        if not acquired:
            raise CoreError('panel_busy', retryable=True)
        matches, unavailable = [], False
        for endpoint, server in servers.items():
            client = get_client_from_server_data(server)
            try:
                sub_id = await _matching_sub_id(client, subscription_url)
            except Exception:
                unavailable = True
                continue
            if sub_id is not None:
                matches.append((endpoint, server, client, sub_id))
        # An unavailable configured panel can share the same advertised alias.
        if unavailable:
            raise CoreError('panel_unavailable', retryable=True)
        if not matches:
            raise CoreError('subscription_not_found')
        if len(matches) != 1:
            raise CoreError('subscription_ambiguous')
        endpoint, server, client, sub_id = matches[0]
        existing = _owned_keys(context.account_id, endpoint, [sub_id])
        if group_id is not None and any(key['group_id'] != group_id for key in existing):
            raise CoreError('subscription_group_unavailable')
        pending = [operation for key in existing if (operation := db.get_pending_panel_identity(key['id']))]
        if pending:
            await resume_panel_identity_renames(pending)
            if any(db.get_pending_panel_identity(key['id']) for key in existing):
                return {'state': 'pending', 'key_ids': sorted(key['id'] for key in existing), 'groups': []}
        snapshot = await inspect_subscription_group(client, sub_id)
        _validate_members(snapshot)
        members = snapshot['members']
        existing = _owned_keys(context.account_id, endpoint, snapshot['sub_ids'],
                               [member['record']['email'] for member in members])
        if existing and group_id is None:
            groups = {key['group_id'] for key in existing}
            if len(groups) != 1:
                raise CoreError('subscription_ambiguous')
            group_id = groups.pop()
        candidates = await _group_candidates(endpoint, members)
        if not candidates or group_id is not None and group_id not in candidates:
            raise CoreError('subscription_group_unavailable')
        if group_id is None and len(candidates) > 1:
            return {'state': 'select_group', 'key_ids': [],
                    'groups': [{'id': item['id'], 'name': item['name']} for item in candidates.values()]}
        selected = candidates[group_id] if group_id is not None else next(iter(candidates.values()))
        key_ids = db.bind_panel_subscription(user_id=context.account_id, endpoint=endpoint,
                                            group_id=selected['id'], members=members,
                                            servers=selected['servers'], now=int(time.time()))
        operations = [operation for key_id in key_ids if (operation := db.get_pending_panel_identity(key_id))]
        await resume_panel_identity_renames(operations)
        state = 'pending' if any(db.get_pending_panel_identity(key_id) for key_id in key_ids) else 'completed'
        return {'state': state, 'key_ids': key_ids, 'groups': []}
