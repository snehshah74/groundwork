"""Prioritization: a transparent score per problem and initiative, and a ranked build-next list."""
import pytest

from app import core, db
from conftest import signup


def seeded(owner):
    owner.post("/api/seed")
    owner.post("/api/analyze", json={})
    return owner


def test_score_components_add_up_and_stay_within_100():
    c = core.score_components(evidence_count=40, severity="high", n_sources=4, recent=40, theme_priority=1)
    assert c["total"] == 100 and sum(v for k, v in c.items() if k != "total") == pytest.approx(100, abs=0.6)
    z = core.score_components(evidence_count=0, severity="low", n_sources=0, recent=0, theme_priority=None)
    assert z["reach"] == 0 and z["corroboration"] == 0 and z["recency"] == 0 and z["strategy"] == 0 and z["total"] == z["severity"] == 4


def test_each_component_moves_the_score_in_the_right_direction():
    base = dict(evidence_count=5, severity="medium", n_sources=1, recent=0, theme_priority=None)
    score = lambda **kw: core.score_components(**{**base, **kw})["total"]
    assert score(evidence_count=15) > score(evidence_count=5) > score(evidence_count=1)
    assert score(severity="high") > score(severity="medium") > score(severity="low")
    assert score(n_sources=3) > score(n_sources=2) > score(n_sources=1)
    assert score(recent=5) > score(recent=0)
    assert score(theme_priority=1) > score(theme_priority=2) > score(theme_priority=3) > score(theme_priority=None)
    assert score(evidence_count=500) == score(evidence_count=20)       # more than 20 customers does not keep inflating it


def test_priorities_rank_problems_and_a_priority_theme_lifts_its_problems(client):
    owner = seeded(signup(client, "o@x.com"))
    themes = owner.post("/api/themes/generate").json()
    start = owner.get("/api/priorities").json()["start_next"]
    assert [p["score"] for p in start] == sorted((p["score"] for p in start), reverse=True) and len(start) == 5
    target = start[-1]                                                  # the lowest-ranked problem
    th = owner.get(f"/api/problems/{target['id']}").json()
    assert th["theme_id"] is not None
    assert owner.patch(f"/api/themes/{th['theme_id']}", json={"priority": 1}).json()["priority"] == 1
    after = owner.get("/api/priorities").json()["start_next"]
    moved = next(p for p in after if p["id"] == target["id"])
    assert moved["components"]["strategy"] == 20 and moved["score"] > target["score"]
    # the theme's problems rise as a group: its best-placed problem now ranks above where it did before
    mates = {p["id"] for p in start if p["theme_title"] == target["theme_title"]}
    best = lambda lst: min(i for i, p in enumerate(lst) if p["id"] in mates)
    assert len(mates) > 1 and best(after) < best(start)
    assert all(p["components"]["strategy"] == 20 for p in after if p["id"] in mates)


def test_theme_priority_validation(client):
    owner = seeded(signup(client, "o@x.com"))
    t = owner.post("/api/themes", json={"title": "Win finance teams"}).json()
    assert owner.patch(f"/api/themes/{t['id']}", json={"priority": 4}).status_code == 400
    assert owner.patch(f"/api/themes/{t['id']}", json={"priority": 0}).status_code == 400
    assert owner.patch(f"/api/themes/{t['id']}", json={"priority": 2}).json()["priority"] == 2
    assert owner.patch(f"/api/themes/{t['id']}", json={"priority": None}).json()["priority"] is None
    assert owner.patch(f"/api/themes/{t['id']}", json={"title": "Renamed"}).json()["title"] == "Renamed"     # other edits leave priority alone


def test_build_next_lists_unshipped_initiatives_by_score_and_start_next_drops_covered_problems(client):
    owner = seeded(signup(client, "o@x.com"))
    probs = owner.get("/api/problems").json()
    ids = [owner.post("/api/initiatives", json={"problem_id": p["id"]}).json()["id"] for p in probs[:3]]
    pr = owner.get("/api/priorities").json()
    assert {i["id"] for i in pr["build_next"]} == set(ids) and len(pr["start_next"]) == len(probs) - 3
    assert [i["score"] for i in pr["build_next"]] == sorted((i["score"] for i in pr["build_next"]), reverse=True)
    first = pr["build_next"][0]
    assert set(first["components"]) == {"reach", "severity", "corroboration", "recency", "strategy", "total"} and first["why"]
    owner.patch(f"/api/initiatives/{first['id']}", json={"status": "shipped"})
    assert first["id"] not in [i["id"] for i in owner.get("/api/priorities").json()["build_next"]]      # done work is not "next"


def test_recent_evidence_counts_more_than_old_evidence(client):
    owner = seeded(signup(client, "o@x.com"))
    before = {p["id"]: p["components"]["recency"] for p in owner.get("/api/priorities").json()["start_next"]}
    ws = owner.get("/api/auth/me").json()["workspace"]["id"]
    with db.conn(f"ws_{ws}") as c:
        c.execute("UPDATE evidence SET created_at='2020-01-01T00:00:00.000+00:00'")
    after = {p["id"]: p["components"]["recency"] for p in owner.get("/api/priorities").json()["start_next"]}
    assert all(v > 0 for v in before.values()) and all(v == 0 for v in after.values())


def test_home_points_at_what_to_build_next(client):
    owner = seeded(signup(client, "o@x.com"))
    hit = next(i for i in owner.get("/api/home").json()["insights"] if i["view"] == "priorities")
    assert hit["level"] == "info" and "Build next" in hit["title"]


def test_viewers_can_read_priorities(client):
    owner, viewer = seeded(signup(client, "o@x.com")), signup(client, "v@x.com")
    tok = owner.post("/api/team/invites", json={"email": "v@x.com", "role": "viewer"}).json()["link"].rsplit("/", 1)[1]
    viewer.post("/api/auth/invites/accept", json={"token": tok})
    assert viewer.get("/api/priorities").status_code == 200 and len(viewer.get("/api/priorities").json()["start_next"]) == 5
