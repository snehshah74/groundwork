"""Run the scheduled sync and imports once: python -m app.cron  (for a cron job; see also POST /api/cron/run)."""
from __future__ import annotations

import sys

from . import auth, db
from .integrations import service


def main() -> int:
    if not db.DATABASE_URL:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2
    auth.init_shared()
    result = service.run_scheduled()
    print(result)
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
