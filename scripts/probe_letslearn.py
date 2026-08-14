"""Probe Lets Learn / Graphy for course and resource endpoints."""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.async_api import async_playwright

OUT = ROOT / "data" / "raw" / "letslearn"
OUT.mkdir(parents=True, exist_ok=True)


async def main() -> None:
    bag = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel="chrome")
        page = await browser.new_page()

        async def on_resp(resp):
            url = resp.url
            ct = (resp.headers.get("content-type") or "").lower()
            if any(k in url for k in ("api", "graphql", "course", "product", "store", "cdn")) and (
                "json" in ct or "api" in url
            ):
                entry = {"url": url, "status": resp.status}
                try:
                    if "json" in ct:
                        entry["data"] = await resp.json()
                    else:
                        entry["preview"] = (await resp.text())[:400]
                except Exception:
                    pass
                bag.append(entry)
                print(resp.status, url[:160])

        page.on("response", on_resp)
        for url in [
            "https://www.letslearn1110.com/",
            "https://www.letslearn1110.com/s/store",
            "https://www.letslearn1110.com/s/courses",
            "https://www.letslearn1110.com/s/pages/free-resources",
        ]:
            print("VISIT", url)
            try:
                await page.goto(url, wait_until="networkidle", timeout=90_000)
                await page.wait_for_timeout(2500)
                html = await page.content()
                safe = re.sub(r"[^a-z0-9]+", "_", url.split(".com")[-1])[:60] or "home"
                (OUT / f"page_{safe}.html").write_text(html, encoding="utf-8")
                links = await page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(a => ({href:a.href, text:(a.innerText||'').trim().slice(0,120)})).slice(0,80)",
                )
                print("links", len(links))
                for L in links[:15]:
                    print(" ", L)
            except Exception as exc:
                print("fail", url, exc)
        (OUT / "api_capture.json").write_text(json.dumps(bag, indent=2, default=str)[:900000], encoding="utf-8")
        print("api events", len(bag))
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
