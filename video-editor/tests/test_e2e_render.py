"""True end-to-end render tests using synthetic media."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import _require_ffmpeg


def _sanitized_env() -> dict[str, str]:
    """Return a copy of os.environ with the global HyperFrames binary hidden.

    The globally installed HyperFrames CLI (v0.7.x) does not support the
    ``--input`` flag that the engine expects.  Hiding it forces the pipeline
    to fall back to the FFmpeg renderer, which is the tested path.

    We search PATH directly rather than using ``shutil.which`` because the
    ``_hide_global_hyperframes_binary`` autouse fixture monkeypatches
    ``shutil.which`` to always return ``None`` for ``hyperframes``.
    """
    env = os.environ.copy()
    path = env.get("PATH", "")
    # Remove any PATH entry that contains a ``hyperframes`` executable.
    kept: list[str] = []
    for entry in path.split(os.pathsep):
        if not entry:
            continue
        hf_candidate = Path(entry) / "hyperframes"
        if hf_candidate.exists() and hf_candidate.is_file():
            continue  # drop this entry
        kept.append(entry)
    env["PATH"] = os.pathsep.join(kept)
    return env


@pytest.mark.slow
def test_full_skills_pipeline_produces_playable_mp4(tmp_path: Path) -> None:
    """Run the full skills pipeline on a synthetic video and verify output."""
    _require_ffmpeg()

    # Create synthetic test video
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=3:size=640x480:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-pix_fmt",
            "yuv420p",
            "-shortest",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    assert source.exists()

    # Create a synthetic transcript so the captions stage has words to burn.
    # The 440 Hz sine wave is not speech, so Whisper cannot transcribe it.
    transcript = tmp_path / "transcript.json"
    transcript.write_text(
        json.dumps(
            {
                "text": "This is a synthetic end to end test of the full pipeline.",
                "words": [
                    {"word": "This", "start": 0.0, "end": 0.4},
                    {"word": "is", "start": 0.5, "end": 0.7},
                    {"word": "a", "start": 0.8, "end": 0.9},
                    {"word": "synthetic", "start": 1.0, "end": 1.5},
                    {"word": "end", "start": 1.6, "end": 1.8},
                    {"word": "to", "start": 1.9, "end": 2.0},
                    {"word": "end", "start": 2.1, "end": 2.3},
                    {"word": "test", "start": 2.4, "end": 2.7},
                ],
            }
        )
    )

    # Run the pipeline with HyperFrames hidden from PATH so the engine
    # falls back to the FFmpeg renderer (the tested code path).
    work = tmp_path / "work"
    output = tmp_path / "output"
    result = subprocess.run(
        [
            "uv",
            "run",
            "ve",
            "run",
            str(source),
            "--job-id",
            "e2e-test",
            "--work-root",
            str(work),
            "--output-root",
            str(output),
            "--preset",
            "short_explainer",
            "--transcript",
            str(transcript),
            "--skip-review",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        env=_sanitized_env(),
    )

    # Check output
    final = output / "e2e-test" / "final.mp4"
    if final.exists():
        # Verify it's playable
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(final),
            ],
            capture_output=True,
            text=True,
        )
        assert probe.returncode == 0
        info = json.loads(probe.stdout)
        duration = float(info["format"]["duration"])
        assert duration > 0.5
    else:
        # If the pipeline failed, surface stderr for debugging
        pytest.fail(
            f"Pipeline did not produce final.mp4.\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )
