"""`ve short`: build a short from a committed content pack.

Stage order is fixed: validate → price → stills → voice → align → compile →
render → mux → normalise → QA. Every paid call is preceded by an approval the
run re-prices itself, and the QA pass measures the file that ships.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import structlog
from sfx_placement.bank import ResolvedSfx, SfxBank, SfxBankError
from sfx_placement.resolvers import manager_resolver
from video_editor_asset_ingestion.budget import BudgetGovernor
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.pricing import PricingRegistry
from video_editor_config import Settings
from video_editor_core.adapters.from_content_pack import pack_to_composition
from video_editor_core.compile_composition import compile_composition
from video_editor_core.composition import (
    Canvas,
    Composition,
    CompositionLayer,
    canvas_for_aspect,
    quantise,
)
from video_editor_core.content_pack import (
    END_CARD_ID,
    Alignment,
    ContentPack,
    TimedScene,
    TimedWord,
    brief_warnings,
    cue_phrases,
    display_lines,
    spoken_lines,
    still_for,
    still_references,
    still_specs,
)
from video_editor_core.cues import resolve_cues
from video_editor_core.designs import DesignError, DesignTokens, load_design
from video_editor_core.graphics import COMPONENTS, GraphicError, render_graphic
from video_editor_core.hyperframes_cli import render_project
from video_editor_core.layer_presets import caption, chip, end_card, pill, scrims
from video_editor_core.layerize import LayerizeError, layerize_still
from video_editor_core.scene_transitions import SUPPORTED_TRANSITIONS
from video_editor_core.sfx_cues import SfxCue, cues_from_composition
from video_editor_core.srt import srt_from_words
from video_editor_ffmpeg.broll import mix_sfx_into_video
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, FFmpegError, run_ffmpeg
from video_editor_ffmpeg.voiceover import mux_voiceover
from video_editor_state.asset_audit import (
    cost_tracking_degraded_reasons,
    stub_asset_ids,
    used_asset_paths,
)
from video_editor_state.job import JobContext
from video_editor_state.paths import default_cache_root
from video_editor_tools.transcription import resolve_transcription_settings

from video_editor_pipelines.composition_qa import gather_composition_qa
from video_editor_pipelines.estimate_ui import (
    EstimateAbortedError,
    EstimateError,
    confirm,
    echo,
)
from video_editor_pipelines.explainer_pipeline import (
    build_cost,
    check_approved_spend,
    cost_check_values,
    max_silent_gap,
    measure_loudness,
    normalize_loudness,
    price_requests,
)
from video_editor_pipelines.explainer_qa import (
    QaCheck,
    QaInputs,
    evaluate_qa,
    format_qa,
    qa_report,
)
from video_editor_pipelines.explainer_timing import (
    align_words,
    group_words_by_narration,
    spread_pinned_tail,
)
from video_editor_pipelines.pricing import _format_table
from video_editor_pipelines.sfx_qa import analyze_sfx
from video_editor_pipelines.short_captions import caption_layers, caption_style

logger = structlog.get_logger(__name__)

SHORT_FPS = 30


class ShortPipelineError(RuntimeError):
    """A short build that cannot proceed."""


class ShortPreflightError(ValueError):
    """A pack or design that cannot produce a valid build."""


def _image_params(pack: ContentPack) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if pack.image.model:
        params["model"] = pack.image.model
    if pack.image.resolution:
        params["model_kwargs"] = {"resolution": pack.image.resolution}
    if pack.image.provider in ("auto", "agy_image"):
        # ``agy`` (and a poyo fallback on the same request) derives its aspect
        # directive from these; without them the CLI can return a square.
        # Pinned poyo packs keep their old request keys, so their caches hold.
        width, height = canvas_for_aspect(pack.aspect)
        params["width"] = width
        params["height"] = height
    return params


def _voice_params(pack: ContentPack) -> dict[str, Any]:
    params: dict[str, Any] = {
        "voice_id": pack.voice.voice_id,
        "language": pack.voice.language,
    }
    if pack.voice.model:
        params["model"] = pack.voice.model
    return params


def _still_prompt(pack: ContentPack, prompt: str) -> str:
    """The prompt as sent: pack text first, series style suffix last.

    Terminal punctuation is preserved: a sentence ending in ``?``, ``!`` or
    ``…`` keeps it, and only a missing terminator gains the full stop that
    joins the style suffix.
    """
    body = prompt.strip()
    suffix = pack.image.style_suffix.strip()
    if not suffix:
        return body
    if body.endswith((".", "?", "!", "…")):
        return f"{body} {suffix}"
    return f"{body}. {suffix}"


def short_job_requests(pack: ContentPack) -> list[AssetRequest]:
    """Every paid request this build makes — the list the gate prices."""
    requests: list[AssetRequest] = []
    for still in still_specs(pack):
        scene = next(
            scene
            for scene in pack.scenes
            if (scene.still is not None and scene.still.key == still.key)
            or any(shot.still is not None and shot.still.key == still.key for shot in scene.shots)
        )
        params = _image_params(pack)
        references = still_references(pack, still)
        if references:
            params["reference_images"] = references
        if still.edit_of:
            # Resolved to the source still's file when it exists (generate_stills runs edits last).
            params["edit_of"] = f"still_{still.edit_of}"
        requests.append(
            AssetRequest(
                asset_id=f"still_{still.key}",
                job_id=pack.job_id,
                scene_id=scene.id,
                asset_type=AssetType.IMAGE,
                prompt=_still_prompt(pack, still.prompt),
                provider=pack.image.provider,
                params=params,
                output_ext="png",
            )
        )
    for scene in pack.scenes:
        if scene.layers != "generated" or scene.generated is None:
            continue
        for role, prompt in (
            ("bg", scene.generated.bg),
            ("mg", scene.generated.mg),
            ("fg", scene.generated.fg),
        ):
            requests.append(
                AssetRequest(
                    asset_id=f"still_{scene.id}__{role}",
                    job_id=pack.job_id,
                    scene_id=scene.id,
                    asset_type=AssetType.IMAGE,
                    prompt=_still_prompt(pack, prompt),
                    provider=pack.image.provider,
                    params=_image_params(pack),
                    output_ext="png",
                )
            )
    for scene in pack.scenes:
        for index, line in enumerate(scene.lines):
            requests.append(
                AssetRequest(
                    asset_id=f"line_{scene.id}_{index}",
                    job_id=pack.job_id,
                    scene_id=scene.id,
                    asset_type=AssetType.TTS,
                    prompt=line.spoken,
                    provider=pack.voice.provider,
                    params=_voice_params(pack),
                    output_ext="mp3",
                )
            )
    if pack.end_card.enabled and pack.end_card.spoken:
        requests.append(
            AssetRequest(
                asset_id=f"line_{END_CARD_ID}_0",
                job_id=pack.job_id,
                scene_id=END_CARD_ID,
                asset_type=AssetType.TTS,
                prompt=pack.end_card.spoken,
                provider=pack.voice.provider,
                params=_voice_params(pack),
                output_ext="mp3",
            )
        )
    return requests


def price_short_job(
    requests: Sequence[AssetRequest],
    registry: PricingRegistry,
    *,
    image_chain: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Price this build's requests, charging each voice line as its own call.

    Sarvam bills ``ceil(chars / 1000)`` *per call* and this build makes one
    call per line (#263). ``price_requests`` merges every TTS request of a list
    into one group and charges that formula once — the explainer's contract,
    not this one. So each voice request is priced alone and its row merged
    back into one line item per provider/model: the table keeps one row per
    provider, and the total is the number the run will actually spend. Every
    other asset type prices exactly as ``price_requests`` prices it.
    """
    other: list[AssetRequest] = []
    voice: dict[tuple[str, str, str], dict[str, Any]] = {}
    for request in requests:
        if request.asset_type != AssetType.TTS:
            other.append(request)
            continue
        for item in price_requests([request], registry, image_chain=image_chain):
            key = (item["provider"], item["model"], item["task_type"])
            row = voice.get(key)
            if row is None:
                voice[key] = dict(item)
                continue
            row["count"] += item["count"]
            row["total_cost"] = round(row["total_cost"] + item["total_cost"], 4)
            row["unit_cost"] = round(row["total_cost"] / row["count"], 6)
    return [*price_requests(other, registry, image_chain=image_chain), *voice.values()]


