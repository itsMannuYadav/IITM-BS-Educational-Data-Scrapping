"""Follow Unknown IITians notes/PYQ section pages and extract Drive/resource links."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.async_api import async_playwright
from rich.console import Console

from core.config import INDEX_PATH, load_index
from core.fetch import extract_gdrive_urls
from core.schema import ResourceRecord, ResourceType
from core.store import write_raw_json

console = Console()
SOURCE = "unknowniitians"


async def main() -> None:
    seed_records = json.loads((ROOT / "data/raw/unknowniitians/records.json").read_text(encoding="utf-8"))
    targets = [
        r["original_url"]
        for r in seed_records
        if "exam-preparation" in r.get("original_url", "")
        and any(k in r["original_url"] for k in ("/notes", "/pyqs"))
    ]
    targets = list(dict.fromkeys(targets))
    console.print(f"Deep targets: {len(targets)}")

    links: list[dict] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel="chrome")
        page = await browser.new_page()
        for url in targets:
            console.print(f"[cyan]Deep[/cyan] {url}")
            try:
                await page.goto(url, wait_until="networkidle", timeout=60_000)
                await page.wait_for_timeout(2500)
                # click "load more" style buttons if any
                for _ in range(5):
                    try:
                        btn = page.get_by_role("button", name=lambda n: n and "more" in n.lower())
                        if await btn.count():
                            await btn.first.click(timeout=1500)
                            await page.wait_for_timeout(1000)
                        else:
                            break
                    except Exception:
                        break
                items = await page.eval_on_selector_all(
                    "a[href]",
                    """els => els.map(a => ({href:a.href||'', text:(a.innerText||'').trim().slice(0,200)}))""",
                )
                html = await page.content()
                for it in items:
                    links.append({"url": it["href"], "title": it["text"], "from": url})
                for m in extract_gdrive_urls(html):
                    links.append({"url": m, "title": "Google Drive resource", "from": url})
            except Exception as exc:
                console.print(f"[red]fail[/red] {url}: {exc}")
        await browser.close()

    write_raw_json(SOURCE, "deep_links.json", links)

    # merge with existing unknowniitians records
    existing = [r for r in load_index() if r.get("source") == SOURCE]
    others = [r for r in load_index() if r.get("source") != SOURCE]
    by_url = {r["original_url"]: r for r in existing}
    added = 0
    for item in links:
        url = (item.get("url") or "").strip()
        if not url or url in by_url:
            continue
        if not any(k in url.lower() for k in ("drive.google", "docs.google", "notion.", "youtube", "youtu.be", ".pdf", "/notes/", "material")):
            continue
        gdrives = extract_gdrive_urls(url)
        rtype = ResourceType.LINK
        if gdrives or "drive.google" in url:
            rtype = ResourceType.GDRIVE
        elif "youtube" in url or "youtu.be" in url:
            rtype = ResourceType.LECTURE
        elif "notion" in url or "note" in url.lower():
            rtype = ResourceType.NOTES
        rec = ResourceRecord(
            source=SOURCE,
            title=(item.get("title") or url)[:300],
            type=rtype,
            original_url=url,
            gdrive_urls=gdrives,
            extra={"discovered_via": "deep_notes_pages", "from": item.get("from")},
        )
        by_url[url] = rec.to_index_row()
        added += 1

    all_ui = list(by_url.values())
    write_raw_json(SOURCE, "records.json", all_ui)
    with INDEX_PATH.open("w", encoding="utf-8") as f:
        for row in others + all_ui:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    console.print(f"[green]Added {added} deep links. Unknown IITians total: {len(all_ui)}[/green]")


if __name__ == "__main__":
    asyncio.run(main())
