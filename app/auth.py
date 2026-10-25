"""Accounts and sessions.

Users and sessions live in one auth database. Every user's workspace is its own SQLite file under
GROUNDWORK_DATA, so one account can never read another account's evidence.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("GROUNDWORK_DATA", str(ROOT / "data")))
AUTH_DB = os.environ.get("GROUNDWORK_AUTH_DB", str(DATA_DIR / "auth.db"))
COOKIE = "gw_session"
SESSION_DAYS = 30
SECURE_COOKIES = os.environ.get("GROUNDWORK_SECURE_COOKIES") == "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    api_token TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SCRYPT = (2**14, 8, 1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@contextmanager
def _conn():
    Path(AUTH_DB).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(AUTH_DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_auth() -> None:
    with _conn() as c:
        c.executescript(SCHEMA)


# ---------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    n, r, p = _SCRYPT
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return "scrypt${}${}${}${}${}".format(n, r, p, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


_DUMMY_HASH = hash_password("not-a-real-password")  # keeps login timing the same when the email is unknown


# ---------------------------------------------------------------- users

def _public(u: sqlite3.Row | None) -> dict | None:
    return {"id": u["id"], "email": u["email"], "name": u["name"], "api_token": u["api_token"]} if u else None


def create_user(email: str, password: str, name: str = "") -> dict:
    email = email.strip().lower()
    if not _EMAIL.match(email) or len(email) > 254:
        raise ValueError("Enter a valid email address.")
    if len(password) < 8:
        raise ValueError("Use at least 8 characters for your password.")
    if len(password) > 200:
        raise ValueError("That password is too long.")
    with _conn() as c:
        if c.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise ValueError("An account with this email already exists. Log in instead.")
        cur = c.execute(
            "INSERT INTO users(email, name, password_hash, api_token, created_at) VALUES (?,?,?,?,?)",
            (email, name.strip()[:80], hash_password(password), "gw_" + secrets.token_urlsafe(24), _now().isoformat(timespec="seconds")),
        )
        return _public(c.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone())


def authenticate(email: str, password: str) -> dict | None:
    with _conn() as c:
        u = c.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
    ok = verify_password(password, u["password_hash"] if u else _DUMMY_HASH)
    return _public(u) if u and ok else None


def user_by_token(api_token: str) -> dict | None:
    with _conn() as c:
        return _public(c.execute("SELECT * FROM users WHERE api_token=?", (api_token,)).fetchone())


def workspace_path(user_id: int) -> str:
    return str(DATA_DIR / "workspaces" / f"user_{int(user_id)}.db")


# ---------------------------------------------------------------- sessions

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    with _conn() as c:
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(timespec="seconds"),))
        c.execute(
            "INSERT INTO sessions(token_hash, user_id, expires_at, created_at) VALUES (?,?,?,?)",
            (_hash(token), user_id, (now + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds"), now.isoformat(timespec="seconds")),
        )
    return token


def user_by_session(token: str | None) -> dict | None:
    if not token:
        return None
    with _conn() as c:
        u = c.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at > ?",
            (_hash(token), _now().isoformat(timespec="seconds")),
        ).fetchone()
    return _public(u)


def delete_session(token: str | None) -> None:
    if token:
        with _conn() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (_hash(token),))
