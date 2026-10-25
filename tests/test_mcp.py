import asyncio
import importlib
import sys

import httpx
import pytest

from app.integrations import linear
from conftest import signup
from test_linear import GOOD_KEY, FakeLinear, make_initiative


def load_mcp(monkeypatch, token):
    monkeypatch.setenv("GROUNDWORK_TOKEN", token)
    sys.modules.pop("mcp_server", None)
    return importlib.import_module("mcp_server")


def token_of(c):
    return c.get("/api/auth/me").json()["api_token"]


def test_bad_or_missing_token_exits(client, monkeypatch):
    monkeypatch.delenv("GROUNDWORK_TOKEN", raising=False)
    sys.modules.pop("mcp_server", None)
    with pytest.raises(SystemExit):
        importlib.import_module("mcp_server")
    monkeypatch.setenv("GROUNDWORK_TOKEN", "gw_nope")
    sys.modules.pop("mcp_server", None)
    with pytest.raises(SystemExit):
        importlib.import_module("mcp_server")


def test_member_tools_end_to_end(client, monkeypatch):
    f = FakeLinear()
    monkeypatch.setattr(linear, "_transport", httpx.MockTransport(f.handler))
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    owner.post("/api/integrations/linear/connect", json={"api_key": GOOD_KEY})
    m = load_mcp(monkeypatch, token_of(owner))

    names = {t.name for t in asyncio.run(m.mcp.list_tools())}
    assert {"add_evidence", "list_problems", "list_integrations", "push_initiative", "get_initiative_context", "report_outcome"} <= names

    assert "P1" in m.list_problems() and f"#{iid}" in m.list_initiatives()
    assert "Initiative" in m.get_initiative_context(iid)
    assert "Added evidence" in m.add_evidence("Agent found this in the logs", "note")
    assert owner.get("/api/status").json()["evidence"] == 25            # same workspace the web app sees
    assert "linear" in m.list_integrations()
    assert "Sent to linear" in m.push_initiative(iid, "linear")
    assert "recorded" in m.report_outcome(iid, "worked", "37% fewer tickets")
    assert owner.get(f"/api/initiatives/{iid}").json()["status"] == "measured"
    assert "must be one of" in m.report_outcome(iid, "great")


def test_viewer_token_is_read_only_and_isolated(client, monkeypatch):
    owner, viewer = signup(client, "o@x.com"), signup(client, "v@x.com")
    iid = make_initiative(owner)
    tok = owner.post("/api/team/invites", json={"email": "v@x.com", "role": "viewer"}).json()["link"].rsplit("/", 1)[1]
    viewer.post("/api/auth/invites/accept", json={"token": tok})
    m = load_mcp(monkeypatch, token_of(viewer))
    assert f"#{iid}" in m.list_initiatives()
    for result in (m.add_evidence("nope"), m.update_status(iid, "approved"), m.report_outcome(iid, "worked"), m.push_initiative(iid)):
        assert "view-only" in result
    before = owner.get(f"/api/initiatives/{iid}").json()
    m.get_initiative_context(iid)                                         # reading must not record a hand-off for a viewer
    assert owner.get(f"/api/initiatives/{iid}").json()["dispatches"] == before["dispatches"]
    # another workspace's agent never sees this data
    other = signup(client, "z@x.com")
    m2 = load_mcp(monkeypatch, token_of(other))
    assert m2.list_initiatives() == "No initiatives found."
