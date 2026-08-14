# OPPE Practice - Authenticated Scraping Setup Guide

## Quick Start Workflow

### Step 1: Login to OPPE Practice
```bash
python main.py login oppepractice
```
This will:
- Open a Chrome browser window
- Navigate to https://oppepractice.iitmbsdegree.in/
- Let you sign in with your account (Google or email)
- Save your authenticated session in `.auth/oppepractice.auth.json`

### Step 2: Discover Authenticated Content
```bash
python main.py discover-auth oppepractice
```
This will:
- Load your saved authentication
- Visit authenticated app pages (subjects, progress, tests)
- Intercept API calls and save them
- Extract courses, subjects, and question info
- Generate files in `data/raw/oppepractice/`:
  - `browser_api_payloads_auth.json` - API responses
  - `browser_pages_auth.json` - Pages visited

### Step 3: Full Scrape (with auth)
```bash
python main.py scrape oppepractice
```
This will:
1. Run public discovery first
2. Load your saved authentication
3. Attempt to access protected API endpoints:
   - `/api/user/profile` - Your profile
   - `/api/subjects` - Subjects list
   - `/api/courses` - Courses
   - `/api/questions` - Practice questions
   - `/api/tests` - Mock tests
   - `/api/leaderboard` - Leaderboard data
4. Index all found resources to `data/index.jsonl`

## Output Files

### During Discovery
- `data/raw/oppepractice/page_*.html` - HTML pages
- `data/raw/oppepractice/page_*.meta.json` - Page metadata
- `data/raw/oppepractice/discover_links.json` - Extracted links
- `data/raw/oppepractice/discover_pages.json` - Page info
- `data/raw/oppepractice/browser_api_payloads_auth.json` - API calls intercepted
- `data/raw/oppepractice/browser_pages_auth.json` - Auth pages visited

### During Scraping
- `data/raw/oppepractice/subjects_page_full.html` - Full subjects page
- `data/raw/oppepractice/subjects_found_auth.json` - Extracted subjects
- `data/raw/oppepractice/api_responses_auth.json` - API responses from endpoints

### Final Index
- `data/index.jsonl` - All indexed resources (one JSON per line)

## Data Structure

Each indexed resource has:
- `id` - UUID
- `source` - "oppepractice"
- `title` - Resource name
- `program` - Subject/program
- `type` - Resource type (PREVIOUS_PAPER, LINK, NOTES, etc.)
- `original_url` - Source URL
- `gdrive_urls` - Any Google Drive links found
- `extra` - Additional metadata (subject, question_id, authenticated flag, etc.)
- `scraped_at` - Timestamp
- `permission_note` - "written-approval-from-source"

## Authentication Details

Your authentication state is stored in:
- `.auth/oppepractice.auth.json` - Cookies and session data
- `.auth/browser-profiles/oppepractice/` - Browser profile cache

These files allow:
- Reusing your session for future scrapes
- Accessing protected content without re-login
- Long-lived authenticated sessions

## Troubleshooting

**"Found saved authentication" but no data:**
- The API endpoints may require additional headers or cookies
- Some endpoints may not be accessible to non-admin users
- Check `data/raw/oppepractice/api_responses_auth.json` for actual API responses

**"Authentication saved but session expired:"**
- Delete `.auth/oppepractice.auth.json` and login again
- Re-run: `python main.py login oppepractice`

**Browser window won't close after login:**
- Press Ctrl+C to terminate the process
- The authentication has already been saved

## Advanced: Manual API Testing

If you want to test specific API endpoints:

```python
import httpx
import json

auth = json.load(open('.auth/oppepractice.auth.json'))
cookies = {c['name']: c['value'] for c in auth.get('cookies', [])}

async with httpx.AsyncClient(cookies=cookies) as client:
    resp = await client.get('https://oppepractice.iitmbsdegree.in/api/questions')
    print(resp.json())
```

## Collection Notes

- Platform supports Google login
- Questions are graded against test cases
- Leaderboards track fastest solutions
- Previous year questions (PYQs) are available for multiple OPPE terms
- Subjects available: Python, DBMS, Java, Data Structures (more coming soon)
