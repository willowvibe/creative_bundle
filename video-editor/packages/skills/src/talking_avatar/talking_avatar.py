"""Talking-avatar skill: lip-sync a cartoon avatar onto a screen recording.

The default ``hybrid`` engine keeps Rhubarb's open/puckered mouth timing, moves
every lip closure onto a wav2vec2-measured M/B/P moment, and fills in missing
open/rounded mouths from each word's phones.  It needs the dialogue text via
``--script`` (or a precomputed ``--align-hints`` JSON).  Measured hints are
produced by the self-contained :mod:`talking_avatar.align_script` run in an
optional environment so torch and g2p_en never become runtime dependencies::

    uv venv .venv-lipsync
    uv pip install --python .venv-lipsync/bin/python torch \\
        --index-url https://download.pytorch.org/whl/cpu
    uv pip install --python .venv-lipsync/bin/python transformers g2p_en numpy

If that interpreter is absent, hybrid logs how to create it and falls back to
plain Rhubarb.  The other engines are ``rhubarb`` and ``loudness``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import structlog
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, CommandError, run_subprocess
from video_editor_state.job import JobContext

from talking_avatar.avatar import AvatarRenderer, iter_avatar_frames, max_bob_overscan
from talking_avatar.bubbles import (
    AvatarAction,
    Bubble,
    BubbleError,
    bubble_start_times,
    load_bubbles,
    resolve_actions,
    resolve_bubbles,
)
from talking_avatar.hybrid import (
    AlignHints,
    HintsError,
    build_hybrid_timeline,
    load_hints,
)
from talking_avatar.rhubarb import (
    DEFAULT_LEAD,
    DEFAULT_STRESS_PERCENTILE,
    RhubarbError,
    build_rhubarb_timeline,
    extract_mouth_cues,
    find_rhubarb_binary,
)
from talking_avatar.timeline import (
    DEFAULT_FPS,
    DEFAULT_QUIET_THRESHOLD,
    VisemeFrame,
    WordTiming,
    build_viseme_timeline,
)

logger = structlog.get_logger(__name__)

DEFAULT_AVATAR_RELATIVE = Path("templates/avatars/bolt/bolt.svg")
DEFAULT_POSITION = "br"
DEFAULT_SIZE = 0.30
DEFAULT_MARGIN = 32
DEFAULT_SEED = 0
POSITIONS = frozenset({"br", "bl", "tr", "tl"})
LIPSYNC_ENGINES = frozenset({"hybrid", "rhubarb", "loudness"})
LIPSYNC_PYTHON_ENV = "VE_LIPSYNC_PYTHON"
LIPSYNC_VENV_DIR = ".venv-lipsync"
LIPSYNC_VENV_HINT = (
    "Create the optional lip-sync environment with: "
    "uv venv .venv-lipsync && "
    "uv pip install --python .venv-lipsync/bin/python torch "
    "--index-url https://download.pytorch.org/whl/cpu && "
    "uv pip install --python .venv-lipsync/bin/python transformers g2p_en numpy"
)
ALIGN_HINTS_CACHE_VERSION = "v1"
RMS_SAMPLE_RATE = 16000
RMS_FPS = DEFAULT_FPS


class TalkingAvatarError(RuntimeError):
    """Raised when the talking-avatar skill cannot build its output."""


def default_avatar_path() -> Path:
    """Locate the bundled Bolt avatar, from the cwd or the source tree."""
    candidates = [Path.cwd(), *Path(__file__).resolve().parents]
    for base in candidates:
        candidate = base / DEFAULT_AVATAR_RELATIVE
        if candidate.exists():
            return candidate
    return DEFAULT_AVATAR_RELATIVE


def repository_root() -> Path:
    """Return the checkout root that contains ``packages/skills``."""
    return Path(__file__).resolve().parents[4]


def default_lipsync_python() -> Path:
    """Return the conventional ``.venv-lipsync`` interpreter path."""
    return repository_root() / LIPSYNC_VENV_DIR / "bin" / "python"


def find_lipsync_python() -> str | None:
    """Find the optional alignment interpreter, or ``None`` when absent."""
    override = os.environ.get(LIPSYNC_PYTHON_ENV)
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return str(candidate)
        resolved = shutil.which(override)
        if resolved:
            return resolved
        return None
    candidate = default_lipsync_python()
    return str(candidate) if candidate.is_file() else None


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def align_hints_cache_path(
    audio_path: Path | str,
    script_path: Path | str,
    work_dir: Path | str,
) -> Path:
    """Return the render-cache path keyed by the audio and script contents."""
    digest = hashlib.sha256(ALIGN_HINTS_CACHE_VERSION.encode("utf-8"))
    for path in (Path(audio_path), Path(script_path)):
        digest.update(b"\0")
        digest.update(_file_digest(path).encode("ascii"))
    return Path(work_dir) / f"align-hints-{digest.hexdigest()[:16]}.json"


def run_align_script(
    python: str,
    audio_path: Path | str,
    script_path: Path | str,
    output_path: Path | str,
) -> None:
    """Run the self-contained aligner in ``python`` and write hints JSON."""
    script = Path(__file__).with_name("align_script.py")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = run_subprocess(
            [python, str(script), str(audio_path), str(script_path), str(output)],
            check=False,
            timeout=RENDER_TIMEOUT_SECONDS,
        )
    except CommandError as exc:
        raise TalkingAvatarError(f"Lip-sync alignment failed: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr or ""
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        raise TalkingAvatarError(
            f"Lip-sync alignment failed ({result.returncode}): {str(detail)[:500]}"
        )
    if not output.exists():
        raise TalkingAvatarError(f"Lip-sync alignment produced no hints at {output}")


def generate_align_hints(
    audio_path: Path | str,
    script_path: Path | str,
    work_dir: Path | str,
    *,
    python: str,
) -> AlignHints:
    """Load cached hints or run :mod:`talking_avatar.align_script` once."""
    cache = align_hints_cache_path(audio_path, script_path, work_dir)
    if cache.is_file():
        try:
            return load_hints(cache)
        except HintsError:
            logger.warning("talking_avatar_align_cache_invalid", path=str(cache))
    run_align_script(python, audio_path, script_path, cache)
    try:
        return load_hints(cache)
    except HintsError as exc:
        raise TalkingAvatarError(str(exc)) from exc


def _audio_duration(audio_path: Path) -> float:
    info = probe_media(audio_path)
    if info.duration is None:
        raise TalkingAvatarError(f"Could not probe audio duration: {audio_path}")
    return float(info.duration)


def extract_rms_envelope(
    audio_path: Path | str,
    *,
    sample_rate: int = RMS_SAMPLE_RATE,
    rms_fps: float = RMS_FPS,
) -> list[float]:
    """Decode ``audio_path`` and return per-frame RMS amplitudes.

    Samples are mixed to mono and measured over non-overlapping hops, one per
    ``rms_fps`` frame (30 fps by default).  Values are peak-normalised so the
    loudest frame is 1.0; :func:`talking_avatar.timeline.rms_to_openness`
    turns them into the dB-normalised, smoothed openness curve.
    """
    import numpy as np

    cmd = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(audio_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "s16le",
        "-",
    ]
    result = run_subprocess(cmd, check=False, text=False, timeout=RENDER_TIMEOUT_SECONDS)
    if result.returncode != 0:
        stderr = result.stderr or b""
        if isinstance(stderr, str):
            stderr = stderr.encode("utf-8", "replace")
        raise TalkingAvatarError(
            f"Could not decode audio {audio_path}: {stderr.decode('utf-8', 'replace')[:500]}"
        )

    pcm = np.frombuffer(result.stdout or b"", dtype=np.int16).astype(np.float32) / 32768.0
    hop = max(1, int(round(sample_rate / rms_fps)))
    frame_count = len(pcm) // hop
    if frame_count == 0:
        return []
    blocks = pcm[: frame_count * hop].reshape(frame_count, hop)
    envelope = np.sqrt(np.mean(blocks * blocks, axis=1))
    peak = float(envelope.max()) if envelope.size else 0.0
    if peak > 0.0:
        envelope = envelope / peak
    return [float(value) for value in envelope]


def transcribe_speech(
    audio_path: Path | str,
    output_path: Path | str,
    *,
    model_size: str = "base",
    language: str | None = None,
) -> tuple[list[WordTiming], str | None]:
    """Transcribe ``audio_path`` and return word timings plus detected language.

    ``language`` is left as ``None`` by default so faster-whisper detects the
    spoken language.  Its ``info.language`` is passed back to the timeline,
    which only applies English spelling overrides when the detection says the
    audio is English.
    """
    from video_editor_transcribe import transcribe_audio

    payload = transcribe_audio(
        audio_path,
        output_path,
        model_size=model_size,
        language=language,
        word_timestamps=True,
        device="cpu",
        compute_type="int8",
    )
    words: list[WordTiming] = []
    for item in payload.get("words", []):
        text = str(item.get("word") or item.get("text") or "").strip()
        if not text:
            continue
        words.append(
            WordTiming(
                text=text,
                start=float(item.get("start", 0.0)),
                end=float(item.get("end", item.get("start", 0.0))),
            )
        )
    detected = payload.get("language")
    detected_language = str(detected) if detected else None
    return words, detected_language


def transcribe_words(
    audio_path: Path | str,
    output_path: Path | str,
    *,
    model_size: str = "base",
    language: str | None = None,
) -> list[WordTiming]:
    """Transcribe ``audio_path`` to word timings with faster-whisper."""
    words, _ = transcribe_speech(
        audio_path,
        output_path,
        model_size=model_size,
        language=language,
    )
    return words


def _overlay_xy(
    position: str,
    *,
    output_width: int,
    output_height: int,
    avatar_size: int,
    padding: int,
    margin: int,
) -> tuple[int, int]:
    if position not in POSITIONS:
        raise TalkingAvatarError(
            f"Unknown position {position!r}; choose one of {sorted(POSITIONS)}"
        )
    left = margin - padding
    right = output_width - margin - avatar_size - padding
    top = margin - padding
    if position in {"br", "bl"}:
        # Anchor the shoulders to the frame edge, then overscan by the maximum
        # upward bob so the bottom never lifts away from the edge.
        bottom = output_height + max_bob_overscan(avatar_size) - avatar_size - padding
    else:
        bottom = output_height - margin - avatar_size - padding
    x = right if position in {"br", "tr"} else left
    y = bottom if position in {"br", "bl"} else top
    return max(0, int(round(x))), max(0, int(round(y)))


def _ffmpeg_command(
    *,
    screen_path: Path,
    audio_path: Path,
    output_path: Path,
    frame_size: tuple[int, int],
    overlay_x: int,
    overlay_y: int,
    duration: float,
    fps: float,
) -> list[str]:
    duration_text = f"{max(duration, 0.001):.3f}"
    fps_text = f"{fps:g}"
    width, height = frame_size
    filter_graph = (
        f"[0:v]fps={fps_text},"
        f"tpad=stop_mode=clone:stop_duration={duration_text}[bg];"
        f"[bg][1:v]overlay=x={overlay_x}:y={overlay_y}:format=auto[v]"
    )
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(screen_path),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgba",
        "-s",
        f"{width}x{height}",
        "-framerate",
        fps_text,
        "-i",
        "-",
        "-i",
        str(audio_path),
        "-filter_complex",
        filter_graph,
        "-map",
        "[v]",
        "-map",
        "2:a",
        "-t",
        duration_text,
        "-r",
        fps_text,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


async def _pipe_frames(cmd: list[str], frames: Iterator[bytes]) -> None:
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    if process.stdin is None or process.stderr is None:
        raise TalkingAvatarError("Could not open FFmpeg pipes")
    stderr_task = asyncio.create_task(process.stderr.read())
    try:
        for frame in frames:
            try:
                process.stdin.write(frame)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                break
    finally:
        try:
            process.stdin.close()
            await process.stdin.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass
    stderr = await stderr_task
    try:
        returncode = await asyncio.wait_for(process.wait(), timeout=RENDER_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise TalkingAvatarError("FFmpeg timed out while compositing the avatar") from exc
    if returncode != 0:
        detail = stderr.decode("utf-8", "replace") if stderr else ""
        raise TalkingAvatarError(f"FFmpeg failed ({returncode}): {detail[:1000]}")


def _timeline_payload(
    timeline: Sequence[VisemeFrame],
    *,
    fps: float,
    duration: float,
    words: Sequence[WordTiming],
) -> dict[str, Any]:
    return {
        "fps": fps,
        "duration": duration,
        "words": [{"text": word.text, "start": word.start, "end": word.end} for word in words],
        "frames": [
            {
                "frame": frame.frame,
                "time": round(frame.time, 6),
                "shape": frame.shape,
                "rms": round(frame.rms, 6),
                "openness": round(frame.openness, 6),
                "word": frame.word,
            }
            for frame in timeline
        ],
    }


class TalkingAvatarSkill:
    """Composite a lip-synced cartoon avatar onto a screen recording."""

    @property
    def name(self) -> str:
        return "talking_avatar"

    def __init__(
        self,
        ctx: JobContext | None = None,
        *,
        avatar_path: Path | str | None = None,
        fps: float = DEFAULT_FPS,
        quiet_threshold: float = DEFAULT_QUIET_THRESHOLD,
    ) -> None:
        self.ctx = ctx
        self.avatar_path = Path(avatar_path) if avatar_path is not None else None
        self.fps = float(fps)
        self.quiet_threshold = float(quiet_threshold)

    def _work_dir(self, output_path: Path) -> Path:
        if self.ctx is not None:
            directory = self.ctx.work_dir / "talking_avatar"
        else:
            directory = output_path.parent / ".talking_avatar"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _resolve_words(
        self,
        audio_path: Path,
        work_dir: Path,
        words: Sequence[Any] | None,
    ) -> tuple[list[WordTiming], str | None]:
        if words is not None:
            from talking_avatar.timeline import _coerce_words

            return _coerce_words(words), None
        return transcribe_speech(audio_path, work_dir / "transcript.json")

    def run(
        self,
        screen_path: Path | str,
        audio_path: Path | str,
        output_path: Path | str | None = None,
        *,
        words: Sequence[Any] | None = None,
        avatar_path: Path | str | None = None,
        position: str = DEFAULT_POSITION,
        size: float = DEFAULT_SIZE,
        margin: int = DEFAULT_MARGIN,
        seed: int = DEFAULT_SEED,
        lipsync: str = "hybrid",
        script: Path | str | None = None,
        align_hints: Path | str | None = None,
        lead: float = DEFAULT_LEAD,
        crossfade: bool = True,
        bounce: bool = False,
        stress_open: bool = True,
        stress_percentile: float = DEFAULT_STRESS_PERCENTILE,
        motion: bool = True,
        bubbles: Path | str | None = None,
    ) -> dict[str, Any]:
        """Render ``output_path`` and return paths plus a timeline summary."""
        screen = Path(screen_path)
        audio = Path(audio_path)
        if not screen.exists():
            raise FileNotFoundError(f"Screen recording not found: {screen}")
        if not audio.exists():
            raise FileNotFoundError(f"Voice audio not found: {audio}")
        if not 0.0 < float(size) <= 1.0:
            raise TalkingAvatarError("--size must be greater than 0 and at most 1")
        if int(margin) < 0:
            raise TalkingAvatarError("--margin must be zero or greater")
        if position not in POSITIONS:
            raise TalkingAvatarError(
                f"Unknown position {position!r}; choose one of {sorted(POSITIONS)}"
            )
        requested_engine = str(lipsync).strip().lower()
        if requested_engine not in LIPSYNC_ENGINES:
            raise TalkingAvatarError(
                f"Unknown lipsync engine {lipsync!r}; choose one of {sorted(LIPSYNC_ENGINES)}"
            )
        if float(lead) < 0.0:
            raise TalkingAvatarError("--lead must be zero or greater")
        if not 0.0 <= float(stress_percentile) <= 100.0:
            raise TalkingAvatarError("--stress-percentile must be between 0 and 100")
        if script is not None:
            script = Path(script)
            if not script.exists():
                raise FileNotFoundError(f"Script not found: {script}")
        if align_hints is not None:
            align_hints = Path(align_hints)
            if not align_hints.exists():
                raise FileNotFoundError(f"Alignment hints not found: {align_hints}")
        if requested_engine == "hybrid" and align_hints is None and script is None:
            raise TalkingAvatarError(
                "The hybrid lip-sync engine needs the dialogue text: pass --script FILE "
                "(or --align-hints FILE with precomputed hints)."
            )
        bubbles_path: Path | None = None
        if bubbles is not None:
            bubbles_path = Path(bubbles)
            if not bubbles_path.exists():
                raise FileNotFoundError(f"Bubbles file not found: {bubbles_path}")

        if output_path is None:
            if self.ctx is not None:
                output = self.ctx.output_dir / "talking_avatar.mp4"
            else:
                output = Path("talking_avatar.mp4")
        else:
            output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        if self.ctx is not None:
            self.ctx.ensure_dirs()

        screen_info = probe_media(screen)
        if screen_info.width <= 0 or screen_info.height <= 0:
            raise TalkingAvatarError(f"Could not read video dimensions: {screen}")
        audio_duration = _audio_duration(audio)

        work_dir = self._work_dir(output)
        word_timings, detected_language = self._resolve_words(audio, work_dir, words)

        engine = requested_engine
        script_source: str | None = None
        raw_cues: list[Any] = []
        stress_upgraded = 0
        hybrid_fixes = {"closure": 0, "open": 0, "rounded": 0}
        hints: AlignHints | None = None

        if requested_engine == "hybrid":
            if align_hints is not None:
                try:
                    hints = load_hints(align_hints)
                except HintsError as exc:
                    raise TalkingAvatarError(str(exc)) from exc
            else:
                python = find_lipsync_python()
                if python is None:
                    logger.warning(
                        "talking_avatar_lipsync_missing",
                        expected=str(default_lipsync_python()),
                        setup=LIPSYNC_VENV_HINT,
                        fallback="rhubarb",
                    )
                    engine = "rhubarb"
                else:
                    assert script is not None
                    hints = generate_align_hints(
                        audio,
                        script,
                        work_dir,
                        python=python,
                    )
            if hints is not None:
                engine = "hybrid"

        timeline: list[VisemeFrame] = []
        if engine in {"rhubarb", "hybrid"}:
            try:
                rhubarb_binary = find_rhubarb_binary()
            except RhubarbError as exc:
                logger.warning(
                    "talking_avatar_rhubarb_missing",
                    error=str(exc),
                    fallback="loudness",
                )
                engine = "loudness"
                hints = None
            else:
                script_path: Path | None = None
                if script is not None:
                    script_path = Path(script)
                    script_source = "file"
                else:
                    transcript = " ".join(word.text for word in word_timings).strip()
                    if transcript:
                        script_path = work_dir / "rhubarb-script.txt"
                        script_path.write_text(transcript, encoding="utf-8")
                        script_source = "whisper"
                try:
                    mouth_cues, _payload = extract_mouth_cues(
                        audio,
                        work_dir,
                        script_path=script_path,
                        binary=rhubarb_binary,
                    )
                except RhubarbError as exc:
                    raise TalkingAvatarError(str(exc)) from exc
                raw_cues = [cue.as_dict() for cue in mouth_cues]
                stress_stats: dict[str, Any] = {}
                envelope = extract_rms_envelope(audio) if (bounce or stress_open) else None
                if engine == "hybrid" and hints is not None:
                    timeline = build_hybrid_timeline(
                        mouth_cues,
                        hints,
                        duration=audio_duration,
                        fps=self.fps,
                        lead=float(lead),
                        envelope=envelope,
                        rms_fps=RMS_FPS,
                        bounce=bool(bounce),
                        stress_open=bool(stress_open),
                        stress_percentile=float(stress_percentile),
                        stats=stress_stats,
                        words=word_timings,
                    )
                    hybrid_fixes = {
                        "closure": int(stress_stats.get("hybrid_closure_fixes", 0)),
                        "open": int(stress_stats.get("hybrid_open_fixes", 0)),
                        "rounded": int(stress_stats.get("hybrid_rounded_fixes", 0)),
                    }
                else:
                    timeline = build_rhubarb_timeline(
                        mouth_cues,
                        duration=audio_duration,
                        fps=self.fps,
                        lead=float(lead),
                        envelope=envelope,
                        rms_fps=RMS_FPS,
                        bounce=bool(bounce),
                        stress_open=bool(stress_open),
                        stress_percentile=float(stress_percentile),
                        stats=stress_stats,
                    )
                    engine = "rhubarb"
                stress_upgraded = int(stress_stats.get("stress_upgraded", 0))

        if engine == "loudness":
            envelope = extract_rms_envelope(audio)
            timeline = build_viseme_timeline(
                word_timings,
                envelope,
                duration=audio_duration,
                fps=self.fps,
                rms_fps=RMS_FPS,
                quiet_threshold=self.quiet_threshold,
                language=detected_language,
            )
        if not timeline:
            raise TalkingAvatarError("Audio is too short to build a viseme timeline")

        resolved_bubbles: list[Bubble] = []
        resolved_actions: list[AvatarAction] = []
        if bubbles_path is not None:
            try:
                specs = load_bubbles(bubbles_path)
                resolved_bubbles = resolve_bubbles(specs, word_timings)
                resolved_actions = resolve_actions(specs, word_timings, resolved_bubbles)
            except BubbleError as exc:
                raise TalkingAvatarError(str(exc)) from exc

        avatar = Path(avatar_path) if avatar_path is not None else self.avatar_path
        if avatar is None:
            avatar = default_avatar_path()
        avatar = Path(avatar)
        if not avatar.exists():
            raise FileNotFoundError(f"Avatar not found: {avatar}")

        avatar_size = max(2, int(round(screen_info.height * float(size))))
        renderer = AvatarRenderer(avatar, avatar_size)
        overlay_x, overlay_y = _overlay_xy(
            position,
            output_width=screen_info.width,
            output_height=screen_info.height,
            avatar_size=renderer.size,
            padding=renderer.padding,
            margin=int(margin),
        )
        frame_size = renderer.frame_size
        stage_size: tuple[int, int] | None = None
        if resolved_bubbles:
            stage_width = max(frame_size, int(round(renderer.size * 2.4)))
            stage_height = max(frame_size, int(round(renderer.size * 1.7)))
            stage_width += stage_width % 2
            stage_height += stage_height % 2
            stage_size = (stage_width, stage_height)
            overlay_x -= stage_width - frame_size
            overlay_y -= stage_height - frame_size
        overlay_size = stage_size or (frame_size, frame_size)

        sidecar = output.with_suffix(".json")
        payload = _timeline_payload(
            timeline,
            fps=self.fps,
            duration=audio_duration,
            words=word_timings,
        )
        payload.update(
            {
                "input": str(screen),
                "audio": str(audio),
                "output": str(output),
                "avatar": str(avatar),
                "position": position,
                "size": float(size),
                "margin": int(margin),
                "seed": int(seed),
                "avatar_pixels": renderer.size,
                "engine": engine,
                "lead": float(lead),
                "script_source": script_source,
                "align_hints": str(align_hints) if align_hints is not None else None,
                "hybrid_fixes": hybrid_fixes,
                "cues": raw_cues,
                "crossfade": bool(crossfade),
                "bounce": bool(bounce),
                "stress_open": bool(stress_open),
                "stress_upgraded": stress_upgraded,
                "stress_percentile": float(stress_percentile),
                "motion": bool(motion),
                "bubbles": [
                    {
                        "text": bubble.text,
                        "start": round(bubble.start, 6),
                        "end": round(bubble.end, 6),
                    }
                    for bubble in resolved_bubbles
                ],
                "actions": [
                    {
                        "type": action.type,
                        "start": round(action.start, 6),
                        "end": round(action.end, 6),
                    }
                    for action in resolved_actions
                ],
            }
        )
        sidecar.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        cmd = _ffmpeg_command(
            screen_path=screen,
            audio_path=audio,
            output_path=output,
            frame_size=overlay_size,
            overlay_x=overlay_x,
            overlay_y=overlay_y,
            duration=audio_duration,
            fps=self.fps,
        )
        frames = iter_avatar_frames(
            renderer,
            timeline,
            fps=self.fps,
            seed=int(seed),
            duration=audio_duration,
            crossfade=bool(crossfade),
            bounce=bool(bounce),
            motion=bool(motion),
            bubble_times=bubble_start_times(resolved_bubbles),
            bubbles=resolved_bubbles,
            actions=resolved_actions,
            stage_size=stage_size,
        )
        logger.info(
            "talking_avatar_render_start",
            screen=str(screen),
            audio=str(audio),
            output=str(output),
            frames=len(timeline),
            avatar_pixels=renderer.size,
            duration=audio_duration,
        )
        asyncio.run(_pipe_frames(cmd, frames))

        if not output.exists() or output.stat().st_size == 0:
            raise TalkingAvatarError(f"FFmpeg produced no output at {output}")

        if self.ctx is not None:
            self.ctx.manifest().stage_done(
                "talking_avatar",
                {"video": str(output), "timeline": str(sidecar)},
            )
            self.ctx.store.save()

        logger.info(
            "talking_avatar_complete",
            output=str(output),
            timeline=str(sidecar),
            frames=len(timeline),
        )
        return {
            "output": str(output),
            "timeline": str(sidecar),
            "avatar": str(avatar),
            "position": position,
            "size": float(size),
            "margin": int(margin),
            "seed": int(seed),
            "duration": audio_duration,
            "frames": len(timeline),
            "words": len(word_timings),
            "engine": engine,
            "lead": float(lead),
            "script_source": script_source,
            "align_hints": str(align_hints) if align_hints is not None else None,
            "hybrid_fixes": hybrid_fixes,
            "cues": len(raw_cues),
            "stress_open": bool(stress_open),
            "stress_upgraded": stress_upgraded,
            "motion": bool(motion),
            "bubbles": len(resolved_bubbles),
            "actions": len(resolved_actions),
        }


def render_talking_avatar(
    screen_path: Path | str,
    audio_path: Path | str,
    output_path: Path | str,
    *,
    words: Sequence[Any] | None = None,
    avatar_path: Path | str | None = None,
    position: str = DEFAULT_POSITION,
    size: float = DEFAULT_SIZE,
    margin: int = DEFAULT_MARGIN,
    seed: int = DEFAULT_SEED,
    lipsync: str = "hybrid",
    script: Path | str | None = None,
    align_hints: Path | str | None = None,
    lead: float = DEFAULT_LEAD,
    crossfade: bool = True,
    bounce: bool = False,
    stress_open: bool = True,
    stress_percentile: float = DEFAULT_STRESS_PERCENTILE,
    motion: bool = True,
    bubbles: Path | str | None = None,
) -> dict[str, Any]:
    """Convenience wrapper around :class:`TalkingAvatarSkill`."""
    return TalkingAvatarSkill().run(
        screen_path,
        audio_path,
        output_path,
        words=words,
        avatar_path=avatar_path,
        position=position,
        size=size,
        margin=margin,
        seed=seed,
        lipsync=lipsync,
        script=script,
        align_hints=align_hints,
        lead=lead,
        crossfade=crossfade,
        bounce=bounce,
        stress_open=stress_open,
        stress_percentile=stress_percentile,
        motion=motion,
        bubbles=bubbles,
    )


__all__ = [
    "ALIGN_HINTS_CACHE_VERSION",
    "DEFAULT_AVATAR_RELATIVE",
    "DEFAULT_MARGIN",
    "DEFAULT_POSITION",
    "DEFAULT_SEED",
    "DEFAULT_SIZE",
    "LIPSYNC_ENGINES",
    "LIPSYNC_PYTHON_ENV",
    "LIPSYNC_VENV_DIR",
    "LIPSYNC_VENV_HINT",
    "POSITIONS",
    "TalkingAvatarError",
    "TalkingAvatarSkill",
    "align_hints_cache_path",
    "default_avatar_path",
    "default_lipsync_python",
    "extract_rms_envelope",
    "find_lipsync_python",
    "generate_align_hints",
    "render_talking_avatar",
    "repository_root",
    "run_align_script",
    "transcribe_speech",
    "transcribe_words",
]
