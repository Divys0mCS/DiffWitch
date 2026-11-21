# Architecture

DiffWitch is one FastAPI process on Render, a Next.js app on Vercel, and Neon
Postgres. There is no Redis, no worker dyno, no second service, and no disk —
everything the free tier does not give us is replaced by Postgres or by
in-process state that is safe to lose.

```mermaid
flowchart TB
    subgraph GitHub
        PR[Pull request<br/>opened / reopened / synchronize]
        APP[GitHub App<br/>PR: rw · Contents: r · Metadata: r]
        API[(GitHub REST API)]
    end

    subgraph Render["Render free web service (one process)"]
        WH[POST /webhooks/github<br/>HMAC verify · delivery id · enqueue]
        DBAPI[Dashboard API<br/>/api/runs · /api/usage · /api/settings]
        SSE[GET /api/runs/:id/events<br/>SSE + heartbeat]
        MCP[/mcp read-only MCP<br/>bearer-protected/]
        WORKER{{Worker loop<br/>FOR UPDATE SKIP LOCKED}}
        AGENT[Review agent]
        TOOLS[MCP tools in-process<br/>get_pr_metadata · get_diff<br/>list_changed_files · read_file · post_review]
        LLM[LLMProvider<br/>Gemini REST]
    end

    subgraph Neon["Neon Postgres (free)"]
        JOBS[(jobs)]
        RUNS[(review_runs)]
        STEPS[(run_steps)]
        USAGE[(usage_daily)]
        SETTINGS[(settings)]
        DELIV[(webhook_deliveries)]
    end

    subgraph Vercel["Vercel Hobby"]
        UI[Next.js dashboard<br/>runs · timeline · quota · settings]
    end

    PR -->|webhook| WH
    WH -->|idempotency| DELIV
    WH -->|insert run + job| JOBS
    WH -->|200 in <100 ms| GitHub
    JOBS --> WORKER
    WORKER -->|JWT → installation token| APP
    APP --> API
    WORKER -->|diff + file reads| API
    WORKER --> AGENT
    AGENT --> TOOLS
    AGENT -->|1 call (LITE) or ≤3 (AGENT)| LLM
    AGENT -->|steps| STEPS
    AGENT -->|review| API
    WORKER --> RUNS
    WORKER -->|budget reservation| USAGE
    STEPS --> SSE
    RUNS --> SSE
    UI -->|bearer token| DBAPI
    UI -->|fetch SSE, direct to Render| SSE
    UI -.->|fallback: poll every 2s| DBAPI
    SETTINGS --> DBAPI
```

## Request paths

### 1. Webhook (must be fast)

```
GitHub → POST /webhooks/github
  1. read raw body
  2. HMAC-SHA256(secret, body) compared in constant time with X-Hub-Signature-256
  3. INSERT ... ON CONFLICT DO NOTHING into webhook_deliveries (delivery id)
  4. upsert installation / repository / pull_request
  5. if a completed run exists for (repo, PR, head SHA) → record a "skipped: cached" run
     else INSERT review_runs + jobs
  6. return 200
```

No GitHub call and no LLM call happens here. GitHub allows 10 seconds; this
takes one round trip to Neon.

### 2. Worker

The lifespan starts exactly one worker task:

```
loop:
  claim = UPDATE jobs SET status='running', attempts=attempts+1
          WHERE id = (SELECT id FROM jobs WHERE status='queued' AND run_after <= now()
                      ORDER BY run_after LIMIT 1 FOR UPDATE SKIP LOCKED)
          RETURNING ...
  if none: sleep 2s
  else: process(claim)
```

On boot, jobs stuck in `running` for longer than `JOB_STUCK_AFTER_SECONDS`
(default 900s) are requeued — this is how a Render restart mid-review recovers.

### 3. Review

