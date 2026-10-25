"""Postgres access (Supabase or any Postgres).

Shared tables (users, workspaces, members...) live in the private `gw` schema. Every workspace is its own schema,
`ws_<id>`, holding the product data, so one workspace can never read another's rows and the
workspace queries stay free of tenant filters.

Neither is `public`: Supabase publishes everything in `public` through its REST API, and these tables hold password
hashes and agent tokens. The API roles are also revoked from our schemas (see lock_down).

Set DATABASE_URL to the Supabase connection string. The transaction pooler (port 6543) works.
"""
from __future__ import annotations

import contextvars
import os
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get("DATABASE_URL", "")
SHARED = "gw"

_pool: ConnectionPool | None = None
_schema: contextvars.ContextVar[str | None] = contextvars.ContextVar("groundwork_schema", default=None)


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not set. Point it at your Supabase (or other Postgres) connection string.")
        _pool = ConnectionPool(
            DATABASE_URL, min_size=1, max_size=int(os.environ.get("GROUNDWORK_DB_POOL", "10")),
            kwargs={"row_factory": dict_row, "prepare_threshold": None},  # no prepared statements: pgbouncer-safe
            open=True,
        )
    return _pool


def schema_name(workspace_id: int) -> str:
    return f"ws_{int(workspace_id)}"


def use_schema(schema: str | None) -> None:
    """Point the current request at one workspace. Only names we generated are accepted."""
    if schema is not None and not (schema.startswith("ws_") and schema[3:].isdigit()):
        raise ValueError("bad schema name")
    _schema.set(schema)


def current_schema() -> str | None:
    return _schema.get()


class _Conn:
    """Thin wrapper so SQL can keep using ? placeholders."""

    def __init__(self, c):
        self._c = c

    def execute(self, sql: str, args=None):
        if args:
            return self._c.execute(sql.replace("?", "%s"), args)
        return self._c.execute(sql)


@contextmanager
def conn(schema: str | None = None):
    """One transaction. Workspace tables resolve through search_path; `public` is always visible."""
    schema = schema or _schema.get()
    with _get_pool().connection() as c:
        c.execute(f"SET LOCAL search_path TO {schema}, {SHARED}" if schema and schema != SHARED else f"SET LOCAL search_path TO {SHARED}")
        yield _Conn(c)


def lock_down(c, schema: str) -> None:
    """On Supabase, take the anon and authenticated API roles away from a schema. A no-op on plain Postgres."""
    c.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN "
        f"EXECUTE 'REVOKE ALL ON SCHEMA {schema} FROM anon, authenticated'; "
        f"EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA {schema} FROM anon, authenticated'; "
        f"EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA {schema} FROM anon, authenticated'; "
        "END IF; END $$"
    )


def migrate(schema: str, migrations: list[tuple[int, str, str]]) -> int:
    """Apply pending (version, description, sql) migrations to one schema, all in one transaction.

    A schema created before versioning existed has no schema_version row, so it counts as version 0 and the
    baseline migration (written with IF NOT EXISTS) adopts it. A failure rolls everything back and re-raises.
    Returns the version the schema is now at.
    """
    with conn(schema) as c:
        c.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (schema,))   # two workers must not migrate one schema at once
        c.execute(f"CREATE TABLE IF NOT EXISTS {schema}.schema_version (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL)")
        row = c.execute(f"SELECT version FROM {schema}.schema_version").fetchone()
        current = row["version"] if row else 0
        for version, _description, sql in sorted(migrations):
            if version > current:
                c.execute(sql)
                current = version
        c.execute(f"INSERT INTO {schema}.schema_version(id, version) VALUES (1, ?) ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version", (current,))
    return current


def schema_version(schema: str) -> int:
    """Current migration version of a schema; 0 when it has none yet (never migrated, or created before versioning)."""
    if not one("SELECT to_regclass(?) AS t", (f"{schema}.schema_version",))["t"]:
        return 0
    row = one(f"SELECT version FROM {schema}.schema_version")
    return row["version"] if row else 0


def rows(sql: str, args: tuple = ()) -> list[dict]:
    with conn() as c:
        return c.execute(sql, args).fetchall()


def one(sql: str, args: tuple = ()) -> dict | None:
    with conn() as c:
        return c.execute(sql, args).fetchone()


def close() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
