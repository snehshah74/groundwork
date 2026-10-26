"""PRD, launch plan and prototype per initiative. The prototype is untrusted HTML and must be neutralized."""
import re

import pytest

from app import ai, docgen
from conftest import signup
from test_linear import make_initiative
from test_usage import FakeAnthropic


def gen(owner, iid, kind):
    return owner.post(f"/api/initiatives/{iid}/documents/{kind}")


def test_offline_prd_is_complete_and_cites_real_evidence(client):
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    d = gen(owner, iid, "prd").json()
    init = owner.get(f"/api/initiatives/{iid}").json()
    assert d["kind"] == "prd" and d["mode"] == "offline" and d["title"].startswith("PRD: ")
    body = d["body"]
    for section in ("## Overview", "## Problem and evidence", "## Who it is for", "## Success metric", "## Requirements", "## Out of scope", "## Open questions"):
        assert section in body
    assert all(a in body for a in init["acceptance_criteria"]) and all(s["title"] in body for s in init["stories"])
    cited = set(re.findall(r"\[#(\d+)\]", body))
    assert cited and cited <= {str(e["id"]) for e in init["evidence"]}          # only evidence this initiative really has


def test_offline_launch_plan_ties_back_to_the_success_metric(client):
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    d = gen(owner, iid, "launch").json()
    init = owner.get(f"/api/initiatives/{iid}").json()
    assert d["kind"] == "launch" and init["success_metric"] in d["body"]
    for section in ("## Audience", "## Announcement draft", "## Checklist", "## How we will know it worked"):
        assert section in d["body"]
    assert "- [ ]" in d["body"]


def test_offline_prototype_is_self_contained_and_escapes_everything(client):
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    owner.post(f"/api/initiatives/{iid}/stories", json={"title": 'As a user, I can <script>alert("x")</script> export', "activity": "Export"})
    html = gen(owner, iid, "prototype").json()["body"]
    assert html.lower().startswith("<!doctype html>") and "Content-Security-Policy" in html
    assert "<script>alert" not in html and "&lt;script&gt;alert" in html            # story text is data, never markup
    assert not re.search(r"https?://", html.replace("http://www.w3.org", ""))        # nothing to load from anywhere
    assert len(html) < docgen.MAX_HTML_BYTES


def test_hostile_model_html_is_stripped_and_locked_down():
    evil = ('<html><head><title>x</title><base href="https://evil.example/"><meta http-equiv="refresh" content="0;url=https://evil.example">'
            '<link rel="stylesheet" href="https://evil.example/a.css"><script src="https://evil.example/x.js"></script></head>'
            '<body><iframe src="https://evil.example"></iframe><object data="https://evil.example"></object><embed src="https://evil.example">'
            '<form action="https://evil.example"><input name="x"></form><img src="https://evil.example/p.png">'
            '<button onclick="document.title=1">ok</button><script>var a = 1;</script></body></html>')
    out = docgen.harden_html(evil)
    low = out.lower()
    assert "default-src 'none'" in low and "form-action 'none'" in low and "base-uri 'none'" in low
    assert low.index("content-security-policy") < low.index("<body")               # the policy comes first
    for banned in ("<base", "http-equiv=\"refresh\"", "<iframe", "<object", "<embed", "<link", "src=\"https://evil.example/x.js\""):
        assert banned not in low
    assert "<button" in low and "var a = 1" in low                                  # the prototype's own behaviour survives
    # plain text or a fenced block from the model still becomes a valid, locked-down page
    assert docgen.harden_html("```html\n<p>hi</p>\n```").lower().startswith("<!doctype html>")
    assert len(docgen.harden_html("<p>" + "x" * (docgen.MAX_HTML_BYTES * 2))) <= docgen.MAX_HTML_BYTES


