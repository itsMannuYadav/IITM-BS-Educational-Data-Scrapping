"""Fetch Unknown IITians notes/PYQs per subject using logged-in JWT."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from rich.console import Console

from core.config import INDEX_PATH, load_index
from core.fetch import extract_gdrive_urls
from core.schema import ResourceRecord, ResourceType
from core.store import write_raw_json
from sources.unknowniitians import (
    KEY_PATH,
    SOURCE,
    SUPA,
    merge_into_index,
    record_from_row,
)

console = Console()
RAW = Path("data/raw/unknowniitians")
USER_JWT = RAW / "supabase_user_jwt.txt"


async def fetch_all(client: httpx.AsyncClient, path_query: str) -> list[dict]:
    url = f"{SUPA}/rest/v1/{path_query}"
    rows: list[dict] = []
    start = 0
    page = 1000
    while True:
        resp = await client.get(url, headers={"Range": f"{start}-{start+page-1}", "Prefer": "count=exact"})
        if resp.status_code not in (200, 206):
            console.print(f"[yellow]{resp.status_code}[/yellow] {path_query[:80]} {resp.text[:100]}")
            break
        chunk = resp.json()
        if not isinstance(chunk, list):
            break
        rows.extend(chunk)
        if len(chunk) < page:
            break
        start += page
    return rows


async def main() -> None:
    if not USER_JWT.exists():
        raise SystemExit("Missing user JWT — login first")
    anon = KEY_PATH.read_text(encoding="utf-8").strip()
    user = USER_JWT.read_text(encoding="utf-8").strip()
    headers = {
        "apikey": anon,
        "Authorization": f"Bearer {user}",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=60, headers=headers, follow_redirects=True) as client:
        subjects = await fetch_all(client, "iitm_bs_subjects?select=*")
        write_raw_json(SOURCE, "subjects_authed.json", subjects)
        console.print(f"subjects={len(subjects)}")

        all_notes: list[dict] = []
        # chunk subject ids
        ids = [s["id"] for s in subjects if "id" in s]
        for i in range(0, len(ids), 15):
            chunk = ids[i : i + 15]
            id_list = ",".join(str(x) for x in chunk)
            q = (
                "iitm_branch_notes?select=*&is_active=eq.true"
                f"&subject_id=in.({id_list})&order=week_number.asc"
            )
            rows = await fetch_all(client, q)
            all_notes.extend(rows)
            with_links = sum(1 for r in rows if r.get("file_link"))
            console.print(f"subjects {chunk[0]}.. rows={len(rows)} links={with_links}")

        # also full tables again
        pyqs = await fetch_all(client, "pyqs?select=*&is_active=eq.true")
        notes = await fetch_all(client, "notes?select=*&is_active=eq.true")

    # dedupe notes by id
    by_id = {}
    for r in all_notes:
        by_id[r.get("id")] = r
    all_notes = list(by_id.values())
    write_raw_json(SOURCE, "iitm_branch_notes_by_subject.json", all_notes)
    write_raw_json(SOURCE, "pyqs_authed.json", pyqs)
    write_raw_json(SOURCE, "notes_authed.json", notes)

    link_count = sum(1 for r in all_notes if r.get("file_link"))
    console.print(f"unique branch notes={len(all_notes)} with file_link={link_count}")
    console.print(f"pyqs={len(pyqs)} with file_link={sum(1 for r in pyqs if r.get('file_link'))}")
    console.print(f"notes={len(notes)} with file_link={sum(1 for r in notes if r.get('file_link'))}")

    records: list[ResourceRecord] = []
    for row in all_notes:
        rec = record_from_row(row, kind="iitm_branch_note")
        if rec:
            records.append(rec)
    for row in pyqs:
        rec = record_from_row(row, kind="pyq")
        if rec:
            records.append(rec)
    for row in notes:
        rec = record_from_row(row, kind="note")
        if rec:
            records.append(rec)

    # Also mine earlier capture for any extra drive links
    capture = RAW / "authed_api_capture.json"
    if capture.exists():
        try:
            bag = json.loads(capture.read_text(encoding="utf-8"))
            for e in bag:
                data = e.get("data")
                rows = data if isinstance(data, list) else []
                for row in rows:
                    if isinstance(row, dict) and (row.get("file_link") or row.get("content_url")):
                        rec = record_from_row(row, kind="capture")
                        if rec:
                            records.append(rec)
        except Exception:
            pass

    best: dict[str, ResourceRecord] = {}
    for rec in records:
        prev = best.get(rec.original_url)
        if not prev or ((rec.gdrive_urls or rec.file_url) and not (prev.gdrive_urls or prev.file_url)):
            best[rec.original_url] = rec
    merged = list(best.values())
    with_files = sum(1 for r in merged if r.gdrive_urls or (r.file_url and str(r.file_url).startswith("http")))
    write_raw_json(SOURCE, "records_authed.json", [r.to_index_row() for r in merged])
    total = merge_into_index(merged)
    console.print(
        f"[bold green]Indexed {len(merged)} Unknown IITians rows[/bold green] "
        f"({with_files} downloadable; index total {total})"
    )


if __name__ == "__main__":
    asyncio.run(main())
