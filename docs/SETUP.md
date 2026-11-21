# Local setup

Everything below is PowerShell (Windows). No `export`, no `&&`, no `rm -rf`.

## 0. Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| Python | 3.14.x or 3.12.x | Every pinned dependency ships a cp314 wheel, so 3.14 works. Render uses 3.12.8. |
| Node.js | 20+ | Frontend only (Phase 5). |
| Git | any | |
| Neon account | free tier | Database. |
| Google AI Studio key | free tier | LLM. |

Check your Python launcher knows the interpreter:

```powershell
py --list
```

## 1. Get the code

```powershell
git clone https://github.com/Divys0mCS/DiffWitch.git
Set-Location DiffWitch
```

## 2. Backend virtual environment

```powershell
Set-Location backend
py -3.12 -m venv .venv
```

If PowerShell blocks activation scripts, allow it for this session only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

On Python 3.14 use `py -3.14 -m venv .venv` instead; the pinned versions all have
cp314 wheels.

## 3. Environment file

```powershell
Copy-Item .env.example .env
notepad .env
```

Fill in:

- `DATABASE_URL` — Neon **pooled** connection string (Project → Connection Details →
  *Pooled connection*). Keep `?sslmode=require`; the backend translates it for asyncpg.
- `FRONTEND_ORIGIN` — `http://localhost:3000` for local development.

The app starts without a database (it logs a warning and `/health` reports
`db: not_configured`) so you can boot it before Neon exists.

## 4. Run migrations

```powershell
alembic upgrade head
```

Useful commands:

```powershell
alembic current          # what revision is applied
alembic history          # migration list
alembic downgrade -1     # roll back one revision
```

## 5. Start the API

```powershell
uvicorn app.main:app --reload --port 8000
```

Then open:

- <http://127.0.0.1:8000/health> — liveness, includes DB status and latency
- <http://127.0.0.1:8000/health/ready> — 503 unless Postgres answers
- <http://127.0.0.1:8000/docs> — OpenAPI UI (development only)

## 6. Tests and lint

```powershell
pytest
ruff check .
ruff format --check .
```

Tests run against in-memory SQLite, so no database is required. The migration
smoke test applies the whole Alembic chain and rolls it back.

## Troubleshooting

**`asyncpg` build error on install** — you are on a Python version without a
matching wheel. Use `py -3.12` or `py -3.14`.

**`ModuleNotFoundError: app`** — run commands from the `backend` directory (the
one containing `app/`).

**`ssl` errors connecting to Neon** — make sure you copied the pooled string with
`?sslmode=require`, and that the password in the URL is percent-encoded if it
contains `@`, `:`, `/` or `#`.
