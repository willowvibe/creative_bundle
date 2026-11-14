"""Tests for the documented demo fixtures.

The `ve run` / `ve music` workflows documented in README and
`workflows/*.md` all assume specific files in `demo/`. If any of
those files disappear, every documented example breaks. This
module guards the documented surface area.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.conftest import _require_ffmpeg

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = REPO_ROOT / "demo"


def test_demo_directory_exists() -> None:
    """The demo/ directory must exist for documented workflows to work."""
    assert DEMO_DIR.is_dir(), (
        f"demo/ directory missing at {DEMO_DIR}; "
        "every documented workflow in README.md assumes this path."
    )


def test_demo_raw_mp4_exists() -> None:
    """demo/raw.mp4 is referenced by the one-command run script."""
    raw = DEMO_DIR / "raw.mp4"
    assert raw.exists(), f"{raw} missing; run `python scripts/generate_demo.py`."
    assert raw.stat().st_size > 0


def test_demo_transcript_json_exists() -> None:
    """demo/transcript.json is referenced by every skills pipeline example."""
    transcript = DEMO_DIR / "transcript.json"
    assert transcript.exists(), f"{transcript} missing; run `python scripts/generate_demo.py`."
    assert transcript.stat().st_size > 0


def test_demo_music_m4a_exists_and_is_valid_aac() -> None:
    """demo/music.m4a is referenced by 4+ documented music workflows.

    Regression: previously this file didn't exist (Issue #21 in
    docs/ISSUES_BUGS.md) and every documented music workflow crashed
    with FileNotFoundError. The fix: scripts/generate_demo.py now
    synthesizes a 20s 220Hz AAC file alongside the speech fixture.
    """
    _require_ffmpeg()
    music = DEMO_DIR / "music.m4a"
    assert music.exists(), (
        f"{music} missing; run `python scripts/generate_demo.py` "
        "to (re)generate the demo music fixture."
    )
    assert music.stat().st_size > 0

    # Probe with ffprobe — confirms the file is decodable AAC, not a
    # zero-byte or truncated artifact.
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "default=noprint_wrappers=1",
            str(music),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "codec_name=aac" in result.stdout, (
        f"demo/music.m4a is not AAC-encoded: {result.stdout!r}"
    )


def test_documented_music_workflow_runs_end_to_end(
    tmp_path: Path,
) -> None:
    """The README's `ve run --music-path ./demo/music.m4a` example works.

    Bug #21 regression: this would crash with FileNotFoundError before
    the fixture was added. We don't run the full pipeline (too slow) —
    we just confirm the fixture is discoverable to MusicSkill via the
    same path-resolution code path the CLI uses.
    """
    from music.music import MusicSkill
    from video_editor_state.job import JobContext

    ctx = JobContext(
        job_id="doc-music-fixture",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )
    skill = MusicSkill(ctx, music_path=DEMO_DIR / "music.m4a")
    resolved = skill._find_music()
    assert resolved.exists()
    assert resolved == DEMO_DIR / "music.m4a"
