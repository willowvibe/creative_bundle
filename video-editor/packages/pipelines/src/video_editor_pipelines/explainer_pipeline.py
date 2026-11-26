"""Run an explainer job end to end.

The command layer owns the estimate gate; this module owns everything after
approval:

    one narration request  ->  stills (one per reuse cluster)
    ->  transcribe + align ->  durations rewritten into the plan
    ->  HyperFrames render ->  ducked music ->  karaoke captions
    ->  loudnorm -14 LUFS  ->  QA gate

One narration request replaces one per scene: the same words cost the same but
arrive as one continuous track with no seam at the cuts, and one transcript
keeps the captions continuous across them.

The gate prices :func:`explainer_job_requests` — the very list
:func:`run_explainer_job` buys — rather than the default per-scene tally in
``ve estimate``. That pipeline-wide tally emits one TTS request per scene and
prices music on the plan's 1 s placeholders, so it would quote a different
build from the one this module makes.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from captions.models import CaptionStyle
from music.mix import mix_background_music
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.pricing import (
    PricingRegistry,
    estimate_image_cost,
    estimate_music_cost,
    estimate_tts_cost,
    estimate_video_cost,
)
from video_editor_asset_ingestion.providers.tts.tts_router import _pick_provider
from video_editor_asset_ingestion.scene_assets import (
    attach_results_to_plan,
    music_request,
    sfx_requests_for_scene,
)
from video_editor_config import Settings
from video_editor_core.designs import DesignError, designs_dir, load_design
from video_editor_core.hyperframes_cli import hyperframes_available
from video_editor_core.render_graph import Rect
from video_editor_core.safe_area import LANDSCAPE_CAPTION_TOP, PORTRAIT_CAPTION_TOP
from video_editor_core.scene_plan_composition import (
    canvas_for_aspect,
    render_scene_plan,
)
from video_editor_core.srt import srt_from_words
from video_editor_core.timeline import EDL, Segment
from video_editor_ffmpeg.broll import mix_sfx_into_video
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, run_ffmpeg
from video_editor_state.asset_audit import (
    cost_tracking_degraded_reasons,
    stub_asset_ids,
    used_asset_paths,
)
from video_editor_state.job import JobContext
from video_editor_tools.transcription import resolve_transcription_settings

from video_editor_pipelines.ai_label import label_ai_stills
from video_editor_pipelines.caption_qa import caption_contrast_check, caption_tokens_check
from video_editor_pipelines.composition_qa import gather_composition_qa
from video_editor_pipelines.estimate_ui import (
    EstimateAbortedError,
    EstimateError,
    confirm,
    echo,
)
from video_editor_pipelines.explainer_footage import (
    Judge,
    resolve_with_footage,
    router_judge,
    size_router_for_footage,
)
from video_editor_pipelines.explainer_plan import (
    NARRATION_PREFIX,
    attach_footage_shots,
    attach_map_media,
    build_explainer_plan,
    counts_as_scene_still,
    explainer_requests,
    plan_model,
    rewrite_timings,
)
from video_editor_pipelines.explainer_qa import (
    QaCheck,
    QaInputs,
    evaluate_qa,
    format_qa,
    qa_report,
)
from video_editor_pipelines.explainer_script import ExplainerScript
from video_editor_pipelines.explainer_timing import (
    SCENE_GAP_SECONDS,
    SceneTiming,
    align_words,
    derive_scene_timings,
    per_scene_timings,
    sentence_cuts_check,
    spread_pinned_tail,
)
from video_editor_pipelines.pricing import (
    _format_table,
    _resolve_model,
    auto_image_pricing_provider,
)
from video_editor_pipelines.still_reuse import ReusePlan

logger = structlog.get_logger(__name__)

LOUDNESS_TARGET_LUFS = -14.0

#: Seconds of music requested per scene. The narration length is unknown until
#: the voice has spoken, so the track is bought long enough for the whole reel
#: and the ducking mix trims it.
MUSIC_SECONDS_PER_SCENE = 6
DEFAULT_MUSIC_PROMPT = "warm minimal ambient bed"

#: ``tts_router`` delegates to one of these providers at render time; pricing
#: must charge the provider that will actually be billed, not the router.
_ROUTER_PRICING_PROVIDERS: dict[str, str] = {
    "sarvam": "sarvam_tts",
    "elevenlabs": "elevenlabs_tts",
    "kokoro": "kokoro_tts",
    "kitten_tts": "kitten_tts",
    "moss_tts": "moss_tts",
}

#: Voices/languages the router uses when a request does not name them.
_ROUTER_DEFAULT_VOICE = "elevenlabs:Brian"
_ROUTER_DEFAULT_LANGUAGE = "en"


class ExplainerPreflightError(ValueError):
    """An explainer job that cannot build, caught before anything is paid for.

    The design name, the caption preset and the HyperFrames CLI are all free to
    check but were previously only discovered deep inside the paid build
    (after TTS and stills). Raising a dedicated type lets the CLI present a
    user-facing message and lets the worker fail the job with the same text.
    """


def preflight_explainer(script: ExplainerScript, *, caption_preset: str = "") -> None:
    """Validate the free, local prerequisites of an explainer build.

    Runs, in order, without any network or paid call:

    1. the script's ``design:`` (a file under ``designs/``);
    2. the effective caption preset (``caption_preset`` when supplied, else the
       script's ``caption_preset:``);
    3. that the HyperFrames CLI needed to render typographic scenes is on PATH.

    Raises :class:`ExplainerPreflightError` naming the failed check and the
    fix. Call this before pricing so an unbuildable job never produces an
    estimate, and before a worker's estimate/approval branch so it never
    spends.
    """
    try:
        load_design(script.design)
    except DesignError as exc:
        raise ExplainerPreflightError(
            f"design check failed: {exc}. Set `design:` in the script to a "
            f"file under {designs_dir()} (for example `cream_editorial`)."
        ) from exc

    preset = caption_preset or script.caption_preset
    if preset:
        try:
            CaptionStyle.preset(preset)
        except ValueError as exc:
            raise ExplainerPreflightError(
                f"caption preset check failed: {exc}. Set `caption_preset:` in "
                "the script or pass a valid --caption-preset."
            ) from exc

    if not hyperframes_available():
        raise ExplainerPreflightError(
            "HyperFrames check failed: the `hyperframes` CLI is not on PATH. "
            "Explainer typographic scenes render with HyperFrames; install it "
            "and put it on PATH before building (`npm install -g hyperframes`)."
        )


@dataclass
class ExplainerResult:
    final_path: Path
    plan_path: Path
    qa_path: Path
    passed: bool
    checks: list[QaCheck]
    #: Sidecar captions when ``captions: {mode: sidecar}``; otherwise ``None``.
    srt_path: Path | None = None
    #: Rendered QA table; the CLI prints it (the pipelines layer has no UI).
    qa_text: str = ""


@dataclass
class _PriceGroup:
    """Requests that share a provider, model, asset type and price tier."""

    provider: str
    model: str
    task_type: str
    params: dict[str, Any] = field(default_factory=dict)
    count: int = 0
    chars: int = 0
    char_counts: list[int] = field(default_factory=list)
    seconds: float = 0.0
    generation_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Request building
# ---------------------------------------------------------------------------


def music_duration_seconds(plan: dict[str, Any]) -> int:
    """The music length the pipeline asks for: enough for every scene."""
    return int(round(len(plan.get("scenes", []) or []) * MUSIC_SECONDS_PER_SCENE))


def explainer_job_requests(
    plan: dict[str, Any],
    script: ExplainerScript,
    job_id: str,
    *,
    tts_provider: str = "tts_router",
    image_provider: str = "auto",
    music_provider: str = "poyo_music",
    music_path: Path | None = None,
    sfx_provider: str = "",
) -> list[AssetRequest]:
    """Every paid request this job makes — the list the gate must price.

    One narration request for the whole script, one still per reuse cluster
    (a typographic scene never emits an IMAGE request), one music track at
    the real requested length unless the caller supplied a track, and — when
    ``sfx_provider`` is set — one cue per scene. A free provider (Freesound)
    prices at zero but still appears in the estimate, so the gate never has to
    guess what a later stage will fetch.
    """
    requests = explainer_requests(
        plan, script, job_id, tts_provider=tts_provider, image_provider=image_provider
    )
    if music_path is None and music_provider:
        requests.append(
            music_request(
                job_id,
                prompt=script.music_mood or DEFAULT_MUSIC_PROMPT,
                duration_seconds=music_duration_seconds(plan),
                provider=music_provider,
            )
        )
    if sfx_provider:
        requests.extend(
            request
            for scene in plan_model(plan).scenes
            for request in sfx_requests_for_scene(scene, job_id, sfx_provider=sfx_provider)
        )
    return requests


# ---------------------------------------------------------------------------
# Pricing the gate's request list
# ---------------------------------------------------------------------------


def router_pricing_provider(params: dict[str, Any]) -> str:
    """The provider ``tts_router`` will deterministically delegate to.

    The router picks from the request's voice prefix and language (see
    ``providers/tts/tts_router.py``), so the gate can price the paid provider
    instead of the router, which has no price row of its own.
    """
    voice = params.get("voice_id")
    voice_id = voice if isinstance(voice, str) and voice else _ROUTER_DEFAULT_VOICE
    language = params.get("language")
    lang = language if isinstance(language, str) and language else _ROUTER_DEFAULT_LANGUAGE
    return _ROUTER_PRICING_PROVIDERS[_pick_provider(voice_id, lang)]


def _grouped(
    requests: Sequence[AssetRequest],
    registry: PricingRegistry,
    *,
    image_chain: Sequence[str] = (),
) -> list[_PriceGroup]:
    groups: dict[tuple[str, str, str, str], _PriceGroup] = {}
    for request in requests:
        provider = request.provider or "unknown"
        asset_type = request.asset_type.value
        if provider == "tts_router" and asset_type == "tts":
            provider = router_pricing_provider(request.params)
        if provider == "local_media" and asset_type in ("video", "image"):
            # A ``file:`` shot copies the user's own file: free.
            provider = "pexels"
        if provider == "stock" and asset_type in ("video", "image"):
            # Every stock library is free; price the chain's head (all $0).
            provider = "pexels"
        if provider == "auto" and asset_type == "image":
            # ``"auto"`` walks ``assets.image_chain``: price the first paid
            # member of the configured chain (the shipped head, agy_image, is
            # quota-based and unpriced). The approval then covers the expected
            # spend, and the worker's spend gate re-prices the same list.
            provider = auto_image_pricing_provider(image_chain, registry)
        requested = request.params.get("model")
        model = _resolve_model(
            provider,
            asset_type,
            explicit=requested.strip()
            if isinstance(requested, str) and requested.strip()
            else None,
        )
        # TTS is priced per call over the whole prompt; its per-request params
        # must not split one call into several lines.
        pricing_params = {} if asset_type == "tts" else dict(request.params)
        key = (
            provider,
            model,
            asset_type,
            json.dumps(pricing_params, sort_keys=True, default=str),
        )
        group = groups.get(key)
        if group is None:
            group = _PriceGroup(provider=provider, model=model, task_type=asset_type)
            group.params = pricing_params
            groups[key] = group
        group.count += 1
        group.chars += len(request.prompt)
        group.char_counts.append(len(request.prompt))
        seconds = request.params.get("duration_seconds")
        if isinstance(seconds, int | float):
            group.seconds += float(seconds)
        if asset_type == "music" and isinstance(seconds, int | float):
            group.generation_seconds += float(seconds)
    return list(groups.values())


def price_requests(
    requests: Sequence[AssetRequest],
    registry: PricingRegistry,
    *,
    image_chain: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Tally the exact request list into ``ve estimate``-shaped line items."""
    items: list[dict[str, Any]] = []
    for group in _grouped(requests, registry, image_chain=image_chain):
        if group.task_type == "tts":
            total_cost = estimate_tts_cost(
                group.provider,
                group.model,
                group.chars,
                registry,
                char_counts=group.char_counts,
            )
        elif group.task_type in ("image", "data_viz"):
            total_cost = estimate_image_cost(
                group.provider,
                group.model,
                group.count,
                registry,
                params=group.params,
            )
        elif group.task_type == "video":
            total_cost = estimate_video_cost(
                group.provider,
                group.model,
                group.seconds or group.count * 1.0,
                registry,
                params=group.params,
                count=group.count,
            )
        elif group.task_type == "music":
            total_cost = estimate_music_cost(
                group.provider,
                group.model,
                group.generation_seconds,
                registry,
            )
        else:
            total_cost = 0.0
        items.append(
            {
                "provider": group.provider,
                "model": group.model,
                "task_type": group.task_type,
                "count": group.count,
                "unit_cost": round(total_cost / group.count, 6) if group.count else 0.0,
                "total_cost": round(total_cost, 4),
                "currency": "USD",
            }
        )
    return items