def preflight_short(pack: ContentPack) -> DesignTokens:
    """Validate everything that can fail before the first paid call."""
    missing_brief = brief_warnings(pack)
    if missing_brief:
        logger.warning("short_brief_incomplete", job_id=pack.job_id, warnings=missing_brief)
    try:
        tokens = load_design(pack.design)
    except DesignError as exc:
        raise ShortPreflightError(str(exc)) from exc
    width, height = canvas_for_aspect(pack.aspect)
    canvas = Canvas(width=width, height=height, fps=SHORT_FPS)
    # Every pill the pack can reach: ``pill`` stacks by index, so a scene with
    # three ``show`` lines needs rows 0-2 inside the safe area, not just row 0.
    # A pack with no ``show`` line never draws a pill, so it must not require
    # the design to carry a ``pill`` block.
    pills = max(
        (sum(1 for line in scene.lines if line.show) for scene in pack.scenes),
        default=0,
    )
    for scene in pack.scenes:
        if scene.transition_out and scene.transition_out not in SUPPORTED_TRANSITIONS:
            raise ShortPreflightError(
                f"scene {scene.id!r}: unknown transition {scene.transition_out!r}; "
                f"known: {', '.join(sorted(SUPPORTED_TRANSITIONS))}"
            )
        if scene.graphic is None:
            continue
        if scene.graphic.component not in COMPONENTS:
            raise ShortPreflightError(
                f"scene {scene.id!r}: unknown graphic {scene.graphic.component!r}; "
                f"known: {', '.join(sorted(COMPONENTS))}"
            )
        try:
            render_graphic(scene.graphic.component, scene.graphic.data, tokens, canvas)
        except GraphicError as exc:
            raise ShortPreflightError(f"scene {scene.id!r}: {exc}") from exc
    checks: list[tuple[str, Callable[[], object]]] = [
        ("caption", lambda: caption(tokens, canvas)),
        # A bad preset is a raw ``ValueError`` from the captions skill, raised
        # at compile time — after every paid stage has already run.
        ("caption preset", lambda: caption_style(tokens)),
        ("end card", lambda: end_card(tokens, canvas)),
        ("chip", lambda: chip(tokens, canvas)),
        ("scrims", lambda: scrims(tokens, canvas)),
    ]
    checks.extend(
        (f"pill {index}", partial(pill, tokens, canvas, index=index)) for index in range(pills)
    )
    for name, check in checks:
        try:
            check()
        except ValueError as exc:
            # ``LayerPresetError`` is a ``ValueError``; so are the captions
            # skill's unknown preset and a non-numeric design value. Its
            # message already names the design, so do not repeat it.
            detail = str(exc)
            prefix = "" if pack.design in detail else f"design {pack.design!r} "
            raise ShortPreflightError(f"{prefix}{name}: {detail}") from exc
    if pack.end_card.enabled and not pack.end_card.text.strip():
        raise ShortPreflightError("the pack has no end_card.text; the closing card would be blank")
    return tokens


