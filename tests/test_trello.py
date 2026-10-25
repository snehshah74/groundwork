"""Trello integration against an in-memory fake of the REST API."""
import json
import re

import httpx
import pytest

from app.integrations import trello
from app.integrations.trello import state_from_list
from conftest import signup
from test_linear import make_initiative

CREDS = {"api_key": "k1", "token": "t1"}


class FakeTrello:
    def __init__(self):
        self.cards: dict[str, dict] = {}
        self.checklists: dict[str, dict] = {}
        self.lists = {"L1": "Backlog", "L2": "Doing", "L3": "Done", "L4": "Weekly notes"}
        self.leaked = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        if q.get("key") != "k1" or q.get("token") != "t1":
            return httpx.Response(401, text="invalid key")
        path, method = request.url.path, request.method
        ok = lambda data: httpx.Response(200, json=data)
        if path == "/1/members/me":
            return ok({"fullName": "Tre Tester", "username": "tre"})
        if path == "/1/members/me/boards":
            return ok([{"id": "B1", "name": "Product", "lists": [{"id": k, "name": v} for k, v in self.lists.items()]}])
        if path == "/1/cards" and method == "POST":
            if q["idList"] not in self.lists:
                return httpx.Response(400, text="invalid value for idList")
            cid = f"card{len(self.cards) + 1}"
            self.cards[cid] = {"id": cid, "name": q["name"], "desc": q["desc"], "idList": q["idList"], "closed": False, "shortUrl": f"https://trello.com/c/{cid}"}
            return ok(self.cards[cid])
        m = re.fullmatch(r"/1/cards/(card\d+)", path)
        if m and method == "PUT":
            self.cards[m.group(1)].update({k: q[k] for k in ("name", "desc") if k in q})
            return ok(self.cards[m.group(1)])
        if m and method == "GET":
            c = self.cards[m.group(1)]
            return ok({**c, "list": {"id": c["idList"], "name": self.lists[c["idList"]]}})
        m = re.fullmatch(r"/1/cards/(card\d+)/checklists", path)
        if m and method == "POST":
            clid = f"cl{len(self.checklists) + 1}"
            self.checklists[clid] = {"id": clid, "name": q["name"], "card": m.group(1), "items": {}}
            return ok({"id": clid})
        m = re.fullmatch(r"/1/checklists/(cl\d+)/checkItems", path)
        if m and method == "POST":
            items = self.checklists[m.group(1)]["items"]
            iid = f"it{sum(len(c['items']) for c in self.checklists.values()) + 1}"
            items[iid] = {"id": iid, "name": q["name"]}
            return ok({"id": iid, "name": q["name"], "state": "incomplete"})
        m = re.fullmatch(r"/1/cards/(card\d+)/checkItem/(it\d+)", path)
        if m and method == "PUT":
            for cl in self.checklists.values():
                if m.group(2) in cl["items"]:
                    cl["items"][m.group(2)]["name"] = q["name"]
            return ok({})
        return httpx.Response(404, text="The requested resource was not found.")


@pytest.fixture()
def fake(monkeypatch):
    f = FakeTrello()
    monkeypatch.setattr(trello, "_transport", httpx.MockTransport(f.handler))
    return f


def test_list_names_map_to_states():
    assert state_from_list("Done") == "completed" and state_from_list("Shipped 🚀") == "completed"
    assert state_from_list("In Progress") == "started" and state_from_list("Doing") == "started"
    assert state_from_list("Done / Review") == "completed"            # done wins over review
    assert state_from_list("To Do") == "unstarted" and state_from_list("Up next") == "unstarted"
    assert state_from_list("Backlog") == "backlog" and state_from_list("Ideas") == "backlog"
    assert state_from_list("Weekly notes") == "" and state_from_list("") == ""


