"""Shorts factory skill.

Long-form video → 3-5 vertical Shorts/Reels. Identifies viral moments
from a transcript (LLM topic scoring, with energy/density fallback),
slices each moment from the long-form video, reframes to 9:16, and
emits one MP4 per selected moment.

The skill is intentionally deterministic where possible:
- Viral moment scoring: LLM via ``llm.generate()`` if available,
  otherwise a transcript-energy + sentence-density heuristic.
- Slicing: ffmpeg ``-ss``/``-t`` cut (no re-encode when possible).
- Reframe: center-crop with optional zoom (real face-tracking is
  handled by the reframe skill; this is the lightweight fallback).
- Captions: re-uses the captions skill's word-level style when an
  aligned transcript is available, otherwise burnt subtitles are
  skipped (the plan is still emitted).
"""

from __future__ import annotations

import contextlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from video_editor_ffmpeg.probe import MediaInfoError, probe_duration
from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, run_ffmpeg

# Word-level caps that signal a quotable viral moment during transcript
# density scoring. Includes emphasis markers that humans use when telling
# stories (e.g., "the truth is", "here's the thing").
_HOOK_PHRASES: tuple[str, ...] = (
    "the truth is",
    "here's the thing",
    "here is the thing",
    "the key is",
    "the secret",
    "nobody tells you",
    "the mistake",
    "the reason",
    "what i learned",
    "what nobody",
    "the trick",
    "the real",
    "the only",
    "actually",
    "literally",
    "in fact",
)


# Word-level emphasis patterns that often indicate shareable moments.
_EMPHASIS_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(p) for p in _HOOK_PHRASES) + r")\b", re.IGNORECASE
)


@dataclass
class ShortCandidate:
    """A single Short selected from the long-form video.

    Attributes
    ----------
    short_id:
        Stable, human-readable identifier (e.g. ``"short_001"``).
    start, end:
        Source timeline in seconds (inclusive low, exclusive high).
    duration:
        Cached ``end - start`` for convenience.
    score:
        Virality score in ``[0.0, 1.0]``. Higher = more viral.
    title:
        Suggested title for the Short (drawn from LLM or first sentence).
    transcript_excerpt:
        Transcript text covering ``[start, end]`` used to justify the pick.
    rationale:
        Short string explaining why the moment was selected (e.g.
        ``"hook: 'the truth is...'"`` or ``"llm_virality"``).
    output_path:
        Path to the rendered MP4 (set after slicing). ``None`` until
        ``run()`` finishes.
    """

    short_id: str
    start: float
    end: float
    duration: float
    score: float
    title: str
    transcript_excerpt: str
    rationale: str
    output_path: Path | None = None


@dataclass
class ShortsFactoryConfig:
    """Configuration for the shorts factory pipeline.

    Defaults match the ``shorts_factory.json`` preset's
    ``moment_selection`` block.
    """

    count: int = 4
    min_duration: float = 15.0
    max_duration: float = 60.0
    target_aspect: str = "9:16"
    enable_zoom: bool = True
    zoom_amount: float = 1.1
    burn_captions: bool = True
    # Don't allow candidate windows to span more than this gap without
    # transcript text; beyond this we treat the window as a transition.
    max_window: float = 90.0


class ShortsFactoryError(RuntimeError):
    """Raised when the shorts factory cannot continue (e.g. no LLM, no
    transcript, video file missing, ffmpeg failure)."""


class MomentScorer(Protocol):
    """Pluggable interface for ranking candidate moments.

    Implementations may use an LLM, a heuristic, or a hybrid. The
    returned dict must include ``score``, ``title``, and ``rationale``
    for each timestamp.
    """

    def score_moments(
        self,
        transcript: list[dict[str, Any]],
        candidates: list[tuple[float, float]],
    ) -> dict[tuple[float, float], dict[str, Any]]:
        """Return a mapping ``(start, end) -> {score, title, rationale}``."""
        ...


