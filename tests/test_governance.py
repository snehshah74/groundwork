"""Governance: what agents read and changed, set against the spec and the outcome."""
from app import db
from conftest import signup
from test_linear import make_initiative
from test_mcp import load_mcp, token_of

LONG_AGO = "2020-01-01T00:00:00.000+00:00"


def setup(client, monkeypatch):
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    return owner, iid, load_mcp(monkeypatch, token_of(owner))


def flags(owner, iid):
    item = next(i for i in owner.get("/api/governance").json()["items"] if i["id"] == iid)
    return {f["code"] for f in item["flags"]}


def test_agent_reads_and_writes_are_recorded_with_who_and_what(client, monkeypatch):
    owner, iid, m = setup(client, monkeypatch)
    m.get_initiative_context(iid)
    m.get_evidence(iid)
    m.update_status(iid, "in_progress")
    m.report_outcome(iid, "worked", "37% fewer tickets")
    events = owner.get(f"/api/initiatives/{iid}/activity").json()
    agent = [e for e in events if e["kind"] == "agent"]
    assert [e["text"] for e in agent][:4] == ["recorded an outcome: worked (37% fewer tickets)", "changed status to in_progress",
                                              "read the customer evidence", "read the build brief"]          # newest first
    assert all(e["who"] == "o@x.com" for e in agent)
    assert any(e["kind"] == "dispatch" for e in events)                  # the brief read is also a delivery


def test_denied_attempts_by_a_view_only_agent_are_recorded_too(client, monkeypatch):
    owner, iid, _ = setup(client, monkeypatch)
    viewer = signup(client, "v@x.com")
    tok = owner.post("/api/team/invites", json={"email": "v@x.com", "role": "viewer"}).json()["link"].rsplit("/", 1)[1]
    viewer.post("/api/auth/invites/accept", json={"token": tok})
    m = load_mcp(monkeypatch, token_of(viewer))
    assert "view-only" in m.update_status(iid, "shipped")
    ev = [e for e in owner.get(f"/api/initiatives/{iid}/activity").json() if e["kind"] == "agent"]
    assert ev[0]["text"].startswith("tried to change status but is view-only") and ev[0]["who"] == "v@x.com"
    assert owner.get(f"/api/initiatives/{iid}").json()["status"] != "shipped"


def test_sent_without_stories_and_without_criteria_are_flagged(client, monkeypatch):
    owner, iid, m = setup(client, monkeypatch)
    probs = owner.get("/api/problems").json()
    bare = owner.post("/api/initiatives", json={"problem_id": probs[1]["id"]}).json()["id"]
    owner.patch(f"/api/initiatives/{bare}", json={"status": "approved", "acceptance_criteria": []})
    assert "no_criteria" in flags(owner, bare) and "no_stories" not in flags(owner, bare)        # not sent yet: only the criteria gap
    m.get_initiative_context(bare)
    assert {"no_stories", "no_criteria"} <= flags(owner, bare)
    assert "no_stories" not in flags(owner, iid)                                                  # this one has a story map


def test_out_of_date_while_building_is_flagged_and_clears_when_resent(client, monkeypatch):
    owner, iid, m = setup(client, monkeypatch)
    owner.patch(f"/api/initiatives/{iid}", json={"status": "in_progress"})
    m.get_initiative_context(iid)
    assert "stale" not in flags(owner, iid)
    owner.patch(f"/api/initiatives/{iid}", json={"persona": "A different audience"})
    assert "stale" in flags(owner, iid)
    m.get_initiative_context(iid)
    assert "stale" not in flags(owner, iid)


def test_shipped_without_a_real_measurement_is_flagged(client, monkeypatch):
    owner, iid, m = setup(client, monkeypatch)
    owner.patch(f"/api/initiatives/{iid}", json={"status": "shipped"})
    assert "no_outcome" in flags(owner, iid)
    m.report_outcome(iid, "too_early")
    assert "no_outcome" in flags(owner, iid)                             # "too early" is not a measurement
    m.report_outcome(iid, "worked", "-30% tickets")
    assert "no_outcome" not in flags(owner, iid)


def test_in_progress_work_with_no_recent_activity_is_stalled(client, monkeypatch):
    owner, iid, m = setup(client, monkeypatch)
    owner.patch(f"/api/initiatives/{iid}", json={"status": "in_progress"})
    m.get_initiative_context(iid)
    assert "stalled" not in flags(owner, iid)
    ws = owner.get("/api/auth/me").json()["workspace"]["id"]
    with db.conn(f"ws_{ws}") as c:
        c.execute("UPDATE dispatches SET created_at=?", (LONG_AGO,))
        c.execute("UPDATE agent_events SET created_at=?", (LONG_AGO,))
        c.execute("UPDATE initiatives SET updated_at=?", (LONG_AGO,))
    assert "stalled" in flags(owner, iid)


def test_summary_counts_and_flagged_items_come_first(client, monkeypatch):
    owner, iid, m = setup(client, monkeypatch)
    probs = owner.get("/api/problems").json()
    clean = owner.post("/api/initiatives", json={"problem_id": probs[1]["id"]}).json()["id"]
    owner.post(f"/api/initiatives/{clean}/stories", json={"title": "As a user, I can do it", "acceptance": ["it works"]})
    owner.patch(f"/api/initiatives/{iid}", json={"status": "shipped"})
    g = owner.get("/api/governance").json()
    assert g["summary"]["no_outcome"] == 1 and g["summary"]["flagged"] >= 1
    assert g["items"][0]["id"] == iid and g["items"][0]["flags"]       # the flagged one leads
    first = g["items"][0]
    assert {"title", "status", "sent_to", "agent_actions", "stories", "criteria", "context", "verdict"} <= set(first)


def test_viewers_can_read_governance_and_it_is_per_workspace(client, monkeypatch):
    owner, iid, _ = setup(client, monkeypatch)
    other = signup(client, "z@x.com")
    assert other.get("/api/governance").json()["items"] == []
    assert owner.get("/api/governance").status_code == 200