def approved_total(estimate_path: Path) -> float:
    """The total a stored ``cost_estimate.json`` approval priced."""
    try:
        data = json.loads(estimate_path.read_text(encoding="utf-8"))
        total = data["total_cost"]
        if isinstance(total, bool) or not isinstance(total, int | float):
            raise TypeError(f"total_cost is not a number: {total!r}")
        return float(total)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        raise ValueError(f"the approved estimate at {estimate_path} is unreadable: {exc}") from exc


def check_approved_spend(
    estimate_path: Path,
    requests: Sequence[AssetRequest],
    *,
    image_chain: Sequence[str] = (),
    price: Callable[..., list[dict[str, Any]]] = price_requests,
) -> float:
    """Refuse a build whose real request list costs more than its approval.

    ``price`` defaults to this module's per-list tally; a caller whose billing
    unit differs (one TTS call per line, see ``short_pipeline.price_short_job``)
    passes its own so the approval and this re-price charge the same way.

    The worker trusts any existing ``plan/cost_estimate.json``, but the
    payload's own providers, ``music_path`` and ``no_reuse`` can differ from
    the flags the file was priced with — and ``ve estimate`` writes a different
    list (per-scene TTS, a 1 s music bed). Re-price the requests this run will
    actually make and stop before any paid call when that total exceeds the
    stored one. A cheaper run is fine; exact equality is not required.
    """
    registry = PricingRegistry.load()
    items = price(requests, registry, image_chain=image_chain)
    unpriced = [
        item
        for item in items
        if registry.unit_cost(item["provider"], item["model"], item["task_type"]) is None
    ]
    if unpriced:
        # An unpriced request is a spend this check cannot see, so refuse
        # rather than compare a total that omits it.
        missing = ", ".join(
            f"{item['provider']}/{item['model']} ({item['task_type']})" for item in unpriced
        )
        raise ValueError(
            f"cannot price this build for approval: no price for {missing}; "
            "refusing to spend against an estimate that omits it"
        )
    actual = round(sum(float(item["total_cost"]) for item in items), 4)
    approved = approved_total(estimate_path)
    if actual > approved:
        raise ValueError(
            f"this build costs ${actual:.4f}, but the approved estimate is "
            f"${approved:.4f}; re-approve it with `ve explain` before running"
        )
    return actual


