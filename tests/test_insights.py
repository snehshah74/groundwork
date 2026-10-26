"""The insights digest: findings written from a workspace snapshot, cited evidence validated, runnable on demand or weekly."""
import json
import re
from types import SimpleNamespace

from app import ai, core, db, insights
from conftest import signup
from test_usage import FakeAnthropic


def seeded(owner):
    owner.post("/api/seed")
    owner.post("/api/analyze", json={})
    return owner


def claude_returns(monkeypatch, reply):
    fake = FakeAnthropic(reply=reply)
    monkeypatch.setattr(ai, "_client", lambda: fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    return fake


def test_offline_digest_is_built_from_the_rule_based_findings_and_replaces_the_last_run(client):
    owner = seeded(signup(client, "o@x.com"))
    assert owner.get("/api/insights").json() == {"run": None, "items": []}
    first = owner.post("/api/insights/generate").json()
    assert first["run"]["mode"] == "offline" and 1 <= len(first["items"]) <= 5
    assert all({"id", "title", "detail", "kind"} <= set(i) for i in first["items"])
    assert owner.get("/api/insights").json()["items"] == first["items"]
    second = owner.post("/api/insights/generate").json()
    assert {i["id"] for i in second["items"]}.isdisjoint({i["id"] for i in first["items"]})          # a new run, not a pile-up
    assert owner.get("/api/insights").json()["run"]["id"] == second["run"]["id"]


def test_items_can_be_dismissed(client):
    owner = seeded(signup(client, "o@x.com"))
    items = owner.post("/api/insights/generate").json()["items"]
    assert owner.post(f"/api/insights/{items[0]['id']}/dismiss").status_code == 200
    assert items[0]["id"] not in [i["id"] for i in owner.get("/api/insights").json()["items"]]
    assert owner.post("/api/insights/99999/dismiss").status_code == 404


def test_claude_findings_are_validated_trimmed_and_capped(client, monkeypatch):
    owner = seeded(signup(client, "o@x.com"))
    ws = owner.get("/api/auth/me").json()["workspace"]["id"]
    db.use_schema(f"ws_{ws}")
    valid = sorted({int(x) for x in re.findall(r"evidence #(\d+)", core.workspace_digest())})
    db.use_schema(None)
    assert valid
    reply = [{"title": "  Export is the loudest problem  ", "detail": "Seven customers across three sources.", "kind": "opportunity", "evidence_ids": [valid[0], 987654]},
             {"title": "", "detail": "no title: dropped", "kind": "risk", "evidence_ids": []},
             {"title": "Odd kind", "detail": "kind is normalized", "kind": "banana", "evidence_ids": []}] + \
            [{"title": f"Extra {n}", "detail": "d", "kind": "gap", "evidence_ids": []} for n in range(8)]
    fake = claude_returns(monkeypatch, reply)
    out = owner.post("/api/insights/generate").json()
    assert out["run"]["mode"] == "claude" and fake.calls == 1 and len(out["items"]) == 5
    first = out["items"][0]
    assert first["title"] == "Export is the loudest problem" and first["kind"] == "opportunity" and first["evidence_ids"] == [valid[0]]     # the invented id is gone
    assert out["items"][1]["title"] == "Odd kind" and out["items"][1]["kind"] == "gap"
    assert owner.get("/api/usage").json()["by_kind"] == {"insights": 1}


def test_junk_from_claude_falls_back_to_the_rule_based_digest(client, monkeypatch):
    owner = seeded(signup(client, "o@x.com"))
    claude_returns(monkeypatch, "I could not produce JSON, sorry")
    out = owner.post("/api/insights/generate").json()
    assert out["run"]["mode"] == "offline" and out["items"]


def test_credits_run_out_gracefully(client, monkeypatch):
    monkeypatch.setenv("GROUNDWORK_AI_CREDITS", "1")
    owner = seeded(signup(client, "o@x.com"))
    fake = claude_returns(monkeypatch, [{"title": "One", "detail": "d", "kind": "gap", "evidence_ids": []}])
    assert owner.post("/api/insights/generate").json()["run"]["mode"] == "claude"
    assert owner.post("/api/insights/generate").json()["run"]["mode"] == "offline" and fake.calls == 1


def test_viewers_read_but_cannot_generate_or_dismiss(client):
    owner, viewer = seeded(signup(client, "o@x.com")), signup(client, "v@x.com")
    items = owner.post("/api/insights/generate").json()["items"]
    tok = owner.post("/api/team/invites", json={"email": "v@x.com", "role": "viewer"}).json()["link"].rsplit("/", 1)[1]
    viewer.post("/api/auth/invites/accept", json={"token": tok})
    assert viewer.get("/api/insights").json()["items"] == items
    assert viewer.post("/api/insights/generate").status_code == 403 and viewer.post(f"/api/insights/{items[0]['id']}/dismiss").status_code == 403
    assert signup(client, "z@x.com").get("/api/insights").json()["items"] == []


def test_weekly_scheduled_run_only_when_switched_on_due_and_ai_is_available(client, monkeypatch):
    owner = seeded(signup(client, "o@x.com"))
    ws = owner.get("/api/auth/me").json()["workspace"]["id"]
    claude_returns(monkeypatch, [{"title": "Weekly", "detail": "d", "kind": "gap", "evidence_ids": []}])
    from app.integrations import service
    assert service.run_scheduled()["insights"] == 0                                    # off by default
    assert owner.put("/api/settings", json={"auto_insights": True}).json()["auto_insights"] is True
    assert service.run_scheduled()["insights"] == 1                                    # on, never run: due
    assert owner.get("/api/insights").json()["items"][0]["title"] == "Weekly"
    assert service.run_scheduled()["insights"] == 0                                    # ran just now: not due again
    with db.conn(f"ws_{ws}") as c:
        c.execute("UPDATE insights SET created_at='2020-01-01T00:00:00.000+00:00'")
    assert service.run_scheduled()["insights"] == 1                                    # a week later: due
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with db.conn(f"ws_{ws}") as c:
        c.execute("UPDATE insights SET created_at='2020-01-01T00:00:00.000+00:00'")
    assert service.run_scheduled()["insights"] == 0                                    # no AI: nothing to schedule
