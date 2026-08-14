from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv
import os

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
AUTH_DIR = ROOT / os.getenv("AUTH_DIR", ".auth")
DATA_DIR = ROOT / os.getenv("DATA_DIR", "data")
RAW_DIR = DATA_DIR / "raw"
FILES_DIR = DATA_DIR / "files"
INDEX_PATH = DATA_DIR / "index.jsonl"

COLLECTOR_EMAIL = os.getenv("COLLECTOR_EMAIL", "23f3002876@ds.study.iitm.ac.in")
REQUEST_DELAY_SECONDS = float(os.getenv("REQUEST_DELAY_SECONDS", "1.5"))

for d in (AUTH_DIR, RAW_DIR, FILES_DIR):
    d.mkdir(parents=True, exist_ok=True)


def auth_state_path(source: str) -> Path:
    return AUTH_DIR / f"{source}.auth.json"


def source_files_dir(source: str) -> Path:
    path = FILES_DIR / source
    path.mkdir(parents=True, exist_ok=True)
    return path


def source_raw_dir(source: str) -> Path:
    path = RAW_DIR / source
    path.mkdir(parents=True, exist_ok=True)
    return path


def append_index(records: list[dict]) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with INDEX_PATH.open("a", encoding="utf-8") as f:
        for row in records:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_index() -> list[dict]:
    if not INDEX_PATH.exists():
        return []
    rows: list[dict] = []
    with INDEX_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
