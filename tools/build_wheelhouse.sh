#!/usr/bin/env bash
# Build an offline wheelhouse for the Pi, from a PC with internet.
#
# Use this when the Raspberry Pi has no network at all. Run it on the machine
# you are copying files from (any OS/architecture — the wheels are selected for
# the Pi, not for this machine), then copy the whole kit to the USB stick.
#
#   bash tools/build_wheelhouse.sh
#
# setup_pi.sh picks up wheelhouse/ automatically.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

rm -rf wheelhouse && mkdir -p wheelhouse

# Raspberry Pi 4, 64-bit Raspberry Pi OS Bullseye = aarch64, CPython 3.9.
pip download -d wheelhouse \
  --python-version 3.9 \
  --only-binary=:all: \
  --platform manylinux2014_aarch64 \
  -r requirements-dev.txt

echo
echo "Wheelhouse built: $(ls wheelhouse | wc -l) wheels, $(du -sh wheelhouse | cut -f1)"
echo "Copy the whole kit folder to the USB stick; setup_pi.sh will use it."
