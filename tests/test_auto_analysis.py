"""New evidence is grouped automatically: into a matching problem, or into new problems once enough has piled up."""
from app import ai, analysis, core, db
from conftest import signup


def problem_titled(owner, word):
    return next(p for p in owner.get("/api/problems").json() if word.lower() in p["title"].lower())


def grouped(owner, eid):
    return next(e for e in owner.get("/api/evidence").json() if e["id"] == eid)["problem_id"]


def started(owner):
    """Seeded workspace whose first analysis was done by hand, so problems already exist."""
    owner.post("/api/seed")
    owner.post("/api/analyze", json={})
    return owner


def test_new_evidence_joins_the_matching_problem(client):
    owner = started(signup(client, "o@x.com"))
    export = problem_titled(owner, "export")
    before = export["evidence_count"]
    e = owner.post("/api/evidence", json={"text": "Exporting every report as a separate CSV file is painful, I need all reports exported together", "source": "support"}).json()
    assert grouped(owner, e["id"]) == export["id"]
    assert problem_titled(owner, "export")["evidence_count"] == before + 1


def test_unrelated_evidence_is_left_alone_and_nothing_new_is_invented(client):
    owner = started(signup(client, "o@x.com"))
    n = len(owner.get("/api/problems").json())
    e = owner.post("/api/evidence", json={"text": "Bananas are yellow and the office plants need watering on Fridays", "source": "note"}).json()
    assert grouped(owner, e["id"]) is None
    assert len(owner.get("/api/problems").json()) == n


def test_an_item_that_only_shares_a_common_word_is_not_guessed_into_a_problem(client):
    owner = started(signup(client, "o@x.com"))
    e = owner.post("/api/evidence", json={"text": "Our invoice", "source": "note"}).json()      # "invoice" appears in several problems
    assert grouped(owner, e["id"]) is None


def test_it_can_be_switched_off_by_an_admin_only(client):
    owner = started(signup(client, "o@x.com"))
    member = signup(client, "m@x.com")
    tok = owner.post("/api/team/invites", json={"email": "m@x.com", "role": "member"}).json()["link"].rsplit("/", 1)[1]
    member.post("/api/auth/invites/accept", json={"token": tok})
    assert owner.get("/api/settings").json()["auto_analyze"] is True
    assert member.put("/api/settings", json={"auto_analyze": False}).status_code == 403
    assert owner.put("/api/settings", json={"auto_analyze": False}).json()["auto_analyze"] is False
    e = owner.post("/api/evidence", json={"text": "Exporting every report as a separate CSV file is painful", "source": "support"}).json()
    assert grouped(owner, e["id"]) is None                            # off means off
    owner.put("/api/settings", json={"auto_analyze": True})
    owner.post("/api/evidence", json={"text": "Another export of reports by CSV, one at a time", "source": "support"})
    assert grouped(owner, e["id"]) is not None                        # back on: the earlier item is swept up too


def test_a_pile_of_uploaded_evidence_becomes_problems_without_a_click(client):
    owner = signup(client, "o@x.com")
    rows = ["text,source"] + [f'"The invoice reminder emails are far too noisy number {n}",support' for n in range(4)] + \
           [f'"Dashboard is slow to load and freezes when filtering invoices attempt {n}",review' for n in range(4)]
    r = owner.post("/api/evidence/upload", files={"file": ("feedback.csv", "\n".join(rows), "text/csv")})
    assert r.json()["added"] == 8
    probs = owner.get("/api/problems").json()
    assert len(probs) >= 2 and owner.get("/api/status").json()["grouped"] == 8


def test_a_few_items_with_no_problems_yet_wait(client):
    owner = signup(client, "o@x.com")
    owner.post("/api/evidence", json={"text": "One lonely customer comment", "source": "note"})
    owner.post("/api/evidence", json={"text": "Another lonely customer comment", "source": "note"})
    assert owner.get("/api/problems").json() == []                    # two items are not a pattern


