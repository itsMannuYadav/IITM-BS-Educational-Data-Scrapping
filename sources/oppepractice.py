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

from core.config import INDEX_PATH, REQUEST_DELAY_SECONDS, auth_state_path, load_index, source_files_dir
from core.fetch import safe_name
from core.schema import ResourceRecord, ResourceType
from core.store import write_raw_json

console = Console()

SOURCE = "oppepractice"
BASE = "https://oppepractice.iitmbsdegree.in"
SUPA = "https://hzlqdbmyvltvoqiaojjg.supabase.co"
API_DELAY = 0.35
PAGE = 30
JSON_DIRNAME = "json"
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


RSC_REF_RE = re.compile(r"^\$L?([0-9a-fA-F]+)$")
RSC_HEADERS = {
    "RSC": "1",
    "Accept": "text/x-component",
    "User-Agent": HEADERS["User-Agent"],
}


def parse_rsc_text_chunks(text: str) -> dict[str, str]:
    """Parse Next.js flight rows. `id:T{hexlen},{bytes}` lengths are UTF-8 bytes
    and the next row may start immediately, with no newline."""
    data = text.encode("utf-8")
    hexdigits = b"0123456789abcdefABCDEF"
    chunks: dict[str, str] = {}
    i = 0
    n = len(data)
    while i < n:
        if data[i] in (10, 13):
            i += 1
            continue
        j = i
        while j < n and data[j] in hexdigits:
            j += 1
        if j == i or j >= n or data[j] != 58:  # ':'
            i += 1
            continue
        cid = data[i:j].decode("ascii").lower()
        j += 1
        if j < n and data[j] == 84:  # 'T'
            k = j + 1
            while k < n and data[k] in hexdigits:
                k += 1
            if k > j + 1 and k < n and data[k] == 44:  # ','
                size = int(data[j + 1 : k], 16)
                start = k + 1
                payload = data[start : start + size]
                try:
                    chunks[cid] = payload.decode("utf-8")
                except UnicodeDecodeError:
                    chunks[cid] = payload.decode("utf-8", errors="replace")
                i = start + size
                continue
        nl = data.find(b"\n", j)
        if nl < 0:
            break
        i = nl + 1
    return chunks


def _json_slice(text: str, start: int) -> str | None:
    if start >= len(text) or text[start] not in "{[":
        return None
    stack: list[str] = []
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]":
            if not stack or stack[-1] != ch:
                return None
            stack.pop()
            if not stack:
                return text[start : i + 1]
    return None


def is_rsc_ref(value: Any) -> bool:
    return isinstance(value, str) and bool(RSC_REF_RE.fullmatch(value))


def resolve_rsc_refs(value: Any, chunks: dict[str, str]) -> Any:
    if value == "$undefined":
        return None
    if isinstance(value, str):
        m = RSC_REF_RE.fullmatch(value)
        if not m:
            return value
        cid = m.group(1).lower()
        if cid in chunks:
            return chunks[cid]
        return value
    if isinstance(value, list):
        return [resolve_rsc_refs(v, chunks) for v in value]
    if isinstance(value, dict):
        return {k: resolve_rsc_refs(v, chunks) for k, v in value.items()}
    return value