def estimate_explainer(
    ctx: JobContext,
    script: ExplainerScript,
    plan: dict[str, Any],
    *,
    config_path: Path | None = None,
    tts_provider: str = "tts_router",
    image_provider: str = "auto",
    music_provider: str = "poyo_music",
    music_path: Path | None = None,
    auto_approve: bool = False,
    final: bool = False,
    sfx_provider: str = "",
) -> Path:
    """Price the job's real request list, ask for approval, and record it.

    ``ve estimate`` cannot express this list: it derives requests from the
    plan with one TTS request per scene and prices music on the plan's 1 s
    placeholders. This gate prices :func:`explainer_job_requests`, so the
    number on screen is the number the run will actually spend.
    """
    requests = explainer_job_requests(
        plan,
        script,
        ctx.job_id,
        tts_provider=tts_provider,
        image_provider=image_provider,
        music_provider=music_provider,
        music_path=music_path,
        sfx_provider=sfx_provider,
    )
    registry = PricingRegistry.load()
    registry.check_freshness()
    items = price_requests(
        requests,
        registry,
        image_chain=Settings.load(config_path).config.assets.image_chain_for(final),
    )
    total_cost = round(sum(item["total_cost"] for item in items), 4)
    anticipated_seconds = music_duration_seconds(plan)

    echo(
        f"\nEstimated costs for {ctx.job_id} ({len(plan['scenes'])} scenes, "
        f"~{anticipated_seconds}s music bed):"
    )
    echo(_format_table(items, total_cost))
    # Fail closed: a zero-rate local provider is a real price (unit_cost
    # returns 0.0), but ``None`` means the registry has no row for this
    # provider/model at all. Approving that total would approve a spend the
    # estimate silently omits, so refuse instead.
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
    voice = script.voice or "script default"
    echo(f"\nVoice: {voice}")

    if not auto_approve:
        echo(f"\nTotal: ${total_cost:.4f}")
        answer = confirm()
        if answer.lower() not in ("y", "yes"):
            echo("Aborted. Edit the script or re-run with different providers.")
            raise EstimateAbortedError

    estimate_data = {
        "job_id": ctx.job_id,
        "approved_at": datetime.now(UTC).isoformat(),
        "voice_id": script.voice,
        "voice_name": voice,
        "line_items": items,
        "total_cost": total_cost,
        "currency": "USD",
    }
    estimate_path = ctx.work_dir / "plan" / "cost_estimate.json"
    estimate_path.parent.mkdir(parents=True, exist_ok=True)
    estimate_path.write_text(json.dumps(estimate_data, indent=2), encoding="utf-8")

    ctx.manifest().stage_done(
        "estimate", {"cost_estimate": str(estimate_path), "total_cost": total_cost}
    )
    ctx.store.save()
    echo(f"Cost estimate approved and saved to {estimate_path}")
    return estimate_path


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def prepare_explainer(
    ctx: JobContext, script: ExplainerScript, *, reuse: bool = True
) -> tuple[dict[str, Any], ReusePlan]:
    """Build the plan, write it beside its reuse report, and return both."""
    plan, reuse_plan = build_explainer_plan(script, ctx.job_id, reuse=reuse)
    plan_dir = ctx.work_dir / "plan"
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "scene_plan.json").write_text(
        json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    report = {
        "still_scenes": reuse_plan.still_scenes,
        "renders": reuse_plan.renders,
        "saved": reuse_plan.saved,
        "clusters": [
            {
                "owner": cluster.owner,
                "scenes": cluster.scenes,
                "overlap": round(cluster.overlap, 3),
            }
            for cluster in reuse_plan.clusters
        ],
        "unclustered": reuse_plan.unclustered,
    }
    (plan_dir / "reuse_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("explainer_plan_ready", job_id=ctx.job_id, scenes=len(plan["scenes"]))
    return plan, reuse_plan


# ---------------------------------------------------------------------------
# Measurement helpers
# ---------------------------------------------------------------------------


def render_plan_document(plan: dict[str, Any]) -> dict[str, Any]:
    """A copy of ``plan`` that carries no music bed for the renderer.

    ``compile_scene_plan_project`` turns a root ``music_path`` into a unity-gain
    ``<audio id="music">``. This pipeline mixes the bed itself with speech
    ducking, so handing the renderer that key would ship the music twice and
    the sidechain would duck an already-scored bed against itself. The path
    stays in the plan on disk as metadata; only the render copy drops it.
    """
    return {key: value for key, value in plan.items() if key != "music_path"}


def reconcile_final_tail(timings: Sequence[SceneTiming], audio_end: float) -> list[SceneTiming]:
    """Stretch the final scene to the end of the narration when it falls short.

    Whisper can drop the last scene's words (#268), and the estimated fallback
    is only a word-count guess. The compiled composition sizes its
    ``<audio data-duration>`` from the summed scene durations, so a final scene
    that ends before the narration would clip the voice. When a measurement is
    good the ``FINAL_TAIL_SECONDS`` end is already past the audio and is kept.
    """
    reconciled = list(timings)
    if not reconciled or reconciled[-1].end >= audio_end:
        return reconciled
    reconciled[-1] = replace(reconciled[-1], end=round(audio_end, 3))
    return reconciled


def cost_check_values(estimate_path: Path, spent: float | None) -> tuple[float, float]:
    """``(cost_usd, estimate_usd)`` for the QA gate, failing closed.

    A missing or unreadable approval, or a build whose price could not be
    measured, yields ``cost > estimate`` so the gate names the gap instead of
    passing on an unmeasured ``0.0``.
    """
    try:
        data = json.loads(estimate_path.read_text(encoding="utf-8"))
        estimate = float(data["total_cost"])
    except (OSError, TypeError, ValueError, KeyError):
        return math.inf, 0.0
    return (math.inf if spent is None else float(spent)), estimate


def measure_loudness(path: Path) -> tuple[float, float]:
    """Measure integrated loudness and true peak with ffmpeg's loudnorm."""
    result = run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            "loudnorm=I=-14:TP=-1.0:print_format=json",
            "-f",
            "null",
            "-",
        ],
        check=True,
        timeout=RENDER_TIMEOUT_SECONDS,
    )
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", result.stderr, re.DOTALL)
    if match is None:
        raise RuntimeError("ffmpeg did not report loudness")
    data = json.loads(match.group(0))
    return float(data["input_i"]), float(data["input_tp"])


