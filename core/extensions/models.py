"""Native Pydantic contracts, with the released dictionary validator retained."""
import json

from pydantic import BaseModel, TypeAdapter

from core.schemas import validate_schema, validate_value

MAX_JSON_BYTES = 65536


def json_bytes(value):
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(encoded) > MAX_JSON_BYTES:
        raise ValueError('JSON exceeds 64 KiB')
    return encoded


def declaration(*, input_schema=None, result_schema=None, input_model=None, result_model=None):
    if input_model is not None or result_model is not None:
        if input_schema is not None or result_schema is not None:
            raise ValueError('use models or legacy schemas, not both')
        if not isinstance(input_model, type) or not issubclass(input_model, BaseModel) or result_model is None:
            raise ValueError('input_model must be a Pydantic model; result_model is required')
        inputs, result = TypeAdapter(input_model), TypeAdapter(result_model)
        if inputs.json_schema().get('type') != 'object':
            raise ValueError('operation inputs must be an object')
        return {'input_schema': inputs.json_schema(), 'result_schema': result.json_schema(mode='serialization'),
                'input_adapter': inputs, 'result_adapter': result, 'schema_format': 'pydantic'}
    validate_schema(input_schema)
    validate_schema(result_schema)
    if input_schema['type'] != 'object':
        raise ValueError('operation inputs must be a closed object')
    from copy import deepcopy
    return {'input_schema': deepcopy(input_schema), 'result_schema': deepcopy(result_schema),
            'schema_format': 'legacy'}


def parse_inputs(policy, inputs):
    json_bytes(inputs)
    if not isinstance(inputs, dict):
        raise ValueError('inputs must be an object')
    if 'input_adapter' in policy:
        return policy['input_adapter'].validate_python(inputs)
    validate_value(inputs, policy['input_schema'])
    return inputs


def serialize_result(policy, result):
    if 'result_adapter' in policy:
        adapter = policy['result_adapter']
        result = adapter.dump_python(adapter.validate_python(result), mode='json', by_alias=True)
    else:
        validate_value(result, policy['result_schema'])
    json_bytes(result)
    return result