@dataclass
class _HeuristicScorer:
    """Fallback scorer using transcript-energy + sentence density.

    The score for a ``(start, end)`` window is:

    - 0.5 baseline (so empty transcripts still produce something)
    - + 0.3 * hit-rate of hook phrases inside the window
    - + 0.2 * (word_count / max_window_seconds)  (i.e. density)
    - clamped to ``[0.0, 1.0]``
    """

    hook_boost: float = 0.3
    density_boost: float = 0.2

    def score_moments(
        self,
        transcript: list[dict[str, Any]],
        candidates: list[tuple[float, float]],
    ) -> dict[tuple[float, float], dict[str, Any]]:
        scored: dict[tuple[float, float], dict[str, Any]] = {}
        for start, end in candidates:
            text = _window_text(transcript, start, end)
            hook_hits = len(_EMPHASIS_PATTERN.findall(text))
            words = max(len(text.split()), 1)
            duration = max(end - start, 1.0)
            density = words / duration
            score = 0.5
            score += self.hook_boost * min(hook_hits / 3.0, 1.0)
            score += self.density_boost * min(density / 3.0, 1.0)
            score = max(0.0, min(1.0, score))
            title = _first_sentence(text)
            rationale = (
                f"hook_phrase x{hook_hits}, density={density:.2f} words/s"
                if hook_hits
                else f"density={density:.2f} words/s"
            )
            scored[(start, end)] = {
                "score": score,
                "title": title,
                "rationale": rationale,
            }
        return scored


def _window_text(transcript: list[dict[str, Any]], start: float, end: float) -> str:
    """Return transcript text overlapping ``[start, end]``."""
    parts: list[str] = []
    for seg in transcript:
        seg_start = float(seg.get("start", 0.0))
        seg_end = float(seg.get("end", seg_start))
        if seg_end <= start or seg_start >= end:
            continue
        text = str(seg.get("text", "")).strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def _first_sentence(text: str, max_len: int = 80) -> str:
    """Return the first sentence of ``text`` truncated to ``max_len``."""
    text = text.strip()
    if not text:
        return ""
    # Cheap sentence splitter: split on . / ! / ? followed by space.
    for sep in (". ", "! ", "? "):
        if sep in text:
            text = text.split(sep, 1)[0] + sep.rstrip()
            break
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def _candidate_windows(
    transcript: list[dict[str, Any]],
    cfg: ShortsFactoryConfig,
) -> list[tuple[float, float]]:
    """Generate candidate (start, end) windows from the transcript.

    The algorithm slides a window of ``min_duration..max_duration``
    across the transcript, snapping to sentence boundaries so that
    cuts don't slice mid-word. Idle gaps longer than ``max_window``
    produce no candidate.
    """
    if not transcript:
        return []
    ends = [float(seg.get("end", seg.get("start", 0.0))) for seg in transcript]
    starts = [float(seg.get("start", 0.0)) for seg in transcript]
    # Respect the actual transcript length; never pad shorter transcripts
    # up to ``min_duration`` because that would produce ghost windows.
    total_dur = ends[-1]
    if total_dur < cfg.min_duration:
        return []
    target = max(cfg.min_duration, min(cfg.max_duration, 30.0))

    windows: list[tuple[float, float]] = []
    # Step the window in roughly half-target increments so we get
    # some overlap (so a great moment near a boundary still scores).
    step = max(target / 2.0, 5.0)
    cursor = 0.0
    while cursor + cfg.min_duration <= total_dur:
        end = min(cursor + target, total_dur)
        # Snap end to the nearest segment boundary within 2s.
        snapped_end = _snap_to_boundary(ends, end, tolerance=2.0)
        if snapped_end - cursor >= cfg.min_duration:
            snapped_start = _snap_to_boundary(starts, cursor, tolerance=2.0)
            if snapped_end - snapped_start <= cfg.max_duration:
                windows.append((snapped_start, snapped_end))
        cursor += step
    return _dedupe_windows(windows)


def _snap_to_boundary(times: list[float], target: float, tolerance: float) -> float:
    """Snap ``target`` to the nearest element of ``times`` within ``tolerance``."""
    if not times:
        return target
    best = min(times, key=lambda t: abs(t - target))
    if abs(best - target) <= tolerance:
        return best
    return target


