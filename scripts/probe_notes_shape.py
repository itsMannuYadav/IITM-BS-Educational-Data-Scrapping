"""Probe AceGrade notes/lectures endpoints and dump sample payloads."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

API = "https://project-b-backend-3uoftpsktq-el.a.run.app/backendapi"
OUT = Path("data/raw/acegrade")


async def main() -> None:
    urls = [
        f"{API}/notes/get_notes/cs1001/ds",
        f"{API}/notes/get_notes/cs1002/ds",
        f"{API}/notes/get_notes/ma1001/ds",
        f"{API}/notes/get_notes/cs2001/ds",
        f"{API}/course",
        f"{API}/course/cs1001",
        f"{API}/course/cs1001/ds",
        f"{API}/lectures",
        f"{API}/lectures/cs1001",
        f"{API}/lectures/cs1001/ds",
        f"{API}/notes/downloaded",
        f"{API}/notes/downloaded/cs1001/ds",
        f"{API}/pyq/get_available_papers",
        f"{API}/pyq/exam_sim",
    ]
    results = []
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "Mozilla/5.0"}) as c:
        for url in urls:
            try:
                r = await c.get(url)
                preview = r.text[:1500]
                print(url, r.status_code, preview[:180].replace("\n", " "))
                results.append({"url": url, "status": r.status_code, "body": r.json() if "json" in r.headers.get("content-type","") else preview})
            except Exception as e:
                print("ERR", url, e)
                results.append({"url": url, "error": str(e)})
    (OUT / "notes_lectures_probe.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
