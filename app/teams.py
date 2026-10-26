"""Workspaces, members, roles and invites.

Roles, from most to least access:
  owner   everything, including deleting members and changing owners
  admin   manage members, invites and integrations
  member  create and edit product data
  viewer  read only
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from . import core, db

ROLES = ["viewer", "member", "admin", "owner"]
INVITE_ROLES = ["admin", "member", "viewer"]
INVITE_DAYS = 7


def rank(role: str) -> int:
    return ROLES.index(role) if role in ROLES else -1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="seconds")


def _token() -> str:
    return "gw_" + secrets.token_urlsafe(24)


# ---------------------------------------------------------------- workspaces

def create_workspace(user_id: int, name: str) -> dict:
    name = name.strip()[:80]
    if not name:
        raise ValueError("Give the workspace a name.")
    with db.conn(db.SHARED) as c:
        ws = c.execute("INSERT INTO workspaces(name, created_by, created_at) VALUES (?,?,?) RETURNING *", (name, user_id, _iso(_now()))).fetchone()
        c.execute(
            "INSERT INTO members(workspace_id, user_id, role, api_token, created_at) VALUES (?,?,?,?,?)",
            (ws["id"], user_id, "owner", _token(), _iso(_now())),
        )
    core.init_workspace(db.schema_name(ws["id"]))
    return {"id": ws["id"], "name": ws["name"], "role": "owner"}


def workspaces_for(user_id: int) -> list[dict]:
    return db.rows(
        "SELECT w.id, w.name, m.role FROM members m JOIN workspaces w ON w.id=m.workspace_id WHERE m.user_id=? ORDER BY w.id",
        (user_id,),
    )


def membership(user_id: int, workspace_id: int) -> dict | None:
    return db.one(
        "SELECT w.id, w.name, m.role, m.api_token FROM members m JOIN workspaces w ON w.id=m.workspace_id "
        "WHERE m.user_id=? AND m.workspace_id=?",
        (user_id, workspace_id),
    )


def rename_workspace(workspace_id: int, name: str) -> None:
    name = name.strip()[:80]
    if not name:
        raise ValueError("Give the workspace a name.")
    with db.conn(db.SHARED) as c:
        c.execute("UPDATE workspaces SET name=? WHERE id=?", (name, workspace_id))


def delete_workspace(workspace_id: int) -> None:
    with db.conn(db.SHARED) as c:
        c.execute("DELETE FROM workspaces WHERE id=?", (workspace_id,))
    core.drop_workspace(db.schema_name(workspace_id))


def member_by_token(api_token: str) -> dict | None:
    """Resolve an agent token to its user, workspace and role."""
    if not api_token:
        return None
    return db.one(
        "SELECT m.user_id, m.workspace_id, m.role, u.email FROM members m JOIN users u ON u.id=m.user_id WHERE m.api_token=?",
        (api_token,),
    )


# ---------------------------------------------------------------- members

def list_members(workspace_id: int) -> list[dict]:
    return db.rows(
        "SELECT u.id AS user_id, u.email, u.name, m.role, m.created_at FROM members m JOIN users u ON u.id=m.user_id "
        "WHERE m.workspace_id=? ORDER BY m.created_at, u.id",
        (workspace_id,),
    )


def _owner_count(c, workspace_id: int) -> int:
    return c.execute("SELECT COUNT(*) AS n FROM members WHERE workspace_id=? AND role='owner'", (workspace_id,)).fetchone()["n"]


def set_role(workspace_id: int, user_id: int, role: str, actor_role: str) -> None:
    if role not in ROLES:
        raise ValueError("Unknown role.")
    if role == "owner" and actor_role != "owner":
        raise ValueError("Only an owner can make someone an owner.")
    with db.conn(db.SHARED) as c:
        cur = c.execute("SELECT role FROM members WHERE workspace_id=? AND user_id=?", (workspace_id, user_id)).fetchone()
        if not cur:
            raise LookupError("That person is not in this workspace.")
        if cur["role"] == "owner" and actor_role != "owner":
            raise ValueError("Only an owner can change an owner's role.")
        if cur["role"] == "owner" and role != "owner" and _owner_count(c, workspace_id) <= 1:
            raise ValueError("A workspace needs at least one owner.")
        c.execute("UPDATE members SET role=? WHERE workspace_id=? AND user_id=?", (role, workspace_id, user_id))


def remove_member(workspace_id: int, user_id: int, actor_role: str) -> None:
    with db.conn(db.SHARED) as c:
        cur = c.execute("SELECT role FROM members WHERE workspace_id=? AND user_id=?", (workspace_id, user_id)).fetchone()
        if not cur:
            raise LookupError("That person is not in this workspace.")
        if cur["role"] == "owner":
            if actor_role != "owner":
                raise ValueError("Only an owner can remove an owner.")
            if _owner_count(c, workspace_id) <= 1:
                raise ValueError("A workspace needs at least one owner.")
        c.execute("DELETE FROM members WHERE workspace_id=? AND user_id=?", (workspace_id, user_id))


def rotate_token(workspace_id: int, user_id: int) -> str:
    token = _token()
    with db.conn(db.SHARED) as c:
        c.execute("UPDATE members SET api_token=? WHERE workspace_id=? AND user_id=?", (token, workspace_id, user_id))
    return token


# ---------------------------------------------------------------- invites

def create_invite(workspace_id: int, email: str, role: str, invited_by: int) -> dict:
    email = email.strip().lower()
    if "@" not in email or len(email) > 254:
        raise ValueError("Enter a valid email address.")
    if role not in INVITE_ROLES:
        raise ValueError("Choose admin, member or viewer.")
    with db.conn(db.SHARED) as c:
        if c.execute(
            "SELECT 1 FROM members m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? AND u.email=?", (workspace_id, email)
        ).fetchone():
            raise ValueError("That person is already in this workspace.")
        c.execute("DELETE FROM invites WHERE workspace_id=? AND email=? AND accepted_at IS NULL", (workspace_id, email))
        raw = secrets.token_urlsafe(24)
        row = c.execute(
            "INSERT INTO invites(workspace_id, email, role, token_hash, invited_by, expires_at, created_at) "
            "VALUES (?,?,?,?,?,?,?) RETURNING id, email, role, expires_at",
            (workspace_id, email, role, hashlib.sha256(raw.encode()).hexdigest(), invited_by, _iso(_now() + timedelta(days=INVITE_DAYS)), _iso(_now())),
        ).fetchone()
    return {**row, "token": raw}


def list_invites(workspace_id: int) -> list[dict]:
    return db.rows(
        "SELECT id, email, role, expires_at, created_at FROM invites WHERE workspace_id=? AND accepted_at IS NULL AND expires_at > ? ORDER BY id DESC",
        (workspace_id, _iso(_now())),
    )


def revoke_invite(workspace_id: int, invite_id: int) -> bool:
    with db.conn(db.SHARED) as c:
        return c.execute("DELETE FROM invites WHERE id=? AND workspace_id=? AND accepted_at IS NULL", (invite_id, workspace_id)).rowcount > 0


def accept_invite(raw_token: str, user: dict) -> dict:
    """Join the workspace an invite was issued for. The signed-in email must match the invited email."""
    h = hashlib.sha256(raw_token.encode()).hexdigest()
    with db.conn(db.SHARED) as c:
        inv = c.execute(
            "SELECT i.*, w.name AS workspace_name FROM invites i JOIN workspaces w ON w.id=i.workspace_id WHERE i.token_hash=?", (h,)
        ).fetchone()
        if not inv or inv["accepted_at"] or inv["expires_at"] < _iso(_now()):
            raise ValueError("This invite link is invalid or has expired. Ask for a new one.")
        if inv["email"] != user["email"].lower():
            raise PermissionError(f"This invite was sent to {inv['email']}. Sign in with that email to accept it.")
        c.execute(
            "INSERT INTO members(workspace_id, user_id, role, api_token, created_at) VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING",
            (inv["workspace_id"], user["id"], inv["role"], _token(), _iso(_now())),
        )
        c.execute("UPDATE invites SET accepted_at=? WHERE id=?", (_iso(_now()), inv["id"]))
    return {"id": inv["workspace_id"], "name": inv["workspace_name"], "role": inv["role"]}


def invite_preview(raw_token: str) -> dict | None:
    h = hashlib.sha256(raw_token.encode()).hexdigest()
    return db.one(
        "SELECT i.email, i.role, w.name AS workspace_name FROM invites i JOIN workspaces w ON w.id=i.workspace_id "
        "WHERE i.token_hash=? AND i.accepted_at IS NULL AND i.expires_at > ?",
        (h, _iso(_now())),
    )


# ---------------------------------------------------------------- audit log

def audit(workspace_id: int, user: dict | None, action: str, detail: str = "") -> None:
    with db.conn(db.SHARED) as c:
        c.execute(
            "INSERT INTO audit_log(workspace_id, user_id, actor, action, detail, created_at) VALUES (?,?,?,?,?,?)",
            (workspace_id, user["id"] if user else None, user["email"] if user else "system", action, detail[:500], _iso(_now())),
        )


def list_audit(workspace_id: int, limit: int = 100) -> list[dict]:
    return db.rows("SELECT id, actor AS email, action, detail, created_at FROM audit_log WHERE workspace_id=? ORDER BY id DESC LIMIT ?", (workspace_id, limit))


def get_member(workspace_id: int, user_id: int) -> dict | None:
    return db.one("SELECT u.email, m.role FROM members m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? AND m.user_id=?", (workspace_id, user_id))


# ---------------------------------------------------------------- account deletion

def delete_user(user_id: int) -> None:
    """Delete an account. Workspaces only they belonged to are deleted with it. A workspace others still use needs
    another owner first, so deleting an account can never leave a team without one."""
    mine = db.rows("SELECT m.workspace_id, w.name, m.role FROM members m JOIN workspaces w ON w.id=m.workspace_id WHERE m.user_id=?", (user_id,))
    stuck, solo = [], []
    for w in mine:
        others = db.one("SELECT count(*) AS n FROM members WHERE workspace_id=? AND user_id<>?", (w["workspace_id"], user_id))["n"]
        if others == 0:
            solo.append(w)
        elif w["role"] == "owner" and not db.one("SELECT 1 AS x FROM members WHERE workspace_id=? AND user_id<>? AND role='owner'", (w["workspace_id"], user_id)):
            stuck.append(w["name"])
    if stuck:
        raise ValueError("Make someone else an owner of " + ", ".join(f'"{n}"' for n in stuck) + " first, or remove the other members.")
    for w in solo:
        delete_workspace(w["workspace_id"])
    with db.conn(db.SHARED) as c:
        c.execute("DELETE FROM users WHERE id=?", (user_id,))
