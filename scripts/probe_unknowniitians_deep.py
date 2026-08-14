"""Inspect Unknown IITians notes pages for APIs / Drive / auth walls."""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.async_api import async_playwright

OUT = ROOT / "data" / "raw" / "unknowniitians"
OUT.mkdir(parents=True, exist_ok=True)

URLS = [
    "https://www.unknowniitians.com/exam-preparation/iitm-bs/notes/data-science/foundation",
    "https://www.unknowniitians.com/exam-preparation/iitm-bs/pyqs",
    "https://www.unknowniitians.com/exam-preparation/iitm-bs",
]


async def main() -> None:
    bag = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel="chrome")
        page = await browser.new_page()

        async def on_resp(resp):
            url = resp.url
            ct = (resp.headers.get("content-type") or "").lower()
            if any(k in url for k in ("api", "graphql", "supabase", "firebase", "sanity", "contentful", "strapi")) or "json" in ct:
                entry = {"url": url, "status": resp.status, "ct": ct}
                try:
                    if "json" in ct:
                        entry["data"] = await resp.json()
                    else:
                        entry["preview"] = (await resp.text())[:500]
                except Exception:
                    pass
                bag.append(entry)
                print("API", resp.status, url[:140])

        page.on("response", on_resp)
        for url in URLS:
            print("VISIT", url)
            await page.goto(url, wait_until="networkidle", timeout=90_000)
            await page.wait_for_timeout(3000)
            html = await page.content()
            safe = re.sub(r"[^a-z0-9]+", "_", url.split("unknowniitians.com")[-1])[:80]
            (OUT / f"deep_{safe}.html").write_text(html, encoding="utf-8")
            text = await page.inner_text("body")
            print("title", await page.title())
            print("body sample", text[:400].replace("\n", " | "))
            # buttons / cards
            labels = await page.eval_on_selector_all(
                "button,a,h1,h2,h3",
                "els => els.map(e => (e.innerText||'').trim()).filter(Boolean).slice(0,40)",
            )
            print("labels", labels[:20])
            drives = re.findall(r"https?://(?:drive|docs)\.google\.com/[^\s\"'<>]+", html)
            print("drive in html", len(drives), drives[:3])
        (OUT / "api_capture.json").write_text(json.dumps(bag, indent=2, default=str)[:800000], encoding="utf-8")
        print("api events", len(bag))
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
