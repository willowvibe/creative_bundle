"""Shared FFmpeg subprocess wrapper with timeout + stderr capture."""

from __future__ import annotations

import subprocess
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 600.0

#: Upper bounds for subprocesses outside this module's wrappers (#279). Every
#: ``subprocess.run`` in the engine carries one, so a wedged FFmpeg (or a
#: HyperFrames Chrome) fails the job instead of hanging the worker.
#: Quick metadata calls: ffprobe, ``ffmpeg -filters``.
PROBE_TIMEOUT_SECONDS = 120.0
#: Anything that reads or encodes a whole file: renders, mixes, analysis passes.
RENDER_TIMEOUT_SECONDS = 3600.0


class FFmpegError(RuntimeError):
    """Raised when an FFmpeg/FFprobe command fails or times out."""

    def __init__(
        self,
        message: str,
        cmd: list[str],
        stderr: str | None = None,
        stdout: str | None = None,
    ):
        super().__init__(message)
        self.cmd = cmd
        self.stderr = stderr
        self.stdout = stdout

    def __str__(self) -> str:
        cmd_str = " ".join(self.cmd)
        suffix = f"\nstderr: {self.stderr}" if self.stderr else ""
        return f"{self.args[0]}\ncommand: {cmd_str}{suffix}"


class CommandError(RuntimeError):
    """Raised when a generic subprocess command fails or times out."""

    def __init__(
        self,
        message: str,
        cmd: list[str],
        stderr: str | None = None,
        stdout: str | None = None,
    ):
        super().__init__(message)
        self.cmd = cmd
        self.stderr = stderr
        self.stdout = stdout

    def __str__(self) -> str:
        cmd_str = " ".join(self.cmd)
        suffix = f"\nstderr: {self.stderr}" if self.stderr else ""
        return f"{self.args[0]}\ncommand: {cmd_str}{suffix}"


def run_ffmpeg(
    cmd: list[str],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    check: bool = True,
    text: bool = True,
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    """Run an FFmpeg/FFprobe subprocess with a timeout and capture stderr.

    Wraps ``subprocess.run`` so callers get a single, actionable exception
    containing the command and the subprocess stderr instead of a bare
    ``CalledProcessError`` with only a return code.
    """
    logger.info("running_ffmpeg", cmd=cmd, timeout=timeout)
    try:
        return subprocess.run(
            cmd,
            check=check,
            capture_output=True,
            text=text,
            timeout=timeout,
            **kwargs,
        )
    except subprocess.TimeoutExpired as exc:
        stderr = _decode_stderr(exc.stderr)
        raise FFmpegError(
            f"FFmpeg/FFprobe timed out after {timeout}s",
            cmd,
            stderr,
            _decode_stderr(exc.stdout),
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = _decode_stderr(exc.stderr)
        raise FFmpegError(
            f"FFmpeg/FFprobe exited with status {exc.returncode}",
            cmd,
            stderr,
            _decode_stderr(exc.stdout),
        ) from exc


def _decode_stderr(stderr: Any) -> str | None:
    if stderr is None:
        return None
    if isinstance(stderr, bytes):
        return stderr.decode("utf-8", errors="replace").strip() or None
    if isinstance(stderr, str):
        return stderr.strip() or None
    return str(stderr).strip() or None


def run_subprocess(
    cmd: list[str],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    check: bool = True,
    text: bool = True,
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    """Run a generic subprocess with timeout and captured stderr.

    Non-FFmpeg callers (pnpm, WhisperX, etc.) use this instead of duplicating
    timeout/stderr handling. Mirrors ``run_ffmpeg`` but raises ``CommandError``.
    """
    logger.info("running_subprocess", cmd=cmd, timeout=timeout)
    try:
        return subprocess.run(
            cmd,
            check=check,
            capture_output=True,
            text=text,
            timeout=timeout,
            **kwargs,
        )
    except subprocess.TimeoutExpired as exc:
        stderr = _decode_stderr(exc.stderr)
        raise CommandError(
            f"Subprocess timed out after {timeout}s",
            cmd,
            stderr,
            _decode_stderr(exc.stdout),
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = _decode_stderr(exc.stderr)
        raise CommandError(
            f"Subprocess exited with status {exc.returncode}",
            cmd,
            stderr,
            _decode_stderr(exc.stdout),
        ) from exc
