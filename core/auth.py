from __future__ import annotations

import asyncio
import os
import re
import subprocess
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from rich.console import Console

from core.config import AUTH_DIR, COLLECTOR_EMAIL, auth_state_path

console = Console()


def browser_profile_dir(source: str) -> Path:
    path = AUTH_DIR / "browser-profiles" / source
    path.mkdir(parents=True, exist_ok=True)
    return path


def chrome_is_running() -> bool:
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return "chrome.exe" in out.lower()
    except Exception:
        return False


def kill_chrome() -> None:
    subprocess.run(
        ["taskkill", "/F", "/IM", "chrome.exe", "/T"],
        capture_output=True,
        text=True,
    )


def detect_chrome_user_data_hint() -> str:
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    return str(local) if local.exists() else ""


async def launch_context(
    source: str,
    *,
    headless: bool = True,
    use_saved_auth: bool = True,
    use_system_profile: bool = False,
) -> tuple[Playwright, Browser | None, BrowserContext]:
    """
    Return (playwright, browser|None, context).

    Interactive login defaults to a *dedicated* Chrome profile under
    `.auth/browser-profiles/{source}` so your normal Chrome can stay open.
    """
    state = auth_state_path(source)
    pw = await async_playwright().start()
    common_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
    ]

    if not headless:
        if use_system_profile:
            user_data = os.getenv("CHROME_USER_DATA_DIR", "").strip() or detect_chrome_user_data_hint()
            profile = os.getenv("CHROME_PROFILE_DIRECTORY", "").strip() or "Profile 1"
            if chrome_is_running():
                console.print(
                    "[yellow]Chrome is running — closing it so Profile 1 can be used...[/yellow]"
                )
                kill_chrome()
                await asyncio.sleep(2)
            console.print(
                f"[green]Opening School Chrome profile[/green]\n"
                f"  user data: {user_data}\n"
                f"  profile:   {profile}"
            )
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=user_data,
                channel="chrome",
                headless=False,
                args=[*common_args, f"--profile-directory={profile}"],
                viewport={"width": 1400, "height": 900},
                ignore_default_args=["--enable-automation"],
            )
            return pw, None, context

        # Dedicated collector profile — safe while normal Chrome stays open
        profile_dir = browser_profile_dir(source)
        console.print(
            f"[green]Opening collector Chrome window[/green]\n"
            f"  profile: {profile_dir}\n"
            f"  Sign in with [bold]{COLLECTOR_EMAIL}[/bold] (one-time)."
        )
        kwargs: dict = {
            "user_data_dir": str(profile_dir),
            "channel": "chrome",
            "headless": False,
            "args": common_args,
            "viewport": {"width": 1400, "height": 900},
            "ignore_default_args": ["--enable-automation"],
        }
        # storage_state + persistent profile can conflict; prefer profile cookies
        context = await pw.chromium.launch_persistent_context(**kwargs)
        return pw, None, context

    browser = await pw.chromium.launch(headless=True, channel="chrome")
    kwargs: dict = {"viewport": {"width": 1400, "height": 900}}
    if use_saved_auth and state.exists():
        kwargs["storage_state"] = str(state)
        console.print(f"[green]Loaded auth session:[/green] {state}")
    else:
        console.print("[yellow]No saved session — run: python main.py login acegrade[/yellow]")
    context = await browser.new_context(**kwargs)
    return pw, browser, context


async def save_auth(context: BrowserContext, source: str) -> Path:
    path = auth_state_path(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    await context.storage_state(path=str(path))
    console.print(f"[green]Saved auth session:[/green] {path}")
    return path


async def _click_login_if_present(page: Page) -> None:
    for name in ("Log In", "Login", "Sign in", "Sign In", "Sign In/Register", "Sign In / Register"):
        try:
            btn = page.get_by_role("button", name=name).first
            if await btn.count():
                await btn.click(timeout=4000)
                await page.wait_for_timeout(1500)
                console.print(f"[cyan]Clicked[/cyan] {name}")
                return
        except Exception:
            continue
    for name in ("Sign In", "Login", "Sign in"):
        try:
            link = page.get_by_role("link", name=re.compile(name, re.I)).first
            if await link.count():
                await link.click(timeout=4000)
                await page.wait_for_timeout(1500)
                console.print(f"[cyan]Clicked link[/cyan] {name}")
                return
        except Exception:
            continue


async def interactive_login(
    source: str,
    start_url: str,
    *,
    use_system_profile: bool = False,
) -> Path:
    console.print(
        f"\n[bold]Login for [cyan]{source}[/cyan][/bold]\n"
        f"Account: [bold]{COLLECTOR_EMAIL}[/bold]\n"
        f"1) Wait for AceGrade to load (not about:blank)\n"
        f"2) Click Log In / Google if asked\n"
        f"3) Use [bold]{COLLECTOR_EMAIL}[/bold]\n"
        f"4) When logged in, come back here and press Enter\n"
    )

    pw, browser, context = await launch_context(
        source,
        headless=False,
        use_saved_auth=False,
        use_system_profile=use_system_profile,
    )

    # Always open a fresh tab and navigate — avoids stuck about:blank
    page = await context.new_page()
    console.print(f"[cyan]Navigating to[/cyan] {start_url}")
    try:
        await page.goto(start_url, wait_until="domcontentloaded", timeout=90_000)
        await page.wait_for_timeout(2000)
        console.print(f"[green]Loaded:[/green] {page.url} | {await page.title()}")
    except Exception as exc:
        console.print(f"[red]Navigation failed:[/red] {exc}")
        console.print("Paste this URL manually in the open Chrome window:")
        console.print(f"  {start_url}")

    await _click_login_if_present(page)

    await asyncio.to_thread(input, "Press Enter after login is complete... ")
    path = await save_auth(context, source)

    # Keep system Chrome usable: only close the automation context
    try:
        await context.close()
    except Exception:
        pass
    if browser:
        await browser.close()
    await pw.stop()
    return path
