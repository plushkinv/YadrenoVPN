"""Transport-neutral failures with safe, explicitly selected details."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class CoreError(Exception):
    """An expected domain failure; adapters own presentation and status mapping."""

    def __init__(
        self, code: str, *, details: Mapping[str, Any] | None = None,
        retryable: bool = False, operation_id: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.details = dict(details or {})
        self.retryable = retryable
        self.operation_id = operation_id

    def as_dict(self) -> dict[str, Any]:
        result = {'code': self.code, 'details': dict(self.details), 'retryable': self.retryable}
        if self.operation_id is not None:
            result['operation_id'] = self.operation_id
        return result
