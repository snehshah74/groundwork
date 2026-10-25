"""Linear integration against an in-memory fake of Linear's GraphQL API."""
import hashlib
import hmac
import json
import time

import httpx
import pytest

from app import db
from app.integrations import linear
from conftest import signup

GOOD_KEY = "lin_api_good_key_123"


class FakeLinear:
    def __init__(self, teams=1):
        self.issues: dict[str, dict] = {}
        self.teams = [{"id": f"team-{n}", "key": f"T{n}", "name": f"Team {n}"} for n in range(1, teams + 1)]
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if request.headers.get("authorization") != GOOD_KEY:
            return httpx.Response(401, json={"errors": [{"message": "Authentication required"}]})
        body = json.loads(request.content)
        q, v = body["query"], body.get("variables", {})
        if "viewer" in q:
            return self._ok({"viewer": {"id": "u1", "name": "Lin Tester", "email": "l@x.com"}, "teams": {"nodes": self.teams}})
        if "issueCreate" in q:
            n = len(self.issues) + 1
            issue = {"id": f"iss-{n}", "identifier": f"T1-{n}", "url": f"https://linear.app/x/issue/T1-{n}", "title": v["in"]["title"],
                     "state": {"name": "Backlog", "type": "backlog"}, "input": v["in"]}
            self.issues[issue["id"]] = issue
            return self._ok({"issueCreate": {"success": True, "issue": self._pub(issue)}})
        if "issueUpdate" in q:
            issue = self.issues[v["id"]]
            issue["title"] = v["in"].get("title", issue["title"])
            issue["input"] = {**issue["input"], **v["in"]}
            return self._ok({"issueUpdate": {"success": True, "issue": self._pub(issue)}})
        if "issues(" in q:
            return self._ok({"issues": {"nodes": [self._pub(self.issues[i]) for i in v["ids"] if i in self.issues]}})
        return httpx.Response(200, json={"errors": [{"message": "unsupported in fake"}]})

    @staticmethod
    def _pub(i):
        return {k: i[k] for k in ("id", "identifier", "url", "title", "state")}

    @staticmethod
    def _ok(data):
        return httpx.Response(200, json={"data": data})

    def move(self, issue_id, type_, name):
        self.issues[issue_id]["state"] = {"type": type_, "name": name}


@pytest.fixture()
def fake(monkeypatch):
    f = FakeLinear()
    monkeypatch.setattr(linear, "_transport", httpx.MockTransport(f.handler))
    return f


def make_initiative(c):
    c.post("/api/seed")
    c.post("/api/analyze", json={})
    pid = c.get("/api/problems").json()[0]["id"]
    iid = c.post("/api/initiatives", json={"problem_id": pid}).json()["id"]
    c.post(f"/api/initiatives/{iid}/stories/generate")
    return iid


def test_connect_rejects_bad_key_and_hides_secrets(client, fake):
    owner = signup(client, "o@x.com")
    r = owner.post("/api/integrations/linear/connect", json={"api_key": "nope"})
    assert r.status_code == 400 and "rejected" in r.json()["detail"]
    r = owner.post("/api/integrations/linear/connect", json={"api_key": GOOD_KEY})
    assert r.status_code == 200 and r.json()["connected"] and r.json()["config"]["team_id"] == "team-1"  # single team auto-selected
    assert GOOD_KEY not in owner.get("/api/integrations").text
    cat = {i["id"]: i for i in owner.get("/api/integrations").json()}
    assert cat["linear"]["status"] == "ready" and cat["jira"]["status"] == "soon" and cat["gmail"]["status"] == "soon"
    assert cat["linear"]["webhook_url"].startswith("http://testserver/api/webhooks/linear/")


def test_secret_is_encrypted_at_rest(client, fake):
    owner = signup(client, "o@x.com")
    owner.post("/api/integrations/linear/connect", json={"api_key": GOOD_KEY})
    ws = owner.get("/api/auth/me").json()["workspace"]["id"]
    raw = db.one(f"SELECT secret FROM ws_{ws}.connections")["secret"]
    assert GOOD_KEY not in raw and len(raw) > 40


def test_push_creates_then_updates(client, fake):
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    owner.post("/api/integrations/linear/connect", json={"api_key": GOOD_KEY})
    r = owner.post(f"/api/initiatives/{iid}/push/linear")
    assert r.status_code == 200, r.text
    n_stories = len(owner.get(f"/api/initiatives/{iid}").json()["stories"])
    assert r.json()["created"] == 1 + n_stories
    parent = next(i for i in fake.issues.values() if "parentId" not in i["input"])
    kids = [i for i in fake.issues.values() if i["input"].get("parentId") == parent["id"]]
    assert len(kids) == n_stories and "Acceptance criteria" in parent["input"]["description"]
    assert parent["input"]["teamId"] == "team-1" and parent["input"]["priority"] in (2, 3, 4)
    # initiative moves to in progress: it has been handed off
    assert owner.get(f"/api/initiatives/{iid}").json()["status"] == "in_progress"
    # second push edits, never duplicates
    before = len(fake.issues)
    r2 = owner.post(f"/api/initiatives/{iid}/push/linear").json()
    assert len(fake.issues) == before and r2["created"] == 0 and r2["updated"] == 1 + n_stories
    links = owner.get(f"/api/initiatives/{iid}/links").json()
    assert len(links) == 1 + n_stories and links[0]["external_key"].startswith("T1-")