def test_claude_writes_documents_and_credits_are_charged_by_weight(client, monkeypatch):
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    page = '<!doctype html><html><head><title>p</title></head><body><script src="https://evil.example/x.js"></script><button>Go</button></body></html>'
    fakes = iter(["# PRD\n\n## Overview\nFrom Claude [#1].", "# Launch\n\n## Audience\nFrom Claude.", page])
    fake = FakeAnthropic()
    fake._create = None
    fake.messages.create = lambda **kw: __import__("types").SimpleNamespace(
        content=[__import__("types").SimpleNamespace(type="text", text=next(fakes))],
        usage=__import__("types").SimpleNamespace(input_tokens=200, output_tokens=100))
    monkeypatch.setattr(ai, "_client", lambda: fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    prd, launch, proto = gen(owner, iid, "prd").json(), gen(owner, iid, "launch").json(), gen(owner, iid, "prototype").json()
    assert prd["mode"] == launch["mode"] == proto["mode"] == "claude" and "From Claude" in prd["body"]
    assert "evil.example/x.js" not in proto["body"] and "<button>Go</button>" in proto["body"]
    u = owner.get("/api/usage").json()
    assert u["by_kind"] == {"prd": 2, "launch": 2, "prototype": 3} and u["used"] == 7


def test_a_prototype_that_does_not_fit_the_credit_limit_falls_back_to_the_template(client, monkeypatch):
    monkeypatch.setenv("GROUNDWORK_AI_CREDITS", "2")
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    fake = FakeAnthropic(reply="<html></html>")
    monkeypatch.setattr(ai, "_client", lambda: fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert gen(owner, iid, "prototype").json()["mode"] == "offline" and fake.calls == 0          # 3 credits do not fit in 2


def test_versions_latest_edit_and_download(client):
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    first = gen(owner, iid, "prd").json()
    second = gen(owner, iid, "prd").json()
    assert second["id"] > first["id"]
    latest = owner.get(f"/api/initiatives/{iid}/documents").json()
    assert [d["kind"] for d in latest] == ["prd"] and latest[0]["id"] == second["id"]
    assert owner.put(f"/api/documents/{second['id']}", json={"body": "# My edited PRD"}).json()["body"] == "# My edited PRD"
    r = owner.get(f"/api/documents/{second['id']}/download")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and r.headers["content-disposition"].endswith('.md"')
    assert "text/html" not in r.headers["content-type"]
    proto = gen(owner, iid, "prototype").json()
    assert owner.put(f"/api/documents/{proto['id']}", json={"body": "<b>x</b>"}).status_code == 400          # prototypes are regenerated, not hand-edited
    pr = owner.get(f"/api/documents/{proto['id']}/download")
    assert pr.headers["content-disposition"].endswith('.html"') and "text/html" not in pr.headers["content-type"]
    assert gen(owner, iid, "poem").status_code == 404 and gen(owner, 99999, "prd").status_code == 404


def test_viewers_read_but_cannot_generate_and_documents_stay_in_their_workspace(client):
    owner, viewer, other = signup(client, "o@x.com"), signup(client, "v@x.com"), signup(client, "z@x.com")
    iid = make_initiative(owner)
    doc = gen(owner, iid, "prd").json()
    tok = owner.post("/api/team/invites", json={"email": "v@x.com", "role": "viewer"}).json()["link"].rsplit("/", 1)[1]
    viewer.post("/api/auth/invites/accept", json={"token": tok})
    assert viewer.get(f"/api/documents/{doc['id']}").status_code == 200
    assert gen(viewer, iid, "prd").status_code == 403
    assert other.get(f"/api/documents/{doc['id']}").status_code == 404


def test_documents_are_removed_with_their_initiative_and_included_in_the_export(client):
    owner = signup(client, "o@x.com")
    iid = make_initiative(owner)
    doc = gen(owner, iid, "launch").json()
    assert any(d["id"] == doc["id"] for d in owner.get("/api/team/export").json()["documents"])
    ws = owner.get("/api/auth/me").json()["workspace"]["id"]
    from app import db
    with db.conn(f"ws_{ws}") as c:
        c.execute("DELETE FROM initiatives WHERE id=?", (iid,))
    assert owner.get(f"/api/documents/{doc['id']}").status_code == 404
