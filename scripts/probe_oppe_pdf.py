"""Extract test/PDF/API snippets and probe remaining OPPE endpoints."""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

BASE = "https://oppepractice.iitmbsdegree.in"
SUPA = "https://hzlqdbmyvltvoqiaojjg.supabase.co"
ANON = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imh6bHFkYm15dmx0dm9xaWFvampnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODM1OTIzNzcsImV4cCI6MjA5OTE2ODM3N30."
    "a1wYSNRK7bJjCcc9mweQNLjG7Py7mDnWOvi3YxvVY2Y"
)
QID = "79dfbf8e-be5c-4dbb-a9c2-398c86414bed"
OUT = Path("data/raw/oppepractice")
AUTH = Path(".auth/oppepractice.auth.json")
JS_DIR = OUT / "js"


def cookies() -> dict[str, str]:
    state = json.loads(AUTH.read_text(encoding="utf-8"))
    return {c["name"]: c["value"] for c in state.get("cookies", [])}


def snippets() -> None:
    out = []
    for path in JS_DIR.glob("*.js"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for needle in (
            "/api/",
            "/app/test",
            "test-series",
            "testSeries",
            "pdf",
            "from(",
            ".from(",
            "rest/v1",
        ):
            idx = 0
            while True:
                i = text.find(needle, idx)
                if i < 0:
                    break
                out.append(f"{path.name} @ {i}: {text[max(0,i-80):i+160].replace(chr(10),' ')}")
                idx = i + len(needle)
                if len(out) > 400:
                    break
        if len(out) > 400:
            break
    (OUT / "js_context.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    for line in out[:80]:
        print(line[:220])
    print("snippet count", len(out))


def user_jwt(cookie_map: dict[str, str]) -> str | None:
    # supabase splits large cookies as .0 .1
    parts = []
    for i in range(5):
        key = f"sb-hzlqdbmyvltvoqiaojjg-auth-token.{i}"
        if key in cookie_map:
            parts.append(cookie_map[key])
    if not parts and "sb-hzlqdbmyvltvoqiaojjg-auth-token" in cookie_map:
        parts = [cookie_map["sb-hzlqdbmyvltvoqiaojjg-auth-token"]]
    if not parts:
        return None
    raw = "".join(parts)
    # cookie value is often URL-encoded JSON: {"access_token":"...","refresh_token":"..."}
    try:
        from urllib.parse import unquote

        raw = unquote(raw)
        data = json.loads(raw)
        return data.get("access_token") or data.get("accessToken")
    except Exception:
        return raw if raw.startswith("eyJ") else None


def main() -> None:
    snippets()
    ck = cookies()
    jwt = user_jwt(ck)
    print("user jwt", bool(jwt), "len", len(jwt or ""))
    headers = {
        "User-Agent": "IITM-BS-Educational-Collector/1.0 (approved-collection)",
        "Accept": "*/*",
        "apikey": ANON,
        "Authorization": f"Bearer {jwt or ANON}",
    }
    probe = {}
    with httpx.Client(timeout=40, headers={k: v for k, v in headers.items() if k != "apikey"}, cookies=ck, follow_redirects=True) as c:
        paths = [
            f"/api/questions/{QID}/pdf",
            f"/api/questions/{QID}",
            f"/api/questions/{QID}/solution",
            "/api/subjects/python/questions?offset=60",
            "/api/subjects/python/questions?offset=90",
            "/api/subjects/python/series",
            "/api/subjects/python/test-series",
            "/api/subjects/python/tests",
            "/api/test-series/python",
            "/api/tests?subject=python",
            "/app/test/python",
        ]
        for p in paths:
            r = c.get(f"{BASE}{p}")
            info = {
                "status": r.status_code,
                "ctype": r.headers.get("content-type"),
                "bytes": len(r.content),
                "cd": r.headers.get("content-disposition"),
            }
            if "json" in (r.headers.get("content-type") or ""):
                try:
                    data = r.json()
                    info["keys"] = list(data)[:20] if isinstance(data, dict) else f"list:{len(data)}"
                    if isinstance(data, dict):
                        info["hasMore"] = data.get("hasMore")
                        info["nextOffset"] = data.get("nextOffset")
                        info["rows"] = len(data.get("rows") or [])
                except Exception:
                    info["preview"] = r.text[:200]
            elif "pdf" in (r.headers.get("content-type") or "") or p.endswith("/pdf"):
                dest = OUT / "sample_question.pdf"
                dest.write_bytes(r.content)
                info["saved"] = str(dest)
                info["magic"] = r.content[:8].hex()
            else:
                info["preview"] = r.text[:180]
            probe[p] = info
            print(r.status_code, p, info.get("ctype"), info.get("bytes"), info.get("cd"), info.get("rows"))

    # supabase openapi / tables
    with httpx.Client(timeout=40, headers={"apikey": ANON, "Authorization": f"Bearer {jwt or ANON}"}) as c:
        r = c.get(f"{SUPA}/rest/v1/", headers={"Accept": "application/openapi+json"})
        print("openapi", r.status_code, r.headers.get("content-type"), len(r.content))
        probe["supabase_openapi"] = {
            "status": r.status_code,
            "ctype": r.headers.get("content-type"),
            "bytes": len(r.content),
            "preview": r.text[:500],
        }
        if r.status_code == 200:
            try:
                spec = r.json()
                paths = list((spec.get("paths") or {}).keys())
                probe["supabase_tables"] = paths
                print("tables", paths[:40], "count", len(paths))
                (OUT / "supabase_openapi.json").write_text(json.dumps(spec, indent=2)[:500000], encoding="utf-8")
            except Exception as exc:
                probe["supabase_openapi"]["err"] = str(exc)

        for table in [
            "questions",
            "subjects",
            "topics",
            "tests",
            "test_series",
            "problems",
            "pyqs",
            "question",
        ]:
            rr = c.get(f"{SUPA}/rest/v1/{table}?select=*&limit=2")
            print("table", table, rr.status_code, rr.text[:120].replace("\n", " "))
            probe[f"supa_{table}"] = {"status": rr.status_code, "preview": rr.text[:300]}

    (OUT / "probe_pdf_supabase.json").write_text(json.dumps(probe, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
