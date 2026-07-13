"""Loader and public API for custom extensions."""
from __future__ import annotations

import ast
import importlib.util
import inspect
import logging
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from bot.utils.action_registry import register_action_handler as _register_action_handler
from bot.utils.page_flow import register_page_guard as _register_page_guard
from bot.utils.page_flow import register_page_hook as _register_page_hook

logger = logging.getLogger(__name__)

CUSTOM_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / 'custom_extensions'
CUSTOM_EXTENSIONS_ENABLED_SETTING = 'custom_extensions_enabled'

_TRUE_VALUES = {'1', 'true', 'yes', 'on', 'enabled', 'да', 'вкл'}
_EXTENSION_FILENAME_RE = re.compile(r'^[a-z][a-z0-9_]*\.py$')
_BLOCKED_IMPORT_ROOTS = {'sqlite3', 'database', 'sys'}
_BLOCKED_IMPORT_PREFIXES = {
    'bot.handlers',
    'bot.keyboards',
    'bot.middlewares',
    'bot.services',
    'bot.states',
    'bot.utils.action_registry',
    'bot.utils.lifecycle_registry',
    'bot.utils.page_flow',
    'bot.utils.payment_provider_registry',
    'bot.utils.policy_registry',
}
_BLOCKED_IMPORT_MODULES = {'bot', 'bot.utils'}
_ALLOWED_IMPORT_PREFIXES = {'bot.utils.custom_extensions'}
_BLOCKED_DYNAMIC_CODE_CALLS = {'eval', 'exec', 'compile'}
_BLOCKED_INTROSPECTION_CALLS = {'vars', 'dir'}
_GETATTR_CALLS = {'getattr'}
_DUNDER_GETATTRIBUTE_CALLS = {'__getattribute__'}
_PUBLIC_CUSTOM_EXTENSIONS_API = {
    'CUSTOM_EXTENSIONS_DIR',
    'CUSTOM_EXTENSIONS_ENABLED_SETTING',
    'CustomExtensionsLoadResult',
    'build_callback_data',
    'get_custom_extensions_diagnostics',
    'get_core_api',
    'get_extension_config',
    'get_extension_storage',
    'is_custom_extensions_enabled',
    'load_custom_extensions',
    'register_action_handler',
    'register_access_guard',
    'register_callback_handler',
    'register_command_handler',
    'register_extension_schema',
    'register_extension_settings',
    'register_guard',
    'register_key_lifecycle_hook',
    'register_page_hook',
    'register_payment_provider',
    'register_pricing_policy',
    'register_promo_reward_policy',
    'register_referral_reward_policy',
    'register_user_access_guard',
    'validate_custom_extension_file',
    'validate_custom_extensions_dir',
}


@dataclass
class CustomExtensionsLoadResult:
    """Summary of downloading files from custom_extensions."""

    loaded: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    skipped: bool = False
    reason: str = ''


_REGISTRATION_KINDS = (
    'actions',
    'guards',
    'page_hooks',
    'pricing_policies',
    'promo_reward_policies',
    'referral_reward_policies',
    'key_lifecycle_hooks',
    'payment_providers',
    'callback_handlers',
    'command_handlers',
    'user_access_guards',
    'schemas',
    'settings',
)

_LAST_LOAD_RESULT = CustomExtensionsLoadResult(skipped=True, reason='not_loaded')
_CURRENT_EXTENSION: ContextVar[str | None] = ContextVar('custom_extension_id', default=None)
_CURRENT_EXTENSION_BOT: ContextVar[Any | None] = ContextVar('custom_extension_bot', default=None)
_CURRENT_EXTENSION_TELEGRAM_ID: ContextVar[int | None] = ContextVar('custom_extension_telegram_id', default=None)
_EXTENSION_REGISTRATIONS: dict[str, dict[str, set[str]]] = {}


def register_guard(name: str, func: Callable) -> None:
    """Registers page/route guard extensions."""
    guard_name = _require_extension_registry_name(name, 'guard')
    _register_page_guard(guard_name, _bind_extension_callable(func))
    _record_registration('guards', guard_name)


def register_access_guard(name: str, func: Callable) -> None:
    """Registers access guard extensions for route/page transitions."""
    register_guard(name, func)


def register_user_access_guard(name: str, func: Callable, *, replace: bool = False) -> None:
    """Registers a global user-area access guard."""
    from bot.utils.user_access import register_user_access_guard as _register_user_access_guard

    guard_name = _require_extension_registry_name(name, 'user access guard')
    _require_bool_option(replace, 'replace')
    _register_user_access_guard(guard_name, _bind_extension_callable(func), replace=replace)
    _record_registration('user_access_guards', guard_name)


def register_page_hook(name: str, func: Callable) -> None:
    """Registers the extension's before-render hook."""
    hook_name = _require_extension_registry_name(name, 'page hook')
    _register_page_hook(hook_name, _bind_extension_callable(func))
    _record_registration('page_hooks', hook_name)


def register_action_handler(action_value: str, callback_data: str, *, replace: bool = False) -> None:
    """Registers the extension's internal action as action_value -> callback_data."""
    action_key = _require_text(action_value, 'action_value').strip()
    callback = _require_text(callback_data, 'callback_data').strip()
    _require_bool_option(replace, 'replace')
    if not action_key.startswith('cmd_ext_'):
        raise ValueError("action_value расширения должен начинаться с cmd_ext_")
    _register_action_handler(action_key, callback, replace=replace)
    _record_registration('actions', action_key)


def register_callback_handler(
    action_name: str,
    handler: Callable,
    *,
    replace: bool = False,
    bypass_user_access_guard: bool = False,
) -> str:
    """Registers the extension's declarative callback handler."""
    from bot.utils.extension_callbacks import register_extension_callback_handler

    extension_id = _require_current_extension()
    _require_bool_option(replace, 'replace')
    _require_bool_option(bypass_user_access_guard, 'bypass_user_access_guard')
    action_key = register_extension_callback_handler(
        extension_id,
        action_name,
        _bind_extension_callable(handler),
        replace=replace,
        bypass_user_access_guard=bypass_user_access_guard,
    )
    _record_registration('callback_handlers', action_key)
    return action_key


def build_callback_data(action_name: str, payload: str | None = None) -> str:
    """Collects callback_data of the current extension into namespace `ext:`."""
    from bot.utils.extension_callbacks import build_extension_callback_data

    return build_extension_callback_data(_require_current_extension(), action_name, payload)


def register_command_handler(
    command: str,
    description: str,
    handler: Callable,
    *,
    replace: bool = False,
) -> str:
    """Registers the extension's Telegram bot command handler."""
    from bot.utils.extension_commands import register_extension_command_handler

    extension_id = _require_current_extension()
    _require_bool_option(replace, 'replace')
    action_key = register_extension_command_handler(
        extension_id,
        command,
        description,
        _bind_extension_callable(handler),
        replace=replace,
    )
    _record_registration('command_handlers', action_key)
    return action_key


def register_pricing_policy(name: str, func: Callable, *, replace: bool = False) -> None:
    """Registers the pricing policy of the extension."""
    from bot.utils.policy_registry import register_pricing_policy as _register_pricing_policy

    policy_name = _require_extension_owned_registry_name(name, 'pricing policy')
    _require_bool_option(replace, 'replace')
    _register_pricing_policy(policy_name, _bind_extension_callable(func), replace=replace)
    _record_registration('pricing_policies', policy_name)


