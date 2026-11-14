"""Image generation through the local ``agy`` CLI (Gemini CLI agent).

What this module enforces:

- the CLI is invoked with an argv list, never through a shell;
- ``--sandbox`` is unconditional, and the child runs with a dedicated
  ``HOME`` whose ``PreToolUse`` hook allows only ``generate_image``,
  ``finish`` and writes inside the turn workspace;
- the child runs in an empty per-turn workspace, so it has no project to index,
  and is told an absolute save path inside it;
- the reply is structured JSON (``--output-format json``/``stream-json`` plus a
  JSON schema), but only the file on disk is authoritative: it is verified,
  aspect-checked, and published with a same-directory rename.

The structured reply carries the image model and the agent's version; cost is
reported as ``0.0`` with ``quota_units: 1`` because ``agy`` spends quota rather
than a priced API call.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import structlog
from video_editor_ffmpeg.probe import MediaInfoError, probe_media
from video_editor_ffmpeg.run import FFmpegError
from video_editor_state.errors import VEProviderError

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers._media import MediaContentError
from video_editor_asset_ingestion.providers.base import GenerativeProvider
from video_editor_asset_ingestion.providers.visual.agy_profile import (
    auth_ready,
    ensure_profile,
    profile_env,
)
from video_editor_asset_ingestion.providers.visual.fal_ai import _validate_image_bytes

logger = structlog.get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_DRIVING_MODEL = "gemini-3.8-flash-low"
DEFAULT_EFFORT = "low"
#: agy 1.5 hands generate_image to an "image-generator" subagent and can report SUCCESS before the
#: file lands (2026-11-11). A live session keeps running, so give the file this long to appear; a
#: finished one-shot process gets a short grace for a last flush.
SESSION_FILE_GRACE_SECONDS = 120.0
ONE_SHOT_FILE_GRACE_SECONDS = 15.0
SUPPORTED_VERSION_RANGE = ((1, 4, 0), (2, 0, 0))
MAX_SESSIONS = 2

#: Framing when the request names none — the same default PoYo and OpenRouter use.
DEFAULT_ASPECT = (16, 9)

#: How far the drawn image may sit from the asked aspect before it is rejected.
ASPECT_TOLERANCE = 0.03

#: The reply the agent must produce. ``_verify`` still owns the dimensions.
IMAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "width": {"type": "integer"},
        "height": {"type": "integer"},
        "image_model": {"type": "string"},
    },
    "required": ["path", "width", "height", "image_model"],
    "additionalProperties": False,
}


def classify_agy_error(text: str) -> str:
    """Map an agent error to the breaker's ``quota``/``auth``/``other`` kinds."""
    lowered = text.lower()
    if any(
        marker in lowered
        for marker in (
            "quota",
            "rate limit",
            "resource exhausted",
            "too many requests",
            "429",
        )
    ):
        return "quota"
    if any(
        marker in lowered
        for marker in (
            "not authenticated",
            "unauthenticated",
            "unauthorized",
            "authentication",
            "sign in",
            "login",
            "oauth",
            "401",
            "403",
        )
    ):
        return "auth"
    return "other"


def _version_tuple(text: str) -> tuple[int, int, int] | None:
    for token in text.replace("v", " ").split():
        parts = token.split(".")
        if len(parts) >= 2 and all(part.isdigit() for part in parts[:2]):
            return (
                int(parts[0]),
                int(parts[1]),
                int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0,
            )
    return None


def _supported_version(version: tuple[int, int, int]) -> bool:
    low, high = SUPPORTED_VERSION_RANGE
    return low <= version < high


def _target_aspect(request: AssetRequest) -> tuple[int, int]:
    return request.aspect() or DEFAULT_ASPECT


