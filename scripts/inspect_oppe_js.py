"""Download remaining OPPE JS chunks and extract API/Supabase hints."""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

BASE = "https://oppepractice.iitmbsdegree.in"
OUT = Path("data/raw/oppepractice")
JS_DIR = OUT / "js"
JS_DIR.mkdir(parents=True, exist_ok=True)

URLS = json.loads((OUT / "browser_js_urls.json").read_text(encoding="utf-8"))
URLS = [u for u in URLS if u.startswith("https://oppepractice.iitmbsdegree.in/_next/")]

PATTERNS = [
    r'["\'](https://[a-z0-9]+\.supabase\.co[^"\']*)["\']',
    r'eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}',
    r'/api/[A-Za-z0-9_/${}.?-]+',
    r'/app/[A-Za-z0-9_/${}.?-]+',
    r'sb-[a-z0-9]+-auth-token',
    r'NEXT_PUBLIC_[A-Z0-9_]+',
]


def main() -> None:
    hits: dict[str, list[str]] = {}
    snippets: list[str] = []
    with httpx.Client(timeout=40, follow_redirects=True, headers={"User-Agent": "IITM-BS-Educational-Collector/1.0"}) as c:
        for url in URLS:
            name = url.rsplit("/", 1)[-1]
            dest = JS_DIR / name
            if dest.exists() and dest.stat().st_size > 100:
                text = dest.read_text(encoding="utf-8", errors="ignore")
            else:
                r = c.get(url)
                print(r.status_code, name, len(r.text))
                dest.write_text(r.text, encoding="utf-8")
                text = r.text
            file_hits = []
            for pat in PATTERNS:
                found = re.findall(pat, text)
                if found:
                    file_hits.extend(found[:40])
            if "questions" in text.lower() and "/api/" in text:
                for m in re.findall(r".{0,40}/api/subjects.{0,80}", text):
                    snippets.append(f"{name}: {m.replace(chr(10),' ')[:200]}")
                for m in re.findall(r".{0,40}questions.{0,80}", text):
                    if "api" in m.lower() or "fetch" in m.lower() or "offset" in m.lower():
                        snippets.append(f"{name}: {m.replace(chr(10),' ')[:200]}")
            if file_hits:
                hits[name] = sorted(set(file_hits))[:80]
                print("HITS", name, hits[name][:15])

    (OUT / "js_deep_hits.json").write_text(
        json.dumps({"hits": hits, "snippets": snippets[:250]}, indent=2),
        encoding="utf-8",
    )
    print("files with hits", len(hits), "snippets", len(snippets))


if __name__ == "__main__":
    main()
