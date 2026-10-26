"""Discovery importers: the framework (dedupe, cursor, PII masking, schedule) and Zendesk against a fake API."""
import base64
import re
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app import core, db
from app.integrations import linear, service, zendesk
from app.integrations.base import scrub
from conftest import signup
from test_linear import GOOD_KEY, FakeLinear

CREDS = {"subdomain": "acme", "email": "agent@acme.com", "api_token": "ztok"}


def ticket(n, ts, subject="Export is slow", body="It takes forever"):
    return {"id": n, "subject": subject, "description": body, "updated_at": ts}


class FakeZendesk:
    def __init__(self, tickets, page_size=2, role="agent"):
        self.tickets, self.page_size, self.role = tickets, page_size, role
        self.queries, self.calls = [], 0
        self.next_host = "acme.zendesk.com"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if request.headers.get("authorization") != "Basic " + base64.b64encode(b"agent@acme.com/token:ztok").decode():
            return httpx.Response(401, json={"error": "Couldn't authenticate you"})
        if request.url.path == "/api/v2/users/me.json":
            return httpx.Response(200, json={"user": {"name": "Zed Agent", "email": "agent@acme.com", "role": self.role}})
        q = parse_qs(urlparse(str(request.url)).query)
        query, page = q["query"][0], int(q.get("page", ["1"])[0])
        self.queries.append(query)
        since = re.search(r"updated>(\S+)", query).group(1)
        hits = sorted((t for t in self.tickets if t["updated_at"] > since), key=lambda t: t["updated_at"])
        chunk = hits[(page - 1) * self.page_size: page * self.page_size]
        more = page * self.page_size < len(hits)
        nxt = f"https://{self.next_host}/api/v2/search.json?page={page + 1}&query={query}" if more else None
        return httpx.Response(200, json={"results": [{**t, "result_type": "ticket"} for t in chunk], "next_page": nxt})


@pytest.fixture()
def zd(monkeypatch):
    def make(tickets, **kw):
        f = FakeZendesk(tickets, **kw)
        monkeypatch.setattr(zendesk, "_transport", httpx.MockTransport(f.handler))
        return f
    return make


def evidence(c):
    return c.get("/api/evidence").json()


def test_scrub_masks_contact_details_but_keeps_the_complaint():
    t = scrub("Call me on +1 (415) 555-0199 or 020 7946 0958, mail jane.doe+x@client.co.uk. Order 12345 broke.")
    assert "[phone]" in t and "[email]" in t and "jane" not in t and "555" not in t and "7946" not in t
    assert "Order 12345 broke." in t                                   # short numbers are not phone numbers
    assert scrub("Export is slow") == "Export is slow"


def test_evidence_with_same_external_ref_is_stored_once(client):
    owner = signup(client, "o@x.com")
    owner.get("/api/status")
    db.use_schema("ws_1")
    assert core.add_evidence("a", "zendesk", "zendesk:1")["id"] and core.add_evidence("a again", "zendesk", "zendesk:1") is None
    assert core.add_evidence("typed by hand") and core.add_evidence("typed by hand")      # no ref: duplicates are the user's business
    assert [e["text"] for e in core.list_evidence()].count("a") == 1
    db.use_schema(None)


@pytest.mark.parametrize("sub", ["", "evil.com/x", "a b", "-x", "acme.zendesk.com.evil.com", "acme@evil.com", "http://169.254.169.254", "x" * 70])
def test_subdomain_cannot_point_anywhere_but_zendesk(client, zd, sub):
    f = zd([])
    owner = signup(client, "o@x.com")
    assert owner.post("/api/integrations/zendesk/connect", json={**CREDS, "subdomain": sub}).status_code == 400
    assert f.calls == 0


def test_connect(client, zd):
    zd([])
    owner = signup(client, "o@x.com")
    assert "rejected" in owner.post("/api/integrations/zendesk/connect", json={**CREDS, "api_token": "bad"}).json()["detail"]
    for url in ("acme", "acme.zendesk.com", "https://acme.zendesk.com/"):
        assert owner.post("/api/integrations/zendesk/connect", json={**CREDS, "subdomain": url}).json()["config"]["account"] == "Zed Agent"
    assert "ztok" not in owner.get("/api/integrations").text


def test_end_users_are_refused(client, zd):
    zd([], role="end-user")
    owner = signup(client, "o@x.com")
    assert "customer, not an agent" in owner.post("/api/integrations/zendesk/connect", json=CREDS).json()["detail"]


