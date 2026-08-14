"""Capture authenticated OPPE Practice network calls + extra JS chunks."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
AUTH = ROOT / ".auth" / "oppepractice.auth.json"
OUT = ROOT / "data" / "raw" / "oppepractice"
BASE = "https://oppepractice.iitmbsdegree.in"
QID = "79dfbf8e-be5c-4dbb-a9c2-398c86414bed"

PAGES = [
    f"{BASE}/app/subjects",
    f"{BASE}/app/subjects/python",
    f"{BASE}/app/subjects/python?tab=pyqs",
    f"{BASE}/app/subjects/python?tab=test-series",
    f"{BASE}/app/subjects/dbms",
    f"{BASE}/app/questions/{QID}",
    f"{BASE}/app/test",
    f"{BASE}/app/progress",
    f"{BASE}/leaderboard",
]


async def main() -> None:
    bag: list[dict] = []
    js_urls: set[str] = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel="chrome")
        context = await browser.new_context(
            storage_state=str(AUTH),
            viewport={"width": 1400, "height": 900},
        )

        async def on_response(resp):
            url = resp.url
            if any(x in url for x in ("google", "gstatic", "gtm", "analytics", "fonts.")):
                return
            ct = (resp.headers.get("content-type") or "").lower()
            method = resp.request.method
            entry: dict = {
                "url": url,
                "status": resp.status,
                "method": method,
                "ctype": ct[:80],
            }
            try:
                if "json" in ct or "/api/" in url:
                    try:
                        entry["data"] = await resp.json()
                    except Exception:
                        text = await resp.text()
                        entry["preview"] = text[:500]
                elif "javascript" in ct or url.endswith(".js"):
                    js_urls.add(url)
                elif "text/x-component" in ct or "rsc" in ct:
                    text = await resp.text()
                    entry["rsc_preview"] = text[:1500]
                elif method in ("POST", "PUT") or "action" in url.lower():
                    text = await resp.text()
                    entry["preview"] = text[:500]
            except Exception as exc:
                entry["err"] = str(exc)[:120]
            interesting = (
                "/api/" in url
                or "json" in ct
                or "rsc" in ct
                or "text/x-component" in ct
                or method not in ("GET", "HEAD")
                or "question" in url.lower()
                or "test" in url.lower()
            )
            if interesting:
                bag.append(entry)
                print(resp.status, method, url[:140], list(entry.get("data", {}) if isinstance(entry.get("data"), dict) else [])[:8])

        page = await context.new_page()
        page.on("response", on_response)

        for url in PAGES:
            print("VISIT", url)
            try:
                await page.goto(url, wait_until="networkidle", timeout=60_000)
            except Exception as exc:
                print(" nav err", exc)
            await page.wait_for_timeout(2500)
            print("  now", page.url, await page.title())

            # try clicking first question / test if present
            for sel in (
                "a[href*='/app/questions/']",
                "a[href*='/app/test']",
                "button:has-text('Open')",
                "button:has-text('Start')",
            ):
                try:
                    loc = page.locator(sel).first
                    if await loc.count():
                        href = await loc.get_attribute("href")
                        print("  clickable", sel, href)
                except Exception:
                    pass

        # Extra: RSC fetch from the page context (cookies included)
        rsc = await page.evaluate(
            """async (url) => {
              const r = await fetch(url, { headers: { RSC: '1', Accept: 'text/x-component' } });
              return { status: r.status, ctype: r.headers.get('content-type'), text: (await r.text()).slice(0, 4000) };
            }""",
            f"{BASE}/app/questions/{QID}",
        )
        (OUT / "rsc_question_preview.json").write_text(json.dumps(rsc, indent=2), encoding="utf-8")
        print("RSC", rsc.get("status"), rsc.get("ctype"), (rsc.get("text") or "")[:200])

        slim = []
        for e in bag:
            slim.append({k: v for k, v in e.items() if k != "data" or True})
        (OUT / "browser_capture.json").write_text(
            json.dumps(slim, indent=2, default=str)[:2_000_000], encoding="utf-8"
        )
        (OUT / "browser_js_urls.json").write_text(json.dumps(sorted(js_urls), indent=2), encoding="utf-8")
        print("captured", len(bag), "js", len(js_urls))
        await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
