"""SQLite authorization and schema invariants for the shared extension area."""
from __future__ import annotations

import sqlite3

from runtime.readiness import sqlite_authorizer

INFORMATION_PRAGMAS = frozenset({
    'table_info', 'table_xinfo', 'table_list', 'index_list', 'index_info',
    'index_xinfo', 'foreign_key_list', 'foreign_key_check', 'integrity_check',
    'quick_check', 'database_list', 'compile_options',
})
_SCHEMA_ACTIONS = frozenset({
    sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE,
    sqlite3.SQLITE_CREATE_VIEW, sqlite3.SQLITE_DROP_VIEW,
    sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_DROP_TRIGGER,
    sqlite3.SQLITE_ALTER_TABLE,
})
_DML_ACTIONS = frozenset({sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE})
_SCHEMA_TABLES = {'sqlite_master', 'sqlite_schema', 'sqlite_temp_master', 'sqlite_temp_schema'}


def is_extension_name(name: str | None) -> bool:
    """SQLite identifiers are ASCII case-insensitive; ownership is shared."""
    return isinstance(name, str) and name[:4].lower() == 'ext_' and len(name) > 4


def is_extension_object(kind: str, name: str, table: str) -> bool:
    if kind in {'table', 'view'}:
        return is_extension_name(name)
    return kind in {'index', 'trigger'} and is_extension_name(table)


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class SQLAccess:
    def __init__(self):
        self.trusted = False
        self.read_only = False
        self.probing = False
        self.schema_changed = False
        self.error = None
        self.objects = {}

    def __call__(self, action, first, second, database, trigger):
        if not self.probing and sqlite_authorizer(action, first, second, database, trigger) != sqlite3.SQLITE_OK:
            return sqlite3.SQLITE_DENY
        if self.trusted:
            return sqlite3.SQLITE_OK
        if database not in (None, 'main', 'temp'):
            return sqlite3.SQLITE_DENY
        if action in _SCHEMA_ACTIONS or action in _DML_ACTIONS or action == sqlite3.SQLITE_REINDEX:
            if not self.probing:
                try:
                    from bot.utils.action_policy import ensure_action_policy_read_only
                    ensure_action_policy_read_only('database SQL write')
                except RuntimeError as exc:
                    self.error = exc
                    return sqlite3.SQLITE_DENY
                if self.read_only:
                    return sqlite3.SQLITE_DENY
            if database == 'temp' and not (action in _DML_ACTIONS and first in _SCHEMA_TABLES):
                return sqlite3.SQLITE_DENY
        if action in _DML_ACTIONS:
            # SQLite itself writes schema records during DDL. Direct schema DML
            # remains rejected by SQLite: writable_schema is never enabled.
            allowed = is_extension_name(first) or first in _SCHEMA_TABLES
            if first == 'sqlite_sequence' and self.schema_changed and not trigger and not self.probing:
                allowed = True
        elif action in _SCHEMA_ACTIONS:
            self.schema_changed = True
            target = second if action in {
                sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_DROP_INDEX,
                sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_DROP_TRIGGER,
                sqlite3.SQLITE_ALTER_TABLE,
            } else first
            allowed = is_extension_name(target)
            # Native AUTOINCREMENT bookkeeping has no extension-owned name.
            if action == sqlite3.SQLITE_CREATE_TABLE and first == 'sqlite_sequence':
                allowed = True
            if allowed and action == sqlite3.SQLITE_CREATE_INDEX:
                self.objects[('index', first)] = ('index', first, second, None)
        elif action == sqlite3.SQLITE_REINDEX:
            obj = self.objects.get(('index', first)) or self.objects.get(('table', first))
            allowed = bool(obj and is_extension_object(*obj[:3]))
        elif action == sqlite3.SQLITE_PRAGMA:
            allowed = str(first).lower() in INFORMATION_PRAGMAS
        elif action == sqlite3.SQLITE_FUNCTION:
            allowed = str(second).lower() not in {'load_extension', 'readfile', 'writefile', 'fts3_tokenizer'}
        else:
            allowed = action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_RECURSIVE}
        return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY


def schema_snapshot(execute):
    # Trigger names can equal table/index names; never collapse those records.
    return {(row[0], row[1]): tuple(row) for row in execute(
        'SELECT type, name, tbl_name, sql FROM main.sqlite_schema'
    ).fetchall()}


def validate_schema(session, before):
    """Validate rename destinations, foreign keys and deferred trigger programs."""
    after = schema_snapshot(session.internal)
    def core_objects(objects):
        return {name: row for name, row in objects.items()
                if row[1] != 'sqlite_sequence' and not is_extension_object(*row[:3])}
    if core_objects(before) != core_objects(after):
        raise sqlite3.DatabaseError('extension SQL cannot change objects outside ext_*')
    for kind, name, table, sql in after.values():
        if kind != 'table' or not is_extension_name(name):
            continue
        for fk in session.internal(f'PRAGMA main.foreign_key_list({quote_identifier(name)})'):
            if not is_extension_name(fk[2]):
                raise sqlite3.DatabaseError('extension foreign keys must reference ext_* tables')

    # CREATE TRIGGER stores its body without preparing it. Compile every possible
    # entry into the affected schema now, so an old storage/core connection can
    # never activate a deferred write to core tables. EXPLAIN executes no DML.
    targets = {row[2] for row in after.values() if row[0] == 'trigger' and is_extension_name(row[2])}
    for target in targets:
        quoted = quote_identifier(target)
        columns = [row[1] for row in session.internal(f'PRAGMA main.table_xinfo({quoted})') if row[6] == 0]
        if ('table', target) in after:
            # UPDATE OF also recognizes the three implicit rowid spellings.
            # They must be compiled even when old repository callers write them.
            for alias in ('rowid', '_rowid_', 'oid'):
                if alias in columns:
                    continue
                try:
                    session.internal(f'SELECT {alias} FROM {quoted} LIMIT 0').close()
                except sqlite3.OperationalError:
                    continue  # WITHOUT ROWID has no implicit aliases.
                columns.append(alias)
        assignments = ', '.join(f'{quote_identifier(c)} = {quote_identifier(c)}' for c in columns)
        statements = [f'INSERT INTO {quoted} DEFAULT VALUES', f'DELETE FROM {quoted}']
        if assignments:
            statements.append(f'UPDATE {quoted} SET {assignments}')
        for statement in statements:
            try:
                session.probe('EXPLAIN ' + statement)
            except sqlite3.OperationalError as exc:
                if ('view', target) in after and 'cannot modify' in str(exc) and 'because it is a view' in str(exc):
                    continue
                raise


def sql_capabilities():
    return {
        'available': True,
        'factory': 'get_database',
        'methods': ['query', 'execute', 'executemany', 'transaction'],
        'read_scope': 'all_tables',
        'write_scope': 'shared_ext_*',
        'schema_migrations': ['declarative', 'sql'],
        'information_pragmas': sorted(INFORMATION_PRAGMAS),
        'core_writes': 'core_api_only',
        'knowledge_unit': 'extension_sql_api',
    }