def test_connect_lists_every_list_as_a_target(client, fake):
    owner = signup(client, "o@x.com")
    assert "rejected" in owner.post("/api/integrations/trello/connect", json={"api_key": "bad", "token": "t1"}).json()["detail"]
    assert "Enter both" in owner.post("/api/integrations/trello/connect", json={"api_key": "k1"}).json()["detail"]
    st = owner.post("/api/integrations/trello/connect", json=CREDS).json()
    assert [t["name"] for t in st["config"]["targets"]][:2] == ["Product / Backlog", "Product / Doing"]
    assert st["config"]["target_label"] == "list" and st["webhooks"] is False
    assert "t1" not in owner.get("/api/integrations").text


def test_push_card_with_stories_checklist_then_update(client, fake):
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    owner.post("/api/integrations/trello/connect", json=CREDS)
    owner.patch("/api/integrations/trello", json={"target_id": "L1"})
    r = owner.post(f"/api/initiatives/{iid}/push/trello")
    assert r.status_code == 200, r.text
    n = len(owner.get(f"/api/initiatives/{iid}").json()["stories"])
    assert r.json()["created"] == 1 + n                               # the card and its stories; the checklist is bookkeeping
    card = fake.cards["card1"]
    assert card["idList"] == "L1" and "## Problem" in card["desc"] and len(fake.cards) == 1
    [cl] = fake.checklists.values()
    assert cl["name"] == "Stories" and len(cl["items"]) == n
    # a story added in Groundwork becomes one more item on the SAME checklist; nothing is duplicated
    owner.post(f"/api/initiatives/{iid}/stories", json={"title": "As an admin, I can export everything"})
    r2 = owner.post(f"/api/initiatives/{iid}/push/trello").json()
    assert len(fake.cards) == 1 and len(fake.checklists) == 1 and len(cl["items"]) == n + 1
    assert [l["kind"] for l in owner.get(f"/api/initiatives/{iid}/links").json()].count("story") == n + 1
    assert r2["created"] == 1                                         # only the new story (the checklist bookkeeping link is not counted)


def test_sync_follows_the_card_between_lists(client, fake):
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    owner.post("/api/integrations/trello/connect", json=CREDS)
    owner.patch("/api/integrations/trello", json={"target_id": "L1"})
    owner.post(f"/api/initiatives/{iid}/push/trello")
    assert owner.post("/api/integrations/trello/sync").json()["changed"] == 0       # Backlog right after pushing: no downgrade
    fake.cards["card1"]["idList"] = "L4"                                            # a list whose name says nothing
    assert owner.post("/api/integrations/trello/sync").json()["changed"] == 0
    fake.cards["card1"]["idList"] = "L3"
    assert owner.post("/api/integrations/trello/sync").json()["changed"] == 1
    assert owner.get(f"/api/initiatives/{iid}").json()["status"] == "shipped"
    assert owner.get(f"/api/initiatives/{iid}/links").json()[0]["external_status"] == "Done"
    fake.cards["card1"]["idList"] = "L2"; fake.cards["card1"]["closed"] = True      # archived cards are ignored
    owner.post("/api/integrations/trello/sync")
    assert owner.get(f"/api/initiatives/{iid}").json()["status"] == "shipped"


def test_trello_errors_do_not_leak_credentials(client, fake, monkeypatch):
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    owner.post("/api/integrations/trello/connect", json=CREDS)
    owner.patch("/api/integrations/trello", json={"target_id": "L1"})
    fake.lists.pop("L1")                                                            # the list was deleted in Trello
    r = owner.post(f"/api/initiatives/{iid}/push/trello")
    assert r.status_code == 400 and "idList" in r.json()["detail"]
    # a network failure must not echo the URL, which contains the key and token
    def boom(request): raise httpx.ConnectError("failed for https://api.trello.com/1/cards?key=k1&token=t1")
    monkeypatch.setattr(trello, "_transport", httpx.MockTransport(boom))
    r = owner.post(f"/api/initiatives/{iid}/push/trello")
    assert r.status_code == 400 and "k1" not in r.text and "t1" not in r.text
    assert "k1" not in json.dumps(owner.get("/api/integrations/trello/log").json())
