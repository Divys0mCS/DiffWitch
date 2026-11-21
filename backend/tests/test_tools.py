from __future__ import annotations

import json

from app.config import Settings
from app.mcp.context import PRMetadata, StaticFileReader, build_context
from app.mcp.tools import TOOL_NAMES, RunToolbox
from tests.fixtures import CLEAN_REVIEW, TWO_FILE_DIFF


def _settings(**overrides) -> Settings:
    values = {"_env_file": None, "max_file_diff_chars": 20_000, "max_prompt_chars": 60_000}
    values.update(overrides)
    return Settings(**values)


def _context(reader=None, diff: str = TWO_FILE_DIFF):
    metadata = PRMetadata(
        repo="Divys0mCS/DiffWitch",
        number=12,
        title="Add sys.exit",
        author="dev",
        head_sha="a" * 40,
        base_sha="b" * 40,
        base_ref="main",
        additions=3,
        deletions=1,
        changed_files=3,
    )
    return build_context(metadata, diff, settings=_settings(), reader=reader)


def test_catalog_lists_the_five_required_tools():
    toolbox = RunToolbox(_context())
    names = [item["name"] for item in toolbox.catalog()]
    assert names == list(TOOL_NAMES)
    assert set(names) == {
        "get_pr_metadata",
        "get_diff",
        "list_changed_files",
        "read_file",
        "post_review",
    }


async def test_get_pr_metadata_returns_json():
    toolbox = RunToolbox(_context())
    text, ok = await toolbox.call("get_pr_metadata")
    assert ok is True
    payload = json.loads(text)
    assert payload["repo"] == "Divys0mCS/DiffWitch"
    assert payload["number"] == 12
    assert payload["head_sha"] == "a" * 40


async def test_get_diff_whole_and_per_file():
    toolbox = RunToolbox(_context())
    whole, _ = await toolbox.call("get_diff")
    assert "app/main.py" in whole
    # Lockfiles only appear in the "filtered out" note, never as reviewable hunks.
    assert "FILTERED OUT" in whole
    assert "+ new" not in whole.split("FILTERED OUT")[1]

    single, ok = await toolbox.call("get_diff", {"path": "app/main.py"})
    assert ok is True
    assert "+ new 5 |     sys.exit(0)" in single


async def test_get_diff_unknown_path_explains():
    toolbox = RunToolbox(_context())
    text, _ = await toolbox.call("get_diff", {"path": "nope.py"})
    assert "No reviewable diff" in text
    assert "app/main.py" in text


async def test_list_changed_files_marks_skipped():
    toolbox = RunToolbox(_context())
    text, _ = await toolbox.call("list_changed_files")
    payload = json.loads(text)
    by_path = {item["path"]: item for item in payload["files"]}
    assert by_path["app/main.py"]["reviewable"] is True
    assert by_path["package-lock.json"]["reviewable"] is False
    assert by_path["package-lock.json"]["skipped_reason"] in {"ignored", "generated"}
    assert by_path["assets/logo.png"]["skipped_reason"] == "binary"


async def test_read_file_without_reader_explains_limitation():
    toolbox = RunToolbox(_context())
    text, _ = await toolbox.call("read_file", {"path": "app/main.py"})
    assert "unavailable" in text.lower()


async def test_read_file_with_reader_returns_numbered_lines():
    reader = StaticFileReader({"app/main.py": "line1\nline2\nline3\nline4"})
    toolbox = RunToolbox(_context(reader=reader))
    text, ok = await toolbox.call(
        "read_file", {"path": "app/main.py", "start_line": 2, "end_line": 3}
    )
    assert ok is True
    assert "    2 | line2" in text
    assert "line1" not in text
    assert reader.reads == [("app/main.py", "head", 2, 3)]


async def test_read_file_missing_path_is_graceful():
    reader = StaticFileReader({})
    toolbox = RunToolbox(_context(reader=reader))
    text, _ = await toolbox.call("read_file", {"path": "nope.py"})
    assert "not found" in text


async def test_post_review_accepts_valid_review():
    toolbox = RunToolbox(_context())
    text, ok = await toolbox.call("post_review", CLEAN_REVIEW)
    assert ok is True
    payload = json.loads(text)
    assert payload["accepted"] is True
    assert payload["comments"] == 1
    assert toolbox.submitted_review is not None


async def test_post_review_rejects_structurally_invalid_comments():
    toolbox = RunToolbox(_context())
    text, _ = await toolbox.call(
        "post_review",
        {
            "summary": "s",
            "overall_risk": "low",
            "comments": [{"file": "", "line": 0, "message": "x"}],
        },
    )
    payload = json.loads(text)
    assert payload["accepted"] is False
    assert "error" in payload


async def test_post_review_is_lenient_about_a_missing_summary():
    toolbox = RunToolbox(_context())
    text, _ = await toolbox.call("post_review", {"overall_risk": "low", "comments": []})
    payload = json.loads(text)
    assert payload["accepted"] is True


async def test_unknown_tool_returns_error_not_exception():
    toolbox = RunToolbox(_context())
    text, ok = await toolbox.call("delete_everything", {})
    assert ok is False
    assert "Tool error" in text


async def test_results_are_truncated():
    toolbox = RunToolbox(_context(), max_result_chars=120)
    text, _ = await toolbox.call("get_diff")
    assert len(text) <= 200
    assert "truncated" in text


async def test_calls_are_recorded():
    toolbox = RunToolbox(_context())
    await toolbox.call("get_pr_metadata")
    await toolbox.call("list_changed_files")
    assert [call["tool"] for call in toolbox.calls] == [
        "get_pr_metadata",
        "list_changed_files",
    ]


async def test_none_args_are_dropped():
    toolbox = RunToolbox(_context())
    text, ok = await toolbox.call("get_diff", {"path": None})
    assert ok is True
    assert "app/main.py" in text