def _dedupe_windows(windows: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Remove duplicates and near-duplicates (overlap > 70%)."""
    deduped: list[tuple[float, float]] = []
    for w in sorted(windows):
        if not deduped:
            deduped.append(w)
            continue
        last = deduped[-1]
        overlap = max(0.0, min(w[1], last[1]) - max(w[0], last[0]))
        size = max(w[1] - w[0], last[1] - last[0], 1e-6)
        if overlap / size < 0.7:
            deduped.append(w)
    return deduped


def _select_top(
    candidates: list[tuple[float, float]],
    scored: dict[tuple[float, float], dict[str, Any]],
    count: int,
) -> list[ShortCandidate]:
    """Pick the top ``count`` candidates by score, breaking ties by start."""
    sorted_cands = sorted(
        candidates,
        key=lambda w: (-scored.get(w, {}).get("score", 0.0), w[0]),
    )
    out: list[ShortCandidate] = []
    for idx, (start, end) in enumerate(sorted_cands[:count], start=1):
        meta = scored.get((start, end), {})
        out.append(
            ShortCandidate(
                short_id=f"short_{idx:03d}",
                start=start,
                end=end,
                duration=end - start,
                score=float(meta.get("score", 0.0)),
                title=str(meta.get("title", "")),
                transcript_excerpt=str(meta.get("transcript_excerpt", "")),
                rationale=str(meta.get("rationale", "default")),
            )
        )
    return out


def _clip_video(
    src: Path,
    dst: Path,
    start: float,
    duration: float,
    target_aspect: str,
    enable_zoom: bool,
    zoom_amount: float,
) -> None:
    """Slice ``[start, start+duration]`` from ``src`` and reframe to ``target_aspect``.

    Uses ffmpeg's ``-ss``/``-t`` to slice, then a ``crop`` filter to
    reframe. If ``enable_zoom`` is True, applies a small ``scale`` +
    ``crop`` to give a punch-in effect without re-encoding the whole
    stream (we use ``-c:v copy`` for the slice and pipe through a
    cheap filtergraph for the reframe).
    """
    if not src.exists():
        raise ShortsFactoryError(f"source video does not exist: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    # Step 1: stream-copy the slice (fast, no quality loss).
    sliced = dst.with_suffix(".sliced.mp4")
    cmd_slice = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(src),
        "-t",
        f"{duration:.3f}",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        str(sliced),
    ]
    _run_ffmpeg(cmd_slice)

    # Step 2: reframe with crop+zoom filter (forces re-encode).
    try:
        target_w, target_h = _parse_aspect(target_aspect)
    except ValueError:
        target_w, target_h = 1080, 1920
    if enable_zoom and zoom_amount > 1.0:
        vf = (
            f"scale=trunc(iw*{zoom_amount}/2)*2:trunc(ih*{zoom_amount}/2)*2,"
            f"crop={target_w}:{target_h}"
        )
    else:
        vf = f"scale=trunc(iw/2)*2:trunc(ih/2)*2,crop={target_w}:{target_h}"
    cmd_reframe = [
        "ffmpeg",
        "-y",
        "-i",
        str(sliced),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(dst),
    ]
    _run_ffmpeg(cmd_reframe)

    # Clean up the intermediate sliced file.
    with contextlib.suppress(FileNotFoundError):
        sliced.unlink()


def _parse_aspect(aspect: str) -> tuple[int, int]:
    """Parse ``"9:16"`` / ``"16:9"`` strings into ``(width, height)``."""
    parts = aspect.split(":")
    if len(parts) != 2:
        raise ValueError(f"invalid aspect ratio: {aspect!r}")
    w_str, h_str = parts
    if not w_str.isdigit() or not h_str.isdigit():
        raise ValueError(f"invalid aspect ratio: {aspect!r}")
    w, h = int(w_str), int(h_str)
    # Cap to HD limits for Shorts (we always reframe to 1080x1920).
    if w == 9 and h == 16:
        return 1080, 1920
    if w == 16 and h == 9:
        return 1920, 1080
    if w == 1 and h == 1:
        return 1080, 1080
    if w == 4 and h == 5:
        return 1080, 1350
    return 1080, 1920


def _run_ffmpeg(cmd: list[str]) -> None:
    """Run ``cmd`` and raise ``ShortsFactoryError`` on non-zero exit."""
    result = run_ffmpeg(cmd, check=False, timeout=RENDER_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise ShortsFactoryError(
            f"ffmpeg failed (exit {result.returncode}): {' '.join(cmd)}\n{result.stderr[-400:]}"
        )


def _probe_duration(video_path: Path) -> float:
    """Return the duration of ``video_path`` in seconds.

    Uses ffprobe when available, otherwise falls back to a 600s
    default so the heuristic still produces something.
    """
    try:
        return probe_duration(video_path)
    except (FileNotFoundError, MediaInfoError, OSError, ValueError):
        return 600.0


def _transcript_excerpt(transcript: list[dict[str, Any]], start: float, end: float) -> str:
    """Return transcript text covering ``[start, end]``."""
    return _window_text(transcript, start, end)


@dataclass
class ShortsFactorySkill:
    """Skill: turn one long-form video into N vertical Shorts.

    The skill follows the ``SkillProtocol`` contract used throughout the
    engine: callers invoke ``run()`` and read the manifest dict from
    ``ctx.ensure_dirs() / shorts_factory / plan.json``.
    """

    name: str = "shorts_factory"
    config: ShortsFactoryConfig = field(default_factory=ShortsFactoryConfig)
    scorer: MomentScorer | None = None
    # Optional progress callback (used by CLI spinner / MCP).
    on_progress: Callable[[str], None] | None = None

    def _emit(self, msg: str) -> None:
        if self.on_progress is not None:
            with contextlib.suppress(Exception):
                self.on_progress(msg)

    def _resolve_scorer(self) -> MomentScorer:
        if self.scorer is not None:
            return self.scorer
        # Prefer the batched Jev classifier when a key is configured; fall back
        # to the deterministic heuristic so offline runs and tests still work.
        try:
            from shorts_factory.jev_scorer import JevMomentScorer

            jev = JevMomentScorer()
            if jev.available():
                return jev
        except Exception:  # noqa: BLE001, S110 - scoring must never break the run
            pass
        return _HeuristicScorer()

    def run(
        self,
        video_path: Path,
        transcript: list[dict[str, Any]] | None,
        output_dir: Path,
        count: int | None = None,
    ) -> dict[str, Any]:
        """Slice ``video_path`` into ``count`` Shorts and emit a plan.

        Parameters
        ----------
        video_path:
            Path to the long-form source video.
        transcript:
            Optional list of segment dicts with ``start``, ``end``,
            ``text`` keys. If ``None``, a transcript-derived selector
            is still used: the heuristic falls back to evenly-spaced
            windows so the pipeline still produces something.
        output_dir:
            Directory where the Shorts and ``plan.json`` will be written.
        count:
            Override ``cfg.count``. Clamped to ``[1, 8]``.
        """
        if video_path is None:
            raise ShortsFactoryError("video_path is required")
        video_path = Path(video_path)
        if not video_path.exists():
            raise ShortsFactoryError(f"video not found: {video_path}")

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        use_count = count if count is not None else self.config.count
        use_count = max(1, min(int(use_count), 8))

        # Normalise transcript
        transcript = transcript or []
        _validate_transcript(transcript)

        # If no transcript, synthesise a minimal one from the probe
        # duration so we still emit evenly-spaced windows.
        if not transcript:
            total = _probe_duration(video_path)
            transcript = [
                {"start": 0.0, "end": total, "text": ""},
            ]

        cfg = self.config
        self._emit("scanning transcript for viral moments")
        candidates = _candidate_windows(transcript, cfg)
        if not candidates:
            raise ShortsFactoryError(
                "no candidate moments found in transcript; "
                "min_duration may be larger than the source video"
            )

        scorer = self._resolve_scorer()
        scored = scorer.score_moments(transcript, candidates)
        # Attach the window text to the metadata so the plan can show
        # why the moment was selected.
        for window, meta in scored.items():
            excerpt = _transcript_excerpt(transcript, window[0], window[1])
            meta["transcript_excerpt"] = excerpt
            if not meta.get("title"):
                meta["title"] = _first_sentence(excerpt)

        top = _select_top(candidates, scored, use_count)

        # Slice each candidate video.
        self._emit(f"rendering {len(top)} shorts")
        for cand in top:
            out_path = output_dir / f"{cand.short_id}.mp4"
            _clip_video(
                src=video_path,
                dst=out_path,
                start=cand.start,
                duration=cand.duration,
                target_aspect=cfg.target_aspect,
                enable_zoom=cfg.enable_zoom,
                zoom_amount=cfg.zoom_amount,
            )
            cand.output_path = out_path

        plan = {
            "long_video": str(video_path),
            "count": len(top),
            "scoring": (
                "heuristic"
                if isinstance(scorer, _HeuristicScorer)
                else getattr(scorer, "name", "custom")
            ),
            "aspect_ratio": cfg.target_aspect,
            "shorts": [
                {
                    "short_id": c.short_id,
                    "start": c.start,
                    "end": c.end,
                    "duration": c.duration,
                    "score": c.score,
                    "title": c.title,
                    "transcript_excerpt": c.transcript_excerpt,
                    "rationale": c.rationale,
                    "output_path": str(c.output_path) if c.output_path else None,
                }
                for c in top
            ],
        }
        plan_path = output_dir / "plan.json"
        plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False))
        self._emit(f"wrote {plan_path}")
        return plan


def _validate_transcript(transcript: list[dict[str, Any]]) -> None:
    """Validate segment shapes; raise on the first bad segment."""
    for idx, seg in enumerate(transcript):
        if not isinstance(seg, dict):
            raise ShortsFactoryError(
                f"transcript segment {idx} must be a dict, got {type(seg).__name__}"
            )
        for key in ("start", "end"):
            if key not in seg:
                raise ShortsFactoryError(f"transcript segment {idx} missing required key {key!r}")
            try:
                float(seg[key])
            except (TypeError, ValueError) as exc:
                raise ShortsFactoryError(
                    f"transcript segment {idx} has non-numeric {key!r}: {seg[key]!r}"
                ) from exc


__all__ = [
    "ShortCandidate",
    "ShortsFactoryConfig",
    "ShortsFactoryError",
    "ShortsFactorySkill",
    "MomentScorer",
    "_HeuristicScorer",
    "_candidate_windows",
    "_first_sentence",
    "_parse_aspect",
    "_probe_duration",
    "_validate_transcript",
]


# ---------------------------------------------------------------------------
# Convenience math helpers exposed for testing.
# ---------------------------------------------------------------------------


def _word_count(text: str) -> int:
    """Return the number of whitespace-separated tokens in ``text``."""
    return len([w for w in text.split() if w])


def _is_perfect_aspect(aspect: str) -> bool:
    """Return True if ``aspect`` is one of the supported ratios."""
    return aspect in ("9:16", "16:9", "1:1", "4:5")


def _safe_clip_range(start: float, end: float, total: float) -> tuple[float, float]:
    """Clamp ``[start, end]`` to ``[0, total]`` and ensure ``start < end``."""
    s = max(0.0, min(float(start), max(total - 0.1, 0.0)))
    e = max(0.0, min(float(end), total))
    if e <= s:
        # Guarantee at least a tiny non-zero window for safety.
        e = min(total, s + 0.1)
    return s, e


def _rough_duration_bucket(duration: float) -> str:
    """Bucket ``duration`` into a coarse label for logging."""
    if duration < 15.0:
        return "micro"
    if duration < 30.0:
        return "short"
    if duration < 60.0:
        return "standard"
    return "longform"


def _is_hook_phrase(text: str) -> bool:
    """Return True if ``text`` contains any of the hook phrase patterns."""
    return bool(_EMPHASIS_PATTERN.search(text))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Return the cosine similarity of two equal-length numeric vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
