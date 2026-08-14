"""
OPPE Practice connector — IITM BS OPPE PYQs and practice questions.

Public:
  GET /api/subjects/{slug}/questions?offset=N   paginated catalog (30/page)
  Supabase REST  subjects, topics  (anon key from the frontend bundle)

Auth:
  Saved session in .auth/oppepractice.auth.json (Google / Supabase).
  PDF solutions: GET /api/questions/{id}/pdf
  The site requires a phone number on the collector account before PDFs download.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from rich.console import Console

from core.config import INDEX_PATH, REQUEST_DELAY_SECONDS, auth_state_path, load_index
from core.fetch import safe_name
from core.schema import ResourceRecord, ResourceType
from core.store import write_raw_json

console = Console()

SOURCE = "oppepractice"
BASE = "https://oppepractice.iitmbsdegree.in"
SUPA = "https://hzlqdbmyvltvoqiaojjg.supabase.co"
API_DELAY = 0.35
PAGE = 30
FALLBACK_SLUGS = ("python", "dbms", "pdsa", "java", "c", "syscmd", "linux", "embedded-c")
SEED_PATHS = ["/", "/app/subjects", "/leaderboard", "/contact", "/privacy"]
APP_PATHS = ["/app/subjects", "/app/progress", "/app/subjects/python", "/app/subjects/dbms"]

HEADERS = {
    "User-Agent": "IITM-BS-Educational-Collector/1.0 (approved-collection)",
    "Accept": "application/json, text/html",
}
ANON_RE = re.compile(
    r"(eyJ[A-Za-z0-9_\-]+\.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imh6bHFkYm15dmx0dm9xaWFvampn[^\"']+)"
)
SUPA_URL_RE = re.compile(r"https://[a-z0-9]+\.supabase\.co")


def site_cookies() -> dict[str, str]:
    path = auth_state_path(SOURCE)
    if not path.exists():
        return {}
    state = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for cookie in state.get("cookies", []):
        domain = (cookie.get("domain") or "").lstrip(".")
        if "iitmbsdegree.in" in domain:
            out[cookie["name"]] = cookie["value"]
    return out


async def capture_anon_key(client: httpx.AsyncClient) -> str | None:
    cached = Path("data/raw/oppepractice/supabase_anon.txt")
    if cached.exists():
        key = cached.read_text(encoding="utf-8").strip()
        if key.startswith("eyJ"):
            return key
    home = await client.get(f"{BASE}/")
    chunks = sorted(set(re.findall(r"/_next/static/[^\"']+\.js", home.text)))
    for path in chunks:
        text = (await client.get(urljoin(BASE, path))).text
        m = ANON_RE.search(text)
        if m:
            key = m.group(1)
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(key, encoding="utf-8")
            url_m = SUPA_URL_RE.search(text)
            if url_m:
                write_raw_json(SOURCE, "supabase_url.json", {"url": url_m.group(0)})
            return key
    return None


async def supabase_rows(client: httpx.AsyncClient, table: str, anon: str) -> list[dict]:
    headers = {
        "apikey": anon,
        "Authorization": f"Bearer {anon}",
        "Prefer": "count=exact",
    }
    rows: list[dict] = []
    start = 0
    page = 1000
    while True:
        resp = await client.get(
            f"{SUPA}/rest/v1/{table}?select=*",
            headers={**headers, "Range": f"{start}-{start + page - 1}"},
        )
        if resp.status_code not in (200, 206):
            console.print(f"[yellow]supabase {table}[/yellow] {resp.status_code} {resp.text[:120]}")
            break
        chunk = resp.json()
        if not isinstance(chunk, list):
            break
        rows.extend(chunk)
        if len(chunk) < page:
            break
        start += page
    write_raw_json(SOURCE, f"supa_{table}.json", rows)
    return rows


async def fetch_question_page(
    client: httpx.AsyncClient,
    slug: str,
    offset: int,
    cookies: dict[str, str] | None = None,
) -> dict[str, Any]:
    await asyncio.sleep(API_DELAY)
    url = f"{BASE}/api/subjects/{slug}/questions?offset={offset}"
    resp = await client.get(url, cookies=cookies)
    if resp.status_code == 404:
        return {"rows": [], "hasMore": False, "missing": True}
    if resp.status_code != 200:
        console.print(f"[yellow]{resp.status_code}[/yellow] {url}")
        return {"rows": [], "hasMore": False}
    data = resp.json()
    return data if isinstance(data, dict) else {"rows": [], "hasMore": False}


async def harvest_questions(
    client: httpx.AsyncClient, slug: str, cookies: dict[str, str] | None = None
) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        payload = await fetch_question_page(client, slug, offset, cookies=cookies)
        if payload.get("missing") and offset == 0:
            return []
        chunk = payload.get("rows") or []
        if not isinstance(chunk, list):
            break
        rows.extend(q for q in chunk if isinstance(q, dict))
        if not payload.get("hasMore") or not chunk:
            break
        nxt = payload.get("nextOffset")
        offset = int(nxt) if nxt is not None else offset + PAGE
        if offset > 20_000:
            break
    write_raw_json(SOURCE, f"questions_{slug}.json", rows)
    return rows


def classify(exam: str | None, kind: str | None) -> ResourceType:
    exam_l = (exam or "").lower()
    if "oppe" in exam_l or "pyq" in exam_l:
        return ResourceType.PREVIOUS_PAPER
    if (kind or "").lower() in {"coding", "sql"}:
        return ResourceType.PREVIOUS_PAPER
    return ResourceType.OTHER


def question_record(row: dict[str, Any], *, slug: str, subject_name: str) -> ResourceRecord:
    qid = str(row.get("id") or "")
    title = (row.get("title") or "Question").strip()
    exam = row.get("exam")
    topic = row.get("topicName")
    pdf_url = f"{BASE}/api/questions/{qid}/pdf" if qid else None
    page_url = f"{BASE}/app/questions/{qid}" if qid else f"{BASE}/app/subjects/{slug}"
    return ResourceRecord(
        source=SOURCE,
        title=title,
        course=subject_name,
        term=str(exam) if exam else None,
        program=slug.upper(),
        type=classify(exam, row.get("kind")),
        authors=["IITM BS Community"],
        original_url=page_url,
        file_url=pdf_url,
        extra={
            "subject": slug,
            "subject_name": subject_name,
            "topic": topic,
            "topic_id": row.get("topicId"),
            "question_id": qid,
            "kind": row.get("kind"),
            "difficulty": row.get("difficulty"),
            "exam": exam,
            "tags": row.get("tags") or [],
            "week": row.get("week"),
            "pdf_url": pdf_url,
            "category": "practice_question",
        },
    )


def subject_records(subjects: list[dict]) -> list[ResourceRecord]:
    records: list[ResourceRecord] = []
    for sub in subjects:
        slug = sub.get("slug")
        name = sub.get("name") or slug
        if not slug:
            continue
        live = bool(sub.get("is_active"))
        records.append(
            ResourceRecord(
                source=SOURCE,
                title=f"{name} — OPPE Practice",
                course=name,
                program=str(sub.get("short_code") or slug).upper(),
                type=ResourceType.LINK,
                original_url=f"{BASE}/app/subjects/{slug}",
                authors=["IITM BS Community"],
                extra={
                    "subject": slug,
                    "is_active": live,
                    "description": sub.get("description"),
                    "tabs": ["practice", "pyqs", "test-series", "syllabus"],
                    "category": "subject",
                },
            )
        )
        if live:
            for tab, label in (
                ("pyqs", "PYQs"),
                ("test-series", "Test Series"),
                ("syllabus", "Syllabus"),
            ):
                records.append(
                    ResourceRecord(
                        source=SOURCE,
                        title=f"{name} — {label}",
                        course=name,
                        program=str(sub.get("short_code") or slug).upper(),
                        type=ResourceType.SYLLABUS if tab == "syllabus" else ResourceType.LINK,
                        original_url=f"{BASE}/app/subjects/{slug}?tab={tab}",
                        authors=["IITM BS Community"],
                        extra={"subject": slug, "tab": tab, "category": tab},
                    )
                )
    return records


def merge_into_index(new_records: list[ResourceRecord]) -> int:
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
        if prev and prev.get("local_path") and not rec.local_path:
            row["local_path"] = prev["local_path"]
            row["mime_or_ext"] = prev.get("mime_or_ext")
        merged.append(row)
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with INDEX_PATH.open("w", encoding="utf-8") as f:
        for row in merged:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(merged)


async def try_download_pdfs(
    client: httpx.AsyncClient,
    records: list[ResourceRecord],
    cookies: dict[str, str],
    limit: int | None,
) -> int:
    pending = [r for r in records if r.file_url and r.extra.get("question_id") and not r.local_path]
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        return 0
    console.print(f"[bold]PDF downloads[/bold] (limit={limit}, pending={len(pending)})")
    saved = 0
    for rec in pending:
        qid = rec.extra.get("question_id")
        slug = rec.extra.get("subject") or "misc"
        name = safe_name(f"{slug}_{qid[:8]}_{rec.title}.pdf")
        await asyncio.sleep(REQUEST_DELAY_SECONDS)
        try:
            resp = await client.get(rec.file_url or "", cookies=cookies)
        except Exception as exc:
            console.print(f"[red]PDF error[/red] {rec.title}: {exc}")
            continue
        if resp.status_code == 403:
            msg = resp.text.strip()
            console.print(
                f"[yellow]PDF download blocked:[/yellow] {msg or '403'}\n"
                "  Add a phone number on https://oppepractice.iitmbsdegree.in/app/settings "
                "then re-run with downloads enabled."
            )
            break
        if resp.status_code != 200 or b"%PDF" not in resp.content[:8] and not (
            resp.headers.get("content-type") or ""
        ).lower().startswith("application/pdf"):
            console.print(f"[yellow]PDF skip {resp.status_code}[/yellow] {rec.title}")
            continue
        dest = Path("data/files/oppepractice") / slug / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        rec.local_path = str(dest)
        rec.mime_or_ext = "pdf"
        saved += 1
        console.print(f"[green]Saved[/green] {dest} ({len(resp.content)} bytes)")
    return saved


async def discover(headless: bool = True) -> dict[str, Any]:
    pages = []
    links: list[dict[str, str]] = []
    async with httpx.AsyncClient(timeout=40, headers=HEADERS, follow_redirects=True) as client:
        for seed in SEED_PATHS:
            url = urljoin(BASE, seed)
            try:
                resp = await client.get(url)
                page_name = seed.strip("/").replace("/", "_") or "home"
                write_raw_json(SOURCE, f"page_{page_name}.meta.json", {
                    "url": str(resp.url),
                    "status": resp.status_code,
                })
                Path(f"data/raw/{SOURCE}").mkdir(parents=True, exist_ok=True)
                Path(f"data/raw/{SOURCE}/page_{page_name}.html").write_text(resp.text, encoding="utf-8")
                pages.append({"url": str(resp.url), "status": resp.status_code, "bytes": len(resp.text)})
                for href, title in re.findall(
                    r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                    resp.text,
                    flags=re.I | re.S,
                ):
                    text = re.sub(r"<[^>]+>", "", title).strip()[:200]
                    links.append({"url": urljoin(str(resp.url), href), "title": text})
            except Exception as exc:
                pages.append({"url": url, "error": str(exc)})
    write_raw_json(SOURCE, "discover_links.json", links)
    write_raw_json(SOURCE, "discover_pages.json", pages)
    return {"source": SOURCE, "pages": len(pages), "links": len(links)}


async def discover_authenticated(headless: bool = True) -> dict[str, Any]:
    from core.auth import launch_context, save_auth

    api_bag: list[dict[str, Any]] = []
    pw, browser, context = await launch_context(SOURCE, headless=headless, use_saved_auth=True)
    page = await context.new_page()

    async def on_response(resp):
        try:
            ct = (resp.headers.get("content-type") or "").lower()
            if resp.status in (200, 201) and "application/json" in ct:
                try:
                    data = await resp.json()
                except Exception:
                    data = None
                api_bag.append({"url": resp.url, "method": resp.request.method, "status": resp.status, "data": data})
        except Exception:
            return

    page.on("response", on_response)
    pages = []
    for path in APP_PATHS:
        url = f"{BASE}{path}"
        console.print(f"[cyan]Visit (auth)[/cyan] {url}")
        try:
            await page.goto(url, wait_until="networkidle", timeout=60_000)
            await asyncio.sleep(REQUEST_DELAY_SECONDS)
            pages.append({"url": page.url, "title": await page.title(), "authenticated": True})
        except Exception as exc:
            pages.append({"url": url, "error": str(exc), "authenticated": True})
    write_raw_json(SOURCE, "browser_api_payloads_auth.json", api_bag)
    write_raw_json(SOURCE, "browser_pages_auth.json", pages)
    await save_auth(context, SOURCE)
    await context.close()
    if browser:
        await browser.close()
    await pw.stop()
    return {"source": SOURCE, "authenticated": True, "pages": len(pages), "api_calls": len(api_bag)}


async def scrape_and_download(
    *, download: bool = False, download_limit: int | None = None
) -> list[ResourceRecord]:
    cookies = site_cookies()
    if cookies:
        console.print(f"[green]Loaded {len(cookies)} site cookies[/green]")
    else:
        console.print(
            "[yellow]No saved OPPE session.[/yellow] Catalog is public; "
            "PDFs need `python main.py login oppepractice` plus a phone number on the account."
        )

    records: list[ResourceRecord] = []
    async with httpx.AsyncClient(
        timeout=60,
        headers=HEADERS,
        follow_redirects=True,
    ) as client:
        anon = await capture_anon_key(client)
        subjects: list[dict] = []
        if anon:
            subjects = await supabase_rows(client, "subjects", anon)
            topics = await supabase_rows(client, "topics", anon)
            console.print(f"[green]Subjects {len(subjects)}[/green]  topics {len(topics)}")
        else:
            console.print("[yellow]Could not extract Supabase anon key — using slug fallback[/yellow]")

        if subjects:
            records.extend(subject_records(subjects))
            slugs = [s["slug"] for s in subjects if s.get("slug")]
            names = {s["slug"]: s.get("name") or s["slug"] for s in subjects if s.get("slug")}
        else:
            slugs = list(FALLBACK_SLUGS)
            names = {s: s.upper() for s in slugs}
            records.extend(
                subject_records([{"slug": s, "name": s, "is_active": s in ("python", "dbms")} for s in slugs])
            )

        by_slug: dict[str, list[dict]] = {}
        for slug in slugs:
            console.print(f"[bold]Questions[/bold] {slug}")
            rows = await harvest_questions(client, slug, cookies=cookies)
            if not rows:
                console.print(f"  [dim]{slug}: no catalog (inactive or empty)[/dim]")
                continue
            by_slug[slug] = rows
            name = names.get(slug, slug)
            for row in rows:
                records.append(question_record(row, slug=slug, subject_name=name))
            console.print(f"  [green]{slug}: {len(rows)} questions[/green]")

        write_raw_json(
            SOURCE,
            "harvest_summary.json",
            {slug: len(rows) for slug, rows in by_slug.items()},
        )

        if download:
            await try_download_pdfs(client, records, cookies, download_limit)

    write_raw_json(SOURCE, "records.json", [r.to_index_row() for r in records])
    total = merge_into_index(records)
    console.print(f"[green]{SOURCE}: indexed {len(records)} resources[/green] (index now {total})")
    return records
