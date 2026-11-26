"""Assemble the publishable ``diffwitch`` package from ``backend/app``.

The backend's import package is called ``app``.  A top-level ``app`` on PyPI
would collide with the existing project of that name, and renaming the backend
would change the Render start command, so the wheel is generated instead: the
source tree is copied to ``build/pypi/diffwitch`` and only the import root is
rewritten (``app.x`` -> ``diffwitch.x``).  Nothing in ``backend/`` is modified.

Usage:

    pip install build
    python scripts/build_wheel.py

Artifacts land in ``dist/pypi/``.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "backend" / "app"
BUILD = ROOT / "build" / "pypi"
PACKAGE = BUILD / "diffwitch"
OUT = ROOT / "dist" / "pypi"

# Ordered: the plain `from app import` rule must run before the dotted ones
# would otherwise never see it.
RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # Leading whitespace is captured so function-local imports keep their indent.
    (re.compile(r"^([ \t]*)from app import", re.MULTILINE), r"\1from diffwitch import"),
    (re.compile(r"^([ \t]*)from app\.", re.MULTILINE), r"\1from diffwitch."),
    (re.compile(r"^([ \t]*)import app\.", re.MULTILINE), r"\1import diffwitch."),
    (re.compile(r"python -m app\.", re.MULTILINE), "python -m diffwitch."),
)

STALE_IMPORT = re.compile(r"^\s*(?:from|import) app\b", re.MULTILINE)


def assemble() -> None:
    if BUILD.exists():
        shutil.rmtree(BUILD)
    PACKAGE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        APP, PACKAGE, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    )

    for path in sorted(PACKAGE.rglob("*.py")):
        original = path.read_text("utf-8")
        rewritten = original
        for pattern, replacement in RULES:
            rewritten = pattern.sub(replacement, rewritten)
        if rewritten != original:
            path.write_text(rewritten, "utf-8")

    stale = [
        str(path.relative_to(BUILD))
        for path in PACKAGE.rglob("*.py")
        if STALE_IMPORT.search(path.read_text("utf-8"))
    ]
    if stale:
        raise SystemExit(f"imports still point at 'app': {', '.join(stale)}")

    for name in ("README.md", "LICENSE"):
        shutil.copy2(ROOT / name, BUILD / name)
    shutil.copy2(ROOT / "pypi" / "pyproject.toml", BUILD / "pyproject.toml")


def build_wheel() -> int:
    command = [sys.executable, "-m", "build", "--outdir", str(OUT), str(BUILD)]
    print("+", " ".join(command))
    return subprocess.call(command)


def main() -> int:
    if importlib.util.find_spec("build") is None:
        print("The 'build' package is missing: python -m pip install build", file=sys.stderr)
        return 1
    assemble()
    print(f"assembled {PACKAGE}")
    return build_wheel()


if __name__ == "__main__":
    raise SystemExit(main())
