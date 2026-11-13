"""Stdlib-only presentation snapshot for workers that survive a source rollback."""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

FILENAME = 'maintenance_language.json'


@lru_cache(maxsize=1)
def _snapshot():
    return json.loads(Path(__file__).with_name(FILENAME).read_text(encoding='utf-8'))


def text(key: str, **values) -> str:
    template = _snapshot()['messages'][key]
    if not isinstance(template, str):
        raise ValueError('Maintenance messages must be plain templates: ' + key)
    return template.format_map(values) if values else template


def export(destination: Path, *, code=None, messages=None):
    """Write both dependencies before publishing the worker that imports them."""
    snapshot = {'language': code, 'messages': dict(messages)} if messages is not None else _snapshot()
    for name, content in (
        ('maintenance_localization.py', Path(__file__).read_bytes()),
        (FILENAME, (json.dumps(snapshot, ensure_ascii=False) + '\n').encode('utf-8')),
    ):
        target = destination / name
        temporary = destination / ('.' + name + '.' + str(os.getpid()))
        try:
            with temporary.open('wb') as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
