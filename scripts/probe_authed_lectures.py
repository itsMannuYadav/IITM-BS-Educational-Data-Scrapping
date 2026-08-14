"""Probe AceGrade lectures API using saved auth_token from storage state."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
AUTH = ROOT / ".auth" / "acegrade.auth.json"
API = "https://project-b-backend-3uoftpsktq-el.a.run.app/backendapi"
OUT = ROOT / "data" / "raw" / "acegrade"


def load_token() -> str:
    state = json.loads(AUTH.read_text(encoding="utf-8"))
    for origin in state.get("origins", []):
        for item in origin.get("localStorage", []):
            if item.get("name") == "auth_token" and item.get("value"):
                return item["value"]
    raise SystemExit("auth_token not found — run: python main.py login acegrade")


async def main() -> None:
    token = load_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    }
    urls = [
        f"{API}/course/cs1001",
        f"{API}/course/cs1002",
        f"{API}/course/ma1001",
        f"{API}/course/cs2001",
        f"{API}/lectures/cs1001",
        f"{API}/lectures",
        f"{API}/notes/downloaded/cs1001/ds",
        f"{API}/user_dashboard/get_dashboard_data",
        f"{API}/pyq/get_available_papers",
    ]
    results = []
    async with httpx.AsyncClient(timeout=30, headers=headers, follow_redirects=True) as c:
        for url in urls:
            r = await c.get(url)
            preview = r.text[:800]
            print(url, r.status_code, preview[:160].replace("\n", " "))
            try:
                body = r.json()
            except Exception:
                body = preview
            results.append({"url": url, "status": r.status_code, "body": body})
    (OUT / "authed_probe.json").write_text(json.dumps(results, indent=2, default=str)[:500000], encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
