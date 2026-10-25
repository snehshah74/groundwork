"""Connections, sync links, push, pull and webhooks for the current workspace."""
from __future__ import annotations

import json
import secrets
from dataclasses import asdict

from .. import core, db
from . import catalog, crypto
from .base import Integration, IntegrationError, Link, next_status


def _integration(provider: str) -> Integration:
    integ = catalog.get(provider)
    if not integ:
        raise IntegrationError("That integration is not available yet.")
    return integ


# ---------------------------------------------------------------- connections

def _row(provider: str) -> dict | None:
    return core.one("SELECT * FROM connections WHERE provider=?", (provider,))


def _load(provider: str) -> tuple[dict, dict, dict]:
    row = _row(provider)
    if not row:
        raise IntegrationError("Connect this integration first.")
    try:
        secret = json.loads(crypto.decrypt(row["secret"]))
    except crypto.SecretKeyMissing as e:
        raise IntegrationError(str(e)) from e
    return json.loads(row["config"]), secret, row


def _store(provider: str, cfg: dict, secret: dict, user_id: int | None, webhook_token: str | None = None) -> None:
    try:
        enc = crypto.encrypt(json.dumps(secret))
    except crypto.SecretKeyMissing as e:
        raise IntegrationError(str(e)) from e
    with core.conn() as c:
        c.execute(
            """INSERT INTO connections(provider, status, config, secret, webhook_token, connected_by, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(provider) DO UPDATE SET status='connected', config=excluded.config, secret=excluded.secret,
                 webhook_token=COALESCE(connections.webhook_token, excluded.webhook_token), updated_at=excluded.updated_at""",
            (provider, "connected", json.dumps(cfg), enc, webhook_token, user_id, core.now(), core.now()),
        )


def connect(provider: str, credentials: dict, user_id: int, workspace_id: int) -> dict:
    integ = _integration(provider)
    cfg, secret = integ.connect(credentials)
    token = (_row(provider) or {}).get("webhook_token") or secrets.token_urlsafe(24)
    existing = _row(provider)
    if existing:  # reconnecting keeps the saved webhook secret and chosen team
        old_cfg, old_secret, _ = _load(provider)
        cfg["team_id"] = cfg.get("team_id") or old_cfg.get("team_id", "")
        if old_secret.get("webhook_secret"):
            secret["webhook_secret"] = old_secret["webhook_secret"]
    _store(provider, cfg, secret, user_id, token)
    with db.conn(db.SHARED) as c:
        c.execute("INSERT INTO webhook_routes(token, workspace_id, provider) VALUES (?,?,?) ON CONFLICT DO NOTHING", (token, workspace_id, provider))
    log(provider, "push", True, "Connected")
    return public_state(provider)


def update_settings(provider: str, team_id: str | None = None, webhook_secret: str | None = None) -> dict:
    cfg, secret, row = _load(provider)
    if team_id is not None:
        if team_id and not any(t["id"] == team_id for t in cfg.get("teams", [])):
            raise IntegrationError("That team is not available to this connection.")
        cfg["team_id"] = team_id
    if webhook_secret is not None:
        secret["webhook_secret"] = webhook_secret.strip()
    _store(provider, cfg, secret, row["connected_by"])
    return public_state(provider)


def disconnect(provider: str) -> None:
    row = _row(provider)
    with core.conn() as c:
        c.execute("DELETE FROM connections WHERE provider=?", (provider,))
        c.execute("DELETE FROM sync_links WHERE provider=?", (provider,))
    if row and row["webhook_token"]:
        with db.conn(db.SHARED) as c:
            c.execute("DELETE FROM webhook_routes WHERE token=?", (row["webhook_token"],))


def test(provider: str) -> dict:
    cfg, secret, _ = _load(provider)
    new_cfg, _ = _integration(provider).connect({"api_key": secret.get("api_key", ""), "access_token": secret.get("access_token")})
    cfg["teams"] = new_cfg.get("teams", cfg.get("teams"))
    _store(provider, cfg, secret, None)
    return public_state(provider)


def public_state(provider: str) -> dict:
    """What the UI may see: never the credentials."""
    meta = next((m for m in catalog.all_metas() if m.id == provider), None)
    row = _row(provider)
    out = {"id": meta.id, "name": meta.name, "kind": meta.kind, "description": meta.description, "status": meta.status,
           "auth": meta.auth, "fields": [asdict(f) for f in meta.fields], "connected": bool(row)}
    if row:
        cfg = json.loads(row["config"])
        try:
            has_whsec = bool(json.loads(crypto.decrypt(row["secret"])).get("webhook_secret"))
        except crypto.SecretKeyMissing:
            has_whsec = False
        out.update({"config": {"account": cfg.get("viewer", ""), "teams": cfg.get("teams", []), "team_id": cfg.get("team_id", "")},
                    "webhook_token": row["webhook_token"], "has_webhook_secret": has_whsec,
                    "last_log": core.one("SELECT direction, ok, summary, created_at FROM sync_log WHERE provider=? ORDER BY id DESC LIMIT 1", (provider,))})
    return out


def catalog_state() -> list[dict]:
    return [public_state(m.id) for m in catalog.all_metas()]


# ---------------------------------------------------------------- links and log

def log(provider: str, direction: str, ok: bool, summary: str) -> None:
    with core.conn() as c:
        c.execute("INSERT INTO sync_log(provider, direction, ok, summary, created_at) VALUES (?,?,?,?,?)", (provider, direction, ok, summary[:500], core.now()))


