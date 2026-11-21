# Local setup

Everything below is PowerShell (Windows). No `export`, no `&&`, no `rm -rf`.

## 0. Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| Python | 3.14.x or 3.12.x | Every pinned dependency ships a cp314 wheel, so 3.14 works. Render uses 3.12.8. |
| Node.js | 20+ | Dashboard. |
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
- `DASHBOARD_PASSWORD` — any password you choose; this is what you type on the
  login page and what the `/mcp` endpoint expects as its bearer token.
- `SESSION_SECRET` — the key used to sign dashboard sessions. Generate one:

  ```powershell
  python -c "import secrets; print(secrets.token_urlsafe(56))"
  ```

  Leave it empty in development and the app signs with `DASHBOARD_PASSWORD`
  instead (fine locally, never in production).

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

## 7. LLM provider (Phase 2)

The LLM is behind `LLMProvider`, so the pipeline never imports a vendor SDK.
Gemini is the first implementation.

In `.env`:

```dotenv
LLM_PROVIDER=gemini
LLM_MODEL=<the exact model id from AI Studio>
GEMINI_API_KEY=<your key>
LLM_RPM=5
LLM_RPD=20
DAILY_LLM_BUDGET=18
```

- `LLM_MODEL` is never hardcoded. If the model id is wrong you get
  `Model '...' was not found for this API key.` and nothing else breaks.
- `DAILY_LLM_BUDGET` is the number of LLM calls allowed per UTC day. It is
  enforced in Postgres and is deliberately lower than `LLM_RPD` so a retry storm
  cannot eat the whole allowance. `0` disables the cap.
- `LLM_RPM` is a local sliding-window limiter. When the window is full the worker
  waits instead of failing, and only gives up if the wait would exceed ~90 s.

### Try the pipeline without a key (or without GitHub)

```powershell
python -m app.cli.review_diff --diff samples\sample.diff --repo owner\repo --pr 7 --provider fake
```

With a real key:

```powershell
python -m app.cli.review_diff --diff samples\sample.diff --repo owner\repo --pr 7 --out review.json
```

The CLI prints every step (filtering, the LLM call, token usage, the verdict) and
prints the validated JSON review. Exit codes: `0` ok, `1` error, `2` skipped
(quota or nothing reviewable).

### What gets filtered before the LLM sees it

Lockfiles, minified files, source maps, images/binaries, vendored folders and
empty diffs are dropped and listed in the prompt's `FILTERED OUT` note. Large
diffs are truncated **by whole hunk** (never mid-hunk), so every line number the
model is shown really exists in the diff. Total prompt size is capped by
`MAX_PROMPT_CHARS` and per-file size by `MAX_FILE_DIFF_CHARS`.

### Prompt-injection defence

PR titles, code and comments are wrapped in
`<<<UNTRUSTED_PR_DATA_START>>>` / `<<<UNTRUSTED_PR_DATA_END>>>` blocks, the
delimiters are stripped from the content first, and the system prompt states that
nothing inside the block is an instruction.

## 8. Run the dashboard (frontend)

```powershell
Set-Location ..\frontend
npm install
Copy-Item .env.example .env.local
notepad .env.local
```

Set the API URL (no trailing slash):

```dotenv
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
```

```powershell
npm run dev
```

Open <http://localhost:3000>, sign in with `DASHBOARD_PASSWORD`, and the runs
ledger appears. `npm run build` is the same check CI-style: it type-checks and
builds every route.

Production build locally:

```powershell
npm run build
npm run start
```

## 9. Worker, MCP endpoint and events

- The worker starts with the app (`WORKER_ENABLED=true`) — no separate process,
  no extra Render service. On boot it requeues jobs that were stuck in `running`
  when the previous instance died.
- Read-only MCP server: `http://127.0.0.1:8000/mcp/` with
  `Authorization: Bearer <DASHBOARD_PASSWORD>`.
- Queue inspection (SQL, Neon SQL editor):

  ```sql
  SELECT status, count(*) FROM jobs GROUP BY status;
  SELECT id, status, skip_reason, error FROM review_runs ORDER BY created_at DESC LIMIT 10;
  ```

## Troubleshooting

**`asyncpg` build error on install** — you are on a Python version without a
matching wheel. Use `py -3.12` or `py -3.14`.

**`ModuleNotFoundError: app`** — run commands from the `backend` directory (the
one containing `app/`).

**`ssl` errors connecting to Neon** — make sure you copied the pooled string with
`?sslmode=require`, and that the password in the URL is percent-encoded if it
contains `@`, `:`, `/` or `#`.

**Dashboard says "waking up the server…" forever** — the browser cannot reach
`NEXT_PUBLIC_API_URL`. It must be reachable from the browser, not just from the
shell: `127.0.0.1:8000` works only if the backend runs on the same machine, and a
changed value needs `npm run build` (it is baked in at build time).

**Login returns 401** — the password does not match `DASHBOARD_PASSWORD`. If the
backend has no password set it returns `503` instead.

**SSE connects then immediately falls back to polling** — a proxy is buffering
the stream. On Render this does not happen; behind a local proxy, disable
buffering for `/api/runs/*/events`.
