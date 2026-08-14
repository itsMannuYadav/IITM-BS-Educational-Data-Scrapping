"""Fetch public OPPE catalog tables with the anon key, plus test-series HTML."""
from __future__ import annotations

import json
from pathlib import Path

import httpx

SUPA = "https://hzlqdbmyvltvoqiaojjg.supabase.co"
ANON = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imh6bHFkYm15dmx0dm9xaWFvampnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODM1OTIzNzcsImV4cCI6MjA5OTE2ODM3N30."
    "a1wYSNRK7bJjCcc9mweQNLjG7Py7mDnWOvi3YxvVY2Y"
)
BASE = "https://oppepractice.iitmbsdegree.in"
OUT = Path("data/raw/oppepractice")


def fetch_all(client: httpx.Client, table: str) -> list:
    rows = []
    start = 0
    page = 1000
    while True:
        r = client.get(
            f"{SUPA}/rest/v1/{table}?select=*",
            headers={"Range": f"{start}-{start + page - 1}", "Prefer": "count=exact"},
        )
        print(table, r.status_code, r.headers.get("content-range"), len(r.content))
        if r.status_code not in (200, 206):
            print(" ", r.text[:200])
            break
        chunk = r.json()
        if not isinstance(chunk, list):
            break
        rows.extend(chunk)
        if len(chunk) < page:
            break
        start += page
    return rows


def main() -> None:
    headers = {
        "apikey": ANON,
        "Authorization": f"Bearer {ANON}",
        "Accept": "application/json",
    }
    with httpx.Client(timeout=40, headers=headers) as c:
        for table in ("subjects", "topics", "test_sets", "banners", "articles", "resources", "syllabus"):
            rows = fetch_all(c, table)
            (OUT / f"supa_{table}.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
            print(" saved", table, len(rows))
            if rows and isinstance(rows[0], dict):
                print("  keys", list(rows[0].keys()))
                print("  sample", json.dumps(rows[0], default=str)[:300])

    with httpx.Client(timeout=40, headers={"User-Agent": "IITM-BS-Educational-Collector/1.0"}, follow_redirects=True) as c:
        for path in (
            "/app/subjects/python?tab=test-series",
            "/app/subjects/python?tab=pyqs",
            "/app/subjects/python?tab=syllabus",
            "/app/subjects/dbms?tab=test-series",
        ):
            r = c.get(f"{BASE}{path}")
            name = path.replace("/", "_").replace("?", "_")
            html_path = OUT / f"page{name}.html"
            html_path.write_text(r.text, encoding="utf-8")
            print(path, r.status_code, len(r.text), "next_f", "self.__next_f" in r.text)


if __name__ == "__main__":
    main()
