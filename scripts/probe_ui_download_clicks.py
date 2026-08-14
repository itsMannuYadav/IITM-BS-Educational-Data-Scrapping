"""Find Unknown IITians download API by clicking Download while logged in."""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.async_api import async_playwright
from rich.console import Console

from core.auth import browser_profile_dir

console = Console()
PROFILE = browser_profile_dir("unknowniitians")
OUT = ROOT / "data" / "raw" / "unknowniitians"


async def main() -> None:
    bag = []
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1400, "height": 900},
        )
        page = context.pages[0] if context.pages else await context.new_page()

        async def on_resp(resp):
            url = resp.url
            if not any(k in url for k in ("supabase", "drive.google", "download", "storage", "functions")):
                return
            entry = {"url": url, "status": resp.status, "method": resp.request.method}
            try:
                headers = await resp.request.all_headers()
                entry["req"] = {
                    k: v
                    for k, v in headers.items()
                    if k.lower() in ("authorization", "apikey", "content-type")
                }
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
            print(resp.request.method, resp.status, url[:160])

        page.on("response", on_resp)
        await page.goto(
            "https://www.unknowniitians.com/exam-preparation/iitm-bs/notes/data-science/foundation",
            wait_until="networkidle",
            timeout=90_000,
        )
        await page.wait_for_timeout(2000)

        # Click visible Download buttons
        clicked = 0
        selectors = [
            "button:has-text('Download')",
            "a:has-text('Download')",
            "text=Download",
        ]
        for sel in selectors:
            locs = page.locator(sel)
            n = await locs.count()
            console.print(f"{sel} count={n}")
            for i in range(min(n, 12)):
                try:
                    await locs.nth(i).click(timeout=3000)
                    clicked += 1
                    await page.wait_for_timeout(1500)
                except Exception as exc:
                    console.print(f"click fail {exc}")
        console.print(f"clicked={clicked}")

        # Also try PYQ page
        await page.goto(
            "https://www.unknowniitians.com/exam-preparation/iitm-bs/pyqs",
            wait_until="networkidle",
            timeout=90_000,
        )
        await page.wait_for_timeout(2000)
        locs = page.locator("button:has-text('Download'), a:has-text('Download')")
        n = await locs.count()
        console.print(f"pyq download buttons={n}")
        for i in range(min(n, 10)):
            try:
                await locs.nth(i).click(timeout=3000)
                await page.wait_for_timeout(1500)
            except Exception:
                pass

        (OUT / "download_click_capture.json").write_text(
            json.dumps(bag, indent=2, default=str)[:1_500_000], encoding="utf-8"
        )
        console.print(f"captured {len(bag)} network events")
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
