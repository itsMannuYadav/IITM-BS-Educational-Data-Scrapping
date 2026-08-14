"""Probe OPPE Practice API + Next.js chunks for catalog endpoints."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx

BASE = "https://oppepractice.iitmbsdegree.in"
OUT = Path("data/raw/oppepractice")
AUTH = Path(".auth/oppepractice.auth.json")
HEADERS = {
    "User-Agent": "IITM-BS-Educational-Collector/1.0 (approved-collection)",
    "Accept": "application/json, text/html",
}

JS_URLS = [
    "/_next/static/immutable/chunks/0as93-hrkv5ee.js",
    "/_next/static/immutable/chunks/0c0hxoamwjsbw.js",
    "/_next/static/immutable/chunks/0nbyd1-7wxn0z.js",
    "/_next/static/immutable/chunks/19vys3yqkb48d.js",
    "/_next/static/immutable/chunks/1fvefoxvtmsn-.js",
    "/_next/static/immutable/chunks/1h5ubfahlgdqz.js",
    "/_next/static/immutable/chunks/1k-gy7bk9kpzy.js",
    "/_next/static/immutable/chunks/1xg8x4zb12vbn.js",
    "/_next/static/immutable/chunks/24qtklo-dzgeo.js",
    "/_next/static/immutable/chunks/2aoixyysd_i_y.js",
    "/_next/static/immutable/chunks/2gx_vd823in8y.js",
    "/_next/static/immutable/chunks/3-e1mggnrnh9q.js",
    "/_next/static/immutable/chunks/3lt7t0nu_ly9n.js",
]


def load_cookies() -> dict[str, str]:
    if not AUTH.exists():
        return {}
    state = json.loads(AUTH.read_text(encoding="utf-8"))
    return {c["name"]: c["value"] for c in state.get("cookies", [])}


def preview(data, n: int = 400) -> str:
    if isinstance(data, (dict, list)):
        text = json.dumps(data, ensure_ascii=False)
    else:
        text = str(data)
    return text[:n].replace("\n", " ")


async def dump_js(client: httpx.AsyncClient) -> list[str]:
    api_hits: set[str] = set()
    snippets: list[str] = []
    js_dir = OUT / "js"
    js_dir.mkdir(parents=True, exist_ok=True)
    for path in JS_URLS:
        url = f"{BASE}{path}"
        resp = await client.get(url)
        print(f"js {resp.status_code} {path} {len(resp.text)}")
        if resp.status_code != 200:
            continue
        name = Path(path).name
        (js_dir / name).write_text(resp.text, encoding="utf-8")
        text = resp.text
        for m in re.findall(r'["\'](/api/[^"\']+)["\']', text):
            api_hits.add(m)
        for m in re.findall(r"`(/api/[^`]+)`", text):
            api_hits.add(m)
        for m in re.findall(r'["\'](/app/[^"\']+)["\']', text):
            api_hits.add(m)
        # template literals like /api/subjects/${slug}/questions
        for m in re.findall(r"/api/[A-Za-z0-9_/${}.?-]+", text):
            if len(m) < 180:
                api_hits.add(m)
        for pat in (
            r".{0,50}/api/subjects.{0,80}",
            r".{0,50}/api/questions.{0,80}",
            r".{0,50}/api/tests.{0,80}",
            r".{0,40}offset.{0,60}",
            r".{0,40}nextOffset.{0,60}",
            r".{0,40}statement.{0,80}",
            r".{0,40}starterCode.{0,80}",
        ):
            for m in re.findall(pat, text):
                snippets.append(m.replace("\n", " ")[:220])
    (OUT / "js_api_paths.json").write_text(
        json.dumps({"paths": sorted(api_hits), "snippets": snippets[:200]}, indent=2),
        encoding="utf-8",
    )
    print("api path candidates", len(api_hits))
    for p in sorted(api_hits)[:80]:
        print("  PATH", p)
    return sorted(api_hits)


async def probe_endpoint(client: httpx.AsyncClient, path: str) -> dict:
    url = path if path.startswith("http") else f"{BASE}{path}"
    try:
        resp = await client.get(url)
    except Exception as exc:
        return {"path": path, "error": str(exc)}
    item: dict = {
        "path": path,
        "status": resp.status_code,
        "ctype": resp.headers.get("content-type", ""),
        "bytes": len(resp.content),
    }
    try:
        data = resp.json()
        item["json_preview"] = preview(data)
        if isinstance(data, dict):
            item["keys"] = list(data.keys())[:30]
            rows = data.get("rows") or data.get("data") or data.get("items")
            if isinstance(rows, list):
                item["row_count"] = len(rows)
                item["hasMore"] = data.get("hasMore")
                item["nextOffset"] = data.get("nextOffset")
                if rows and isinstance(rows[0], dict):
                    item["row_keys"] = list(rows[0].keys())
        elif isinstance(data, list) and data:
            item["row_count"] = len(data)
            if isinstance(data[0], dict):
                item["row_keys"] = list(data[0].keys())
        item["data"] = data
    except Exception:
        item["text_preview"] = resp.text[:300]
    print(
        f"  {resp.status_code} {path} {item.get('row_count', '')} "
        f"{item.get('keys') or item.get('ctype')}"
    )
    return item


async def main() -> None:
    cookies = load_cookies()
    print("cookies", len(cookies), sorted(cookies)[:12])
    async with httpx.AsyncClient(
        timeout=40,
        headers=HEADERS,
        cookies=cookies,
        follow_redirects=True,
    ) as client:
        paths = await dump_js(client)

        endpoints = [
            "/api/subjects",
            "/api/courses",
            "/api/questions",
            "/api/tests",
            "/api/leaderboard",
            "/api/me",
            "/api/user",
            "/api/user/profile",
            "/api/auth/session",
            "/api/progress",
            "/api/subjects/python",
            "/api/subjects/dbms",
            "/api/subjects/pdsa",
            "/api/subjects/java",
            "/api/subjects/c",
            "/api/subjects/syscmd",
            "/api/subjects/python/questions",
            "/api/subjects/python/questions?offset=0",
            "/api/subjects/python/questions?offset=30",
            "/api/subjects/python/questions?limit=200",
            "/api/subjects/python/questions?offset=0&limit=200",
            "/api/subjects/dbms/questions",
            "/api/subjects/dbms/questions?offset=30",
            "/api/subjects/pdsa/questions",
            "/api/subjects/java/questions",
            "/api/subjects/c/questions",
            "/api/subjects/syscmd/questions",
            "/api/subjects/python/tests",
            "/api/subjects/python/test-series",
            "/api/subjects/python/pyqs",
            "/api/subjects/python/topics",
            "/api/tests/python",
            "/api/leaderboard/python",
            "/app/subjects/python",
        ]
        # add unique /api/ paths found in JS that look concrete
        for p in paths:
            if "${" in p or "`" in p:
                continue
            if p.startswith("/api/") and p not in endpoints:
                endpoints.append(p)

        results = {}
        for ep in endpoints:
            results[ep] = await probe_endpoint(client, ep)
            await asyncio.sleep(0.25)

        # If python questions exist, fetch one question detail by id
        py = results.get("/api/subjects/python/questions", {}).get("data") or {}
        rows = py.get("rows") if isinstance(py, dict) else None
        if isinstance(rows, list) and rows:
            qid = rows[0].get("id")
            detail_paths = [
                f"/api/questions/{qid}",
                f"/api/subjects/python/questions/{qid}",
                f"/api/problem/{qid}",
                f"/api/problems/{qid}",
                f"/app/questions/{qid}",
                f"/app/subjects/python/{qid}",
                f"/app/practice/{qid}",
            ]
            for ep in detail_paths:
                results[ep] = await probe_endpoint(client, ep)
                await asyncio.sleep(0.25)

        slim = {}
        for k, v in results.items():
            slim[k] = {kk: vv for kk, vv in v.items() if kk != "data"}
            data = v.get("data")
            if isinstance(data, dict) and "rows" in data:
                slim[k]["sample_row"] = (data["rows"] or [None])[0]
                slim[k]["row_count"] = len(data.get("rows") or [])
                slim[k]["hasMore"] = data.get("hasMore")
                slim[k]["nextOffset"] = data.get("nextOffset")
            elif data is not None and k.count("/") >= 3:
                slim[k]["data"] = data
        (OUT / "api_probe.json").write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")
        (OUT / "api_probe_full.json").write_text(
            json.dumps(results, indent=2, default=str), encoding="utf-8"
        )
        print("wrote api_probe.json")


if __name__ == "__main__":
    asyncio.run(main())
