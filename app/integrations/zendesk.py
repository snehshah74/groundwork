"""Zendesk: import support tickets as customer evidence.

Auth is an agent email plus an API token (Admin Center > Apps and integrations > APIs > Zendesk API). Only the
subject and first message of each ticket are imported, with emails and phone numbers masked.
"""
from __future__ import annotations

import base64
import re
from datetime import datetime, timedelta, timezone

import httpx

from .base import Field, Integration, IntegrationError, Meta, scrub

_transport: httpx.BaseTransport | None = None      # tests swap this for a fake Zendesk
MAX_PER_RUN = 200
FIRST_RUN_DAYS = 90                                   # do not pull a decade of history the first time
_SUBDOMAIN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def base_url(raw: str) -> str:
    """Accept acme or acme.zendesk.com. The subdomain is rebuilt into a zendesk.com URL, never used as a free-form host."""
    sub = (raw or "").strip().lower().removeprefix("https://").removesuffix("/").removesuffix(".zendesk.com")
    if not _SUBDOMAIN.match(sub):
        raise IntegrationError("Enter your Zendesk subdomain, for example acme for acme.zendesk.com.")
    return f"https://{sub}.zendesk.com"


def _get(secret: dict, url_or_path: str, params: dict | None = None) -> dict:
    base = base_url(secret.get("subdomain", ""))
    url = url_or_path if url_or_path.startswith("http") else base + url_or_path
    if not url.startswith(base + "/"):                # a paging link must stay on the same Zendesk account
        raise IntegrationError("Zendesk returned a link to another host, so it was not followed.")
    auth = base64.b64encode(f"{secret.get('email', '')}/token:{secret.get('api_token', '')}".encode()).decode()
    try:
        with httpx.Client(timeout=30, transport=_transport) as c:
            r = c.get(url, params=params, headers={"Authorization": f"Basic {auth}", "Accept": "application/json"})
    except httpx.HTTPError as e:
        raise IntegrationError(f"Could not reach Zendesk: {type(e).__name__}") from e
    if r.status_code in (401, 403):
        raise IntegrationError("Zendesk rejected the credentials. Check the email and API token, and that API access is enabled.")
    if r.status_code == 429:
        raise IntegrationError("Zendesk is rate limiting requests. Try again in a minute.")
    if r.status_code >= 400:
        raise IntegrationError(f"Zendesk returned HTTP {r.status_code}.")
    return r.json()


class Zendesk(Integration):
    meta = Meta(
        id="zendesk", name="Zendesk", kind="discovery", status="ready", auth="api_key",
        description="Import support tickets as evidence. Emails and phone numbers are masked.",
        fields=[
            Field("subdomain", "Zendesk subdomain", help="acme for acme.zendesk.com"),
            Field("email", "Agent email"),
            Field("api_token", "API token", secret=True),
        ],
    )

    def connect(self, credentials):
        secret = {"subdomain": base_url(credentials.get("subdomain", "")).removeprefix("https://").removesuffix(".zendesk.com"),
                  "email": (credentials.get("email") or "").strip(), "api_token": (credentials.get("api_token") or "").strip()}
        if not secret["email"] or not secret["api_token"]:
            raise IntegrationError("Enter the agent email and an API token.")
        me = _get(secret, "/api/v2/users/me.json").get("user", {})
        if me.get("role") == "end-user":
            raise IntegrationError("That account is a customer, not an agent, so it cannot read tickets.")
        return {"viewer": me.get("name") or me.get("email", ""), "targets": [], "target_id": ""}, secret

    def import_evidence(self, cfg, secret, since):
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        try:
            start = datetime.strptime(since, fmt).replace(tzinfo=timezone.utc) if since else datetime.now(timezone.utc) - timedelta(days=FIRST_RUN_DAYS)
        except ValueError:
            start = datetime.now(timezone.utc) - timedelta(days=FIRST_RUN_DAYS)
        since = start.strftime(fmt)
        # Search is strictly "after", so start one second early: tickets sharing the previous cursor's timestamp are
        # fetched again and skipped by their stable id, instead of being missed.
        url: str | None = "/api/v2/search.json"
        params: dict | None = {"query": f"type:ticket updated>{(start - timedelta(seconds=1)).strftime(fmt)}", "sort_by": "updated_at", "sort_order": "asc"}
        items: list[dict] = []
        cursor = since
        while url and len(items) < MAX_PER_RUN:
            data = _get(secret, url, params)
            for t in data.get("results", []):
                if t.get("result_type", "ticket") != "ticket":
                    continue
                text = scrub(f"{t.get('subject') or ''}\n\n{(t.get('description') or '')[:2000]}".strip())
                cursor = t.get("updated_at") or cursor
                if text:
                    items.append({"text": text, "source": "zendesk", "external_ref": f"zendesk:{t['id']}"})
                if len(items) >= MAX_PER_RUN:
                    break
            url, params = data.get("next_page"), None          # next_page is a full URL that already carries the query
        return items, cursor
