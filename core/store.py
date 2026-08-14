from __future__ import annotations

import json
from pathlib import Path

from core.config import source_raw_dir
from core.schema import ResourceRecord


def write_raw_json(source: str, name: str, payload: object) -> Path:
    path = source_raw_dir(source) / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def dedupe_key(record: ResourceRecord) -> str:
    return f"{record.source}|{record.original_url}|{record.file_url or ''}|{record.title}"


def merge_unique(existing: list[ResourceRecord], new_items: list[ResourceRecord]) -> list[ResourceRecord]:
    seen = {dedupe_key(r) for r in existing}
    out = list(existing)
    for item in new_items:
        key = dedupe_key(item)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out
