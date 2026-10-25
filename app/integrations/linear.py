"""Linear: initiative -> parent issue, stories -> sub-issues, status read back by webhook or sync.

Auth is a personal API key (Settings > API in Linear) or OAuth when LINEAR_CLIENT_ID and
LINEAR_CLIENT_SECRET are set. Docs: https://linear.app/developers
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx

from .base import Field, Integration, IntegrationError, Link, Meta, initiative_markdown, story_markdown

API = "https://api.linear.app/graphql"
PRIORITY = {"high": 2, "medium": 3, "low": 4}   # Linear: 1 urgent, 2 high, 3 medium, 4 low
_transport: httpx.BaseTransport | None = None      # tests swap this for a fake Linear


def _gql(secret: dict, query: str, variables: dict | None = None) -> dict:
    token = secret.get("access_token")
    headers = {"Authorization": f"Bearer {token}" if token else secret.get("api_key", ""), "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=20, transport=_transport) as c:
            r = c.post(API, headers=headers, json={"query": query, "variables": variables or {}})
    except httpx.HTTPError as e:
        raise IntegrationError(f"Could not reach Linear: {e}") from e
    if r.status_code in (401, 403):
        raise IntegrationError("Linear rejected the credentials. Check the API key and that it has access.")
    if r.status_code == 429:
        raise IntegrationError("Linear is rate limiting requests. Try again in a minute.")
    try:
        data = r.json()
    except ValueError as e:
        raise IntegrationError(f"Linear returned an unexpected response ({r.status_code}).") from e
    if data.get("errors"):
        msg = data["errors"][0].get("message", "unknown error")
        raise IntegrationError(f"Linear said: {msg}")
    return data["data"]


_ISSUE = "id identifier url title state { name type }"


class Linear(Integration):
    meta = Meta(
        id="linear", name="Linear", kind="tracker", status="ready", auth="api_key",
        description="Send initiatives to Linear as issues with their stories as sub-issues, and keep status in sync.",
        fields=[Field("api_key", "Personal API key", secret=True, help="Linear > Settings > Account > Security & access > Personal API keys")],
    )

    def connect(self, credentials: dict) -> tuple[dict, dict]:
        key, token = (credentials.get("api_key") or "").strip(), credentials.get("access_token")
        if not key and not token:
            raise IntegrationError("Paste your Linear API key.")
        secret = {"access_token": token} if token else {"api_key": key}
        data = _gql(secret, "query { viewer { id name email } teams { nodes { id key name } } }")
        teams = data["teams"]["nodes"]
        if not teams:
            raise IntegrationError("That Linear account has no teams to send work to.")
        cfg = {"viewer": data["viewer"]["name"] or data["viewer"]["email"], "teams": teams, "team_id": teams[0]["id"] if len(teams) == 1 else ""}
        return cfg, secret

    # ---------------------------------------------------------------- push
    def push_initiative(self, cfg, secret, initiative, stories, links):
        team = cfg.get("team_id")
        if not team:
            raise IntegrationError("Choose which Linear team should receive this work.")
        problem = initiative.get("problem") or {}
        prio = PRIORITY.get(problem.get("severity", ""), 0)
        out: list[Link] = []

        parent_link = links.get(("initiative", initiative["id"]))
        body = initiative_markdown(initiative, problem or None, len(initiative.get("evidence", [])))
        if parent_link:
            issue = _gql(secret, "mutation($id: String!, $in: IssueUpdateInput!) { issueUpdate(id: $id, input: $in) { success issue { %s } } }" % _ISSUE,
                         {"id": parent_link.external_id, "in": {"title": initiative["title"], "description": body}})["issueUpdate"]["issue"]
        else:
            issue = _gql(secret, "mutation($in: IssueCreateInput!) { issueCreate(input: $in) { success issue { %s } } }" % _ISSUE,
                         {"in": {"teamId": team, "title": initiative["title"], "description": body, "priority": prio}})["issueCreate"]["issue"]
        out.append(Link("initiative", initiative["id"], issue["id"], issue["identifier"], issue["url"], issue["state"]["name"]))

        for s in stories:
            existing = links.get(("story", s["id"]))
            sbody = story_markdown(s)
            if existing:
                child = _gql(secret, "mutation($id: String!, $in: IssueUpdateInput!) { issueUpdate(id: $id, input: $in) { success issue { %s } } }" % _ISSUE,
                             {"id": existing.external_id, "in": {"title": s["title"], "description": sbody}})["issueUpdate"]["issue"]
            else:
                child = _gql(secret, "mutation($in: IssueCreateInput!) { issueCreate(input: $in) { success issue { %s } } }" % _ISSUE,
                             {"in": {"teamId": team, "title": s["title"], "description": sbody, "parentId": issue["id"], "priority": prio}})["issueCreate"]["issue"]
            out.append(Link("story", s["id"], child["id"], child["identifier"], child["url"], child["state"]["name"]))
        return out

    # ---------------------------------------------------------------- pull
    def pull_status(self, cfg, secret, links):
        ids = [l.external_id for l in links]
        found: dict[str, dict] = {}
        for n in range(0, len(ids), 50):
            data = _gql(secret, "query($ids: [ID!]) { issues(first: 100, filter: { id: { in: $ids } }) { nodes { %s } } }" % _ISSUE, {"ids": ids[n:n + 50]})
            for node in data["issues"]["nodes"]:
                found[node["id"]] = {"state_type": node["state"]["type"], "state_name": node["state"]["name"], "url": node["url"]}
        return found

    # ---------------------------------------------------------------- webhook
    def verify_webhook(self, cfg, secret, body, headers):
        key = secret.get("webhook_secret", "")
        if not key:
            raise IntegrationError("No webhook signing secret is saved for Linear.")
        sent = headers.get("linear-signature", "")
        good = hmac.new(key.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sent, good):
            raise IntegrationError("Signature does not match.")
        event = json.loads(body)
        ts = event.get("webhookTimestamp")
        if ts and abs(time.time() * 1000 - float(ts)) > 5 * 60 * 1000:   # replayed or badly delayed delivery
            raise IntegrationError("Webhook is too old.")
        return event