def register_promo_reward_policy(name: str, func: Callable, *, replace: bool = False) -> None:
    """Registers the promo reward policy of the extension."""
    from bot.utils.policy_registry import register_promo_reward_policy as _register_promo_reward_policy

    policy_name = _require_extension_owned_registry_name(name, 'promo reward policy')
    _require_bool_option(replace, 'replace')
    _register_promo_reward_policy(policy_name, _bind_extension_callable(func), replace=replace)
    _record_registration('promo_reward_policies', policy_name)


def register_referral_reward_policy(name: str, func: Callable, *, replace: bool = False) -> None:
    """Registers the extension's referral reward policy."""
    from bot.utils.policy_registry import register_referral_reward_policy as _register_referral_reward_policy

    policy_name = _require_extension_owned_registry_name(name, 'referral reward policy')
    _require_bool_option(replace, 'replace')
    _register_referral_reward_policy(policy_name, _bind_extension_callable(func), replace=replace)
    _record_registration('referral_reward_policies', policy_name)


def register_key_lifecycle_hook(
    name: str,
    func: Callable,
    *,
    events: list[str] | tuple[str, ...] | set[str] | None = None,
    replace: bool = False,
) -> None:
    """Registers the extension's key lifecycle hook."""
    from bot.utils.lifecycle_registry import register_key_lifecycle_hook as _register_key_lifecycle_hook

    hook_name = _require_extension_owned_registry_name(name, 'key lifecycle hook')
    _require_bool_option(replace, 'replace')
    _register_key_lifecycle_hook(hook_name, _bind_extension_callable(func), events=events, replace=replace)
    _record_registration('key_lifecycle_hooks', hook_name)


def register_payment_provider(
    provider_id: str,
    *,
    create_payment: Callable,
    check_payment: Callable,
    webhook_handler: Callable | None = None,
    webhook_secret: str | None = None,
    title: str | None = None,
    label: str | None = None,
    minimum_amount_cents: int = 0,
    is_enabled=True,
    auto_check_interval_seconds: int | None = 300,
    metadata: dict | None = None,
    replace: bool = False,
) -> None:
    """Registers a custom payment provider extension."""
    from bot.utils.payment_provider_registry import register_payment_provider as _register_payment_provider

    provider_key = _require_extension_payment_provider_id(provider_id)
    _require_bool_option(replace, 'replace')
    provider = _register_payment_provider(
        provider_key,
        create_payment=_bind_extension_callable(create_payment),
        check_payment=_bind_extension_callable(check_payment),
        webhook_handler=_bind_extension_callable(webhook_handler) if webhook_handler is not None else None,
        webhook_secret=webhook_secret,
        title=title,
        label=label,
        minimum_amount_cents=minimum_amount_cents,
        is_enabled=_bind_extension_callable(is_enabled) if callable(is_enabled) else is_enabled,
        auto_check_interval_seconds=auto_check_interval_seconds,
        metadata=metadata,
        replace=replace,
    )
    _record_registration('payment_providers', provider.provider_id)


def register_extension_schema(extension_id: str, migrations: list[dict]) -> None:
    """Registers and applies the declarative schema of extension tables."""
    from database.requests import register_extension_schema as db_register_extension_schema

    ext_id = _require_current_extension_namespace(extension_id)
    db_register_extension_schema(ext_id, migrations)
    _record_registration('schemas', ext_id)


def get_extension_storage(extension_id: str):
    """Returns the extension's namespaced storage/repository API."""
    from database.requests import get_extension_storage as db_get_extension_storage

    return db_get_extension_storage(_require_current_extension_namespace(extension_id))


def register_extension_settings(fields: list[dict]) -> list[dict]:
    """Registers admin-editable settings for the current extension."""
    from bot.utils.extension_settings import register_extension_settings as _register_extension_settings

    extension_id = _require_current_extension()
    normalized = _register_extension_settings(extension_id, fields)
    _record_registration('settings', extension_id)
    return normalized


def get_extension_config() -> dict[str, Any]:
    """Returns validated settings values for the current extension."""
    from bot.utils.extension_settings import get_extension_config as _get_extension_config

    return _get_extension_config(_require_current_extension())


def get_core_api():
    """Returns the limited core facade for the current extension."""
    from bot.utils.extension_core import ExtensionCoreAPI

    return ExtensionCoreAPI(_require_current_extension())


def is_custom_extensions_enabled() -> bool:
    """Returns whether loading custom extensions is enabled."""
    from database.requests import get_setting

    return _as_bool(get_setting(CUSTOM_EXTENSIONS_ENABLED_SETTING, '0'))


def load_custom_extensions(
    extensions_dir: str | Path | None = None,
    *,
    enabled: bool | None = None,
) -> CustomExtensionsLoadResult:
    """Loads extensions from the gitignored folder if they are explicitly enabled."""
    global _LAST_LOAD_RESULT

    result = CustomExtensionsLoadResult()

    if enabled is None:
        enabled = is_custom_extensions_enabled()
    if not enabled:
        result.skipped = True
        result.reason = 'disabled'
        _LAST_LOAD_RESULT = result
        return result

    base_dir = Path(extensions_dir) if extensions_dir is not None else CUSTOM_EXTENSIONS_DIR
    if not base_dir.exists():
        result.skipped = True
        result.reason = 'directory_missing'
        _LAST_LOAD_RESULT = result
        return result
    if not base_dir.is_dir():
        result.skipped = True
        result.reason = 'not_directory'
        _LAST_LOAD_RESULT = result
        return result

    for path in sorted(base_dir.glob('*.py')):
        if path.name.startswith('_'):
            continue
        if not _EXTENSION_FILENAME_RE.fullmatch(path.name):
            result.failed[path.name] = 'invalid_filename'
            logger.warning("Расширение %s пропущено: имя файла не соответствует API", path.name)
            continue

        extension_id = path.stem
        registry_snapshot = _snapshot_runtime_registries()
        previous_registrations = _clone_extension_registrations(
            _EXTENSION_REGISTRATIONS.get(extension_id)
        )
        extension_token = _CURRENT_EXTENSION.set(extension_id)
        try:
            _remove_extension_runtime_registrations(extension_id)
            _EXTENSION_REGISTRATIONS.pop(extension_id, None)
            _load_extension_module(path)
        except Exception as e:
            _restore_runtime_registries(registry_snapshot)
            if previous_registrations is not None:
                _EXTENSION_REGISTRATIONS[extension_id] = previous_registrations
            else:
                _EXTENSION_REGISTRATIONS.pop(extension_id, None)
            result.failed[path.name] = str(e)
            logger.exception("Ошибка загрузки расширения %s: %s", path.name, e)
            continue
        finally:
            _CURRENT_EXTENSION.reset(extension_token)

        result.loaded.append(extension_id)

    _LAST_LOAD_RESULT = result
    return result


