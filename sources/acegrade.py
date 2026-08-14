"""
AceGrade connector — uses their Cloud Run backend API.

Public (no login):
  GET /backendapi/term/{term}
  GET /backendapi/pyq/{courseId}/{ds|es}
  GET /backendapi/notes/get_notes/{courseId}/{ds|es}

Auth required later:
  GET /backendapi/course/{courseId}   (lectures)
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console
from rich.progress import Progress

from core.auth import launch_context, save_auth
from core.config import INDEX_PATH, REQUEST_DELAY_SECONDS, load_index
from core.fetch import download_file, extract_gdrive_urls, safe_name
from core.schema import ResourceRecord, ResourceType
from core.store import write_raw_json
from core.token import load_auth_token

console = Console()

SOURCE = "acegrade"
BASE = "https://www.acegrade.in"
API_BASE = "https://project-b-backend-3uoftpsktq-el.a.run.app/backendapi"
DEFAULT_TERM = "24t3"
PROGRAMS = ("ds", "es")
API_DELAY = 0.35

DRIVE_FILE_RE = re.compile(r"https?://drive\.google\.com/file/d/([a-zA-Z0-9_-]+)", re.I)

SEED_PATHS = ["/", "/prev_papers", "/notes", "/lectures", "/books", "/login"]


def gdrive_uc_url(file_id: str) -> str:
    return f"https://drive.google.com/uc?export=download&id={file_id}"


def extract_drive_id(url: str) -> str | None:
    m = DRIVE_FILE_RE.search(url)
    return m.group(1) if m else None


async def fetch_json(client: httpx.AsyncClient, url: str, *, delay: float = API_DELAY) -> Any | None:
    await asyncio.sleep(delay)
    try:
        resp = await client.get(url)
        if resp.status_code == 404:
            return None
        if resp.status_code == 401:
            console.print(f"[yellow]auth required[/yellow] {url}")
            return {"_auth_required": True}
        if resp.status_code != 200:
            console.print(f"[yellow]{resp.status_code}[/yellow] {url}")
            return None
        return resp.json()
    except Exception as exc:
        console.print(f"[red]API error[/red] {url}: {exc}")
        return None


async def fetch_course_catalog(client: httpx.AsyncClient, term: str = DEFAULT_TERM) -> dict[str, Any]:
    data = await fetch_json(client, f"{API_BASE}/term/{term}")
    write_raw_json(SOURCE, f"term_{term}.json", data or {})
    return data or {}


def iter_courses(term_payload: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    meta = term_payload.get("course_metadata") or {}
    for level, mapping in meta.items():
        if not isinstance(mapping, dict):
            continue
        for course_id, course_name in mapping.items():
            out.append({"level": level, "course_id": course_id, "course_name": str(course_name)})
    return out


def records_from_pyq_payload(
    *,
    payload: dict[str, Any],
    course_id: str,
    course_name: str,
    level: str,
    program: str,
) -> list[ResourceRecord]:
    records: list[ResourceRecord] = []
    for exam_type, items in (payload or {}).items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            url = (item.get("url") or "").strip()
            if not url:
                continue
            term = item.get("term") or ""
            title = f"{course_name} ({course_id}) - {exam_type} - {term}".strip(" -")
            records.append(
                ResourceRecord(
                    source=SOURCE,
                    title=title,
                    course=course_name,
                    term=str(term) if term else None,
                    program=f"{level}/{program}",
                    type=ResourceType.PREVIOUS_PAPER,
                    authors=[],
                    original_url=url,
                    file_url=url,
                    gdrive_urls=extract_gdrive_urls(url) or ([url] if "drive.google" in url else []),
                    extra={
                        "course_id": course_id,
                        "exam_type": exam_type,
                        "downloads": item.get("downloads"),
                        "api": f"{API_BASE}/pyq/{course_id}/{program}",
                    },
                )
            )
    return records


def records_from_notes_payload(
    *,
    payload: dict[str, Any],
    course_id: str,
    course_name: str,
    level: str,
    program: str,
) -> list[ResourceRecord]:
    records: list[ResourceRecord] = []
    content = (payload or {}).get("content") or {}
    notes_groups = content.get("notes") or []
    if not isinstance(notes_groups, list):
        return records
    for group in notes_groups:
        if not isinstance(group, dict):
            continue
        author = (group.get("source") or "").strip()
        items = group.get("content") or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            url = (item.get("url") or "").strip()
            if not url:
                continue
            note_title = (item.get("title") or "Notes").strip()
            title = f"{course_name} ({course_id}) - {note_title}"
            if author:
                title = f"{title} - {author}"
            records.append(
                ResourceRecord(
                    source=SOURCE,
                    title=title,
                    course=course_name,
                    program=f"{level}/{program}",
                    type=ResourceType.NOTES,
                    authors=[author] if author else [],
                    original_url=url,
                    file_url=url,
                    gdrive_urls=extract_gdrive_urls(url) or ([url] if "drive.google" in url else []),
                    extra={
                        "course_id": course_id,
                        "note_title": note_title,
                        "downloads": item.get("downloads"),
                        "api": f"{API_BASE}/notes/get_notes/{course_id}/{program}",
                    },
                )
            )
    return records


async def harvest_pyqs(client: httpx.AsyncClient, courses: list[dict[str, str]]) -> list[ResourceRecord]:
    records: list[ResourceRecord] = []
    raw_dump: dict[str, Any] = {}
    with Progress() as progress:
        task = progress.add_task("PYQs", total=len(courses) * len(PROGRAMS))
        for course in courses:
            for program in PROGRAMS:
                url = f"{API_BASE}/pyq/{course['course_id']}/{program}"
                payload = await fetch_json(client, url)
                progress.advance(task)
                if not payload or not isinstance(payload, dict) or payload.get("_auth_required"):
                    continue
                key = f"{course['course_id']}_{program}"
                raw_dump[key] = payload
                records.extend(
                    records_from_pyq_payload(
                        payload=payload,
                        course_id=course["course_id"],
                        course_name=course["course_name"],
                        level=course["level"],
                        program=program,
                    )
                )
    write_raw_json(SOURCE, "pyq_all.json", raw_dump)
    return records


async def harvest_notes(client: httpx.AsyncClient, courses: list[dict[str, str]]) -> list[ResourceRecord]:
    records: list[ResourceRecord] = []
    raw_dump: dict[str, Any] = {}
    with Progress() as progress:
        task = progress.add_task("Notes", total=len(courses) * len(PROGRAMS))
        for course in courses:
            for program in PROGRAMS:
                url = f"{API_BASE}/notes/get_notes/{course['course_id']}/{program}"
                payload = await fetch_json(client, url)
                progress.advance(task)
                if not payload or not isinstance(payload, dict) or payload.get("_auth_required"):
                    continue
                key = f"{course['course_id']}_{program}"
                raw_dump[key] = payload
                records.extend(
                    records_from_notes_payload(
                        payload=payload,
                        course_id=course["course_id"],
                        course_name=course["course_name"],
                        level=course["level"],
                        program=program,
                    )
                )
    write_raw_json(SOURCE, "notes_all.json", raw_dump)
    return records


async def download_gdrive_files(records: list[ResourceRecord], *, limit: int | None = None) -> int:
    targets = [r for r in records if r.gdrive_urls and extract_drive_id(r.gdrive_urls[0])]
    if limit is not None:
        targets = targets[:limit]
    saved = 0
    async with httpx.AsyncClient(follow_redirects=True, timeout=120.0) as client:
        for rec in targets:
            url = rec.gdrive_urls[0]
            file_id = extract_drive_id(url)
            assert file_id
            kind = "notes" if rec.type == ResourceType.NOTES else "pyq"
            dest_name = safe_name(
                f"{rec.extra.get('course_id', 'x')}_{kind}_{rec.extra.get('note_title') or rec.extra.get('exam_type') or 'file'}_{rec.term or ''}.pdf"
            )
            path = await download_file(
                client,
                gdrive_uc_url(file_id),
                SOURCE,
                subdir=str(Path(kind) / str(rec.extra.get("course_id") or "misc")),
                suggested_name=dest_name,
            )
            if not path:
                continue
            if path.stat().st_size < 500:
                path.unlink(missing_ok=True)
                continue
            head = path.read_bytes()[:300].lower()
            if b"<html" in head or b"<!doctype" in head:
                console.print(f"[yellow]Drive gate HTML — link kept only:[/yellow] {rec.title[:80]}")
                path.unlink(missing_ok=True)
                continue
            rec.local_path = str(path)
            rec.mime_or_ext = path.suffix
            saved += 1
    return saved


async def discover(headless: bool = True) -> dict[str, Any]:
    api_bag: list[dict[str, Any]] = []
    pw, browser, context = await launch_context(SOURCE, headless=headless, use_saved_auth=True)
    page = await context.new_page()

    async def on_response(resp):
        try:
            ct = (resp.headers.get("content-type") or "").lower()
            url = resp.url
            if resp.status == 200 and ("application/json" in ct or "backendapi" in url):
                try:
                    data = await resp.json()
                except Exception:
                    data = {"_raw_text": (await resp.text())[:20_000]}
                api_bag.append({"url": url, "data": data})
        except Exception:
            return

    page.on("response", on_response)
    pages = []
    for path in SEED_PATHS:
        url = f"{BASE}{path}"
        console.print(f"[cyan]Visit[/cyan] {url}")
        try:
            await page.goto(url, wait_until="networkidle", timeout=60_000)
            await asyncio.sleep(REQUEST_DELAY_SECONDS)
            pages.append({"url": page.url, "title": await page.title()})
        except Exception as exc:
            pages.append({"url": url, "error": str(exc)})
    write_raw_json(SOURCE, "browser_api_payloads.json", api_bag)
    write_raw_json(SOURCE, "browser_pages.json", pages)
    await save_auth(context, SOURCE)
    await context.close()
    if browser:
        await browser.close()
    await pw.stop()
    return {"pages": len(pages), "api": len(api_bag)}


def static_doc_records() -> list[ResourceRecord]:
    docs = [
        (
            "ES Handbook",
            "https://docs.google.com/document/u/1/d/e/2PACX-1vSD1ldcEz7GatzyEJyMkQMZmSyf4INBZ8AlD3b8SV8jksl7HgYyKqOsR5QjuVYz8A/pub",
            ResourceType.SYLLABUS,
        ),
        (
            "ES Grading doc",
            "https://docs.google.com/document/u/1/d/e/2PACX-1vQUOCI-V6exuvJsgPNZMDIOD3APbyM3kNuclVJrCFoXO3BocUErW_wvoUJoP0lii4dwX9PTzF3ZB8w5/pub",
            ResourceType.OTHER,
        ),
        (
            "DS Handbook",
            "https://docs.google.com/document/u/1/d/e/2PACX-1vRxGnnDCVAO3KX2CGtMIcJQuDrAasVk2JHbDxkjsGrTP5ShhZK8N6ZSPX89lexKx86QPAUswSzGLsOA/pub",
            ResourceType.SYLLABUS,
        ),
        (
            "DS Grading doc",
            "https://docs.google.com/document/d/e/2PACX-1vSUvKzH7yIXNVwUgRYSIT8M0x1jhFSkslEtj9UPo3dtWI_sJ38Hh_PzbBygpF0vIOo8K7lTy-uYkqdu/pub",
            ResourceType.OTHER,
        ),
    ]
    out: list[ResourceRecord] = []
    for title, url, rtype in docs:
        out.append(
            ResourceRecord(
                source=SOURCE,
                title=title,
                type=rtype,
                original_url=url,
                gdrive_urls=[url],
                program="docs",
            )
        )
    return out


async def harvest_lectures(
    client: httpx.AsyncClient,
    courses: list[dict[str, str]],
    *,
    term_payload: dict[str, Any],
) -> list[ResourceRecord]:
    token = load_auth_token(SOURCE)
    if not token:
        console.print("[yellow]No auth_token — skip lectures. Run: python main.py login acegrade[/yellow]")
        return []

    prefix = term_payload.get("prefix") or "ns"
    term = term_payload.get("term") or DEFAULT_TERM
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    records: list[ResourceRecord] = []
    raw_dump: dict[str, Any] = {}

    with Progress() as progress:
        task = progress.add_task("Lectures", total=len(courses))
        for course in courses:
            course_key = f"{prefix}_{term}_{course['course_id']}"
            url = f"{API_BASE}/course/{course_key}"
            await asyncio.sleep(API_DELAY)
            try:
                resp = await client.get(url, headers=headers)
            except Exception as exc:
                console.print(f"[red]lecture error[/red] {course_key}: {exc}")
                progress.advance(task)
                continue
            progress.advance(task)
            if resp.status_code != 200:
                console.print(f"[yellow]{resp.status_code}[/yellow] {course_key}")
                continue
            payload = resp.json()
            raw_dump[course_key] = payload
            course_title = payload.get("title") or course["course_name"]
            for week in payload.get("week_wise") or []:
                if not isinstance(week, dict):
                    continue
                week_title = week.get("title") or "Week"
                for video in week.get("videos") or []:
                    if not isinstance(video, dict):
                        continue
                    yt = (video.get("yt_vid") or "").strip()
                    if not yt:
                        continue
                    vtitle = (video.get("title") or "Lecture").strip()
                    yt_url = f"https://www.youtube.com/watch?v={yt}"
                    records.append(
                        ResourceRecord(
                            source=SOURCE,
                            title=f"{course_title} ({course['course_id']}) - {week_title} - {vtitle}",
                            course=course_title,
                            term=week_title,
                            program=course["level"],
                            type=ResourceType.LECTURE,
                            original_url=yt_url,
                            file_url=yt_url,
                            extra={
                                "course_id": course["course_id"],
                                "api_course_id": course_key,
                                "yt_vid": yt,
                                "duration": video.get("duration"),
                                "availability": video.get("availability"),
                                "transcript_vtt_url": video.get("transcript_vtt_url") or None,
                                "forum_url": payload.get("forum_url"),
                                "api": url,
                            },
                        )
                    )
    write_raw_json(SOURCE, "lectures_all.json", raw_dump)
    return records


def merge_preserve_local_paths(new_records: list[ResourceRecord]) -> list[ResourceRecord]:
    """Keep previously downloaded local_path values when re-indexing."""
    old = {r.get("original_url"): r for r in load_index() if r.get("original_url")}
    out: list[ResourceRecord] = []
    for rec in new_records:
        prev = old.get(rec.original_url)
        if prev and prev.get("local_path") and not rec.local_path:
            rec.local_path = prev["local_path"]
            rec.mime_or_ext = prev.get("mime_or_ext") or rec.mime_or_ext
        out.append(rec)
    return out


async def scrape_and_download(
    *,
    download: bool = True,
    download_limit: int | None = None,
    term: str = DEFAULT_TERM,
) -> list[ResourceRecord]:
    headers = {
        "User-Agent": "IITM-BS-Educational-Collector/1.0 (approved-collection)",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=60.0, headers=headers, follow_redirects=True) as client:
        term_payload = await fetch_course_catalog(client, term=term)
        courses = iter_courses(term_payload)
        console.print(f"[bold]Courses in term {term}:[/bold] {len(courses)}")

        notes = await harvest_notes(client, courses)
        console.print(f"[green]Notes links:[/green] {len(notes)}")
        pyqs = await harvest_pyqs(client, courses)
        console.print(f"[green]PYQ links:[/green] {len(pyqs)}")
        lectures = await harvest_lectures(client, courses, term_payload=term_payload)
        console.print(f"[green]Lecture videos:[/green] {len(lectures)}")

        records = notes + pyqs + lectures + static_doc_records()

    saved = 0
    if download:
        console.print(f"[bold]Drive downloads[/bold] (limit={download_limit})")
        # Only attempt Drive-backed notes/PYQs
        drive_records = [r for r in records if r.gdrive_urls]
        saved = await download_gdrive_files(drive_records, limit=download_limit)
        console.print(f"[green]Files saved locally:[/green] {saved}")

    records = merge_preserve_local_paths(records)
    write_raw_json(SOURCE, "records.json", [r.to_index_row() for r in records])
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with INDEX_PATH.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(r.model_dump_json() + "\n")

    console.print(f"[bold green]Indexed {len(records)} AceGrade resources[/bold green] -> {INDEX_PATH}")
    return records
