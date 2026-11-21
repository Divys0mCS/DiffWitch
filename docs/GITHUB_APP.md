# Create the GitHub App

You need this before Phase 4 can post a review. Everything here is on the free
plan.

## 1. Create the app

1. Open <https://github.com/settings/apps> → **New GitHub App**.
2. Fill in:

| Field | Value |
| --- | --- |
| GitHub App name | `DiffWitch` (must be globally unique — add your username if taken) |
| Homepage URL | your Vercel URL, or `https://github.com/Divys0mCS/DiffWitch` for now |
| Webhook URL | `https://<your-render-service>.onrender.com/webhooks/github` |
| Webhook secret | a long random string — this becomes `GITHUB_WEBHOOK_SECRET` |
| Callback URL | leave empty (not needed) |
| Expire user authorization tokens | leave default |
| Request user authorization (OAuth) during installation | **off** |
| Webhook → Active | **on** |

3. **Repository permissions** — set exactly these and no more:

| Permission | Access |
| --- | --- |
| Metadata | Read-only (mandatory) |
| Contents | Read-only |
| Pull requests | Read & write |

4. **Subscribe to events**: check **Pull request** only.
5. **Where can this GitHub App be installed?** — "Only on this account" is fine.
6. Click **Create GitHub App**.

## 2. Collect the three values

On the app's settings page:

1. **App ID** — the number at the top. → `GITHUB_APP_ID`
2. **Private keys** → *Generate a private key*. A `.pem` file downloads once.
   Base64-encode it (PowerShell, no bash):

   ```powershell
   [Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\Users\you\Downloads\diffwitch.2026-11-21.private-key.pem"))
   ```

   Copy the single-line output. → `GITHUB_PRIVATE_KEY_B64`
   (Delete the `.pem` from Downloads afterwards, or keep it somewhere safe and
   git-ignored. Never commit it.)

   To write it to a file instead of the clipboard:

   ```powershell
   [Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\path\key.pem")) | Set-Content -NoNewline .\key.b64
   ```

3. **Webhook secret** — the string you typed. → `GITHUB_WEBHOOK_SECRET`

## 3. Install it on a test repository

1. App settings → **Install App** → choose your account → **Install**.
2. Pick **Only select repositories** and choose a test repo you can open PRs on.
3. Copy the installation id from the URL
   (`https://github.com/settings/installations/12345678` → `12345678`). DiffWitch
   reads it from webhooks, so you do not need to store it, but it is useful when
   debugging.

## 4. Put the values on the backend

Locally, in `backend/.env`:

```dotenv
GITHUB_APP_ID=123456
GITHUB_PRIVATE_KEY_B64=LS0tLS1CRUdJTi...
GITHUB_WEBHOOK_SECRET=the-same-string-you-typed
```

On Render: Dashboard → your service → **Environment** → add the same three keys.
Changing environment variables restarts the free instance, which is expected.

## 5. Test it

1. Open a pull request on the test repo (or push a commit to an open PR).
2. GitHub App → **Advanced** → **Recent Deliveries**: the delivery should be
   `200`.
3. The dashboard's Runs page should show the run within a few seconds.
4. The PR should receive one review from the app with inline comments.

### If the delivery fails

| Symptom | Cause |
| --- | --- |
| `401 Bad signature` | `GITHUB_WEBHOOK_SECRET` does not match the app's webhook secret. |
| `503 Webhook secret is not configured` | The env var is missing on Render; add it and redeploy. |
| `404` | The Render URL is wrong, or you appended `/` to the webhook URL. |
| Delivery `200` but no review | Check the run in the dashboard: it may be `skipped: quota` or `failed` with the API's message. |

## 6. Redelivering a missed webhook

The free Render instance sleeps. If GitHub delivered while it was asleep, Render
accepts the connection and wakes, but very slow cold starts can time out.

GitHub App settings → **Advanced** → **Recent Deliveries** → pick the delivery →
**Redeliver**. DiffWitch deduplicates by delivery id, so redelivering a delivery
it already processed is a no-op.

Keeping an uptime pinger on `/health` (see [DEPLOYMENT.md](DEPLOYMENT.md)) makes
this rare.
