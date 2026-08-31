#!/usr/bin/env bash
# Re-copy the MM01 driver from the parent System 8000 API project.
#
# This kit holds a frozen copy of the driver so it stays stable for teaching.
# If the driver is fixed upstream, run this to pull the change across, then
# re-run the tests.
#
#   bash tools/sync_bridge.sh [path-to-system_8000_api]

set -euo pipefail
SRC="${1:-$HOME/vpg/system_8000_api}"
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -d "$SRC/app/mm01_bridge" ]; then
  echo "Parent project not found at: $SRC"
  echo "Usage: bash tools/sync_bridge.sh /path/to/system_8000_api"
  exit 1
fi

cp "$SRC"/app/mm01_bridge/*.py        "$KIT"/app/mm01_bridge/
cp "$SRC"/app/models/mm01_models.py   "$KIT"/app/models/
cp "$SRC"/app/routers/mm01.py         "$KIT"/app/routers/
cp "$SRC"/static/js/mm01.js           "$KIT"/static/js/
cp "$SRC"/tests/test_mm01_protocol.py "$KIT"/tests/
cp "$SRC"/tests/test_api_mm01.py      "$KIT"/tests/

echo "Driver synced from $SRC"
echo "Now run:  python3 -m pytest tests/ -q"
