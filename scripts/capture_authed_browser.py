"""Capture lecture API calls from logged-in AceGrade browser session."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / ".auth" / "browser-profiles" / "acegrade"
OUT = ROOT / "data" / "raw" / "acegrade"


async def main() -> None:
    bag = []
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            channel="chrome",
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1400, "height": 900},
        )

        async def on_response(resp):
            url = resp.url
            if "backendapi" not in url and "googleapis" not in url:
                return
            entry = {"url": url, "status": resp.status, "method": resp.request.method}
            try:
                headers = await resp.request.all_headers()
                interesting = {
                    k: v
                    for k, v in headers.items()
                    if k.lower() in ("authorization", "courses", "content-type", "token", "auth")
                    or "auth" in k.lower()
                }
                entry["req_headers"] = interesting
            except Exception:
                pass
            try:
                ct = (resp.headers.get("content-type") or "").lower()
                if "json" in ct:
                    entry["data"] = await resp.json()
                else:
                    entry["preview"] = (await resp.text())[:300]
            except Exception:
                pass
            bag.append(entry)
            print(resp.status, resp.request.method, url[:120], entry.get("req_headers"))

        page = await context.new_page()
        page.on("response", on_response)
        await page.goto("https://www.acegrade.in/lectures", wait_until="networkidle", timeout=90_000)
        await page.wait_for_timeout(4000)
        # try clicking foundation / a course
        for label in ["Foundation", "CT", "Python", "Mathematics"]:
            try:
                loc = page.get_by_text(label, exact=False).first
                if await loc.count():
                    await loc.click(timeout=2000)
                    await page.wait_for_timeout(2500)
            except Exception:
                pass

        await page.goto("https://www.acegrade.in/notes", wait_until="networkidle", timeout=90_000)
        await page.wait_for_timeout(3000)

        (OUT / "authed_browser_capture.json").write_text(
            json.dumps(bag, indent=2, default=str)[:1_000_000], encoding="utf-8"
        )
        print("captured", len(bag))
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
