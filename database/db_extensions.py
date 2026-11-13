"""Controlled storage and schema registry for custom extensions."""
from __future__ import annotations

from localization import text as _tr

import json
import logging
import math
import re
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from .connection import get_db

logger = logging.getLogger(__name__)

_EXTENSION_ID_RE = re.compile(r'^[a-z][a-z0-9_]{0,47}$')
_IDENTIFIER_RE = re.compile(r'^[a-z][a-z0-9_]{0,63}$')
_SQL_IDENTIFIER_RE = re.compile(r'^[a-z][a-z0-9_]{0,191}$')
_STORAGE_KEY_RE = re.compile(r'^[a-zA-Z0-9_.:-]{1,128}$')
_ALLOWED_COLUMN_TYPES = {'TEXT', 'INTEGER', 'REAL', 'BLOB', 'NUMERIC'}
_ALLOWED_DEFAULT_SQL = {'CURRENT_TIMESTAMP', 'CURRENT_DATE', 'CURRENT_TIME'}


def create_extension_support_tables(conn: sqlite3.Connection) -> None:
    """Creates extension storage/schema system tables."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS extension_schema_versions (
            extension_id TEXT PRIMARY KEY,
            version INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS extension_storage (
            extension_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (extension_id, key)
        )
        """
    )


def register_extension_schema(extension_id: str, migrations: Sequence[Mapping[str, Any]]) -> None:
    """Apply extension-owned migration sequences on a dedicated connection."""
    from .extension_sql import extension_connection
    with extension_connection() as conn:
        apply_extension_schema(conn, extension_id, migrations)


