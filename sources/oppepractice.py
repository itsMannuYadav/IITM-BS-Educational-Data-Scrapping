"""
OPPE Practice connector — scrapes practice questions and previous year questions.

Public endpoints:
  GET /app/subjects - Lists all subjects/courses
  GET /leaderboard - Leaderboard data
  Individual questions and tests are in the React app

Auth required for full access:
  Login via Google/email
  API endpoints for questions, tests, submissions
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from playwright.async_api import Page
from rich.console import Console
from rich.progress import Progress

from core.auth import launch_context, save_auth
from core.config import INDEX_PATH, REQUEST_DELAY_SECONDS, load_index
from core.fetch import extract_gdrive_urls
from core.schema import ResourceRecord, ResourceType
from core.store import write_raw_json

console = Console()

SOURCE = "oppepractice"
BASE = "https://oppepractice.iitmbsdegree.in"
API_BASE = f"{BASE}/api"

SEED_PATHS = ["/", "/app/subjects", "/leaderboard", "/contact", "/privacy"]
APP_PATHS = ["/app/subjects", "/app/progress", "/app/tests"]


def extract_api_data_from_html(html: str) -> dict[str, Any]:
    """Extract embedded API data, links, and other content from HTML."""
    data = {
        "gdrive_urls": extract_gdrive_urls(html),
        "file_urls": [],
        "api_endpoints": [],
        "subjects": [],
        "questions_count": 0,
    }
    
    # Look for file URLs
    import re
    file_urls = re.findall(
        r'https?://[^\s"\'<>]*\.(pdf|docx?|xlsx?|pptx?|zip|txt)',
        html,
        re.I
    )
    data["file_urls"] = list(set(file_urls))
    
    # Look for API endpoints in JavaScript
    api_patterns = re.findall(
        r'(?:fetch|axios|api).*?["\']([^"\']*(?:api|v1)[^"\']*)["\']',
        html,
        re.I
    )
    data["api_endpoints"] = list(set(api_patterns))[:20]
    
    # Look for subject references
    subjects = re.findall(
        r'(?:Python|DBMS|Java|C|Data Structures|PDSA|System Commands)',
        html
    )
    data["subjects"] = list(set(subjects))
    
    # Count question references
    data["questions_count"] = len(re.findall(r'question|problem|exercise', html, re.I))
    
    return data



async def discover(headless: bool = True) -> dict[str, Any]:
    """Discover available courses and structure (public)."""
    headers = {
        "User-Agent": "IITM-BS-Educational-Collector/1.0 (approved-collection)",
        "Accept": "application/json, text/html",
    }
    pages = []
    links: list[dict[str, str]] = []
    
    async with httpx.AsyncClient(timeout=40, headers=headers, follow_redirects=True) as client:
        # Fetch main pages
        for seed in SEED_PATHS:
            url = urljoin(BASE, seed)
            try:
                resp = await client.get(url)
                page_name = seed.strip("/").replace("/", "_") or "home"
                
                # Save metadata
                write_raw_json(SOURCE, f"page_{page_name}.meta.json", {
                    "url": str(resp.url),
                    "status": resp.status_code,
                })
                
                # Save HTML
                Path(f"data/raw/{SOURCE}").mkdir(parents=True, exist_ok=True)
                Path(f"data/raw/{SOURCE}/page_{page_name}.html").write_text(
                    resp.text, encoding="utf-8"
                )
                
                pages.append({
                    "url": str(resp.url),
                    "status": resp.status_code,
                    "bytes": len(resp.text)
                })
                
                # Extract links and Google Drive URLs
                for href, title in re.findall(
                    r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                    resp.text,
                    flags=re.I | re.S
                ):
                    abs_url = urljoin(str(resp.url), href)
                    text = re.sub(r"<[^>]+>", "", title).strip()[:200]
                    links.append({"url": abs_url, "title": text})
                
                # Extract Google Drive URLs
                for m in extract_gdrive_urls(resp.text):
                    links.append({"url": m, "title": "Google Drive resource"})
                    
            except Exception as exc:
                pages.append({"url": url, "error": str(exc)})
    
    write_raw_json(SOURCE, "discover_links.json", links)
    write_raw_json(SOURCE, "discover_pages.json", pages)
    
    return {
        "source": SOURCE,
        "pages": len(pages),
        "links": len(links),
        "note": "OPPE Practice platform — requires login for full access to questions"
    }


async def discover_authenticated(headless: bool = True) -> dict[str, Any]:
    """Discover available courses and structure with authentication."""
    api_bag: list[dict[str, Any]] = []
    pw, browser, context = await launch_context(SOURCE, headless=headless, use_saved_auth=True)
    page = await context.new_page()

    async def on_response(resp):
        try:
            ct = (resp.headers.get("content-type") or "").lower()
            url = resp.url
            # Capture all API responses
            if resp.status in (200, 201, 400, 401, 403) and "application/json" in ct:
                try:
                    data = await resp.json()
                except Exception:
                    data = None
                api_bag.append({
                    "url": url,
                    "method": resp.request.method,
                    "status": resp.status,
                    "data": data
                })
        except Exception:
            return

    page.on("response", on_response)
    pages = []
    
    # Visit authenticated app pages and wait longer
    for path in APP_PATHS:
        url = f"{BASE}{path}"
        console.print(f"[cyan]Visit (auth)[/cyan] {url}")
        try:
            await page.goto(url, wait_until="networkidle", timeout=60_000)
            # Wait extra time for all API calls to complete
            await asyncio.sleep(REQUEST_DELAY_SECONDS * 2)
            pages.append({
                "url": page.url,
                "title": await page.title(),
                "authenticated": True
            })
        except Exception as exc:
            pages.append({
                "url": url,
                "error": str(exc),
                "authenticated": True
            })
    
    write_raw_json(SOURCE, "browser_api_payloads_auth.json", api_bag)
    write_raw_json(SOURCE, "browser_pages_auth.json", pages)
    
    # Save authentication state for future requests
    await save_auth(context, SOURCE)
    await context.close()
    if browser:
        await browser.close()
    await pw.stop()
    
    return {
        "source": SOURCE,
        "authenticated": True,
        "pages": len(pages),
        "api_calls": len(api_bag),
        "note": "Authentication saved for future scraping"
    }


async def scrape_and_download(
    *, download: bool = False, download_limit: int | None = None
) -> list[ResourceRecord]:
    """Scrape available courses and practice materials with authentication."""
    console.print("[bold]Running public discovery[/bold]")
    await discover()
    
    records: list[ResourceRecord] = []
    
    # Parse existing HTML pages for embedded content
    console.print("[bold]Analyzing captured HTML pages for links and content...[/bold]")
    html_pages = [
        "data/raw/oppepractice/page_app_subjects.html",
        "data/raw/oppepractice/page_home.html",
        "data/raw/oppepractice/page_leaderboard.html",
    ]
    
    for page_path in html_pages:
        try:
            page_file = Path(page_path)
            if page_file.exists():
                page_html = page_file.read_text()
                page_data = extract_api_data_from_html(page_html)
                
                # Extract Google Drive links
                for gdrive_url in page_data.get("gdrive_urls", []):
                    console.print(f"  Found Google Drive link: {gdrive_url[:80]}")
                    records.append(
                        ResourceRecord(
                            source=SOURCE,
                            title=f"Question/Content - {gdrive_url.split('/')[-2][:30]}",
                            type=ResourceType.GDRIVE,
                            original_url=gdrive_url,
                            gdrive_urls=[gdrive_url],
                            authors=["IITM BS Community"],
                            permission_note="written-approval-from-source",
                            extra={
                                "source_page": page_path,
                                "authenticated": True,
                            },
                        )
                    )
                
                # Extract direct file links  
                for file_url in page_data.get("file_urls", []):
                    console.print(f"  Found file link: {Path(file_url).name}")
                    records.append(
                        ResourceRecord(
                            source=SOURCE,
                            title=f"Practice Content - {Path(file_url).name}",
                            type=ResourceType.PREVIOUS_PAPER,
                            original_url=file_url,
                            file_url=file_url,
                            authors=["IITM BS Community"],
                            permission_note="written-approval-from-source",
                            extra={
                                "source_page": page_path,
                                "authenticated": True,
                            },
                        )
                    )
                
                if page_data.get("questions_count", 0) > 0:
                    console.print(f"  {Path(page_path).name}: {page_data['questions_count']} question references")
                    
        except Exception as e:
            console.print(f"[yellow]Error parsing {page_path}: {e}[/yellow]")
    
    headers = {
        "User-Agent": "IITM-BS-Educational-Collector/1.0 (approved-collection)",
        "Accept": "application/json",
    }
    
    # Try to load saved auth and fetch authenticated content
    auth_state_path = Path(f".auth/{SOURCE}.auth.json")
    auth_cookies = {}
    auth_headers = headers.copy()
    
    if auth_state_path.exists():
        console.print(f"[green]Found saved authentication[/green]")
        try:
            state = json.loads(auth_state_path.read_text(encoding="utf-8"))
            # Extract cookies from auth state
            if "cookies" in state:
                for cookie in state["cookies"]:
                    auth_cookies[cookie["name"]] = cookie["value"]
            console.print(f"[green]Loaded {len(auth_cookies)} auth cookies[/green]")
        except Exception as e:
            console.print(f"[yellow]Could not load auth state: {e}[/yellow]")
    
    async with httpx.AsyncClient(
        timeout=60, 
        headers=auth_headers,
        cookies=auth_cookies,
        follow_redirects=True
    ) as client:
        # Try to get subjects/courses information
        try:
            console.print("[bold]Fetching subjects...[/bold]")
            resp = await client.get(f"{BASE}/app/subjects")
            subjects_data = resp.text
            write_raw_json(SOURCE, "subjects_page_full.html", subjects_data)
            
            # Extract subject names from the page
            subjects = []
            for match in re.findall(
                r'(?:Programming in Python|Database Management|DBMS|Java|Data Structures|Python|DataStructures|C|PDSA)',
                subjects_data,
                re.I
            ):
                if match not in subjects:
                    subjects.append(match)
            
            write_raw_json(SOURCE, "subjects_found_auth.json", subjects)
            console.print(f"[green]Found {len(subjects)} subjects[/green]")
            
            # Try to access API endpoints directly
            console.print("[bold]Attempting API access (auth)...[/bold]")
            
            # Common API patterns for question/content APIs
            api_endpoints = [
                "/api/user/profile",
                "/api/subjects",
                "/api/courses",
                "/api/questions",
                "/api/tests",
                "/api/leaderboard",
                "/api/v1/questions",
                "/api/v1/problems",
            ]
            
            # Try subject-specific endpoints
            subject_endpoints = [
                "/api/subjects/python/questions",
                "/api/subjects/dbms/questions",
                "/api/questions?subject=python",
                "/api/questions?subject=dbms",
            ]
            
            api_endpoints.extend(subject_endpoints)
            
            api_responses = {}
            for endpoint in api_endpoints:
                try:
                    api_resp = await client.get(f"{BASE}{endpoint}")
                    if api_resp.status_code in (200, 201):
                        try:
                            data = api_resp.json()
                            api_responses[endpoint] = {"status": api_resp.status_code, "data": data}
                            console.print(f"  {endpoint}: {api_resp.status_code} [OK]")
                            
                            # If this returns questions, extract them
                            if isinstance(data, dict) and any(
                                k in data for k in ["questions", "problems", "items", "data", "results", "rows"]
                            ):
                                questions_list = (
                                    data.get("questions") or data.get("problems") or 
                                    data.get("items") or data.get("data") or data.get("results") or data.get("rows") or []
                                )
                                if isinstance(questions_list, list):
                                    console.print(f"    Found {len(questions_list)} items")
                                    
                                    # Extract subject from endpoint URL
                                    subject_match = re.search(r'/api/subjects/(\w+)/', endpoint)
                                    subject_from_url = subject_match.group(1).upper() if subject_match else None
                                    
                                    for q in questions_list[:50]:  # Sample first 50
                                        if isinstance(q, dict):
                                            title = q.get("title") or q.get("name") or q.get("question", "Question")
                                            # Prefer subject from URL, then topicName, then tags
                                            subject = (
                                                subject_from_url or 
                                                q.get("subject") or q.get("topicName") or 
                                                q.get("category") or "General"
                                            )
                                            q_id = q.get("id") or q.get("_id")
                                            
                                            records.append(
                                                ResourceRecord(
                                                    source=SOURCE,
                                                    title=f"{title}",
                                                    program=subject,
                                                    type=ResourceType.PREVIOUS_PAPER,
                                                    original_url=f"{BASE}/app/subjects/{subject_from_url.lower() if subject_from_url else 'general'}" if subject_from_url else f"{BASE}/app/subjects",
                                                    authors=["IITM BS Community"],
                                                    permission_note="written-approval-from-source",
                                                    extra={
                                                        "subject": subject,
                                                        "topic": q.get("topicName"),
                                                        "question_id": q_id,
                                                        "difficulty": q.get("difficulty") or q.get("level"),
                                                        "exam": q.get("exam"),
                                                        "category": "practice_question",
                                                        "authenticated": True,
                                                    },
                                                )
                                            )
                        except Exception as parse_err:
                            api_responses[endpoint] = {
                                "status": api_resp.status_code,
                                "error": f"Could not parse JSON: {str(parse_err)[:100]}"
                            }
                            console.print(f"  {endpoint}: {api_resp.status_code} (parse error)")
                    else:
                        console.print(f"  {endpoint}: {api_resp.status_code}")
                except Exception as e:
                    pass  # Silent fail for endpoints that don't exist
            
            write_raw_json(SOURCE, "api_responses_auth.json", api_responses)
            
            # If no questions found via API, create subject records
            if not records and subjects:
                console.print("[yellow]No API questions found - creating subject records[/yellow]")
                for subject in subjects:
                    records.append(
                        ResourceRecord(
                            source=SOURCE,
                            title=f"{subject} - Practice Questions",
                            program=subject,
                            type=ResourceType.PREVIOUS_PAPER,
                            original_url=f"{BASE}/app/subjects",
                            authors=["IITM BS Community"],
                            permission_note="written-approval-from-source",
                            extra={
                                "subject": subject,
                                "platform": "OPPE Practice",
                                "category": "practice_questions",
                                "authenticated": True,
                            },
                        )
                    )
            
        except Exception as exc:
            console.print(f"[yellow]Error during authenticated scrape: {exc}[/yellow]")
    
    # Add main platform records
    if not records:  # Only add if no authenticated content found
        records = [
            ResourceRecord(
                source=SOURCE,
                title="OPPE Practice Platform - Online Proctored Programming Exam",
                program=None,
                type=ResourceType.LINK,
                original_url=f"{BASE}/app/subjects",
                authors=["IITM BS Community"],
                permission_note="written-approval-from-source",
                extra={
                    "platform": "OPPE Practice",
                    "description": "Practice questions, PYQs, and mock tests for OPPE exams",
                    "authenticated": False,
                },
            ),
            ResourceRecord(
                source=SOURCE,
                title="OPPE Practice - Question Leaderboards",
                program=None,
                type=ResourceType.LINK,
                original_url=f"{BASE}/leaderboard",
                authors=["IITM BS Community"],
                permission_note="written-approval-from-source",
                extra={
                    "category": "leaderboard",
                    "description": "Real-time leaderboards for each practice question",
                },
            ),
        ]
    else:
        # Append platform info records
        records.extend([
            ResourceRecord(
                source=SOURCE,
                title="OPPE Practice Platform - Online Proctored Programming Exam",
                program=None,
                type=ResourceType.LINK,
                original_url=f"{BASE}/app/subjects",
                authors=["IITM BS Community"],
                permission_note="written-approval-from-source",
                extra={
                    "platform": "OPPE Practice",
                    "description": "Practice questions, PYQs, and mock tests for OPPE exams",
                    "authenticated": True,
                },
            ),
        ])
    
    # Merge into index
    old = [r for r in load_index() if r.get("source") != SOURCE]
    with INDEX_PATH.open("w", encoding="utf-8") as f:
        for row in old:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        for rec in records:
            f.write(rec.model_dump_json() + "\n")
    
    console.print(f"[green]{SOURCE}: indexed {len(records)} resources[/green]")
    return records

