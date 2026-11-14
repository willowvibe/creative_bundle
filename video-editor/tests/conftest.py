"""Shared test fixtures."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog

# A developer ``.env`` may point the data dir at a real machine directory (A13).
# Pin it to empty *before* any app module reads ``.env``, so module-level
# defaults stay the repo-local ``projects`` / ``outputs`` and tests stay
# hermetic; ``load_dotenv`` will not override an existing empty value.
os.environ.setdefault("VIDEO_EDITOR_DATA_DIR", "")

# The transcription wrapper imports ``faster_whisper`` /
# ``video_editor_transcribe`` at module load time. Install a stub before any
# test module is collected so the suite does not require a real
# faster-whisper install or network model download.
if "faster_whisper" not in sys.modules:
    _faster_whisper_stub = types.ModuleType("faster_whisper")

    class _StubWhisperModel:
        def __init__(self, *args: object, **kwargs: object) -> None:  # pragma: no cover
            raise RuntimeError(
                "_StubWhisperModel should never be instantiated in tests; "
                "monkeypatch whisper_local._load_model."
            )

    _faster_whisper_stub.WhisperModel = _StubWhisperModel  # type: ignore[attr-defined]
    sys.modules["faster_whisper"] = _faster_whisper_stub


@pytest.fixture(autouse=True)
def _hermetic_classify(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests offline: a loaded ``.env`` (the CLI imports ``load_dotenv``)
    must not route the suite to the live TypeSafe/Jev classifier. Tests that
    exercise Jev inject a fake client explicitly (``classify_client=...``).

    Also keep the suite's default data root inside its temp dirs; a developer
    ``.env`` may point ``VIDEO_EDITOR_DATA_DIR`` at a real machine directory.
    """
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("VIDEO_EDITOR_DATA_DIR", raising=False)


def _require_ffmpeg() -> None:
    """Skip the current test unless both ``ffmpeg`` and ``ffprobe`` are on PATH.

    Bug #107: tests that shell out to FFmpeg should skip gracefully on hosts
    without the tools, rather than failing with ``FileNotFoundError``.
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available on PATH")
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe not available on PATH")


@pytest.fixture
def allow_elevenlabs_non_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the non-voice ElevenLabs providers' own offline tests exercise their HTTP code.

    In real use these providers are disabled: ElevenLabs is voice-over only (providers/elevenlabs_policy.py).
    """
    from video_editor_asset_ingestion.providers.elevenlabs_policy import TEST_OVERRIDE_ENV

    monkeypatch.setenv(TEST_OVERRIDE_ENV, "1")


@pytest.fixture
def tmp_media(tmp_path: Path) -> Path:
    """Create a short synthetic video with audio for testing.

    Bug #107: if ffmpeg is not on PATH, skip the test instead of failing
    with FileNotFoundError.
    """
    _require_ffmpeg()
    video_path = tmp_path / "test.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=3:size=320x240:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-pix_fmt",
            "yuv420p",
            str(video_path),
        ],
        check=True,
        capture_output=True,
    )
    return video_path


@pytest.fixture
def tmp_job(tmp_path: Path) -> Path:
    """Create a temporary job directory structure."""
    work_dir = tmp_path / "work" / "test-job"
    output_dir = tmp_path / "output" / "test-job"
    work_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    return tmp_path


@pytest.fixture(autouse=True)
def _configure_structlog_for_tests() -> Iterator[None]:
    """Route structlog through stdlib so pytest's caplog captures log records."""
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    yield


@pytest.fixture(autouse=True)
def _hide_global_hyperframes_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mask any globally-installed `hyperframes` binary from the test process.

    The installed HyperFrames CLI (v0.7.x) expects a project directory with a
    HyperFrames HTML composition, while the legacy in-tree callers pass a JSON
    plan file. Rather than fail every graphics/pipeline test that incidentally
    probes for HyperFrames, unit tests fall back to the FFmpeg renderer. Tests
    that explicitly exercise the CLI contract monkeypatch subprocess.run and
    are unaffected by this fixture.
    """

    real_which = shutil.which

    def _which(
        cmd: str | os.PathLike[str],
        mode: int = 1,
        path: str | os.PathLike[str] | None = None,
    ) -> str | None:
        if cmd == "hyperframes":
            return None
        # A bare ``agy`` is the user's real Gemini agent: a test that reaches
        # it spends quota and waits minutes. Provider tests pass a fake by
        # absolute path; the opt-in integration test keeps the real one.
        if cmd == "agy" and os.environ.get("VE_AGY_INTEGRATION") != "1":
            return None
        return real_which(cmd, mode, path)

    monkeypatch.setattr(shutil, "which", _which)


@pytest.fixture(scope="session")
def hyperframes_binary() -> str:
    """The real ``hyperframes`` executable, found without ``shutil.which``.

    The autouse fixture above masks the binary from ``shutil.which`` so no
    test renders by accident. A render test needs the opposite, so this scans
    ``PATH`` itself and skips — never fails — when the tool is not installed
    or is older than the v0.8 project contract this suite renders against.
    """
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry) / "hyperframes"
        if not (candidate.is_file() and os.access(candidate, os.X_OK)):
            continue
        try:
            probe = subprocess.run(
                [str(candidate), "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        match = re.search(r"(\d+)\.(\d+)", probe.stdout or probe.stderr)
        if match is None or (int(match.group(1)), int(match.group(2))) < (0, 8):
            continue
        return str(candidate)
    pytest.skip("hyperframes >= 0.8 is not installed; this test's render leg needs it")


@pytest.fixture(autouse=True)
def _isolated_cli_queue() -> Iterator[None]:
    """Drop the CLI's process-wide in-memory queue after every test.

    ``video_editor_cli.main._get_queue`` is a module singleton, so a test that
    only enqueues (``test_enqueue_accepts_valid_json``) left its job behind and
    the next test's ``ve worker --max-jobs 1`` ran that job instead of its own.
    Tests that need a queue object of their own still use ``fresh_queue``.
    """
    main = sys.modules.get("video_editor_cli.main")
    if main is not None:
        from video_editor_state.queue import InMemoryJobQueue

        main._queue_instance = InMemoryJobQueue()  # type: ignore[attr-defined]
    yield
    main = sys.modules.get("video_editor_cli.main")
    if main is not None:
        main._queue_instance = None  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _no_agy_file_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests fake agy; never wait for a subagent's file (live runs keep the real grace)."""
    if os.environ.get("VE_AGY_INTEGRATION") == "1":
        return
    from video_editor_asset_ingestion.providers.visual import agy_image

    monkeypatch.setattr(agy_image, "SESSION_FILE_GRACE_SECONDS", 0.0)
    monkeypatch.setattr(agy_image, "ONE_SHOT_FILE_GRACE_SECONDS", 0.0)
