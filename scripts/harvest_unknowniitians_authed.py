"""
Unknown IITians authenticated harvest.

1) python main.py login unknowniitians   (Sign In with Google in the collector Chrome)
2) python scripts/harvest_unknowniitians_authed.py

Uses the saved collector browser profile + any Supabase user JWT from localStorage
to re-fetch notes/PYQs so file_link fields that were null anonymously can appear.
Also crawls notes/PYQ pages while logged in and captures Drive links from the network.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from playwright.async_api import async_playwright
from rich.console import Console

from core.auth import browser_profile_dir, save_auth
from core.config import INDEX_PATH, load_index
from core.fetch import extract_gdrive_urls
from core.schema import ResourceRecord, ResourceType
from core.store import write_raw_json
from sources.unknowniitians import SOURCE, SUPA, KEY_PATH, fetch_table, record_from_row, merge_into_index

console = Console()
RAW = Path("data/raw/unknowniitians")
PROFILE = browser_profile_dir(SOURCE)

DEEP_URLS = [
    "https://www.unknowniitians.com/exam-preparation/iitm-bs/notes/data-science/foundation",
    "https://www.unknowniitians.com/exam-preparation/iitm-bs/notes/data-science/diploma",
    "https://www.unknowniitians.com/exam-preparation/iitm-bs/notes/data-science/degree",
    "https://www.unknowniitians.com/exam-preparation/iitm-bs/notes/electronic-systems/foundation",
    "https://www.unknowniitians.com/exam-preparation/iitm-bs/notes/electronic-systems/diploma",
    "https://www.unknowniitians.com/exam-preparation/iitm-bs/notes/electronic-systems/degree",
    "https://www.unknowniitians.com/exam-preparation/iitm-bs/pyqs",
]


def load_tokens_from_storage(storage: dict) -> tuple[str | None, str | None]:
    """Return (anon_or_apikey, user_access_token)."""
    anon = KEY_PATH.read_text(encoding="utf-8").strip() if KEY_PATH.exists() else None
    user = None
    for origin in storage.get("origins", []):
        for item in origin.get("localStorage", []):
            name = item.get("name") or ""
            val = item.get("value") or ""
            if not val:
                continue
            # supabase auth token JSON
            if "auth-token" in name or name.endswith("-auth-token"):
                try:
                    payload = json.loads(val)
                    user = payload.get("access_token") or payload.get("accessToken")
                except Exception:
                    if val.startswith("eyJ"):
                        user = val
            if name in ("access_token", "supabase.auth.token") and val.startswith("eyJ"):
                user = val
    return anon, user


async def harvest_with_token(token: str, *, label: str) -> list[ResourceRecord]:
    headers = {
        "apikey": (KEY_PATH.read_text(encoding="utf-8").strip() if KEY_PATH.exists() else token),
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "IITM-BS-Educational-Collector/1.0 (approved-collection)",
    }
    records: list[ResourceRecord] = []
    async with httpx.AsyncClient(timeout=60, headers=headers, follow_redirects=True) as client:
        for table, kind in (
            ("iitm_branch_notes", "iitm_branch_note"),
            ("notes", "note"),
            ("pyqs", "pyq"),
        ):
            rows = await fetch_table(client, table)
            write_raw_json(SOURCE, f"{table}_{label}.json", rows)
            with_links = sum(1 for r in rows if r.get("file_link") or r.get("content_url"))
            console.print(f"[cyan]{label}/{table}[/cyan] rows={len(rows)} with_links={with_links}")
            for row in rows:
                rec = record_from_row(row, kind=kind)
                if rec:
                    records.append(rec)
    return records


async def browser_capture_links() -> tuple[list[ResourceRecord], str | None]:
    """Open logged-in collector Chrome and scrape Drive links from notes/PYQ pages."""
    records: list[ResourceRecord] = []
    seen: set[str] = set()
    captured_api: list[dict[str, Any]] = []
    user: str | None = None

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1400, "height": 900},
        )

        async def on_response(resp):
            url = resp.url
            if "supabase.co" not in url and "drive.google" not in url:
                return
            try:
                ct = (resp.headers.get("content-type") or "").lower()
                entry: dict[str, Any] = {"url": url, "status": resp.status}
                if "json" in ct:
                    entry["data"] = await resp.json()
                captured_api.append(entry)
            except Exception:
                return

        page = context.pages[0] if context.pages else await context.new_page()
        page.on("response", on_response)

        await page.goto("https://www.unknowniitians.com/exam-preparation/iitm-bs", wait_until="networkidle")
        await page.wait_for_timeout(2000)
        storage = await context.storage_state()
        await save_auth(context, SOURCE)
        (RAW / "authed_storage.json").write_text(json.dumps(storage), encoding="utf-8")
        _anon, user = load_tokens_from_storage(storage)
        if user:
            (RAW / "supabase_user_jwt.txt").write_text(user, encoding="utf-8")
            console.print("[green]Captured user JWT[/green]")
        else:
            console.print("[yellow]No user JWT in localStorage — are you signed in?[/yellow]")

        for url in DEEP_URLS:
            console.print(f"[cyan]Visit[/cyan] {url}")
            try:
                await page.goto(url, wait_until="networkidle", timeout=90_000)
                await page.wait_for_timeout(2500)
                html = await page.content()
                for m in extract_gdrive_urls(html):
                    if m in seen:
                        continue
                    seen.add(m)
                    records.append(
                        ResourceRecord(
                            source=SOURCE,
                            title=f"Drive from page {url.split('/')[-1]}",
                            type=ResourceType.GDRIVE,
                            original_url=m,
                            gdrive_urls=[m],
                            extra={"discovered_via": "authed_page_html", "from": url},
                        )
                    )
                for name in ("Download", "LOGIN TO DOWNLOAD", "Open", "View"):
                    try:
                        btns = page.get_by_role("button", name=re.compile(name, re.I))
                        count = await btns.count()
                        for i in range(min(count, 8)):
                            await btns.nth(i).click(timeout=2000)
                            await page.wait_for_timeout(800)
                    except Exception:
                        pass
                items = await page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(a => ({href:a.href||'', text:(a.innerText||'').trim().slice(0,160)}))",
                )
                for it in items:
                    href = (it.get("href") or "").strip()
                    if not href or href in seen:
                        continue
                    if "drive.google" in href or "docs.google" in href:
                        seen.add(href)
                        records.append(
                            ResourceRecord(
                                source=SOURCE,
                                title=(it.get("text") or href)[:300],
                                type=ResourceType.GDRIVE,
                                original_url=href,
                                gdrive_urls=extract_gdrive_urls(href) or [href],
                                extra={"discovered_via": "authed_anchor", "from": url},
                            )
                        )
            except Exception as exc:
                console.print(f"[red]fail[/red] {url}: {exc}")

        write_raw_json(SOURCE, "authed_api_capture.json", captured_api[:500])
        for entry in captured_api:
            data = entry.get("data")
            rows = data if isinstance(data, list) else []
            if isinstance(data, dict):
                for v in data.values():
                    if isinstance(v, list):
                        rows = v
                        break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                link = (row.get("file_link") or row.get("content_url") or "").strip()
                if not link or link in seen:
                    continue
                seen.add(link)
                rec = record_from_row(row, kind="authed_api")
                if rec:
                    records.append(rec)

        await context.close()
    return records, user


async def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    if not PROFILE.exists():
        console.print("[red]No collector profile yet. Run:[/red] python main.py login unknowniitians")
        raise SystemExit(1)

    console.print("[bold]Step A:[/bold] browser capture (logged-in profile)")
    page_records, user_jwt = await browser_capture_links()

    all_records: list[ResourceRecord] = list(page_records)

    console.print("[bold]Step B:[/bold] supabase with anon key")
    if KEY_PATH.exists():
        anon = KEY_PATH.read_text(encoding="utf-8").strip()
        all_records.extend(await harvest_with_token(anon, label="anon"))

    if user_jwt:
        console.print("[bold]Step C:[/bold] supabase with user JWT")
        all_records.extend(await harvest_with_token(user_jwt, label="user"))
    else:
        console.print("[yellow]Skipping user-token harvest — sign in then re-run.[/yellow]")

    # dedupe by original_url preferring rows with gdrive
    best: dict[str, ResourceRecord] = {}
    for rec in all_records:
        key = rec.original_url
        prev = best.get(key)
        if not prev:
            best[key] = rec
            continue
        if (rec.gdrive_urls or rec.file_url) and not (prev.gdrive_urls or prev.file_url):
            best[key] = rec
    merged = list(best.values())
    with_files = sum(1 for r in merged if r.gdrive_urls or (r.file_url and str(r.file_url).startswith("http")))
    write_raw_json(SOURCE, "records_authed.json", [r.to_index_row() for r in merged])
    total = merge_into_index(merged)
    console.print(
        f"[bold green]Unknown IITians authed index: {len(merged)}[/bold green] "
        f"({with_files} downloadable; index total {total})"
    )


if __name__ == "__main__":
    asyncio.run(main())
