"""Download Google Drive files listed in data/index.jsonl that lack local_path."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
import typer
from rich.console import Console

from core.fetch import download_file, safe_name
from sources.acegrade import extract_drive_id, gdrive_uc_url

app = typer.Typer(add_completion=False)
console = Console()
INDEX = ROOT / "data" / "index.jsonl"


def read_index() -> list[dict]:
    if not INDEX.exists():
        return []
    return [json.loads(l) for l in INDEX.read_text(encoding="utf-8").splitlines() if l.strip()]


def write_index(rows: list[dict]) -> None:
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    with INDEX.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


async def run(limit: int | None, source: str | None) -> None:
    rows = read_index()
    pending = []
    for r in rows:
        if source and r.get("source") != source:
            continue
        if r.get("local_path"):
            continue
        urls = r.get("gdrive_urls") or []
        if not urls or not extract_drive_id(urls[0]):
            continue
        pending.append(r)
    if limit is not None:
        pending = pending[:limit]
    console.print(f"Pending downloads: {len(pending)}")

    saved = 0
    updates: dict[str, dict] = {}
    consecutive_net_fails = 0
    async with httpx.AsyncClient(follow_redirects=True, timeout=120.0) as client:
        for r in pending:
            file_id = extract_drive_id(r["gdrive_urls"][0])
            assert file_id
            extra = r.get("extra") or {}
            kind = "notes" if r.get("type") == "notes" else ("pyq" if r.get("type") == "previous_paper" else "files")
            course_key = extra.get("course_id") or extra.get("course_code") or "misc"
            dest = safe_name(
                f"{course_key}_{kind}_{extra.get('note_title') or extra.get('exam_type') or r.get('title') or 'file'}_{r.get('term') or ''}.pdf"
            )
            try:
                path = await download_file(
                    client,
                    gdrive_uc_url(file_id),
                    r["source"],
                    subdir=f"{kind}/{course_key}",
                    suggested_name=dest,
                )
                consecutive_net_fails = 0
            except Exception as exc:
                consecutive_net_fails += 1
                console.print(f"[red]Download error[/red] {exc}")
                if consecutive_net_fails >= 5:
                    console.print("[yellow]Too many network failures — pausing this batch[/yellow]")
                    break
                await asyncio.sleep(min(30, 2 * consecutive_net_fails))
                continue
            if not path or path.stat().st_size < 500:
                if path:
                    path.unlink(missing_ok=True)
                continue
            head = path.read_bytes()[:300].lower()
            if b"<html" in head or b"<!doctype" in head:
                path.unlink(missing_ok=True)
                console.print(f"[yellow]Drive gate:[/yellow] {r.get('title','')[:80]}")
                continue
            r["local_path"] = str(path)
            r["mime_or_ext"] = path.suffix
            updates[r["id"]] = r
            saved += 1

    # Re-read latest index so concurrent scrapes aren't wiped, then patch local paths
    latest = read_index()
    patched = 0
    out = []
    for row in latest:
        upd = updates.get(row.get("id"))
        if upd:
            row = {**row, "local_path": upd.get("local_path"), "mime_or_ext": upd.get("mime_or_ext")}
            patched += 1
        out.append(row)
    # Also append any downloaded ids missing from latest (shouldn't happen)
    latest_ids = {r.get("id") for r in latest}
    for rid, upd in updates.items():
        if rid not in latest_ids:
            out.append(upd)
    write_index(out)
    console.print(f"[green]Saved {saved} files[/green] (patched {patched} index rows)")


@app.command()
def main(
    limit: int = typer.Option(50, help="Max files this run"),
    source: str = typer.Option("", help="Filter by source (empty = all)"),
) -> None:
    asyncio.run(run(limit=limit, source=source or None))


if __name__ == "__main__":
    app()