def get_custom_extensions_diagnostics(
    extensions_dir: str | Path | None = None,
    *,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Returns a read-only snapshot for admin diagnostics of extensions."""
    if enabled is None:
        enabled = is_custom_extensions_enabled()

    base_dir = Path(extensions_dir) if extensions_dir is not None else CUSTOM_EXTENSIONS_DIR
    directory_status = _extension_directory_status(base_dir)
    files = _scan_extension_files(base_dir) if directory_status == 'ok' else []
    from bot.utils.extension_settings import get_all_extension_settings

    return {
        'enabled': bool(enabled),
        'directory': str(base_dir),
        'directory_status': directory_status,
        'files': files,
        'last_load': _load_result_to_dict(_LAST_LOAD_RESULT),
        'registrations': _registrations_snapshot(),
        'registry_totals': _registry_totals(),
        'settings': get_all_extension_settings(),
    }


def validate_custom_extension_file(path: str | Path) -> dict[str, Any]:
    """Statically checks one extension file without registering runtime points."""
    extension_path = Path(path)
    try:
        if not extension_path.is_file():
            raise ValueError('not_file')
        if not _EXTENSION_FILENAME_RE.fullmatch(extension_path.name):
            raise ValueError('invalid_filename')
        _validate_extension_source(extension_path)
        source = extension_path.read_text(encoding='utf-8')
        tree = ast.parse(source, filename=str(extension_path))
        _validate_static_extension_declarations(tree, extension_path.stem)
    except Exception as exc:
        return {
            'file': extension_path.name,
            'ok': False,
            'error': str(exc),
        }
    return {
        'file': extension_path.name,
        'ok': True,
        'error': '',
    }


def validate_custom_extensions_dir(extensions_dir: str | Path | None = None) -> dict[str, Any]:
    """Statically checks the extension directory without downloading files."""
    base_dir = Path(extensions_dir) if extensions_dir is not None else CUSTOM_EXTENSIONS_DIR
    directory_status = _extension_directory_status(base_dir)
    if directory_status != 'ok':
        return {
            'ok': False,
            'directory': str(base_dir),
            'directory_status': directory_status,
            'files': [],
        }
    files = [validate_custom_extension_file(path) for path in sorted(base_dir.glob('*.py')) if not path.name.startswith('_')]
    return {
        'ok': all(item['ok'] for item in files),
        'directory': str(base_dir),
        'directory_status': directory_status,
        'files': files,
    }


def _load_extension_module(path: Path) -> ModuleType:
    _validate_extension_source(path)

    module_name = f"_custom_extensions_{path.stem}_{abs(hash(path.resolve()))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"не удалось создать spec для {path.name}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _validate_extension_source(path: Path) -> None:
    """Rejects standard attempts to bypass the public extension API through direct access to the database."""
    source = path.read_text(encoding='utf-8')
    tree = ast.parse(source, filename=str(path))
    dynamic_import_names = {'__import__', 'import_module'}
    dynamic_code_names = set(_BLOCKED_DYNAMIC_CODE_CALLS)
    introspection_names = set(_BLOCKED_INTROSPECTION_CALLS)
    getattr_names = set(_GETATTR_CALLS)
    dunder_getattribute_names = set(_DUNDER_GETATTRIBUTE_CALLS)
    constant_string_names: dict[str, str] = {}
    public_custom_extensions_aliases: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_public_custom_extensions_import(alias.name):
                    if not alias.asname:
                        raise ValueError(
                            "import bot.utils.custom_extensions должен использовать alias public API"
                        )
                    public_custom_extensions_aliases.add(alias.asname)
                    continue
                _raise_if_blocked_import(alias.name)
            continue

        if isinstance(node, ast.ImportFrom):
            public_from_bot_utils = _is_allowed_public_custom_extensions_import_from(node)
            public_from_api_module = _is_public_custom_extensions_import_from(node)
            if not public_from_bot_utils and not public_from_api_module:
                _raise_if_blocked_import(node.module or '')
            for alias in node.names:
                if public_from_api_module:
                    _raise_if_private_custom_extensions_api_name(alias.name)
                _raise_if_blocked_import(f"{node.module or ''}.{alias.name}")
                if public_from_bot_utils and alias.name == 'custom_extensions':
                    public_custom_extensions_aliases.add(alias.asname or alias.name)
            _collect_dynamic_import_aliases(node, dynamic_import_names)
            _collect_dynamic_code_aliases(node, dynamic_code_names)
            _collect_introspection_aliases(node, introspection_names)
            _collect_getattr_aliases(node, getattr_names)
            continue

        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            _collect_constant_string_assignment_aliases(node, constant_string_names)
            _collect_dynamic_import_assignment_aliases(
                node,
                dynamic_import_names,
                constant_string_names,
            )
            _collect_dynamic_code_assignment_aliases(
                node,
                dynamic_code_names,
                constant_string_names,
            )
            _collect_introspection_assignment_aliases(
                node,
                introspection_names,
                constant_string_names,
            )
            _collect_getattr_assignment_aliases(
                node,
                getattr_names,
                constant_string_names,
            )
            _collect_dunder_getattribute_assignment_aliases(
                node,
                dunder_getattribute_names,
                getattr_names,
                constant_string_names,
            )
            continue

        if (
            isinstance(node, ast.Call)
            and _is_custom_extensions_dynamic_import(
                node,
                dynamic_import_names,
                constant_string_names,
            )
        ):
            raise ValueError(
                "dynamic import bot.utils.custom_extensions запрещён; используйте static public API import"
            )
        if (
            isinstance(node, ast.Call)
            and _is_blocked_dynamic_import(node, dynamic_import_names, constant_string_names)
        ):
            _raise_if_blocked_import('database')
        if (
            isinstance(node, ast.Attribute)
            and _is_public_custom_extensions_alias(node.value, public_custom_extensions_aliases)
        ):
            _raise_if_private_custom_extensions_api_name(node.attr)
        if (
            isinstance(node, ast.Call)
            and _is_blocked_custom_extensions_getattr(
                node,
                public_custom_extensions_aliases,
                getattr_names,
                constant_string_names,
            )
        ):
            raise ValueError("расширениям доступен только public API bot.utils.custom_extensions")
        if (
            isinstance(node, ast.Call)
            and _is_blocked_custom_extensions_dunder_getattribute(
                node,
                public_custom_extensions_aliases,
                dunder_getattribute_names,
                constant_string_names,
            )
        ):
            raise ValueError("расширениям запрещён introspection private API bot.utils.custom_extensions")
        if (
            isinstance(node, ast.Call)
            and _is_blocked_custom_extensions_introspection(
                node,
                public_custom_extensions_aliases,
                introspection_names,
                constant_string_names,
            )
        ):
            raise ValueError("расширениям запрещён introspection private API bot.utils.custom_extensions")
        if (
            isinstance(node, ast.Call)
            and _is_blocked_dynamic_code_call(node, dynamic_code_names, constant_string_names)
        ):
            raise ValueError(
                "расширениям запрещены eval/exec/compile; используйте public extension API"
            )


def _validate_static_extension_declarations(tree: ast.AST, extension_id: str) -> None:
    """Checks obvious static public API calls without executing extension code."""
    from bot.utils.action_registry import normalize_callback_data
    from bot.utils.extension_callbacks import normalize_extension_action_name
    from bot.utils.extension_commands import normalize_extension_command, normalize_extension_command_description
    from bot.utils.extension_settings import normalize_extension_settings_fields
    from database.db_extensions import normalize_extension_id, validate_extension_schema_migrations

    expected_extension_id = normalize_extension_id(extension_id)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = _static_call_name(node.func)
        if func_name == 'register_callback_handler' and node.args:
            action_name = _literal_string_arg(node.args[0])
            if action_name is not None:
                normalize_extension_action_name(action_name)
        elif func_name == 'register_command_handler':
            command = _literal_string_arg(node.args[0]) if node.args else None
            if command is None:
                command = _literal_keyword_string_arg(node, 'command')
            if command is not None:
                normalized_command = normalize_extension_command(command)
                if normalized_command in {'start', 'id', 'help', 'support', 'buy', 'mykeys', 'my_keys'}:
                    raise ValueError(f"extension command '/{normalized_command}' is reserved by the core")
            description = None
            if len(node.args) >= 2:
                description = _literal_string_arg(node.args[1])
            if description is None:
                description = _literal_keyword_string_arg(node, 'description')
            if description is not None:
                normalize_extension_command_description(description)
        elif func_name == 'register_action_handler' and node.args:
            action_value = _literal_string_arg(node.args[0])
            if action_value is not None and not action_value.strip().startswith('cmd_ext_'):
                raise ValueError("action_value расширения должен начинаться с cmd_ext_")
            if len(node.args) >= 2:
                callback_data = _literal_string_arg(node.args[1])
                if callback_data is not None:
                    normalize_callback_data(callback_data, 'callback_data')
        elif func_name == 'register_extension_schema' and node.args:
            schema_extension_id = _literal_string_arg(node.args[0])
            if schema_extension_id is not None and normalize_extension_id(schema_extension_id) != expected_extension_id:
                raise ValueError('extension_id schema должен совпадать с именем extension-файла')
            if len(node.args) >= 2:
                migrations = _literal_value_arg(node.args[1])
                if migrations is not None:
                    validate_extension_schema_migrations(expected_extension_id, migrations)
        elif func_name == 'register_extension_settings' and node.args:
            fields = _literal_value_arg(node.args[0])
            if fields is not None:
                normalize_extension_settings_fields(fields)


def _static_call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ''


def _literal_string_arg(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_keyword_string_arg(node: ast.Call, name: str) -> str | None:
    for keyword in node.keywords:
        if keyword.arg == name:
            return _literal_string_arg(keyword.value)
    return None


def _literal_value_arg(node: ast.AST) -> Any | None:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def _raise_if_blocked_import(module_name: str) -> None:
    if _is_blocked_import_module(module_name):
        raise ValueError(
            "расширениям запрещён прямой импорт sqlite3/database/sys/internal registries/core runtime modules; "
            "используйте public extension API"
        )


def _is_allowed_public_custom_extensions_import_from(node: ast.ImportFrom) -> bool:
    return (
        str(node.module or '').casefold() == 'bot.utils'
        and bool(node.names)
        and all(alias.name == 'custom_extensions' for alias in node.names)
    )


def _is_public_custom_extensions_import(module_name: str) -> bool:
    return str(module_name or '').strip().casefold() == 'bot.utils.custom_extensions'


def _is_public_custom_extensions_import_from(node: ast.ImportFrom) -> bool:
    return str(node.module or '').casefold() == 'bot.utils.custom_extensions'


def _raise_if_private_custom_extensions_api_name(name: str) -> None:
    if name == '*' or name not in _PUBLIC_CUSTOM_EXTENSIONS_API:
        raise ValueError("расширениям доступен только public API bot.utils.custom_extensions")


def _is_public_custom_extensions_alias(node: ast.AST, aliases: set[str]) -> bool:
    return isinstance(node, ast.Name) and node.id in aliases


def _is_blocked_custom_extensions_getattr(
    node: ast.Call,
    aliases: set[str],
    getattr_names: set[str],
    constant_string_names: dict[str, str],
) -> bool:
    if not _is_getattr_reference(node.func, getattr_names, constant_string_names) or len(node.args) < 2:
        return False
    if not _is_public_custom_extensions_alias(node.args[0], aliases):
        return False
    attr_name = _resolve_static_string(node.args[1], constant_string_names)
    return attr_name not in _PUBLIC_CUSTOM_EXTENSIONS_API


def _is_blocked_custom_extensions_dunder_getattribute(
    node: ast.Call,
    aliases: set[str],
    dunder_getattribute_names: set[str],
    constant_string_names: dict[str, str],
) -> bool:
    if not node.args or not _is_public_custom_extensions_alias(node.args[0], aliases):
        return False
    if isinstance(node.func, ast.Attribute):
        return node.func.attr in _DUNDER_GETATTRIBUTE_CALLS
    if isinstance(node.func, ast.Name):
        return node.func.id in dunder_getattribute_names
    if isinstance(node.func, ast.Subscript):
        return _is_subscript_dunder_getattribute_reference(node.func, constant_string_names)
    return False


def _is_blocked_custom_extensions_introspection(
    node: ast.Call,
    aliases: set[str],
    introspection_names: set[str],
    constant_string_names: dict[str, str],
) -> bool:
    if not _is_introspection_reference(node.func, introspection_names, constant_string_names):
        return False
    return bool(node.args) and _is_public_custom_extensions_alias(node.args[0], aliases)


def _collect_dynamic_import_aliases(node: ast.ImportFrom, names: set[str]) -> None:
    module_name = str(node.module or '')
    if module_name == 'importlib':
        for alias in node.names:
            if alias.name == 'import_module':
                names.add(alias.asname or alias.name)
    if module_name == 'builtins':
        for alias in node.names:
            if alias.name == '__import__':
                names.add(alias.asname or alias.name)


def _collect_dynamic_code_aliases(node: ast.ImportFrom, names: set[str]) -> None:
    module_name = str(node.module or '')
    if module_name != 'builtins':
        return
    for alias in node.names:
        if alias.name in _BLOCKED_DYNAMIC_CODE_CALLS:
            names.add(alias.asname or alias.name)


def _collect_introspection_aliases(node: ast.ImportFrom, names: set[str]) -> None:
    module_name = str(node.module or '')
    if module_name != 'builtins':
        return
    for alias in node.names:
        if alias.name in _BLOCKED_INTROSPECTION_CALLS:
            names.add(alias.asname or alias.name)


def _collect_getattr_aliases(node: ast.ImportFrom, names: set[str]) -> None:
    module_name = str(node.module or '')
    if module_name != 'builtins':
        return
    for alias in node.names:
        if alias.name in _GETATTR_CALLS:
            names.add(alias.asname or alias.name)


def _collect_dynamic_import_assignment_aliases(
    node: ast.Assign | ast.AnnAssign,
    names: set[str],
    constant_string_names: dict[str, str],
) -> None:
    value = node.value
    if value is None or not _is_dynamic_import_reference(value, names, constant_string_names):
        return

    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        if isinstance(target, ast.Name):
            names.add(target.id)


def _collect_dynamic_code_assignment_aliases(
    node: ast.Assign | ast.AnnAssign,
    names: set[str],
    constant_string_names: dict[str, str],
) -> None:
    value = node.value
    if value is None or not _is_dynamic_code_reference(value, names, constant_string_names):
        return

    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        if isinstance(target, ast.Name):
            names.add(target.id)


def _collect_introspection_assignment_aliases(
    node: ast.Assign | ast.AnnAssign,
    names: set[str],
    constant_string_names: dict[str, str],
) -> None:
    value = node.value
    if value is None or not _is_introspection_reference(value, names, constant_string_names):
        return

    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        if isinstance(target, ast.Name):
            names.add(target.id)


def _collect_getattr_assignment_aliases(
    node: ast.Assign | ast.AnnAssign,
    names: set[str],
    constant_string_names: dict[str, str],
) -> None:
    value = node.value
    if value is None or not _is_getattr_reference(value, names, constant_string_names):
        return

    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        if isinstance(target, ast.Name):
            names.add(target.id)


def _collect_dunder_getattribute_assignment_aliases(
    node: ast.Assign | ast.AnnAssign,
    names: set[str],
    getattr_names: set[str],
    constant_string_names: dict[str, str],
) -> None:
    value = node.value
    if value is None or not _is_dunder_getattribute_reference(
        value,
        names,
        getattr_names,
        constant_string_names,
    ):
        return

    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        if isinstance(target, ast.Name):
            names.add(target.id)


def _collect_constant_string_assignment_aliases(
    node: ast.Assign | ast.AnnAssign,
    names: dict[str, str],
) -> None:
    value = node.value
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        resolved_value = value.value
    elif isinstance(value, ast.Name) and value.id in names:
        resolved_value = names[value.id]
    else:
        resolved_value = None

    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        if not isinstance(target, ast.Name):
            continue
        if resolved_value is None:
            names.pop(target.id, None)
        else:
            names[target.id] = resolved_value


def _is_dynamic_import_reference(
    node: ast.AST,
    dynamic_import_names: set[str],
    constant_string_names: dict[str, str] | None = None,
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in dynamic_import_names
    if isinstance(node, ast.Attribute):
        return node.attr in {'import_module', '__import__'}
    if isinstance(node, ast.Subscript):
        return _is_subscript_dynamic_import_reference(node, constant_string_names or {})
    if isinstance(node, ast.Call):
        return _is_getattr_dynamic_import_reference(node, constant_string_names or {})
    return False


def _is_dynamic_code_reference(
    node: ast.AST,
    dynamic_code_names: set[str],
    constant_string_names: dict[str, str] | None = None,
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in dynamic_code_names
    if isinstance(node, ast.Attribute):
        return node.attr in _BLOCKED_DYNAMIC_CODE_CALLS
    if isinstance(node, ast.Subscript):
        return _is_subscript_dynamic_code_reference(node, constant_string_names or {})
    if isinstance(node, ast.Call):
        return _is_getattr_dynamic_code_reference(node, constant_string_names or {})
    return False


def _is_introspection_reference(
    node: ast.AST,
    introspection_names: set[str],
    constant_string_names: dict[str, str] | None = None,
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in introspection_names
    if isinstance(node, ast.Attribute):
        return node.attr in _BLOCKED_INTROSPECTION_CALLS
    if isinstance(node, ast.Subscript):
        return _is_subscript_introspection_reference(node, constant_string_names or {})
    if isinstance(node, ast.Call):
        return _is_getattr_introspection_reference(node, constant_string_names or {})
    return False


def _is_getattr_reference(
    node: ast.AST,
    getattr_names: set[str],
    constant_string_names: dict[str, str] | None = None,
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in getattr_names
    if isinstance(node, ast.Attribute):
        return node.attr in _GETATTR_CALLS
    if isinstance(node, ast.Subscript):
        return _is_subscript_getattr_reference(node, constant_string_names or {})
    if isinstance(node, ast.Call):
        return _is_getattr_getattr_reference(node, constant_string_names or {})
    return False


def _is_dunder_getattribute_reference(
    node: ast.AST,
    dunder_getattribute_names: set[str],
    getattr_names: set[str],
    constant_string_names: dict[str, str] | None = None,
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in dunder_getattribute_names
    if isinstance(node, ast.Attribute):
        return node.attr in _DUNDER_GETATTRIBUTE_CALLS
    if isinstance(node, ast.Subscript):
        return _is_subscript_dunder_getattribute_reference(node, constant_string_names or {})
    if isinstance(node, ast.Call):
        return _is_getattr_dunder_getattribute_reference(
            node,
            getattr_names,
            constant_string_names or {},
        )
    return False


def _is_blocked_dynamic_import(
    node: ast.Call,
    dynamic_import_names: set[str],
    constant_string_names: dict[str, str],
) -> bool:
    func = node.func
    if not _is_dynamic_import_reference(func, dynamic_import_names, constant_string_names) or not node.args:
        return False

    first_arg = node.args[0]
    module_name = _resolve_static_string(first_arg, constant_string_names)
    if module_name is None:
        return False
    return _is_blocked_import_module(module_name)


def _is_custom_extensions_dynamic_import(
    node: ast.Call,
    dynamic_import_names: set[str],
    constant_string_names: dict[str, str],
) -> bool:
    func = node.func
    if not _is_dynamic_import_reference(func, dynamic_import_names, constant_string_names) or not node.args:
        return False

    module_name = _resolve_static_string(node.args[0], constant_string_names)
    return _is_public_custom_extensions_import(module_name or '')


def _is_blocked_import_module(module_name: str) -> bool:
    value = str(module_name or '').strip().casefold()
    if not value:
        return False
    if any(value == prefix or value.startswith(f'{prefix}.') for prefix in _ALLOWED_IMPORT_PREFIXES):
        return False
    if value == 'bot' or value.startswith('bot.'):
        return True
    if value in _BLOCKED_IMPORT_MODULES:
        return True
    root = value.split('.', 1)[0]
    if root in _BLOCKED_IMPORT_ROOTS:
        return True
    return any(
        value == prefix or value.startswith(f'{prefix}.')
        for prefix in _BLOCKED_IMPORT_PREFIXES
    )


def _is_blocked_dynamic_code_call(
    node: ast.Call,
    dynamic_code_names: set[str],
    constant_string_names: dict[str, str],
) -> bool:
    return _is_dynamic_code_reference(node.func, dynamic_code_names, constant_string_names)


def _is_getattr_dynamic_import_reference(node: ast.Call, constant_string_names: dict[str, str]) -> bool:
    if not isinstance(node.func, ast.Name) or node.func.id != 'getattr' or len(node.args) < 2:
        return False
    attr_name = _resolve_static_string(node.args[1], constant_string_names)
    return attr_name in {'import_module', '__import__'}


def _is_subscript_dynamic_import_reference(node: ast.Subscript, constant_string_names: dict[str, str]) -> bool:
    if not _is_builtins_reference(node.value):
        return False
    item_name = _resolve_static_string(node.slice, constant_string_names)
    return item_name == '__import__'


def _is_getattr_dynamic_code_reference(node: ast.Call, constant_string_names: dict[str, str]) -> bool:
    if not isinstance(node.func, ast.Name) or node.func.id != 'getattr' or len(node.args) < 2:
        return False
    attr_name = _resolve_static_string(node.args[1], constant_string_names)
    return attr_name in _BLOCKED_DYNAMIC_CODE_CALLS


def _is_subscript_dynamic_code_reference(node: ast.Subscript, constant_string_names: dict[str, str]) -> bool:
    if not _is_builtins_reference(node.value):
        return False
    item_name = _resolve_static_string(node.slice, constant_string_names)
    return item_name in _BLOCKED_DYNAMIC_CODE_CALLS


def _is_getattr_introspection_reference(node: ast.Call, constant_string_names: dict[str, str]) -> bool:
    if not isinstance(node.func, ast.Name) or node.func.id != 'getattr' or len(node.args) < 2:
        return False
    attr_name = _resolve_static_string(node.args[1], constant_string_names)
    return attr_name in _BLOCKED_INTROSPECTION_CALLS


def _is_subscript_introspection_reference(node: ast.Subscript, constant_string_names: dict[str, str]) -> bool:
    if not _is_builtins_reference(node.value):
        return False
    item_name = _resolve_static_string(node.slice, constant_string_names)
    return item_name in _BLOCKED_INTROSPECTION_CALLS


def _is_getattr_getattr_reference(node: ast.Call, constant_string_names: dict[str, str]) -> bool:
    if not isinstance(node.func, ast.Name) or node.func.id != 'getattr' or len(node.args) < 2:
        return False
    attr_name = _resolve_static_string(node.args[1], constant_string_names)
    return attr_name in _GETATTR_CALLS


def _is_getattr_dunder_getattribute_reference(
    node: ast.Call,
    getattr_names: set[str],
    constant_string_names: dict[str, str],
) -> bool:
    if not _is_getattr_reference(node.func, getattr_names, constant_string_names) or len(node.args) < 2:
        return False
    attr_name = _resolve_static_string(node.args[1], constant_string_names)
    return attr_name in _DUNDER_GETATTRIBUTE_CALLS


def _is_subscript_dunder_getattribute_reference(node: ast.Subscript, constant_string_names: dict[str, str]) -> bool:
    item_name = _resolve_static_string(node.slice, constant_string_names)
    return item_name in _DUNDER_GETATTRIBUTE_CALLS


def _is_subscript_getattr_reference(node: ast.Subscript, constant_string_names: dict[str, str]) -> bool:
    if not _is_builtins_reference(node.value):
        return False
    item_name = _resolve_static_string(node.slice, constant_string_names)
    return item_name in _GETATTR_CALLS


def _is_builtins_reference(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == '__builtins__'


def _resolve_static_string(node: ast.AST, names: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return names.get(node.id)
    return None


def _record_registration(kind: str, name: object) -> None:
    current_extension = _CURRENT_EXTENSION.get()
    if current_extension is None or kind not in _REGISTRATION_KINDS:
        return
    normalized_name = _normalize_registration_name(kind, name)
    if not normalized_name:
        return
    extension_items = _EXTENSION_REGISTRATIONS.setdefault(
        current_extension,
        {item: set() for item in _REGISTRATION_KINDS},
    )
    extension_items.setdefault(kind, set()).add(normalized_name)


def _normalize_registration_name(kind: str, name: object) -> str:
    value = str(name or '').strip()
    if kind == 'actions':
        return value
    return value.casefold()


def _require_current_extension_namespace(extension_id: str) -> str:
    from database.requests import normalize_extension_id

    ext_id = normalize_extension_id(extension_id)
    current_extension = _CURRENT_EXTENSION.get()
    if current_extension is None:
        return ext_id

    current_id = normalize_extension_id(current_extension)
    if ext_id != current_id:
        raise ValueError('extension_id должен совпадать с именем текущего расширения')
    return ext_id


def _require_current_extension() -> str:
    from database.requests import normalize_extension_id

    current_extension = _CURRENT_EXTENSION.get()
    if current_extension is None:
        raise ValueError('операция доступна только из контекста custom extension')
    return normalize_extension_id(current_extension)


def _require_extension_registry_name(name: str, kind: str) -> str:
    registry_name = _require_text(name, f'{kind} name').strip()
    if not registry_name.casefold().startswith('ext_'):
        raise ValueError(f"{kind} расширения должен начинаться с ext_")
    return registry_name


def _require_extension_owned_registry_name(name: str, kind: str) -> str:
    from database.requests import normalize_extension_id

    registry_name = _require_text(name, f'{kind} name').strip()
    normalized_name = registry_name.casefold()
    if normalized_name.startswith(('ext_', 'ext.')):
        return registry_name

    current_extension = _CURRENT_EXTENSION.get()
    if current_extension is not None:
        current_id = normalize_extension_id(current_extension)
        if normalized_name.startswith(f'{current_id}.'):
            return registry_name

    raise ValueError(
        f"{kind} расширения должен начинаться с ext_, ext. "
        "или namespace текущего расширения"
    )


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} должен быть строкой")
    return value


def _require_bool_option(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} должен быть bool")
    return value


def _require_extension_payment_provider_id(provider_id: str) -> str:
    from bot.utils.payment_provider_registry import normalize_payment_provider_id
    from database.requests import normalize_extension_id

    provider_key = normalize_payment_provider_id(provider_id)
    current_extension = _CURRENT_EXTENSION.get()
    if current_extension is None:
        raise ValueError('payment provider расширения можно регистрировать только во время загрузки расширения')

    current_id = normalize_extension_id(current_extension)
    if provider_key == current_id or provider_key.startswith(f'{current_id}_'):
        return provider_key

    raise ValueError('provider_id расширения должен совпадать с namespace текущего расширения')


def _bind_extension_callable(func: Callable) -> Callable:
    extension_id = _CURRENT_EXTENSION.get()
    if extension_id is None:
        return func

    if inspect.iscoroutinefunction(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            token = _CURRENT_EXTENSION.set(extension_id)
            bot = _extract_extension_bot(args, kwargs)
            telegram_id = _extract_extension_telegram_id(args, kwargs)
            bot_token = _CURRENT_EXTENSION_BOT.set(bot) if bot is not None else None
            telegram_token = _CURRENT_EXTENSION_TELEGRAM_ID.set(telegram_id) if telegram_id is not None else None
            try:
                return await func(*args, **kwargs)
            finally:
                if telegram_token is not None:
                    _CURRENT_EXTENSION_TELEGRAM_ID.reset(telegram_token)
                if bot_token is not None:
                    _CURRENT_EXTENSION_BOT.reset(bot_token)
                _CURRENT_EXTENSION.reset(token)

        return async_wrapper

    @wraps(func)
    def wrapper(*args, **kwargs):
        token = _CURRENT_EXTENSION.set(extension_id)
        bot = _extract_extension_bot(args, kwargs)
        telegram_id = _extract_extension_telegram_id(args, kwargs)
        bot_token = _CURRENT_EXTENSION_BOT.set(bot) if bot is not None else None
        telegram_token = _CURRENT_EXTENSION_TELEGRAM_ID.set(telegram_id) if telegram_id is not None else None
        try:
            result = func(*args, **kwargs)
            if inspect.isawaitable(result):
                return _await_with_extension_context(result, extension_id, bot=bot, telegram_id=telegram_id)
            return result
        finally:
            if telegram_token is not None:
                _CURRENT_EXTENSION_TELEGRAM_ID.reset(telegram_token)
            if bot_token is not None:
                _CURRENT_EXTENSION_BOT.reset(bot_token)
            _CURRENT_EXTENSION.reset(token)

    return wrapper


async def _await_with_extension_context(
    awaitable: Any,
    extension_id: str,
    *,
    bot: Any = None,
    telegram_id: int | None = None,
) -> Any:
    token = _CURRENT_EXTENSION.set(extension_id)
    bot_token = _CURRENT_EXTENSION_BOT.set(bot) if bot is not None else None
    telegram_token = _CURRENT_EXTENSION_TELEGRAM_ID.set(telegram_id) if telegram_id is not None else None
    try:
        return await awaitable
    finally:
        if telegram_token is not None:
            _CURRENT_EXTENSION_TELEGRAM_ID.reset(telegram_token)
        if bot_token is not None:
            _CURRENT_EXTENSION_BOT.reset(bot_token)
        _CURRENT_EXTENSION.reset(token)


@contextmanager
def _extension_bot_context(bot: Any):
    """Temporarily exposes the runtime bot to the extension core facade."""
    if bot is None:
        yield
        return
    token = _CURRENT_EXTENSION_BOT.set(bot)
    try:
        yield
    finally:
        _CURRENT_EXTENSION_BOT.reset(token)


def _get_current_extension_bot() -> Any:
    return _CURRENT_EXTENSION_BOT.get()


def _get_current_extension_telegram_id() -> int | None:
    return _CURRENT_EXTENSION_TELEGRAM_ID.get()


def _extract_extension_bot(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    for item in list(args) + list(kwargs.values()):
        bot = getattr(item, 'bot', None)
        if bot is not None:
            return bot
        message = getattr(item, 'message', None)
        bot = getattr(message, 'bot', None)
        if bot is not None:
            return bot
    return None


def _extract_extension_telegram_id(args: tuple[Any, ...], kwargs: dict[str, Any]) -> int | None:
    for item in list(args) + list(kwargs.values()):
        if isinstance(item, dict) and isinstance(item.get('telegram_id'), int):
            return item['telegram_id']
        if hasattr(item, 'get'):
            try:
                value = item.get('telegram_id')
            except Exception:
                value = None
            if isinstance(value, int):
                return value
        user = getattr(item, 'from_user', None)
        if user is not None and isinstance(getattr(user, 'id', None), int):
            return user.id
        message = getattr(item, 'message', None)
        user = getattr(message, 'from_user', None)
        if user is not None and isinstance(getattr(user, 'id', None), int):
            return user.id
    return None


def _extension_directory_status(base_dir: Path) -> str:
    if not base_dir.exists():
        return 'directory_missing'
    if not base_dir.is_dir():
        return 'not_directory'
    return 'ok'


def _scan_extension_files(base_dir: Path) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    for path in sorted(base_dir.glob('*.py')):
        if path.name.startswith('_'):
            status = 'ignored_private'
        elif _EXTENSION_FILENAME_RE.fullmatch(path.name):
            status = 'candidate'
        else:
            status = 'invalid_filename'
        files.append({
            'file': path.name,
            'extension': path.stem,
            'status': status,
        })
    return files


def _load_result_to_dict(result: CustomExtensionsLoadResult) -> dict[str, Any]:
    return {
        'loaded': list(result.loaded),
        'failed': dict(result.failed),
        'skipped': bool(result.skipped),
        'reason': str(result.reason or ''),
    }


def _registrations_snapshot() -> dict[str, dict[str, list[str]]]:
    snapshot: dict[str, dict[str, list[str]]] = {}
    for extension_name, registrations in sorted(_EXTENSION_REGISTRATIONS.items()):
        snapshot[extension_name] = {
            kind: sorted(value for value in registrations.get(kind, set()) if value)
            for kind in _REGISTRATION_KINDS
        }
    return snapshot


def _clone_extension_registrations(
    registrations: dict[str, set[str]] | None,
) -> dict[str, set[str]] | None:
    if registrations is None:
        return None
    return {
        kind: set(registrations.get(kind, set()))
        for kind in _REGISTRATION_KINDS
    }


def _remove_extension_runtime_registrations(extension_id: str) -> None:
    registrations = _EXTENSION_REGISTRATIONS.get(extension_id)
    if not registrations:
        return

    from bot.utils import page_flow
    from bot.utils.action_registry import ACTION_REGISTRY
    from bot.utils.extension_callbacks import remove_extension_callback_handlers
    from bot.utils.extension_commands import remove_extension_command_handlers
    from bot.utils.extension_settings import remove_extension_settings
    from bot.utils.lifecycle_registry import KEY_LIFECYCLE_HOOKS
    from bot.utils.payment_provider_registry import PAYMENT_PROVIDERS
    from bot.utils.policy_registry import (
        PRICING_POLICIES,
        PROMO_REWARD_POLICIES,
        REFERRAL_REWARD_POLICIES,
    )
    from bot.utils.user_access import remove_user_access_guards

    for name in registrations.get('actions', set()):
        ACTION_REGISTRY.pop(name, None)
    for name in registrations.get('guards', set()):
        page_flow.PAGE_GUARDS.pop(name, None)
    for name in registrations.get('page_hooks', set()):
        page_flow.PAGE_HOOKS.pop(name, None)
    for name in registrations.get('pricing_policies', set()):
        PRICING_POLICIES.pop(name, None)
    for name in registrations.get('promo_reward_policies', set()):
        PROMO_REWARD_POLICIES.pop(name, None)
    for name in registrations.get('referral_reward_policies', set()):
        REFERRAL_REWARD_POLICIES.pop(name, None)
    for name in registrations.get('key_lifecycle_hooks', set()):
        KEY_LIFECYCLE_HOOKS.pop(name, None)
    for name in registrations.get('payment_providers', set()):
        PAYMENT_PROVIDERS.pop(name, None)
    remove_extension_callback_handlers(extension_id, registrations.get('callback_handlers', set()))
    remove_extension_command_handlers(extension_id, registrations.get('command_handlers', set()))
    remove_user_access_guards(registrations.get('user_access_guards', set()))
    if registrations.get('settings'):
        remove_extension_settings(extension_id)


def _snapshot_runtime_registries() -> dict[str, Any]:
    from bot.utils import page_flow
    from bot.utils.action_registry import ACTION_REGISTRY
    from bot.utils.extension_callbacks import EXTENSION_ACCESS_CHECK_CALLBACKS, EXTENSION_CALLBACK_HANDLERS
    from bot.utils.extension_commands import EXTENSION_COMMAND_DEFINITIONS, EXTENSION_COMMAND_HANDLERS
    from bot.utils.extension_settings import EXTENSION_SETTINGS
    from bot.utils.lifecycle_registry import KEY_LIFECYCLE_HOOKS
    from bot.utils.payment_provider_registry import PAYMENT_PROVIDERS
    from bot.utils.policy_registry import (
        PRICING_POLICIES,
        PROMO_REWARD_POLICIES,
        REFERRAL_REWARD_POLICIES,
    )
    from bot.utils.user_access import USER_ACCESS_GUARDS

    return {
        'actions': dict(ACTION_REGISTRY),
        'guards': dict(page_flow.PAGE_GUARDS),
        'page_hooks': dict(page_flow.PAGE_HOOKS),
        'pricing_policies': dict(PRICING_POLICIES),
        'promo_reward_policies': dict(PROMO_REWARD_POLICIES),
        'referral_reward_policies': dict(REFERRAL_REWARD_POLICIES),
        'key_lifecycle_hooks': dict(KEY_LIFECYCLE_HOOKS),
        'payment_providers': dict(PAYMENT_PROVIDERS),
        'callback_handlers': dict(EXTENSION_CALLBACK_HANDLERS),
        'access_check_callbacks': set(EXTENSION_ACCESS_CHECK_CALLBACKS),
        'command_handlers': dict(EXTENSION_COMMAND_HANDLERS),
        'command_definitions': dict(EXTENSION_COMMAND_DEFINITIONS),
        'user_access_guards': dict(USER_ACCESS_GUARDS),
        'settings': {
            extension_id: [dict(field) for field in fields]
            for extension_id, fields in EXTENSION_SETTINGS.items()
        },
    }


def _restore_runtime_registries(snapshot: dict[str, Any]) -> None:
    from bot.utils import page_flow
    from bot.utils.action_registry import ACTION_REGISTRY
    from bot.utils.extension_callbacks import EXTENSION_ACCESS_CHECK_CALLBACKS, EXTENSION_CALLBACK_HANDLERS
    from bot.utils.extension_commands import EXTENSION_COMMAND_DEFINITIONS, EXTENSION_COMMAND_HANDLERS
    from bot.utils.extension_settings import EXTENSION_SETTINGS
    from bot.utils.lifecycle_registry import KEY_LIFECYCLE_HOOKS
    from bot.utils.payment_provider_registry import PAYMENT_PROVIDERS
    from bot.utils.policy_registry import (
        PRICING_POLICIES,
        PROMO_REWARD_POLICIES,
        REFERRAL_REWARD_POLICIES,
    )
    from bot.utils.user_access import USER_ACCESS_GUARDS

    ACTION_REGISTRY.clear()
    ACTION_REGISTRY.update(snapshot['actions'])
    page_flow.PAGE_GUARDS.clear()
    page_flow.PAGE_GUARDS.update(snapshot['guards'])
    page_flow.PAGE_HOOKS.clear()
    page_flow.PAGE_HOOKS.update(snapshot['page_hooks'])
    PRICING_POLICIES.clear()
    PRICING_POLICIES.update(snapshot['pricing_policies'])
    PROMO_REWARD_POLICIES.clear()
    PROMO_REWARD_POLICIES.update(snapshot['promo_reward_policies'])
    REFERRAL_REWARD_POLICIES.clear()
    REFERRAL_REWARD_POLICIES.update(snapshot['referral_reward_policies'])
    KEY_LIFECYCLE_HOOKS.clear()
    KEY_LIFECYCLE_HOOKS.update(snapshot['key_lifecycle_hooks'])
    PAYMENT_PROVIDERS.clear()
    PAYMENT_PROVIDERS.update(snapshot['payment_providers'])
    EXTENSION_CALLBACK_HANDLERS.clear()
    EXTENSION_CALLBACK_HANDLERS.update(snapshot['callback_handlers'])
    EXTENSION_ACCESS_CHECK_CALLBACKS.clear()
    EXTENSION_ACCESS_CHECK_CALLBACKS.update(snapshot.get('access_check_callbacks', set()))
    EXTENSION_COMMAND_HANDLERS.clear()
    EXTENSION_COMMAND_HANDLERS.update(snapshot.get('command_handlers', {}))
    EXTENSION_COMMAND_DEFINITIONS.clear()
    EXTENSION_COMMAND_DEFINITIONS.update(snapshot.get('command_definitions', {}))
    USER_ACCESS_GUARDS.clear()
    USER_ACCESS_GUARDS.update(snapshot.get('user_access_guards', {}))
    EXTENSION_SETTINGS.clear()
    EXTENSION_SETTINGS.update(snapshot.get('settings', {}))


def _registry_totals() -> dict[str, int]:
    from bot.utils import page_flow
    from bot.utils.action_registry import ACTION_REGISTRY
    from bot.utils.extension_callbacks import EXTENSION_CALLBACK_HANDLERS
    from bot.utils.extension_commands import EXTENSION_COMMAND_DEFINITIONS
    from bot.utils.extension_settings import EXTENSION_SETTINGS
    from bot.utils.lifecycle_registry import KEY_LIFECYCLE_HOOKS
    from bot.utils.payment_provider_registry import PAYMENT_PROVIDERS
    from bot.utils.policy_registry import (
        PRICING_POLICIES,
        PROMO_REWARD_POLICIES,
        REFERRAL_REWARD_POLICIES,
    )
    from bot.utils.user_access import USER_ACCESS_GUARDS

    return {
        'actions': len(ACTION_REGISTRY),
        'guards': len(page_flow.PAGE_GUARDS),
        'page_hooks': len(page_flow.PAGE_HOOKS),
        'pricing_policies': len(PRICING_POLICIES),
        'promo_reward_policies': len(PROMO_REWARD_POLICIES),
        'referral_reward_policies': len(REFERRAL_REWARD_POLICIES),
        'key_lifecycle_hooks': len(KEY_LIFECYCLE_HOOKS),
        'payment_providers': len(PAYMENT_PROVIDERS),
        'callback_handlers': len(EXTENSION_CALLBACK_HANDLERS),
        'command_handlers': len(EXTENSION_COMMAND_DEFINITIONS),
        'user_access_guards': len(USER_ACCESS_GUARDS),
        'settings': sum(len(fields) for fields in EXTENSION_SETTINGS.values()),
    }


def reset_custom_extensions_runtime() -> dict[str, dict[str, int]]:
    """Removes all currently registered custom extension runtime objects."""
    global _LAST_LOAD_RESULT

    before = _registry_totals()
    for extension_id in list(_EXTENSION_REGISTRATIONS):
        _remove_extension_runtime_registrations(extension_id)
        _EXTENSION_REGISTRATIONS.pop(extension_id, None)
    for module_name in list(sys.modules):
        if module_name.startswith('_custom_extensions_'):
            sys.modules.pop(module_name, None)
    _LAST_LOAD_RESULT = CustomExtensionsLoadResult(skipped=True, reason='reset')
    after = _registry_totals()
    return {'before': before, 'after': after}


def _as_bool(value: object) -> bool:
    return str(value or '').strip().casefold() in _TRUE_VALUES


__all__ = [
    'CUSTOM_EXTENSIONS_DIR',
    'CUSTOM_EXTENSIONS_ENABLED_SETTING',
    'CustomExtensionsLoadResult',
    'build_callback_data',
    'get_custom_extensions_diagnostics',
    'get_core_api',
    'get_extension_config',
    'get_extension_storage',
    'is_custom_extensions_enabled',
    'load_custom_extensions',
    'register_action_handler',
    'register_access_guard',
    'register_callback_handler',
    'register_command_handler',
    'register_extension_schema',
    'register_extension_settings',
    'register_guard',
    'register_key_lifecycle_hook',
    'register_page_hook',
    'register_payment_provider',
    'register_pricing_policy',
    'register_promo_reward_policy',
    'register_referral_reward_policy',
    'register_user_access_guard',
    'reset_custom_extensions_runtime',
    'validate_custom_extension_file',
    'validate_custom_extensions_dir',
]
