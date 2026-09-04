#!/usr/bin/env python3
"""Render whitepaper.html to the white paper PDF at the repository root.

    python3 docs/whitepaper/render.py [output.pdf]

Uses headless Chromium over the DevTools protocol rather than the simpler
--print-to-pdf flag, because only printToPDF accepts a footer template — that
is what puts "Page n of m" on every page. Needs chromium (or chromium-browser)
on PATH and the `websockets` package. Neither is needed to run the app, so this
is a documentation tool and nothing here is in requirements.txt.
"""
import asyncio, base64, json, pathlib, subprocess, sys, time, urllib.request
import websockets

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "whitepaper.html"
OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parents[1] / "MM01-StudentDAQ-white-paper.pdf"
PORT = 9333
MM = 1 / 25.4  # inches per mm

FOOTER = """<div style="font-family:'Liberation Sans',Arial,sans-serif;font-size:7.5px;color:#666;
width:100%;padding:0 18mm;display:flex;justify-content:space-between;align-items:center;">
<span>Creating a web-based API for the MM-01 StudentDAQ on Raspberry Pi Hardware</span>
<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>"""

async def main():
    profile = HERE / ".chrome-profile"
    proc = subprocess.Popen([
        "chromium-browser", "--headless", "--disable-gpu", "--no-sandbox",
        f"--remote-debugging-port={PORT}", "--remote-allow-origins=*",
        f"--user-data-dir={profile}", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                page = next(t for t in targets if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.2)
        else:
            raise SystemExit("Chromium did not start")

        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
            seq = 0
            async def call(method, **params):
                nonlocal seq
                seq += 1
                await ws.send(json.dumps({"id": seq, "method": method, "params": params}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == seq:
                        if "error" in msg:
                            raise RuntimeError(msg["error"])
                        return msg.get("result", {})
                    if msg.get("method") == "Page.loadEventFired":
                        loaded.set()

            loaded = asyncio.Event()
            await call("Page.enable")
            await call("Page.navigate", url=SRC.as_uri())
            # Wait for the load event (may already have been consumed above).
            for _ in range(50):
                if loaded.is_set():
                    break
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 0.2))
                    if msg.get("method") == "Page.loadEventFired":
                        break
                except asyncio.TimeoutError:
                    pass
            await asyncio.sleep(0.5)  # let fonts settle

            res = await call(
                "Page.printToPDF",
                printBackground=True,
                preferCSSPageSize=False,
                paperWidth=210 * MM, paperHeight=297 * MM,
                marginTop=18 * MM, marginBottom=20 * MM,
                marginLeft=18 * MM, marginRight=18 * MM,
                displayHeaderFooter=True,
                headerTemplate="<span></span>",
                footerTemplate=FOOTER,
            )
            OUT.write_bytes(base64.b64decode(res["data"]))
            print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
        # The snap-confined browser ignores signals from here; ask it to quit.
        v = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version"))
        async with websockets.connect(v["webSocketDebuggerUrl"], max_size=None) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
            try:
                await asyncio.wait_for(ws.recv(), 3)
            except Exception:
                pass
    finally:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            print("warning: chromium still running", file=sys.stderr)

asyncio.run(main())