def _stage_done(ctx: JobContext, stage: str, outputs: dict[str, Any]) -> None:
    """Record a stage and persist the manifest (#302).

    ``stage_done`` only mutates the in-memory manifest; ``estimate_short`` was
    the only writer that saved it, so ``manifest.json`` lagged ``qa.json`` and a
    copied project reported the source project's stages.
    """
    ctx.manifest().stage_done(stage, outputs)
    ctx.store.save()


@dataclass(frozen=True)
class ShortEstimate:
    """A priced build, before it is approved and persisted."""

    path: Path
    total_cost: float
    items: list[dict[str, Any]]
    voice_id: str


def price_short(
    ctx: JobContext,
    pack: ContentPack,
    *,
    config_path: Path | None = None,
    final: bool = False,
) -> ShortEstimate:
    """Price the build's real request list without prompting or writing (#303).

    The MCP ``run_short`` tool uses this to check a caller's ``approve_usd``
    before it records an approval; ``estimate_short`` adds the terminal UI.
    """
    requests = short_job_requests(pack)
    registry = PricingRegistry.load()
    registry.check_freshness()
    items = price_short_job(
        requests,
        registry,
        image_chain=Settings.load(config_path).config.assets.image_chain_for(final),
    )
    unpriced = [
        item
        for item in items
        if registry.unit_cost(item["provider"], item["model"], item["task_type"]) is None
    ]
    if unpriced:
        missing = ", ".join(
            f"{item['provider']}/{item['model']} ({item['task_type']})" for item in unpriced
        )
        raise EstimateError(
            f"no price for {missing}; add it to models.yaml or choose a different "
            "provider. Refusing to approve an estimate that omits this spend."
        )
    total_cost = round(sum(float(item["total_cost"]) for item in items), 4)
    return ShortEstimate(
        path=ctx.work_dir / "plan" / "cost_estimate.json",
        total_cost=total_cost,
        items=items,
        voice_id=pack.voice.voice_id,
    )