def test_import_paginates_masks_and_is_incremental(client, zd):
    f = zd([ticket(1, "2099-01-01T10:00:00Z", "Export slow", "Call me +1 (415) 555-0199 or jane@client.com"),
            ticket(2, "2099-01-01T11:00:00Z"), ticket(3, "2099-01-01T12:00:00Z"), ticket(4, "2099-01-01T13:00:00Z"), ticket(5, "2099-01-01T14:00:00Z")])
    owner = signup(client, "o@x.com")
    owner.post("/api/integrations/zendesk/connect", json=CREDS)
    # first run: start 90 days back; tickets dated 2099 are all "new"
    r = owner.post("/api/integrations/zendesk/import").json()
    assert r == {"added": 5, "skipped": 0}                            # three pages of two, followed via next_page
    texts = [e["text"] for e in evidence(owner)]
    assert not any("jane@" in t or "555" in t for t in texts) and any("[email]" in t and "[phone]" in t for t in texts)
    assert {e["source"] for e in evidence(owner)} == {"zendesk"}
    # nothing new: the cursor (with its one-second overlap) refetches the last ticket and skips it by id
    again = owner.post("/api/integrations/zendesk/import").json()
    assert again["added"] == 0 and again["skipped"] <= 1
    assert "updated>2099-01-01T13:59:59Z" in f.queries[-1]
    f.tickets.append(ticket(6, "2099-01-01T15:00:00Z", "Search broken"))
    assert owner.post("/api/integrations/zendesk/import").json()["added"] == 1
    assert len(evidence(owner)) == 6


def test_cap_per_run_continues_next_time_without_losing_tickets_that_share_a_timestamp(client, zd, monkeypatch):
    monkeypatch.setattr(zendesk, "MAX_PER_RUN", 3)
    zd([ticket(1, "2099-02-01T10:00:00Z"), ticket(2, "2099-02-01T11:00:00Z"), ticket(3, "2099-02-01T12:00:00Z"),
        ticket(4, "2099-02-01T12:00:00Z"), ticket(5, "2099-02-01T13:00:00Z")], page_size=10)
    owner = signup(client, "o@x.com")
    owner.post("/api/integrations/zendesk/connect", json=CREDS)
    assert owner.post("/api/integrations/zendesk/import").json()["added"] == 3
    assert owner.post("/api/integrations/zendesk/import").json()["added"] == 2          # ticket 4 shares ticket 3's second and is not missed
    assert len(evidence(owner)) == 5


def test_paging_link_to_another_host_is_not_followed(client, zd):
    f = zd([ticket(i, f"2099-03-01T1{i}:00:00Z") for i in range(5)], page_size=2)
    f.next_host = "evil.example.com"
    owner = signup(client, "o@x.com")
    owner.post("/api/integrations/zendesk/connect", json=CREDS)
    r = owner.post("/api/integrations/zendesk/import")
    assert r.status_code == 400 and "another host" in r.json()["detail"]
    assert f.calls == 2                                               # connect + first page only: the refused link never reached the network


def test_scheduled_run_covers_every_workspace_and_survives_failures(client, zd, monkeypatch):
    zd([ticket(1, "2099-04-01T10:00:00Z")])
    lin = FakeLinear()
    monkeypatch.setattr(linear, "_transport", httpx.MockTransport(lin.handler))
    a, b = signup(client, "a@x.com"), signup(client, "b@x.com")
    a.post("/api/integrations/zendesk/connect", json=CREDS)
    b.post("/api/integrations/linear/connect", json={"api_key": GOOD_KEY})
    # b's Linear key gets revoked: its scheduled sync fails but a's import must still happen
    monkeypatch.setattr(linear, "_transport", httpx.MockTransport(lambda r: httpx.Response(401, json={})))
    out = service.run_scheduled()
    assert out["workspaces"] == 2 and out["imported"] == 1 and len(evidence(a)) == 1
    # b has no links yet so the tracker sync is a clean no-op rather than an error
    assert out["synced"] == 1 and out["errors"] == 0


def test_cron_endpoint_is_hidden_without_the_token(client, zd, monkeypatch):
    zd([ticket(1, "2099-05-01T10:00:00Z")])
    a = signup(client, "a@x.com")
    a.post("/api/integrations/zendesk/connect", json=CREDS)
    hook = TestClient(client.app)
    monkeypatch.delenv("GROUNDWORK_CRON_TOKEN", raising=False)
    assert hook.post("/api/cron/run", headers={"authorization": "Bearer anything"}).status_code == 404     # disabled entirely
    monkeypatch.setenv("GROUNDWORK_CRON_TOKEN", "a-long-random-cron-token-123")
    assert hook.post("/api/cron/run").status_code == 404
    assert hook.post("/api/cron/run", headers={"authorization": "Bearer wrong-token-wrong-token"}).status_code == 404
    r = hook.post("/api/cron/run", headers={"authorization": "Bearer a-long-random-cron-token-123"})
    assert r.status_code == 200 and r.json()["imported"] == 1 and len(evidence(a)) == 1
    monkeypatch.setenv("GROUNDWORK_CRON_TOKEN", "short")
    assert hook.post("/api/cron/run", headers={"authorization": "Bearer short"}).status_code == 404         # too short to trust