def max_silent_gap(words: Sequence[dict[str, Any]]) -> float:
    gap = 0.0
    for previous, current in zip(words, words[1:], strict=False):
        gap = max(gap, float(current["start"]) - float(previous["end"]))
    return gap


#: Caption-band samples per second when measuring the backdrop (#341).
BACKDROP_SAMPLE_FPS = 4
_BAND_W, _BAND_H = 96, 24


def _linear(value: int) -> float:
    c = value / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


_LINEAR_LUT = [_linear(v) for v in range(256)]


def band_luminance(video: Path, rect: Any) -> list[tuple[float, float]]:
    """(time, mean relative luminance) of ``rect`` in ``video``, sampled at 4 fps.

    One ffmpeg pass crops the caption band, shrinks it and emits 8-bit luma;
    each pixel is linearised before averaging so the mean is a WCAG-style
    relative luminance, comparable with ``captions.theme`` colours.
    """
    result = run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-v",
            "error",
            "-i",
            str(video),
            "-vf",
            (
                f"fps={BACKDROP_SAMPLE_FPS},crop={rect.width}:{rect.height}:{rect.x}:{rect.y},"
                f"scale={_BAND_W}:{_BAND_H},format=gray"
            ),
            "-f",
            "rawvideo",
            "-",
        ],
        check=True,
        text=False,
        timeout=RENDER_TIMEOUT_SECONDS,
    )
    frame = _BAND_W * _BAND_H
    data: bytes = result.stdout
    return [
        (
            round(index / BACKDROP_SAMPLE_FPS, 3),
            sum(_LINEAR_LUT[v] for v in data[offset : offset + frame]) / frame,
        )
        for index, offset in enumerate(range(0, len(data) - frame + 1, frame))
    ]


def line_backdrop(series: Sequence[tuple[float, float]], start: float, end: float) -> float:
    """Mean backdrop luminance over ``[start, end]``; nearest sample if none fall inside."""
    inside = [lum for t, lum in series if start <= t <= end]
    if inside:
        return sum(inside) / len(inside)
    if not series:
        return 0.0
    middle = (start + end) / 2
    return min(series, key=lambda sample: abs(sample[0] - middle))[1]


def _cut_fields(check: Any) -> tuple[str, bool, str]:
    return check.name, check.ok, check.detail


#: Top of the caption band as a share of the height. Landscape (long-form) sits
#: low, centred near 90%, clear of the picture; portrait stays higher, above the
#: Shorts/Reels interface that covers the bottom of the frame.
CAPTION_BAND_HEIGHT = 0.094


def caption_band(width: int, height: int) -> Rect:
    """Where the explainer's burned captions go for this canvas."""
    top = LANDSCAPE_CAPTION_TOP if width > height else PORTRAIT_CAPTION_TOP
    return Rect(
        x=int(width * 0.08),
        y=int(height * top),
        width=int(width * 0.84),
        height=int(height * CAPTION_BAND_HEIGHT),
    )


def burn_captions(
    video: Path,
    words: list[dict[str, Any]],
    output: Path,
    *,
    width: int,
    height: int,
    preset: str = "karaoke",
    report: list[dict[str, Any]] | None = None,
    cache_dir: Path | None = None,
) -> Path:
    """Burn the preset's captions over ``video`` at the plan's canvas size.

    Each line's colours follow the measured backdrop behind the caption band
    (#341): the preset look on dark frames, charcoal text on light plates.
    ``report`` (when given) receives one record per line for the QA gates.
    """
    from captions.captions import caption_graph_layers
    from captions.theme import theme_for_backdrop
    from captions.timing import generate_caption_words, generate_segment_caption_lines
    from video_editor_core.render_graph import Cut, RenderGraph
    from video_editor_core.renderer import burn_overlays

    duration = probe_media(video).duration_ms() / 1000.0
    style = CaptionStyle.preset(preset)
    edl = EDL(
        job_id=f"explainer_{video.stem}",
        source_path=str(video),
        segments=[
            Segment(
                id="s0",
                source_start=0.0,
                source_end=duration,
                timeline_start=0.0,
                timeline_end=duration,
                label="all",
            )
        ],
    )
    lines = generate_segment_caption_lines(
        generate_caption_words({"words": words, "segments": []}, edl),
        0.0,
        duration,
        style,
    )
    rect = caption_band(width, height)
    lines = [line for line in lines if line.words]
    series = band_luminance(video, rect)
    overrides: list[dict[str, object]] = []
    for line in lines:
        backdrop = line_backdrop(series, line.start, line.end)
        theme = theme_for_backdrop(backdrop, style)
        overrides.append(theme)
        if report is not None:
            report.append(
                {
                    "start": round(line.start, 3),
                    "end": round(line.end, 3),
                    "text": line.text,
                    "backdrop": round(backdrop, 4),
                    "font_color": str(theme.get("font_color", style.font_color)),
                    "highlight_color": str(theme.get("highlight_color", style.highlight_color)),
                }
            )
    graph = RenderGraph(
        job_id=edl.job_id,
        width=width,
        height=height,
        fps=30,
        source=str(video),
        has_audio=True,
        render_preference="hyperframes",
        cuts=[
            Cut(
                source_start=0.0,
                source_end=duration,
                timeline_start=0.0,
                timeline_end=duration,
            )
        ],
        overlays=caption_graph_layers(lines, rect, style, end_limit=duration, overrides=overrides),
    )
    # The captions render on a transparent page of their own and FFmpeg lays them over
    # the finished video; embedding the video in the page took 112 min and then timed
    # out. A failed render never falls back to drawtext for Telugu (it cannot shape it).
    return burn_overlays(graph, output, cache_dir=cache_dir)


