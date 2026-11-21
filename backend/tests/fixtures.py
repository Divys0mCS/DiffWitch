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
