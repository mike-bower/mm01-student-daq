#!/usr/bin/env python3
"""
Download the two front-end libraries into static/vendor/ so the app runs with
no internet connection.

The page loads Alpine.js (reactivity) and uPlot (the strip chart) from local
files. Without them you get an unstyled, non-interactive page and no chart.

Run once, on a machine with internet:

    python3 tools/vendor_assets.py

Uses only the standard library, so it works before `pip install`.
"""

import hashlib
import sys
import urllib.request
from pathlib import Path

VENDOR = Path(__file__).resolve().parents[1] / "static" / "vendor"

ASSETS = {
    "alpine.min.js":
        "https://cdn.jsdelivr.net/npm/alpinejs@3.13.10/dist/cdn.min.js",
    "uPlot.iife.min.js":
        "https://cdn.jsdelivr.net/npm/uplot@1.6.28/dist/uPlot.iife.min.js",
    "uPlot.min.css":
        "https://cdn.jsdelivr.net/npm/uplot@1.6.28/dist/uPlot.min.css",
}


def main() -> int:
    VENDOR.mkdir(parents=True, exist_ok=True)
    failed = []

    for name, url in ASSETS.items():
        dest = VENDOR / name
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                data = resp.read()
        except Exception as exc:
            print(f"  FAILED  {name}: {exc}")
            failed.append(name)
            continue

        if not data:
            print(f"  FAILED  {name}: empty response")
            failed.append(name)
            continue

        dest.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()[:12]
        print(f"  ok      {name:22} {len(data):>8,} bytes  sha256:{digest}")

    if failed:
        print(f"\n{len(failed)} asset(s) could not be downloaded: {', '.join(failed)}")
        print("Check the internet connection and run this again.")
        return 1

    print(f"\nVendored into {VENDOR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
