# Livelihood Gap Engine (MVP)

Evidence-based readiness assessment for women-led SHG enterprises. A planning tool, **not** a prediction of success.
The default data is **real, sourced block-level facts for Balurghat block (Dakshin Dinajpur, West Bengal)**: Census 2011 population,
literacy, workers, roads, banks (via a Wikipedia summary of Census 2011 / District Statistical Handbook 2014, see the Evidence page).
No village-, SHG- or market-level data was found online, so **no enterprise is scored yet**: every factor shows Unknown and each enterprise
shows "Insufficient evidence" until an analyst adds field evidence in /admin. Optional demo data (clearly labelled) can be added with `python -m app.seed --demo`.

## What is built (MVP)
- Scoring engine (`backend/app/scoring.py`): configurable weights (must total 100), ratings 0-5, unknown is never 0,
  proxy evidence excluded, evidence coverage shown separately, below 60% coverage = "Insufficient evidence" (not ranked).
- FastAPI + SQLAlchemy API with seed script, evidence-source provenance, gap tracking (Unknown vs Missing kept separate),
  audit log, weight updates (admin only, confirmation required), CSV report.
- Next.js 16 frontend: Overview, Enterprise Recommendations (with comparison + factor evidence + assessment form),
  Supply-Chain Gaps (editable), Evidence & Data, Reports (CSV + print to PDF).

## Not built yet
Leaflet map / geographic explorer, CSV import workflow, PostGIS and Alembic migrations (tables are created with
`create_all`), user accounts (writes use API keys: analyst and admin roles; key kept in memory only), admin UI for weights (API exists),
Vitest / Playwright suites, deployment verification.

## Run locally
```bash
# backend
cd backend
pip install -r requirements.txt
python -m app.seed                      # real Balurghat block data; repeatable. Add --demo for labelled sample data
LGE_ANALYST_KEY=dev-analyst LGE_ADMIN_KEY=dev-admin uvicorn app.main:app --port 8000
# API docs: http://localhost:8000/docs   health: http://localhost:8000/health

# frontend (new terminal)
cd frontend
cp .env.example .env.local
npm install && npm run dev              # http://localhost:3000
```
Public pages are read-only. To add or edit data, open http://localhost:3000/admin and sign in with the analyst key (`dev-analyst` in the command above).

## Tests
```bash
cd backend && python -m pytest -q       # 36 tests
cd frontend && npm run build
```

## Deploy notes
Frontend: Vercel, set `NEXT_PUBLIC_API_URL`. Backend: Render (start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`),
set `DATABASE_URL`, `CORS_ORIGINS` (your frontend URL), `LGE_ANALYST_KEY`, `LGE_ADMIN_KEY`.
For PostgreSQL add a driver (e.g. `psycopg[binary]`). Verify with `GET /health`. Not yet deployed or verified.

## Data honesty
The only cluster is the whole Balurghat block (no village clusters, coordinates or boundaries were sourced). Travel times are not calculated.
Replace demo rows with real evidence via the Evidence page and assessment form.