#: The limiter starts this far under the -1.0 dBTP QA ceiling: AAC's decoded
#: inter-sample peaks overshoot the pre-encode ceiling, and the residual
#: loudness pass then adds gain. The ceiling adapts down when a build overshoots.
LOUDNESS_CEILING_DB = -1.7
#: Aim 0.2 dB under the gate so the shipped file has measurement margin.
LOUDNESS_PEAK_TARGET_DB = -1.2
#: Corrective passes re-encode the *original* with the cumulative gain and keep
#: only the last file, so the shipped audio has one AAC generation. Four passes
#: cover a hot source whose peaks and loudness need separate corrections.
LOUDNESS_MAX_PASSES = 4
#: Stop once the measured integrated loudness is this close to the target.
LOUDNESS_RESIDUAL_LU = 0.5


def _normalize_pass(video: Path, output: Path, gain_db: float, ceiling_db: float) -> None:
    """Apply one gain + look-ahead limiter pass; video is stream-copied."""
    limit = math.pow(10.0, ceiling_db / 20.0)
    run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-c:v",
            "copy",
            "-af",
            f"volume={gain_db:.2f}dB,alimiter=limit={limit:.6f}:level=false:latency=true",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output),
        ],
        check=True,
        timeout=RENDER_TIMEOUT_SECONDS,
    )


