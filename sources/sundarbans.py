"""
Sundarbans Study Corner connector.

Catalog is embedded in a public Vite chunk (StudyView-*.js) — no login needed.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console

from core.config import INDEX_PATH, load_index
from core.fetch import extract_gdrive_urls
from core.schema import ResourceRecord, ResourceType
from core.store import write_raw_json

console = Console()

SOURCE = "sundarbans"
BASE = "https://sundarbans.iitmbs.org"


def classify_link(url: str, bucket: str) -> ResourceType:
    u = url.lower()
    if bucket == "pyq" or "pyq" in u or "question" in u:
        return ResourceType.PREVIOUS_PAPER
    if bucket == "notes" or "note" in u:
        return ResourceType.NOTES
    if bucket == "lectures" or "youtube" in u or "youtu.be" in u:
        return ResourceType.LECTURE
    if "drive.google.com/drive/folders" in u:
        return ResourceType.GDRIVE
    if "notion." in u:
        return ResourceType.NOTES
    if "cheat" in u:
        return ResourceType.CHEAT_SHEET
    if "syllabus" in u:
        return ResourceType.SYLLABUS
    return ResourceType.LINK


async def discover_studyview_js(client: httpx.AsyncClient) -> tuple[str, str]:
    home = await client.get(f"{BASE}/")
    html = home.text
    write_raw_json(SOURCE, "home_meta.json", {"status": home.status_code, "len": len(html)})
    assets = sorted(set(re.findall(r"/assets/[A-Za-z0-9_.-]+\.js", html)))
    # Follow StudyView refs from any asset (usually index-*.js)
    candidates: list[str] = []
    for path in assets:
        text = (await client.get(f"{BASE}{path}")).text
        for ref in re.findall(r"StudyView-[A-Za-z0-9]+\.js", text):
            candidates.append(f"/assets/{ref}")
        if "StudyView" in path:
            candidates.append(path)
    for path in dict.fromkeys(candidates):
        resp = await client.get(f"{BASE}{path}")
        if resp.status_code == 200 and "drive.google.com" in resp.text and "foundation:[" in resp.text:
            return path, resp.text
    raise RuntimeError("Could not locate Sundarbans StudyView catalog chunk")


def js_catalog_to_json(js_text: str) -> dict[str, Any]:
    """Extract foundation/diploma/bs arrays and coerce JS object literals to JSON."""
    start = js_text.find("foundation:[")
    if start < 0:
        raise RuntimeError("foundation:[ not found in StudyView.js")
    # Find end of bs array: after foundation and diploma
    bs_pos = js_text.find("bs:[", start)
    if bs_pos < 0:
        raise RuntimeError("bs:[ not found")
    # Walk brackets from foundation:[
    i = start + len("foundation")
    # rebuild wrapper
    # slice from foundation:[ ... through end of bs:[...]
    # find matching close for the object that contains these three keys is hard;
    # instead extract each level array separately.
    levels: dict[str, str] = {}
    for level in ("foundation", "diploma", "bs"):
        marker = f"{level}:["
        pos = js_text.find(marker, start if level != "foundation" else 0)
        if pos < 0:
            raise RuntimeError(f"{marker} not found")
        arr_start = pos + len(level) + 1  # points at '['
        depth = 0
        j = arr_start
        while j < len(js_text):
            ch = js_text[j]
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    levels[level] = js_text[arr_start : j + 1]
                    break
            elif ch in "\"'":
                quote = ch
                j += 1
                while j < len(js_text):
                    if js_text[j] == "\\":
                        j += 2
                        continue
                    if js_text[j] == quote:
                        break
                    j += 1
            j += 1
        else:
            raise RuntimeError(f"Unclosed array for {level}")

    def coerce(arr_js: str) -> Any:
        s = arr_js
        # quote bare keys
        s = re.sub(r"([{\[,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', s)
        # remove trailing commas
        s = re.sub(r",\s*([}\]])", r"\1", s)
        return json.loads(s)

    return {k: coerce(v) for k, v in levels.items()}


def records_from_catalog(catalog: dict[str, Any]) -> list[ResourceRecord]:
    records: list[ResourceRecord] = []
    seen: set[str] = set()

    def add(rec: ResourceRecord) -> None:
        key = f"{rec.type}|{rec.original_url}|{rec.title}"
        if key in seen or rec.original_url in ("#", "", "about:blank"):
            return
        if rec.original_url.endswith("#") and len(rec.original_url) <= 2:
            return
        seen.add(key)
        records.append(rec)

    for level, courses in catalog.items():
        if not isinstance(courses, list):
            continue
        for course in courses:
            if not isinstance(course, dict):
                continue
            subject = course.get("subject") or ""
            code = course.get("code") or ""
            resources = course.get("resources") or {}
            if not isinstance(resources, dict):
                continue
            for bucket, items in resources.items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    title = (item.get("title") or "Resource").strip()
                    link = (item.get("link") or "").strip()
                    if not link or link == "#":
                        continue
                    full_title = f"{subject} ({code}) - {title}" if code else f"{subject} - {title}"
                    gdrives = extract_gdrive_urls(link)
                    add(
                        ResourceRecord(
                            source=SOURCE,
                            title=full_title,
                            course=subject or None,
                            program=f"{level}/{code}" if code else level,
                            type=classify_link(link, str(bucket)),
                            original_url=link,
                            file_url=link if "drive.google.com/file" in link else None,
                            gdrive_urls=gdrives or ([link] if "drive.google.com" in link else []),
                            extra={
                                "course_code": code,
                                "bucket": bucket,
                                "badge": item.get("badge"),
                                "description": course.get("description"),
                            },
                        )
                    )

    # Also capture loose folder map if present in raw later
    return records


def extract_folder_map(js_text: str) -> list[ResourceRecord]:
    """Extract notes:{ CODE: "drive folder", ... } style maps if present."""
    records: list[ResourceRecord] = []
    for m in re.finditer(
        r'([A-Z]{2,}[A-Z0-9]+)\s*:\s*"(https://drive\.google\.com/drive/folders/[^"]+)"',
        js_text,
    ):
        code, url = m.group(1), m.group(2)
        records.append(
            ResourceRecord(
                source=SOURCE,
                title=f"{code} - Drive folder",
                course=code,
                program="folders",
                type=ResourceType.GDRIVE,
                original_url=url,
                gdrive_urls=[url],
                extra={"course_code": code, "bucket": "folder_map"},
            )
        )
    # dedupe
    seen = set()
    out = []
    for r in records:
        if r.original_url in seen:
            continue
        seen.add(r.original_url)
        out.append(r)
    return out


def merge_into_index(new_records: list[ResourceRecord]) -> int:
    """Replace sundarbans rows; keep other sources + preserve local paths."""
    old_rows = load_index()
    preserved = [r for r in old_rows if r.get("source") != SOURCE]
    old_local = {
        r.get("original_url"): r
        for r in old_rows
        if r.get("source") == SOURCE and r.get("local_path")
    }
    merged: list[dict] = list(preserved)
    for rec in new_records:
        row = rec.to_index_row()
        prev = old_local.get(rec.original_url)
        if prev and prev.get("local_path"):
            row["local_path"] = prev["local_path"]
            row["mime_or_ext"] = prev.get("mime_or_ext")
        merged.append(row)
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with INDEX_PATH.open("w", encoding="utf-8") as f:
        for row in merged:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(merged)


async def discover(headless: bool = True) -> dict[str, Any]:
    async with httpx.AsyncClient(
        timeout=60,
        headers={"User-Agent": "IITM-BS-Educational-Collector/1.0 (approved-collection)"},
        follow_redirects=True,
    ) as client:
        path, text = await discover_studyview_js(client)
    raw_dir = Path("data/raw/sundarbans")
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "StudyView.js").write_text(text, encoding="utf-8")
    (raw_dir / "StudyView.path.txt").write_text(path, encoding="utf-8")
    catalog = js_catalog_to_json(text)
    write_raw_json(SOURCE, "catalog.json", catalog)
    return {
        "chunk": path,
        "bytes": len(text),
        "levels": {k: len(v) if isinstance(v, list) else 0 for k, v in catalog.items()},
    }


async def scrape_and_download(*, download: bool = False, download_limit: int | None = None) -> list[ResourceRecord]:
    async with httpx.AsyncClient(
        timeout=60,
        headers={"User-Agent": "IITM-BS-Educational-Collector/1.0 (approved-collection)"},
        follow_redirects=True,
    ) as client:
        path, text = await discover_studyview_js(client)

    raw_dir = Path("data/raw/sundarbans")
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "StudyView.js").write_text(text, encoding="utf-8")
    (raw_dir / "StudyView.path.txt").write_text(path, encoding="utf-8")

    catalog = js_catalog_to_json(text)
    write_raw_json(SOURCE, "catalog.json", catalog)
    records = records_from_catalog(catalog) + extract_folder_map(text)
    write_raw_json(SOURCE, "records.json", [r.to_index_row() for r in records])

    total = merge_into_index(records)
    console.print(
        f"[bold green]Sundarbans indexed {len(records)} resources[/bold green] "
        f"(index total now {total})"
    )
    if download:
        console.print(
            "[yellow]Drive downloads for Sundarbans: use[/yellow] "
            "python scripts/download_missing.py --source sundarbans --limit N"
        )
    return records
