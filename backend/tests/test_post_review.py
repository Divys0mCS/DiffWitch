from __future__ import annotations

from typing import Any

from app.schemas import parse_review_payload
from app.services.github import GitHubError
from app.services.post_review import post_review, render_comment_body, render_summary_body

REVIEW = {
    "summary": "Adds an exit call.",
    "overall_risk": "medium",
    "comments": [
        {
            "file": "app/main.py",
            "line": 5,
            "side": "RIGHT",
            "severity": "critical",
            "category": "bug",
            "message": "This skips the rest of main().",
            "suggested_fix": "return 0",
        }
    ],
}


class FakeClient:
    def __init__(self, *, existing=None, fail_inline=False):
        self.existing = existing or []
        self.fail_inline = fail_inline
        self.reviews: list[dict[str, Any]] = []

    async def list_review_comments(self, owner, repo, number):
        return self.existing

    async def create_review(self, owner, repo, number, *, body, comments, commit_id=None):
        if self.fail_inline and comments:
            raise GitHubError("Unprocessable", status_code=422)
        self.reviews.append({"body": body, "comments": comments, "commit_id": commit_id})
        return {"id": 99, "html_url": "https://github.com/acme/app/pull/5#review-99"}


def test_render_comment_body_has_severity_and_fix():
    result = parse_review_payload(REVIEW)
    body = render_comment_body(result.comments[0])
    assert "CRITICAL" in body
    assert "bug" in body
    assert "return 0" in body


def test_render_summary_body_mentions_risk_and_mode():
    result = parse_review_payload(REVIEW)
    body = render_summary_body(result, mode="lite")
    assert "MEDIUM" in body
    assert "LITE mode" in body
    assert "1 inline comment" in body


async def test_posts_a_single_review_with_inline_comments():
    client = FakeClient()
    result = parse_review_payload(REVIEW)
    posted = await post_review(
        client,
        owner="acme",
        repo="app",
        number=5,
        head_sha="a" * 40,
        result=result,
        mode="lite",
    )
    assert posted.ok is True
    assert posted.comments_posted == 1
    assert posted.review_url.endswith("#review-99")
    assert len(client.reviews) == 1
    comment = client.reviews[0]["comments"][0]
    assert comment == {
        "path": "app/main.py",
        "line": 5,
        "side": "RIGHT",
        "body": comment["body"],
    }
    assert client.reviews[0]["commit_id"] == "a" * 40


async def test_skips_duplicate_comments_already_on_the_pr():
    result = parse_review_payload(REVIEW)
    existing = [
        {
            "path": "app/main.py",
            "line": 5,
            "body": render_comment_body(result.comments[0]),
        }
    ]
    client = FakeClient(existing=existing)
    posted = await post_review(
        client, owner="acme", repo="app", number=5, head_sha="a" * 40, result=result, mode="lite"
    )
    assert posted.comments_posted == 0
    assert client.reviews[0]["comments"] == []


async def test_unanchored_comments_are_not_sent():
    client = FakeClient()
    result = parse_review_payload(REVIEW)
    posted = await post_review(
        client,
        owner="acme",
        repo="app",
        number=5,
        head_sha="a" * 40,
        result=result,
        mode="lite",
        right_anchors={"app/main.py": {1, 2}},
        left_anchors={},
    )
    assert posted.comments_posted == 0


async def test_422_falls_back_to_summary_review():
    client = FakeClient(fail_inline=True)
    result = parse_review_payload(REVIEW)
    posted = await post_review(
        client, owner="acme", repo="app", number=5, head_sha="a" * 40, result=result, mode="agent"
    )
    assert posted.ok is True
    assert posted.degraded_to_summary is True
    assert len(client.reviews) == 1
    assert client.reviews[0]["comments"] == []
    assert "app/main.py:5" in client.reviews[0]["body"]


async def test_non_422_error_is_reported():
    class Broken(FakeClient):
        async def create_review(self, *args, **kwargs):
            raise GitHubError("server exploded", status_code=500)

    posted = await post_review(
        Broken(),
        owner="acme",
        repo="app",
        number=5,
        head_sha="a" * 40,
        result=parse_review_payload(REVIEW),
        mode="lite",
    )
    assert posted.ok is False
    assert "exploded" in (posted.error or "")
