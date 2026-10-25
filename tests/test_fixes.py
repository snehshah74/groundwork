"""Regressions for bugs found while building the integrations."""
import json

from app import ai, db
from app.integrations import linear
from app.integrations.base import next_status
from conftest import signup
from test_linear import GOOD_KEY, FakeLinear, make_initiative

import httpx


def test_tracker_state_never_moves_an_initiative_backwards():
    assert next_status("proposed", "started") == "in_progress"
    assert next_status("in_progress", "backlog") is None            # a fresh push sits in Backlog: must not undo "in progress"
    assert next_status("in_progress", "unstarted") is None
    assert next_status("shipped", "started") is None                 # reopened work does not un-ship it
    assert next_status("measured", "completed") is None
    assert next_status("in_progress", "completed") == "shipped"
    assert next_status("proposed", "canceled") is None
    assert next_status("approved", "approved-ish") is None


def test_sync_right_after_push_keeps_in_progress(client, monkeypatch):
    f = FakeLinear()
    monkeypatch.setattr(linear, "_transport", httpx.MockTransport(f.handler))
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    owner.post("/api/integrations/linear/connect", json={"api_key": GOOD_KEY})
    owner.post(f"/api/initiatives/{iid}/push/linear")
    assert owner.get(f"/api/initiatives/{iid}").json()["status"] == "in_progress"
    assert owner.post("/api/integrations/linear/sync").json()["changed"] == 0
    assert owner.get(f"/api/initiatives/{iid}").json()["status"] == "in_progress"


def test_too_early_outcome_does_not_mark_measured(client):
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    owner.patch(f"/api/initiatives/{iid}", json={"status": "shipped"})
    owner.post(f"/api/initiatives/{iid}/outcome", json={"verdict": "too_early", "note": "launched yesterday"})
    assert owner.get(f"/api/initiatives/{iid}").json()["status"] == "shipped"
    stats = owner.get("/api/status").json()
    assert stats["measured"] == 0                                     # "too early to tell" is not a measurement
    titles = [i["title"] for i in owner.get("/api/home").json()["insights"]]
    assert any("never measured" in t for t in titles)                 # and the nudge to measure stays
    owner.post(f"/api/initiatives/{iid}/outcome", json={"verdict": "worked", "metric_value": "-30% tickets"})
    assert owner.get(f"/api/initiatives/{iid}").json()["status"] == "measured"
    assert owner.get("/api/status").json()["measured"] == 1


def test_clustering_only_consumes_what_it_sent(client, monkeypatch):
    owner = signup(client, "o@x.com")
    owner.get("/api/status")
    db.use_schema("ws_1")
    from app import core
    for n in range(260):
        core.add_evidence(f"customer said thing {n}", "note")
    db.use_schema(None)
    sent = {}

    def fake_ask(system, user, max_tokens=4000):
        ids = [e["id"] for e in json.loads(user)]
        sent["n"] = len(ids)
        return [{"title": "Everything", "summary": "s", "persona": "p", "severity": "high", "evidence_ids": ids}]

    monkeypatch.setattr(ai, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai, "_ask", fake_ask)
    r = owner.post("/api/analyze", json={}).json()
    assert sent["n"] == ai.MAX_CLUSTER_ITEMS == 250
    assert r["evidence_analyzed"] == 250 and r["remaining"] == 10     # the other 10 are left to group, not dumped in "Other feedback"
    assert [p["title"] for p in owner.get("/api/problems").json()] == ["Everything"]
    assert owner.get("/api/status").json()["grouped"] == 250
    r2 = owner.post("/api/analyze", json={}).json()                   # second run picks up the rest
    assert r2["evidence_analyzed"] == 10 and r2["remaining"] == 0
