#!/usr/bin/env bash
# Start Groundwork on http://127.0.0.1:8000 (or PORT=8001 bash run.sh)
set -e
cd "$(dirname "$0")"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
if [ -z "$DATABASE_URL" ]; then
  echo "DATABASE_URL is not set. Copy .env.example to .env and add your Supabase (or other Postgres) connection string." >&2
  exit 1
fi
if [ -z "$ANTHROPIC_API_KEY" ]; then
  echo "Note: ANTHROPIC_API_KEY is not set. Running in offline mode (rough clustering, template specs)."
fi
if [ -z "$SUPABASE_URL" ]; then
  echo "Note: SUPABASE_URL is not set. Using the local email and password login (development only)."
fi
PORT="${PORT:-8000}"
if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is already in use by:" >&2
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2
  echo "Stop that program (kill <PID>, or kill -9 <PID> if it will not stop), or use another port:  PORT=8001 bash run.sh" >&2
  exit 1
fi
echo "Open http://127.0.0.1:$PORT"
exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
