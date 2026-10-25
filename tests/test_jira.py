"""Jira Cloud integration against an in-memory fake of the REST v3 API."""
import base64
import hashlib
import hmac
import json
import re
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db
from app.integrations import jira
from conftest import signup
from test_linear import make_initiative

CREDS = {"site": "acme.atlassian.net", "email": "me@acme.com", "api_token": "tok-good"}


class FakeJira:
    def __init__(self, issue_types=("Epic", "Story", "Task")):
        self.issue_types = issue_types
        self.issues: dict[str, dict] = {}
        self.jqls: list[str] = []
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        want = "Basic " + base64.b64encode(b"me@acme.com:tok-good").decode()
        if request.headers.get("authorization") != want:
            return httpx.Response(401, json={"errorMessages": ["bad creds"]})
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else {}
        if path == "/rest/api/3/myself":
            return httpx.Response(200, json={"displayName": "Jo Tester", "emailAddress": "me@acme.com"})
        if path == "/rest/api/3/project/search":
            return httpx.Response(200, json={"values": [{"id": "10000", "key": "ENG", "name": "Engineering"}, {"id": "10001", "key": "OPS", "name": "Operations"}]})
        if path == "/rest/api/3/issue" and method == "POST":
            f = body["fields"]
            if f["issuetype"]["name"] not in self.issue_types:
                return httpx.Response(400, json={"errors": {"issuetype": "Specify a valid issue type"}})
            assert f["description"]["type"] == "doc" and f["description"]["version"] == 1, "description must be ADF"
            n = len(self.issues) + 1
            issue = {"id": str(10000 + n), "key": f"{f['project']['key']}-{n}", "fields": f,
                     "status": {"name": "To Do", "statusCategory": {"key": "new"}}}
            self.issues[issue["key"]] = issue
            return httpx.Response(201, json={"id": issue["id"], "key": issue["key"], "self": f"https://acme.atlassian.net/rest/api/3/issue/{issue['id']}"})
        m = re.fullmatch(r"/rest/api/3/issue/([A-Z]+-\d+)", path)
        if m and method == "PUT":
            self.issues[m.group(1)]["fields"].update(body["fields"])
            return httpx.Response(204)
        if path == "/rest/api/3/search/jql" and method == "POST":
            self.jqls.append(body["jql"])
            keys = [k.strip() for k in re.search(r"key in \((.*)\)", body["jql"]).group(1).split(",")]
            return httpx.Response(200, json={"issues": [{"id": self.issues[k]["id"], "key": k, "fields": {"status": self.issues[k]["status"]}} for k in keys if k in self.issues]})
        return httpx.Response(404, json={"errorMessages": [f"no route {method} {path}"]})

    def move(self, key, name, category):
        self.issues[key]["status"] = {"name": name, "statusCategory": {"key": category}}


@pytest.fixture()
def fake(monkeypatch):
    f = FakeJira()
    monkeypatch.setattr(jira, "_transport", httpx.MockTransport(f.handler))
    return f


def connect(owner, project="ENG"):
    st = owner.post("/api/integrations/jira/connect", json=CREDS)
    assert st.status_code == 200, st.text
    if project:
        assert owner.patch("/api/integrations/jira", json={"target_id": project}).status_code == 200
    return st.json()


@pytest.mark.parametrize("site", [
    "http://acme.atlassian.net", "https://localhost", "https://169.254.169.254", "https://127.0.0.1:8080",
    "https://acme.atlassian.net.evil.com", "https://acme.atlassian.net@evil.com", "https://evil.com/acme.atlassian.net",
    "https://acme.atlassian.net:8443", "https://user:pw@acme.atlassian.net", "ftp://acme.atlassian.net", "",
])
def test_site_must_be_a_plain_atlassian_cloud_host(client, fake, site):
    owner = signup(client, "o@x.com")
    r = owner.post("/api/integrations/jira/connect", json={**CREDS, "site": site})
    assert r.status_code == 400
    assert fake.calls == 0                                            # no request was ever made to the supplied address


def test_connect_lists_projects_and_rejects_bad_token(client, fake):
    owner = signup(client, "o@x.com")
    assert "rejected" in owner.post("/api/integrations/jira/connect", json={**CREDS, "api_token": "nope"}).json()["detail"]
    st = connect(owner, project=None)
    assert [t["key"] for t in st["config"]["targets"]] == ["ENG", "OPS"] and st["config"]["target_id"] == "" and st["config"]["target_label"] == "project"
    assert "tok-good" not in owner.get("/api/integrations").text
    assert owner.patch("/api/integrations/jira", json={"target_id": "EVIL"}).json()["detail"] == "That project is not available to this connection."


