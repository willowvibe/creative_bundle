#!/usr/bin/env python3
"""Open a generated FableCut project in the bundled FableCut editor.

FableCut hardcodes ``./project.json`` and ``./media/`` paths, so this
script temporarily replaces the editor's project with the one the
video-editor engine exported. The server is started in the background and
a state file is written so ``finalize_fablecut.py`` can later copy the
edited project back and run ``ve import-fablecut``.

Usage:
    python scripts/open_in_fablecut.py \
        videos/2026-07-12/suryastakam/fablecut

After editing in the browser and saving, run:
    python scripts/finalize_fablecut.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FABLECUT_DIR = REPO_ROOT / "tools" / "FableCut"
PROJECT_FILE = FABLECUT_DIR / "project.json"
MEDIA_DIR = FABLECUT_DIR / "media"
BACKUP_SUFFIX = ".ve_backup"
STATE_FILE = FABLECUT_DIR / ".fablecut_state.json"
PID_FILE = FABLECUT_DIR / ".fablecut_server.pid"
URL = "http://localhost:7777"


def _resolve_source(project_dir: Path) -> Path:
    """Resolve the source project directory."""
    src = project_dir.expanduser()
    if not src.is_absolute():
        src = REPO_ROOT / src
    if not src.is_dir():
        raise SystemExit(f"Source project directory not found: {src}")
    src_json = src / "project.json"
    if not src_json.is_file():
        raise SystemExit(f"No project.json in source directory: {src}")
    return src.resolve()


def _backup_editor_defaults() -> None:
    """Back up the editor's default project.json and media folder once."""
    if (
        PROJECT_FILE.exists()
        and not (PROJECT_FILE.parent / f"project.json{BACKUP_SUFFIX}").exists()
    ):
        shutil.copy2(PROJECT_FILE, PROJECT_FILE.parent / f"project.json{BACKUP_SUFFIX}")
    backup_media = FABLECUT_DIR / f"media{BACKUP_SUFFIX}"
    if MEDIA_DIR.exists() and not backup_media.exists():
        shutil.copytree(MEDIA_DIR, backup_media)


def _project_revision(path: Path) -> int:
    """Return the revision field from a project.json, or 0 on any error."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data.get("revision", 0) or 0)
    except Exception:  # noqa: BLE001 - keep the helper process alive
        return 0


def _has_newer_editor_project(src: Path) -> bool:
    """Return True if the in-browser FableCut project is newer than the source."""
    if not PROJECT_FILE.is_file():
        return False
    src_json = src / "project.json"
    if not src_json.is_file():
        return False
    return _project_revision(PROJECT_FILE) > _project_revision(src_json)


def _sync_into_editor(src: Path) -> None:
    """Copy the source project.json and media into the FableCut editor root."""
    _backup_editor_defaults()

    src_json = src / "project.json"
    src_media = src / "media"

    PROJECT_FILE.write_text(src_json.read_text(encoding="utf-8"), encoding="utf-8")

    if MEDIA_DIR.exists():
        shutil.rmtree(MEDIA_DIR)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    if src_media.is_dir():
        for item in src_media.iterdir():
            if item.is_file():
                shutil.copy2(item, MEDIA_DIR / item.name)
            elif item.is_dir():
                shutil.copytree(item, MEDIA_DIR / item.name, dirs_exist_ok=True)


def _server_running() -> bool:
    """Return True if the FableCut server appears to be running."""
    if not PID_FILE.is_file():
        return False
    pid = PID_FILE.read_text(encoding="utf-8").strip()
    if not pid.isdigit():
        return False
    return Path(f"/proc/{pid}").is_dir()


def _stop_server() -> None:
    """Kill the background FableCut server if it is running."""
    if not PID_FILE.is_file():
        return
    pid = PID_FILE.read_text(encoding="utf-8").strip()
    PID_FILE.unlink(missing_ok=True)
    if pid.isdigit():
        try:
            import os

            os.kill(int(pid), 9)
        except ProcessLookupError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Open a generated FableCut project in the bundled editor."
    )
    parser.add_argument(
        "project_dir", help="Path to the exported FableCut project (contains project.json + media/)"
    )
    parser.add_argument(
        "--no-launch",
        action="store_true",
        help="Only sync the project; do not start the Node server.",
    )
    parser.add_argument(
        "--stop-existing",
        action="store_true",
        default=True,
        help="Stop an already-running FableCut server before starting a new one (default: True).",
    )
    parser.add_argument(
        "--force-sync",
        action="store_true",
        help="Overwrite any in-browser FableCut edits with the source project (default: preserve edits).",
    )
    args = parser.parse_args()

    if not FABLECUT_DIR.is_dir():
        print(
            f"FableCut editor not found at {FABLECUT_DIR}.\n"
            "Clone it there first:\n"
            "  git clone https://github.com/ronak-create/FableCut.git tools/FableCut",
            file=sys.stderr,
        )
        return 1

    src = _resolve_source(Path(args.project_dir))

    # FableCut auto-saves edits to tools/FableCut/project.json. If the editor
    # copy is newer than the source, preserve it unless the caller explicitly
    # asks to overwrite. This prevents accidental loss of in-browser edits.
    if _has_newer_editor_project(src) and not args.force_sync:
        print(
            f"WARNING: FableCut already has a newer project.json (revision "
            f"{_project_revision(PROJECT_FILE)} > source revision "
            f"{_project_revision(src / 'project.json')}).\n"
            "Preserving in-browser edits. Use --force-sync to overwrite."
        )
    else:
        _sync_into_editor(src)
        print(f"Synced FableCut project from {src}")

    # Record the source path so finalize can copy the edited project back.
    import json as _json

    STATE_FILE.write_text(
        _json.dumps(
            {
                "source_project_dir": str(src),
                "project_file": str(PROJECT_FILE),
                "media_dir": str(MEDIA_DIR),
                "opened_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    if not _has_newer_editor_project(src) or args.force_sync:
        print(f"Editor project file: {PROJECT_FILE}")
        print(f"Editor media folder: {MEDIA_DIR}")
    print(f"State file: {STATE_FILE}")

    if args.no_launch:
        print(
            "Project synced. Run with --no-launch omitted to start the server and open the browser."
        )
        return 0

    if args.stop_existing:
        _stop_server()

    print("Starting FableCut server in the background...")
    try:
        proc = subprocess.Popen(
            ["node", "server.js"],
            cwd=FABLECUT_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    except FileNotFoundError:
        print(
            "node command not found. Start the server manually:\n"
            f"  cd {FABLECUT_DIR} && node server.js",
            file=sys.stderr,
        )
        return 1

    # Give the server a moment to bind.
    for _ in range(20):
        try:
            import urllib.request

            with urllib.request.urlopen(URL, timeout=0.5):
                break
        except Exception:  # noqa: BLE001 - keep the helper process alive
            time.sleep(0.2)
    else:
        print(
            f"Warning: server did not respond on {URL} yet; it may still be starting.",
            file=sys.stderr,
        )

    print(f"FableCut server running at {URL} (PID {proc.pid})")
    print("Opening browser...")
    webbrowser.open(URL)
    print("Edit and save in the browser, then run:")
    print("  python scripts/finalize_fablecut.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
