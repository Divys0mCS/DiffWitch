from __future__ import annotations

from app.services.diff import (
    glob_to_regex,
    is_ignored,
    parse_diff,
    prepare_diff,
    prepare_file,
)
from tests.fixtures import APP_MAIN_LEFT, APP_MAIN_RIGHT, TWO_FILE_DIFF


def test_parse_diff_finds_all_files():
    files = parse_diff(TWO_FILE_DIFF)
    assert [f.path for f in files] == ["app/main.py", "package-lock.json", "assets/logo.png"]


def test_parse_diff_marks_binary():
    binary = parse_diff(TWO_FILE_DIFF)[2]
    assert binary.is_binary is True


def test_parse_diff_line_numbers():
    main = parse_diff(TWO_FILE_DIFF)[0]
    assert main.added == 3
    assert main.removed == 1
    hunk = main.hunks[0]
    assert hunk.new_start == 1
    added = [(line.new_lineno, line.text) for line in hunk.lines if line.kind == "add"]
    assert added == [(2, "import sys"), (5, "    sys.exit(0)"), (6, "    print(os.getcwd())")]
    removed = [(line.old_lineno, line.text) for line in hunk.lines if line.kind == "del"]
    assert removed == [(4, '    print("hi")')]


def test_parse_diff_detects_new_and_deleted_files():
    raw = "\n".join(
        [
            "diff --git a/new.py b/new.py",
            "new file mode 100644",
            "--- /dev/null",
            "+++ b/new.py",
            "@@ -0,0 +1,1 @@",
            "+print(1)",
            "diff --git a/old.py b/old.py",
            "deleted file mode 100644",
            "--- a/old.py",
            "+++ /dev/null",
            "@@ -1,1 +0,0 @@",
            "-print(2)",
        ]
    )
    files = parse_diff(raw)
    assert files[0].status == "added"
    assert files[0].path == "new.py"
    assert files[1].status == "deleted"
    assert files[1].path == "old.py"


def test_parse_diff_rename():
    raw = "\n".join(
        [
            "diff --git a/a/old.py b/a/new.py",
            "similarity index 95%",
            "rename from a/old.py",
            "rename to a/new.py",
            "--- a/a/old.py",
            "+++ b/a/new.py",
            "@@ -1,1 +1,1 @@",
            "-x = 1",
            "+x = 2",
        ]
    )
    files = parse_diff(raw)
    assert files[0].status == "renamed"
    assert files[0].path == "a/new.py"
    assert files[0].old_path == "a/old.py"


def test_glob_matching_is_path_aware():
    assert glob_to_regex("package-lock.json").match("frontend/package-lock.json")
    assert glob_to_regex("package-lock.json").match("package-lock.json")
    assert not glob_to_regex("src/*.ts").match("src/deep/file.ts")
    assert glob_to_regex("vendor/**").match("vendor/a/b/c.go")
    assert glob_to_regex("**/*.min.js").match("static/js/app.min.js")
    assert glob_to_regex("*.md").match("docs/README.md")


def test_is_ignored():
    patterns = [glob_to_regex("*.lock"), glob_to_regex("vendor/**")]
    assert is_ignored("yarn.lock", patterns)
    assert is_ignored("vendor/lib/x.go", patterns)
    assert not is_ignored("app/main.py", patterns)


def test_prepare_diff_filters_lockfiles_and_binaries():
    prepared = prepare_diff(
        TWO_FILE_DIFF,
        ignore_globs=["package-lock.json", "*.lock"],
    )
    assert prepared.included_paths == ["app/main.py"]
    reasons = dict(prepared.skipped)
    assert reasons["package-lock.json"] in {"ignored", "generated"}
    assert reasons["assets/logo.png"] == "binary"


def test_prepare_diff_anchors_match_diff():
    prepared = prepare_diff(TWO_FILE_DIFF, ignore_globs=["package-lock.json"])
    anchors = prepared.right_anchors
    assert anchors["app/main.py"] == APP_MAIN_RIGHT
    assert prepared.left_anchors["app/main.py"] == APP_MAIN_LEFT


def test_rendered_diff_contains_line_numbers():
    prepared = prepare_diff(TWO_FILE_DIFF, ignore_globs=["package-lock.json"])
    text = prepared.text
    assert "+ new 5 |     sys.exit(0)" in text
    assert '- old 4 |     print("hi")' in text
    assert "FILE: app/main.py" in text


def test_truncation_drops_whole_hunks_and_keeps_anchors_valid():
    raw = "\n".join(
        [
            "diff --git a/big.py b/big.py",
            "--- a/big.py",
            "+++ b/big.py",
            "@@ -1,1 +1,1 @@",
            "+" + "a" * 50,
            "@@ -90,1 +90,1 @@",
            "+" + "b" * 500,
        ]
    )
    parsed = parse_diff(raw)[0]
    rendered = prepare_file(parsed, max_chars=120)
    assert rendered.truncated is True
    assert 1 in rendered.right_lines
    assert 90 not in rendered.right_lines
    assert "further hunk(s) not shown" in rendered.text


def test_prompt_size_cap_omits_files():
    many = []
    for index in range(10):
        many.extend(
            [
                f"diff --git a/f{index}.py b/f{index}.py",
                f"--- a/f{index}.py",
                f"+++ b/f{index}.py",
                "@@ -1,1 +1,1 @@",
                "+" + ("x" * 400),
            ]
        )
    prepared = prepare_diff(
        "\n".join(many),
        ignore_globs=[],
        max_file_chars=1_000,
        max_total_chars=2_500,
    )
    assert len(prepared.files) >= 1
    assert prepared.omitted
    assert "OMITTED FOR SIZE" in prepared.text


def test_max_files_limit():
    many = []
    for index in range(5):
        many.extend(
            [
                f"diff --git a/f{index}.py b/f{index}.py",
                f"--- a/f{index}.py",
                f"+++ b/f{index}.py",
                "@@ -1,1 +1,1 @@",
                f"+line{index}",
            ]
        )
    prepared = prepare_diff("\n".join(many), ignore_globs=[], max_files=2)
    assert len(prepared.files) == 2
    assert len(prepared.omitted) == 3


def test_empty_diff_is_handled():
    prepared = prepare_diff("", ignore_globs=[])
    assert prepared.files == []
    assert prepared.text == ""