def save_short_estimate(ctx: JobContext, estimate: ShortEstimate) -> Path:
    """Persist an approved estimate and record the stage."""
    estimate.path.parent.mkdir(parents=True, exist_ok=True)
    estimate.path.write_text(
        json.dumps(
            {
                "job_id": ctx.job_id,
                "approved_at": datetime.now(UTC).isoformat(),
                "voice_id": estimate.voice_id,
                "line_items": estimate.items,
                "total_cost": estimate.total_cost,
                "currency": "USD",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _stage_done(
        ctx, "estimate", {"cost_estimate": str(estimate.path), "total_cost": estimate.total_cost}
    )
    return estimate.path


def estimate_short(
    ctx: JobContext,
    pack: ContentPack,
    *,
    config_path: Path | None = None,
    auto_approve: bool = False,
    final: bool = False,
) -> Path:
    """Price the build's real request list, ask for approval, and record it."""
    estimate = price_short(ctx, pack, config_path=config_path, final=final)

    echo(
        f"\nEstimated costs for {ctx.job_id} ({len(pack.scenes)} scenes, "
        f"{len(still_specs(pack))} stills):"
    )
    echo(_format_table(estimate.items, estimate.total_cost))
    echo(f"\nVoice: {estimate.voice_id}")

    if not auto_approve:
        echo(f"\nTotal: ${estimate.total_cost:.4f}")
        answer = confirm()
        if answer.lower() not in ("y", "yes"):
            echo("Aborted. Edit the pack or re-run with different providers.")
            raise EstimateAbortedError

    save_short_estimate(ctx, estimate)
    echo(f"Cost estimate approved and saved to {estimate.path}")
    return estimate.path


@dataclass(frozen=True)
class StillAssets:
    """Generated stills by key, plus the results for the cost ledger."""

    paths: dict[str, Path]
    results: tuple[AssetResult, ...]


def _resolve(
    ctx: JobContext,
    requests: list[AssetRequest],
    *,
    config_path: Path | None = None,
    final: bool = False,
) -> list[AssetResult]:
    """Buy (or reuse) every request in one manager batch."""
    config = Settings.load(config_path).config.assets
    manager = AssetIngestionManager(ctx, config=config, final=final)
    return asyncio.run(manager.resolve(requests))


def generate_stills(
    ctx: JobContext,
    pack: ContentPack,
    *,
    config_path: Path | None = None,
    final: bool = False,
) -> StillAssets:
    """One image per generated still key; aliases cost nothing."""
    pending = [
        request for request in short_job_requests(pack) if request.asset_type == AssetType.IMAGE
    ]
    done: dict[str, AssetResult] = {}
    # Waves: a still made as an edit of another (#318) waits until its source exists, then
    # carries the source file as its first reference image.
    while pending:
        ready = [r for r in pending if r.params.get("edit_of") in (None, *done)]
        if not ready:
            raise ShortPipelineError(
                f"edit_of cannot be resolved for {[r.asset_id for r in pending]}"
            )
        wave = [_with_edit_source(request, done) for request in ready]
        for result in _resolve(ctx, wave, config_path=config_path, final=final):
            done[result.asset_id] = result
        pending = [r for r in pending if r not in ready]
        missing = [r.asset_id for r in ready if r.asset_id not in done]
        if missing:
            raise ShortPipelineError(f"the still request {missing[0]!r} returned no asset")
    paths = {
        asset_id.removeprefix("still_"): result.resolved_path() for asset_id, result in done.items()
    }
    return StillAssets(paths=paths, results=tuple(done.values()))


def _with_edit_source(request: AssetRequest, done: dict[str, AssetResult]) -> AssetRequest:
    """``request`` with its ``edit_of`` source file prepended to ``reference_images``."""
    source = request.params.get("edit_of")
    if not source:
        return request
    params = dict(request.params)
    params["reference_images"] = [
        str(done[source].resolved_path()),
        *params.get("reference_images", []),
    ]
    return request.model_copy(update={"params": params})


def key_generated_assets(ctx: JobContext, pack: ContentPack, stills: StillAssets) -> StillAssets:
    """Chroma-key the generated MG/FG plates so the BG shows through (#317)."""
    paths = dict(stills.paths)
    output_dir = ctx.work_dir / "generated"
    output_dir.mkdir(parents=True, exist_ok=True)
    for scene in pack.scenes:
        if scene.layers != "generated":
            continue
        for role in ("mg", "fg"):
            key = f"{scene.id}__{role}"
            source = paths.get(key)
            if source is None:
                raise ShortPipelineError(
                    f"scene {scene.id!r}: generated {role} plate {key!r} was not produced"
                )
            target = output_dir / f"{key}.png"
            completed = run_ffmpeg(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(source),
                    "-vf",
                    "colorkey=0x00FF00:0.30:0.10,format=rgba",
                    "-frames:v",
                    "1",
                    str(target),
                ],
                timeout=RENDER_TIMEOUT_SECONDS,
            )
            if completed.returncode != 0:
                raise ShortPipelineError(
                    f"scene {scene.id!r}: could not key the {role} plate: {completed.stderr[-300:]}"
                )
            paths[key] = target
    return StillAssets(paths=paths, results=stills.results)


def layerize_stills(
    ctx: JobContext,
    pack: ContentPack,
    stills: StillAssets,
    *,
    config_path: Path | None = None,
) -> StillAssets:
    """Build the depth/fg/bg trio for every scene that asks for ``layers: auto``.

    Runs the configured GPU command once per still key and adds the derived
    paths to the mapping the adapter reads. Reused stills share one build.
    """
    keys = {still_for(pack, scene.id) for scene in pack.scenes if scene.layers == "auto"}
    if not keys:
        return stills
    config = Settings.load(config_path).config.layerize
    cache_dir = Path(default_cache_root()) / "layerize"
    paths = dict(stills.paths)
    records: dict[str, dict[str, object]] = {}
    for key in sorted(keys):
        source = paths.get(key)
        if source is None:
            raise ShortPipelineError(
                f"scene still {key!r} was not generated, so it cannot be layerized"
            )
        try:
            result = layerize_still(
                source,
                cache_dir,
                command=config.command,
                model=config.depth_model,
                process_res=config.process_res,
                cutout=config.cutout,
                timeout=config.timeout,
            )
        except LayerizeError as exc:
            raise ShortPipelineError(str(exc)) from exc
        paths[f"{key}__depth"] = result.depth
        paths[f"{key}__fg"] = result.fg
        paths[f"{key}__bg"] = result.bg
        if result.near is not None:
            paths[f"{key}__near"] = result.near
        records[key] = {
            "depth": str(result.depth),
            "fg": str(result.fg),
            "bg": str(result.bg),
            "cached": result.cached,
        }
    _stage_done(ctx, "layerize", {"assets": records})
    echo(f"Layerized {len(records)} still(s) for layers: auto")
    return StillAssets(paths=paths, results=stills.results)


@dataclass(frozen=True)
class VoiceTrack:
    """The concatenated narration, its measured length and its purchases."""

    path: Path
    duration: float
    results: tuple[AssetResult, ...]


def _concat_narration(segments: list[Path], gaps: Sequence[float] | float, output: Path) -> Path:
    """Join the per-line files into one track with ``gap`` seconds between."""
    output.parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    filters: list[str] = []
    labels: list[str] = []
    index = 0

    def _gap_after(position: int) -> float:
        if isinstance(gaps, (int, float)):
            return float(gaps)
        return float(gaps[position]) if position < len(gaps) else 0.0

    for position, segment in enumerate(segments):
        command.extend(["-i", str(segment)])
        filters.append(
            f"[{index}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=mono[a{index}]"
        )
        labels.append(f"[a{index}]")
        index += 1
        gap = _gap_after(position)
        if gap > 0 and position + 1 < len(segments):
            command.extend(["-f", "lavfi", "-t", f"{gap:.3f}", "-i", "anullsrc=r=44100:cl=mono"])
            filters.append(
                f"[{index}:a]aresample=44100,"
                f"aformat=sample_fmts=fltp:channel_layouts=mono[a{index}]"
            )
            labels.append(f"[a{index}]")
            index += 1
    filters.append("".join(labels) + f"concat=n={len(labels)}:v=0:a=1[out]")
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[out]",
            "-c:a",
            "pcm_s16le",
            str(output),
        ]
    )
    try:
        run_ffmpeg(command)
    except FFmpegError as exc:
        # The stage contract is ``ShortPipelineError``; ``main.short`` catches
        # only that, so a failed join after every paid TTS call must not
        # escape as a traceback.
        raise ShortPipelineError(f"could not join the narration: {exc}") from exc
    return output


