"""Public borrowed panel clients for trusted installed Python extensions."""
from __future__ import annotations

from bot.services.panels.http import PanelResponse


def list_panels() -> list[dict]:
    from database.requests import get_all_servers
    return [{
        'server_id': row['id'], 'name': row['name'], 'panel_type': '3x-ui',
        'panel_version': row.get('panel_version'), 'is_active': bool(row['is_active']),
        'inbound_group_id': row.get('inbound_group_id'),
    } for row in get_all_servers()]


class ExtensionPanelClient:
    """Resolve the shared adapter on each request, so invalidation stays effective."""
    __slots__ = ('__server_id',)

    def __init__(self, server_id: int):
        if type(server_id) is not int or not 0 < server_id <= 2**63 - 1:
            raise ValueError('server_id must be a positive SQLite integer')
        object.__setattr__(self, '_ExtensionPanelClient__server_id', server_id)

    def __setattr__(self, name, value):
        raise PermissionError('Panel connection and credentials are managed by core')

    def __delattr__(self, name):
        raise PermissionError('Panel connection and credentials are managed by core')

    @property
    def server_id(self) -> int:
        return self.__server_id

    async def request(self, method: str, endpoint: str, *, params=None, json=None,
                      data=None, headers=None, timeout=None) -> PanelResponse:
        from bot.services.vpn_api import get_client
        from runtime.readiness import require_active
        require_active()
        client = await get_client(self.__server_id)
        return await client.request_http(method, endpoint, params=params, json=json,
                                         data=data, headers=headers, timeout=timeout)


def panel_capabilities() -> dict:
    return {
        'contract_version': 1, 'list': 'list_panels', 'factory': 'get_panel_client',
        'request': 'request', 'identity': 'server_id', 'response': 'PanelResponse',
        'authentication': 'managed_bearer', 'token_access': False,
        'endpoint_allowlist': False, 'network_retries': 0, 'unauthorized_replays': 1,
        'redirects': False,
    }