def _aspect_line(request: AssetRequest) -> str:
    """The aspect directive for the prompt (#291).

    Every request gets one: the agent draws square when told nothing, which a
    16:9 or 9:16 composition then crops. Pixel sizes are added when known.
    """
    width, height = _target_aspect(request)
    pixels = ""
    w, h = request.params.get("width"), request.params.get("height")
    if (
        isinstance(w, int)
        and isinstance(h, int)
        and not isinstance(w, bool)
        and not isinstance(h, bool)
        and w > 0
        and h > 0
    ):
        pixels = f" ({w}x{h})"
    return f"\n\nAspect: {width}:{height}{pixels}."


def _sandbox_disabled(value: object) -> bool:
    """Return True for the spellings of "false" a YAML/JSON config may carry."""
    if isinstance(value, str):
        return value.strip().lower() in {"false", "0"}
    # ``is False`` alone misses 0; keep bools out of the numeric branch so
    # ``True`` (== 1) is never read as false.
    return value is False or (
        isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0
    )


def _set_pdeathsig() -> None:
    """Ask Linux to SIGKILL the agent if the parent dies (#295)."""
    if not sys.platform.startswith("linux"):
        return
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        # PR_SET_PDEATHSIG = 1
        libc.prctl(1, signal.SIGKILL, 0, 0, 0)
    except Exception:  # noqa: BLE001 — best effort; the kill path still runs
        return


def _extract_json(text: str) -> dict[str, Any] | None:
    """Return the first JSON object embedded in an agent response."""
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


class AGYImageError(VEProviderError):
    """A generation attempt that should fall through to the next provider."""

    def __init__(self, message: str, *, kind: str = "other"):
        super().__init__(message)
        self.kind = kind


