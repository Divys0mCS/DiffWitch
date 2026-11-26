# Keeping the Render backend awake

Render's free web services spin down after about 15 minutes without traffic, and
the next request pays a 30–60 s cold start. GitHub's webhook timeout is 10 s, so
a cold instance means the review never starts.

[`.github/workflows/keepalive.yml`](../.github/workflows/keepalive.yml) runs
`GET https://diffwitch-api.onrender.com/health` every 10 minutes from GitHub
Actions, which is free for public repositories.

## What it does

- `schedule: "*/10 * * * *"` — GitHub's cron is best-effort; expect runs to
  start a few minutes late under load. Still well inside the 15-minute window.
- `--max-time 120 --retry 2 --retry-all-errors` — the first ping after a long
  gap can take a minute; a single timeout does not fail the job.
- The job **fails** on any status other than 200, so a broken deployment shows
  up as a red run instead of passing silently.
- `workflow_dispatch` lets you run it by hand: **Actions → keepalive → Run
  workflow**. Useful about 5 minutes before a demo.

No secrets are needed, and the workflow has no repository permissions.

## Cost: the free hours maths

One always-on free service uses roughly 730 of Render's 750 free instance hours
per month. That fits, but only just — do not add a second always-on service on
the same account. To leave headroom, narrow the schedule to waking hours only,
for example:

```yaml
- cron: "*/10 3-18 * * *" # every 10 min, 03:00–18:59 UTC
```

## Things that stop it

| Problem | Fix |
| --- | --- |
| GitHub disables scheduled workflows after 60 days without repository activity | Re-enable it in **Actions**, or push any commit |
| The workflow only runs from the **default branch** (`main`) | Merge the change to `main` |
| Actions minutes exhausted / billing blocked | Use a free external pinger instead (UptimeRobot, cron-job.org) pointing at the same `/health` URL |
| Render service renamed or moved | Update `HEALTH_URL` in the workflow |

## Verifying it is working

Open **Actions → keepalive** and check that recent runs are green and print
`HTTP 200` with the JSON body (`"db":"up"`). To check the endpoint yourself:

```powershell
curl.exe -s -w "`nHTTP %{http_code} in %{time_total}s`n" https://diffwitch-api.onrender.com/health
```

A warm instance answers in about 1–3 seconds; 30 seconds or more means it was
asleep and the ping is doing its job.
