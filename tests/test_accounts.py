import time

import jwt

from app import auth, teams
from conftest import signup


def test_pages_and_redirects(client):
    assert client.get("/").status_code == 200
    assert client.get("/api/status").status_code == 401
    r = client.get("/app", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/login"


def test_signup_login_logout(client):
    c = signup(client, "ann@x.com", name="Ann")
    me = c.get("/api/auth/me").json()
    assert me["user"]["email"] == "ann@x.com" and me["workspace"]["role"] == "owner"
    assert me["api_token"].startswith("gw_")
    assert client.post("/api/auth/login", json={"email": "ann@x.com", "password": "wrong-pass"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "ann@x.com", "password": "password-1x"}).status_code == 200
    c.post("/api/auth/logout")
    assert c.get("/api/status").status_code == 401


def test_duplicate_and_weak_passwords(client):
    signup(client, "a@x.com")
    assert client.post("/api/auth/signup", json={"email": "A@x.com", "password": "password-1x"}).status_code == 400
    assert client.post("/api/auth/signup", json={"email": "b@x.com", "password": "short"}).status_code == 400


def test_workspaces_are_isolated(client):
    a, b = signup(client, "a@x.com"), signup(client, "b@x.com")
    assert a.post("/api/seed").status_code == 200
    assert a.get("/api/status").json()["evidence"] == 24
    assert b.get("/api/status").json()["evidence"] == 0


def test_invite_flow_and_roles(client):
    owner, guest = signup(client, "o@x.com"), signup(client, "g@x.com")
    inv = owner.post("/api/team/invites", json={"email": "g@x.com", "role": "viewer"}).json()
    token = inv["link"].rsplit("/", 1)[1]
    assert guest.get(f"/api/auth/invites/{token}").json()["workspace_name"]
    # wrong email cannot accept
    other = signup(client, "x@x.com")
    assert other.post("/api/auth/invites/accept", json={"token": token}).status_code == 403
    ws = guest.post("/api/auth/invites/accept", json={"token": token}).json()
    assert ws["role"] == "viewer"
    # reused link is dead
    assert guest.post("/api/auth/invites/accept", json={"token": token}).status_code == 400
    owner.post("/api/seed")
    # viewer reads but cannot write
    assert guest.get("/api/status").json()["evidence"] == 24
    assert guest.post("/api/evidence", json={"text": "hi"}).status_code == 403
    # promote to member, now it works
    uid = next(m["user_id"] for m in owner.get("/api/team").json()["members"] if m["email"] == "g@x.com")
    assert owner.patch(f"/api/team/members/{uid}", json={"role": "member"}).status_code == 200
    assert guest.post("/api/evidence", json={"text": "hi"}).status_code == 200
    # members cannot manage the team or reset
    assert guest.post("/api/team/invites", json={"email": "z@x.com"}).status_code == 403
    assert guest.post("/api/reset").status_code == 403
    # removed member loses access
    assert owner.delete(f"/api/team/members/{uid}").status_code == 200
    assert guest.get("/api/status").status_code in (200,)  # falls back to their own workspace
    assert guest.get("/api/status").json()["evidence"] == 0


def test_last_owner_protected(client):
    o = signup(client, "o@x.com")
    uid = o.get("/api/auth/me").json()["user"]["id"]
    assert o.patch(f"/api/team/members/{uid}", json={"role": "admin"}).status_code == 400
    assert o.delete(f"/api/team/members/{uid}").status_code == 400


def test_switch_workspace_requires_membership(client):
    a, b = signup(client, "a@x.com"), signup(client, "b@x.com")
    b_ws = b.get("/api/auth/me").json()["workspace"]["id"]
    assert a.post(f"/api/auth/workspaces/{b_ws}/switch").status_code == 404
    new = a.post("/api/auth/workspaces", json={"name": "Second"}).json()
    assert a.get("/api/auth/me").json()["workspace"]["id"] == new["id"]


def test_agent_token_resolves_member(client):
    o = signup(client, "o@x.com")
    tok = o.get("/api/auth/me").json()["api_token"]
    m = teams.member_by_token(tok)
    assert m["email"] == "o@x.com" and m["role"] == "owner"
    new = o.post("/api/team/token/rotate").json()["api_token"]
    assert teams.member_by_token(tok) is None and teams.member_by_token(new)


def test_supabase_exchange(client, monkeypatch):
    monkeypatch.setattr(auth, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(auth, "SUPABASE_ANON_KEY", "anon")
    monkeypatch.setattr(auth, "SUPABASE_JWT_SECRET", "s" * 40)
    cfg = client.get("/api/config").json()
    assert cfg["mode"] == "supabase" and cfg["supabase_anon_key"] == "anon"
    # local password routes are off in supabase mode
    assert client.post("/api/auth/login", json={"email": "a@b.co", "password": "x"}).status_code == 404
    good = jwt.encode({"sub": "sb-1", "email": "Sam@X.com", "aud": "authenticated", "exp": time.time() + 600,
                       "user_metadata": {"full_name": "Sam"}}, "s" * 40, algorithm="HS256")
    r = client.post("/api/auth/exchange", json={"access_token": good})
    assert r.status_code == 200 and r.json()["user"]["email"] == "sam@x.com"
    assert client.get("/api/auth/me").json()["workspace"]["role"] == "owner"
    # same identity returns the same account
    again = client.post("/api/auth/exchange", json={"access_token": good}).json()["user"]["id"]
    assert again == r.json()["user"]["id"]
    # forged, expired and wrong-audience tokens are rejected
    for bad in (
        jwt.encode({"sub": "x", "email": "e@x.com", "aud": "authenticated", "exp": time.time() + 60}, "wrong" * 10, algorithm="HS256"),
        jwt.encode({"sub": "x", "email": "e@x.com", "aud": "authenticated", "exp": time.time() - 60}, "s" * 40, algorithm="HS256"),
        jwt.encode({"sub": "x", "email": "e@x.com", "aud": "other", "exp": time.time() + 60}, "s" * 40, algorithm="HS256"),
        "garbage",
    ):
        assert TestClientFresh(client).post("/api/auth/exchange", json={"access_token": bad}).status_code == 401


def TestClientFresh(client):
    from fastapi.testclient import TestClient
    return TestClient(client.app)


def test_supabase_api_roles_cannot_reach_our_data(client):
    """Supabase exposes `public` through a REST API reachable with the published anon key. Nothing of ours may be there or granted."""
    from app import db
    with db.conn("public") as c:
        c.execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon NOLOGIN; END IF; "
                  "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF; END $$")
    # the same lock-down runs on a fresh install (shared schema) and on each new workspace
    from app import auth, core
    auth._shared_ready = False
    auth.init_shared()
    signup(client, "a@x.com").get("/api/status")                       # first signed-in request creates the workspace schema
    with db.conn("public") as c:
        leaked = c.execute("SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema='public'").fetchall()
        assert leaked == [], leaked                                   # nothing of ours in the exposed schema
        for role in ("anon", "authenticated"):
            for tbl in ("gw.users", "gw.sessions", "gw.members", "ws_1.evidence", "ws_1.connections"):
                assert not c.execute("SELECT has_table_privilege(%s, %s, 'SELECT') AS ok" .replace("%s", "?"), (role, tbl)).fetchone()["ok"], (role, tbl)
            for sch in ("gw", "ws_1"):
                assert not c.execute("SELECT has_schema_privilege(?, ?, 'USAGE') AS ok", (role, sch)).fetchone()["ok"], (role, sch)
