"""Tests run against a real Postgres. Set TEST_DATABASE_URL (it is wiped: every ws_* schema and the shared tables)."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
URL = os.environ.get("TEST_DATABASE_URL")
if not URL:
    pytest.exit("Set TEST_DATABASE_URL to a throwaway Postgres database to run the tests.", returncode=2)
os.environ["DATABASE_URL"] = URL
os.environ.setdefault("GROUNDWORK_SECRET_KEY", "test-secret-key-for-fernet-derivation")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, core, db  # noqa: E402


def _wipe():
    with db.conn("public") as c:
        for r in c.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'ws\\_%'").fetchall():
            c.execute(f"DROP SCHEMA {r['schema_name']} CASCADE")
        c.execute("DROP SCHEMA IF EXISTS gw CASCADE")
        c.execute("DROP TABLE IF EXISTS public.webhook_routes, public.invites, public.members, public.workspaces, public.sessions, public.users CASCADE")  # left by early dev builds
    auth._shared_ready = False
    core._ready.clear()


@pytest.fixture()
def client():
    _wipe()
    auth.init_shared()
    from app import main
    main._failures.clear()
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(scope="session", autouse=True)
def _close_pool():
    yield
    db.close()


def signup(client, email, password="password-1x", name=""):
    """A second browser: its own cookie jar."""
    c = TestClient(client.app)
    r = c.post("/api/auth/signup", json={"email": email, "password": password, "name": name})
    assert r.status_code == 200, r.text
    return c
