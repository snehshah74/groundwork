"""Encrypts integration credentials at rest. Set GROUNDWORK_SECRET_KEY to a long random string and keep it safe:
losing it means every connection has to be re-created."""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


class SecretKeyMissing(RuntimeError):
    pass


def _fernet() -> Fernet:
    raw = os.environ.get("GROUNDWORK_SECRET_KEY", "")
    if len(raw) < 16:
        raise SecretKeyMissing("Set GROUNDWORK_SECRET_KEY (16+ characters) before connecting an integration.")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()))


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise SecretKeyMissing("Stored credentials cannot be read. GROUNDWORK_SECRET_KEY changed, so reconnect this integration.") from e