def payload_needs_refetch(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return True
    body = payload.get("body_md")
    if not body or is_rsc_ref(body):
        return True
    stack: list[Any] = [payload]
    while stack:
        cur = stack.pop()
        if is_rsc_ref(cur):
            return True
        if isinstance(cur, dict):
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return False


def _loads_rsc_json(blob: str) -> Any:
    return json.loads(blob.replace("$undefined", "null"))


def extract_rsc_object(text: str, key: str) -> dict[str, Any] | None:
    chunks = parse_rsc_text_chunks(text)
    needle = f'"{key}":'
    pos = 0
    while True:
        idx = text.find(needle, pos)
        if idx < 0:
            return None
        i = idx + len(needle)
        while i < len(text) and text[i] in " \n\r\t":
            i += 1
        blob = _json_slice(text, i)
        if blob:
            try:
                data = _loads_rsc_json(blob)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                data = resolve_rsc_refs(data, chunks)
                if key == "current" and (data.get("body_md") is not None or data.get("id")):
                    return data
                if key == "subject" and data.get("slug"):
                    return data
                if key not in {"current", "subject"}:
                    return data
        pos = idx + len(needle)


def extract_keyed_arrays(text: str, key: str) -> list[dict[str, Any]]:
    needle = f'"{key}":'
    out: list[dict[str, Any]] = []
    pos = 0
    while True:
        idx = text.find(needle, pos)
        if idx < 0:
            break
        i = idx + len(needle)
        while i < len(text) and text[i] in " \n\r\t":
            i += 1
        blob = _json_slice(text, i)
        pos = idx + len(needle)
        if not blob:
            continue
        try:
            data = _loads_rsc_json(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            out.extend(row for row in data if isinstance(row, dict))
    return out


def extract_test_run(text: str) -> dict[str, Any] | None:
    chunks = parse_rsc_text_chunks(text)
    pos = 0
    while True:
        idx = text.find('{"slug":', pos)
        if idx < 0:
            return None
        blob = _json_slice(text, idx)
        pos = idx + 8
        if not blob:
            continue
        try:
            data = _loads_rsc_json(blob)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or not isinstance(data.get("sections"), list):
            continue
        resolved = resolve_rsc_refs(data, chunks)
        if resolved.get("sections"):
            return resolved
    return None


async def fetch_rsc(client: httpx.AsyncClient, url: str, cookies: dict[str, str] | None = None) -> httpx.Response:
    await asyncio.sleep(API_DELAY)
    return await client.get(url, cookies=cookies, headers=RSC_HEADERS)


def question_json_path(slug: str, qid: str, title: str) -> Path:
    name = safe_name(f"{qid[:8]}_{title}.json")
    return source_files_dir(SOURCE) / JSON_DIRNAME / slug / name


async def fetch_question_detail(
    client: httpx.AsyncClient, qid: str, cookies: dict[str, str] | None = None
) -> dict[str, Any] | None:
    url = f"{BASE}/app/questions/{qid}"
    resp = await fetch_rsc(client, url, cookies=cookies)
    if resp.status_code != 200:
        console.print(f"[yellow]detail {resp.status_code}[/yellow] {qid}")
        return None
    current = extract_rsc_object(resp.text, "current")
    subject = extract_rsc_object(resp.text, "subject")
    if not current:
        return None
    if subject:
        current["subject"] = subject
    return current


async def harvest_question_jsons(
    client: httpx.AsyncClient,
    records: list[ResourceRecord],
    cookies: dict[str, str],
    *,
    limit: int | None = None,
) -> list[dict]:
    pending = [r for r in records if r.extra.get("question_id")]
    if limit is not None:
        pending = pending[:limit]
    console.print(f"[bold]Question JSON[/bold] ({len(pending)} questions)")
    saved = 0
    skipped = 0
    failed = 0
    combined: list[dict] = []
    for i, rec in enumerate(pending, 1):
        qid = str(rec.extra.get("question_id"))
        slug = str(rec.extra.get("subject") or "misc")
        dest = question_json_path(slug, qid, rec.title)
        if dest.exists() and dest.stat().st_size > 50:
            try:
                payload = json.loads(dest.read_text(encoding="utf-8"))
            except Exception:
                payload = None
            if isinstance(payload, dict) and not payload_needs_refetch(payload):
                rec.extra["json_path"] = str(dest)
                rec.extra["has_body"] = True
                rec.extra["has_solution"] = bool(
                    payload.get("solution_md") or payload.get("reference_sql")
                )
                combined.append(payload)
                skipped += 1
                continue
        detail = await fetch_question_detail(client, qid, cookies=cookies)
        if not detail or payload_needs_refetch(detail):
            failed += 1
            if failed <= 5 or i % 50 == 0:
                console.print(f"  [yellow]incomplete body[/yellow] {rec.title}")
            continue
        nested_subject = detail.pop("subject", None)
        if isinstance(nested_subject, dict):
            slug = str(nested_subject.get("slug") or slug)
            subject_name = nested_subject.get("name") or rec.course
        else:
            subject_name = rec.course
        payload = {
            "source": SOURCE,
            "url": rec.original_url,
            "subject": slug,
            "subject_name": subject_name,
            "exam": rec.term or rec.extra.get("exam"),
            "tags": rec.extra.get("tags") or [],
            "topic_id": rec.extra.get("topic_id"),
            **detail,
        }
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        rec.extra["json_path"] = str(dest)
        rec.extra["has_body"] = True
        rec.extra["has_solution"] = bool(payload.get("solution_md") or payload.get("reference_sql"))
        combined.append(payload)
        saved += 1
        if saved <= 3 or saved % 25 == 0:
            console.print(f"  [green]{saved}[/green] {slug}/{rec.title[:50]}")
    console.print(
        f"[green]Practice JSON saved {saved}[/green]  skipped complete {skipped}  "
        f"failed {failed}  total {saved + skipped}"
    )
    return combined


def write_full_questions(rows: list[dict[str, Any]]) -> None:
    write_raw_json(SOURCE, "questions_full.json", rows)
    jsonl = Path("data/raw/oppepractice/questions_full.jsonl")
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    with jsonl.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_set_looks_valid(row: dict[str, Any]) -> bool:
    return bool(row.get("id") and row.get("name") and ("questionCount" in row or "available" in row))


async def discover_test_sets(
    client: httpx.AsyncClient,
    slugs: list[str],
    cookies: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for slug in slugs:
        for tab in ("test-series", "pyqs"):
            url = f"{BASE}/app/subjects/{slug}?tab={tab}"
            resp = await fetch_rsc(client, url, cookies=cookies)
            if resp.status_code != 200:
                console.print(f"[yellow]test list {resp.status_code}[/yellow] {slug}?tab={tab}")
                continue
            for key in ("sets", "past"):
                for item in extract_keyed_arrays(resp.text, key):
                    if not test_set_looks_valid(item):
                        continue
                    sid = str(item["id"])
                    row = {
                        **item,
                        "subject": slug,
                        "tab": tab,
                        "list_source": key,
                    }
                    prev = found.get((slug, sid))
                    if prev:
                        tabs = list(dict.fromkeys((prev.get("tabs") or [prev.get("tab")]) + [tab]))
                        row["tabs"] = tabs
                        row["tab"] = prev.get("tab") or tab
                    else:
                        row["tabs"] = [tab]
                    found[(slug, sid)] = row
    rows = list(found.values())
    write_raw_json(SOURCE, "test_sets.json", rows)
    return rows


def test_set_record(row: dict[str, Any], *, subject_name: str) -> ResourceRecord:
    slug = str(row.get("subject") or "")
    sid = str(row.get("id") or "")
    name = (row.get("name") or sid).strip()
    url = f"{BASE}/app/test/{slug}/{sid}/run?env=learning"
    return ResourceRecord(
        source=SOURCE,
        title=name,
        course=subject_name,
        term=str(row.get("exam") or "OPPE"),
        program=slug.upper(),
        type=ResourceType.PREVIOUS_PAPER,
        authors=["IITM BS Community"],
        original_url=url,
        extra={
            "subject": slug,
            "subject_name": subject_name,
            "test_set_id": sid,
            "exam": row.get("exam"),
            "question_count": row.get("questionCount"),
            "section_count": row.get("sectionCount"),
            "duration_seconds": row.get("durationSeconds"),
            "total_marks": row.get("totalMarks"),
            "available": row.get("available"),
            "tabs": row.get("tabs") or [row.get("tab")],
            "category": "test_set",
        },
    )


def test_question_record(
    question: dict[str, Any],
    *,
    slug: str,
    subject_name: str,
    set_id: str,
    set_name: str,
    section: str | None,
    exam: str | None,
) -> ResourceRecord:
    qid = str(question.get("id") or "")
    title = (question.get("title") or "Question").strip()
    page_url = f"{BASE}/app/test/{slug}/{set_id}/run?env=learning#question-{qid}"
    pdf_url = f"{BASE}/api/questions/{qid}/pdf" if qid else None
    return ResourceRecord(
        source=SOURCE,
        title=f"{set_name}: {title}",
        course=subject_name,
        term=str(exam) if exam else set_name,
        program=slug.upper(),
        type=classify(exam, question.get("kind")),
        authors=["IITM BS Community"],
        original_url=page_url,
        file_url=pdf_url,
        extra={
            "subject": slug,
            "subject_name": subject_name,
            "question_id": qid,
            "kind": question.get("kind"),
            "marks": question.get("marks"),
            "exam": exam,
            "test_set_id": set_id,
            "test_set_name": set_name,
            "section": section,
            "pdf_url": pdf_url,
            "category": "test_question",
        },
    )


def flatten_test_questions(
    run: dict[str, Any],
    *,
    slug: str,
    subject_name: str,
    set_meta: dict[str, Any],
    run_url: str,
) -> list[dict[str, Any]]:
    set_id = str(set_meta.get("id") or "")
    set_name = str(run.get("setName") or set_meta.get("name") or set_id)
    exam = set_meta.get("exam") or set_name
    out: list[dict[str, Any]] = []
    for section in run.get("sections") or []:
        if not isinstance(section, dict):
            continue
        section_name = section.get("name")
        for question in section.get("questions") or []:
            if not isinstance(question, dict) or not question.get("id"):
                continue
            qid = str(question["id"])
            dest = (
                source_files_dir(SOURCE)
                / JSON_DIRNAME
                / "tests"
                / slug
                / safe_name(set_id)
                / safe_name(f"{qid[:8]}_{question.get('title') or 'question'}.json")
            )
            payload = {
                "source": SOURCE,
                "url": f"{run_url}#question-{qid}",
                "subject": slug,
                "subject_name": subject_name,
                "exam": exam,
                "test_set_id": set_id,
                "test_set_name": set_name,
                "section": section_name,
                "category": "test_question",
                **question,
            }
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            payload["json_path"] = str(dest)
            out.append(payload)
    return out


async def harvest_tests(
    client: httpx.AsyncClient,
    slugs: list[str],
    names: dict[str, str],
    cookies: dict[str, str],
    records: list[ResourceRecord],
) -> list[dict[str, Any]]:
    sets = await discover_test_sets(client, slugs, cookies=cookies)
    console.print(f"[bold]Mock tests / PYQs[/bold] ({len(sets)} sets)")
    combined: list[dict] = []
    saved = 0
    failed = 0
    for ts in sets:
        slug = str(ts.get("subject") or "")
        sid = str(ts.get("id") or "")
        if not slug or not sid:
            continue
        if ts.get("available") is False:
            console.print(f"  [dim]skip unavailable {slug}/{sid}[/dim]")
            continue
        subject_name = names.get(slug, slug)
        rec = test_set_record(ts, subject_name=subject_name)
        run_url = rec.original_url
        dest = source_files_dir(SOURCE) / JSON_DIRNAME / "tests" / slug / f"{safe_name(sid)}.json"
        resp = await fetch_rsc(client, run_url, cookies=cookies)
        run = extract_test_run(resp.text) if resp.status_code == 200 else None
        if resp.status_code != 200 or not run:
            resp = await fetch_rsc(client, run_url, cookies=cookies)
            run = extract_test_run(resp.text) if resp.status_code == 200 else None
        if resp.status_code != 200:
            failed += 1
            console.print(f"  [yellow]test {resp.status_code}[/yellow] {slug}/{sid}")
            records.append(rec)
            continue
        if not run:
            failed += 1
            fail_path = Path("data/raw/oppepractice") / f"rsc_test_fail_{safe_name(slug)}_{safe_name(sid)}.txt"
            fail_path.parent.mkdir(parents=True, exist_ok=True)
            fail_path.write_text(resp.text, encoding="utf-8")
            console.print(f"  [yellow]no test payload[/yellow] {slug}/{sid}")
            records.append(rec)
            continue
        set_payload = {
            "source": SOURCE,
            "url": run_url,
            "subject": slug,
            "subject_name": subject_name,
            "test_set_id": sid,
            "category": "test_set",
            **ts,
            "run": run,
        }
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(set_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        rec.extra["json_path"] = str(dest)
        rec.extra["has_body"] = True
        records.append(rec)
        questions = flatten_test_questions(
            run,
            slug=slug,
            subject_name=subject_name,
            set_meta=ts,
            run_url=run_url,
        )
        combined.extend(questions)
        for q in questions:
            qrec = test_question_record(
                q,
                slug=slug,
                subject_name=subject_name,
                set_id=sid,
                set_name=str(run.get("setName") or ts.get("name") or sid),
                section=q.get("section"),
                exam=ts.get("exam"),
            )
            qrec.extra["json_path"] = q.get("json_path")
            qrec.extra["has_body"] = not payload_needs_refetch(q)
            qrec.extra["has_solution"] = bool(q.get("solution_md") or q.get("reference_sql"))
            records.append(qrec)
        saved += 1
        nq = sum(
            len(s.get("questions") or [])
            for s in (run.get("sections") or [])
            if isinstance(s, dict)
        )
        console.print(f"  [green]{saved}[/green] {slug}/{sid} ({nq} questions)")
    console.print(f"[green]Tests saved {saved}[/green]  failed {failed}  questions {len(combined)}")
    return combined


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
    old_ours = {
        r.get("original_url"): r
        for r in old_rows
        if r.get("source") == SOURCE
    }
    merged: list[dict] = list(preserved)
    for rec in new_records:
        row = rec.to_index_row()
        prev = old_ours.get(rec.original_url)
        if prev:
            if prev.get("local_path") and not rec.local_path:
                row["local_path"] = prev["local_path"]
                row["mime_or_ext"] = prev.get("mime_or_ext")
            prev_extra = prev.get("extra") or {}
            extra = row.get("extra") or {}
            if prev_extra.get("json_path") and not extra.get("json_path"):
                extra["json_path"] = prev_extra["json_path"]
                extra["has_body"] = prev_extra.get("has_body", extra.get("has_body"))
                extra["has_solution"] = prev_extra.get("has_solution", extra.get("has_solution"))
                row["extra"] = extra
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

        practice_jsons = await harvest_question_jsons(client, records, cookies)

        live_slugs = [s for s in slugs if names.get(s)]
        test_jsons = await harvest_tests(client, live_slugs, names, cookies, records)
        all_jsons = practice_jsons + test_jsons
        write_full_questions(all_jsons)
        console.print(f"[green]Wrote {len(all_jsons)} question JSON rows[/green]")

        if download:
            await try_download_pdfs(client, records, cookies, download_limit)

    write_raw_json(SOURCE, "records.json", [r.to_index_row() for r in records])
    total = merge_into_index(records)
    console.print(f"[green]{SOURCE}: indexed {len(records)} resources[/green] (index now {total})")
    return records
