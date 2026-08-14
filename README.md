# IITM BS Educational Data Collector

Approved collection toolkit for notes, PYQs, cheat sheets, and Drive-linked resources from partner student sites.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
copy .env.example .env
```

## Collector account

Use: `23f3002876@ds.study.iitm.ac.in` (must have access on each source site).

Never put your password in `.env` or commit `.auth/`.

## AceGrade (working now)

AceGrade exposes a public backend API. We harvest:

- Notes: `/backendapi/notes/get_notes/{course}/{ds|es}`
- PYQs: `/backendapi/pyq/{course}/{ds|es}`
- Course catalog: `/backendapi/term/{term}`

Lectures (`/backendapi/course/{id}`) need Google login — run `login` first when we add that connector step.

```bash
# Index all notes + PYQ Drive links (fast)
python main.py scrape acegrade --no-download

# Also download N Drive PDFs locally
python main.py scrape acegrade --download-limit 50

# Optional browser session for auth-gated endpoints (lectures)
# 1) Close ALL Chrome windows
# 2) Run one of:
python main.py login acegrade
#    or explicitly:
python main.py login acegrade --chrome-profile "Profile 1"

python main.py status
python scripts\export_index.py
```

Outputs:
- `data/index.jsonl` / `data/index.csv` — unified metadata
- `data/files/acegrade/` — downloaded PDFs
- `data/raw/acegrade/` — API dumps
- `.auth/acegrade.auth.json` — local session (gitignored)

## Sources

| Source | Status | How |
|--------|--------|-----|
| acegrade.in | notes + PYQs + lectures | Cloud Run API + login for lectures |
| sundarbans.iitmbs.org | Study Corner catalog | Public StudyView.js chunk (no login) |
| unknowniitians.com | IITM notes/PYQs metadata + public Drive links | Public Supabase REST (anon key) |
| letslearn1110.com | course catalog | Graphy/Spayee store JSON |
| oppepractice.iitmbsdegree.in | OPPE PYQs + practice questions | `/api/subjects/{slug}/questions` + optional PDF solutions |

```bash
python main.py scrape acegrade --no-download
python main.py scrape sundarbans --no-download
python main.py scrape unknowniitians --no-download
python main.py scrape letslearn --no-download
python main.py scrape oppepractice --no-download
python main.py status
python scripts\download_missing.py --source acegrade --limit 100
python scripts\download_missing.py --source sundarbans --limit 50
python scripts\download_missing.py --source unknowniitians --limit 50
python scripts\export_index.py
```

## OPPE Practice

Question catalog is public (Python + DBMS live). Solution PDFs need a logged-in account with a phone number:

```bash
python main.py login oppepractice
python main.py scrape oppepractice --no-download
# After adding a phone number on the site:
python main.py scrape oppepractice --download-limit 50
```

Outputs: `data/raw/oppepractice/questions_{slug}.json`, `data/files/oppepractice/` (PDFs).
