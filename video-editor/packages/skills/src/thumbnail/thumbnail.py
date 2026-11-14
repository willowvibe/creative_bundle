"""Thumbnail skill.

Extracts key frames from a final video, optionally uses an LLM (or a
heuristic) to pick the best candidate and suggest titles, and renders
the chosen thumbnail with an optional title overlay.

Layout (under ``ctx.output_dir``):

    thumbnail/
        candidate_001.jpg
        candidate_002.jpg
        ...
        candidate_NNN.jpg
        selected.jpg                  # the chosen candidate
        with_title.jpg                # selected + title overlay (if enabled)
        plan.json                     # manifest with scores + titles
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import structlog
from video_editor_core.shaped_text import (
    PngOverlay,
    linear_graph,
    needs_shaping,
    render_shaped_text,
)
from video_editor_ffmpeg.probe import MediaInfoError, probe_duration
from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, run_ffmpeg
from video_editor_llm import ClassifyClient, question_score, run_sync

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Min JPEG quality for the produced thumbnails.
_DEFAULT_JPEG_QUALITY = 4  # 2..31, lower is better; 4 is visually lossless

# Min acceptable candidate area (in pixels) so we don't ship tiny thumbs.
_MIN_CANDIDATE_WIDTH = 320
_MIN_CANDIDATE_HEIGHT = 180

# Default title-overlay typography (used when ``title_overlay`` is enabled).
_TITLE_OVERLAY_DEFAULTS: dict[str, Any] = {
    "font_size": 96,
    "font_color": "white",
    "border_width": 6,
    "border_color": "black",
    "shadow": True,
    "position": "bottom_center",
    "max_chars": 64,
}


# Sanitize a title for safe ffmpeg drawtext by stripping colons,
# quotes, backslashes, percent signs, slashes, and commas (the last
# two break drawtext's parser or filter syntax). drawtext's text=
# argument is space-delimited and breaks on unescaped colons.
_TITLE_BAD_CHARS = re.compile(r"[:'\"\\%/,]")


# Ordered rating scale (2-10 levels) used to score a candidate title's hook
# strength with the TypeSafe/Jev classifier. Index 0 = worst, last = best.
_TITLE_HOOK_LEVELS: list[str] = [
    "no hook - generic or forgettable",
    "weak - flat and easy to scroll past",
    "fair - some curiosity but not compelling",
    "strong - a clear reason to click",
    "irresistible - an immediate, must-click hook",
]


def _sanitize_title(raw: str) -> str:
    """Return ``raw`` with characters that break ffmpeg drawtext removed."""
    cleaned = _TITLE_BAD_CHARS.sub("", raw)
    return re.sub(r"\s+", " ", cleaned).strip()


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ThumbnailCandidate:
    """A single candidate thumbnail frame.

    Attributes
    ----------
    candidate_id:
        Stable human-readable identifier (e.g. ``"candidate_001"``).
    timestamp:
        Source timestamp in seconds.
    output_path:
        Path to the rendered JPG.
    score:
        Suitability score in ``[0.0, 1.0]``. Higher = better thumbnail.
    title:
        Suggested title for this thumbnail (LLM or empty).
    rationale:
        Short string explaining why the frame was chosen.
    """

    candidate_id: str
    timestamp: float
    output_path: Path
    score: float = 0.0
    title: str = ""
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "timestamp": self.timestamp,
            "output_path": str(self.output_path),
            "score": self.score,
            "title": self.title,
            "rationale": self.rationale,
        }


@dataclass
class ThumbnailConfig:
    """Configuration for the thumbnail extraction pipeline."""

    candidates: int = 8
    width: int = 1280
    height: int = 720
    jpeg_quality: int = _DEFAULT_JPEG_QUALITY
    title_overlay: bool = True
    title_font_size: int = _TITLE_OVERLAY_DEFAULTS["font_size"]
    title_font_color: str = _TITLE_OVERLAY_DEFAULTS["font_color"]
    title_border_width: int = _TITLE_OVERLAY_DEFAULTS["border_width"]
    title_border_color: str = _TITLE_OVERLAY_DEFAULTS["border_color"]
    title_max_chars: int = _TITLE_OVERLAY_DEFAULTS["max_chars"]
    # Optional override for the thumbnail style prompt (used by some
    # LLM scorers). Leave blank to use the default prompt.
    title_prompt: str = ""
    # Weight (0..1) given to the Jev title-hook score when re-ranking
    # candidates that carry a suggested title. 0 keeps the existing scorer
    # ranking; 1 selects purely by hook strength. Ignored when Jev is
    # unavailable (the base scorer order is used unchanged).
    title_hook_weight: float = 0.5
    # Suffix for the produced files. Default is ``.jpg``. Use ``.png``
    # if you need lossless; the rest of the pipeline uses JPG.
    suffix: str = ".jpg"


class ThumbnailError(RuntimeError):
    """Raised when the thumbnail pipeline cannot continue (e.g. ffmpeg
    failure, missing video, no candidates after scoring)."""


@runtime_checkable
class ThumbnailScorer(Protocol):
    """Pluggable interface for ranking candidate frames.

    Implementations may use an LLM (vision model), a CLIP-style
    similarity score, or a deterministic heuristic. The returned dict
    must include ``score``, ``title``, and ``rationale`` for each
    timestamp.
    """

    def score_candidates(
        self,
        video_path: Path,
        candidates: list[tuple[float, Path]],
    ) -> dict[float, dict[str, Any]]:
        """Return a mapping ``timestamp -> {score, title, rationale}``."""
        ...


# ---------------------------------------------------------------------------
# Default (heuristic) scorer
# ---------------------------------------------------------------------------


@dataclass
class _HeuristicScorer:
    """Fallback scorer: gives equal weight to each candidate.

    The heuristic produces a stable score based on the candidate's
    position in the timeline (middle frames tend to be the "meat" of
    a video, so they get a small boost) and the file size of the
    rendered JPG (a proxy for visual complexity — flat-colour intros
    are usually less compelling thumbnails).

    The score is clamped to ``[0.0, 1.0]``.
    """

    middle_boost: float = 0.2
    complexity_boost: float = 0.3

    def score_candidates(
        self,
        video_path: Path,
        candidates: list[tuple[float, Path]],
    ) -> dict[float, dict[str, Any]]:
        if not candidates:
            return {}
        positions = [ts for ts, _ in candidates]
        ts_min, ts_max = min(positions), max(positions)
        ts_range = max(ts_max - ts_min, 1e-6)

        # Use the largest JPG as the "most complex" reference.
        sizes = [p.stat().st_size for _, p in candidates if p.exists()]
        max_size = max(sizes) if sizes else 1

        scored: dict[float, dict[str, Any]] = {}
        for ts, path in candidates:
            middle_distance = abs((ts - ts_min) / ts_range - 0.5)
            middle_factor = 1.0 - (middle_distance * 2.0)  # 1.0 at center, 0 at edges
            size = path.stat().st_size if path.exists() else 0
            complexity_factor = (size / max_size) if max_size else 0.0
            score = 0.5
            score += self.middle_boost * max(0.0, middle_factor)
            score += self.complexity_boost * complexity_factor
            score = max(0.0, min(1.0, score))
            scored[ts] = {
                "score": score,
                "title": "",
                "rationale": (
                    f"heuristic: middle={middle_factor:.2f}, complexity={complexity_factor:.2f}"
                ),
            }
        return scored


# ---------------------------------------------------------------------------
# Frame extraction helpers
# ---------------------------------------------------------------------------


def _probe_duration(video_path: Path) -> float:
    """Return the duration of ``video_path`` in seconds.

    Falls back to ``600.0`` (10 minutes) when ``ffprobe`` is missing
    or returns a non-numeric value.
    """
    try:
        return probe_duration(video_path)
    except (FileNotFoundError, MediaInfoError, OSError, ValueError):
        return 600.0


def _candidate_timestamps(duration: float, count: int) -> list[float]:
    """Return ``count`` evenly-spaced timestamps in ``[0, duration]``.

    The first/last candidates are pinned to the start/end so callers
    always get bookends.
    """
    if count <= 0:
        return []
    if count == 1:
        return [duration / 2.0]
    if duration <= 0:
        return [0.0]
    step = duration / (count + 1)
    return [step * i for i in range(1, count + 1)]


def _clamp_dimensions(width: int, height: int) -> tuple[int, int]:
    """Make sure the requested thumbnail dimensions are even (H.264
    quirk) and at least the minimum candidate size."""
    width = max(width, _MIN_CANDIDATE_WIDTH)
    height = max(height, _MIN_CANDIDATE_HEIGHT)
    # Round down to even.
    return (width // 2) * 2, (height // 2) * 2


def _extract_frame(
    video_path: Path,
    output_path: Path,
    timestamp: float,
    width: int,
    height: int,
    jpeg_quality: int,
    suffix: str,
) -> None:
    """Extract a single frame at ``timestamp`` to ``output_path``."""
    if not video_path.exists():
        raise ThumbnailError(f"video not found: {video_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Use -ss BEFORE -i for fast seeking (keyframe-accurate enough for
    # thumbnail purposes).
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black",
        "-q:v",
        str(max(1, min(jpeg_quality, 31))),
        str(output_path.with_suffix(suffix)),
    ]
    _run_ffmpeg(cmd)


def _render_title_overlay(
    image_path: Path,
    output_path: Path,
    title: str,
    cfg: ThumbnailConfig,
    width: int,
    height: int,
) -> None:
    """Render a copy of ``image_path`` with a drawtext title overlay.

    The title is drawn at the bottom center with a black border for
    legibility. drawtext is part of ffmpeg's ``vf`` filter set; we
    escape the text safely via :func:`_sanitize_title`.
    """
    if not title:
        # Nothing to overlay: emit a copy via stdout (cheap).
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(image_path),
            "-c",
            "copy",
            str(output_path),
        ]
        _run_ffmpeg(cmd)
        return
    # Position: y = h - font_size - 32 (16px margin).
    y_pos = max(8, height - cfg.title_font_size - 32)
    if needs_shaping(title):
        # drawtext breaks Telugu/Devanagari/... conjuncts (#322): draw a shaped PNG instead.
        png = output_path.with_name(f"{output_path.stem}.title.png")
        shaped = render_shaped_text(
            re.sub(r"\s+", " ", title).strip()[: cfg.title_max_chars],
            png,
            font_size=cfg.title_font_size,
            color=cfg.title_font_color,
            border_width=cfg.title_border_width,
            border_color=cfg.title_border_color,
        )
        try:
            _run_ffmpeg(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(image_path),
                    "-vf",
                    linear_graph([PngOverlay(shaped.path, "(W-w)/2", str(y_pos))]),
                    "-q:v",
                    str(max(1, min(cfg.jpeg_quality, 31))),
                    str(output_path),
                ]
            )
        finally:
            png.unlink(missing_ok=True)
        return
    safe = _sanitize_title(title)
    if not safe:
        return
    truncated = safe[: cfg.title_max_chars]
    drawtext = (
        f"drawtext=text='{truncated}':"
        "fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
        f"fontsize={cfg.title_font_size}:"
        f"fontcolor={cfg.title_font_color}:"
        f"borderw={cfg.title_border_width}:"
        f"bordercolor={cfg.title_border_color}:"
        "shadowcolor=black@0.6:shadowx=2:shadowy=2:"
        f"x=(w-tw)/2:y={y_pos}"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(image_path),
        "-vf",
        drawtext,
        "-q:v",
        str(max(1, min(cfg.jpeg_quality, 31))),
        str(output_path),
    ]
    _run_ffmpeg(cmd)


def _run_ffmpeg(cmd: list[str]) -> None:
    """Run ``cmd`` and raise ``ThumbnailError`` on non-zero exit."""
    result = run_ffmpeg(cmd, check=False, timeout=RENDER_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise ThumbnailError(
            f"ffmpeg failed (exit {result.returncode}): {' '.join(cmd)}\n{result.stderr[-400:]}"
        )


# ---------------------------------------------------------------------------
# Title selection
# ---------------------------------------------------------------------------


def _best_title(candidates: list[ThumbnailCandidate]) -> str:
    """Pick the best title from the scored candidates.

    Empty titles are skipped. If no candidate has a title, returns an
    empty string (so the caller can skip the overlay).
    """
    with_titles = [c for c in candidates if c.title]
    if not with_titles:
        return ""
    return max(with_titles, key=lambda c: c.score).title


def score_titles(
    titles: list[str],
    *,
    classify_client: ClassifyClient | None = None,
) -> dict[str, float]:
    """Score title hook strength with the TypeSafe/Jev classifier.

    Sends every distinct non-empty title to Jev as one batched ``score``
    question (see :data:`_TITLE_HOOK_LEVELS`) and returns a mapping
    ``title -> normalised score in [0.0, 1.0]``. Exactly one classify call is
    made (Jev judges all questions in a single request).

    Jev is a text classifier: only the ``title`` string is judged. Returns an
    empty dict when ``titles`` is empty, when no classifier is available
    (e.g. ``TYPESAFE_API_KEY`` unset), or when the call fails — callers then
    keep their existing scorer untouched. An injected ``classify_client`` is
    never closed; a client built here owns its lifecycle and is closed.
    """
    unique = [t for t in dict.fromkeys(titles) if t]
    if not unique:
        return {}

    client = classify_client
    owns_client = client is None
    if client is None:
        try:
            client = ClassifyClient.from_config()
        except Exception:  # noqa: BLE001 - config probing must not raise
            client = ClassifyClient()

    try:
        if not client.available():
            return {}
        questions: dict[str, Any] = {
            f"t{i}": question_score(
                "How strong a thumbnail title hook is `title`?",
                _TITLE_HOOK_LEVELS,
                data={"title": title},
            )
            for i, title in enumerate(unique)
        }
        body = run_sync(client.classify({"state": "Thumbnail title review."}, questions))
        answers = body.get("answers") or {}
        span = max(len(_TITLE_HOOK_LEVELS) - 1, 1)
        scores: dict[str, float] = {}
        for i, title in enumerate(unique):
            answer = answers.get(f"t{i}") or {}
            raw = answer.get("score") if isinstance(answer, dict) else None
            if not isinstance(raw, (int, float)):
                continue
            scores[title] = max(0.0, min(1.0, float(raw) / span))
        logger.info("thumbnail_jev_title_scores", titles=len(unique), scored=len(scores))
        return scores
    except Exception as exc:  # noqa: BLE001 - fall back to existing scorer
        logger.warning("thumbnail_jev_title_scoring_failed", error=str(exc))
        return {}
    finally:
        if owns_client:
            run_sync(client.close())


def _select_candidate(
    candidates: list[ThumbnailCandidate],
    hook_scores: dict[str, float],
    hook_weight: float,
) -> ThumbnailCandidate:
    """Pick the best candidate, blending Jev title-hook scores when present.

    With no hook scores this is exactly ``max(candidates, key=score)`` (the
    historical behaviour). When hook scores exist, a candidate whose title was
    judged gets ``(1 - w) * frame_score + w * hook_score``; untitled candidates
    keep their frame score.
    """
    if not candidates:
        raise ThumbnailError("no candidates to select")
    if not hook_scores:
        return max(candidates, key=lambda c: c.score)
    weight = max(0.0, min(1.0, hook_weight))

    def _key(cand: ThumbnailCandidate) -> float:
        hook = hook_scores.get(cand.title) if cand.title else None
        if hook is None:
            return cand.score
        return (1.0 - weight) * cand.score + weight * hook

    return max(candidates, key=_key)


# ---------------------------------------------------------------------------
# ThumbnailSkill
# ---------------------------------------------------------------------------


@dataclass
class ThumbnailSkill:
    """Skill: extract key frames from a final video and pick the best.

    The skill follows the ``SkillProtocol`` contract used throughout
    the engine: callers invoke :meth:`run` and read the manifest dict
    from ``ctx.output_dir / "thumbnail" / "plan.json"``.
    """

    name: str = "thumbnail"
    config: ThumbnailConfig = field(default_factory=ThumbnailConfig)
    scorer: ThumbnailScorer | None = None
    on_progress: Callable[[str], None] | None = None
    # Optional TypeSafe/Jev client for title-hook re-ranking. When ``None``
    # (the default) one is built lazily from config/env; if unavailable the
    # existing scorer decides, so behaviour is unchanged without a key.
    classify_client: ClassifyClient | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _emit(self, msg: str) -> None:
        if self.on_progress is not None:
            with contextlib.suppress(Exception):
                self.on_progress(msg)

    def score_titles(self, titles: list[str]) -> dict[str, float]:
        """Delegate to :func:`score_titles` using this skill's client."""
        return score_titles(titles, classify_client=self.classify_client)

    def _resolve_scorer(self) -> ThumbnailScorer:
        if self.scorer is not None:
            return self.scorer
        return _HeuristicScorer()

    def _output_dir(self, ctx: Any) -> Path:
        """Return ``ctx.output_dir / "thumbnail"`` (creating it)."""
        if hasattr(ctx, "ensure_dirs"):
            ctx.ensure_dirs()
        out = Path(ctx.output_dir) / "thumbnail"
        out.mkdir(parents=True, exist_ok=True)
        return out

    # ------------------------------------------------------------------
    # Public run()
    # ------------------------------------------------------------------

    def run(
        self,
        ctx: Any,
        video_path: Path | None = None,
        candidates: int | None = None,
        title_overlay: bool | None = None,
    ) -> dict[str, Any]:
        """Run the thumbnail pipeline.

        Parameters
        ----------
        ctx:
            Job context with ``output_dir`` (and optionally
            ``ensure_dirs``).
        video_path:
            Path to the final video. Defaults to
            ``ctx.work_dir / "rough_cut.mp4"`` if it exists, otherwise
            ``ctx.output_dir / "final.mp4"``.
        candidates:
            Override ``cfg.candidates``. Clamped to ``[1, 32]``.
        title_overlay:
            Override ``cfg.title_overlay``.
        """
        out_dir = self._output_dir(ctx)

        # Resolve video path.
        if video_path is None:
            candidate_paths = [
                Path(ctx.work_dir) / "rough_cut.mp4",
                Path(ctx.output_dir) / "final.mp4",
            ]
            for p in candidate_paths:
                if p.exists():
                    video_path = p
                    break
        if video_path is None:
            raise ThumbnailError("video_path is required; could not infer from ctx")
        video_path = Path(video_path)
        if not video_path.exists():
            raise ThumbnailError(f"video not found: {video_path}")

        cfg = self.config
        use_count = candidates if candidates is not None else cfg.candidates
        use_count = max(1, min(int(use_count), 32))
        use_overlay = title_overlay if title_overlay is not None else cfg.title_overlay

        width, height = _clamp_dimensions(cfg.width, cfg.height)

        # 1. Pick timestamps.
        self._emit("probing video duration")
        duration = _probe_duration(video_path)
        if duration <= 0:
            raise ThumbnailError(f"video has zero duration: {video_path}")
        timestamps = _candidate_timestamps(duration, use_count)

        # 2. Extract frames.
        self._emit(f"extracting {len(timestamps)} candidate frames")
        rendered: list[ThumbnailCandidate] = []
        for idx, ts in enumerate(timestamps, start=1):
            cand_id = f"candidate_{idx:03d}"
            out_path = out_dir / f"{cand_id}{cfg.suffix}"
            _extract_frame(
                video_path=video_path,
                output_path=out_path,
                timestamp=ts,
                width=width,
                height=height,
                jpeg_quality=cfg.jpeg_quality,
                suffix=cfg.suffix,
            )
            rendered.append(
                ThumbnailCandidate(
                    candidate_id=cand_id,
                    timestamp=ts,
                    output_path=out_path,
                )
            )

        # 3. Score.
        self._emit("scoring candidates")
        scorer = self._resolve_scorer()
        scored = scorer.score_candidates(
            video_path,
            [(c.timestamp, c.output_path) for c in rendered],
        )
        for cand in rendered:
            meta = scored.get(cand.timestamp, {})
            cand.score = float(meta.get("score", 0.0))
            cand.title = str(meta.get("title", ""))
            cand.rationale = str(meta.get("rationale", ""))

        # 3b. Optional Jev title-hook pass (additive).
        # Only judge the titles the scorer produced; one batched call. Returns
        # {} when Jev is unavailable/failed, leaving the ranking untouched.
        hook_scores: dict[str, float] = {}
        titled = [c.title for c in rendered if c.title]
        if titled:
            self._emit(f"scoring {len(set(titled))} titles with Jev")
            hook_scores = self.score_titles(titled)

        # 4. Pick the best (Jev hook scores blended in when available).
        chosen = _select_candidate(rendered, hook_scores, cfg.title_hook_weight)
        self._emit(f"selected {chosen.candidate_id} (score={chosen.score:.2f})")

        # 5. Emit plan + selected copy.
        selected_path = out_dir / f"selected{cfg.suffix}"
        _copy_file(chosen.output_path, selected_path)

        overlay_path: Path | None = None
        # Prefer the chosen candidate's own title so the overlay matches the
        # frame; without Jev this equals ``_best_title`` (highest-score title).
        title = chosen.title or _best_title(rendered)
        if use_overlay and title:
            self._emit(f"rendering title overlay: {title!r}")
            overlay_path = out_dir / f"with_title{cfg.suffix}"
            _render_title_overlay(
                image_path=selected_path,
                output_path=overlay_path,
                title=title,
                cfg=cfg,
                width=width,
                height=height,
            )

        plan = {
            "video": str(video_path),
            "duration_seconds": duration,
            "candidates": [c.to_dict() for c in rendered],
            "title_hook_scores": hook_scores,
            "selected": {
                "candidate_id": chosen.candidate_id,
                "timestamp": chosen.timestamp,
                "score": chosen.score,
                "rationale": chosen.rationale,
                "output_path": str(selected_path),
                "title": chosen.title,
            },
            "title_overlay": {
                "enabled": bool(use_overlay and title),
                "title": title,
                "output_path": str(overlay_path) if overlay_path else None,
            },
            "scoring": ("heuristic" if isinstance(scorer, _HeuristicScorer) else "custom"),
        }
        plan_path = out_dir / "plan.json"
        plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False))
        self._emit(f"wrote {plan_path}")
        return plan


