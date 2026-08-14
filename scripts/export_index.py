"""Export data/index.jsonl to a readable CSV + summary JSON."""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
INDEX = ROOT / "data" / "index.jsonl"
OUT_CSV = ROOT / "data" / "index.csv"
OUT_SUMMARY = ROOT / "data" / "summary.json"


def main() -> None:
    rows = []
    if INDEX.exists():
        for line in INDEX.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))

    fields = [
        "id",
        "source",
        "type",
        "title",
        "course",
        "term",
        "program",
        "authors",
        "original_url",
        "gdrive_urls",
        "local_path",
    ]
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    **r,
                    "authors": "; ".join(r.get("authors") or []),
                    "gdrive_urls": "; ".join(r.get("gdrive_urls") or []),
                }
            )

    summary = {
        "total": len(rows),
        "by_type": dict(Counter(r.get("type") for r in rows)),
        "by_source": dict(Counter(r.get("source") for r in rows)),
        "with_local_file": sum(1 for r in rows if r.get("local_path")),
        "with_gdrive": sum(1 for r in rows if r.get("gdrive_urls")),
        "csv": str(OUT_CSV),
    }
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
