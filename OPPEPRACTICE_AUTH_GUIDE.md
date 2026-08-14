# OPPE Practice

Catalog is public. Solution PDFs need a logged-in account **with a phone number**.

```bash
python main.py login oppepractice
python main.py scrape oppepractice --no-download
python main.py scrape oppepractice --download-limit 50
```

## What we harvest

- `GET /api/subjects/{slug}/questions?offset=N` — paginated question list (30/page)
- Live slugs today: `python`, `dbms` (PDSA / Java / C / syscmd listed but not released)
- Each question indexes:
  - practice page: `/app/questions/{id}`
  - solution PDF: `/api/questions/{id}/pdf`
- Subjects / topics from the site's public Supabase catalog

## PDF downloads

The site returns `403 Add your phone number to download this question.` until the collector account has a phone on https://oppepractice.iitmbsdegree.in/app/settings.

Then re-run scrape with `--download-limit`.

## Outputs

- `data/raw/oppepractice/questions_python.json`
- `data/raw/oppepractice/questions_dbms.json`
- `data/raw/oppepractice/supa_subjects.json`
- `data/files/oppepractice/` — PDFs (after phone + download)
- `data/index.jsonl` — unified metadata
