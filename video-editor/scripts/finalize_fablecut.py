#!/usr/bin/env python3
"""Finalize a FableCut editing session and import the edited timeline.

This is the counterpart to ``open_in_fablecut.py``. It:

1. Copies the edited ``tools/FableCut/project.json`` back to the
   original exported project directory.
2. Stops the background FableCut server.
3. Runs ``ve import-fablecut`` to reconcile the edited timeline into a
   new ``ScenePlan``.

Usage:
    python scripts/finalize_fablecut.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FABLECUT_DIR = REPO_ROOT / "tools" / "FableCut"
PROJECT_FILE = FABLECUT_DIR / "project.json"
MEDIA_DIR = FABLECUT_DIR / "media"
STATE_FILE = FABLECUT_DIR / ".fablecut_state.json"
PID_FILE = FABLECUT_DIR / ".fablecut_server.pid"


def _stop_server() -> None:
    """Kill the background FableCut server if it is running."""
    if not PID_FILE.is_file():
        return
    pid_text = PID_FILE.read_text(encoding="utf-8").strip()
    PID_FILE.unlink(missing_ok=True)
    if pid_text.isdigit():
        pid = int(pid_text)
        try:
            import os

            os.kill(pid, 9)
        except ProcessLookupError:
            pass
        # Wait briefly for the port to free up.
        for _ in range(10):
            if not Path(f"/proc/{pid}").is_dir():
                break
            time.sleep(0.1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize a FableCut editing session.")
    parser.add_argument(
        "--plan-out-name",
        default="scene_plan_edited.json",
        help="Name of the reconciled plan file written under <work>/plan/.",
    )
    parser.add_argument(
        "--no-import",
        action="store_true",
        help="Only copy the edited project back and stop the server; do not run ve import-fablecut.",
    )
    args = parser.parse_args()

    if not STATE_FILE.is_file():
        print(
            f"No FableCut session state found at {STATE_FILE}.\nRun open_in_fablecut.py first.",
            file=sys.stderr,
        )
        return 1

    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    source_dir = Path(state["source_project_dir"])
    if not source_dir.is_dir():
        raise SystemExit(f"Original project directory not found: {source_dir}")

    if not PROJECT_FILE.is_file():
        raise SystemExit(f"No edited project.json at {PROJECT_FILE}")

    # Copy the edited project back to the canonical export folder.
    shutil.copy2(PROJECT_FILE, source_dir / "project.json")
    if MEDIA_DIR.is_dir():
        dest_media = source_dir / "media"
        if dest_media.exists():
            shutil.rmtree(dest_media)
        shutil.copytree(MEDIA_DIR, dest_media)

    print(f"Copied edited project back to {source_dir}")

    _stop_server()
    print("Stopped FableCut server.")

    if args.no_import:
        print("Skipped import. Run ve import-fablecut manually when ready.")
        return 0

    # Derive job_id from the source project dir name (last segment before /fablecut).
    parts = source_dir.parts
    job_id = parts[-2] if parts[-1] == "fablecut" else parts[-1]
    work_root = REPO_ROOT / "projects"
    output_root = REPO_ROOT / "videos"

    cmd = [
        "uv",
        "run",
        "ve",
        "import-fablecut",
        job_id,
        "--project-dir",
        str(source_dir),
        "--plan-out-name",
        args.plan_out_name,
        "--work-root",
        str(work_root),
        "--output-root",
        str(output_root),
    ]
    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        print("import-fablecut failed.", file=sys.stderr)
        return result.returncode

    print(f"Import complete. Edited plan: projects/{job_id}/plan/{args.plan_out_name}")
    print("Next: run the render/export step with the edited plan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
