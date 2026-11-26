# check_research.py
"""Fail until every figure row in research.md has a value, source, date and basis."""
from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED_IDS = [f"R{i}" for i in range(1, 10)]
COLUMNS = ["id", "label", "on-screen", "value", "unit", "source", "source_date", "basis"]
VALID_BASIS = {"published", "observed", "estimate"}


def parse_rows(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    in_table = False
    for line in text.splitlines():
        if line.strip().startswith("| id |"):
            in_table = True
            continue
        if in_table:
            if not line.strip().startswith("|"):
                break
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) != len(COLUMNS) or set(cells[0]) <= {"-", " "}:
                continue
            rows.append(dict(zip(COLUMNS, cells)))
    return rows


def main(path: Path) -> int:
    rows = parse_rows(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    seen = {r["id"] for r in rows}
    for rid in REQUIRED_IDS:
        if rid not in seen:
            problems.append(f"{rid}: missing row")
    for row in rows:
        for col in ("label", "on-screen", "value", "source", "source_date", "basis"):
            if not row.get(col):
                problems.append(f"{row['id']}: empty {col}")
        if row.get("basis") and row["basis"] not in VALID_BASIS:
            problems.append(f"{row['id']}: basis must be one of {sorted(VALID_BASIS)}")
        if row.get("source_date") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["source_date"]):
            problems.append(f"{row['id']}: source_date must be YYYY-MM-DD")
    if problems:
        print("\n".join(problems))
        return 1
    print(f"ok: {len(rows)} figures complete")
    return 0


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name("research.md"))
    raise SystemExit(main(target))
