"""
Deeper Lets Learn harvest after login.

Uses collector Chrome profile to open store/course pages and capture
resource links (Drive, PDFs, lesson assets) visible when signed in.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.async_api import async_playwright
from rich.console import Console

from core.auth import browser_profile_dir, save_auth
from core.config import INDEX_PATH, load_index
from core.fetch import extract_gdrive_urls
from core.schema import ResourceRecord, ResourceType
from core.store import write_raw_json
from sources.letslearn import scrape_and_download as public_store_scrape

console = Console()
SOURCE = "letslearn"
PROFILE = browser_profile_dir(SOURCE)
RAW = Path("data/raw/letslearn")

SEED_PATHS = [
    "https://www.letslearn1110.com/",
    "https://www.letslearn1110.com/s/store",
    "https://www.letslearn1110.com/products",
    "https://www.letslearn1110.com/s/pages/free-resources",
    "https://www.letslearn1110.com/s/pages/whatsappgroups",
    "https://www.letslearn1110.com/blog",
]


async def deep_crawl() -> list[ResourceRecord]:
    records: list[ResourceRecord] = []
    seen: set[str] = set()
    api_bag: list[dict] = []

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
            ct = (resp.headers.get("content-type") or "").lower()
            if "json" in ct or "/s/store" in url or "drive.google" in url:
                entry = {"url": url, "status": resp.status}
                try:
                    if "json" in ct:
                        entry["data"] = await resp.json()
                except Exception:
                    pass
                api_bag.append(entry)

        page.on("response", on_resp)

        await page.goto("https://www.letslearn1110.com/", wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        await save_auth(context, SOURCE)

        course_urls: list[str] = []
        for seed in SEED_PATHS:
            console.print(f"[cyan]Visit[/cyan] {seed}")
            try:
                await page.goto(seed, wait_until="networkidle", timeout=90_000)
                await page.wait_for_timeout(2000)
                html = await page.content()
                for m in extract_gdrive_urls(html):
                    if m not in seen:
                        seen.add(m)
                        records.append(
                            ResourceRecord(
                                source=SOURCE,
                                title="Lets Learn Drive resource",
                                type=ResourceType.GDRIVE,
                                original_url=m,
                                gdrive_urls=[m],
                                extra={"from": seed},
                            )
                        )
                links = await page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(a => ({href:a.href||'', text:(a.innerText||'').trim().slice(0,160)}))",
                )
                for it in links:
                    href = (it.get("href") or "").strip()
                    if not href or href in seen:
                        continue
                    if any(k in href for k in ("/courses/", "/s/store/courses", "/products/", "drive.google")):
                        seen.add(href)
                        course_urls.append(href)
                        records.append(
                            ResourceRecord(
                                source=SOURCE,
                                title=(it.get("text") or href)[:300],
                                type=ResourceType.LINK,
                                original_url=href,
                                gdrive_urls=extract_gdrive_urls(href),
                                extra={"from": seed},
                            )
                        )
            except Exception as exc:
                console.print(f"[red]fail[/red] {seed}: {exc}")

        # visit up to 40 course pages
        for url in course_urls[:40]:
            if "letslearn1110.com" not in url and "drive.google" not in url:
                continue
            if "drive.google" in url:
                continue
            console.print(f"[cyan]Course[/cyan] {url}")
            try:
                await page.goto(url, wait_until="networkidle", timeout=60_000)
                await page.wait_for_timeout(1500)
                html = await page.content()
                for m in extract_gdrive_urls(html):
                    if m in seen:
                        continue
                    seen.add(m)
                    records.append(
                        ResourceRecord(
                            source=SOURCE,
                            title=f"Drive from {url.split('/')[-1]}",
                            type=ResourceType.GDRIVE,
                            original_url=m,
                            gdrive_urls=[m],
                            extra={"from": url},
                        )
                    )
                # pdf / cloudinary / bunnycdn style assets
                for m in re.findall(r"https?://[^\s\"'<>]+\.pdf(?:\?[^\s\"'<>]*)?", html, flags=re.I):
                    if m in seen:
                        continue
                    seen.add(m)
                    records.append(
                        ResourceRecord(
                            source=SOURCE,
                            title=Path(m.split("?")[0]).name,
                            type=ResourceType.NOTES,
                            original_url=m,
                            file_url=m,
                            extra={"from": url},
                        )
                    )
            except Exception as exc:
                console.print(f"[yellow]skip[/yellow] {url}: {exc}")

        write_raw_json(SOURCE, "authed_api_capture.json", api_bag[:300])
        await context.close()
    return records


def merge_letslearn(extra: list[ResourceRecord]) -> int:
    others = [r for r in load_index() if r.get("source") != SOURCE]
    existing = [r for r in load_index() if r.get("source") == SOURCE]
    by_url = {r.get("original_url"): r for r in existing}
    for rec in extra:
        row = rec.to_index_row()
        prev = by_url.get(rec.original_url)
        if prev and prev.get("local_path"):
            row["local_path"] = prev["local_path"]
            row["mime_or_ext"] = prev.get("mime_or_ext")
        by_url[rec.original_url] = row
    rows = list(by_url.values())
    with INDEX_PATH.open("w", encoding="utf-8") as f:
        for row in others + rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(others) + len(rows)


async def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    console.print("[bold]Public store scrape[/bold]")
    public = await public_store_scrape(download=False)
    console.print(f"public courses: {len(public)}")

    if not PROFILE.exists():
        console.print("[red]No Lets Learn collector profile. Run:[/red] python main.py login letslearn")
        raise SystemExit(1)

    console.print("[bold]Authed deep crawl[/bold]")
    deep = await deep_crawl()
    write_raw_json(SOURCE, "records_deep.json", [r.to_index_row() for r in deep])
    total = merge_letslearn(deep)
    with_files = sum(1 for r in deep if r.gdrive_urls or r.type == ResourceType.NOTES)
    console.print(
        f"[bold green]Lets Learn deep added/updated {len(deep)}[/bold green] "
        f"({with_files} file-like; index total {total})"
    )


if __name__ == "__main__":
    asyncio.run(main())
