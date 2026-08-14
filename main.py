from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console

from core.auth import interactive_login
from core.config import COLLECTOR_EMAIL, INDEX_PATH, load_index

app = typer.Typer(add_completion=False, help="IITM BS educational data collector")
console = Console()

START_URLS = {
    "acegrade": "https://www.acegrade.in/",
    "unknowniitians": "https://www.unknowniitians.com/exam-preparation/iitm-bs",
    "sundarbans": "https://sundarbans.iitmbs.org/study",
    "letslearn": "https://www.letslearn1110.com/",
    "oppepractice": "https://oppepractice.iitmbsdegree.in/",
}


@app.command("login")
def login(
    source: str = typer.Argument("acegrade", help="Site key: acegrade, sundarbans, ..."),
    school_profile: bool = typer.Option(
        False,
        "--school-profile",
        help="Use your real Chrome 'Profile 1' (School). Closes Chrome first.",
    ),
    chrome_profile: Optional[str] = typer.Option(
        None,
        "--chrome-profile",
        help="Chrome profile folder, e.g. 'Profile 1'. Implies --school-profile.",
    ),
) -> None:
    """Open Chrome, sign in to the site, save session."""
    import os

    from core.auth import detect_chrome_user_data_hint

    url = START_URLS.get(source)
    if not url:
        console.print(f"[red]Unknown source:[/red] {source}. Known: {', '.join(START_URLS)}")
        raise typer.Exit(1)

    use_system = school_profile or bool(chrome_profile)
    if chrome_profile:
        user_data = os.getenv("CHROME_USER_DATA_DIR") or detect_chrome_user_data_hint()
        if not user_data:
            console.print("[red]Could not find Chrome User Data folder.[/red]")
            raise typer.Exit(1)
        os.environ["CHROME_USER_DATA_DIR"] = user_data
        os.environ["CHROME_PROFILE_DIRECTORY"] = chrome_profile
        console.print(f"Using Chrome profile: [bold]{chrome_profile}[/bold]")

    console.print(f"Collector account hint: [bold]{COLLECTOR_EMAIL}[/bold]")
    if use_system:
        console.print("[yellow]Will close running Chrome, then open School profile.[/yellow]")
    else:
        console.print("[cyan]Using a separate collector Chrome window[/cyan]")
    asyncio.run(interactive_login(source, url, use_system_profile=use_system))


@app.command("discover")
def discover(
    source: str = typer.Argument("acegrade"),
    headed: bool = typer.Option(False, "--headed", help="Show browser window"),
) -> None:
    """Map pages / catalog for a source."""
    if source == "acegrade":
        from sources.acegrade import discover as fn

        console.print(asyncio.run(fn(headless=not headed)))
    elif source == "sundarbans":
        from sources.sundarbans import discover as fn

        console.print(asyncio.run(fn(headless=not headed)))
    elif source == "unknowniitians":
        from sources.unknowniitians import scrape_and_download as fn

        console.print({"note": "discover via scrape", **{"count": len(asyncio.run(fn()))}})
    elif source == "letslearn":
        from sources import stubs

        console.print(asyncio.run(stubs.discover(source, headed=headed)))
    elif source == "oppepractice":
        from sources.oppepractice import discover as fn

        console.print(asyncio.run(fn(headless=not headed)))
    else:
        console.print("[yellow]Unknown source[/yellow]", source)
        raise typer.Exit(1)


@app.command("discover-auth")
def discover_auth(
    source: str = typer.Argument("acegrade"),
    headed: bool = typer.Option(True, "--headless", help="Run headless (hide browser)"),
) -> None:
    """Map authenticated content / catalog for a source (requires login)."""
    if source == "oppepractice":
        from sources.oppepractice import discover_authenticated as fn

        console.print(asyncio.run(fn(headless=not headed)))
    else:
        console.print(f"[yellow]{source} does not support authenticated discovery[/yellow]")
        raise typer.Exit(1)


@app.command("scrape")
def scrape(
    source: str = typer.Argument("acegrade"),
    no_download: bool = typer.Option(False, "--no-download", help="Index links only"),
    download_limit: Optional[int] = typer.Option(
        None,
        "--download-limit",
        help="Max Google Drive files to download this run",
    ),
) -> None:
    """Harvest metadata and optionally download Drive files."""
    if source == "acegrade":
        from sources.acegrade import scrape_and_download as fn

        records = asyncio.run(fn(download=not no_download, download_limit=download_limit))
    elif source == "sundarbans":
        from sources.sundarbans import scrape_and_download as fn

        records = asyncio.run(fn(download=not no_download, download_limit=download_limit))
    elif source == "unknowniitians":
        from sources.unknowniitians import scrape_and_download as fn

        records = asyncio.run(fn(download=not no_download, download_limit=download_limit))
    elif source == "letslearn":
        from sources.letslearn import scrape_and_download as fn

        records = asyncio.run(fn(download=not no_download, download_limit=download_limit))
    elif source == "oppepractice":
        from sources.oppepractice import scrape_and_download as fn

        records = asyncio.run(fn(download=not no_download, download_limit=download_limit))
    else:
        console.print("[yellow]Unknown source[/yellow]", source)
        raise typer.Exit(1)
    console.print(f"Done. Records this run: {len(records)}. Index: {INDEX_PATH}")


@app.command("status")
def status() -> None:
    """Show how many resources are already indexed."""
    rows = load_index()
    by_source: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for r in rows:
        by_source[r.get("source", "?")] = by_source.get(r.get("source", "?"), 0) + 1
        by_type[r.get("type", "?")] = by_type.get(r.get("type", "?"), 0) + 1
    console.print(f"Total indexed: [bold]{len(rows)}[/bold]")
    console.print("[bold]By source[/bold]")
    for k, v in sorted(by_source.items()):
        console.print(f"  {k}: {v}")
    console.print("[bold]By type[/bold]")
    for k, v in sorted(by_type.items()):
        console.print(f"  {k}: {v}")


if __name__ == "__main__":
    app()