def recent_log(provider: str, limit: int = 20) -> list[dict]:
    return core.rows("SELECT direction, ok, summary, created_at FROM sync_log WHERE provider=? ORDER BY id DESC LIMIT ?", (provider, limit))


def links_for(initiative_id: int) -> list[dict]:
    story_ids = [s["id"] for s in core.list_stories(initiative_id)]
    out = core.rows("SELECT * FROM sync_links WHERE kind='initiative' AND local_id=? ORDER BY id", (initiative_id,))
    if story_ids:
        out += core.rows("SELECT * FROM sync_links WHERE kind='story' AND local_id = ANY(?) ORDER BY id", (story_ids,))
    return out


def _links(provider: str) -> list[Link]:
    return [Link(r["kind"], r["local_id"], r["external_id"], r["external_key"], r["external_url"], r["external_status"])
            for r in core.rows("SELECT * FROM sync_links WHERE provider=?", (provider,))]


def _save_links(provider: str, links: list[Link]) -> None:
    with core.conn() as c:
        for l in links:
            c.execute(
                """INSERT INTO sync_links(provider, kind, local_id, external_id, external_key, external_url, external_status, synced_at)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(provider, kind, local_id) DO UPDATE SET external_id=excluded.external_id, external_key=excluded.external_key,
                     external_url=excluded.external_url, external_status=excluded.external_status, synced_at=excluded.synced_at""",
                (provider, l.kind, l.local_id, l.external_id, l.external_key, l.external_url, l.external_status, core.now()),
            )


# ---------------------------------------------------------------- push / pull

def push(provider: str, initiative_id: int) -> dict:
    integ = _integration(provider)
    cfg, secret, _ = _load(provider)
    i = core.get_initiative(initiative_id)
    if not i:
        raise LookupError("Initiative not found")
    i["problem"] = core.get_problem(i["problem_id"])
    existing = {(l.kind, l.local_id): l for l in _links(provider)}
    try:
        links = integ.push_initiative(cfg, secret, i, i["stories"], existing)
    except IntegrationError as e:
        log(provider, "push", False, f"{i['title']}: {e}")
        raise
    _save_links(provider, links)
    core.record_dispatch(initiative_id, provider)
    created = sum(1 for l in links if (l.kind, l.local_id) not in existing)
    log(provider, "push", True, f"{i['title']}: {created} created, {len(links) - created} updated")
    return {"links": [asdict(l) for l in links], "created": created, "updated": len(links) - created}


def _apply(provider: str, link: Link, state_type: str, state_name: str, url: str = "") -> str | None:
    """Record a tracker state on a link and, for initiatives, move Groundwork status to match. Returns new status."""
    with core.conn() as c:
        c.execute("UPDATE sync_links SET external_status=?, external_url=COALESCE(NULLIF(?, ''), external_url), synced_at=? WHERE provider=? AND kind=? AND local_id=?",
                  (state_name, url, core.now(), provider, link.kind, link.local_id))
    if link.kind != "initiative":
        return None
    cur = core.one("SELECT status FROM initiatives WHERE id=?", (link.local_id,))
    new = next_status(cur["status"], state_type) if cur else None
    if new:
        core.set_status(link.local_id, new)
    return new


def sync(provider: str) -> dict:
    """Pull current tracker state for every linked item."""
    integ = _integration(provider)
    cfg, secret, _ = _load(provider)
    links = _links(provider)
    if not links:
        return {"checked": 0, "changed": 0}
    try:
        states = integ.pull_status(cfg, secret, links)
    except IntegrationError as e:
        log(provider, "pull", False, str(e))
        raise
    changed = 0
    for l in links:
        s = states.get(l.external_id)
        if s and _apply(provider, l, s["state_type"], s["state_name"], s.get("url", "")):
            changed += 1
    log(provider, "pull", True, f"Checked {len(links)} items, {changed} initiatives changed status")
    return {"checked": len(links), "changed": changed}


# ---------------------------------------------------------------- webhooks (no session: the URL token picks the workspace)

def handle_webhook(provider: str, token: str, body: bytes, headers: dict) -> dict:
    route = db.one("SELECT workspace_id, provider FROM webhook_routes WHERE token=?", (token,))
    if not route or route["provider"] != provider:
        raise LookupError("unknown webhook")
    schema = db.schema_name(route["workspace_id"])
    core.init_workspace(schema)
    db.use_schema(schema)
    cfg, secret, _ = _load(provider)
    try:
        event = _integration(provider).verify_webhook(cfg, secret, body, headers)
    except IntegrationError as e:
        log(provider, "pull", False, f"Rejected webhook: {e}")
        raise PermissionError(str(e)) from e
    data = event.get("data") or {}
    if event.get("type") != "Issue" or not data.get("id") or not data.get("state"):
        return {"ignored": True}
    row = core.one("SELECT * FROM sync_links WHERE provider=? AND external_id=?", (provider, data["id"]))
    if not row:
        return {"ignored": True}
    link = Link(row["kind"], row["local_id"], row["external_id"])
    new = _apply(provider, link, data["state"].get("type", ""), data["state"].get("name", ""), event.get("url", ""))
    log(provider, "pull", True, f"{row['external_key'] or data['id']} is now {data['state'].get('name')}" + (f"; initiative moved to {new}" if new else ""))
    return {"ok": True, "status": new}
