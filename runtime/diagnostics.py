"""Read-only local diagnostics: python -m runtime.diagnostics."""
import json
from pathlib import Path


def main():
    from database.connection import DB_PATH
    if not Path(DB_PATH).is_file():
        raise SystemExit('Database is not initialized')
    from core.administration import web_diagnostics
    result = web_diagnostics()
    # This process cannot assert readiness or live plugin execution in the owner.
    result['core_active'] = None
    from database.requests import get_core_module_manifests
    result['modules'] = [{**value, 'state': 'runtime_not_inspected'} for value in get_core_module_manifests().values()]
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