```
authenticate as the App (RS256 JWT, 9-minute expiry)
  → POST /app/installations/:id/access_tokens (cached until 5 min before expiry)
  → GET /repos/:o/:r/pulls/:n          (metadata)
  → GET /repos/:o/:r/pulls/:n  (Accept: application/vnd.github.v3.diff)
  → filter + truncate the diff
  → reserve one call in usage_daily (atomic conditional upsert)
  → LITE: one generateContent call with responseSchema
    AGENT: ≤ MAX_AGENT_STEPS calls, each able to call an MCP tool
  → validate JSON with Pydantic, clamp comments to real diff lines
  → POST /repos/:o/:r/pulls/:n/reviews (one review, inline comments)
```

Every stage writes a `run_steps` row and publishes it on the event bus.

## Why these choices

| Problem | Decision |
| --- | --- |
| Render sleeps, 750 free hours | Exactly one service; the MCP server is mounted in the same process. |
| No Redis | `jobs` table with `SKIP LOCKED`; the worker is a lifespan task. |
| Ephemeral disk | Nothing is written to disk. Neon is the only store. |
| Neon pooled endpoint | `statement_cache_size=0`, `sslmode` translated to asyncpg `ssl`, small pool with pre-ping. |
| Gemini ~5 RPM / ~20 RPD | LITE is one call per PR; a Postgres budget stops before overspending; RPM waits instead of failing. |
| Flaky 429/503 | Exponential backoff with jitter, honouring `Retry-After` / `retryDelay`. |
| Instance restart mid-run | Stuck `running` jobs are requeued on boot. |
| Duplicate webhooks | Delivery-id ledger with `ON CONFLICT DO NOTHING`. |
| Re-reviewing a SHA | Cache by `(repo, PR, head_sha)`; only the dashboard's "Re-review" bypasses it. |

## Security model

- **Webhook**: constant-time HMAC, 503 when the secret is unset, 401 on mismatch.
- **Dashboard**: `DASHBOARD_PASSWORD` → HMAC-signed expiring session token
  (`SESSION_SECRET`), accepted as a bearer token or cookie. Every `/api/*` route
  depends on it, so there is no open dashboard API. Login is rate-limited.
- **MCP endpoint**: same password, mandatory bearer, read-only tools.
- **GitHub**: least privilege (Pull requests read & write, Contents read, Metadata
  read). Installation tokens are minted on demand and cached in memory only.
- **Prompt injection**: PR text is wrapped in `<<<UNTRUSTED_PR_DATA>>>` blocks,
  the delimiters are stripped from the content first, and the system prompt says
  nothing inside is an instruction. Model output is untrusted JSON and is
  validated, truncated and clamped before it reaches GitHub.
- **Secrets**: only in env vars. `.env*` and `*.pem` are git-ignored, and logs
  redact tokens, keys and signatures.

## Data model

| Table | Purpose |
| --- | --- |
| `installations` | GitHub App installations (id = GitHub's installation id). |
| `repositories` | Repos the app can see (id = GitHub's repo id). |
| `pull_requests` | Last known state of each PR. |
| `review_runs` | One row per review attempt: status, risk, summary, tokens, review URL. |
| `run_steps` | Ordered reasoning trace; the dashboard timeline. |
| `jobs` | The queue. |
| `usage_daily` | Atomic daily LLM budget counter. |
| `settings` | Dashboard overrides (mode, caps, filters). |
| `webhook_deliveries` | Delivery-id idempotency ledger. |

## Failure behaviour

| Failure | What the user sees |
| --- | --- |
| Gemini quota/429 exhausted | Run `skipped`, reason `quota: provider`, visible in the timeline. |
| Local budget reached | Run `skipped` before any call: `quota: daily_budget`. |
| Model id retired | Run `failed` with "copy the current id from AI Studio into LLM_MODEL". |
| GitHub 5xx | Job requeued with backoff, then `failed` with the API message. |
| Bad inline payload (422) | Falls back to a summary-only review; the findings are still posted. |
| Render restarts mid-run | Job requeued on boot; the run continues from a fresh step sequence. |
| Backend asleep | Dashboard shows "waking up the server" and retries automatically. |
