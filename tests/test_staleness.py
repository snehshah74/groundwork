"""An initiative's brief goes out of date when evidence or the spec changes after an agent or tracker last got it."""
from app import db
from conftest import signup
from test_linear import make_initiative

OLD, SENT = "2025-12-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"


def prepare(owner):
    """A dispatched initiative whose brief was sent on SENT and whose content is all older than that."""
    iid = make_initiative(owner)
    owner.post(f"/api/initiatives/{iid}/dispatch", json={"target": "claude-code"})
    ws = owner.get("/api/auth/me").json()["workspace"]["id"]
    with db.conn(f"ws_{ws}") as c:
        c.execute("UPDATE dispatches SET created_at=?", (SENT,))
        c.execute("UPDATE initiatives SET updated_at=?", (OLD,))
        c.execute("UPDATE evidence SET assigned_at=?", (OLD,))
        c.execute("UPDATE problems SET evidence_changed_at=?", (OLD,))
    return iid


def ctx(owner, iid):
    return owner.get(f"/api/initiatives/{iid}").json()["context"]


def test_never_sent_is_not_stale(client):
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    assert ctx(owner, iid) == {"sent_at": None, "stale": False, "reasons": []}


def test_just_sent_is_not_stale(client):
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    owner.post(f"/api/initiatives/{iid}/dispatch", json={"target": "claude-code"})
    c = ctx(owner, iid)
    assert c["sent_at"] and c["stale"] is False


def test_new_evidence_on_the_problem_makes_it_stale(client):
    owner = signup(client, "o@x.com")
    iid = prepare(owner)
    assert ctx(owner, iid)["stale"] is False
    pid = owner.get(f"/api/initiatives/{iid}").json()["problem_id"]
    eid = owner.post("/api/evidence", json={"text": "Another customer hit the export problem", "source": "support"}).json()["id"]
    assert ctx(owner, iid)["stale"] is False                          # not about this problem yet
    assert owner.put(f"/api/evidence/{eid}/problem", json={"problem_id": pid}).status_code == 200
    c = ctx(owner, iid)
    assert c["stale"] is True and c["reasons"] == ["1 new piece of evidence"]
    assert owner.get("/api/initiatives").json()[0]["context"]["stale"] is True            # the board sees it too


def test_removed_or_moved_evidence_makes_it_stale(client):
    owner = signup(client, "o@x.com")
    iid = prepare(owner)
    ev = owner.get(f"/api/initiatives/{iid}").json()["evidence"]
    assert owner.delete(f"/api/evidence/{ev[0]['id']}").status_code == 200
    assert ctx(owner, iid)["reasons"] == ["evidence was removed or moved"]


def test_editing_the_spec_or_stories_makes_it_stale_but_a_status_change_does_not(client):
    owner = signup(client, "o@x.com")
    iid = prepare(owner)
    owner.patch(f"/api/initiatives/{iid}", json={"status": "approved"})
    assert ctx(owner, iid)["stale"] is False
    owner.patch(f"/api/initiatives/{iid}", json={"success_metric": "Cut tickets 60% in 4 weeks"})
    assert ctx(owner, iid)["reasons"] == ["the spec or stories were edited"]
    # same result when only a story is edited
    iid2 = prepare(owner)
    sid = owner.get(f"/api/initiatives/{iid2}").json()["stories"][0]["id"]
    owner.patch(f"/api/stories/{sid}", json={"title": "As an admin, I can export all reports"})
    assert ctx(owner, iid2)["stale"] is True


def test_sending_it_again_clears_the_flag_and_both_reasons_can_apply(client):
    owner = signup(client, "o@x.com")
    iid = prepare(owner)
    pid = owner.get(f"/api/initiatives/{iid}").json()["problem_id"]
    eid = owner.post("/api/evidence", json={"text": "More feedback", "source": "note"}).json()["id"]
    owner.put(f"/api/evidence/{eid}/problem", json={"problem_id": pid})
    owner.patch(f"/api/initiatives/{iid}", json={"persona": "Finance admins at multi-entity accounts"})
    assert ctx(owner, iid)["reasons"] == ["1 new piece of evidence", "the spec or stories were edited"]
    owner.post(f"/api/initiatives/{iid}/dispatch", json={"target": "claude-code"})        # the agent reads it again
    assert ctx(owner, iid)["stale"] is False


def test_home_nudges_about_stale_initiatives(client):
    owner = signup(client, "o@x.com")
    iid = prepare(owner)
    owner.patch(f"/api/initiatives/{iid}", json={"persona": "Someone new"})
    insights = owner.get("/api/home").json()["insights"]
    hit = next(i for i in insights if "changed since" in i["title"])
    assert hit["level"] == "attention" and hit["target"] == {"initiative_id": iid, "tab": "handoff"} and "spec" in hit["detail"]


def test_evidence_can_be_moved_between_problems_and_unassigned(client):
    owner = signup(client, "o@x.com")
    make_initiative(owner)
    probs = owner.get("/api/problems").json()
    eid = owner.get(f"/api/problems/{probs[0]['id']}").json()["evidence"][0]["id"]
    assert owner.put(f"/api/evidence/{eid}/problem", json={"problem_id": probs[1]["id"]}).json()["problem_id"] == probs[1]["id"]
    assert owner.put(f"/api/evidence/{eid}/problem", json={"problem_id": None}).json()["problem_id"] is None
    assert owner.put(f"/api/evidence/{eid}/problem", json={"problem_id": 99999}).status_code == 404
    assert owner.put("/api/evidence/99999/problem", json={"problem_id": probs[0]["id"]}).status_code == 404
