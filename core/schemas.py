"""Bounded JSON schemas shared by HTTP and declared module operations."""
from __future__ import annotations

import json
import re

_TYPES = {'object', 'array', 'string', 'integer', 'number', 'boolean', 'null'}
_FIELDS = {'type', 'properties', 'required', 'additionalProperties', 'items', 'enum',
           'minimum', 'maximum', 'minLength', 'maxLength', 'minItems', 'maxItems', 'pattern', 'description'}


def validate_schema(schema, depth=0):
    """Reject unsupported declarations instead of silently weakening validation."""
    if depth > 8 or not isinstance(schema, dict) or set(schema) - _FIELDS or schema.get('type') not in _TYPES:
        raise ValueError('unsupported schema')
    kind = schema['type']
    allowed = {'type', 'enum', 'description'} | {
        'object': {'properties', 'required', 'additionalProperties'},
        'array': {'items', 'minItems', 'maxItems'},
        'string': {'minLength', 'maxLength', 'pattern'},
        'integer': {'minimum', 'maximum'}, 'number': {'minimum', 'maximum'},
        'boolean': set(), 'null': set(),
    }[kind]
    if set(schema) - allowed or ('description' in schema and not isinstance(schema['description'], str)):
        raise ValueError('inapplicable schema field')
    if kind == 'object':
        properties = schema.get('properties', {})
        required = schema.get('required', [])
        if not isinstance(properties, dict) or len(properties) > 64 or schema.get('additionalProperties') is not False:
            raise ValueError('object schemas must be bounded and closed')
        if (any(not isinstance(key, str) or not 1 <= len(key) <= 128 for key in properties)
                or not isinstance(required, list) or any(not isinstance(key, str) or key not in properties for key in required)
                or len(set(required)) != len(required)):
            raise ValueError('invalid required fields')
        for child in properties.values():
            validate_schema(child, depth + 1)
    if kind == 'array':
        validate_schema(schema.get('items'), depth + 1)
    for low, high, ceiling in (('minimum', 'maximum', 2**53), ('minLength', 'maxLength', 8192), ('minItems', 'maxItems', 100)):
        for field in (low, high):
            if field not in schema:
                continue
            value = schema[field]
            types = (int, float) if low == 'minimum' else (int,)
            floor = -ceiling if low == 'minimum' else 0
            if type(value) not in types or not floor <= value <= ceiling:
                raise ValueError('invalid schema bound')
        if low in schema and high in schema and schema[low] > schema[high]:
            raise ValueError('inverted schema bounds')
    if 'pattern' in schema:
        if not isinstance(schema['pattern'], str) or len(schema['pattern']) > 1024:
            raise ValueError('invalid pattern')
        re.compile(schema['pattern'])
    if 'enum' in schema:
        if not isinstance(schema['enum'], list) or not 1 <= len(schema['enum']) <= 100:
            raise ValueError('invalid enum')
        for item in schema['enum']:
            validate_value(item, {key: value for key, value in schema.items() if key != 'enum'})
    json.dumps(schema, allow_nan=False)


def validate_value(value, schema, path='$', depth=0):
    kind = schema['type']
    valid = {'object': type(value) is dict, 'array': type(value) is list,
             'string': type(value) is str, 'integer': type(value) is int,
             'number': type(value) in (int, float), 'boolean': type(value) is bool,
             'null': value is None}[kind]
    if not valid or depth > 8:
        raise ValueError(path)
    if 'enum' in schema and not any(type(value) is type(item) and value == item for item in schema['enum']):
        raise ValueError(path)
    if kind == 'object':
        properties = schema.get('properties', {})
        if len(value) > 64 or set(value) - set(properties) or set(schema.get('required', [])) - set(value):
            raise ValueError(path)
        for key, item in value.items():
            validate_value(item, properties[key], path + '.' + key, depth + 1)
    elif kind == 'array':
        if not schema.get('minItems', 0) <= len(value) <= min(schema.get('maxItems', 100), 100):
            raise ValueError(path)
        for item in value:
            validate_value(item, schema['items'], path + '[]', depth + 1)
    elif kind == 'string':
        if not schema.get('minLength', 0) <= len(value) <= min(schema.get('maxLength', 8192), 8192):
            raise ValueError(path)
        if 'pattern' in schema and not re.fullmatch(schema['pattern'], value):
            raise ValueError(path)
    elif kind in ('integer', 'number'):
        if not -2**53 <= value <= 2**53 or not schema.get('minimum', -2**53) <= value <= schema.get('maximum', 2**53):
            raise ValueError(path)
    if depth == 0 and len(json.dumps(value, allow_nan=False).encode()) > 65536:
        raise ValueError(path)


EMPTY_OBJECT = {'type': 'object', 'properties': {}, 'additionalProperties': False}
