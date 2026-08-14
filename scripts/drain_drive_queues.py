"""Keep downloading Drive files until queues are empty (or max rounds).

Usage:
  python scripts/drain_drive_queues.py
  python scripts/drain_drive_queues.py --sources acegrade,sundarbans
  python scripts/drain_drive_queues.py --sources sundarbans
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.download_missing import run  # type: ignore
from sources.acegrade import extract_drive_id

INDEX = ROOT / "data" / "index.jsonl"


def pending_count(source: str) -> int:
    rows = [json.loads(l) for l in INDEX.read_text(encoding="utf-8").splitlines() if l.strip()]
    n = 0
    for r in rows:
        if r.get("source") != source:
            continue
        if r.get("local_path"):
            continue
        urls = r.get("gdrive_urls") or []
        if urls and extract_drive_id(urls[0]):
            n += 1
    return n


async def main(sources: list[str]) -> None:
    batch = 100
    max_rounds = 40
    for source in sources:
        for rnd in range(1, max_rounds + 1):
            left = pending_count(source)
            print(f"\n=== {source} round {rnd}: pending={left} ===", flush=True)
            if left <= 0:
                print(f"{source} queue drained", flush=True)
                break
            try:
                await run(limit=min(batch, left), source=source)
            except Exception as exc:
                print(f"{source} round error: {exc} — retrying after 20s", flush=True)
                await asyncio.sleep(20)
                continue
            after = pending_count(source)
            print(f"{source} after round: pending={after}", flush=True)
            if after >= left:
                print(f"{source}: no progress this round; waiting 15s", flush=True)
                await asyncio.sleep(15)
                await run(limit=min(batch, left), source=source)
                after2 = pending_count(source)
                if after2 >= left:
                    print(f"{source}: still no progress; stopping source", flush=True)
                    break


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sources",
        default="acegrade,sundarbans",
        help="Comma-separated sources to drain",
    )
    args = parser.parse_args()
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    asyncio.run(main(sources))
