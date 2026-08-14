"""Merge all Unknown IITians raw tables into the index (prefer rows with file links)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.store import write_raw_json
from sources.unknowniitians import SOURCE, merge_into_index, record_from_row

RAW = Path("data/raw/unknowniitians")
TABLES = [
    ("iitm_branch_notes.json", "iitm_branch_note"),
    ("iitm_branch_notes_by_subject.json", "iitm_branch_note"),
    ("pyqs.json", "pyq"),
    ("pyqs_authed.json", "pyq"),
    ("notes.json", "note"),
    ("notes_authed.json", "note"),
]


def main() -> None:
    recs = []
    for fname, kind in TABLES:
        path = RAW / fname
        if not path.exists():
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        for row in rows:
            rec = record_from_row(row, kind=kind)
            if rec:
                recs.append(rec)

    best = {}
    for r in recs:
        # Prefer concrete download URLs; for placeholders use id-based key
        key = r.original_url
        if key.startswith("https://www.unknowniitians.com/exam-preparation/iitm-bs#"):
            key = f"id:{r.extra.get('id') or r.title}"
        prev = best.get(key)
        if not prev:
            best[key] = r
            continue
        if (r.gdrive_urls or r.file_url) and not (prev.gdrive_urls or prev.file_url):
            best[key] = r

    merged = list(best.values())
    with_files = sum(
        1 for r in merged if r.gdrive_urls or (r.file_url and str(r.file_url).startswith("http"))
    )
    write_raw_json(SOURCE, "records.json", [r.to_index_row() for r in merged])
    total = merge_into_index(merged)
    print(f"merged={len(merged)} downloadable={with_files} index_total={total}")


if __name__ == "__main__":
    main()
