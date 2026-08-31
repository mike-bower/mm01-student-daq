#!/usr/bin/env bash
# One-time setup for the MM01 StudentDAQ kit on a Raspberry Pi.
#
#   bash setup_pi.sh
#
# Needs internet, unless a wheelhouse/ folder is present (see README, "No
# internet on the Pi"). Safe to run more than once.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

say() { printf '\n\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\n\033[1;33m!!\033[0m %s\n' "$*"; }

say "1/5  System packages"
# python3-venv: create the virtual environment.
# libhidapi-hidraw0: the USB HID library the `hid` package talks to.
sudo apt-get update
sudo apt-get install -y python3-venv libhidapi-hidraw0

say "2/5  Python virtual environment"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip

say "3/5  Python packages"
# Versions are pinned so pip installs prebuilt aarch64 wheels and never has to
# compile anything. Do not "upgrade" them — see the note in requirements.txt.
if [ -d wheelhouse ]; then
  echo "Using the offline wheelhouse."
  ./.venv/bin/pip install --no-index --find-links wheelhouse -r requirements-dev.txt
else
  ./.venv/bin/pip install -r requirements-dev.txt
fi

say "4/5  USB permissions"
# Without this rule /dev/hidraw* is root-only, and the app finds no device even
# though lsusb shows it.
sudo cp 99-mm01.rules /etc/udev/rules.d/99-mm01.rules
sudo udevadm control --reload
sudo udevadm trigger
echo "Installed /etc/udev/rules.d/99-mm01.rules"

say "5/5  Front-end libraries"
if [ -f static/vendor/alpine.min.js ] && [ -f static/vendor/uPlot.iife.min.js ]; then
  echo "Already present — nothing to download."
else
  ./.venv/bin/python tools/vendor_assets.py
fi

say "Checking the install (no hardware needed)"
if ./.venv/bin/python -m pytest tests/ -q; then
  echo "Tests passed."
else
  warn "Tests failed — see the output above before continuing."
fi

cat <<'MSG'

Setup complete.

  Unplug and replug the MM01 now, so the new USB permissions take effect.

  Start the app:      ./run.sh
  Then open:          http://localhost:8110       (on the Pi)
                      http://<pi-ip>:8110         (from another machine)

  Find <pi-ip> with:  hostname -I
  No hardware yet?    Set MM01_SIM_ENABLED=true in .env and restart.

MSG