def generate_voice(
    ctx: JobContext,
    pack: ContentPack,
    *,
    config_path: Path | None = None,
    final: bool = False,
) -> VoiceTrack:
    """Read every line, then concatenate them into one narration track."""
    requests = [
        request for request in short_job_requests(pack) if request.asset_type == AssetType.TTS
    ]
    results = _resolve(ctx, requests, config_path=config_path, final=final)
    by_id = {result.asset_id: result for result in results}
    segments: list[Path] = []
    for request in requests:
        result = by_id.get(request.asset_id)
        if result is None:
            raise ShortPipelineError(f"the voice request {request.asset_id!r} returned no asset")
        segments.append(result.resolved_path())
    pauses = [
        line.pause_after if line.pause_after > 0 else pack.voice.gap_seconds
        for scene in pack.scenes
        for line in scene.lines
    ]
    pauses.append(pack.voice.gap_seconds)
    if len(pauses) != len(segments):
        pauses = [pack.voice.gap_seconds] * len(segments)
    narration = _concat_narration(segments, pauses, ctx.work_dir / "narration.wav")
    duration = probe_media(narration).duration_ms() / 1000.0
    _stage_done(ctx, "voice", {"narration": str(narration), "seconds": duration})
    return VoiceTrack(path=narration, duration=duration, results=tuple(results))


def _transcribe(audio: Path, ctx: JobContext, config_path: Path | None = None) -> dict[str, Any]:
    """The transcription seam: one whisper pass over the whole narration.

    Module-level so an offline test can replace it without touching a model.
    ``vad_filter=False`` and no ``initial_prompt`` are the #268 lesson: a VAD
    drop or a primed decoder collapses the opening or closing scene.
    """
    from video_editor_transcribe.whisper_local import transcribe_audio

    settings = resolve_transcription_settings(config_path)
    transcript = transcribe_audio(
        audio,
        ctx.work_dir / "narration_words.json",
        model_size=settings.model,
        language=settings.language or None,
        word_timestamps=True,
        device=settings.device,
        compute_type=settings.compute_type,
        vad_filter=False,
    )
    return transcript


