"""Account-owned import: validate a URL identifier, inspect, then atomically claim."""
from __future__ import annotations

import time
from urllib.parse import unquote, urlsplit

from core.context import AccountContext
from core.panel_identity import physical_panel_key
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


async def import_subscription(context: AccountContext, subscription_url: str) -> dict:
    require_active()
    if not isinstance(context, AccountContext):
        raise CoreError('authentication_required')
    _url(subscription_url)
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
        snapshot = await inspect_subscription_group(client, sub_id)
        return db.claim_panel_subscription(user_id=context.account_id, endpoint=endpoint,
                                          server_id=server['id'], source_url=subscription_url,
                                          snapshot=snapshot, now=int(time.time()))