class AGYSession:
    """One long-lived ``agy --input-format stream-json`` process.

    A session runs one turn per NDJSON message. The provider keeps at most
    ``MAX_SESSIONS`` of these, each with its own empty workspace and guard
    boundary. A session that dies is discarded; the caller then retries the
    image as a normal one-shot call.
    """

    def __init__(
        self,
        *,
        argv: list[str],
        workspace: Path,
        env: dict[str, str],
        timeout: float,
    ) -> None:
        self.argv = argv
        self.workspace = workspace
        self.env = env
        self.timeout = timeout
        self.proc: asyncio.subprocess.Process | None = None
        self.driving_model = ""
        self._stderr = bytearray()
        self._stderr_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            *self.argv,
            cwd=str(self.workspace),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
            start_new_session=True,
            preexec_fn=_set_pdeathsig,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        line = await asyncio.wait_for(self._readline(), timeout=min(20.0, self.timeout))
        event = self._parse_event(line)
        if event.get("event") == "init":
            init = event.get("init")
            if isinstance(init, dict):
                self.driving_model = str(init.get("model") or "")

    async def _readline(self) -> bytes:
        assert self.proc is not None and self.proc.stdout is not None
        return await self.proc.stdout.readline()

    def _parse_event(self, line: bytes) -> dict[str, Any]:
        if not line:
            raise AGYImageError("agy stream closed before replying")
        try:
            value = json.loads(line.decode("utf-8", "replace"))
        except ValueError as exc:
            raise AGYImageError(f"agy stream sent invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise AGYImageError("agy stream sent a non-object event")
        return value

    async def _drain_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        while True:
            chunk = await self.proc.stderr.read(4096)
            if not chunk:
                return
            self._stderr.extend(chunk)
            if len(self._stderr) > 16_384:
                del self._stderr[: len(self._stderr) - 16_384]

    def stderr_text(self) -> str:
        return self._stderr.decode("utf-8", "replace").strip()

    async def turn(self, prompt: str) -> dict[str, Any]:
        """Send one turn and return its terminal ``result`` object."""
        if self.proc is None or self.proc.stdin is None or self.proc.returncode is not None:
            raise AGYImageError("agy stream session is not running")
        message = {
            "event": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            },
        }
        try:
            self.proc.stdin.write((json.dumps(message) + "\n").encode())
            await self.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise AGYImageError(f"agy stream closed while sending a turn: {exc}") from exc

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise AGYImageError(f"agy timed out after {self.timeout:.0f}s")
            try:
                line = await asyncio.wait_for(self._readline(), timeout=remaining)
            except TimeoutError:
                raise AGYImageError(f"agy timed out after {self.timeout:.0f}s") from None
            event = self._parse_event(line)
            kind = event.get("event")
            if kind == "result":
                result = event.get("result")
                return result if isinstance(result, dict) else {}
            if kind == "init":
                init = event.get("init")
                if isinstance(init, dict):
                    self.driving_model = str(init.get("model") or "")
            # step_update/other progress events are deliberately ignored: the
            # file check and the final result are the only trusted evidence.

    async def close(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is not None:
            with contextlib.suppress(Exception):
                if proc.stdin is not None:
                    proc.stdin.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            if proc.returncode is None:
                AGYImageProvider._kill(proc)
                await AGYImageProvider._reap(proc)
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stderr_task
            self._stderr_task = None


class AGYImageProvider(GenerativeProvider):
    """Generate one image per request through the ``agy`` CLI."""

    provider_name = "agy_image"

    #: Synthesizes images from a prompt; eligible for ``"auto"`` stills.
    generates_images = True

    def __init__(self, config: ProviderConfig | None = None) -> None:
        cfg = config or ProviderConfig()
        options = cfg.provider_options or {}
        # ProviderConfig.timeout defaults to 120.0, so ``or`` would silently
        # cap AGY runs below its 300 s budget. Treat it as configured only
        # when the caller actually set it.
        configured = "timeout" in cfg.model_fields_set
        self.timeout = float(cfg.timeout) if configured else DEFAULT_TIMEOUT_SECONDS
        self.model = str(options.get("model") or DEFAULT_DRIVING_MODEL)
        self.agy_bin = str(options.get("agy_bin") or "agy")
        self.effort = str(options.get("effort") or DEFAULT_EFFORT)
        if self.effort not in {"low", "medium", "high", "xhigh", "max"}:
            logger.warning("agy_image_unknown_effort", effort=self.effort, using=DEFAULT_EFFORT)
            self.effort = DEFAULT_EFFORT
        self.profile_dir = str(options.get("profile_dir") or "~/.config/video-editor/agy-profile")
        self.use_session = bool(options.get("session", True))
        self._version: str | None = None
        self._sessions: list[AGYSession] = []
        self._session_slots = asyncio.Semaphore(MAX_SESSIONS)
        self._session_lock = asyncio.Lock()

        # ``--sandbox`` is the security boundary between model-written script
        # text and this machine: the user's agy settings allow ``command(*)``.
        # The flag is unconditional, so a false option cannot weaken it, but
        # silently ignoring a security-looking setting would mislead.
        if _sandbox_disabled(options.get("sandbox", True)):
            logger.warning(
                "agy_image_sandbox_forced",
                reason="--sandbox cannot be disabled; ignoring provider_options.sandbox=false",
            )

    # ------------------------------------------------------------------
    # Profile / preflight
    # ------------------------------------------------------------------

    def _prepare_profile(self) -> Path:
        profile = ensure_profile(self.profile_dir)
        if not auth_ready(profile):
            raise AGYImageError(
                "agy OAuth credentials were not found; run `ve agy-setup`",
                kind="auth",
            )
        return profile

    async def preflight(self) -> None:
        """Binary, supported version and a cheap authenticated probe (#292)."""
        binary = shutil.which(self.agy_bin)
        if binary is None:
            raise AGYImageError(f"agy binary not found: {self.agy_bin!r}")
        profile = self._prepare_profile()
        version_out, _ = await self._capture([binary, "--version"], cwd=profile, timeout=10.0)
        version = _version_tuple(version_out)
        if version is None:
            raise AGYImageError(f"could not parse agy version from {version_out!r}")
        if not _supported_version(version):
            raise AGYImageError(
                f"agy {version_out.strip()} is outside the supported range "
                f"{SUPPORTED_VERSION_RANGE[0]}..{SUPPORTED_VERSION_RANGE[1]}"
            )
        self._version = version_out.strip()
        # ``models`` is the cheapest authenticated call: it does not spend an
        # image quota, but it fails when the login is missing/expired.
        models_out, _ = await self._capture(
            [binary, "--output-format", "json", "models"],
            cwd=profile,
            timeout=20.0,
        )
        try:
            payload = json.loads(models_out)
        except ValueError as exc:
            raise AGYImageError(
                f"agy auth probe returned invalid JSON: {exc}", kind="auth"
            ) from exc
        if not isinstance(payload, dict) or payload.get("status") != "SUCCESS":
            response = payload.get("response") if isinstance(payload, dict) else models_out
            raise AGYImageError(
                f"agy auth probe failed: {str(response)[:500]}",
                kind=classify_agy_error(str(response)),
            )

    async def _capture(self, argv: list[str], *, cwd: Path, timeout: float) -> tuple[str, str]:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=profile_env(self.profile_dir),
            start_new_session=True,
            preexec_fn=_set_pdeathsig,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            self._kill(proc)
            await self._reap(proc)
            raise AGYImageError(
                f"agy {' '.join(argv[1:])} timed out after {timeout:.0f}s"
            ) from None
        except BaseException:
            self._kill(proc)
            with contextlib.suppress(asyncio.CancelledError):
                await self._reap(proc)
            raise
        text_out = (stdout or b"").decode("utf-8", "replace")
        text_err = (stderr or b"").decode("utf-8", "replace")
        if proc.returncode != 0:
            raise AGYImageError(
                f"agy {' '.join(argv[1:])} exited {proc.returncode}: {text_err[-500:]}",
                kind=classify_agy_error(text_err),
            )
        return text_out, text_err

    # ------------------------------------------------------------------
    # Prompt / argv
    # ------------------------------------------------------------------

    def _prompt(self, request: AssetRequest, tmp: Path) -> str:
        return (
            f"{request.prompt}{_aspect_line(request)}"
            f"\n\nGenerate exactly one image with the generate_image tool. "
            "If a subagent makes the image, wait for it to finish and check that the file exists "
            "before you reply; never reply while it is still running. "
            f"Save the image to {tmp} and reply with JSON "
            f'{{"path": "{tmp}", "width": <int>, "height": <int>, '
            '"image_model": "<model>"}}.'
        )

    def _base_argv(self, binary: str) -> list[str]:
        budget = max(1, int(self.timeout - 30))
        return [
            binary,
            "--sandbox",
            "--disable-slash-commands",
            "--effort",
            self.effort,
            "--model",
            self.model,
            "--print-timeout",
            f"{budget}s",
        ]

    def _write_schema(self, directory: Path) -> Path:
        path = directory / "agy-image-schema.json"
        path.write_text(json.dumps(IMAGE_SCHEMA), encoding="utf-8")
        return path

    def _argv(self, request: AssetRequest, tmp: Path, binary: str) -> list[str]:
        schema = self._write_schema(tmp.parent)
        return [
            *self._base_argv(binary),
            "--output-format",
            "json",
            "--json-schema",
            str(schema),
            "-p",
            self._prompt(request, tmp),
        ]

    def _session_argv(self, binary: str, workspace: Path) -> list[str]:
        schema = self._write_schema(workspace)
        return [
            *self._base_argv(binary),
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--json-schema",
            str(schema),
        ]

    # ------------------------------------------------------------------
    # Generate
    # ------------------------------------------------------------------

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        output_path = Path(output_path).resolve()
        if request.asset_type is not AssetType.IMAGE:
            raise AGYImageError(f"agy_image handles images only, got {request.asset_type}")
        ext = request.output_ext.lstrip(".").lower()
        if ext and ext != "png":
            raise AGYImageError(f"agy_image writes PNG only; request asked for {ext!r}")

        binary = shutil.which(self.agy_bin)
        if binary is None:
            raise AGYImageError(f"agy binary not found: {self.agy_bin!r}")
        self._prepare_profile()
        if self._version is None:
            await self.preflight()

        started = time.monotonic()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        reply: dict[str, Any]
        width: int
        height: int
        if self.use_session:
            try:
                reply, width, height = await self._generate_session(request, output_path, binary)
            except AGYImageError as exc:
                if exc.kind in {"quota", "auth"}:
                    raise
                logger.warning(
                    "agy_session_failed_falling_back",
                    job_id=request.job_id,
                    asset_id=request.asset_id,
                    error=str(exc),
                )
                reply, width, height = await self._generate_once(request, output_path, binary)
        else:
            reply, width, height = await self._generate_once(request, output_path, binary)

        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.IMAGE,
            local_path=str(output_path),
            scene_id=request.scene_id,
            metadata={
                "provider": self.provider_name,
                "model": str(reply.get("image_model") or "agy-default"),
                "width": width,
                "height": height,
                "agy_version": self._version,
                "driving_model": self.model,
                "image_model": reply.get("image_model"),
                "quota_units": 1,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "cost": 0.0,
            },
        )

    async def _generate_once(
        self, request: AssetRequest, output_path: Path, binary: str
    ) -> tuple[dict[str, Any], int, int]:
        with tempfile.TemporaryDirectory(prefix="agy-run-") as workspace:
            tmp = Path(workspace) / "image.png"
            try:
                stdout, stderr = await self._run_capture(
                    self._argv(request, tmp, binary), cwd=Path(workspace)
                )
                reply, agent_text = self._parse_stdout(stdout, stderr, Path(workspace), tmp)
                await _await_file(tmp, ONE_SHOT_FILE_GRACE_SECONDS)
                width, height = self._verify_agent(tmp, _target_aspect(request), agent_text)
                self._publish(tmp, output_path)
                return reply, width, height
            finally:
                tmp.unlink(missing_ok=True)

    async def _generate_session(
        self, request: AssetRequest, output_path: Path, binary: str
    ) -> tuple[dict[str, Any], int, int]:
        session = await self._acquire_session(binary)
        tmp = session.workspace / f"image-{uuid.uuid4().hex}.png"
        try:
            result = await session.turn(self._prompt(request, tmp))
            reply, agent_text = self._parse_result(result, session.workspace, tmp)
            await _await_file(tmp, SESSION_FILE_GRACE_SECONDS)
            width, height = self._verify_agent(tmp, _target_aspect(request), agent_text)
            self._publish(tmp, output_path)
            await self._release_session(session)
            return reply, width, height
        except BaseException:
            await self._discard_session(session)
            raise
        finally:
            tmp.unlink(missing_ok=True)

    async def _acquire_session(self, binary: str) -> AGYSession:
        await self._session_slots.acquire()
        try:
            async with self._session_lock:
                if self._sessions:
                    return self._sessions.pop()
                workspace = Path(tempfile.mkdtemp(prefix="agy-session-"))
                session = AGYSession(
                    argv=self._session_argv(binary, workspace),
                    workspace=workspace,
                    env=profile_env(self.profile_dir),
                    timeout=self.timeout,
                )
                try:
                    await session.start()
                except BaseException:
                    await session.close()
                    shutil.rmtree(workspace, ignore_errors=True)
                    raise
                return session
        except BaseException:
            self._session_slots.release()
            raise

    async def _release_session(self, session: AGYSession) -> None:
        async with self._session_lock:
            self._sessions.append(session)
        self._session_slots.release()

    async def _discard_session(self, session: AGYSession) -> None:
        try:
            await session.close()
        finally:
            shutil.rmtree(session.workspace, ignore_errors=True)
            self._session_slots.release()

    async def aclose(self) -> None:
        async with self._session_lock:
            sessions = self._sessions
            self._sessions = []
        for session in sessions:
            await session.close()
            shutil.rmtree(session.workspace, ignore_errors=True)

    # ------------------------------------------------------------------
    # Parsing / process helpers
    # ------------------------------------------------------------------

    def _parse_stdout(
        self, stdout: str, stderr: str, workspace: Path, tmp: Path
    ) -> tuple[dict[str, Any], str]:
        try:
            payload = json.loads(stdout)
        except ValueError as exc:
            raise AGYImageError(f"agy did not return JSON: {exc}; stderr: {stderr[-500:]}") from exc
        if not isinstance(payload, dict):
            raise AGYImageError("agy returned a non-object JSON payload")
        return self._parse_result(payload, workspace, tmp)

    def _parse_result(
        self, payload: dict[str, Any], workspace: Path, tmp: Path
    ) -> tuple[dict[str, Any], str]:
        status = str(payload.get("status") or "")
        if status != "SUCCESS":
            reason = payload.get("error") or payload.get("response") or status or "unknown error"
            raise AGYImageError(
                f"agy failed: {str(reason)[:800]}", kind=classify_agy_error(str(reason))
            )
        reply = payload.get("structured_output")
        if not isinstance(reply, dict):
            reply = _extract_json(str(payload.get("response") or ""))
        if not isinstance(reply, dict):
            raise AGYImageError(
                "agy returned no structured output: " + str(payload.get("response") or "")[:800]
            )
        missing = [key for key in ("path", "width", "height", "image_model") if key not in reply]
        if missing:
            raise AGYImageError(f"agy reply is missing schema field(s): {missing}")
        if not isinstance(reply["path"], str) or not isinstance(reply["image_model"], str):
            raise AGYImageError("agy reply path/image_model must be strings")
        if not isinstance(reply["width"], int) or not isinstance(reply["height"], int):
            raise AGYImageError("agy reply width/height must be integers")
        reported = Path(reply["path"]).expanduser()
        if not reported.is_absolute():
            reported = workspace / reported
        try:
            reported = reported.resolve()
        except (OSError, RuntimeError) as exc:
            raise AGYImageError(f"agy reported an unresolvable path: {reply['path']}") from exc
        if reported != tmp.resolve():
            raise AGYImageError(
                f"agy reported {reported}, expected {tmp.resolve()}; refusing an unexpected file"
            )
        agent_text = str(payload.get("response") or payload.get("error") or "")
        return reply, agent_text

    def _verify_agent(
        self, path: Path, aspect: tuple[int, int], agent_text: str
    ) -> tuple[int, int]:
        """Verify the file, but preserve a quota/auth reason the agent gave.

        The agent can report ``status: SUCCESS`` with a fabricated path after
        its image tool returned a quota/auth error. In that case the missing
        file is the symptom; the agent's text is what must open the breaker.
        """
        try:
            return self._verify(path, aspect)
        except AGYImageError as exc:
            kind = classify_agy_error(agent_text)
            if kind != "other":
                raise AGYImageError(f"{exc}; agent said: {agent_text[:800]}", kind=kind) from exc
            raise

    async def _run_capture(self, argv: list[str], *, cwd: Path) -> tuple[str, str]:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=profile_env(self.profile_dir),
            start_new_session=True,
            preexec_fn=_set_pdeathsig,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except TimeoutError:
            self._kill(proc)
            # Reap the killed child so its transport is closed on this loop
            # rather than garbage-collected after the loop shuts down. A
            # cancellation arriving here propagates: it is a newer, stronger
            # signal than the timeout and must not become an ``AGYImageError``.
            await self._reap(proc)
            raise AGYImageError(f"agy timed out after {self.timeout:.0f}s") from None
        except BaseException:
            # ``CancelledError`` is a ``BaseException``: without this branch a
            # cancelled task would leave the agent running in its own session.
            # Kill the group and reap before propagating the interruption. The
            # cancellation that is already in flight wins over any second one
            # raised while reaping, so it is suppressed here.
            self._kill(proc)
            with contextlib.suppress(asyncio.CancelledError):
                await self._reap(proc)
            raise
        if proc.returncode != 0:
            tail = (stderr or b"")[-500:].decode("utf-8", "replace")
            raise AGYImageError(
                f"agy exited {proc.returncode}: {tail}", kind=classify_agy_error(tail)
            )
        return (
            (stdout or b"").decode("utf-8", "replace"),
            (stderr or b"").decode("utf-8", "replace"),
        )

    @staticmethod
    async def _reap(proc: asyncio.subprocess.Process, timeout: float = 5.0) -> None:
        """Bounded wait for a killed child so its transport closes on this loop.

        Only an unresponsive child's timeout is swallowed. ``CancelledError``
        propagates so a cancellation that arrives while reaping is not turned
        into an ``AGYImageError`` by the timeout branch; a caller already
        unwinding from another exception suppresses it explicitly.
        """
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except TimeoutError:
            logger.warning(
                "agy_image_reap_incomplete",
                pid=proc.pid,
                reason="child did not exit in time",
            )

    @staticmethod
    def _kill(proc: asyncio.subprocess.Process) -> None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            # The group may already be gone; the process fallback can itself
            # race a reaped child, so it must not replace the original error.
            with contextlib.suppress(ProcessLookupError, RuntimeError):
                proc.kill()

    @staticmethod
    def _publish(tmp: Path, output_path: Path) -> None:
        """Move a verified image into place without exposing a partial file (#280).

        The workspace lives under the system temp dir, usually another
        filesystem, where ``shutil.move`` degrades to copy-then-delete. Copy to
        a hidden sibling first, so the final step is a same-object rename and
        readers of ``output_path`` see either nothing or the whole image.
        """
        staged = output_path.parent / f".{output_path.stem}.{uuid.uuid4().hex}.agy.png"
        try:
            shutil.copyfile(tmp, staged)
            os.replace(staged, output_path)
        finally:
            staged.unlink(missing_ok=True)

    def _verify(self, path: Path, aspect: tuple[int, int]) -> tuple[int, int]:
        if not path.exists() or path.stat().st_size == 0:
            raise AGYImageError("agy reported success but wrote no image")
        try:
            _validate_image_bytes(path.read_bytes())
        except MediaContentError as exc:
            raise AGYImageError(f"agy wrote bytes that are not a valid image: {exc}") from exc
        try:
            info = probe_media(path)
        except (MediaInfoError, FFmpegError, ValueError) as exc:
            # probe_media delegates to run_ffmpeg (FFmpegError) and json.loads
            # (ValueError); MediaInfoError keeps a future probe change covered.
            raise AGYImageError(f"agy wrote an unreadable image: {exc}") from exc
        if info.width <= 0 or info.height <= 0:
            raise AGYImageError("agy wrote an image with no readable dimensions")
        # The agent treats the aspect line as advice; a composition cover-crops
        # whatever comes back, so a square still loses the subject (#291).
        # Falling through lets a provider that takes the aspect as a parameter
        # draw it instead.
        wanted = aspect[0] / aspect[1]
        got = info.width / info.height
        if abs(got / wanted - 1.0) > ASPECT_TOLERANCE:
            raise AGYImageError(
                f"agy drew {info.width}x{info.height}, not the requested aspect "
                f"{aspect[0]}:{aspect[1]}"
            )
        return info.width, info.height


async def _await_file(path: Path, seconds: float, *, poll: float = 1.0) -> None:
    """Wait up to ``seconds`` for ``path`` to exist with a stable, non-zero size."""
    deadline = time.monotonic() + seconds
    last = -1
    while True:
        size = path.stat().st_size if path.exists() else 0
        if size > 0 and size == last:
            return
        if time.monotonic() >= deadline:
            return  # _verify reports the missing file
        last = size
        await asyncio.sleep(poll)
