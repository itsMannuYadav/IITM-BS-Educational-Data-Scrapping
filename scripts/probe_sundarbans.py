"""Discover and extract Sundarbans Study Corner catalog from Vite JS chunk."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx

BASE = "https://sundarbans.iitmbs.org"
OUT = Path("data/raw/sundarbans")
OUT.mkdir(parents=True, exist_ok=True)


async def main() -> None:
    headers = {"User-Agent": "IITM-BS-Educational-Collector/1.0 (approved-collection)"}
    async with httpx.AsyncClient(timeout=60, headers=headers, follow_redirects=True) as c:
        home = await c.get(BASE + "/")
        html = home.text
        (OUT / "home.html").write_text(html, encoding="utf-8")
        assets = sorted(set(re.findall(r"/assets/[A-Za-z0-9_.-]+\.js", html)))
        print("assets from html", len(assets))
        study_urls = [a for a in assets if "StudyView" in a or "study" in a.lower()]
        # Also pull index chunk for import map
        index_assets = [a for a in assets if a.startswith("/assets/index-")]
        all_js_urls = list(dict.fromkeys(study_urls + index_assets + assets))
        found_study = None
        for path in all_js_urls:
            url = BASE + path
            r = await c.get(url)
            text = r.text
            if "StudyView" in path or ("foundation" in text and "drive.google.com" in text and "pyq" in text.lower()):
                print("CANDIDATE", path, len(text), r.status_code)
                if "drive.google.com" in text and ("notes" in text or "pyq" in text.lower()):
                    found_study = (path, text)
                    break
            # follow dynamic imports mentioning StudyView
            for m in re.findall(r"assets/StudyView-[A-Za-z0-9]+\.js", text):
                su = BASE + "/" + m if not m.startswith("/") else BASE + m
                if not m.startswith("assets"):
                    su = BASE + "/assets/" + m.split("/")[-1]
                # normalize
                su = BASE + "/assets/" + Path(m).name
                print("found ref", su)
                rr = await c.get(su)
                if rr.status_code == 200 and "drive.google.com" in rr.text:
                    found_study = ("/assets/" + Path(m).name, rr.text)
                    break
            if found_study:
                break

        if not found_study:
            # brute: list by fetching homepage vite map deps from index
            for path in assets:
                url = BASE + path
                r = await c.get(url)
                refs = re.findall(r"StudyView-[A-Za-z0-9]+\.js", r.text)
                for ref in refs:
                    su = f"{BASE}/assets/{ref}"
                    print("trying", su)
                    rr = await c.get(su)
                    print(su, rr.status_code, len(rr.text))
                    if rr.status_code == 200 and "drive.google.com" in rr.text:
                        found_study = (f"/assets/{ref}", rr.text)
                        break
                if found_study:
                    break

        if not found_study:
            print("FAILED to find StudyView chunk")
            return

        path, text = found_study
        (OUT / "StudyView.js").write_text(text, encoding="utf-8")
        (OUT / "StudyView.path.txt").write_text(path, encoding="utf-8")
        print("saved", path, "bytes", len(text))
        print("drive links", len(re.findall(r"drive\.google\.com", text)))
        print("youtube", len(re.findall(r"youtube\.com|youtu\.be", text)))
        print("notion", len(re.findall(r"notion\.(so|site)", text)))


if __name__ == "__main__":
    asyncio.run(main())
