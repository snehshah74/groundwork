"""Accounts and sessions.

Identity comes from Supabase Auth (Google, Microsoft, email) when SUPABASE_URL is set: the browser signs
in with Supabase, then trades the Supabase access token for a Groundwork session cookie. Without Supabase
(local development) a plain email and password login is available instead.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from . import db

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")  # only for projects still on the legacy HS256 secret
COOKIE = "gw_session"
WS_COOKIE = "gw_ws"
SESSION_DAYS = 30
SECURE_COOKIES = os.environ.get("GROUNDWORK_SECURE_COOKIES") == "1"

SHARED_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    supabase_id TEXT UNIQUE,
    password_hash TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspaces (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    created_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
    workspace_id BIGINT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner','admin','member','viewer')),
    api_token TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, user_id)
);
CREATE TABLE IF NOT EXISTS invites (
    id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin','member','viewer')),
    token_hash TEXT NOT NULL UNIQUE,
    invited_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
    expires_at TEXT NOT NULL,
    accepted_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS webhook_routes (
    token TEXT PRIMARY KEY,
    workspace_id BIGINT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    provider TEXT NOT NULL
);
"""

# Append-only, like core.MIGRATIONS.
SHARED_MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "baseline", SHARED_SCHEMA),
]

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SCRYPT = (2**14, 8, 1)
_shared_ready = False


def supabase_enabled() -> bool:
    return bool(SUPABASE_URL and SUPABASE_ANON_KEY)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="seconds")


def init_shared() -> int:
    global _shared_ready
    if _shared_ready:
        return db.schema_version(db.SHARED)
    with db.conn("public") as c:   # the schema does not exist yet, so start from one that does
        c.execute("SELECT pg_advisory_xact_lock(4242)")  # two workers starting at once must not race on CREATE SCHEMA
        c.execute(f"CREATE SCHEMA IF NOT EXISTS {db.SHARED}")
    version = db.migrate(db.SHARED, SHARED_MIGRATIONS)
    with db.conn(db.SHARED) as c:
        db.lock_down(c, db.SHARED)
    _shared_ready = True
    return version


# ---------------------------------------------------------------- passwords (local dev login)

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

def _public(u: dict | None) -> dict | None:
    return {"id": u["id"], "email": u["email"], "name": u["name"]} if u else None


def create_user(email: str, password: str, name: str = "") -> dict:
    """Local email and password account (development without Supabase)."""
    email = email.strip().lower()
    if not _EMAIL.match(email) or len(email) > 254:
        raise ValueError("Enter a valid email address.")
    if len(password) < 8:
        raise ValueError("Use at least 8 characters for your password.")
    if len(password) > 200:
        raise ValueError("That password is too long.")
    with db.conn(db.SHARED) as c:
        if c.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise ValueError("An account with this email already exists. Log in instead.")
        row = c.execute(
            "INSERT INTO users(email, name, password_hash, created_at) VALUES (?,?,?,?) RETURNING *",
            (email, name.strip()[:80], hash_password(password), _iso(_now())),
        ).fetchone()
    return _public(row)


def authenticate(email: str, password: str) -> dict | None:
    u = db.one("SELECT * FROM users WHERE email=?", (email.strip().lower(),))
    ok = verify_password(password, (u or {}).get("password_hash") or _DUMMY_HASH)
    return _public(u) if u and ok else None


def upsert_supabase_user(sub: str, email: str, name: str) -> dict:
    """Find the account for a Supabase identity, linking by email the first time it is seen."""
    email = email.strip().lower()
    with db.conn(db.SHARED) as c:
        u = c.execute("SELECT * FROM users WHERE supabase_id=?", (sub,)).fetchone()
        if not u:
            u = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if u:
                u = c.execute("UPDATE users SET supabase_id=? WHERE id=? RETURNING *", (sub, u["id"])).fetchone()
            else:
                u = c.execute(
                    "INSERT INTO users(email, name, supabase_id, created_at) VALUES (?,?,?,?) RETURNING *",
                    (email, name.strip()[:80], sub, _iso(_now())),
                ).fetchone()
    return _public(u)


def user_by_id(user_id: int) -> dict | None:
    return _public(db.one("SELECT * FROM users WHERE id=?", (user_id,)))


# ---------------------------------------------------------------- Supabase token

_jwks = None


def verify_supabase_token(token: str) -> dict:
    """Validate a Supabase access token and return its claims. Raises ValueError when it is not valid."""
    global _jwks
    if not SUPABASE_URL:
        raise ValueError("Supabase is not configured.")
    try:
        alg = jwt.get_unverified_header(token).get("alg", "")
        if alg == "HS256":
            if not SUPABASE_JWT_SECRET:
                raise ValueError("SUPABASE_JWT_SECRET is not set.")
            claims = jwt.decode(token, SUPABASE_JWT_SECRET, algorithms=["HS256"], audience="authenticated")
        else:
            _jwks = _jwks or jwt.PyJWKClient(f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json", cache_keys=True)
            key = _jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256", "ES256"], audience="authenticated")
    except jwt.PyJWTError as e:
        raise ValueError("That sign-in could not be verified. Try again.") from e
    if not claims.get("sub") or not claims.get("email"):
        raise ValueError("Your account has no email address.")
    return claims


# ---------------------------------------------------------------- sessions

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    with db.conn(db.SHARED) as c:
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (_iso(now),))
        c.execute(
            "INSERT INTO sessions(token_hash, user_id, expires_at, created_at) VALUES (?,?,?,?)",
            (_hash(token), user_id, _iso(now + timedelta(days=SESSION_DAYS)), _iso(now)),
        )
    return token


def user_by_session(token: str | None) -> dict | None:
    if not token:
        return None
    return _public(db.one(
        "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at > ?",
        (_hash(token), _iso(_now())),
    ))


def delete_session(token: str | None) -> None:
    if token:
        with db.conn(db.SHARED) as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (_hash(token),))
