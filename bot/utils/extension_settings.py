"""Declarative settings registry and storage helpers for custom extensions."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

from database.db_extensions import normalize_extension_id

SETTINGS_STORAGE_PREFIX = 'settings.'
SECRET_MASK = '••••••'

_FIELD_KEY_RE = re.compile(r'^[a-z][a-z0-9_]{0,63}$')
_ALLOWED_TYPES = {'bool', 'text', 'url', 'int', 'secret', 'choice'}
_URL_SCHEMES = {'http', 'https', 'tg'}
_MISSING = object()

EXTENSION_SETTINGS: dict[str, list[dict[str, Any]]] = {}


def register_extension_settings(extension_id: str, fields: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Registers normalized settings declarations for one extension."""
    ext_id = normalize_extension_id(extension_id)
    normalized = normalize_extension_settings_fields(fields)
    EXTENSION_SETTINGS[ext_id] = normalized
    return deepcopy(normalized)


def remove_extension_settings(extension_id: str) -> None:
    """Removes settings declarations for one extension."""
    EXTENSION_SETTINGS.pop(normalize_extension_id(extension_id), None)


def normalize_extension_settings_fields(fields: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validates settings declarations without writing to runtime registries."""
    if isinstance(fields, (str, bytes)) or not isinstance(fields, Sequence):
        raise ValueError('settings fields must be a list of field declarations')

    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, raw_field in enumerate(fields):
        field = _normalize_field(raw_field, index)
        key = field['key']
        if key in seen:
            raise ValueError(f"settings field '{key}' is declared more than once")
        seen.add(key)
        normalized.append(field)
    return normalized


def get_extension_settings(extension_id: str) -> list[dict[str, Any]]:
    """Returns a safe copy of settings declarations for one extension."""
    return deepcopy(EXTENSION_SETTINGS.get(normalize_extension_id(extension_id), []))


def get_all_extension_settings() -> dict[str, list[dict[str, Any]]]:
    """Returns a safe copy of all registered settings declarations."""
    return {
        extension_id: deepcopy(fields)
        for extension_id, fields in sorted(EXTENSION_SETTINGS.items())
    }


def get_extension_config(extension_id: str) -> dict[str, Any]:
    """Returns extension config with defaults and valid saved values only."""
    ext_id = normalize_extension_id(extension_id)
    storage = _get_storage(ext_id)
    config: dict[str, Any] = {}
    for field in EXTENSION_SETTINGS.get(ext_id, []):
        key = field['key']
        raw_value = storage.get(_storage_key(key), _MISSING)
        if raw_value is _MISSING:
            config[key] = deepcopy(field['default'])
            continue
        try:
            config[key] = normalize_extension_setting_value(field, raw_value)
        except ValueError:
            config[key] = deepcopy(field['default'])
    return config


def get_extension_settings_state(extension_id: str) -> list[dict[str, Any]]:
    """Returns admin-facing field state including invalid saved value warnings."""
    ext_id = normalize_extension_id(extension_id)
    storage = _get_storage(ext_id)
    result: list[dict[str, Any]] = []
    for field in EXTENSION_SETTINGS.get(ext_id, []):
        key = field['key']
        raw_value = storage.get(_storage_key(key), _MISSING)
        saved = raw_value is not _MISSING
        invalid = False
        warning = ''
        if saved:
            try:
                value = normalize_extension_setting_value(field, raw_value)
            except ValueError as exc:
                value = deepcopy(field['default'])
                invalid = True
                warning = str(exc)
        else:
            value = deepcopy(field['default'])
        result.append({
            'field': deepcopy(field),
            'value': value,
            'display_value': format_extension_setting_value(field, value),
            'is_saved': saved,
            'is_saved_invalid': invalid,
            'warning': warning,
        })
    return result


def save_extension_setting(extension_id: str, field_key: str, value: Any) -> Any:
    """Validates and saves one setting value."""
    ext_id = normalize_extension_id(extension_id)
    field = get_extension_setting_field(ext_id, field_key)
    normalized = normalize_extension_setting_value(field, value)
    _get_storage(ext_id).set(_storage_key(field['key']), normalized)
    return normalized


def clear_extension_setting(extension_id: str, field_key: str) -> bool:
    """Deletes a saved setting value, so the declaration default is used."""
    ext_id = normalize_extension_id(extension_id)
    field = get_extension_setting_field(ext_id, field_key)
    return bool(_get_storage(ext_id).delete(_storage_key(field['key'])))


def get_extension_setting_field(extension_id: str, field_key: str) -> dict[str, Any]:
    """Returns one normalized field declaration."""
    key = _normalize_field_key(field_key)
    for field in EXTENSION_SETTINGS.get(normalize_extension_id(extension_id), []):
        if field['key'] == key:
            return deepcopy(field)
    raise ValueError(f"settings field '{key}' is not registered")


def parse_extension_setting_input(field: Mapping[str, Any], raw_text: Any) -> Any:
    """Parses plain admin text input into a typed setting value."""
    if not isinstance(raw_text, str):
        raise ValueError('value must be a string')
    value = raw_text.strip()
    field_type = str(field.get('type') or '')
    if field_type == 'int':
        if not value:
            raise ValueError('value must be an integer')
        try:
            number = int(value)
        except Exception as exc:
            raise ValueError('value must be an integer') from exc
        return normalize_extension_setting_value(field, number)
    if field_type == 'bool':
        lowered = value.casefold()
        if lowered in {'1', 'true', 'yes', 'on', 'enabled', 'включено', 'да'}:
            return True
        if lowered in {'0', 'false', 'no', 'off', 'disabled', 'выключено', 'нет'}:
            return False
        raise ValueError('value must be bool')
    return normalize_extension_setting_value(field, value)


def format_extension_setting_value(field: Mapping[str, Any], value: Any) -> str:
    """Formats one setting value for admin display."""
    field_type = str(field.get('type') or '')
    if field_type == 'bool':
        return 'включено' if bool(value) else 'выключено'
    if field_type == 'secret':
        return SECRET_MASK if value else 'не задано'
    if field_type == 'choice':
        value_text = str(value)
        for choice in field.get('choices') or []:
            if choice.get('value') == value_text:
                return str(choice.get('label') or value_text)
        return value_text
    if value is None or value == '':
        return 'не задано'
    return str(value)


def setting_storage_key(field_key: str) -> str:
    """Returns the extension_storage key for one setting field."""
    return _storage_key(_normalize_field_key(field_key))


def _normalize_field(raw_field: Mapping[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(raw_field, Mapping):
        raise ValueError(f'settings field #{index + 1} must be a dict')

    field = dict(raw_field)
    key = _normalize_field_key(field.get('key'))
    field_type = _normalize_type(field.get('type'))
    label = _normalize_required_text(field.get('label'), f"settings field '{key}' label")
    required = _normalize_bool(field.get('required', False), f"settings field '{key}' required")

    result: dict[str, Any] = {
        'key': key,
        'type': field_type,
        'label': label,
        'required': required,
    }

    if 'default' not in field:
        raise ValueError(f"settings field '{key}' must declare default")

    for optional in ('placeholder', 'help'):
        if optional in field and field[optional] is not None:
            result[optional] = _normalize_optional_text(field[optional], f"settings field '{key}' {optional}")

    if field_type == 'int':
        if 'min' in field and field['min'] is not None:
            result['min'] = _normalize_int(field['min'], f"settings field '{key}' min")
        if 'max' in field and field['max'] is not None:
            result['max'] = _normalize_int(field['max'], f"settings field '{key}' max")
        if 'min' in result and 'max' in result and result['min'] > result['max']:
            raise ValueError(f"settings field '{key}' min must be <= max")
    elif 'min' in field or 'max' in field:
        raise ValueError(f"settings field '{key}' min/max are supported only for int")

    if field_type == 'choice':
        result['choices'] = _normalize_choices(field.get('choices'), key)
    elif 'choices' in field:
        raise ValueError(f"settings field '{key}' choices are supported only for choice")

    result['default'] = normalize_extension_setting_value(result, field['default'])
    return result


def normalize_extension_setting_value(field: Mapping[str, Any], value: Any) -> Any:
    """Validates and normalizes one setting value against a field declaration."""
    field_type = str(field.get('type') or '')
    key = str(field.get('key') or 'setting')
    required = bool(field.get('required', False))

    if field_type == 'bool':
        if not isinstance(value, bool):
            raise ValueError(f"settings field '{key}' value must be bool")
        return value

    if field_type == 'int':
        number = _normalize_int(value, f"settings field '{key}' value")
        minimum = field.get('min')
        maximum = field.get('max')
        if minimum is not None and number < minimum:
            raise ValueError(f"settings field '{key}' value must be >= {minimum}")
        if maximum is not None and number > maximum:
            raise ValueError(f"settings field '{key}' value must be <= {maximum}")
        return number

    if field_type in {'text', 'secret', 'url'}:
        if not isinstance(value, str):
            raise ValueError(f"settings field '{key}' value must be a string")
        text = value.strip()
        if required and not text:
            raise ValueError(f"settings field '{key}' value is required")
        if field_type == 'url' and text:
            parsed = urlparse(text)
            if parsed.scheme.casefold() not in _URL_SCHEMES:
                raise ValueError(f"settings field '{key}' URL scheme is not supported")
            if parsed.scheme.casefold() in {'http', 'https'} and not parsed.netloc:
                raise ValueError(f"settings field '{key}' URL must include host")
        return text

    if field_type == 'choice':
        if not isinstance(value, str):
            raise ValueError(f"settings field '{key}' value must be a string")
        choice_value = value.strip()
        choices = {str(choice.get('value')) for choice in field.get('choices') or []}
        if choice_value not in choices:
            raise ValueError(f"settings field '{key}' value must be one of declared choices")
        return choice_value

    raise ValueError(f"settings field '{key}' type is not supported")


def _normalize_field_key(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError('settings field key must be a string')
    key = value.strip().casefold()
    if not _FIELD_KEY_RE.fullmatch(key):
        raise ValueError("settings field key must match ^[a-z][a-z0-9_]{0,63}$")
    return key


def _normalize_type(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError('settings field type must be a string')
    field_type = value.strip().casefold()
    if field_type not in _ALLOWED_TYPES:
        raise ValueError(f"settings field type must be one of {', '.join(sorted(_ALLOWED_TYPES))}")
    return field_type


def _normalize_required_text(value: Any, field_name: str) -> str:
    text = _normalize_optional_text(value, field_name)
    if not text:
        raise ValueError(f'{field_name} is required')
    return text


def _normalize_optional_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f'{field_name} must be a string')
    return value.strip()


def _normalize_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f'{field_name} must be bool')
    return value


def _normalize_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'{field_name} must be integer')
    return value


def _normalize_choices(raw_choices: Any, field_key: str) -> list[dict[str, str]]:
    if isinstance(raw_choices, (str, bytes)) or not isinstance(raw_choices, Sequence):
        raise ValueError(f"settings field '{field_key}' choices must be a list")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw_choice in enumerate(raw_choices):
        if isinstance(raw_choice, str):
            value = raw_choice.strip()
            label = value
        elif isinstance(raw_choice, Mapping):
            value = _normalize_required_text(raw_choice.get('value'), f"settings field '{field_key}' choice value")
            label = _normalize_required_text(raw_choice.get('label', value), f"settings field '{field_key}' choice label")
        else:
            raise ValueError(f"settings field '{field_key}' choice #{index + 1} must be a string or dict")
        if not value:
            raise ValueError(f"settings field '{field_key}' choice value is required")
        if value in seen:
            raise ValueError(f"settings field '{field_key}' choice '{value}' is duplicated")
        seen.add(value)
        result.append({'value': value, 'label': label})
    if not result:
        raise ValueError(f"settings field '{field_key}' choices must not be empty")
    return result


def _storage_key(field_key: str) -> str:
    return f'{SETTINGS_STORAGE_PREFIX}{field_key}'


def _get_storage(extension_id: str):
    from database.requests import get_extension_storage

    return get_extension_storage(extension_id)


__all__ = [
    'EXTENSION_SETTINGS',
    'SECRET_MASK',
    'SETTINGS_STORAGE_PREFIX',
    'clear_extension_setting',
    'format_extension_setting_value',
    'get_all_extension_settings',
    'get_extension_config',
    'get_extension_setting_field',
    'get_extension_settings',
    'get_extension_settings_state',
    'normalize_extension_setting_value',
    'normalize_extension_settings_fields',
    'parse_extension_setting_input',
    'register_extension_settings',
    'remove_extension_settings',
    'save_extension_setting',
    'setting_storage_key',
]