def test_auto_grouping_marks_a_sent_brief_out_of_date(client):
    owner = started(signup(client, "o@x.com"))
    export = problem_titled(owner, "export")
    iid = owner.post("/api/initiatives", json={"problem_id": export["id"]}).json()["id"]
    owner.post(f"/api/initiatives/{iid}/dispatch", json={"target": "claude-code"})
    ws = owner.get("/api/auth/me").json()["workspace"]["id"]
    with db.conn(f"ws_{ws}") as c:
        c.execute("UPDATE dispatches SET created_at='2026-01-01T00:00:00.000+00:00'")
        c.execute("UPDATE initiatives SET updated_at='2025-12-01T00:00:00.000+00:00'")
        c.execute("UPDATE evidence SET assigned_at='2025-12-01T00:00:00.000+00:00'")
        c.execute("UPDATE problems SET evidence_changed_at='2025-12-01T00:00:00.000+00:00'")
    assert owner.get(f"/api/initiatives/{iid}").json()["context"]["stale"] is False
    owner.post("/api/evidence", json={"text": "Exporting each report as its own CSV file is painful, please export all reports at once", "source": "support"})
    assert owner.get(f"/api/initiatives/{iid}").json()["context"]["reasons"] == ["1 new piece of evidence"]


def test_only_one_analysis_runs_at_a_time(client):
    owner = started(signup(client, "o@x.com"))
    ws = owner.get("/api/auth/me").json()["workspace"]["id"]
    db.use_schema(f"ws_{ws}")
    assert core.try_lock("analysis") is True and core.try_lock("analysis") is False
    e = core.add_evidence("Exporting every report as a separate CSV file is painful", "support")
    assert analysis.auto_analyze() == {"skipped": "busy"}
    assert core.one("SELECT problem_id FROM evidence WHERE id=?", (e["id"],))["problem_id"] is None
    core.release_lock("analysis")
    assert analysis.auto_analyze()["assigned"] == 1
    with db.conn() as c:                                              # a crashed run must not block forever: expired locks are taken over
        c.execute("INSERT INTO settings(key, value) VALUES ('lock:analysis', '2000-01-01T00:00:00.000+00:00') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value")
    assert core.try_lock("analysis") is True
    core.release_lock("analysis")
    db.use_schema(None)


def test_claude_assignments_are_validated_and_single_item_problems_are_refused(client, monkeypatch):
    owner = started(signup(client, "o@x.com"))
    export = problem_titled(owner, "export")
    # off while adding, so nothing is grouped yet
    owner.put("/api/settings", json={"auto_analyze": False})
    ids = [owner.post("/api/evidence", json={"text": t, "source": "note"}).json()["id"] for t in ("alpha two", "beta two", "gamma two", "delta two")]
    owner.put("/api/settings", json={"auto_analyze": True})
    reply = {"assignments": [{"evidence_id": ids[0], "problem_id": export["id"]},
                             {"evidence_id": 999999, "problem_id": export["id"]},          # not an evidence id we sent
                             {"evidence_id": ids[1], "problem_id": 424242}],              # not a problem that exists
             "new_problems": [{"title": "Real pattern", "summary": "s", "persona": "p", "severity": "high", "evidence_ids": [ids[2], ids[3]]},
                              {"title": "One-off", "summary": "s", "persona": "p", "severity": "low", "evidence_ids": [ids[1]]}]}
    monkeypatch.setattr(ai, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai, "_ask", lambda system, user, max_tokens=4000: reply)
    owner.post("/api/evidence", json={"text": "trigger", "source": "note"})
    titles = [p["title"] for p in owner.get("/api/problems").json()]
    assert "Real pattern" in titles and "One-off" not in titles
    assert grouped(owner, ids[0]) == export["id"] and grouped(owner, ids[1]) is None
    assert grouped(owner, ids[2]) == grouped(owner, ids[3]) is not None
