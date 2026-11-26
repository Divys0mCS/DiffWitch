# Publishing the CLI to PyPI

The CLI is published as [`diffwitch`](https://pypi.org/project/diffwitch/).
The backend package is called `app`, which already exists on PyPI, so
`scripts/build_wheel.py` generates a `diffwitch` package from `backend/app` and
rewrites only the import root. `backend/` is never modified, so the Render
start command (`uvicorn app.main:app`) keeps working.

Artifacts are written to `dist/pypi/`; `build/` and `dist/` are git-ignored.

## One-time setup (PyPI trusted publishing, no token)

1. Sign in at <https://pypi.org> → **Your projects** → **Publishing** →
   **Add a pending publisher**.
2. Fill in exactly:

| Field | Value |
| --- | --- |
| PyPI project name | `diffwitch` |
| Owner | `Divys0mCS` |
| Repository name | `DiffWitch` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

3. In GitHub: **Settings → Environments → New environment** → `pypi`. No
   secrets are needed — the workflow exchanges a GitHub OIDC token for a
   short-lived PyPI credential.

## Releasing

1. Bump `version` in `pypi/pyproject.toml` (PyPI rejects a version that already
   exists; the workflow refuses a tag that does not match).
2. Commit, then tag and push:

```powershell
git tag v0.1.0
git push origin v0.1.0
```

The `publish-cli` workflow installs the backend dev requirements, runs
`ruff check` and `ruff format --check`, runs the test suite, then builds the
wheel and uploads it — a lint or test failure stops the upload. `workflow_dispatch`
runs the same job by hand.

## Building locally first

```powershell
py -3.12 -m venv build\\venv
.\\build\\venv\\Scripts\\Activate.ps1
pip install build
python scripts/build_wheel.py
pip install dist\\pypi\\diffwitch-0.1.0-py3-none-any.whl
diffwitch --diff backend\\samples\\sample.diff --provider fake
```

`--provider fake` needs no API key and exercises the full pipeline.
