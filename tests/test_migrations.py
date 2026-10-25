import pytest

from app import core, db, migrate
from conftest import signup


def test_new_workspace_is_at_latest_version(client):
    signup(client, "a@x.com").get("/api/status")
    assert db.schema_version("ws_1") == max(v for v, *_ in core.MIGRATIONS)
    assert db.schema_version("gw") >= 1


def test_adopts_a_schema_created_before_versioning(client):
    """Workspaces made by the first Postgres release have tables but no schema_version."""
    with db.conn("public") as c:
        c.execute("CREATE SCHEMA ws_77")
    with db.conn("ws_77") as c:
        c.execute(core.SCHEMA)
        c.execute("INSERT INTO evidence(source, text, created_at) VALUES ('note', 'kept', 'now')")
    assert db.schema_version("ws_77") == 0
    core.init_workspace("ws_77")
    assert db.schema_version("ws_77") == max(v for v, *_ in core.MIGRATIONS)
    db.use_schema("ws_77")
    assert core.list_evidence()[0]["text"] == "kept"          # data untouched
    db.use_schema(None)


def test_new_migration_applies_once_and_keeps_data(client, monkeypatch):
    signup(client, "a@x.com").post("/api/seed")
    latest = max(v for v, *_ in core.MIGRATIONS)
    monkeypatch.setattr(core, "MIGRATIONS", core.MIGRATIONS + [(latest + 1, "add lang", "ALTER TABLE evidence ADD COLUMN lang TEXT")])
    core._ready.discard("ws_1")
    assert core.init_workspace("ws_1") == latest + 1
    with db.conn("ws_1") as c:
        assert c.execute("SELECT count(*) AS n FROM evidence WHERE lang IS NULL").fetchone()["n"] == 24
    core._ready.discard("ws_1")
    assert core.init_workspace("ws_1") == latest + 1             # running again is a no-op, not a "column exists" error


def test_failed_migration_rolls_back_completely(client, monkeypatch):
    signup(client, "a@x.com").get("/api/status")
    latest = max(v for v, *_ in core.MIGRATIONS)
    bad = (latest + 1, "half applied", "ALTER TABLE evidence ADD COLUMN half TEXT; SELECT * FROM table_that_does_not_exist")
    monkeypatch.setattr(core, "MIGRATIONS", core.MIGRATIONS + [bad])
    core._ready.discard("ws_1")
    with pytest.raises(Exception):
        core.init_workspace("ws_1")
    assert db.schema_version("ws_1") == latest
    with db.conn("ws_1") as c:
        cols = [r["column_name"] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='ws_1' AND table_name='evidence'").fetchall()]
    assert "half" not in cols                                      # DDL is transactional in Postgres


def test_cli_upgrades_every_workspace(client, monkeypatch, capsys):
    signup(client, "a@x.com").get("/api/status")
    signup(client, "b@x.com").get("/api/status")
    latest = max(v for v, *_ in core.MIGRATIONS)
    monkeypatch.setattr(core, "MIGRATIONS", core.MIGRATIONS + [(latest + 1, "noop", "SELECT 1")])
    core._ready.clear()
    assert migrate.main() == 0
    out = capsys.readouterr().out
    assert f"ws_1: {latest} -> {latest + 1}" in out and f"ws_2: {latest} -> {latest + 1}" in out and "2 workspace(s) upgraded" in out