def validate_extension_schema_migrations(extension_id: str, migrations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Checks the declarative extension scheme without writing to the production database."""
    ext_id = normalize_extension_id(extension_id)
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    try:
        normalized = _normalize_schema_migrations(migrations)
        has_sql = any('sql' in step for step in normalized)
        # SQL can depend on installed core/shared tables. Static validation checks
        # its declaration; real execution enforces SQL access. Pure declarative
        # validation stays in memory, outside the live application's write fence.
        for step in normalized:
            for table in step.get('tables', []):
                _create_extension_table(conn, ext_id, table)
            if not has_sql:
                for table in step.get('alter_tables', []):
                    _alter_extension_table(conn, ext_id, table)
        return normalized
    finally:
        conn.close()


def apply_extension_schema(
    conn: sqlite3.Connection,
    extension_id: str,
    migrations: Sequence[Mapping[str, Any]],
) -> None:
    """Applies a declarative extension scheme to the passed connection."""
    ext_id = normalize_extension_id(extension_id)
    create_extension_support_tables(conn)

    normalized = _normalize_schema_migrations(migrations)
    current_version = _get_extension_schema_version(conn, ext_id)
    for migration in normalized:
        version = migration['version']
        if version <= current_version:
            continue

        from .extension_sql import sql_session
        with sql_session(conn) as session, session.atomic():
            if 'sql' in migration:
                for statement in migration['sql']:
                    session.run(statement)
            else:
                # Legacy declarations keep their names and validation rules.
                from runtime.readiness import sqlite_authorizer
                conn.set_authorizer(sqlite_authorizer)
                for table in migration.get('tables', []):
                    _create_extension_table(conn, ext_id, table)
                for table in migration.get('alter_tables', []):
                    _alter_extension_table(conn, ext_id, table)
            from runtime.readiness import sqlite_authorizer
            conn.set_authorizer(sqlite_authorizer)
            _set_extension_schema_version(conn, ext_id, version)
        current_version = version
        logger.info("Extension schema %s обновлена до версии %s", ext_id, version)


def get_extension_storage(extension_id: str) -> 'ExtensionStorage':
    """Returns the extension's namespaced storage/repository API."""
    return ExtensionStorage(extension_id)


def normalize_extension_id(extension_id: str) -> str:
    """Normalizes and validates extension_id."""
    if not isinstance(extension_id, str):
        raise ValueError(_tr('db_extensions.normalize_extension_id.1'))
    value = extension_id.strip().casefold()
    if not _EXTENSION_ID_RE.fullmatch(value):
        raise ValueError(_tr('db_extensions.normalize_extension_id.2'))
    return value


def extension_table_name(extension_id: str, table_name: str) -> str:
    """Returns the physical name of the extension table."""
    return f"ext_{normalize_extension_id(extension_id)}_{_normalize_local_table_name(table_name)}"


class ExtensionStorage:
    """Namespaced storage extensions without arbitrary SQL."""

    def __init__(self, extension_id: str, conn: sqlite3.Connection | None = None):
        self.extension_id = normalize_extension_id(extension_id)
        self._conn = conn

    def get(self, key: str, default: Any = None) -> Any:
        storage_key = _normalize_storage_key(key)

        def op(conn: sqlite3.Connection) -> Any:
            row = conn.execute(
                """
                SELECT value FROM extension_storage
                WHERE extension_id = ? AND key = ?
                """,
                (self.extension_id, storage_key),
            ).fetchone()
            if row is None:
                return default
            return json.loads(row['value'])

        return self._with_conn(op)

    def set(self, key: str, value: Any) -> None:
        _ensure_extension_mutation_allowed('extension_storage.set')
        storage_key = _normalize_storage_key(key)
        payload = _json_dumps_storage_value(value)

        def op(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO extension_storage (extension_id, key, value, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(extension_id, key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (self.extension_id, storage_key, payload),
            )

        self._with_conn(op)

    def delete(self, key: str) -> bool:
        _ensure_extension_mutation_allowed('extension_storage.delete')
        storage_key = _normalize_storage_key(key)

        def op(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute(
                "DELETE FROM extension_storage WHERE extension_id = ? AND key = ?",
                (self.extension_id, storage_key),
            )
            return cursor.rowcount > 0

        return self._with_conn(op)

    def items(self, prefix: str | None = None) -> dict[str, Any]:
        if prefix is not None:
            prefix = _normalize_storage_key(prefix)

        def op(conn: sqlite3.Connection) -> dict[str, Any]:
            if prefix is None:
                rows = conn.execute(
                    """
                    SELECT key, value FROM extension_storage
                    WHERE extension_id = ?
                    ORDER BY key
                    """,
                    (self.extension_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT key, value FROM extension_storage
                    WHERE extension_id = ? AND key LIKE ? ESCAPE '\\'
                    ORDER BY key
                    """,
                    (self.extension_id, _storage_like_prefix(prefix)),
                ).fetchall()
            return {row['key']: json.loads(row['value']) for row in rows}

        return self._with_conn(op)

    def table(self, table_name: str) -> 'ExtensionTable':
        return ExtensionTable(self, table_name)

    def _with_conn(self, callback):
        if self._conn is not None:
            return callback(self._conn)
        with get_db() as conn:
            return callback(conn)


class ExtensionTable:
    """Repository API for one extension table."""

    def __init__(self, storage: ExtensionStorage, table_name: str):
        self._storage = storage
        self.local_name = _normalize_local_table_name(table_name)
        self.physical_name = extension_table_name(storage.extension_id, self.local_name)

    def insert(self, values: Mapping[str, Any]) -> int:
        _ensure_extension_mutation_allowed('extension_table.insert')
        payload = _normalize_values(values)
        columns = list(payload.keys())
        placeholders = ', '.join('?' for _ in columns)
        column_sql = ', '.join(_quote_identifier(column) for column in columns)
        sql = (
            f"INSERT INTO {_quote_identifier(self.physical_name)} "
            f"({column_sql}) VALUES ({placeholders})"
        )

        def op(conn: sqlite3.Connection) -> int:
            cursor = conn.execute(sql, tuple(payload[column] for column in columns))
            return cursor.lastrowid

        return self._storage._with_conn(op)

    def get_by(self, column: str, value: Any) -> dict[str, Any] | None:
        column_name = _normalize_identifier(column, 'column')
        sql = (
            f"SELECT * FROM {_quote_identifier(self.physical_name)} "
            f"WHERE {_quote_identifier(column_name)} = ? LIMIT 1"
        )

        def op(conn: sqlite3.Connection) -> dict[str, Any] | None:
            row = conn.execute(sql, (value,)).fetchone()
            return _row_to_dict(row)

        return self._storage._with_conn(op)

    def find(self, filters: Mapping[str, Any] | None = None, *, limit: int = 100) -> list[dict[str, Any]]:
        where_sql, params = _build_where({} if filters is None else filters)
        safe_limit = _normalize_find_limit(limit)
        sql = f"SELECT * FROM {_quote_identifier(self.physical_name)}"
        if where_sql:
            sql += f" WHERE {where_sql}"
        sql += f" LIMIT {safe_limit}"

        def op(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

        return self._storage._with_conn(op)

    def update(self, filters: Mapping[str, Any], values: Mapping[str, Any]) -> int:
        _ensure_extension_mutation_allowed('extension_table.update')
        where_sql, where_params = _build_where(filters)
        if not where_sql:
            raise ValueError(_tr('db_extensions.update.1'))
        payload = _normalize_values(values)
        set_sql = ', '.join(
            f"{_quote_identifier(column)} = ?" for column in payload.keys()
        )
        sql = (
            f"UPDATE {_quote_identifier(self.physical_name)} "
            f"SET {set_sql} WHERE {where_sql}"
        )

        def op(conn: sqlite3.Connection) -> int:
            cursor = conn.execute(sql, tuple(payload.values()) + tuple(where_params))
            return cursor.rowcount

        return self._storage._with_conn(op)

    def delete(self, filters: Mapping[str, Any]) -> int:
        _ensure_extension_mutation_allowed('extension_table.delete')
        where_sql, params = _build_where(filters)
        if not where_sql:
            raise ValueError(_tr('db_extensions.delete.1'))
        sql = f"DELETE FROM {_quote_identifier(self.physical_name)} WHERE {where_sql}"

        def op(conn: sqlite3.Connection) -> int:
            cursor = conn.execute(sql, params)
            return cursor.rowcount

        return self._storage._with_conn(op)


def _ensure_extension_mutation_allowed(operation: str) -> None:
    """Reject storage writes made from declarative semantic policies."""
    from bot.utils.action_policy import ensure_action_policy_read_only

    ensure_action_policy_read_only(operation)


def _normalize_schema_migrations(migrations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(migrations, Sequence) or isinstance(migrations, (str, bytes)):
        raise ValueError(_tr('db_extensions.normalize_schema_migrations.1'))

    result: list[dict[str, Any]] = []
    seen_versions: set[int] = set()
    for migration in migrations:
        if not isinstance(migration, Mapping):
            raise ValueError(_tr('db_extensions.normalize_schema_migrations.2'))
        _reject_unknown_keys(migration, {'version', 'tables', 'alter_tables', 'sql'}, 'migration')
        version = _normalize_schema_version(migration.get('version'))
        if version in seen_versions:
            raise ValueError(_tr('db_extensions.normalize_schema_migrations.3', p0=f'{version}'))
        seen_versions.add(version)
        if 'sql' in migration:
            if 'tables' in migration or 'alter_tables' in migration:
                raise ValueError('migration must use either sql or declarative tables/alter_tables')
            statements = _required_sequence_field(migration, 'sql', 'migration.sql', nonempty=True)
            if any(not isinstance(sql, str) or not sql.strip() for sql in statements):
                raise ValueError('migration.sql must contain nonempty SQL statements')
            result.append({'version': version, 'sql': list(statements)})
            continue
        tables = _optional_sequence_field(migration, 'tables', 'migration.tables')
        alter_tables = _optional_sequence_field(migration, 'alter_tables', 'migration.alter_tables')
        result.append({
            'version': version,
            'tables': tables,
            'alter_tables': alter_tables,
        })

    return sorted(result, key=lambda item: item['version'])


def _normalize_schema_version(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(_tr('db_extensions.normalize_schema_version.1'))
    return value


def _schema_bool(
    data: Mapping[str, Any],
    field: str,
    label: str,
    *,
    default: bool = False,
) -> bool:
    if field not in data:
        return default
    value = data[field]
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be bool")
    return value


def _create_extension_table(
    conn: sqlite3.Connection,
    extension_id: str,
    table_decl: Mapping[str, Any],
) -> None:
    if not isinstance(table_decl, Mapping):
        raise ValueError(_tr('db_extensions.create_extension_table.1'))
    _reject_unknown_keys(table_decl, {'name', 'columns', 'unique', 'indexes'}, 'table declaration')

    table_name = extension_table_name(extension_id, table_decl.get('name'))
    columns = _required_sequence_field(table_decl, 'columns', 'table.columns', nonempty=True)

    column_defs = [_build_column_sql(column) for column in columns]
    for unique_group in _optional_sequence_field(table_decl, 'unique', 'table.unique'):
        if not isinstance(unique_group, Sequence) or isinstance(unique_group, (str, bytes)) or not unique_group:
            raise ValueError(_tr('db_extensions.create_extension_table.2'))
        unique_columns = ', '.join(
            _quote_identifier(_normalize_identifier(column, 'column'))
            for column in unique_group
        )
        column_defs.append(f"UNIQUE ({unique_columns})")

    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {_quote_identifier(table_name)} ({', '.join(column_defs)})"
    )

    for index_decl in _optional_sequence_field(table_decl, 'indexes', 'table.indexes'):
        _create_extension_index(conn, table_name, index_decl)


def _alter_extension_table(
    conn: sqlite3.Connection,
    extension_id: str,
    table_decl: Mapping[str, Any],
) -> None:
    if not isinstance(table_decl, Mapping):
        raise ValueError(_tr('db_extensions.alter_extension_table.1'))
    _reject_unknown_keys(table_decl, {'name', 'add_columns', 'indexes'}, 'alter table declaration')

    table_name = extension_table_name(extension_id, table_decl.get('name'))
    if not _table_exists(conn, table_name):
        raise ValueError(_tr('db_extensions.alter_extension_table.2', p0=f'{table_name}'))

    add_columns = _optional_sequence_field(table_decl, 'add_columns', 'alter_table.add_columns')
    indexes = _optional_sequence_field(table_decl, 'indexes', 'alter_table.indexes')
    if not add_columns and not indexes:
        raise ValueError(_tr('db_extensions.alter_extension_table.3'))

    existing_columns = _table_columns(conn, table_name)
    for column in add_columns:
        column_name = _normalize_identifier(column.get('name') if isinstance(column, Mapping) else None, 'column')
        if column_name in existing_columns:
            raise ValueError(_tr('db_extensions.alter_extension_table.4', p0=f'{column_name}', p1=f'{table_name}'))
        conn.execute(
            f"ALTER TABLE {_quote_identifier(table_name)} "
            f"ADD COLUMN {_build_add_column_sql(column)}"
        )
        existing_columns.add(column_name)

    for index_decl in indexes:
        _create_extension_index(conn, table_name, index_decl)


def _build_column_sql(column_decl: Mapping[str, Any]) -> str:
    if not isinstance(column_decl, Mapping):
        raise ValueError(_tr('db_extensions.build_column_sql.1'))
    _reject_unknown_keys(
        column_decl,
        {'name', 'type', 'nullable', 'primary_key', 'unique', 'default', 'default_sql'},
        'column declaration',
    )
    name = _normalize_identifier(column_decl.get('name'), 'column')
    column_type = _normalize_column_type(column_decl.get('type'))

    primary_key = _schema_bool(column_decl, 'primary_key', 'column.primary_key')
    nullable = _schema_bool(column_decl, 'nullable', 'column.nullable', default=True)
    unique = _schema_bool(column_decl, 'unique', 'column.unique')

    if 'default' in column_decl and 'default_sql' in column_decl:
        raise ValueError(_tr('db_extensions.build_column_sql.2'))

    parts = [_quote_identifier(name), column_type]
    if primary_key:
        parts.append('PRIMARY KEY')
    if not nullable and not primary_key:
        parts.append('NOT NULL')
    if unique:
        parts.append('UNIQUE')
    if 'default' in column_decl:
        parts.append(f"DEFAULT {_sql_literal(column_decl['default'])}")
    if 'default_sql' in column_decl:
        parts.append(f"DEFAULT {_normalize_default_sql(column_decl['default_sql'])}")
    return ' '.join(parts)


def _build_add_column_sql(column_decl: Mapping[str, Any]) -> str:
    if not isinstance(column_decl, Mapping):
        raise ValueError(_tr('db_extensions.build_column_sql.1'))
    primary_key = _schema_bool(column_decl, 'primary_key', 'column.primary_key')
    unique = _schema_bool(column_decl, 'unique', 'column.unique')
    nullable = _schema_bool(column_decl, 'nullable', 'column.nullable', default=True)
    if primary_key:
        raise ValueError(_tr('db_extensions.build_add_column_sql.1'))
    if unique:
        raise ValueError(_tr('db_extensions.build_add_column_sql.2'))
    if 'default_sql' in column_decl:
        raise ValueError(_tr('db_extensions.build_add_column_sql.3'))
    if not nullable and (
        'default' not in column_decl or column_decl.get('default') is None
    ):
        raise ValueError(_tr('db_extensions.build_add_column_sql.4'))
    return _build_column_sql(column_decl)


def _create_extension_index(
    conn: sqlite3.Connection,
    table_name: str,
    index_decl: Mapping[str, Any],
) -> None:
    if not isinstance(index_decl, Mapping):
        raise ValueError(_tr('db_extensions.create_extension_index.1'))
    _reject_unknown_keys(index_decl, {'name', 'columns', 'unique'}, 'index declaration')
    local_index_name = _normalize_identifier(index_decl.get('name'), 'index')
    columns = _required_sequence_field(index_decl, 'columns', 'index.columns', nonempty=True)

    index_name = f"idx_{table_name}_{local_index_name}"
    column_sql = ', '.join(
        _quote_identifier(_normalize_identifier(column, 'column'))
        for column in columns
    )
    unique = _schema_bool(index_decl, 'unique', 'index.unique')
    unique_sql = 'UNIQUE ' if unique else ''
    conn.execute(
        f"CREATE {unique_sql}INDEX IF NOT EXISTS {_quote_identifier(index_name)} "
        f"ON {_quote_identifier(table_name)} ({column_sql})"
    )


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({_quote_identifier(table_name)})").fetchall()
    return {str(row['name']).casefold() for row in rows}


def _get_extension_schema_version(conn: sqlite3.Connection, extension_id: str) -> int:
    row = conn.execute(
        "SELECT version FROM extension_schema_versions WHERE extension_id = ?",
        (extension_id,),
    ).fetchone()
    return int(row['version']) if row else 0


def _set_extension_schema_version(
    conn: sqlite3.Connection,
    extension_id: str,
    version: int,
) -> None:
    conn.execute(
        """
        INSERT INTO extension_schema_versions (extension_id, version, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(extension_id) DO UPDATE SET
            version = excluded.version,
            updated_at = CURRENT_TIMESTAMP
        """,
        (extension_id, version),
    )


def _normalize_local_table_name(name: str) -> str:
    value = _normalize_identifier(name, 'table')
    if value.startswith('ext_'):
        raise ValueError(_tr('db_extensions.normalize_local_table_name.1'))
    return value


def _normalize_identifier(value: Any, kind: str) -> str:
    if not isinstance(value, str):
        raise ValueError(_tr('action_registry.require_text.1', p0=f'{kind}'))
    identifier = value.strip().casefold()
    if not _IDENTIFIER_RE.fullmatch(identifier):
        raise ValueError(_tr('db_extensions.normalize_identifier.1', p0=f'{kind}'))
    return identifier


def _required_sequence_field(
    data: Mapping[str, Any],
    field: str,
    label: str,
    *,
    nonempty: bool = False,
) -> list[Any]:
    if field not in data:
        raise ValueError(_tr('db_extensions.required_sequence_field.1', p0=f'{label}'))
    return _normalize_sequence_value(data[field], label, nonempty=nonempty)


def _optional_sequence_field(data: Mapping[str, Any], field: str, label: str) -> list[Any]:
    if field not in data:
        return []
    return _normalize_sequence_value(data[field], label)


def _normalize_sequence_value(value: Any, label: str, *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(_tr('db_extensions.required_sequence_field.1', p0=f'{label}'))
    items = list(value)
    if nonempty and not items:
        raise ValueError(_tr('db_extensions.normalize_sequence_value.1', p0=f'{label}'))
    return items


def _normalize_column_type(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(_tr('db_extensions.normalize_column_type.1'))
    column_type = value.strip().upper()
    if column_type not in _ALLOWED_COLUMN_TYPES:
        raise ValueError(_tr('db_extensions.normalize_column_type.2', p0=f'{column_type!r}'))
    return column_type


def _normalize_default_sql(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(_tr('db_extensions.normalize_default_sql.1'))
    default_sql = value.strip().upper()
    if default_sql not in _ALLOWED_DEFAULT_SQL:
        raise ValueError(_tr('db_extensions.normalize_default_sql.2'))
    return default_sql


def _normalize_storage_key(key: str) -> str:
    if not isinstance(key, str):
        raise ValueError(_tr('db_extensions.normalize_storage_key.1'))
    value = key.strip()
    if not _STORAGE_KEY_RE.fullmatch(value):
        raise ValueError(_tr('db_extensions.normalize_storage_key.2'))
    return value


def _normalize_find_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError(_tr('db_extensions.normalize_find_limit.1'))
    return max(1, min(limit, 500))


def _storage_like_prefix(prefix: str) -> str:
    return prefix.replace('_', r'\_') + '%'


def _normalize_values(values: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(values, Mapping) or not values:
        raise ValueError(_tr('db_extensions.normalize_values.1'))
    return {
        _normalize_identifier(column, 'column'): _normalize_repository_value(value)
        for column, value in values.items()
    }


def _json_dumps_storage_value(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(_tr('db_extensions.json_dumps_storage_value.1')) from exc


def _normalize_repository_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(_tr('db_extensions.normalize_repository_value.1'))
    return value


def _build_where(filters: Mapping[str, Any]) -> tuple[str, tuple[Any, ...]]:
    if not isinstance(filters, Mapping):
        raise ValueError(_tr('db_extensions.build_where.1'))
    if not filters:
        return '', ()
    normalized = _normalize_values(filters)
    where_sql = ' AND '.join(
        f"{_quote_identifier(column)} = ?" for column in normalized.keys()
    )
    return where_sql, tuple(normalized.values())


def _quote_identifier(identifier: str) -> str:
    if not _SQL_IDENTIFIER_RE.fullmatch(identifier):
        raise ValueError(_tr('db_extensions.quote_identifier.1', p0=f'{identifier}'))
    return f'"{identifier}"'


def _sql_literal(value: Any) -> str:
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return '1' if value else '0'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(_tr('db_extensions.sql_literal.1'))
        return repr(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    raise ValueError(_tr('db_extensions.sql_literal.2'))


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _reject_unknown_keys(data: Mapping[str, Any], allowed: set[str], title: str) -> None:
    unknown = set(data.keys()) - allowed
    if unknown:
        raise ValueError(_tr('db_extensions.reject_unknown_keys.1', p0=f'{title}', p1=f"{', '.join(sorted(unknown))}"))


__all__ = [
    'ExtensionStorage',
    'ExtensionTable',
    'apply_extension_schema',
    'create_extension_support_tables',
    'extension_table_name',
    'get_extension_storage',
    'normalize_extension_id',
    'register_extension_schema',
    'validate_extension_schema_migrations',
]
