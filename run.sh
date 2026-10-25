#!/usr/bin/env bash
# Start Groundwork on http://127.0.0.1:8000
set -e
cd "$(dirname "$0")"
if [ -z "$ANTHROPIC_API_KEY" ]; then
  echo "Note: ANTHROPIC_API_KEY is not set. Running in offline mode (rough clustering, template specs)."
fi
exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
