"""Probe AceGrade Cloud Run API + frontend JS for resource endpoints."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

API = "https://project-b-backend-3uoftpsktq-el.a.run.app/backendapi"
OUT = Path("data/raw/acegrade")


async def main() -> None:
    async with httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0"},
    ) as c:
        r = await c.get(f"{API}/term/24t3")
        print("term", r.status_code)
        term = r.json()
        courses: list[tuple[str, str, str]] = []
        for level, mapping in term.get("course_metadata", {}).items():
            for cid, name in mapping.items():
                courses.append((level, cid, name))
        print("courses", len(courses))

        samples = ["ma1001", "cs1002", "cs2001", "cs3004"]
        suffixes = ["ds", "es", ""]
        resources = [
            "pyq",
            "notes",
            "note",
            "lectures",
            "lecture",
            "books",
            "book",
            "resources",
            "resource",
            "materials",
            "cheatsheet",
            "cheat",
            "syllabus",
            "pdf",
            "links",
        ]
        found = []
        for res in resources:
            for course in samples:
                for suf in suffixes:
                    url = f"{API}/{res}/{course}/{suf}" if suf else f"{API}/{res}/{course}"
                    try:
                        resp = await c.get(url)
                        body = resp.text
                        if resp.status_code == 200 and body and body not in ("null", "{}", "[]"):
                            found.append({"url": url, "status": resp.status_code, "preview": body[:200]})
                            print("HIT", url, resp.status_code, body[:100].replace("\n", " "))
                    except Exception as exc:
                        print("err", url, exc)

        (OUT / "api_probe.json").write_text(
            json.dumps({"found": found, "course_count": len(courses)}, indent=2),
            encoding="utf-8",
        )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("https://www.acegrade.in/prev_papers", wait_until="networkidle")
        scripts = await page.eval_on_selector_all("script[src]", "els => els.map(e => e.src)")
        urls = [u for u in scripts if "_next/static/chunks" in u]
        print("js chunks", len(urls))
        patterns: set[str] = set()
        async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "Mozilla/5.0"}) as c:
            for u in urls:
                try:
                    t = (await c.get(u)).text
                except Exception:
                    continue
                for m in re.findall(r"backendapi/[A-Za-z0-9_./${}-]+", t):
                    patterns.add(m)
                for m in re.findall(r'["\']/backendapi/[A-Za-z0-9_./-]+["\']', t):
                    patterns.add(m)
                for m in re.findall(r"project-b-backend[^\"'\s]+", t):
                    patterns.add(m)
                for m in re.findall(r'["\'](pyq|notes|lectures|books)["\']', t):
                    patterns.add(f"literal:{m}")
        print("patterns:")
        for x in sorted(patterns):
            print(" ", x)
        (OUT / "js_api_patterns.json").write_text(json.dumps(sorted(patterns), indent=2), encoding="utf-8")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