def normalize_loudness(video: Path, output: Path) -> Path:
    """Normalise to -14 LUFS as measured on the encoded file; video is copied.

    Single-pass ``loudnorm`` runs its dynamic mode and misses the integrated
    target whenever the peaks are already hot: the first saju pack's narration
    measured -15.1 LUFS after ``loudnorm=I=-14``, so QA failed a build that
    normalisation had touched. Measure the input, apply that exact gain behind
    a look-ahead limiter, then measure the encode and correct both the residual
    loudness and any true-peak overshoot from the original.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    input_lufs, _ = measure_loudness(video)
    gain_db = LOUDNESS_TARGET_LUFS - input_lufs
    ceiling_db = LOUDNESS_CEILING_DB
    temporary = output.with_name(f".{output.name}.normalize.mp4")
    try:
        for _ in range(LOUDNESS_MAX_PASSES):
            _normalize_pass(video, temporary, gain_db, ceiling_db)
            measured, peak = measure_loudness(temporary)
            residual = LOUDNESS_TARGET_LUFS - measured
            overshoot = peak - LOUDNESS_PEAK_TARGET_DB
            if abs(residual) <= LOUDNESS_RESIDUAL_LU and overshoot <= 0:
                break
            gain_db += residual
            if overshoot > 0:
                # The limiter's pre-encode ceiling cannot promise a decoded
                # true peak, so take the measured excess off the ceiling. The
                # extra 0.2 dB is the AAC generation's own margin.
                ceiling_db -= overshoot + 0.2
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def _expected_still_renders(ctx: JobContext) -> int:
    """The render count the reuse report promised, or ``-1`` when unreadable."""
    report_path = ctx.work_dir / "plan" / "reuse_report.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        return int(report["renders"])
    except (OSError, TypeError, ValueError, KeyError):
        logger.warning("explainer_reuse_report_unreadable", path=str(report_path))
        return -1


def _billed_characters(result: AssetResult, request: AssetRequest | None) -> int | None:
    """Characters the provider actually synthesised, or ``None`` if unknown.

    Some providers report the count on the result (``text_length``, used by
    the local engines); the hosted ones do not, so the text actually sent —
    the request's prompt — is the quantity that was billed.
    """
    reported = result.metadata.get("text_length")
    if isinstance(reported, int) and reported > 0:
        return reported
    # ElevenLabs and Sarvam do not report a character count on the result, so
    # the length of the prompt actually sent is the documented fallback for
    # their per-1k-character pricing. That is still a quantity this result
    # made available (the text it synthesized), not a re-price of a separately
    # constructed request list.
    if request is not None and request.prompt:
        return len(request.prompt)
    return None


def _billed_seconds(result: AssetResult) -> float | None:
    """Generated audio length the result reports, or ``None`` if unknown."""
    reported = result.metadata.get("duration_seconds")
    if isinstance(reported, int | float) and float(reported) > 0:
        return float(reported)
    if result.duration_ms > 0:
        return result.duration_ms / 1000.0
    # Stock libraries report the clip length in whole seconds (Pixabay).
    stock = result.metadata.get("duration")
    if isinstance(stock, int | float) and not isinstance(stock, bool) and float(stock) > 0:
        return float(stock)
    return None


def join_scene_narration(
    clips: Sequence[Path], output: Path, *, gap: float = SCENE_GAP_SECONDS
) -> list[float]:
    """Join scene clips with ``gap`` seconds of silence; return each clip's length.

    The lengths are measured from the clips themselves, so the joined track's
    scene offsets are exact (``per_scene_timings`` lays the scenes out with the
    same gap).
    """
    if not clips:
        raise ValueError("no narration clips to join")
    durations = [probe_media(clip).duration_ms() / 1000.0 for clip in clips]
    inputs: list[str] = []
    chains: list[str] = []
    labels: list[str] = []
    for index, clip in enumerate(clips):
        inputs += ["-i", str(clip)]
        pad = f",apad=pad_dur={gap:.3f}" if index < len(clips) - 1 else ""
        chains.append(f"[{index}:a]aresample=44100,aformat=channel_layouts=mono{pad}[a{index}]")
        labels.append(f"[a{index}]")
    graph = ";".join(chains) + ";" + "".join(labels) + f"concat=n={len(clips)}:v=0:a=1[out]"
    output.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            *inputs,
            "-filter_complex",
            graph,
            "-map",
            "[out]",
            str(output),
        ],
        timeout=RENDER_TIMEOUT_SECONDS,
    )
    return durations


def transcription_language(voice_language: str, configured: str) -> str | None:
    """The language Whisper listens in: the script's voice language, else config.

    A config pinned to ``en`` made Whisper skip whole Telugu passages.
    """
    spoken = (voice_language or "").strip().split("-", 1)[0].lower()
    return spoken or (configured or "").strip() or None


def result_cost(
    result: AssetResult,
    request: AssetRequest | None,
    registry: PricingRegistry,
) -> float | None:
    """Price the billable quantity a provider result reports.

    The result names the provider and model that actually served the request
    and the quantity billed — synthesized characters, generated seconds, or
    one rendered asset. Pricing those quantities (rather than the request
    list) is what lets the gate detect spend divergence. ``None`` when the
    result exposes no billable quantity or the registry has no row for it.
    """
    provider = result.metadata.get("provider")
    if (not isinstance(provider, str) or not provider) and request is not None:
        # Some providers (Freesound) do not name themselves on the result; a
        # request that pinned one says who served it.
        provider = request.provider if request.provider not in {"", "auto", "stock"} else None
    if not isinstance(provider, str) or not provider:
        return None
    reported_model = result.metadata.get("model")
    model = (
        reported_model.strip()
        if isinstance(reported_model, str) and reported_model.strip()
        else _resolve_model(provider, result.asset_type.value)
    )
    asset_type = result.asset_type.value
    params = request.params if request is not None else None
    if asset_type == "tts":
        chars = _billed_characters(result, request)
        if chars is None or registry.unit_cost(provider, model, "tts") is None:
            return None
        return estimate_tts_cost(provider, model, chars, registry)
    if asset_type in ("image", "data_viz"):
        if registry.unit_cost(provider, model, asset_type, params) is None:
            return None
        return estimate_image_cost(provider, model, 1, registry, params=params)
    if asset_type == "video":
        seconds = _billed_seconds(result)
        if seconds is None or registry.unit_cost(provider, model, "video", params) is None:
            return None
        return estimate_video_cost(provider, model, seconds, registry, params=params)
    if asset_type == "sfx":
        unit = registry.unit_cost(provider, model, "sfx", params)
        if unit is None:
            return None
        return unit * (_billed_seconds(result) or 1.0)
    if asset_type == "music":
        seconds = _billed_seconds(result)
        if seconds is None or registry.unit_cost(provider, model, "music", params) is None:
            return None
        return estimate_music_cost(provider, model, seconds, registry, params=params)
    return None


def measured_cost(
    results: Sequence[AssetResult],
    requests: Sequence[AssetRequest],
    *,
    prefer_reported: bool = False,
) -> float | None:
    """Price every result from the quantity its provider reports.

    With ``prefer_reported``, a result that carries a finite
    ``metadata["cost"]`` bill is taken at that value and the rest are priced
    from the quantities they report. That mix is deliberate: a provider that
    reports an exact bill is more accurate than the registry, while a provider
    that reports nothing (ElevenLabs, Sarvam, PoYo) still has a billable
    quantity to price. Pricing *every* result is what keeps a partial sum from
    passing as the whole spend.

    ``None`` when there are no results, pricing cannot be loaded, or any
    single result exposes neither a bill nor a billable quantity — the build
    is then unmeasured rather than partly measured.
    """
    if not results:
        return None
    try:
        registry = PricingRegistry.load()
    except Exception as exc:  # noqa: BLE001 - pricing must not abort a finished build
        logger.warning("explainer_build_price_unavailable", error=str(exc))
        return None
    request_by_asset = {request.asset_id: request for request in requests}
    total = 0.0
    for result in results:
        cost = _reported_cost(result) if prefer_reported else None
        if cost is None:
            cost = result_cost(result, request_by_asset.get(result.asset_id), registry)
        if cost is None:
            logger.warning(
                "explainer_result_unpriceable",
                asset_id=result.asset_id,
                asset_type=result.asset_type.value,
                provider=result.metadata.get("provider"),
                model=result.metadata.get("model"),
            )
            return None
        total += cost
    return round(total, 4)


def _reported_cost(result: AssetResult) -> float | None:
    """The finite bill a provider put on one result, or ``None``."""
    raw = result.metadata.get("cost")
    if raw is None:
        return None
    try:
        cost = float(raw)
    except (TypeError, ValueError):
        return None
    return cost if math.isfinite(cost) else None


def reported_cost(results: Sequence[AssetResult]) -> float | None:
    """Sum the costs the providers themselves reported.

    ``AssetResult.metadata["cost"]`` is where a provider records what it
    actually billed (e.g. OpenRouter's usage cost, or ``0.0`` for a free
    quota-based provider). ``None`` unless *every* result reports a finite
    one: a partial sum must not pass as the whole spend. Callers that can
    price the remaining results should use :func:`measured_cost` with
    ``prefer_reported`` instead.
    """
    if not results:
        return None
    total = 0.0
    for result in results:
        cost = _reported_cost(result)
        if cost is None:
            return None
        total += cost
    return round(total, 4)


def build_cost(results: Sequence[AssetResult], requests: Sequence[AssetRequest]) -> float | None:
    """What the build spent, measured from what the providers returned.

    A result that reports its own bill (OpenRouter usage, a free provider's
    ``0.0``) is taken at that value; every other result is priced from the
    billable quantity it reports, via the registry. Every result is therefore
    measured, so the total is the whole spend rather than a subset of it.
    ``None`` means at least one result could be neither billed nor priced,
    which the gate turns into a failing value rather than $0.00.
    """
    if not results:
        return None
    return measured_cost(results, requests, prefer_reported=True)


def scene_sfx_timeline(plan: dict[str, Any], clips: dict[str, Path]) -> list[tuple[Path, float]]:
    """Where each scene's SFX clip starts, in seconds from the composition's 0.

    Scene windows are contiguous (see ``rewrite_timings``), so a scene's start
    is the sum of the durations before it. A scene with no clip is skipped but
    still advances the cursor — dropping the advance would slide every later
    cue earlier by that scene's length.
    """
    timeline: list[tuple[Path, float]] = []
    cursor = 0.0
    for scene in plan.get("scenes", []) or []:
        clip = clips.get(str(scene.get("id") or ""))
        if clip is not None and clip.exists():
            timeline.append((clip, round(cursor, 3)))
        cursor += float(scene.get("duration_seconds") or 0.0)
    return timeline


def footage_check(report: Sequence[dict[str, Any]]) -> QaCheck:
    """How the footage scenes were filled; fails only on an unjudged stock clip."""
    stock = [row for row in report if row["source"] == "stock"]
    own = [row for row in report if row["source"] == "file"]
    # A stock photo has no clip to judge; it is reported but never fails the check.
    unjudged = [
        row["scene_id"] for row in stock if not row["judged"] and row.get("kind") != "photo"
    ]
    fallback = len(report) - len(stock) - len(own)
    unit = "footage scenes" if not any("shot" in row for row in report) else "footage shots"
    detail = f"{len(stock)}/{len(report)} {unit} on stock, {fallback} AI fallback"
    if own:
        detail += f", {len(own)} own file"
    if unjudged:
        detail += f"; not judged: {', '.join(unjudged)}"
    return QaCheck("footage", not unjudged, detail)


def _srt_word_count(srt_text: str) -> int:
    """Words in the cue text of a SubRip document (index/timing lines skipped)."""
    count = 0
    for line in srt_text.splitlines():
        line = line.strip()
        if not line or line.isdigit() or "-->" in line:
            continue
        count += len(line.split())
    return count


def run_explainer_job(
    ctx: JobContext,
    script: ExplainerScript,
    plan: dict[str, Any],
    *,
    config_path: Path | None = None,
    tts_provider: str = "tts_router",
    image_provider: str = "auto",
    music_provider: str = "poyo_music",
    music_path: Path | None = None,
    caption_preset: str = "",
    final: bool = False,
    sfx_provider: str = "",
    footage_judge: Judge | None = None,
) -> ExplainerResult:
    """Generate assets, time the plan, render, mix, caption, normalise, check.

    ``footage_judge`` checks each stock clip (default: the router's vision
    model, built only when the script has footage scenes).
    """
    ctx.ensure_dirs()
    if music_path is not None and not Path(music_path).is_file():
        # Fail before any paid request rather than after the render.
        raise ValueError(f"music file not found: {music_path}")
    plan_path = ctx.work_dir / "plan" / "scene_plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)

    requests = explainer_job_requests(
        plan,
        script,
        ctx.job_id,
        tts_provider=tts_provider,
        image_provider=image_provider,
        music_provider=music_provider,
        music_path=music_path,
        sfx_provider=sfx_provider,
    )
    config = Settings.load(config_path).config.assets
    has_footage = any(scene.get("type") == "footage" for scene in plan.get("scenes", []))
    footage_report: list[dict[str, Any]] = []
    if has_footage:
        if footage_judge is None:
            from video_editor_llm.router import ModelRouter

            judge_router = ModelRouter.from_config(config_path)
            # The judge makes one call per pick, up to tries x libraries per clip;
            # the stock per-job default (50) ran out mid-pass on a 35-shot video.
            size_router_for_footage(judge_router, requests, config.stock_video_chain_for())
            footage_judge = router_judge(judge_router)
        results, footage_report = asyncio.run(
            resolve_with_footage(
                lambda: AssetIngestionManager(ctx, config=config, final=final),
                requests,
                judge=footage_judge,
                stock_chain=config.stock_video_chain_for(),
            )
        )
    else:
        manager = AssetIngestionManager(ctx, config=config, final=final)
        results = asyncio.run(manager.resolve(requests))
    if footage_report:
        (ctx.work_dir / "footage_report.json").write_text(
            json.dumps(footage_report, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    if script.ai_label:
        results = label_ai_stills(results, ctx.work_dir / "labelled", text=script.ai_label)
    model = plan_model(plan)
    attach_results_to_plan(model, results)
    plan = model.model_dump(mode="json")
    # Map regions/stickers that asked for a photo now point at the file.
    attach_map_media(plan, results)
    attach_footage_shots(plan, results)
    # The narration runs at job scope (``scene_id="job"``), so
    # ``attach_results_to_plan`` drops it; look it up by asset id (F2).
    narrations = [scene.narration for scene in script.scenes]
    scene_ids = [scene.id for scene in script.scenes]
    if script.narration_mode == "per_scene":
        by_id = {result.asset_id: result for result in results}
        scene_clips: list[Path] = []
        for scene_id in scene_ids:
            clip = by_id.get(f"{NARRATION_PREFIX}{scene_id}")
            if clip is None:
                raise RuntimeError(f"no narration clip for {scene_id}")
            scene_clips.append(Path(clip.local_path))
        narration_path = ctx.work_dir / "narration_joined.mp3"
        durations = join_scene_narration(scene_clips, narration_path)
        plan["narration_path"] = str(narration_path)
    else:
        narration = next((result for result in results if result.asset_id == "job_narration"), None)
        if narration is None:
            raise RuntimeError("the narration request returned no asset")
        narration_path = Path(narration.local_path)
        plan["narration_path"] = narration.local_path
    if music_path is None:
        music = next((result for result in results if result.asset_id == "job_music"), None)
        music_path = Path(music.local_path) if music is not None else None
    if music_path is not None:
        plan["music_path"] = str(music_path)

    audio_end = probe_media(narration_path).duration_ms() / 1000.0
    if script.narration_mode == "per_scene":
        # Each clip's length is measured, so the scene layout is exact; no
        # recogniser is needed (Whisper skipped whole Telugu passages).
        words, timings = per_scene_timings(
            narrations,
            durations,
            gap=SCENE_GAP_SECONDS,
            ids=scene_ids,
            spoken=script.spoken,
        )
        transcript: dict[str, Any] = {"words": words}
        (ctx.work_dir / "narration_words.json").write_text(
            json.dumps(transcript, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    else:
        # Timing: one transcript for the whole narration, aligned onto the
        # display text, then turned into contiguous scene durations.
        # ``vad_filter=False`` and a short ``initial_prompt`` are the #268
        # lesson — without them a synthetic tail can be trimmed and the last
        # scene collapses. The prompt is the title, never the narration's
        # opening words: faster-whisper treats a leading transcript as
        # already-spoken context and skips the opening seconds of the audio.
        from video_editor_transcribe.whisper_local import transcribe_audio

        settings = resolve_transcription_settings(config_path)
        transcript = transcribe_audio(
            narration_path,
            ctx.work_dir / "narration_words.json",
            model_size=settings.model,
            language=transcription_language(script.voice_language, settings.language),
            word_timestamps=True,
            device=settings.device,
            compute_type=settings.compute_type,
            initial_prompt=script.title or None,
            vad_filter=False,
        )
        words = align_words(narrations, transcript["words"])
        spread_pinned_tail(words, audio_end)
        timings = derive_scene_timings(narrations, words, ids=scene_ids)
        timings = reconcile_final_tail(timings, audio_end)
    rewrite_timings(plan, timings)
    plan["narration_duration"] = round(audio_end, 3)
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

    width, height = canvas_for_aspect(plan.get("aspect_ratio", script.aspect_ratio))
    # The plan on disk keeps ``music_path`` as metadata; the renderer gets a
    # copy without it, so ``mix_background_music`` is the only music bed.
    render_plan_path = ctx.work_dir / "plan" / "render_plan.json"
    render_plan_path.write_text(
        json.dumps(render_plan_document(plan), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    rendered = render_scene_plan(
        render_plan_path, ctx.work_dir / "rendered.mp4", ctx.work_dir / "composition"
    )
    mixed = (
        mix_background_music(rendered, music_path, ctx.work_dir / "mixed.mp4")
        if music_path is not None
        else rendered
    )
    # Cues land at each scene's cut, so they are mixed after the music bed and
    # before the captions: loudnorm then measures the finished mix, and the
    # caption burn only re-encodes the picture.
    if sfx_provider:
        clips = {
            result.scene_id: Path(result.local_path)
            for result in results
            if result.asset_type is AssetType.SFX and result.local_path
        }
        timeline = scene_sfx_timeline(plan, clips)
        if timeline:
            mixed = mix_sfx_into_video(
                mixed,
                timeline,
                ctx.work_dir / "with_sfx.mp4",
                sfx_gain_db=-6.0,
            )
    sidecar = script.captions_mode == "sidecar"
    caption_report: list[dict[str, Any]] = []
    if sidecar:
        # No burn: the mixed narration video is the deliverable, and the
        # measured words become a SubRip sidecar (the same writer `ve short`
        # uses for `captions.mode: sidecar`).
        captioned = mixed
    else:
        captioned = burn_captions(
            mixed,
            [{"word": word["word"], "start": word["start"], "end": word["end"]} for word in words],
            ctx.work_dir / "captioned.mp4",
            width=width,
            height=height,
            preset=caption_preset or script.caption_preset,
            report=caption_report,
            cache_dir=ctx.work_dir / "overlay_cache",
        )
        (ctx.work_dir / "caption_lines.json").write_text(
            json.dumps(caption_report, indent=2), encoding="utf-8"
        )
    final_video = normalize_loudness(captioned, ctx.output_dir / "final.mp4")
    srt_path: Path | None = None
    srt_words = 0
    if sidecar:
        srt_path = ctx.output_dir / "final.srt"
        srt_text = srt_from_words(words)
        srt_path.write_text(srt_text, encoding="utf-8")
        srt_words = _srt_word_count(srt_text)

    # QA gate, measured on the file that ships. Every field below comes from a
    # measurement of the finished build; anything that could not be measured
    # is passed as a value that fails its check rather than left at a default.
    probe = probe_media(final_video)
    duration = probe.duration_ms() / 1000.0
    try:
        loudness_lufs, true_peak_db = measure_loudness(final_video)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        logger.warning("explainer_loudness_unmeasured", error=str(exc))
        loudness_lufs, true_peak_db = math.nan, math.nan
    cost_usd, estimate_usd = cost_check_values(
        ctx.work_dir / "plan" / "cost_estimate.json", build_cost(results, requests)
    )
    # A composite scene is a still scene only when it actually asks for a
    # still; quote and section plates are drawn from the paper palette.
    still_scenes = sum(
        1
        for scene in plan["scenes"]
        if scene["type"] not in {"typographic", "map", "composite", "footage"}
        or (scene["type"] == "composite" and scene.get("visual_description"))
    )
    display_words = len(script.narration.split())
    # ``words`` is the aligned display stream, whose ends are forced onto the
    # next word's start (every gap is 0). Silence must be measured on the raw
    # transcript instead, which keeps the real inter-word gaps.
    raw_words = transcript.get("words") or []
    degraded_reasons = cost_tracking_degraded_reasons(ctx.work_dir)
    inputs = QaInputs(
        aspect_ratio=plan.get("aspect_ratio", script.aspect_ratio),
        width=probe.width,
        height=probe.height,
        fps=float(probe.fps),
        duration=duration,
        loudness_lufs=loudness_lufs,
        true_peak_db=true_peak_db,
        target_seconds=script.target_seconds,
        scene_starts=[timing.start for timing in timings],
        first_word_starts=[
            float(timing.words[0]["start"]) if timing.words else math.inf for timing in timings
        ],
        # Sidecar: measured against the SRT that ships beside the video.
        caption_coverage=(srt_words if sidecar else len(words)) / display_words
        if display_words
        else 0.0,
        still_scenes=still_scenes,
        # A footage scene's fallback still is not part of the reuse plan.
        still_renders=sum(
            1
            for result in results
            if result.asset_type == AssetType.IMAGE and counts_as_scene_still(result.asset_id)
        ),
        expected_still_renders=_expected_still_renders(ctx),
        max_silent_gap=max_silent_gap(raw_words) if raw_words else math.inf,
        tail_seconds=duration - float(words[-1]["end"]) if words else math.inf,
        cost_usd=cost_usd,
        estimate_usd=estimate_usd,
        stub_assets=tuple(stub_asset_ids(ctx.work_dir, used_paths=used_asset_paths(results))),
        cost_tracking_degraded=bool(degraded_reasons),
        cost_tracking_reasons=tuple(degraded_reasons),
    )
    # Composition gates: glyphs, cues, safe area, hyperframes check, frames.
    extra_checks: list[QaCheck] = []
    composition_dir = ctx.work_dir / "composition"
    if composition_dir.is_dir():
        try:
            extra_checks = gather_composition_qa(
                composition_dir=composition_dir,
                plan=plan,
                frame_dir=ctx.work_dir / "qa" / "frames",
            )
        except Exception as exc:  # noqa: BLE001 - a gate that cannot run must fail
            logger.warning("composition_gates_failed", error=str(exc))
            extra_checks = [QaCheck("composition_gates", False, str(exc))]
    else:
        extra_checks = [QaCheck("composition_gates", False, "no composition project to gate")]
    extra_checks.extend(
        [
            *(
                [
                    QaCheck("caption_tokens", True, "sidecar: no burned captions"),
                    QaCheck("caption_contrast", True, "sidecar: no burned captions"),
                ]
                if sidecar
                else [
                    caption_tokens_check([row["text"] for row in caption_report], script.narration),
                    caption_contrast_check(caption_report),
                ]
            ),
            QaCheck(*_cut_fields(sentence_cuts_check(timings))),
        ]
    )
    if footage_report:
        extra_checks.append(footage_check(footage_report))
    checks = evaluate_qa(inputs, extra_checks)
    qa_path = ctx.work_dir / "qa.json"
    qa_path.write_text(json.dumps(qa_report(inputs, checks), indent=2), encoding="utf-8")
    qa_text = format_qa(checks)
    passed = all(check.ok for check in checks)
    if passed:
        ctx.manifest().stage_done("qa", {"qa": str(qa_path)})
    logger.info("explainer_complete", job_id=ctx.job_id, final=str(final_video), passed=passed)
    return ExplainerResult(
        final_path=final_video,
        plan_path=plan_path,
        qa_path=qa_path,
        passed=passed,
        checks=checks,
        srt_path=srt_path,
        qa_text=qa_text,
    )
