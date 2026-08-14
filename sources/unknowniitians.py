"""
Unknown IITians connector — public Supabase REST catalog.

Tables (anon key captured at runtime from the site):
  iitm_branch_notes, notes, pyqs, iitm_bs_subjects
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
from playwright.async_api import async_playwright
from rich.console import Console

from core.config import INDEX_PATH, load_index
from core.fetch import extract_gdrive_urls
from core.schema import ResourceRecord, ResourceType
from core.store import write_raw_json

console = Console()
SOURCE = "unknowniitians"
SUPA = "https://qzrvctpwefhmcduariuw.supabase.co"
RAW = Path("data/raw/unknowniitians")
KEY_PATH = RAW / "supabase_anon.txt"


async def capture_anon_key() -> str:
    key: str | None = None
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel="chrome")
        page = await browser.new_page()

        async def on_req(req):
            nonlocal key
            if key or "supabase.co" not in req.url:
                return
            headers = await req.all_headers()
            for k, v in headers.items():
                if k.lower() == "apikey" and v.startswith("eyJ"):
                    key = v
                    return

        page.on("request", on_req)
        await page.goto(
            "https://www.unknowniitians.com/exam-preparation/iitm-bs",
            wait_until="networkidle",
            timeout=90_000,
        )
        await page.wait_for_timeout(1500)
        await browser.close()
    if not key:
        raise RuntimeError("Could not capture Unknown IITians Supabase anon key")
    RAW.mkdir(parents=True, exist_ok=True)
    KEY_PATH.write_text(key, encoding="utf-8")
    return key


async def fetch_table(client: httpx.AsyncClient, table: str, query: str = "select=*&is_active=eq.true") -> list[dict]:
    url = f"{SUPA}/rest/v1/{table}?{query}"
    rows: list[dict] = []
    start = 0
    page = 1000
    while True:
        headers = {"Range": f"{start}-{start + page - 1}", "Prefer": "count=exact"}
        resp = await client.get(url, headers=headers)
        if resp.status_code not in (200, 206):
            console.print(f"[yellow]{table}[/yellow] {resp.status_code} {resp.text[:120]}")
            break
        chunk = resp.json()
        if not isinstance(chunk, list):
            break
        rows.extend(chunk)
        if len(chunk) < page:
            break
        start += page
    write_raw_json(SOURCE, f"{table}.json", rows)
    return rows


def record_from_row(row: dict[str, Any], *, kind: str) -> ResourceRecord | None:
    link = (row.get("file_link") or row.get("content_url") or "").strip()
    title = (row.get("title") or "Resource").strip()
    subject = row.get("subject") or row.get("subject_name")
    branch = row.get("branch")
    level = row.get("level") or row.get("class_level")
    parts = [p for p in [subject, branch, level, title] if p]
    full_title = " - ".join(dict.fromkeys(parts))  # preserve order unique
    gdrives = extract_gdrive_urls(link) if link else []
    if kind == "pyq":
        rtype = ResourceType.PREVIOUS_PAPER
    elif kind == "lecture":
        rtype = ResourceType.LECTURE
    else:
        rtype = ResourceType.NOTES if link or True else ResourceType.LINK
        if link and ("youtube" in link or "youtu.be" in link):
            rtype = ResourceType.LECTURE
        elif gdrives:
            rtype = ResourceType.NOTES

    # Skip junk / empty placeholders without any useful metadata
    if not link and title.lower() in {"qwertyu", "test", "asdf"}:
        return None

    return ResourceRecord(
        source=SOURCE,
        title=full_title[:300],
        course=str(subject) if subject else None,
        term=str(row.get("week_number") or row.get("year") or "") or None,
        program="/".join(str(x) for x in [branch, level] if x),
        type=rtype,
        original_url=link or f"https://www.unknowniitians.com/exam-preparation/iitm-bs#{row.get('id')}",
        file_url=link or None,
        gdrive_urls=gdrives,
        extra={
            "id": row.get("id"),
            "kind": kind,
            "week_number": row.get("week_number"),
            "year": row.get("year"),
            "exam_type": row.get("exam_type"),
            "download_count": row.get("download_count"),
            "has_file_link": bool(link),
        },
    )


def merge_into_index(new_records: list[ResourceRecord]) -> int:
    others = [r for r in load_index() if r.get("source") != SOURCE]
    old_local = {
        r.get("original_url"): r
        for r in load_index()
        if r.get("source") == SOURCE and r.get("local_path")
    }
    rows = list(others)
    for rec in new_records:
        row = rec.to_index_row()
        prev = old_local.get(rec.original_url)
        if prev and prev.get("local_path"):
            row["local_path"] = prev["local_path"]
            row["mime_or_ext"] = prev.get("mime_or_ext")
        rows.append(row)
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with INDEX_PATH.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


async def scrape_and_download(*, download: bool = False, download_limit: int | None = None) -> list[ResourceRecord]:
    key = KEY_PATH.read_text(encoding="utf-8").strip() if KEY_PATH.exists() else await capture_anon_key()
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "User-Agent": "IITM-BS-Educational-Collector/1.0 (approved-collection)",
    }
    async with httpx.AsyncClient(timeout=60, headers=headers, follow_redirects=True) as client:
        branch_notes = await fetch_table(client, "iitm_branch_notes")
        general_notes = await fetch_table(client, "notes")
        pyqs = await fetch_table(client, "pyqs")
        subjects = await fetch_table(client, "iitm_bs_subjects", query="select=*")

    records: list[ResourceRecord] = []
    for row in branch_notes:
        rec = record_from_row(row, kind="iitm_branch_note")
        if rec:
            records.append(rec)
    for row in general_notes:
        rec = record_from_row(row, kind="note")
        if rec:
            records.append(rec)
    for row in pyqs:
        rec = record_from_row(row, kind="pyq")
        if rec:
            records.append(rec)

    with_files = sum(1 for r in records if r.gdrive_urls or (r.file_url and r.file_url.startswith("http")))
    write_raw_json(SOURCE, "records.json", [r.to_index_row() for r in records])
    write_raw_json(SOURCE, "subjects.json", subjects)
    total = merge_into_index(records)
    console.print(
        f"[bold green]Unknown IITians indexed {len(records)}[/bold green] "
        f"({with_files} with downloadable links; index total {total})"
    )
    if download:
        console.print(
            "[yellow]Download Drive files via[/yellow] "
            "python scripts/download_missing.py --source unknowniitians --limit N"
        )
    return records
