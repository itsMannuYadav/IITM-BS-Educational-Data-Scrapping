"""Inspect cookie shapes (no secrets) and probe extra Supabase tables."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote

import httpx

AUTH = Path(".auth/oppepractice.auth.json")
SUPA = "https://hzlqdbmyvltvoqiaojjg.supabase.co"
ANON = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imh6bHFkYm15dmx0dm9xaWFvampnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODM1OTIzNzcsImV4cCI6MjA5OTE2ODM3N30."
    "a1wYSNRK7bJjCcc9mweQNLjG7Py7mDnWOvi3YxvVY2Y"
)
OUT = Path("data/raw/oppepractice")
BASE = "https://oppepractice.iitmbsdegree.in"


def cookie_map():
    state = json.loads(AUTH.read_text(encoding="utf-8"))
    return {c["name"]: c["value"] for c in state.get("cookies", [])}


def describe(val: str) -> str:
    v = val or ""
    return (
        f"len={len(v)} start={v[:24]!r} end={v[-12:]!r} "
        f"pct={v.startswith('%')} json={v[:1] in '{['} "
        f"b64={v.startswith('base64-')} eyj={v.startswith('eyJ')}"
    )


def reconstruct_sb(ck: dict[str, str]) -> str | None:
    chunks = []
    base = "sb-hzlqdbmyvltvoqiaojjg-auth-token"
    if base in ck:
        chunks = [ck[base]]
    else:
        i = 0
        while f"{base}.{i}" in ck:
            chunks.append(ck[f"{base}.{i}"])
            i += 1
    if not chunks:
        return None
    raw = "".join(chunks)
    raw = unquote(raw)
    if raw.startswith("base64-"):
        import base64

        pad = "=" * ((4 - (len(raw[7:]) % 4)) % 4)
        raw = base64.b64decode(raw[7:] + pad).decode("utf-8")
    return raw


def main() -> None:
    ck = cookie_map()
    for name, val in ck.items():
        if "sb-" in name or "oppe" in name.lower() or "auth" in name.lower() or name.startswith("__Host"):
            print(name, describe(val))
    raw = reconstruct_sb(ck)
    print("reconstructed", describe(raw) if raw else None)
    access = None
    if raw:
        try:
            data = json.loads(raw)
            print("json keys", list(data)[:20])
            access = data.get("access_token") or data.get("accessToken")
            if not access and isinstance(data.get("session"), dict):
                access = data["session"].get("access_token")
            print("access", bool(access), "len", len(access or ""))
        except Exception as exc:
            print("json parse fail", type(exc).__name__, raw[:40] if raw else None)

    headers = {
        "apikey": ANON,
        "Authorization": f"Bearer {access or ANON}",
        "Accept": "application/json",
    }
    tables = [
        "subjects",
        "topics",
        "questions",
        "test_sets",
        "profiles",
        "banners",
        "articles",
        "resources",
        "syllabus",
        "test_questions",
        "question_tests",
        "test_cases",
        "hidden_tests",
        "solutions",
        "question_pdfs",
        "leaderboard",
        "leaderboards",
        "attempts",
        "submissions",
        "weeks",
        "exams",
        "branches",
        "levels",
        "pyq_sets",
        "mock_tests",
        "series",
        "question_topics",
    ]
    probe = {}
    with httpx.Client(timeout=40, headers=headers) as c:
        for table in tables:
            r = c.get(f"{SUPA}/rest/v1/{table}?select=*&limit=2")
            preview = r.text[:250].replace("\n", " ")
            print(table, r.status_code, preview)
            probe[table] = {"status": r.status_code, "preview": r.text[:400]}
            if r.status_code in (200, 206) and r.headers.get("content-range"):
                probe[table]["range"] = r.headers.get("content-range")

        # count subjects/topics
        for table in ("subjects", "topics", "test_sets"):
            r = c.get(
                f"{SUPA}/rest/v1/{table}?select=id",
                headers={"Prefer": "count=exact", "Range": "0-0"},
            )
            print("count", table, r.status_code, r.headers.get("content-range"), r.text[:80])

    # PDF with better cookies + bearer if we have it
    qid = "79dfbf8e-be5c-4dbb-a9c2-398c86414bed"
    with httpx.Client(timeout=40, cookies=ck, follow_redirects=True, headers={"User-Agent": "IITM-BS-Educational-Collector/1.0"}) as c:
        r = c.get(f"{BASE}/api/questions/{qid}/pdf")
        print("pdf cookies-only", r.status_code, r.headers.get("content-type"), r.text[:120] if r.status_code != 200 else r.headers.get("content-disposition"))
        if access:
            r2 = c.get(
                f"{BASE}/api/questions/{qid}/pdf",
                headers={"Authorization": f"Bearer {access}"},
            )
            print("pdf bearer", r2.status_code, r2.headers.get("content-type"), r2.text[:120] if r2.status_code != 200 else r2.headers.get("content-disposition"))

    # paginate python to count
    with httpx.Client(timeout=40, cookies=ck, headers={"Accept": "application/json"}) as c:
        for slug in ("python", "dbms"):
            offset = 0
            total = 0
            last_has = True
            while last_has and offset < 5000:
                r = c.get(f"{BASE}/api/subjects/{slug}/questions?offset={offset}")
                data = r.json()
                rows = data.get("rows") or []
                total += len(rows)
                last_has = bool(data.get("hasMore"))
                offset = data.get("nextOffset") if data.get("nextOffset") is not None else offset + len(rows)
                if not rows:
                    break
            print("count questions", slug, total, "hasMore", last_has)

    (OUT / "supabase_tables_probe.json").write_text(json.dumps(probe, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
