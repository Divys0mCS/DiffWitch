# Deployment

Three free services, in this order: **Neon → Render → Vercel**, then point the
GitHub App at Render.

Everything below fits free tiers with no credit card.

---

## 1. Neon Postgres

1. <https://console.neon.tech> → create a project (region close to your Render
   region — `oregon` by default in `backend/render.yaml`).
2. **Connection Details** → **Pooled connection** → copy the string. It looks
   like:

   ```
   postgresql://user:pass@ep-cool-name-123456-pooler.us-west-2.aws.neon.tech/neondb?sslmode=require
   ```

   Use the **pooler** host (`-pooler`). DiffWitch disables asyncpg's prepared
   statement cache and translates `sslmode=require` to asyncpg's `ssl`
   argument, so the string works as-is.
3. That value is `DATABASE_URL`.

Schema is applied automatically: the Render start command runs
`alembic upgrade head` before `uvicorn`. To run it by hand against Neon from
your machine, set `DATABASE_URL` in `backend/.env` and run:

```powershell
Set-Location backend
.\.venv\Scripts\Activate.ps1
alembic upgrade head
```

Neon's free tier suspends the compute after a few minutes idle; the first query
after that takes ~1 s. The backend's pool pre-ping hides it.

---

## 2. Render (backend)

### Blueprint (recommended)

1. Push this repo to GitHub (already done).
2. <https://dashboard.render.com> → **New** → **Blueprint**.
3. Connect the repo, set **Blueprint Path** to `backend/render.yaml`.
4. Render reads the service (`diffwitch-api`, free plan, `healthCheckPath:
   /health`, `PYTHON_VERSION=3.12.8`) and asks for every `sync: false` secret.

### Manual service instead

| Setting | Value |
| --- | --- |
| Type | Web Service |
| Runtime | Python 3 |
| Plan | Free |
| Root Directory | `backend` |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Health Check Path | `/health` |

> Python: Render's default is fine, but pin `PYTHON_VERSION=3.12.8` so the wheels
> in `requirements.txt` are guaranteed. Do **not** use `uvicorn[standard]` here —
> plain `uvicorn` avoids `uvloop`/`httptools` build issues.

### Environment variables

| Key | Value | Notes |
| --- | --- | --- |
| `PYTHON_VERSION` | `3.12.8` | Pin; local dev can be 3.14. |
| `ENVIRONMENT` | `production` | |
| `DATABASE_URL` | Neon pooled string | Secret. |
| `FRONTEND_ORIGIN` | `https://your-app.vercel.app` | CORS. No trailing slash. Comma-separate for preview URLs. |
| `DASHBOARD_PASSWORD` | your choice | Secret. Dashboard + `/mcp` bearer. |
| `SESSION_SECRET` | random 48+ chars | Secret. Generate below. |
| `LLM_PROVIDER` | `gemini` | |
| `LLM_MODEL` | exact id from AI Studio | Never hardcoded in the app. |
| `GEMINI_API_KEY` | from AI Studio | Secret. |
| `LLM_RPM` | e.g. `5` | From your AI Studio rate-limit page. |
| `LLM_RPD` | e.g. `20` | Same. |
| `DAILY_LLM_BUDGET` | e.g. `18` | Hard stop per UTC day. |
| `REVIEW_MODE` | `lite` | `agent` costs more calls. |
| `MAX_AGENT_STEPS` | `3` | Total LLM calls in AGENT mode. |
| `GITHUB_APP_ID` | number | See [GITHUB_APP.md](GITHUB_APP.md). |
| `GITHUB_PRIVATE_KEY_B64` | base64 `.pem` | Secret, one line. |
| `GITHUB_WEBHOOK_SECRET` | your choice | Secret; must match the app. |
| `WORKER_ENABLED` | `true` | Only one instance may run the worker — the free plan gives you one. |

Generate `SESSION_SECRET` in PowerShell:

```powershell
-join ((48..57) + (65..90) + (97..122) | Get-Random -Count 56 | ForEach-Object {[char]$_})
```

or with Python:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(56))"
```

### Verify

```powershell
Invoke-RestMethod https://YOUR-SERVICE.onrender.com/health
Invoke-RestMethod https://YOUR-SERVICE.onrender.com/health/ready
```

`/health` → `{"status":"ok",...}`. `/health/ready` also checks the database and
returns `503` if Neon is unreachable or migrations have not run.

First request after 15 idle minutes takes up to a minute — that is Render's free
plan waking, not a bug.

---

## 3. Vercel (frontend)

1. <https://vercel.com/new> → import the same repository.
2. **Root Directory**: `frontend`. Framework preset: Next.js (detected).
3. Environment variable:

   | Key | Value |
   | --- | --- |
   | `NEXT_PUBLIC_API_URL` | `https://YOUR-SERVICE.onrender.com` |

   No trailing slash. This is baked into the client bundle at build time, so set
   it **before** deploying and redeploy after changing it.
4. Deploy.
5. Copy the production URL (`https://your-app.vercel.app`) into Render's
   `FRONTEND_ORIGIN`, and redeploy the backend.

CORS only allows `FRONTEND_ORIGIN`. Vercel preview deployments get different
hostnames — add them comma-separated if you want to use previews:

```
FRONTEND_ORIGIN=https://diffwitch.vercel.app,https://diffwitch-git-main-you.vercel.app
```

The browser talks to Render directly for SSE (`GET /api/runs/:id/events`) so
streaming is not buffered by Vercel's serverless layer.

---

## 4. Keep it awake (free tier etiquette)

Create a free monitor at <https://cron-job.org> (or UptimeRobot):

- URL: `https://YOUR-SERVICE.onrender.com/health`
- Interval: every 10 minutes
- Method: GET, expect `200`

This stays under Render's 750 instance hours/month for a single service and
keeps cold starts out of the webhook path.

---

## 5. Post-deploy checklist

- [ ] `GET /health` → `200`
- [ ] `GET /health/ready` → `200` (database reachable, migrations applied)
- [ ] `GET /api/runs` without a token → `401`
- [ ] Log in at `https://your-app.vercel.app/login` with `DASHBOARD_PASSWORD`
- [ ] GitHub App webhook URL points at `/webhooks/github`; **Recent Deliveries**
      shows `200`
- [ ] Open a PR on the test repo → review appears, run appears in the dashboard
- [ ] Usage page shows the call counted against today's budget
- [ ] Push a second commit to the same PR → a new run (new SHA)
- [ ] Re-deliver the same webhook → no duplicate run

---

## 6. Costs, honestly

| Service | Free allowance | What happens at the limit |
| --- | --- | --- |
| Render | 750 h/month, sleeps after 15 min | Service suspends until next month. |
| Neon | 0.5 GB storage, autosuspend | Writes fail; `/health/ready` reports it. |
| Gemini | ~5 RPM / ~20 RPD (varies by model) | Runs are `skipped` with a reason; nothing is charged. |
| Vercel Hobby | 100 GB bandwidth | Personal, non-commercial use only. |

---

## 7. Rolling back

Render keeps previous deploys: **Deploys** → pick one → **Rollback**. Database
migrations are reversible (`alembic downgrade -1`), but downgrading drops the
tables the older code does not know about — only do it if you mean it.
