"""Public shared extension API v1; identities come from the installed module loader."""
API_VERSION = 1

from bot.services.panels.base import PanelErrorKind, PanelRequestError
from bot.services.panels.http import PanelResponse


def get_bot():
    from bot.utils.custom_extensions import get_bot as borrow_bot
    return borrow_bot()


def list_panels():
    from bot.utils.custom_extensions import list_panels as list_connections
    return list_connections()


def get_panel_client(server_id: int):
    from bot.utils.custom_extensions import get_panel_client as borrow_panel
    return borrow_panel(server_id)


def _owner(operation):
    from bot.utils.custom_extensions import _ensure_extension_mutation_allowed, _require_current_extension
    _ensure_extension_mutation_allowed(operation)
    return _require_current_extension()


def register_module(*, version: str, api_version: int = API_VERSION, required_modules=()):
    from . import registry
    registry.register_module(_owner('register_module'), version=version, api_version=api_version,
                              required_modules=required_modules)


def _register(kind, name, handler, *, priority, required, **options):
    from bot.utils.custom_extensions import _bind_extension_callable
    from . import registry
    owner = _owner('register_' + kind + '_policy')
    registry.register_policy(kind, owner, name, _bind_extension_callable(handler, invocation_kind='policy'),
                              priority=priority, required=required, **options)


def register_pricing_policy(name, handler, *, priority=0, required=True, eligibility=None):
    if eligibility not in (None, 'first_purchase'):
        raise ValueError('unsupported eligibility condition')
    _register('pricing', name, handler, priority=priority, required=required, eligibility=eligibility)


def register_action_policy(name, *, actions, handler, priority=0, required=True):
    from bot.utils.action_policy import normalize_action_policy_actions
    _register('action', name, handler, priority=priority, required=required,
              actions=normalize_action_policy_actions(actions))


def register_eligibility_policy(name, handler, *, priority=0, required=True):
    _register('eligibility', name, handler, priority=priority, required=required)


def register_user_operation(name, handler, *, kind='command', input_schema=None, result_schema=None,
                            input_model=None, result_model=None, legacy_read_versions=()):
    import re
    from .models import declaration
    from bot.utils.custom_extensions import _bind_extension_callable
    from . import registry
    if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name):
        raise ValueError('invalid operation name')
    if kind not in ('query', 'command'):
        raise ValueError('kind must be query or command')
    if (not isinstance(legacy_read_versions, (tuple, list))
            or any(not isinstance(v, str) or not re.fullmatch(r'\d+\.\d+\.\d+', v) for v in legacy_read_versions)
            or legacy_read_versions and kind != 'query'):
        raise ValueError('legacy_read_versions requires a query and explicit module versions')
    contract = declaration(input_schema=input_schema, result_schema=result_schema,
                           input_model=input_model, result_model=result_model)
    owner = _owner('register_user_operation')
    registry.register_policy('user_operation', owner, name,
                              _bind_extension_callable(handler, invocation_kind='module_operation'),
                              operation_kind=kind, legacy_read_versions=tuple(legacy_read_versions), **contract)


def register_reward_policy(name, handler, *, purposes=('key_purchase', 'key_renewal'), priority=0, required=True):
    if not isinstance(purposes, (tuple, list)) or not purposes or set(purposes) - {'key_purchase', 'key_renewal', 'balance_topup'}:
        raise ValueError('unsupported reward purposes')
    _register('reward', name, handler, priority=priority, required=required, actions=tuple(purposes))


def register_event_handler(name, handler, *, events, priority=0):
    from bot.utils.extension_event_registry import CORE_EVENT_NAMES
    _subscriber('event', name, handler, events, CORE_EVENT_NAMES, priority)


def register_webhook(name, handler, *, verifier):
    import re
    from bot.utils.custom_extensions import _bind_extension_callable, register_task_handler
    from . import registry, webhooks
    if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name) or not callable(verifier):
        raise ValueError('a webhook name and callable verifier are required')
    owner = _owner('register_webhook')
    registry.register_policy('webhook', owner, name,
        _bind_extension_callable(handler, invocation_kind='task_handler'),
        verifier=_bind_extension_callable(verifier, invocation_kind='policy'),
        path=f'/webhooks/modules/{owner}/{name}')
    async def deliver(context):
        return await webhooks.dispatch(owner, name, context)
    register_task_handler(webhooks.task_name(name), deliver)


def register_lifecycle_hook(name, handler, *, events, priority=0):
    from bot.utils.lifecycle_registry import KEY_LIFECYCLE_EVENTS
    _subscriber('lifecycle', name, handler, events, KEY_LIFECYCLE_EVENTS, priority)


def _subscriber(kind, name, handler, events, allowed, priority):
    from bot.utils.custom_extensions import _bind_extension_callable
    from . import registry
    if not isinstance(events, (tuple, list)) or not events or set(events) - set(allowed):
        raise ValueError('unsupported subscriber events')
    registry.register_policy(kind, _owner('register_' + kind), name,
                              _bind_extension_callable(handler, invocation_kind='event_handler' if kind == 'event' else 'lifecycle_hook'),
                              priority=priority, required=False, events=tuple(events))


def first_purchase_eligible():
    from .registry import first_purchase_eligibility
    return first_purchase_eligibility({})


def register_settings(fields):
    from bot.utils.custom_extensions import register_extension_settings
    return register_extension_settings(fields)


def get_settings():
    from bot.utils.custom_extensions import get_extension_config
    return get_extension_config()


def register_storage_schema(migrations):
    from bot.utils.custom_extensions import register_extension_schema
    return register_extension_schema(_owner('register_storage_schema'), migrations)


def get_storage():
    from bot.utils.custom_extensions import get_extension_storage, _require_current_extension
    return get_extension_storage(_require_current_extension())


def get_database():
    from bot.utils.custom_extensions import get_database as database
    return database()


def get_core_facade():
    from bot.utils.custom_extensions import _require_current_extension
    from .facade import SharedCoreFacade
    return SharedCoreFacade(_require_current_extension())


__all__ = ['API_VERSION', 'register_module', 'register_action_policy', 'register_pricing_policy',
           'register_eligibility_policy', 'first_purchase_eligible', 'register_settings', 'get_settings',
           'register_storage_schema', 'get_storage', 'get_database', 'get_core_facade']
__all__ += ['register_user_operation']
__all__ += ['register_reward_policy', 'register_event_handler', 'register_lifecycle_hook']
__all__ += ['register_webhook']
__all__ += ['get_bot', 'list_panels', 'get_panel_client', 'PanelResponse', 'PanelRequestError', 'PanelErrorKind']
