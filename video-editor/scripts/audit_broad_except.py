#!/usr/bin/env python3
"""Count broad ``except`` handlers and whether they carry a policy noqa.

The A8 policy requires every broad handler to name the side channel it
protects. Ruff enforces that with ``BLE001``/``S110``; this script exists so an
audit can reproduce the raw counts (including sites ruff does not see because
they are already annotated) without re-writing the grep.

Usage:
    uv run python scripts/audit_broad_except.py [--include-tests]

Exit code 1 when an unannotated broad handler exists.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

BROAD_EXCEPT = re.compile(r"except\s+(?:Exception|BaseException)\b")
BROAD_BASE = re.compile(r"except\s+(?:\([^)]*\)\s*:?\s*)?BaseException\b")
NOQA = re.compile(r"#\s*noqa:\s*[^#\n]*\b(?:BLE001|S110)\b")
ROOTS = ("apps", "packages", "scripts")


def iter_files(include_tests: bool) -> list[Path]:
    roots = [Path(root) for root in ROOTS]
    if include_tests:
        roots.append(Path("tests"))
    files: list[Path] = []
    for root in roots:
        if root.is_dir():
            files.extend(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)
    return files


def _cleanup_guard(lines: list[str], index: int) -> bool:
    """True for lifecycle/cancel guards and handlers that re-raise.

    ``except BaseException`` is a cancellation/finally path; a handler whose
    body raises is propagating the failure, not swallowing it. Neither needs a
    side-channel comment.
    """
    if BROAD_BASE.search(lines[index]):
        return True
    base_indent = len(lines[index]) - len(lines[index].lstrip())
    for line in lines[index + 1 :]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= base_indent:
            return False
        if stripped.startswith("raise"):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-tests", action="store_true", help="also scan tests/")
    args = parser.parse_args()

    total = 0
    cleanup = 0
    unannotated: dict[str, list[int]] = defaultdict(list)
    for path in iter_files(args.include_tests):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, start=1):
            if not BROAD_EXCEPT.search(line) or line.lstrip().startswith("#"):
                continue
            total += 1
            if NOQA.search(line):
                continue
            if _cleanup_guard(lines, number - 1):
                cleanup += 1
                continue
            unannotated[str(path)].append(number)

    missing = sum(len(numbers) for numbers in unannotated.values())
    print(f"broad except handlers: {total}")
    print(f"re-raise cleanup guards (no noqa needed): {cleanup}")
    print(f"without a BLE001/S110 noqa: {missing}")
    for path, numbers in sorted(unannotated.items()):
        joined = ", ".join(str(number) for number in numbers)
        print(f"  {path}: {joined}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
