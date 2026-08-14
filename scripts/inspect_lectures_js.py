"""Inspect AceGrade lectures JS for exact course API usage."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

OUT = Path("data/raw/acegrade")


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel="chrome")
        page = await browser.new_page()
        await page.goto("https://www.acegrade.in/lectures", wait_until="networkidle")
        scripts = await page.evaluate(
            """() => performance.getEntriesByType('resource').map(r => r.name)
               .filter(n => n.includes('/_next/static/chunks/'))"""
        )
        patterns = []
        async with httpx.AsyncClient(timeout=40) as c:
            for u in scripts:
                t = (await c.get(u)).text
                if "backendapi/course" in t or "lecturesURL" in t or "get_notes" in t:
                    for m in re.findall(r".{0,60}backendapi/course.{0,120}", t):
                        patterns.append(m.replace("\n", " "))
                    for m in re.findall(r".{0,40}Authorization.{0,80}", t):
                        patterns.append(m.replace("\n", " ")[:200])
                    for m in re.findall(r".{0,40}auth_token.{0,80}", t):
                        patterns.append(m.replace("\n", " ")[:200])
                    for m in re.findall(r"headers:\s*\{[^}]{0,300}\}", t):
                        if "auth" in m.lower() or "token" in m.lower() or "course" in m.lower():
                            patterns.append(m[:300])
        (OUT / "lectures_js_snippets.json").write_text(
            json.dumps(sorted(set(patterns)), indent=2), encoding="utf-8"
        )
        for x in sorted(set(patterns))[:60]:
            print(x)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
