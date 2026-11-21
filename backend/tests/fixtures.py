"""Realistic unified diffs shared by the tests."""

from __future__ import annotations

TWO_FILE_DIFF = "\n".join(
    [
        "diff --git a/app/main.py b/app/main.py",
        "index 1111111..2222222 100644",
        "--- a/app/main.py",
        "+++ b/app/main.py",
        "@@ -1,5 +1,7 @@",
        " import os",
        "+import sys",
        " ",
        " def main():",
        '-    print("hi")',
        "+    sys.exit(0)",
        "+    print(os.getcwd())",
        "diff --git a/package-lock.json b/package-lock.json",
        "new file mode 100644",
        "index 0000000..3333333",
        "--- /dev/null",
        "+++ b/package-lock.json",
        "@@ -0,0 +1,3 @@",
        '+{"lockfileVersion": 3}',
        "+{}",
        "+{}",
        "diff --git a/assets/logo.png b/assets/logo.png",
        "new file mode 100644",
        "index 0000000..4444444",
        "Binary files /dev/null and b/assets/logo.png differ",
        "",
    ]
)

# app/main.py right-side anchors: 1 (ctx), 2 (add), 3 (ctx), 4 (ctx),
# 5 (add), 6 (add).  left-side anchors: 1, 2, 3, 4.
APP_MAIN_RIGHT = {1, 2, 3, 4, 5, 6}
APP_MAIN_LEFT = {1, 2, 3, 4}


async def seed_run(
    *,
    status: str = "completed",
    sha: str = "a" * 40,
    mode: str = "lite",
    number: int = 5,
    with_steps: bool = True,
    risk: str = "low",
):
    """Create (or reuse) an installation/repo/PR/run for API and worker tests."""
    from app import db
    from app.models import Installation, Job, PullRequest, Repository, ReviewRun, RunStep

    async with db.session_scope() as session:
        if await session.get(Installation, 1) is None:
            session.add(Installation(id=1, account_login="acme", account_type="Organization"))
            await session.flush()
        if await session.get(Repository, 10) is None:
            session.add(
                Repository(
                    id=10,
                    installation_id=1,
                    full_name="acme/app",
                    owner="acme",
                    name="app",
                    default_branch="main",
                )
            )
            await session.flush()

        pr = PullRequest(
            repository_id=10,
            number=number,
            title="Test PR",
            state="open",
            head_sha=sha,
            base_sha="b" * 40,
        )
        session.add(pr)
        await session.flush()

        run = ReviewRun(
            repository_id=10,
            pull_request_id=pr.id,
            pr_number=number,
            pr_title="Test PR",
            head_sha=sha,
            base_sha="b" * 40,
            mode=mode,
            status=status,
            risk=risk if status == "completed" else None,
            summary="Looks fine." if status == "completed" else None,
        )
        session.add(run)
        await session.flush()

        job = Job(run_id=run.id, status="done" if status == "completed" else "queued")
        session.add(job)
        await session.flush()

        if with_steps:
            session.add_all(
                [
                    RunStep(
                        run_id=run.id,
                        seq=1,
                        kind="note",
                        title="Filtered diff",
                        detail={"kept": ["app/main.py"]},
                    ),
                    RunStep(
                        run_id=run.id,
                        seq=2,
                        kind="llm_call",
                        title="Reviewing 1 file",
                        tokens=120,
                        duration_ms=800,
                    ),
                    RunStep(
                        run_id=run.id,
                        seq=3,
                        kind="verdict",
                        title="Verdict: low risk",
                        detail={"risk": "low"},
                    ),
                ]
            )
            await session.flush()

        return run.id, job.id


CLEAN_REVIEW = {
    "summary": "Adds sys.exit(0) and prints the working directory. Low risk.",
    "overall_risk": "low",
    "comments": [
        {
            "file": "app/main.py",
            "line": 5,
            "side": "RIGHT",
            "severity": "warning",
            "category": "bug",
            "message": "sys.exit(0) prevents the rest of main() from running.",
            "suggested_fix": "return 0",
        }
    ],
}
