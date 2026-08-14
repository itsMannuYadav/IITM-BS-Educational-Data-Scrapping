"""Initial discovery stubs for Unknown IITians and Lets Learn."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from rich.console import Console

from core.config import INDEX_PATH, load_index
from core.fetch import extract_gdrive_urls
from core.schema import ResourceRecord, ResourceType
from core.store import write_raw_json

console = Console()

CONFIG = {
    "unknowniitians": {
        "base": "https://www.unknowniitians.com/",
        "seeds": ["/", "/courses", "/free", "/resources", "/study-material", "/blog"],
    },
    "letslearn": {
        "base": "https://www.letslearn1110.com/",
        "seeds": ["/", "/s/courses", "/s/store", "/blog"],
    },
}


async def discover(source: str, headed: bool = False) -> dict[str, Any]:
    cfg = CONFIG[source]
    base = cfg["base"]
    headers = {"User-Agent": "IITM-BS-Educational-Collector/1.0 (approved-collection)"}
    pages = []
    links: list[dict[str, str]] = []
    async with httpx.AsyncClient(timeout=40, headers=headers, follow_redirects=True) as client:
        for seed in cfg["seeds"]:
            url = urljoin(base, seed)
            try:
                resp = await client.get(url)
                html = resp.text
                write_raw_json(source, f"page_{seed.strip('/').replace('/', '_') or 'home'}.meta.json", {
                    "url": str(resp.url),
                    "status": resp.status_code,
                })
                Path(f"data/raw/{source}").mkdir(parents=True, exist_ok=True)
                Path(f"data/raw/{source}/page_{seed.strip('/').replace('/', '_') or 'home'}.html").write_text(
                    html, encoding="utf-8"
                )
                pages.append({"url": str(resp.url), "status": resp.status_code, "bytes": len(html)})
                for href, title in re.findall(
                    r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, flags=re.I | re.S
                ):
                    abs_url = urljoin(str(resp.url), href)
                    text = re.sub(r"<[^>]+>", "", title).strip()[:200]
                    links.append({"url": abs_url, "title": text})
                for m in extract_gdrive_urls(html):
                    links.append({"url": m, "title": "Google Drive resource"})
            except Exception as exc:
                pages.append({"url": url, "error": str(exc)})
    write_raw_json(source, "discover_links.json", links)
    write_raw_json(source, "discover_pages.json", pages)
    return {"source": source, "pages": len(pages), "links": len(links)}


def _interesting(url: str) -> bool:
    u = url.lower()
    return any(
        k in u
        for k in (
            "drive.google",
            "docs.google",
            "notion.",
            "youtube.com",
            "youtu.be",
            "pdf",
            "course",
            "note",
            "material",
            "resource",
            "pyq",
            "paper",
        )
    )


async def scrape(source: str) -> list[ResourceRecord]:
    summary = await discover(source)
    console.print(summary)
    raw = Path(f"data/raw/{source}/discover_links.json")
    links = json.loads(raw.read_text(encoding="utf-8")) if raw.exists() else []
    records: list[ResourceRecord] = []
    seen: set[str] = set()
    for item in links:
        url = (item.get("url") or "").strip()
        if not url or url in seen or not _interesting(url):
            continue
        # skip same-site nav noise without file-ish path unless drive/yt/notion
        host = urlparse(url).netloc
        if host.endswith("letslearn1110.com") or host.endswith("unknowniitians.com"):
            if not any(k in url.lower() for k in ("course", "product", "blog", "material", "pdf")):
                continue
        seen.add(url)
        title = (item.get("title") or url)[:300]
        gdrives = extract_gdrive_urls(url)
        rtype = ResourceType.GDRIVE if gdrives or "drive.google" in url else ResourceType.LINK
        if "youtube" in url or "youtu.be" in url:
            rtype = ResourceType.LECTURE
        if "notion." in url:
            rtype = ResourceType.NOTES
        records.append(
            ResourceRecord(
                source=source,
                title=title or url,
                type=rtype,
                original_url=url,
                gdrive_urls=gdrives,
                extra={"discovered_via": "public_html_pass1"},
            )
        )

    write_raw_json(source, "records.json", [r.to_index_row() for r in records])

    # merge into index
    old = [r for r in load_index() if r.get("source") != source]
    with INDEX_PATH.open("w", encoding="utf-8") as f:
        for row in old:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        for rec in records:
            f.write(rec.model_dump_json() + "\n")
    console.print(f"[green]{source}: indexed {len(records)} candidate links[/green]")
    return records
