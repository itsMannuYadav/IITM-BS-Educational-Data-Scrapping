"""Deep discovery for AceGrade notes/lectures data sources."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

OUT = Path("data/raw/acegrade")
API = "https://project-b-backend-3uoftpsktq-el.a.run.app"


async def main() -> None:
    bag: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        async def on_response(resp):
            url = resp.url
            ct = (resp.headers.get("content-type") or "").lower()
            interesting = any(
                k in url.lower()
                for k in (
                    "backendapi",
                    "firestore",
                    "firebaseio",
                    "googleapis",
                    "supabase",
                    "notes",
                    "lecture",
                    "storage.googleapis",
                    "firebasestorage",
                )
            )
            if not interesting and "json" not in ct:
                return
            entry = {"url": url, "status": resp.status, "ct": ct}
            try:
                if "json" in ct or "backendapi" in url:
                    entry["data"] = await resp.json()
                else:
                    text = await resp.text()
                    entry["preview"] = text[:500]
            except Exception:
                pass
            bag.append(entry)

        page.on("response", on_response)

        for path in ["/notes", "/lectures", "/books", "/prev_papers"]:
            print("visit", path)
            await page.goto(f"https://www.acegrade.in{path}", wait_until="networkidle", timeout=90_000)
            await asyncio.sleep(3)
            # try clicking common UI bits
            for label in ["Foundation", "Diploma", "Degree", "Data Science", "Electronic"]:
                try:
                    loc = page.get_by_text(label, exact=False).first
                    if await loc.count():
                        await loc.click(timeout=2000)
                        await asyncio.sleep(1.5)
                except Exception:
                    pass

        # Collect all script URLs including lazy chunks after navigation
        scripts = await page.evaluate(
            """() => performance.getEntriesByType('resource')
                .map(r => r.name)
                .filter(n => n.includes('/_next/static/chunks/'))"""
        )
        print("perf scripts", len(scripts))
        patterns: set[str] = set()
        async with httpx.AsyncClient(timeout=40, headers={"User-Agent": "Mozilla/5.0"}) as c:
            for u in scripts:
                try:
                    t = (await c.get(u)).text
                except Exception:
                    continue
                if "backendapi" in t or "firestore" in t or "notes" in t.lower():
                    for m in re.findall(r".{0,40}backendapi.{0,80}", t):
                        patterns.add(m.replace("\n", " ")[:200])
                    for m in re.findall(r".{0,30}firestore.{0,80}", t, flags=re.I):
                        patterns.add(m.replace("\n", " ")[:200])
                    for m in re.findall(r'["\']https?://[^"\']+(?:run\.app|firebaseio|googleapis)[^"\']*["\']', t):
                        patterns.add(m)
                    for m in re.findall(r'collection\(["\'][^"\']+["\']\)', t):
                        patterns.add(m)
                    for m in re.findall(r'doc\(["\'][^"\']+["\']\)', t):
                        patterns.add(m)

        (OUT / "notes_network.json").write_text(json.dumps(bag, indent=2, default=str)[:2_000_000], encoding="utf-8")
        (OUT / "notes_js_patterns.json").write_text(json.dumps(sorted(patterns), indent=2), encoding="utf-8")
        print("network events", len(bag))
        print("pattern count", len(patterns))
        for x in sorted(patterns)[:80]:
            print("P:", x)
        # unique backend urls
        backends = sorted({e["url"] for e in bag if "backendapi" in e["url"] or "firestore" in e["url"]})
        print("backend urls:")
        for u in backends:
            print(" ", u)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
