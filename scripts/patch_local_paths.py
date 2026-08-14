"""Patch index.jsonl local_path by matching downloaded files' Drive ID prefixes / gdrive urls."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "data" / "index.jsonl"
FILES = ROOT / "data" / "files"
DRIVE_RE = re.compile(r"/file/d/([a-zA-Z0-9_-]+)")


def drive_id(url: str) -> str | None:
    m = DRIVE_RE.search(url or "")
    return m.group(1) if m else None


def main() -> None:
    # map sha1 prefix from filename OR search by reading? filenames are {sha1_8}_{name}
    # Better: map from gdrive id via recomputing sha1 of uc url? We used sha1(url) of uc?download url.
    import hashlib

    def digest_for_file_id(fid: str) -> str:
        url = f"https://drive.google.com/uc?export=download&id={fid}"
        return hashlib.sha1(url.encode()).hexdigest()[:8]

    # build digest -> path
    by_digest: dict[str, Path] = {}
    for p in FILES.rglob("*"):
        if not p.is_file():
            continue
        name = p.name
        if "_" in name:
            dig = name.split("_", 1)[0]
            if len(dig) == 8:
                by_digest[dig] = p

    rows = [json.loads(l) for l in INDEX.read_text(encoding="utf-8").splitlines() if l.strip()]
    patched = 0
    for r in rows:
        if r.get("local_path"):
            # keep if file exists
            if Path(r["local_path"]).exists():
                continue
        urls = list(r.get("gdrive_urls") or [])
        if r.get("file_url"):
            urls.append(r["file_url"])
        urls.append(r.get("original_url") or "")
        fid = None
        for u in urls:
            fid = drive_id(u or "")
            if fid:
                break
        if not fid:
            continue
        dig = digest_for_file_id(fid)
        path = by_digest.get(dig)
        if not path:
            continue
        r["local_path"] = str(path)
        r["mime_or_ext"] = path.suffix
        patched += 1

    with INDEX.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"patched={patched} local_total={sum(1 for r in rows if r.get('local_path'))}")


if __name__ == "__main__":
    main()
