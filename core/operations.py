"""Validation shared by durable non-financial account operations."""
import hashlib
import json
import re

from core.results import CoreError


def operation_fingerprint(idempotency_key, inputs):
    if not isinstance(idempotency_key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', idempotency_key):
        raise CoreError('invalid_request', details={'field': 'idempotency_key'})
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, allow_nan=False).encode()).hexdigest()


def positive_id(value, field='id'):
    if type(value) is not int or value <= 0:
        raise CoreError('invalid_request', details={'field': field})
    return value
