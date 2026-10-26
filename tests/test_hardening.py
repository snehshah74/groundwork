"""Rate limits in the database, cross-site request checks, audit log, export, account deletion, health."""

import jwt
import time
from fastapi.testclient import TestClient

from app import auth, db, main
from conftest import signup


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["schema_version"] >= 1


def test_login_throttle_lives_in_the_database_not_in_memory(client):
    signup(client, "a@x.com")
    for _ in range(8):
        assert client.post("/api/auth/login", json={"email": "a@x.com", "password": "wrong-pass"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "a@x.com", "password": "password-1x"}).status_code == 429   # even the right password
    assert db.one("SELECT count(*) AS n FROM rate_events WHERE key LIKE 'login:%'")["n"] == 8
    # a different server process sees the same counters: nothing is kept in this one's memory
    assert not hasattr(main, "_failures")
    # another account from the same address is not locked out by the first
    other = signup(client, "b@x.com")
    assert other.post("/api/auth/login", json={"email": "b@x.com", "password": "password-1x"}).status_code == 200


def test_successful_login_clears_the_failure_count(client):
    signup(client, "a@x.com")
    for _ in range(5):
        client.post("/api/auth/login", json={"email": "a@x.com", "password": "wrong-pass"})
    assert client.post("/api/auth/login", json={"email": "a@x.com", "password": "password-1x"}).status_code == 200
    assert db.one("SELECT count(*) AS n FROM rate_events WHERE key LIKE 'login:%'")["n"] == 0


def test_sign_in_exchange_only_counts_failures(client, monkeypatch):
    monkeypatch.setattr(auth, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(auth, "SUPABASE_ANON_KEY", "anon")
    monkeypatch.setattr(auth, "SUPABASE_JWT_SECRET", "s" * 40)
    good = jwt.encode({"sub": "u1", "email": "a@x.com", "aud": "authenticated", "exp": time.time() + 600}, "s" * 40, algorithm="HS256")
    for _ in range(12):                                               # an office behind one address signing in all morning
        assert client.post("/api/auth/exchange", json={"access_token": good}).status_code == 200
    for _ in range(8):
        assert client.post("/api/auth/exchange", json={"access_token": "garbage"}).status_code == 401
    assert client.post("/api/auth/exchange", json={"access_token": good}).status_code == 429


def test_cross_site_writes_are_refused_but_same_site_and_non_browser_clients_work(client):
    owner = signup(client, "o@x.com")
    evil = {"origin": "https://evil.example"}
    assert owner.post("/api/evidence", json={"text": "x"}, headers=evil).status_code == 403
    assert owner.post("/api/auth/workspaces", json={"name": "x"}, headers=evil).status_code == 403
    assert owner.post("/api/auth/logout", headers=evil).status_code == 403
    assert owner.get("/api/status", headers=evil).status_code == 200                      # reads are not state-changing
    assert owner.post("/api/evidence", json={"text": "ok"}, headers={"origin": "http://testserver"}).status_code == 200
    assert owner.post("/api/evidence", json={"text": "curl"}).status_code == 200            # no Origin header: not a browser
    assert owner.post("/api/evidence", json={"text": "null"}, headers={"origin": "null"}).status_code == 403


def test_audit_log_records_who_did_what(client):
    owner, guest = signup(client, "o@x.com", name="Olly"), signup(client, "g@x.com")
    inv = owner.post("/api/team/invites", json={"email": "g@x.com", "role": "member"}).json()
    guest.post("/api/auth/invites/accept", json={"token": inv["link"].rsplit("/", 1)[1]})
    uid = next(m["user_id"] for m in owner.get("/api/team").json()["members"] if m["email"] == "g@x.com")
    owner.patch(f"/api/team/members/{uid}", json={"role": "viewer"})
    owner.delete(f"/api/team/members/{uid}")
    log = owner.get("/api/team/audit").json()
    actions = [e["action"] for e in log]
    assert actions[:4] == ["member.remove", "member.role", "invite.accept", "member.invite"]    # newest first
    assert log[0]["email"] == "o@x.com" and "g@x.com" in log[0]["detail"]
    assert "member -> viewer" in log[1]["detail"]


def test_audit_log_is_for_admins_only(client):
    owner, member = signup(client, "o@x.com"), signup(client, "m@x.com")
    tok = owner.post("/api/team/invites", json={"email": "m@x.com", "role": "member"}).json()["link"].rsplit("/", 1)[1]
    member.post("/api/auth/invites/accept", json={"token": tok})
    assert member.get("/api/team/audit").status_code == 403
    assert TestClient(client.app).get("/api/team/audit").status_code == 401


def test_export_has_the_data_and_none_of_the_secrets(client):
    owner, member = signup(client, "o@x.com"), signup(client, "m@x.com")
    owner.post("/api/seed")
    owner.post("/api/analyze", json={})
    tok = owner.post("/api/team/invites", json={"email": "m@x.com", "role": "member"}).json()["link"].rsplit("/", 1)[1]
    member.post("/api/auth/invites/accept", json={"token": tok})
    assert member.get("/api/team/export").status_code == 403
    r = owner.get("/api/team/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    data = r.json()
    assert len(data["evidence"]) == 24 and data["problems"] and data["product"]["name"] == "Ledgerly"
    assert "connections" not in data and "api_token" not in r.text and "secret" not in r.text.lower()
    assert any(e["action"] == "workspace.export" for e in owner.get("/api/team/audit").json())


def test_delete_account_removes_sole_workspaces_and_keeps_shared_ones(client):
    owner, other = signup(client, "o@x.com"), signup(client, "x@x.com")
    owner.post("/api/seed")
    ws_owner = owner.get("/api/auth/me").json()["workspace"]["id"]
    # the other user's own workspace, plus a shared one they co-own with `owner` (creating it also makes it their active workspace)
    other.post("/api/auth/workspaces", json={"name": "Shared"})
    tok = other.post("/api/team/invites", json={"email": "o@x.com", "role": "admin"}).json()["link"].rsplit("/", 1)[1]
    owner.post("/api/auth/invites/accept", json={"token": tok})
    # the sole owner of a shared workspace must hand it over first
    r = other.request("DELETE", "/api/auth/account", json={"confirm": "x@x.com"})
    assert r.status_code == 400 and "Shared" in r.json()["detail"]
    uid_owner = next(m["user_id"] for m in other.get("/api/team").json()["members"] if m["email"] == "o@x.com")
    other.patch(f"/api/team/members/{uid_owner}", json={"role": "owner"})
    assert owner.request("DELETE", "/api/auth/account", json={"confirm": "wrong@x.com"}).status_code == 400
    assert owner.request("DELETE", "/api/auth/account", json={"confirm": "o@x.com"}).status_code == 200
    assert owner.get("/api/status").status_code == 401                                    # session gone
    assert client.post("/api/auth/login", json={"email": "o@x.com", "password": "password-1x"}).status_code == 401
    assert db.one("SELECT 1 AS x FROM information_schema.schemata WHERE schema_name = ?", (f"ws_{ws_owner}",)) is None   # their private workspace is gone
    assert other.get("/api/status").status_code == 200                                    # shared workspace and the other user live on
    assert [m["email"] for m in other.get("/api/team").json()["members"]] == ["x@x.com"]
