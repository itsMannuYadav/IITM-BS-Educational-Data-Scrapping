import re
from pathlib import Path

import httpx
from playwright.async_api import async_playwright
import asyncio
import json

OUT = Path("data/raw/unknowniitians")
SUPA = "https://qzrvctpwefhmcduariuw.supabase.co"


async def capture_key() -> str:
    key = None
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel="chrome")
        page = await browser.new_page()

        async def on_req(req):
            nonlocal key
            if "supabase.co" in req.url:
                h = await req.all_headers()
                for k, v in h.items():
                    if k.lower() in ("apikey", "authorization") and v.startswith("eyJ"):
                        key = v.replace("Bearer ", "")
                        print("got key via", k, key[:40] + "...")

        page.on("request", on_req)
        await page.goto(
            "https://www.unknowniitians.com/exam-preparation/iitm-bs/notes/data-science/foundation",
            wait_until="networkidle",
            timeout=90_000,
        )
        await page.wait_for_timeout(2000)
        await browser.close()
    if not key:
        raise SystemExit("no supabase key")
    (OUT / "supabase_anon.txt").write_text(key, encoding="utf-8")
    return key


async def harvest(key: str) -> None:
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "User-Agent": "IITM-BS-Educational-Collector/1.0 (approved-collection)",
    }
    endpoints = {
        "iitm_branch_notes": f"{SUPA}/rest/v1/iitm_branch_notes?select=*&is_active=eq.true",
        "notes": f"{SUPA}/rest/v1/notes?select=*&is_active=eq.true",
        "pyqs": f"{SUPA}/rest/v1/pyqs?select=*&is_active=eq.true",
        "iitm_bs_subjects": f"{SUPA}/rest/v1/iitm_bs_subjects?select=*",
        "iitm_branch_pyqs": f"{SUPA}/rest/v1/iitm_branch_pyqs?select=*&is_active=eq.true",
    }
    async with httpx.AsyncClient(timeout=60, headers=headers, follow_redirects=True) as c:
        for name, url in endpoints.items():
            # paginate with Range
            all_rows = []
            start = 0
            page = 1000
            while True:
                h = {**headers, "Range": f"{start}-{start+page-1}", "Prefer": "count=exact"}
                r = await c.get(url, headers=h)
                print(name, r.status_code, r.headers.get("content-range"), len(r.text))
                if r.status_code not in (200, 206):
                    (OUT / f"{name}_error.txt").write_text(r.text[:2000], encoding="utf-8")
                    break
                rows = r.json()
                if not isinstance(rows, list):
                    print("unexpected", rows)
                    break
                all_rows.extend(rows)
                if len(rows) < page:
                    break
                start += page
            (OUT / f"{name}.json").write_text(json.dumps(all_rows, indent=2), encoding="utf-8")
            print("saved", name, len(all_rows))
            if all_rows:
                print("keys", list(all_rows[0].keys()))


async def main():
    key_path = OUT / "supabase_anon.txt"
    if key_path.exists():
        key = key_path.read_text(encoding="utf-8").strip()
    else:
        key = await capture_key()
    await harvest(key)


if __name__ == "__main__":
    asyncio.run(main())
