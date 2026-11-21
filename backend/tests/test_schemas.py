from __future__ import annotations

import json

import pytest
from app.schemas import (
    REVIEW_RESPONSE_SCHEMA,
    Category,
    CommentSide,
    ReviewComment,
    ReviewResult,
    RiskLevel,
    Severity,
    parse_review_payload,
)
from tests.fixtures import CLEAN_REVIEW


def test_parse_valid_payload():
    result = parse_review_payload(CLEAN_REVIEW)
    assert isinstance(result, ReviewResult)
    assert result.overall_risk is RiskLevel.LOW
    comment = result.comments[0]
    assert comment.severity is Severity.WARNING
    assert comment.category is Category.BUG
    assert comment.side is CommentSide.RIGHT


def test_parse_from_json_string_and_fences():
    payload = "```json\n" + json.dumps(CLEAN_REVIEW) + "\n```"
    result = parse_review_payload(payload)
    assert result.comments[0].file == "app/main.py"


def test_lenient_enum_coercion():
    payload = {
        "summary": "x",
        "overall_risk": "HIGH",
        "comments": [
            {
                "file": "b/app/x.py",
                "line": "L42",
                "side": "left",
                "severity": "blocker",
                "category": "Vulnerability",
                "message": "bad",
            }
        ],
    }
    result = parse_review_payload(payload)
    comment = result.comments[0]
    assert result.overall_risk is RiskLevel.HIGH
    assert comment.file == "app/x.py"
    assert comment.line == 42
    assert comment.side is CommentSide.LEFT
    assert comment.severity is Severity.CRITICAL
    assert comment.category is Category.SECURITY


def test_unknown_enums_fall_back():
    result = parse_review_payload(
        {
            "summary": "s",
            "overall_risk": "banana",
            "comments": [
                {
                    "file": "a.py",
                    "line": 1,
                    "message": "m",
                    "severity": "huh",
                    "category": "weird",
                }
            ],
        }
    )
    assert result.overall_risk is RiskLevel.LOW
    assert result.comments[0].severity is Severity.INFO
    assert result.comments[0].category is Category.BUG


def test_missing_fields_have_defaults():
    result = parse_review_payload({"summary": "only a summary"})
    assert result.comments == []
    assert result.overall_risk is RiskLevel.LOW


def test_comments_dict_is_wrapped():
    result = parse_review_payload(
        {"summary": "s", "comments": {"file": "a.py", "line": 3, "message": "m"}}
    )
    assert len(result.comments) == 1


def test_blank_message_becomes_placeholder():
    result = parse_review_payload(
        {"summary": "s", "comments": [{"file": "a.py", "line": 1, "message": "   "}]}
    )
    assert result.comments[0].message


def test_deduplicates_comments():
    payload = {
        "summary": "s",
        "comments": [
            {"file": "a.py", "line": 1, "message": "same", "side": "RIGHT"},
            {"file": "a.py", "line": 1, "message": "same", "side": "RIGHT"},
            {"file": "a.py", "line": 1, "message": "different", "side": "RIGHT"},
        ],
    }
    result = parse_review_payload(payload)
    assert len(result.comments) == 2


def test_clamp_keeps_only_anchored_comments():
    result = parse_review_payload(
        {
            "summary": "s",
            "comments": [
                {"file": "a.py", "line": 5, "message": "ok", "side": "RIGHT"},
                {"file": "a.py", "line": 99, "message": "nope", "side": "RIGHT"},
                {"file": "other.py", "line": 1, "message": "unknown file", "side": "RIGHT"},
                {"file": "a.py", "line": 3, "message": "left ok", "side": "LEFT"},
            ],
        }
    )
    clamped, dropped = result.clamp_to_diff({"a.py": {5}}, {"a.py": {3}}, max_comments=10)
    assert [(c.file, c.line) for c in clamped.comments] == [("a.py", 5), ("a.py", 3)]
    assert len(dropped) == 2


def test_clamp_enforces_max_comments():
    comments = [
        {"file": "a.py", "line": number, "message": f"m{number}", "side": "RIGHT"}
        for number in range(1, 8)
    ]
    result = parse_review_payload({"summary": "s", "comments": comments})
    clamped, dropped = result.clamp_to_diff({"a.py": set(range(1, 8))}, {}, max_comments=3)
    assert len(clamped.comments) == 3
    assert len(dropped) == 4


def test_truncates_absurd_message():
    comment = ReviewComment(file="a.py", line=1, message="x" * 5000)
    assert len(comment.message) <= 2000


def test_control_characters_removed():
    comment = ReviewComment(file="a.py", line=1, message="bad\x00message\x1b[31m")
    assert "\x00" not in comment.message
    assert "\x1b" not in comment.message


def test_response_schema_shape():
    assert REVIEW_RESPONSE_SCHEMA["required"] == ["summary", "overall_risk", "comments"]
    assert REVIEW_RESPONSE_SCHEMA["properties"]["overall_risk"]["enum"] == [
        "low",
        "medium",
        "high",
    ]


def test_invalid_payload_type_raises():
    with pytest.raises(ValueError):
        parse_review_payload(["not", "an", "object"])
