#!/usr/bin/env bash
# Start the MM01 StudentDAQ app.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if [ ! -x .venv/bin/uvicorn ]; then
  echo "Not set up yet. Run:  bash setup_pi.sh"
  exit 1
fi

echo "MM01 StudentDAQ  ->  http://localhost:8110    (Ctrl-C to stop)"
exec ./.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8110