def _copy_file(src: Path, dst: Path) -> None:
    """Copy ``src`` to ``dst`` using a streaming chunked write."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as fin, dst.open("wb") as fout:
        while True:
            chunk = fin.read(64 * 1024)
            if not chunk:
                break
            fout.write(chunk)


__all__ = [
    "ThumbnailCandidate",
    "ThumbnailConfig",
    "ThumbnailError",
    "ThumbnailScorer",
    "ThumbnailSkill",
    "_HeuristicScorer",
    "_TITLE_HOOK_LEVELS",
    "_best_title",
    "_candidate_timestamps",
    "_clamp_dimensions",
    "_extract_frame",
    "_probe_duration",
    "_render_title_overlay",
    "_sanitize_title",
    "_select_candidate",
    "score_titles",
]


# ---------------------------------------------------------------------------
# Pure helpers exposed for testing.
# ---------------------------------------------------------------------------


def _round_even(n: int) -> int:
    """Round ``n`` down to the nearest even integer."""
    return (n // 2) * 2


def _percent_to_q(quality: int) -> int:
    """Map a 1-100 quality value to ffmpeg's ``-q:v`` 1-31 scale.

    ffmpeg's ``-q:v`` is inverted: 1 is highest quality, 31 is lowest.
    """
    quality = max(1, min(100, quality))
    # 100 -> 1, 1 -> 31 (linear map).
    return 1 + (100 - quality) * 30 // 99


def _viewbox(width: int, height: int) -> tuple[int, int]:
    """Return ``(width, height)`` clamped to the minimum candidate size."""
    return _clamp_dimensions(width, height)


def _score_to_color(score: float) -> str:
    """Map a ``[0.0, 1.0]`` score to a coarse traffic-light label."""
    if score >= 0.75:
        return "green"
    if score >= 0.5:
        return "yellow"
    return "red"


def _format_timestamp(ts: float) -> str:
    """Format ``ts`` (seconds) as ``HH:MM:SS.SSS`` for plan manifests."""
    if ts < 0:
        ts = 0.0
    hours = int(ts // 3600)
    minutes = int((ts % 3600) // 60)
    seconds = ts - hours * 3600 - minutes * 60
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"


def _safe_overlay_title(title: str, max_chars: int = 64) -> str:
    """Return a drawtext-safe title truncated to ``max_chars``."""
    return _sanitize_title(title)[:max_chars]


def _is_even(n: int) -> bool:
    """Return True if ``n`` is a positive even integer."""
    return n > 0 and n % 2 == 0
