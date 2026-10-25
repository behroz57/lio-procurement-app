# Lio Procurement App

Upload vendor-offer PDFs, extract the details with AI, and track procurement
requests across your organization.

- **`frontend/`** — Next.js 15, React 19, Tailwind CSS 4
- **`backend/`** — FastAPI, SQLAlchemy, Alembic, JWT auth
- **Database** — PostgreSQL 16

## Prerequisites

- **Docker Desktop** (or Docker Engine) with **Docker Compose v2** — running before you start.
- An **OpenAI API key** is optional: the app boots and runs without it; only PDF extraction needs one.

## Quick start (Docker)

```bash
cp .env.example .env      # optional: add your OpenAI API key for PDF extraction
docker compose up --build
```

| | URL |
| --- | --- |
| App | http://localhost:3200 |
| API + docs | http://localhost:7200 · http://localhost:7200/docs |

Sign in with a demo account — no registration needed (password `demo1234`).
Development happens in the **Acme Corp** organization, which has one of each role:

| Account | Role | Can do |
| --- | --- | --- |
| `admin@acme.com` | admin | everything incl. members & settings |
| `buyer@acme.com` | buyer | approve requests |
| `requester@acme.com` | requester | create & track requests |

Every request needs approval by a buyer or admin (see the **Approvals** tab).

Other tenants exist to show the multi-tenant setup — **Walmart, Schaeffler,
Siemens, BMW** — each with an admin at `admin@<company>.com` (same password).
Their data is fully isolated from Acme's.

On startup the backend applies schema migrations and seeds the database (demo
accounts, organizations, commodity groups, suppliers, Acme's commodity rules).
Both steps are idempotent, so restarts never duplicate or overwrite anything (your data lives
in the `postgres_data` volume; `docker compose down -v` resets it). The OpenAI
key is only needed for PDF extraction — everything else works without it.

## Local development

```bash
docker compose up db        # Postgres only

# backend  → http://localhost:7200
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"     # app + dev tools (pytest, ruff)
alembic upgrade head        # schema
python -m app.seed          # reference data (idempotent)
uvicorn app.main:app --reload --port 7200

# frontend → http://localhost:3200
cd frontend
npm install
npm run dev
```

## PDF extraction

Uploading a vendor quote parses the PDF (pypdf, layout mode) and sends the text
to OpenAI (`OPENAI_MODEL`, default `gpt-4.1-mini`) with a strict JSON schema.

- Alternatives and optional items ("Alt.", "Alternative", "U.P.") are returned
  separately and are not part of the order lines or the total.
- The total cost is always the sum of the order lines, computed on the server.
- The "could not extract" notice lists only the vendor, the order lines and the
  fields the organization marks as required in its settings.
- The form shows warnings to review when line math doesn't add up, when the
  lines don't match the document's grand total minus taxes and fees, or when
  the vendor looks like the customer the quote is addressed to.

Measure extraction quality against the sample quotes in
`backend/tests/fixtures/quotes/` (needs the backend running with an OpenAI key):

```bash
cd backend
python scripts/eval_extraction.py            # live run, saves to out/eval/
python scripts/eval_extraction.py --rescore  # rescore the saved responses
```

## Catalog price suggestions

On the New Request page each order line shows up to 3 matching articles from the
organization's own catalog with the negotiated unit price, while typing and right
after PDF extraction. "Use" fills the line's description, unit price and unit;
when the line already has a higher price in the same unit, the saving per unit is
shown. No OpenAI key is needed.

`GET /articles/suggestions?q=…&limit=3` scores articles by the share of the
line's words they contain, each word weighted by how rare it is in the catalog
(filler, numbers and units are ignored). Weak matches return nothing; ties go to
the cheapest article. Check it with `python scripts/eval_suggestions.py` (or
`--search` for the old phrase search as a baseline).

## Commodity rules (per organization)

An organization can force how requests are categorised with `commodity_rules`
in its settings (`PATCH /organizations/me/settings`, admins only). Each rule is
`{keywords, commodity_group_id, max_total?, from_commodity_group_ids?}`:

- It fires if a keyword appears in the request title or an order line
  (case-insensitive, whole words, singular or plural), or if the request's
  current group is in `from_commodity_group_ids`, and the request total is at
  most `max_total` (inclusive).
- Rules are checked in order; the first match wins and sets the group for the
  whole request.
- Rules apply when a request is created or updated (overriding the chosen
  group, logged in the request's activity) and right after PDF extraction.
- Settings PATCH merges: fields left out of the body keep their saved values.

Acme Corp is seeded with three rules (only if it has no `commodity_rules` yet;
existing rules are never overwritten). Other tenants start with none.

| Order | Matches | Group |
| --- | --- | --- |
| 1 | "cable tie", "zip tie" | 11 Facility Management / Electrical Engineering |
| 2 | "toner" | 29 Information Technology / Hardware |
| 3 | "software subscription", "saas", "software license", "software licence", or current group 31, 106, 436, 766, 1096, 1426, 1756; total up to 1000 | 15 Facility Management / Office Equipment |

## Tests

```bash
cd backend && python -m pytest      # API + extraction tests (in-memory SQLite, mocked OpenAI)
cd frontend && npx tsc --noEmit && npm run build
```

## Layout

```
backend/    FastAPI app — routers, services, models, schemas, core; Alembic migrations
frontend/   Next.js app — app-router pages, typed API client (src/lib/api), components
docker-compose.yml
```
