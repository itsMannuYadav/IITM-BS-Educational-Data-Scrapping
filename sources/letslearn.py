"""
Lets Learn (Graphy/Spayee) connector — harvest public store course listings.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

import httpx
from rich.console import Console

from core.config import INDEX_PATH, load_index
from core.fetch import extract_gdrive_urls
from core.schema import ResourceRecord, ResourceType
from core.store import write_raw_json

console = Console()
SOURCE = "letslearn"
BASE = "https://www.letslearn1110.com"


def extract_items(payload: Any) -> list[dict]:
    if not isinstance(payload, dict):
        return payload if isinstance(payload, list) else []
    sub = payload.get("sub-home")
    if isinstance(sub, dict) and isinstance(sub.get("data"), list):
        return sub["data"]
    for key in ("courses", "data", "products", "items", "result"):
        if isinstance(payload.get(key), list):
            return payload[key]
    return []


def category_names(payload: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(payload, dict):
        return out
    data = payload.get("data")
    if not isinstance(data, list):
        return out
    for item in data:
        if not isinstance(item, dict):
            continue
        _id = item.get("_id")
        if isinstance(_id, dict):
            for v in _id.values():
                if isinstance(v, str) and v.strip():
                    out.append(v.strip())
        name = item.get("name") or item.get("title")
        if isinstance(name, str):
            out.append(name)
    return list(dict.fromkeys(out))


def course_record(item: dict, cat: str) -> ResourceRecord | None:
    res = item.get("spayee:resource") if isinstance(item.get("spayee:resource"), dict) else item
    title = (res.get("spayee:title") or res.get("title") or res.get("name") or "").strip()
    if not title:
        return None
    slug = res.get("spayee:courseUrl") or res.get("courseUrl") or item.get("_id") or ""
    link = f"{BASE}/courses/{slug}" if slug else f"{BASE}/s/store"
    price = res.get("spayee:finalPrice") or res.get("spayee:price") or res.get("price")
    gdrives = extract_gdrive_urls(json.dumps(item, ensure_ascii=False))
    return ResourceRecord(
        source=SOURCE,
        title=f"{cat} - {title}" if cat else title,
        program=cat or None,
        type=ResourceType.PREVIOUS_PAPER if "pyq" in (cat or "").lower() else ResourceType.LINK,
        original_url=link,
        gdrive_urls=gdrives,
        authors=[res.get("spayee:publisher")] if res.get("spayee:publisher") else [],
        extra={
            "category": cat,
            "price": price,
            "course_id": item.get("_id"),
            "slug": slug,
            "course_type": res.get("spayee:courseType"),
        },
    )


async def scrape_and_download(*, download: bool = False, download_limit: int | None = None) -> list[ResourceRecord]:
    headers = {
        "User-Agent": "IITM-BS-Educational-Collector/1.0 (approved-collection)",
        "Accept": "application/json",
    }
    records: list[ResourceRecord] = []
    async with httpx.AsyncClient(timeout=60, headers=headers, follow_redirects=True) as client:
        cats_payload = (await client.get(f"{BASE}/s/store/courses/categories?queryData=%7B%7D&level=0&occ=IN")).json()
        write_raw_json(SOURCE, "categories.json", cats_payload)
        categories = category_names(cats_payload) or [
            "Foundation",
            "Diploma",
            "Diploma in Programming",
            "Diploma in Data Science",
            "PYQs",
            "Degree",
            "Qualifier",
            "Project",
        ]
        console.print(f"Categories: {categories}")

        # also recommended feed
        feeds = [("", f"{BASE}/s/store/subfilters/courses?occ=IN&page={{page}}&limit=50&sortBy=recommended&onlyMembership=false")]
        for cat in categories:
            feeds.append(
                (
                    cat,
                    f"{BASE}/s/store/subfilters/courses/{quote(cat)}?page={{page}}&limit=50&occ=IN&sortBy=relevance",
                )
            )

        for cat, template in feeds:
            page = 0
            while page < 30:
                url = template.format(page=page)
                resp = await client.get(url)
                if resp.status_code != 200:
                    break
                payload = resp.json()
                write_raw_json(
                    SOURCE,
                    f"courses_{re.sub(r'[^a-z0-9]+', '_', (cat or 'recommended').lower())}_p{page}.json",
                    payload,
                )
                items = extract_items(payload)
                if not items:
                    break
                for it in items:
                    if isinstance(it, dict):
                        rec = course_record(it, cat or "recommended")
                        if rec:
                            records.append(rec)
                if len(items) < 50:
                    break
                page += 1

    seen: set[str] = set()
    uniq: list[ResourceRecord] = []
    for r in records:
        key = r.original_url
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    records = uniq
    write_raw_json(SOURCE, "records.json", [r.to_index_row() for r in records])

    others = [r for r in load_index() if r.get("source") != SOURCE]
    with INDEX_PATH.open("w", encoding="utf-8") as f:
        for row in others:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        for rec in records:
            f.write(rec.model_dump_json() + "\n")
    console.print(f"[bold green]Lets Learn indexed {len(records)} courses[/bold green]")
    return records
