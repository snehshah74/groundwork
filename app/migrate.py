"""Upgrade every schema to the latest migration: python -m app.migrate

Workspaces also upgrade themselves on first use after a deploy, so this is for doing it ahead of time
(for example in a release step) and for seeing which workspaces were behind.
"""
from __future__ import annotations

import sys

from . import auth, core, db


def main() -> int:
    if not db.DATABASE_URL:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2
    shared = auth.init_shared()
    print(f"shared ({db.SHARED}): version {shared}")
    moved = 0
    for ws in db.rows("SELECT id FROM workspaces ORDER BY id"):
        schema = db.schema_name(ws["id"])
        before = db.schema_version(schema)
        after = core.init_workspace(schema)
        if after != before:
            moved += 1
        print(f"{schema}: {before} -> {after}")
    print(f"done, {moved} workspace(s) upgraded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
