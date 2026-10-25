"""Jira Cloud: initiative -> Epic, stories -> Stories under it, status read back by webhook or sync.

Auth is the account email plus an API token (https://id.atlassian.com/manage-profile/security/api-tokens).
Jira Cloud only: the site must be https://<name>.atlassian.net. Data Center / Server is not supported.
REST v3 docs: https://developer.atlassian.com/cloud/jira/platform/rest/v3/
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from urllib.parse import urlparse

import httpx

from .base import Field, Integration, IntegrationError, Link, Meta, initiative_markdown, story_markdown

_transport: httpx.BaseTransport | None = None      # tests swap this for a fake Jira
EPIC, STORY = "Epic", "Story"
CATEGORY_TO_STATE = {"new": "unstarted", "indeterminate": "started", "done": "completed"}
_KEY = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")


def normalize_site(raw: str) -> str:
    """Accept acme, acme.atlassian.net or https://acme.atlassian.net/ and nothing else.

    The server makes requests to this address with the user's credentials, so anything that is not a plain
    atlassian.net host (an internal IP, localhost, a lookalike, a URL with embedded credentials) is refused.
    """
    raw = (raw or "").strip()
    if not raw:
        raise IntegrationError("Enter your Jira site, for example acme.atlassian.net.")
    if "://" not in raw:
        raw = "https://" + (raw if "." in raw else f"{raw}.atlassian.net")
    u = urlparse(raw)
    host = (u.hostname or "").lower()
    ok = (u.scheme == "https" and u.username is None and u.password is None and u.port in (None, 443)
          and re.fullmatch(r"[a-z0-9][a-z0-9-]*\.atlassian\.net", host) is not None and u.path in ("", "/") and not u.query)
    if not ok:
        raise IntegrationError("That does not look like a Jira Cloud site. Use the address ending in .atlassian.net.")
    return f"https://{host}"


def _req(secret: dict, method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict:
    site = normalize_site(secret.get("site", ""))
    auth = base64.b64encode(f"{secret.get('email', '')}:{secret.get('api_token', '')}".encode()).decode()
    try:
        with httpx.Client(timeout=20, transport=_transport) as c:
            r = c.request(method, site + path, params=params, json=body,
                          headers={"Authorization": f"Basic {auth}", "Accept": "application/json", "Content-Type": "application/json"})
    except httpx.HTTPError as e:
        raise IntegrationError(f"Could not reach Jira: {e}") from e
    if r.status_code in (401, 403):
        raise IntegrationError("Jira rejected the credentials. Check the email and API token, and that the account can use this project.")
    if r.status_code == 429:
        raise IntegrationError("Jira is rate limiting requests. Try again in a minute.")
    if r.status_code >= 400:
        try:
            data = r.json()
            detail = "; ".join(data.get("errorMessages", []) + [f"{k}: {v}" for k, v in (data.get("errors") or {}).items()])
        except ValueError:
            detail = ""
        raise IntegrationError(f"Jira said: {detail or f'HTTP {r.status_code}'}")
    return r.json() if r.content else {}


# ---------------------------------------------------------------- Markdown -> Atlassian Document Format

def _inline(text: str) -> list[dict]:
    """**bold** becomes a strong mark; a whole line wrapped in _underscores_ becomes italic."""
    em = len(text) > 2 and text.startswith("_") and text.endswith("_")
    if em:
        text = text[1:-1]
    nodes = []
    for n, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if not part:
            continue
        marks = ([{"type": "strong"}] if n % 2 else []) + ([{"type": "em"}] if em else [])
        node = {"type": "text", "text": part}
        if marks:
            node["marks"] = marks
        nodes.append(node)
    return nodes or [{"type": "text", "text": " "}]


def to_adf(markdown: str) -> dict:
    content: list[dict] = []
    bullets: list[dict] = []

    def flush():
        nonlocal bullets
        if bullets:
            content.append({"type": "bulletList", "content": bullets})
            bullets = []

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush()
            content.append({"type": "heading", "attrs": {"level": min(len(m.group(1)) + 1, 6)}, "content": _inline(m.group(2))})
        elif re.match(r"^\s*-\s+", line):
            item = re.sub(r"^\s*-\s+", "", line)
            item = re.sub(r"^\[ \]\s*", "☐ ", item)      # checkbox characters read fine in a bullet; real task lists need ids
            bullets.append({"type": "listItem", "content": [{"type": "paragraph", "content": _inline(item)}]})
        else:
            flush()
            content.append({"type": "paragraph", "content": _inline(line)})
    flush()
    return {"type": "doc", "version": 1, "content": content or [{"type": "paragraph", "content": [{"type": "text", "text": " "}]}]}


class Jira(Integration):
    meta = Meta(
        id="jira", name="Jira", kind="tracker", status="ready", auth="api_key", target_label="project", webhooks=True,
        description="Create an Epic per initiative with its stories as Stories, and keep status in sync.",
        webhook_help="In Jira, open Settings > System > WebHooks, create a webhook for Issue created and updated, and set a secret.",
        fields=[
            Field("site", "Jira site", help="For example acme.atlassian.net"),
            Field("email", "Account email"),
            Field("api_token", "API token", secret=True, help="id.atlassian.com > Security > API tokens"),
        ],
    )

    def connect(self, credentials):
        secret = {"site": normalize_site(credentials.get("site", "")), "email": (credentials.get("email") or "").strip(),
                  "api_token": (credentials.get("api_token") or "").strip()}
        if not secret["email"] or not secret["api_token"]:
            raise IntegrationError("Enter the account email and an API token.")
        me = _req(secret, "GET", "/rest/api/3/myself")
        projects = _req(secret, "GET", "/rest/api/3/project/search", params={"maxResults": 100, "orderBy": "name"}).get("values", [])
        if not projects:
            raise IntegrationError("That account cannot see any Jira projects.")
        targets = [{"id": p["key"], "key": p["key"], "name": p["name"]} for p in projects]
        cfg = {"viewer": me.get("displayName") or me.get("emailAddress", ""), "site": secret["site"], "targets": targets,
               "target_id": targets[0]["id"] if len(targets) == 1 else ""}
        return cfg, secret

    # ---------------------------------------------------------------- push
    def push_initiative(self, cfg, secret, initiative, stories, links):
        project = cfg.get("target_id")
        if not project:
            raise IntegrationError("Choose which Jira project should receive this work.")
        site = secret["site"]
        out: list[Link] = []

        def upsert(existing: Link | None, summary: str, markdown: str, issue_type: str, parent_key: str | None) -> Link:
            fields = {"summary": summary[:255], "description": to_adf(markdown)}
            if existing:
                _req(secret, "PUT", f"/rest/api/3/issue/{existing.external_key}", {"fields": fields})
                return existing
            fields.update({"project": {"key": project}, "issuetype": {"name": issue_type}})
            if parent_key:
                fields["parent"] = {"key": parent_key}
            made = _req(secret, "POST", "/rest/api/3/issue", {"fields": fields})
            return Link("", 0, str(made["id"]), made["key"], f"{site}/browse/{made['key']}", "To Do")

        parent_link = links.get(("initiative", initiative["id"]))
        epic = upsert(parent_link, initiative["title"], initiative_markdown(initiative, initiative.get("problem"), len(initiative.get("evidence", []))), EPIC, None)
        out.append(Link("initiative", initiative["id"], epic.external_id, epic.external_key, epic.external_url, epic.external_status))
        for s in stories:
            child = upsert(links.get(("story", s["id"])), s["title"], story_markdown(s), STORY, epic.external_key)
            out.append(Link("story", s["id"], child.external_id, child.external_key, child.external_url, child.external_status))
        return out

    # ---------------------------------------------------------------- pull
    def pull_status(self, cfg, secret, links):
        keys = [l.external_key for l in links if _KEY.match(l.external_key)]     # keys go into JQL: only well-formed ones
        found: dict[str, dict] = {}
        for n in range(0, len(keys), 50):
            data = _req(secret, "POST", "/rest/api/3/search/jql",
                        {"jql": f"key in ({', '.join(keys[n:n + 50])})", "fields": ["status"], "maxResults": 100})
            for issue in data.get("issues", []):
                st = (issue.get("fields") or {}).get("status") or {}
                found[str(issue["id"])] = {
                    "state_type": CATEGORY_TO_STATE.get((st.get("statusCategory") or {}).get("key", ""), ""),
                    "state_name": st.get("name", ""), "url": f"{secret['site']}/browse/{issue['key']}"}
        return found

    # ---------------------------------------------------------------- webhook
    def verify_webhook(self, cfg, secret, body, headers):
        key = secret.get("webhook_secret", "")
        if not key:
            raise IntegrationError("No webhook secret is saved for Jira.")
        sent = headers.get("x-hub-signature", "").removeprefix("sha256=")
        if not hmac.compare_digest(sent, hmac.new(key.encode(), body, hashlib.sha256).hexdigest()):
            raise IntegrationError("Signature does not match.")
        event = json.loads(body)
        ts = event.get("timestamp")
        if ts and abs(time.time() * 1000 - float(ts)) > 10 * 60 * 1000:      # Jira retries with backoff, so allow a wider window
            raise IntegrationError("Webhook is too old.")
        return event

    def parse_event(self, event):
        issue = event.get("issue") or {}
        status = (issue.get("fields") or {}).get("status") or {}
        if event.get("webhookEvent") not in ("jira:issue_updated", "jira:issue_created") or not issue.get("id") or not status:
            return None
        base = (issue.get("self") or "").split("/rest/")[0]
        return {"external_id": str(issue["id"]), "key": issue.get("key", ""), "state_name": status.get("name", ""),
                "state_type": CATEGORY_TO_STATE.get((status.get("statusCategory") or {}).get("key", ""), ""),
                "url": f"{base}/browse/{issue.get('key', '')}" if base.startswith("https://") else ""}