def test_push_epic_and_stories_then_update_in_place(client, fake):
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    connect(owner)
    r = owner.post(f"/api/initiatives/{iid}/push/jira")
    assert r.status_code == 200, r.text
    n_stories = len(owner.get(f"/api/initiatives/{iid}").json()["stories"])
    assert r.json()["created"] == 1 + n_stories
    epic = next(i for i in fake.issues.values() if i["fields"]["issuetype"]["name"] == "Epic")
    kids = [i for i in fake.issues.values() if i["fields"]["issuetype"]["name"] == "Story"]
    assert len(kids) == n_stories and all(k["fields"]["parent"]["key"] == epic["key"] for k in kids)
    kinds = {c["type"] for c in epic["fields"]["description"]["content"]}
    assert {"heading", "paragraph", "bulletList"} <= kinds             # real structure, not one blob of text
    assert all(len(i["fields"]["summary"]) <= 255 for i in fake.issues.values())
    links = owner.get(f"/api/initiatives/{iid}/links").json()
    assert links[0]["external_url"] == f"https://acme.atlassian.net/browse/{epic['key']}"
    before = len(fake.issues)
    again = owner.post(f"/api/initiatives/{iid}/push/jira").json()
    assert len(fake.issues) == before and again["created"] == 0 and again["updated"] == 1 + n_stories


def test_jira_errors_are_shown_not_swallowed(client, monkeypatch):
    f = FakeJira(issue_types=("Task",))                               # a project with no Epic type
    monkeypatch.setattr(jira, "_transport", httpx.MockTransport(f.handler))
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    connect(owner)
    r = owner.post(f"/api/initiatives/{iid}/push/jira")
    assert r.status_code == 400 and "valid issue type" in r.json()["detail"]
    assert any(not l["ok"] for l in owner.get("/api/integrations/jira/log").json())


def test_sync_maps_status_categories_forward_only(client, fake):
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    connect(owner)
    owner.post(f"/api/initiatives/{iid}/push/jira")
    epic = next(i for i in fake.issues.values() if i["fields"]["issuetype"]["name"] == "Epic")
    assert owner.post("/api/integrations/jira/sync").json()["changed"] == 0          # To Do right after the push: no downgrade
    assert owner.get(f"/api/initiatives/{iid}").json()["status"] == "in_progress"
    fake.move(epic["key"], "Done", "done")
    assert owner.post("/api/integrations/jira/sync").json()["changed"] == 1
    assert owner.get(f"/api/initiatives/{iid}").json()["status"] == "shipped"
    assert owner.get(f"/api/initiatives/{iid}/links").json()[0]["external_status"] == "Done"


def test_sync_never_puts_unvalidated_keys_into_jql(client, fake):
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    connect(owner)
    owner.post(f"/api/initiatives/{iid}/push/jira")
    ws = owner.get("/api/auth/me").json()["workspace"]["id"]
    with db.conn(f"ws_{ws}") as c:
        c.execute("UPDATE sync_links SET external_key = 'ENG-1) OR project = SECRET' WHERE kind='story' AND id = (SELECT min(id) FROM sync_links WHERE kind='story')")
    owner.post("/api/integrations/jira/sync")
    assert fake.jqls and all(re.fullmatch(r"key in \((?:[A-Z]+-\d+(?:, )?)+\)", j) for j in fake.jqls), fake.jqls


def sign(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook(client, fake):
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    connect(owner)
    path = next(i["webhook_url"] for i in owner.get("/api/integrations").json() if i["id"] == "jira").replace("http://testserver", "")
    owner.patch("/api/integrations/jira", json={"webhook_secret": "jirasecret"})
    owner.post(f"/api/initiatives/{iid}/push/jira")
    epic = next(i for i in fake.issues.values() if i["fields"]["issuetype"]["name"] == "Epic")
    hook = TestClient(client.app)

    def event(category, name, ts=None, kind="jira:issue_updated"):
        return json.dumps({"webhookEvent": kind, "timestamp": ts or int(time.time() * 1000),
                           "issue": {"id": epic["id"], "key": epic["key"], "self": f"https://acme.atlassian.net/rest/api/2/issue/{epic['id']}",
                                     "fields": {"status": {"name": name, "statusCategory": {"key": category}}}}}).encode()

    body = event("done", "Done")
    r = hook.post(path, content=body, headers={"x-hub-signature": sign("jirasecret", body)})
    assert r.status_code == 200 and r.json()["status"] == "shipped"
    assert owner.get(f"/api/initiatives/{iid}").json()["status"] == "shipped"
    assert hook.post(path, content=body, headers={"x-hub-signature": sign("wrong", body)}).status_code == 401
    assert hook.post(path, content=body).status_code == 401
    old = event("done", "Done", ts=int((time.time() - 7200) * 1000))
    assert hook.post(path, content=old, headers={"x-hub-signature": sign("jirasecret", old)}).status_code == 401
    other = event("done", "Done", kind="comment_created")
    assert hook.post(path, content=other, headers={"x-hub-signature": sign("jirasecret", other)}).json() == {"ignored": True}
