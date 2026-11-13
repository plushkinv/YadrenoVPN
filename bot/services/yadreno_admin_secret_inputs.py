"""Local data validation for the negotiated secret_inputs_v1 transport."""

from __future__ import annotations

import math
import os
import re
from typing import Any

SECRET_INPUTS_CAPABILITY = "secret_inputs_v1"
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def subprocess_secret_environment(args: dict[str, Any]) -> dict[str, str] | None:
    """Return a per-child environment; never mutate the bot's environment."""
    if "secret_env" not in args:
        return None
    value = args["secret_env"]
    if not isinstance(value, dict):
        raise ValueError("secret_env must be an object of variable names and strings")
    if any(not isinstance(k, str) or not _NAME.fullmatch(k) for k in value):
        raise ValueError("invalid secret_env variable name")
    if any(not isinstance(v, str) or "\x00" in v for v in value.values()):
        raise ValueError("secret_env values must be strings without NUL")
    return {**os.environ, **value}


def sqlite_parameters(args: dict[str, Any]) -> dict[str, Any] | None:
    """Validate resolved SQLite scalar bindings without interpolation."""
    if "params" not in args:
        return None
    params = args["params"]
    if not isinstance(params, dict):
        raise ValueError("params must be an object of named scalar values")
    for key, value in params.items():
        if not isinstance(key, str) or not _NAME.fullmatch(key):
            raise ValueError("invalid SQL parameter name")
        if type(value) not in (str, int, float, bool, type(None)):
            raise ValueError("SQL parameters must be scalar values")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("SQL numbers must be finite")
        if type(value) is int and not -(2 ** 63) <= value < 2 ** 63:
            raise ValueError("SQLite integer parameter is outside the signed 64-bit range")
    return dict(params)
