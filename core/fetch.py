from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse, unquote

import httpx
from rich.console import Console
from tenacity import retry, stop_after_attempt, wait_exponential

from core.config import REQUEST_DELAY_SECONDS, source_files_dir

console = Console()

GDRIVE_RE = re.compile(
    r"https?://(?:drive|docs)\.google\.com/[^\s\"'<>]+",
    re.IGNORECASE,
)
FILE_EXT_RE = re.compile(r"\.(pdf|docx?|pptx?|xlsx?|zip|rar|txt|md|png|jpe?g|webp)(?:\?|$)", re.I)


def extract_gdrive_urls(text: str) -> list[str]:
    return sorted(set(GDRIVE_RE.findall(text or "")))


def guess_filename(url: str, content_disposition: str | None = None, default: str = "file.bin") -> str:
    if content_disposition and "filename=" in content_disposition:
        part = content_disposition.split("filename=")[-1].strip().strip('"')
        if part:
            return Path(unquote(part)).name
    path = unquote(urlparse(url).path)
    name = Path(path).name
    if name and "." in name:
        return name
    return default


def safe_name(name: str) -> str:
    cleaned = re.sub(r"[^\w.\- ()\[\]]+", "_", name).strip(" ._")
    return cleaned[:180] or "file"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
async def download_file(
    client: httpx.AsyncClient,
    url: str,
    source: str,
    *,
    cookies: dict[str, str] | None = None,
    subdir: str = "",
    suggested_name: str | None = None,
) -> Path | None:
    await asyncio.sleep(REQUEST_DELAY_SECONDS)
    headers = {"User-Agent": "IITM-BS-Educational-Collector/1.0 (approved-collection)"}
    resp = await client.get(url, headers=headers, cookies=cookies, follow_redirects=True, timeout=120.0)
    if resp.status_code >= 400:
        console.print(f"[red]Download failed {resp.status_code}:[/red] {url}")
        return None
    name = suggested_name or guess_filename(url, resp.headers.get("content-disposition"))
    if not FILE_EXT_RE.search(name):
        ctype = resp.headers.get("content-type", "")
        if "pdf" in ctype:
            name += ".pdf"
        elif "zip" in ctype:
            name += ".zip"
    dest_dir = source_files_dir(source) / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(url.encode()).hexdigest()[:8]
    dest = dest_dir / f"{digest}_{safe_name(name)}"
    dest.write_bytes(resp.content)
    console.print(f"[green]Saved[/green] {dest} ({len(resp.content)} bytes)")
    return dest


def cookies_from_storage_state(state: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for c in state.get("cookies", []):
        out[c["name"]] = c["value"]
    return out
