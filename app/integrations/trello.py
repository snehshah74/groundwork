"""Trello: initiative -> card, stories -> items on a "Stories" checklist, status read from the list the card is in.

Auth is an API key plus a user token (https://trello.com/power-ups/admin, then Token). Trello has no workflow
states, so status comes from list names (see state_from_list). Sync is manual or scheduled; Trello webhooks need
a separate callback handshake that this version does not implement.
"""
from __future__ import annotations

import re

import httpx

from .base import Field, Integration, IntegrationError, Link, Meta, initiative_markdown

API = "https://api.trello.com/1"
_transport: httpx.BaseTransport | None = None      # tests swap this for a fake Trello
CHECKLIST = "Stories"
DESC_LIMIT = 16000                                  # Trello rejects card descriptions over 16384 characters

# Checked in this order, so "Done / Review" counts as done, not started.
_LIST_STATES = [
    ("completed", re.compile(r"\b(done|complete|completed|shipped|released|live|deployed|finished)\b")),
    ("started", re.compile(r"\b(doing|in progress|progress|wip|started|building|in review|review|testing|qa)\b")),
    ("unstarted", re.compile(r"\b(to ?do|todo|planned|next|ready|up next|sprint)\b")),
    ("backlog", re.compile(r"\b(backlog|ideas?|icebox|inbox|someday)\b")),
]


def state_from_list(name: str) -> str:
    """Map a list name like 'Doing' or 'Done' to a tracker state type; '' when the name says nothing."""
    n = (name or "").lower()
    return next((state for state, rx in _LIST_STATES if rx.search(n)), "")


def _req(secret: dict, method: str, path: str, params: dict | None = None) -> dict | list:
    q = {"key": secret.get("api_key", ""), "token": secret.get("token", ""), **(params or {})}
    try:
        with httpx.Client(timeout=20, transport=_transport) as c:
            r = c.request(method, API + path, params=q)
    except httpx.HTTPError as e:
        raise IntegrationError(f"Could not reach Trello: {type(e).__name__}") from e   # not str(e): the URL carries the credentials
    if r.status_code == 401:
        raise IntegrationError("Trello rejected the API key or token.")
    if r.status_code == 429:
        raise IntegrationError("Trello is rate limiting requests. Try again in a minute.")
    if r.status_code >= 400:
        raise IntegrationError(f"Trello said: {(r.text or f'HTTP {r.status_code}')[:200]}")
    return r.json() if r.content else {}


class Trello(Integration):
    meta = Meta(
        id="trello", name="Trello", kind="tracker", status="ready", auth="api_key", target_label="list",
        description="Create a card per initiative with its stories as a checklist, and follow the card as it moves between lists.",
        fields=[
            Field("api_key", "API key", secret=True, help="From your Trello Power-Up admin page"),
            Field("token", "Token", secret=True, help="Authorize the key to get a token"),
        ],
    )

    def connect(self, credentials):
        secret = {"api_key": (credentials.get("api_key") or "").strip(), "token": (credentials.get("token") or "").strip()}
        if not secret["api_key"] or not secret["token"]:
            raise IntegrationError("Enter both the API key and the token.")
        me = _req(secret, "GET", "/members/me", {"fields": "fullName,username"})
        boards = _req(secret, "GET", "/members/me/boards", {"filter": "open", "fields": "name", "lists": "open"})
        targets = [{"id": l["id"], "key": "", "name": f"{b['name']} / {l['name']}"} for b in boards for l in b.get("lists", [])]
        if not targets:
            raise IntegrationError("That Trello account has no open boards with lists.")
        cfg = {"viewer": me.get("fullName") or me.get("username", ""), "targets": targets, "target_id": targets[0]["id"] if len(targets) == 1 else ""}
        return cfg, secret

    # ---------------------------------------------------------------- push
    def push_initiative(self, cfg, secret, initiative, stories, links):
        list_id = cfg.get("target_id")
        if not list_id:
            raise IntegrationError("Choose which Trello list new cards should go in.")
        desc = initiative_markdown(initiative, initiative.get("problem"), len(initiative.get("evidence", [])))[:DESC_LIMIT]
        out: list[Link] = []

        card_link = links.get(("initiative", initiative["id"]))
        if card_link:
            card = _req(secret, "PUT", f"/cards/{card_link.external_id}", {"name": initiative["title"][:16000], "desc": desc})
            status = card_link.external_status
        else:
            card = _req(secret, "POST", "/cards", {"idList": list_id, "name": initiative["title"][:16000], "desc": desc})
            status = "New"
        card_id, url = card["id"], card.get("shortUrl") or card.get("url", "")
        out.append(Link("initiative", initiative["id"], card_id, "", url, status))

        if not stories:
            return out
        cl_link = links.get(("checklist", initiative["id"]))
        if cl_link:
            checklist_id = cl_link.external_id
        else:
            checklist_id = _req(secret, "POST", f"/cards/{card_id}/checklists", {"name": CHECKLIST})["id"]
        out.append(Link("checklist", initiative["id"], checklist_id, "", url, ""))
        for s in stories:
            existing = links.get(("story", s["id"]))
            if existing:
                _req(secret, "PUT", f"/cards/{card_id}/checkItem/{existing.external_id}", {"name": s["title"][:16000]})
                item_id, st = existing.external_id, existing.external_status
            else:
                item_id, st = _req(secret, "POST", f"/checklists/{checklist_id}/checkItems", {"name": s["title"][:16000]})["id"], "incomplete"
            out.append(Link("story", s["id"], item_id, "", url, st))
        return out

    # ---------------------------------------------------------------- pull
    def pull_status(self, cfg, secret, links):
        found: dict[str, dict] = {}
        for l in links:
            if l.kind != "initiative":
                continue
            card = _req(secret, "GET", f"/cards/{l.external_id}", {"fields": "name,closed,shortUrl", "list": "true"})
            if card.get("closed"):          # archived cards say nothing about progress
                continue
            name = (card.get("list") or {}).get("name", "")
            found[l.external_id] = {"state_type": state_from_list(name), "state_name": name, "url": card.get("shortUrl", "")}
        return found