def test_push_needs_a_team_when_several(client, monkeypatch):
    f = FakeLinear(teams=2)
    monkeypatch.setattr(linear, "_transport", httpx.MockTransport(f.handler))
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    st = owner.post("/api/integrations/linear/connect", json={"api_key": GOOD_KEY}).json()
    assert st["config"]["team_id"] == "" and len(st["config"]["teams"]) == 2
    assert "Choose which Linear team" in owner.post(f"/api/initiatives/{iid}/push/linear").json()["detail"]
    assert owner.patch("/api/integrations/linear", json={"team_id": "team-evil"}).status_code == 400
    assert owner.patch("/api/integrations/linear", json={"team_id": "team-2"}).status_code == 200
    assert owner.post(f"/api/initiatives/{iid}/push/linear").status_code == 200


def test_sync_moves_initiative_status(client, fake):
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    owner.post("/api/integrations/linear/connect", json={"api_key": GOOD_KEY})
    owner.post(f"/api/initiatives/{iid}/push/linear")
    parent = next(i for i in fake.issues.values() if "parentId" not in i["input"])
    fake.move(parent["id"], "completed", "Done")
    assert owner.post("/api/integrations/linear/sync").json()["changed"] == 1
    assert owner.get(f"/api/initiatives/{iid}").json()["status"] == "shipped"
    # recording an outcome makes it measured; a later sync must not drag it back to shipped
    owner.post(f"/api/initiatives/{iid}/outcome", json={"verdict": "worked"})
    owner.post("/api/integrations/linear/sync")
    assert owner.get(f"/api/initiatives/{iid}").json()["status"] == "measured"
    assert any("Checked" in l["summary"] for l in owner.get("/api/integrations/linear/log").json())


def sign(secret, body):
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_signature_replay_and_routing(client, fake):
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    owner.post("/api/integrations/linear/connect", json={"api_key": GOOD_KEY})
    url = owner.get("/api/integrations").json()
    path = next(i["webhook_url"] for i in url if i["id"] == "linear").replace("http://testserver", "")
    owner.patch("/api/integrations/linear", json={"webhook_secret": "whsec_123"})
    owner.post(f"/api/initiatives/{iid}/push/linear")
    parent = next(i for i in fake.issues.values() if "parentId" not in i["input"])
    from fastapi.testclient import TestClient
    hook = TestClient(client.app)   # webhook calls carry no cookie

    def event(state_type, ts=None, issue_id=None):
        return json.dumps({"action": "update", "type": "Issue", "url": parent["url"], "webhookTimestamp": ts or int(time.time() * 1000),
                           "data": {"id": issue_id or parent["id"], "state": {"type": state_type, "name": state_type.title()}}}).encode()

    body = event("started")
    r = hook.post(path, content=body, headers={"linear-signature": sign("whsec_123", body)})
    assert r.status_code == 200 and r.json()["status"] is None            # push already moved it to in progress
    assert owner.get(f"/api/initiatives/{iid}").json()["status"] == "in_progress"
    body = event("completed")
    assert hook.post(path, content=body, headers={"linear-signature": sign("whsec_123", body)}).json()["status"] == "shipped"
    assert owner.get(f"/api/initiatives/{iid}").json()["status"] == "shipped"
    # forged signature, wrong secret, replayed old event, unknown token
    assert hook.post(path, content=body, headers={"linear-signature": "0" * 64}).status_code == 401
    assert hook.post(path, content=body, headers={"linear-signature": sign("other", body)}).status_code == 401
    old = event("started", ts=int((time.time() - 3600) * 1000))
    assert hook.post(path, content=old, headers={"linear-signature": sign("whsec_123", old)}).status_code == 401
    assert hook.post("/api/webhooks/linear/not-a-token", content=body).status_code == 404
    # events for issues we never pushed are ignored, not errors
    other = event("started", issue_id="somebody-elses")
    assert hook.post(path, content=other, headers={"linear-signature": sign("whsec_123", other)}).json() == {"ignored": True}
    # without a saved signing secret nothing is accepted
    owner.patch("/api/integrations/linear", json={"webhook_secret": ""})
    assert hook.post(path, content=body, headers={"linear-signature": sign("", body)}).status_code == 401


def test_roles_on_integrations(client, fake):
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    owner.post("/api/integrations/linear/connect", json={"api_key": GOOD_KEY})
    guests = {}
    for email, role in (("m@x.com", "member"), ("v@x.com", "viewer")):
        g = signup(client, email)
        tok = owner.post("/api/team/invites", json={"email": email, "role": role}).json()["link"].rsplit("/", 1)[1]
        g.post("/api/auth/invites/accept", json={"token": tok})
        guests[role] = g
    m, v = guests["member"], guests["viewer"]
    assert m.post(f"/api/initiatives/{iid}/push/linear").status_code == 200      # members can push
    assert m.post("/api/integrations/linear/connect", json={"api_key": GOOD_KEY}).status_code == 403
    assert m.delete("/api/integrations/linear").status_code == 403
    assert v.post(f"/api/initiatives/{iid}/push/linear").status_code == 403       # viewers cannot
    assert v.get("/api/integrations").status_code == 200


def test_disconnect_removes_links_and_webhook(client, fake):
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    owner.post("/api/integrations/linear/connect", json={"api_key": GOOD_KEY})
    path = next(i["webhook_url"] for i in owner.get("/api/integrations").json() if i["id"] == "linear").replace("http://testserver", "")
    owner.post(f"/api/initiatives/{iid}/push/linear")
    assert owner.delete("/api/integrations/linear").status_code == 200
    assert owner.get(f"/api/initiatives/{iid}/links").json() == []
    from fastapi.testclient import TestClient
    assert TestClient(client.app).post(path, content=b"{}").status_code == 404
    assert owner.post(f"/api/initiatives/{iid}/push/linear").json()["detail"] == "Connect this integration first."


def test_unavailable_provider(client):
    owner = signup(client, "o@x.com")
    assert owner.post("/api/integrations/jira/connect", json={"api_key": "x"}).status_code == 400
