"""AI usage is metered per workspace, and a monthly credit limit falls back to offline mode instead of failing."""
import json
from types import SimpleNamespace

import pytest

from app import ai, db, usage
from conftest import signup
from test_linear import make_initiative


class FakeAnthropic:
    """Stands in for anthropic.Anthropic(): counts calls and reports token usage like the real API."""
    def __init__(self, reply=None, fail=False):
        self.calls, self.fail = 0, fail
        self.reply = reply if reply is not None else {"title": "AI spec", "theme": "t", "persona": "p", "problem_statement": "s",
                                                       "success_metric": "m", "out_of_scope": ["a"], "acceptance_criteria": ["b"]}
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls += 1
        if self.fail:
            raise RuntimeError("upstream down")
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], usage=SimpleNamespace(input_tokens=100, output_tokens=50))


@pytest.fixture()
def claude(monkeypatch):
    """A fake Claude that is installed but switched on only when a test calls .enable(), after any seeding."""
    fake = FakeAnthropic()
    monkeypatch.setattr(ai, "_client", lambda: fake)
    fake.enable = lambda: monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    return fake


def spec(owner, pid):
    return owner.post("/api/initiatives", json={"problem_id": pid})


def first_problem(owner):
    owner.post("/api/seed")
    owner.post("/api/analyze", json={})
    return owner.get("/api/problems").json()[0]["id"]


def test_offline_use_costs_nothing(client):
    owner = signup(client, "o@x.com")
    spec(owner, first_problem(owner))
    u = owner.get("/api/usage").json()
    assert u["used"] == 0 and u["by_kind"] == {} and u["limit"] == 0


def test_each_claude_call_is_recorded_with_its_kind_and_tokens(client, claude):
    owner = signup(client, "o@x.com")
    pid = first_problem(owner)
    claude.enable()
    assert spec(owner, pid).json()["mode"] == "claude" and claude.calls == 1
    u = owner.get("/api/usage").json()
    assert u["used"] == 1 and u["by_kind"] == {"spec": 1} and u["input_tokens"] == 100 and u["output_tokens"] == 50
    owner.post("/api/ask", json={"messages": [{"role": "user", "content": "What next?"}]})
    assert owner.get("/api/usage").json()["by_kind"] == {"spec": 1, "ask": 1}


def test_a_failed_call_is_not_charged_and_falls_back(client, monkeypatch):
    monkeypatch.setattr(ai, "_client", lambda: FakeAnthropic(fail=True))
    owner = signup(client, "o@x.com")
    pid = first_problem(owner)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    r = spec(owner, pid).json()
    assert r["mode"] == "offline" and owner.get("/api/usage").json()["used"] == 0


def test_the_monthly_limit_switches_to_offline_without_calling_the_api(client, claude, monkeypatch):
    monkeypatch.setenv("GROUNDWORK_AI_CREDITS", "2")
    owner = signup(client, "o@x.com")
    pid = first_problem(owner)
    claude.enable()
    assert spec(owner, pid).json()["mode"] == "claude" and spec(owner, pid).json()["mode"] == "claude"
    assert owner.get("/api/status").json()["ai_mode"] == "limited"
    third = spec(owner, pid).json()
    assert third["mode"] == "offline" and claude.calls == 2                        # the third never reached Anthropic
    u = owner.get("/api/usage").json()
    assert u["limit"] == 2 and u["used"] == 2 and u["remaining"] == 0 and u["resets_at"]
    ans = owner.post("/api/ask", json={"messages": [{"role": "user", "content": "hi"}]}).json()
    assert ans["mode"] == "offline" and "credits" in ans["answer"].lower()


def test_last_months_use_does_not_count_and_each_workspace_has_its_own_allowance(client, claude, monkeypatch):
    monkeypatch.setenv("GROUNDWORK_AI_CREDITS", "1")
    a, b = signup(client, "a@x.com"), signup(client, "b@x.com")
    pa, pb = first_problem(a), first_problem(b)
    claude.enable()
    ws_a = a.get("/api/auth/me").json()["workspace"]["id"]
    with db.conn(f"ws_{ws_a}") as c:
        c.execute("INSERT INTO ai_usage(kind, model, input_tokens, output_tokens, credits, created_at) VALUES ('spec','m',1,1,1,'2020-01-15T00:00:00.000+00:00')")
    assert a.get("/api/usage").json()["used"] == 0                                 # an old row from 2020 is not this month
    assert spec(a, pa).json()["mode"] == "claude"
    assert spec(a, pa).json()["mode"] == "offline"                                 # a's single credit is spent
    assert spec(b, pb).json()["mode"] == "claude"                                  # b is a different workspace with its own credit


def test_heavier_actions_cost_more_credits(client, monkeypatch):
    assert usage.cost("spec") == 1 and usage.cost("prototype") == 3 and usage.cost("unknown-kind") == 1
    monkeypatch.setenv("GROUNDWORK_AI_CREDITS", "2")
    owner = signup(client, "o@x.com")
    owner.get("/api/status")
    ws = owner.get("/api/auth/me").json()["workspace"]["id"]
    db.use_schema(f"ws_{ws}")
    assert usage.allowed("spec") is True and usage.allowed("prototype") is False   # 3 credits do not fit in an allowance of 2
    db.use_schema(None)


def test_unlimited_when_no_limit_is_configured(client, claude, monkeypatch):
    monkeypatch.delenv("GROUNDWORK_AI_CREDITS", raising=False)
    owner = signup(client, "o@x.com")
    pid = first_problem(owner)
    claude.enable()
    for _ in range(3):
        assert spec(owner, pid).json()["mode"] == "claude"
    u = owner.get("/api/usage").json()
    assert u["used"] == 3 and u["limit"] == 0 and u["remaining"] is None