def _with_display(words: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    """Attach the pack's display spelling when it lines up word for word.

    A ``speak_as`` line is aligned on the phonetic transcript but captioned
    with the pack's spelling ("Sah-joo" spoken, "Saju" shown). A line whose
    display text does not split into the same number of words keeps the
    spoken spelling rather than guessing a mapping.
    """
    shown = text.split()
    if not shown or len(shown) != len(words):
        return words
    return [
        {
            **word,
            "display": word_text if word_text != str(word["word"]) else "",
        }
        for word, word_text in zip(words, shown, strict=True)
    ]


def align_scenes(
    pack: ContentPack,
    *,
    audio_duration: float,
    transcript: Mapping[str, Any],
) -> Alignment:
    """Turn the transcript into contiguous scene windows tiling the audio."""
    spoken = spoken_lines(pack)
    shown = display_lines(pack)
    narrations = [text for _, text in spoken]
    words = align_words(narrations, transcript.get("words") or [])
    spread_pinned_tail(words, audio_duration)
    groups = group_words_by_narration(narrations, words)

    order: list[str] = []
    per_scene: dict[str, list[dict[str, Any]]] = {}
    for (scene_id, _), group, (_, display) in zip(spoken, groups, shown, strict=True):
        if scene_id not in per_scene:
            per_scene[scene_id] = []
            order.append(scene_id)
        per_scene[scene_id].extend(_with_display(group, display))

    # A scene the recogniser heard nothing in must fail the build, never ship a
    # guessed window. An empty group is one shape of that. A *leading or
    # interior* line the transcript dropped is another, and a sneakier one:
    # ``_offset_to_time`` answers ``if offset < a: recorded = b``, pinning the
    # unheard words onto the *next* matched block, so the group is non-empty but
    # every word in it shares the next scene's first start. Without this check
    # the dropped scene becomes a single-frame flash and the following scene's
    # words fall before its own window — both invisible to the empty-group test.
    first_starts: list[float] = []
    for position, scene_id in enumerate(order):
        scene_words = per_scene[scene_id]
        if not scene_words:
            raise ShortPipelineError(f"scene {scene_id!r} has no timed words")
        here = float(scene_words[0]["start"])
        if position + 1 < len(order):
            following = per_scene[order[position + 1]]
            if following and here >= float(following[0]["start"]):
                raise ShortPipelineError(
                    f"scene {scene_id!r} has no timed words of its own: its "
                    f"first word starts at {here:.3f}s, at or after "
                    f"{order[position + 1]!r} at "
                    f"{float(following[0]['start']):.3f}s"
                )
        first_starts.append(here)

    boundaries: list[float] = [0.0]
    for position in range(1, len(order)):
        start = quantise(first_starts[position], SHORT_FPS)
        boundaries.append(max(start, boundaries[-1] + 1.0 / SHORT_FPS))

    ends = boundaries[1:] + [quantise(audio_duration, SHORT_FPS)]
    scenes: list[TimedScene] = []
    for scene_id, start, end in zip(order, boundaries, ends, strict=True):
        scene_words = per_scene[scene_id]
        if end <= start:
            # ``TimedScene`` would raise a pydantic ``ValidationError`` here;
            # this stage's error contract is ``ShortPipelineError``.
            raise ShortPipelineError(
                f"scene {scene_id!r} would end at {end:.3f}s before it starts "
                f"at {start:.3f}s; the measured words disagree with the "
                f"{audio_duration:.3f}s audio"
            )
        scenes.append(
            TimedScene(
                id=scene_id,
                start=start,
                end=end,
                words=[
                    TimedWord(
                        text=str(word["word"]),
                        display=str(word.get("display") or ""),
                        start=float(word["start"]),
                        end=float(word["end"]),
                    )
                    for word in scene_words
                ],
            )
        )
    return Alignment(duration=ends[-1], scenes=scenes)


@dataclass(frozen=True)
class ShortResult:
    final_path: Path
    composition_path: Path
    qa_path: Path
    passed: bool
    checks: list[QaCheck]
    #: Sidecar captions when ``captions.mode: sidecar``; otherwise ``None``.
    srt_path: Path | None = None
    #: Rendered QA table; the CLI prints it (the pipelines layer has no UI).
    qa_text: str = ""


def _caption_coverage(alignment: Alignment, captions: Sequence[CompositionLayer]) -> float:
    words = sum(len(scene.words) for scene in alignment.scenes)
    if not words:
        return 0.0
    covered = sum(len(layer.content.spans) for layer in captions)
    return min(1.0, covered / words)


SFX_PROVIDER_ORDER: tuple[str, ...] = (
    "earshot_sfx",
    "freesound_sfx",
    "openverse_sfx",
)
SFX_GAIN_DB = -3.0


def _add_sfx(
    ctx: JobContext,
    composition: Composition,
    video: Path,
    *,
    config_path: Path | None = None,
) -> Path:
    """Resolve the cue table through the free chain and mix it onto ``video``.

    Local hand-picked files in ``work/sfx/`` win; the chain then asks Freesound
    and Openverse (both free, so the build's approved spend is unaffected). A
    cue no source can fill is skipped and logged; the video still renders.
    """
    cues = cues_from_composition(composition, scene_cuts=True)
    if not cues:
        return video
    settings = Settings.load(config_path)
    available = settings.config.assets.providers
    providers = tuple(name for name in SFX_PROVIDER_ORDER if name in available)
    if not providers:
        logger.warning("short_sfx_no_providers", cues=len(cues))
        return video
    manager = AssetIngestionManager(
        ctx,
        config=settings.config.assets,
        # The approved estimate never prices SFX, so only free providers may
        # run here. A future priced provider (e.g. elevenlabs_sfx) is blocked
        # by the zero budget instead of spending outside the estimate (#308).
        budget=BudgetGovernor(limit=0.0, registry=PricingRegistry.load()),
    )
    resolver = manager_resolver(manager, providers, job_id=ctx.job_id)
    bank = SfxBank(local_dir=ctx.work_dir / "sfx", resolvers=[resolver])
    resolved, timeline = asyncio.run(_resolve_sfx(bank, cues))
    if not timeline:
        logger.warning("short_sfx_unresolved", cues=len(cues))
        return video
    credits = SfxBank.write_credits(ctx.output_dir / "credits.txt", resolved)
    output = ctx.work_dir / "with_sfx.mp4"
    mix_sfx_into_video(video, timeline, output, sfx_gain_db=SFX_GAIN_DB)
    logger.info("short_sfx_mixed", cues=len(timeline), credits=str(credits))
    return output


async def _resolve_sfx(
    bank: SfxBank, cues: Sequence[SfxCue]
) -> tuple[list[ResolvedSfx], list[tuple[Path, float]]]:
    resolved: list[ResolvedSfx] = []
    timeline: list[tuple[Path, float]] = []
    for cue in cues:
        try:
            item = await bank.resolve(cue.sound)
        except SfxBankError as exc:
            logger.warning("short_sfx_cue_unresolved", sound=cue.sound, error=str(exc))
            continue
        resolved.append(item)
        timeline.append((item.path, cue.start))
    return resolved, timeline


def mix_short_music(
    ctx: JobContext, pack: ContentPack, video: Path, *, config_path: Path | None = None
) -> Path:
    """Mix the pack's local music bed under the narration, with ducking."""
    if not pack.music.path:
        return video
    source = Path(pack.music.path)
    if not source.is_file():
        raise ShortPipelineError(f"music bed not found: {source}")
    track = source
    if pack.music.swells:
        from video_editor_pipelines.musical_pipeline import apply_swells

        track = apply_swells(
            source,
            [(float(at), float(db), float(dur)) for at, db, dur in pack.music.swells],
            ctx.work_dir / "music" / "music_swelled.wav",
        )
    from music.models import MusicStyle
    from music.music import MusicSkill

    skill = MusicSkill(
        ctx,
        music_path=track,
        style=MusicStyle(music_gain_db=pack.music.gain_db),
    )
    return skill.run(input_video=video, output_name="with_music.mp4")


def run_short_job(
    ctx: JobContext,
    pack: ContentPack,
    tokens: DesignTokens,
    *,
    config_path: Path | None = None,
    cli_path: str = "hyperframes",
    final: bool = False,
    strict_look: bool = False,
) -> ShortResult:
    """Stills → voice → alignment → compile → render → mux → normalise → QA."""
    ctx.ensure_dirs()
    requests = short_job_requests(pack)
    estimate_path = ctx.work_dir / "plan" / "cost_estimate.json"
    image_chain = Settings.load(config_path).config.assets.image_chain_for(final)
    try:
        check_approved_spend(
            estimate_path,
            requests,
            image_chain=image_chain,
            price=price_short_job,
        )
    except ValueError as exc:
        # ``check_approved_spend`` names ``ve explain`` in its remedy, which is
        # the wrong command on this path; re-state it for the pack build.
        raise ShortPipelineError(
            f"{exc}\nRun `ve short` on this pack again to price and approve it."
        ) from exc

    stills = generate_stills(ctx, pack, config_path=config_path, final=final)
    if any(scene.layers == "auto" for scene in pack.scenes):
        stills = layerize_stills(ctx, pack, stills, config_path=config_path)
    if any(scene.layers == "generated" for scene in pack.scenes):
        stills = key_generated_assets(ctx, pack, stills)
    voice = generate_voice(ctx, pack, config_path=config_path, final=final)
    transcript = _transcribe(voice.path, ctx, config_path)
    alignment = align_scenes(pack, audio_duration=voice.duration, transcript=transcript)
    (ctx.work_dir / "plan" / "alignment.json").write_text(
        json.dumps(alignment.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    width, height = canvas_for_aspect(pack.aspect)
    canvas = Canvas(width=width, height=height, fps=SHORT_FPS)
    sidecar = pack.captions.mode == "sidecar"
    captions = caption_layers(tokens, alignment, canvas=canvas, fps=SHORT_FPS)
    composition = pack_to_composition(
        pack,
        tokens,
        alignment,
        stills=stills.paths,
        captions=() if sidecar else captions,
        fps=SHORT_FPS,
    )
    composition_path = ctx.work_dir / "plan" / "composition.json"
    composition_path.write_text(
        json.dumps(composition.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    project_dir = ctx.work_dir / "composition"
    compile_composition(composition, project_dir)
    rendered = render_project(
        project_dir, ctx.work_dir / "rendered.mp4", cli_path=cli_path, fps=SHORT_FPS
    )
    voiced = mux_voiceover(rendered, voice.path, ctx.work_dir / "voiced.mp4")
    with_sfx = (
        _add_sfx(ctx, composition, voiced, config_path=config_path)
        if pack.sfx == "auto"
        else voiced
    )
    with_music = mix_short_music(ctx, pack, with_sfx, config_path=config_path)
    final_video = normalize_loudness(with_music, ctx.output_dir / "final.mp4")

    probe = probe_media(final_video)
    duration = probe.duration_ms() / 1000.0
    try:
        loudness_lufs, true_peak_db = measure_loudness(final_video)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        logger.warning("short_loudness_unmeasured", error=str(exc))
        loudness_lufs, true_peak_db = math.nan, math.nan
    results = [*stills.results, *voice.results]
    cost_usd, estimate_usd = cost_check_values(estimate_path, build_cost(results, requests))

    composition_checks: list[QaCheck] = []
    declared_cues = cue_phrases(pack)
    # Re-resolve the cues here rather than quoting the adapter's own count: this
    # is a second, independent measurement whose count the cue gate reports. It
    # is necessarily complete once the adapter has succeeded (``strict=True``
    # would have raised first, and ``resolve_cues`` returns one entry per key),
    # so it is kept as defence in depth, not as a drift detector.
    resolved_cues = resolve_cues(alignment.word_dicts(), declared_cues)
    if project_dir.is_dir():
        try:
            composition_checks = gather_composition_qa(
                composition_dir=project_dir,
                composition=composition,
                declared_cues=len(declared_cues),
                resolved_cues=len(resolved_cues),
                cli_path=cli_path,
                frame_dir=ctx.work_dir / "qa" / "frames",
                strict_look=strict_look,
                expect_depth=any(scene.layers in {"auto", "generated"} for scene in pack.scenes),
                end_screen=pack.youtube_end_screen,
            )
        except Exception as exc:  # noqa: BLE001 - a gate that cannot run must fail
            logger.warning("short_composition_gates_failed", error=str(exc))
            composition_checks = [QaCheck("composition_gates", False, str(exc))]
    else:
        composition_checks = [QaCheck("composition_gates", False, "no composition project to gate")]

    first_word_starts = [
        scene.words[0].start if scene.words else math.inf for scene in alignment.scenes
    ]
    raw_words = transcript.get("words") or []
    degraded_reasons = cost_tracking_degraded_reasons(ctx.work_dir)
    inputs = QaInputs(
        aspect_ratio=pack.aspect,
        width=probe.width,
        height=probe.height,
        fps=float(probe.fps),
        duration=duration,
        loudness_lufs=loudness_lufs,
        true_peak_db=true_peak_db,
        target_seconds=composition.duration,
        scene_starts=[scene.start for scene in alignment.scenes],
        first_word_starts=first_word_starts,
        caption_coverage=1.0 if sidecar else _caption_coverage(alignment, captions),
        still_scenes=len(pack.scenes),
        still_renders=sum(1 for result in stills.results if result.asset_type == AssetType.IMAGE),
        expected_still_renders=len(still_specs(pack))
        + 3 * sum(1 for scene in pack.scenes if scene.layers == "generated"),
        max_silent_gap=max_silent_gap(raw_words) if raw_words else math.inf,
        tail_seconds=(
            duration - alignment.scenes[-1].words[-1].end
            if pack.end_card.enabled and alignment.scenes[-1].words
            else (0.0 if not pack.end_card.enabled else math.inf)
        ),
        expect_tail=pack.end_card.enabled,
        cost_usd=cost_usd,
        estimate_usd=estimate_usd,
        stub_assets=tuple(stub_asset_ids(ctx.work_dir, used_paths=used_asset_paths(results))),
        cost_tracking_degraded=bool(degraded_reasons),
        cost_tracking_reasons=tuple(degraded_reasons),
    )
    checks = evaluate_qa(inputs, composition_checks)
    report = qa_report(inputs, checks)
    if pack.sfx == "auto":
        # Advisory: a soft whoosh may not produce a detectable onset, so
        # this block never changes the build's pass/fail.
        try:
            # The same cue set `_add_sfx` placed: the motion cues plus one
            # whoosh per cut. Measuring without `scene_cuts` hid every whoosh
            # from the report.
            sfx_report = analyze_sfx(
                final_video,
                cues_from_composition(composition, scene_cuts=True),
                reference=voiced,
                cli_path=cli_path,
            )
            report["sfx"] = sfx_report.as_dict()
            if not sfx_report.ok:
                logger.warning("short_sfx_report_not_ok", cues=len(sfx_report.measurements))
        except Exception as exc:  # noqa: BLE001 - advisory measurement
            logger.warning("short_sfx_analysis_failed", error=str(exc))
            report["sfx"] = {"ok": False, "error": str(exc)}
    qa_path = ctx.work_dir / "qa.json"
    qa_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    srt_path: Path | None = None
    if sidecar:
        words = [word for scene in alignment.scenes for word in scene.words]
        srt_path = ctx.output_dir / "final.srt"
        srt_path.write_text(srt_from_words(words), encoding="utf-8")
    qa_text = format_qa(checks)
    passed = all(check.ok for check in checks)
    # Persist the manifest on pass *and* fail, with the verdict in the stage
    # payload, so MCP ``job_status`` never reports a stale stage (#302).
    _stage_done(ctx, "qa", {"qa": str(qa_path), "passed": passed})
    logger.info("short_complete", job_id=ctx.job_id, final=str(final_video), passed=passed)
    return ShortResult(
        final_path=final_video,
        composition_path=composition_path,
        qa_path=qa_path,
        passed=passed,
        checks=checks,
        qa_text=qa_text,
        srt_path=srt_path,
    )
