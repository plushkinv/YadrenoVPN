"""Managed synchronous SQL connections for trusted in-process extensions."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import sqlite3

from . import connection
from .extension_sql_access import SQLAccess, schema_snapshot, validate_schema


def _execute(conn, sql, parameters=()):
    # Also works with a core RuntimeConnection supplied by migration callers;
    # its cursor fence must not replace the composed extension authorizer.
    return sqlite3.Connection.cursor(conn, sqlite3.Cursor).execute(sql, parameters)


@contextmanager
def extension_connection():
    from runtime.readiness import sqlite_authorizer
    conn = sqlite3.connect(connection.DB_PATH, isolation_level=None, cached_statements=0,
                           timeout=connection.get_sqlite_busy_timeout_ms() / 1000)
    try:
        conn.row_factory = sqlite3.Row
        conn.set_authorizer(sqlite_authorizer)
        connection._apply_connection_pragmas(conn)
        yield conn
    finally:
        conn.close()


class SQLSession:
    """Internal executor; never returned by the public extension API."""

    def __init__(self, conn):
        self.conn = conn
        self.access = SQLAccess()
        self.depth = 0

    def internal(self, sql, parameters=()):
        self.conn.set_authorizer(self.access)
        self.access.trusted = True
        try:
            return _execute(self.conn, sql, parameters)
        finally:
            self.access.trusted = False

    @contextmanager
    def atomic(self):
        if self.depth and not self.conn.in_transaction:
            raise sqlite3.OperationalError('the SQL transaction has been rolled back')
        self.depth += 1
        name = f'extension_sql_{self.depth}'
        self.internal(f'SAVEPOINT {name}')
        try:
            yield
            self.internal(f'RELEASE {name}')
        except BaseException:
            if self.conn.in_transaction:
                self.internal(f'ROLLBACK TO {name}')
                self.internal(f'RELEASE {name}')
            raise
        finally:
            self.depth -= 1

    def probe(self, sql):
        self.access.probing = True
        self.conn.set_authorizer(self.access)
        try:
            _execute(self.conn, sql).fetchall()
        finally:
            self.access.probing = False

    def run(self, sql, parameters=(), *, many=False, query=False, batch_item=False):
        if not isinstance(sql, str) or not sql.strip():
            raise ValueError('SQL must be a nonempty single statement')
        if many:
            with self.atomic():
                changed = 0
                for values in parameters:
                    # Parameter generators may themselves query this handle.
                    # Finish authorization/schema checks before resuming them.
                    result = self.run(sql, values, batch_item=True)
                    changed += max(0, result['rowcount'])
                return {'rowcount': changed, 'lastrowid': None}
        with self.atomic():
            before = schema_snapshot(self.internal)
            self.access.objects = dict(before)
            self.access.schema_changed = False
            self.access.read_only = query
            self.access.error = None
            # Reset even on supplied core connections with a statement cache.
            # Each call observes the current policy and runtime activation.
            self.conn.set_authorizer(self.access)
            try:
                cursor = _execute(self.conn, sql, parameters)
                try:
                    if batch_item and cursor.description:
                        raise ValueError('executemany requires statements without returned rows')
                    rows = [dict(row) for row in cursor.fetchall()] if cursor.description else []
                    result = {'rowcount': cursor.rowcount, 'lastrowid': cursor.lastrowid}
                finally:
                    cursor.close()
            except sqlite3.Error:
                if self.access.error is not None:
                    raise self.access.error from None
                raise
            finally:
                self.access.read_only = False
            if self.access.schema_changed:
                validate_schema(self, before)
            return rows if query else result


@contextmanager
def sql_session(conn):
    from runtime.readiness import sqlite_authorizer
    try:
        yield SQLSession(conn)
    finally:
        conn.set_authorizer(sqlite_authorizer)


class ExtensionDatabase:
    """Reusable handle; only an explicit transaction retains its connection."""

    def __init__(self):
        self._current = ContextVar('extension_database_transaction', default=None)

    def _session(self):
        from runtime.readiness import _owner
        current = self._current.get()
        if current is not None and current[0] != _owner():
            raise RuntimeError('a SQL transaction cannot be shared between tasks or threads')
        return current[1] if current else None

    @contextmanager
    def transaction(self):
        """Commit on exit; rollback on exceptions. Nested scopes use savepoints."""
        from runtime.readiness import _owner
        current = self._session()
        if current is not None:
            with current.atomic():
                yield self
            return
        with extension_connection() as conn, sql_session(conn) as session:
            token = self._current.set((_owner(), session))
            try:
                with session.atomic():
                    yield self
            finally:
                self._current.reset(token)

    def _run(self, sql, parameters, **options):
        with self.transaction():
            return self._session().run(sql, parameters, **options)

    def query(self, sql: str, parameters=()) -> list[dict]:
        """Run one read-only statement and return detached dictionaries."""
        return self._run(sql, parameters, query=True)

    def execute(self, sql: str, parameters=()) -> dict:
        """Run one statement; return SQLite rowcount and lastrowid."""
        return self._run(sql, parameters)

    def executemany(self, sql: str, parameters) -> dict:
        """Execute parameter sets atomically; lastrowid is None for a batch."""
        return self._run(sql, parameters, many=True)
