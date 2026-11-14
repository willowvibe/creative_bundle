"""Rough cut skill: intake → transcribe → analyze → locked edit."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, cast

import structlog
from video_editor_asset_ingestion.scene_assets import forced_alignment_response_to_transcript
from video_editor_core.render_cache import SegmentCache, segment_cache_key
from video_editor_core.timeline import EDL, compute_timeline_segments, diff_edls
from video_editor_ffmpeg.cuts import apply_cuts, concat_segments
from video_editor_ffmpeg.normalize import normalize_audio
from video_editor_ffmpeg.probe import extract_audio, probe_media
from video_editor_ffmpeg.regions import parse_ffmpeg_regions
from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_llm import ClassifyClient, question_noul, run_sync
from video_editor_state.job import JobContext
from video_editor_tools.ffmpeg_wrapper import make_proxy
from video_editor_tools.transcription import resolve_transcription_settings
from video_editor_tools.transcription import transcribe as transcribe_audio

logger = structlog.get_logger(__name__)

DEFAULT_FILLERS = {
    "um",
    "umm",
    "uh",
    "uuh",
    "ah",
    "aah",
    "eh",
    "er",
    "erm",
    "like",
    "you know",
    "so",
    "actually",
    "basically",
    "literally",
    "sort of",
    "kind of",
    "i mean",
}
SILENCE_THRESHOLD_DB = -50.0
MIN_SILENCE_DURATION = 0.3
MIN_PAUSE_DURATION = 0.5

# #256: the semantic pass is a model judgment, not a measured cut, so its
# output is bounded. A single low-value region longer than the ceiling is
# distrusted (an 11-second "rambling intro" is usually the take, not filler),
# and accepted regions may not remove more than this fraction of the video.
# One ordinary rambling segment in a short take stays under the cap.
SEMANTIC_MAX_REMOVAL_FRACTION = 0.35
SEMANTIC_MAX_REGION_SECONDS = 8.0


def _silence_regions(audio_path: Path) -> list[tuple[float, float]]:
    """Detect silent regions using FFmpeg silencedetect."""
    from video_editor_ffmpeg.run import run_ffmpeg

    cmd = [
        "ffmpeg",
        "-i",
        str(audio_path),
        "-af",
        f"silencedetect=noise={SILENCE_THRESHOLD_DB}dB:d={MIN_SILENCE_DURATION}",
        "-f",
        "null",
        "-",
    ]
    result = run_ffmpeg(cmd)
    regions = parse_ffmpeg_regions(
        result.stderr,
        start_key="silence_start:",
        end_key="silence_end:",
    )
    return [(start, end) for start, end in regions if end - start >= MIN_SILENCE_DURATION]


def _detect_fillers(transcript: dict[str, Any]) -> list[tuple[float, float]]:
    """Return time ranges of filler words in the transcript."""
    regions = []
    for word in transcript.get("words", []):
        text = word.get("word", "").strip().lower().strip(".,!?;")
        if text in DEFAULT_FILLERS:
            regions.append((word["start"], word["end"]))
    return regions


def _detect_pauses(transcript: dict[str, Any]) -> list[tuple[float, float]]:
    """Detect pauses from gaps between consecutive words."""
    words = transcript.get("words", [])
    pauses = []
    for prev, word in zip(words, words[1:], strict=False):
        gap_start = prev["end"]
        gap_end = word["start"]
        if gap_end - gap_start >= MIN_PAUSE_DURATION:
            pauses.append((gap_start, gap_end))
    return pauses


def _build_kept_segments(
    duration: float,
    silence_regions: list[tuple[float, float]],
    filler_regions: list[tuple[float, float]],
    pause_regions: list[tuple[float, float]],
    semantic_regions: list[tuple[float, float]] | None = None,
) -> list[tuple[float, float, str]]:
    """Invert the set of removed regions into kept segments."""
    # Merge all removal regions.
    removals = sorted(
        {
            tuple(r)
            for r in silence_regions + filler_regions + pause_regions + (semantic_regions or [])
        }
    )
    if not removals:
        return [(0.0, duration, "full")]

    merged: list[tuple[float, float]] = []
    for start, end in removals:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))

    kept: list[tuple[float, float, str]] = []
    cursor = 0.0
    for start, end in merged:
        if start > cursor:
            kept.append((cursor, start, "keep speech"))
        cursor = max(cursor, end)
    if cursor < duration:
        kept.append((cursor, duration, "keep speech"))

    return kept


def _detect_semantic_low_value(
    transcript: dict[str, Any],
    *,
    client: ClassifyClient | None = None,
    threshold: float = 0.6,
    min_segment: float = 0.5,
    max_removal_fraction: float = SEMANTIC_MAX_REMOVAL_FRACTION,
    max_region_seconds: float = SEMANTIC_MAX_REGION_SECONDS,
) -> list[tuple[float, float]]:
    """Flag transcript segments that are low-value for the cut (rambling,
    false starts, off-topic, repeated filler) via a batched Jev classifier.

    Returns the ``(start, end)`` ranges to remove; empty when no classifier is
    available (the deterministic detectors remain the sole signal). The whole
    call (client build + classify + close) runs in one coroutine so the async
    HTTP client never crosses event loops.

    Unlike the deterministic detectors, this one is only as trustworthy as the
    classifier's judgment, so the result is bounded (#256): no single region
    longer than ``max_region_seconds`` is removed (an implausible verdict on a
    long span is distrusted), and the accepted regions may not total more than
    ``max_removal_fraction`` of the take. Regions are accepted in confidence
    order, so a tight budget keeps the clearest wins. Pass
    ``max_removal_fraction=1.0`` and a large ``max_region_seconds`` to restore
    the uncapped behaviour.
    """
    segments = transcript.get("segments", []) or []
    candidates = [
        (float(s.get("start", 0.0)), float(s.get("end", 0.0)), str(s.get("text", "")).strip())
        for s in segments
    ]
    candidates = [(a, b, t) for a, b, t in candidates if t and (b - a) >= min_segment]
    if not candidates:
        return []

    async def _classify() -> list[dict[str, Any]]:
        active = client
        owns_client = active is None
        if active is None:
            try:
                active = ClassifyClient.from_config()
            except Exception:  # noqa: BLE001 - config probing must not raise
                active = ClassifyClient()
        if not active.available():
            if owns_client:
                await active.close()
            return []
        try:
            return await active.classify_batch(
                [text for _, _, text in candidates],
                lambda text: question_noul(
                    "Is `segment` low-value for the final cut — rambling, a false "
                    "start, off-topic, or repeated filler that should be removed?",
                    data={"segment": text},
                    true_label="Low value",
                    false_label="Keep",
                ),
            )
        finally:
            if owns_client:
                await active.close()

    answers = run_sync(_classify())
    flagged: list[tuple[float, float, float]] = []
    for (start, end, _text), answer in zip(candidates, answers, strict=False):
        if isinstance(answer, dict):
            score = float(answer.get("noul", 0.0) or 0.0)
            if score >= threshold:
                flagged.append((score, start, end))
    if not flagged:
        return []

    duration = max(float(s.get("end", 0.0)) for s in segments)
    budget = max(0.0, duration * max_removal_fraction)
    accepted: list[tuple[float, float]] = []
    spent = 0.0
    for score, start, end in sorted(flagged, key=lambda item: item[0], reverse=True):
        length = end - start
        if max_region_seconds > 0 and length > max_region_seconds:
            logger.info(
                "semantic_region_rejected_too_long",
                start=start,
                end=end,
                length=length,
                max_region_seconds=max_region_seconds,
                score=score,
            )
            continue
        if spent + length > budget + 1e-9:
            continue
        accepted.append((start, end))
        spent += length

    accepted.sort()
    if len(accepted) < len(flagged):
        logger.info(
            "semantic_removal_clamped",
            flagged=len(flagged),
            accepted=len(accepted),
            removed_seconds=round(spent, 3),
            budget_seconds=round(budget, 3),
            take_seconds=round(duration, 3),
        )
    return accepted


class RoughCutSkill:
    """Orchestrate the rough cut stage of the new video editor architecture."""

    @property
    def name(self) -> str:
        return "rough_cut"

    def __init__(
        self,
        ctx: JobContext,
        render_options: RenderOptions | None = None,
        classify_client: ClassifyClient | None = None,
        semantic_max_removal: float = SEMANTIC_MAX_REMOVAL_FRACTION,
        semantic_max_region_seconds: float = SEMANTIC_MAX_REGION_SECONDS,
    ):
        self.ctx = ctx
        self.cache = SegmentCache(ctx.work_dir / ".cache" / "segments")
        self.render_options = render_options or RenderOptions()
        self.classify_client = classify_client
        self.semantic_max_removal = semantic_max_removal
        self.semantic_max_region_seconds = semantic_max_region_seconds

    def intake(self, source_path: Path | str, force: bool = False) -> Path:
        """Copy source footage into the project intake directory and record metadata.

        When ``force`` is False and an intake manifest already exists, the
        existing manifest is reused instead of re-copying/re-probing the source.
        """
        source_path = Path(source_path)
        intake_dir = self.ctx.work_dir / "intake"
        intake_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = intake_dir / "manifest.json"

        if not force and manifest_path.exists():
            manifest = self.ctx.store.read_json("intake", "manifest.json")
            dest = Path(manifest["source_path"])
            logger.info(
                "intake_reused",
                job_id=self.ctx.job_id,
                source=str(dest),
            )
            return dest

        dest = intake_dir / source_path.name
        shutil.copyfile(source_path, dest)

        info = probe_media(dest)
        proxy_path = intake_dir / "proxy.mp4"
        make_proxy(dest, proxy_path, width=1280, render_options=self.render_options)
        audio_path = intake_dir / "audio.wav"
        extract_audio(dest, audio_path)

        manifest = {
            "source_path": str(dest),
            "proxy_path": str(proxy_path),
            "audio_path": str(audio_path),
            "duration": info.duration,
            "width": info.width,
            "height": info.height,
            "fps": info.fps,
            "has_audio": info.has_audio,
        }
        with open(intake_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        logger.info("intake_complete", job_id=self.ctx.job_id, source=str(dest))
        return dest

    def transcribe(self, force: bool = False) -> dict[str, Any]:
        """Transcribe the ingested audio using the resolved ASR settings.

        When ``force`` is False and a transcript already exists, it is
        reused instead of re-running transcription.
        """
        transcript_path = self.ctx.artifact("transcript", "transcript.json")
        if not force and transcript_path.exists():
            with open(transcript_path, encoding="utf-8") as f:
                return cast(dict[str, Any], json.load(f))

        intake = self.ctx.store.read_json("intake", "manifest.json")
        audio_path = Path(intake["audio_path"])
        settings = resolve_transcription_settings()
        logger.info(
            "transcribe_settings",
            model=settings.model,
            device=settings.device,
            compute_type=settings.compute_type,
        )
        result = transcribe_audio(
            audio_path,
            transcript_path,
            model=settings.model,
            language=settings.language,
            device=settings.device,
            compute_type=settings.compute_type,
        )
        self.ctx.manifest().stage_done("transcribe", {"transcript": str(transcript_path)})
        self.ctx.store.save()
        logger.info("transcribe_complete", job_id=self.ctx.job_id)
        return result

    def _promote_alignment_to_transcript(self) -> bool:
        """Promote ``transcript/alignment.json`` to ``transcript/transcript.json``.

        Forced alignment (ElevenLabs ``/v1/forced-alignment``) writes
        ``alignment.json`` in the ElevenLabs response shape
        (``{words: [{text, start, end, ...}], ...}``), but the rest of
        the pipeline (``CaptionsSkill``, ``second_pass``, ``graphics``)
        reads ``transcript.json`` in the WhisperX shape
        (``{words: [{word, start, end, probability}]}``). This helper
        does the one-shot conversion so a job that only ran
        ``ve align`` (no WhisperX) can still drive a full pipeline run.

        Returns ``True`` when the conversion happened (alignment file
        present, transcript file absent), ``False`` otherwise. Callers
        fall through to ``self.transcribe()`` on a ``False`` return so
        the priority order stays user-supplied > alignment > WhisperX.
        """
        transcript_path = self.ctx.artifact("transcript", "transcript.json")
        if transcript_path.exists():
            # A pre-existing transcript.json wins (e.g. user-supplied
            # or a previous run). Don't overwrite a known-good file.
            return False
        alignment_path = self.ctx.artifact("transcript", "alignment.json")
        if not alignment_path.exists():
            return False
        with open(alignment_path, encoding="utf-8") as f:
            alignment = json.load(f)
        # The source audio path is informational; capture it from the
        # intake manifest so the converted transcript traces back to
        # the file that was actually aligned.
        source_audio = ""
        try:
            intake = self.ctx.store.read_json("intake", "manifest.json")
            source_audio = str(intake.get("audio_path", ""))
        except FileNotFoundError:
            # No intake yet (e.g. called outside ``run()``); fine —
            # ``source_audio`` is purely metadata.
            pass
        converted = forced_alignment_response_to_transcript(alignment, source_audio=source_audio)
        with open(transcript_path, "w", encoding="utf-8") as f:
            json.dump(converted, f, indent=2)
        self.ctx.manifest().stage_done(
            "transcribe",
            {"transcript": str(transcript_path), "source": "alignment"},
        )
        self.ctx.store.save()
        logger.info(
            "transcribe_promoted_from_alignment",
            job_id=self.ctx.job_id,
            word_count=len(converted.get("words", [])),
            alignment_path=str(alignment_path),
            transcript_path=str(transcript_path),
        )
        return True

    def analyze(self, force: bool = False) -> dict[str, Any]:
        """Detect silence, filler words, and pauses.

        When ``force`` is False and an analysis file already exists, it is
        reused instead of re-running the detectors.
        """
        analysis_path = self.ctx.artifact("analysis", "rough_cut.json")
        if not force and analysis_path.exists():
            with open(analysis_path, encoding="utf-8") as f:
                return cast(dict[str, Any], json.load(f))

        intake = self.ctx.store.read_json("intake", "manifest.json")
        audio_path = Path(intake["audio_path"])
        transcript_path = self.ctx.artifact("transcript", "transcript.json")

        with open(transcript_path, encoding="utf-8") as f:
            transcript = json.load(f)

        silence_regions = _silence_regions(audio_path)
        filler_regions = _detect_fillers(transcript)
        pause_regions = _detect_pauses(transcript)
        # Optional semantic pass: remove rambling / false-start segments the
        # wordlist misses. Additive — deterministic regions are unchanged,
        # and the pass is bounded so it cannot gut the take (#256).
        semantic_regions = _detect_semantic_low_value(
            transcript,
            client=self.classify_client,
            max_removal_fraction=self.semantic_max_removal,
            max_region_seconds=self.semantic_max_region_seconds,
        )

        analysis = {
            "silence_regions": silence_regions,
            "filler_regions": filler_regions,
            "pause_regions": pause_regions,
            "semantic_regions": semantic_regions,
            "duration": intake["duration"],
        }
        with open(analysis_path, "w", encoding="utf-8") as f:
            json.dump(analysis, f, indent=2)

        self.ctx.manifest().stage_done("analyze", {"rough_cut": str(analysis_path)})
        self.ctx.store.save()
        logger.info(
            "analyze_complete",
            job_id=self.ctx.job_id,
            silence=len(silence_regions),
            filler=len(filler_regions),
            pauses=len(pause_regions),
            semantic=len(semantic_regions),
        )
        return analysis

    def plan(self) -> EDL:
        """Build a locked EDL from the analysis."""
        analysis_path = self.ctx.artifact("analysis", "rough_cut.json")
        with open(analysis_path, encoding="utf-8") as f:
            analysis = json.load(f)

        kept = _build_kept_segments(
            analysis["duration"],
            analysis["silence_regions"],
            analysis["filler_regions"],
            analysis["pause_regions"],
            analysis.get("semantic_regions", []),
        )
        segments = compute_timeline_segments(kept)
        intake = self.ctx.store.read_json("intake", "manifest.json")
        edl = EDL(
            job_id=self.ctx.job_id,
            source_path=intake["source_path"],
            segments=segments,
        )

        logger.info("plan_complete", job_id=self.ctx.job_id, segments=len(segments))
        return edl

    def _write_edl(self, edl: EDL) -> Path:
        """Persist an EDL to the canonical plan path after a successful render."""
        edl_path = self.ctx.artifact("plan", "edl.json")
        edl_path.parent.mkdir(parents=True, exist_ok=True)
        with open(edl_path, "w", encoding="utf-8") as f:
            json.dump(edl.model_dump(), f, indent=2)

        self.ctx.manifest().stage_done("plan", {"edl": str(edl_path)})
        self.ctx.store.save()
        logger.info("edl_saved", job_id=self.ctx.job_id, edl=str(edl_path))
        return edl_path

    def render(
        self,
        edl: EDL | None = None,
        operation: str = "rough_cut",
        params: dict[str, Any] | None = None,
        force: bool = False,
    ) -> Path:
        """Render the locked rough cut, reusing cached segments when possible.

        When ``edl`` is None the EDL is read from the canonical plan path.
        When ``force`` is False and the locked output already exists, it is
        reused instead of re-rendering.
        """
        polished = self.ctx.work_dir / "rough_cut" / "locked.mp4"
        if not force and polished.exists() and polished.stat().st_size > 0:
            logger.info("rough_cut_reused", job_id=self.ctx.job_id, output=str(polished))
            return polished

        if edl is None:
            edl_path = self.ctx.artifact("plan", "edl.json")
            with open(edl_path, encoding="utf-8") as f:
                edl = EDL(**json.load(f))

        source_path = Path(edl.source_path)
        params = params or {}

        segment_paths: list[Path] = []
        for seg in edl.segments:
            key = segment_cache_key(
                source_path,
                seg.source_start,
                seg.source_end,
                operation,
                params,
            )
            cached = self.cache.get(key)
            if cached:
                segment_paths.append(cached)
                continue

            # Render segment by cutting from source.
            seg_output = self.ctx.work_dir / "rough_cut" / "segments" / f"{seg.id}.mp4"
            seg_output.parent.mkdir(parents=True, exist_ok=True)
            apply_cuts(
                source_path,
                [(seg.source_start, seg.source_end)],
                seg_output,
                render_options=self.render_options,
            )
            cached = self.cache.put(key, seg_output)
            segment_paths.append(cached)

        assembly = self.ctx.work_dir / "rough_cut" / "assembly.mp4"
        concat_segments(segment_paths, assembly)

        polished = self.ctx.work_dir / "rough_cut" / "locked.mp4"
        normalize_audio(assembly, polished, render_options=self.render_options)

        manifest = {
            "source_path": str(source_path),
            "edl_path": str(self.ctx.artifact("plan", "edl.json")),
            "locked_path": str(polished),
            "duration": edl.duration(),
            "segments": len(edl.segments),
        }
        manifest_path = self.ctx.work_dir / "rough_cut" / "rough_cut.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        self.ctx.manifest().stage_done("rough_cut", {"locked": str(polished)})
        self.ctx.store.save()
        logger.info("rough_cut_complete", job_id=self.ctx.job_id, output=str(polished))
        return polished

    def run(
        self,
        source_path: Path | str,
        transcript_path: Path | str | None = None,
        force: bool = False,
    ) -> Path:
        """Run the full rough cut skill end-to-end.

        Transcript resolution priority:
        1. **user-supplied** ``transcript_path`` — copied verbatim.
        2. **alignment** (Session V) — when ``transcript/alignment.json``
           exists (from ``ve align``), it's converted to the transcript
           shape and written to ``transcript/transcript.json``.
        3. **local ASR fallback** — when neither is present, run a full
           faster-whisper transcription pass.

        When ``force`` is False, each stage skips work if its output already
        exists. The EDL is written only after ``render`` succeeds so a render
        failure does not leave the project diffing against a new, unrendered EDL.
        """
        self.ctx.ensure_dirs()
        self.intake(source_path, force=force)
        if transcript_path:
            dest = self.ctx.artifact("transcript", "transcript.json")
            shutil.copyfile(transcript_path, dest)
            self.ctx.manifest().stage_done("transcribe", {"transcript": str(dest)})
            self.ctx.store.save()
        elif not self._promote_alignment_to_transcript():
            self.transcribe(force=force)
        self.analyze(force=force)
        edl = self.plan()
        polished = self.render(edl, force=force)
        self._write_edl(edl)
        return polished

    def update_and_render(self, new_edl: EDL) -> Path:
        """Re-render only changed segments after a Second Pass refinement.

        The new EDL is written only after ``render`` succeeds so a render
        failure does not overwrite the canonical plan with an unrendered EDL.
        """
        canonical_edl_path = self.ctx.artifact("plan", "edl.json")
        backup_edl_path = self.ctx.work_dir / "plan" / "edl_backup.json"

        if not backup_edl_path.exists():
            backup_edl_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(canonical_edl_path, backup_edl_path)

        with open(backup_edl_path, encoding="utf-8") as f:
            old_edl = EDL(**json.load(f))

        diff = diff_edls(old_edl, new_edl)
        changed = set(diff.changed + diff.added)
        logger.info("partial_rerender", changed=len(changed), unchanged=len(diff.unchanged))

        polished = self.render(new_edl, force=True)

        with open(canonical_edl_path, "w", encoding="utf-8") as f:
            json.dump(new_edl.model_dump(), f, indent=2)
        self.ctx.manifest().stage_done("plan", {"edl": str(canonical_edl_path)})
        self.ctx.store.save()

        # Update the backup so the next edit diffs against this new state.
        with open(backup_edl_path, "w", encoding="utf-8") as f:
            json.dump(new_edl.model_dump(), f, indent=2)

        return polished
