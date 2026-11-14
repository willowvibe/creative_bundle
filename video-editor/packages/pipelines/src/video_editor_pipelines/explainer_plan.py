"""Turn a parsed script into the plan the gate prices and the renderer draws.

``build_explainer_plan`` is pure: same script in, same plan and same cost out.
A typographic scene carries no ``visual_description``, so it emits no IMAGE
request in *any* pipeline; a still scene carries its prompt, and members of a
reuse cluster additionally carry ``props.image_scene``, so
``requests_from_plan`` skips their IMAGE request (#271).

One narration request replaces one per scene: the whole script is spoken as a
single string with scene separators, which is also what makes the captions
continuous across cuts.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

import structlog
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers.tts.tts_router import _pick_provider
from video_editor_asset_ingestion.scene_assets import requests_from_plan
from video_editor_core.models.scene_plan import ScenePlan

from video_editor_pipelines.explainer_script import ExplainerScript, is_designed_still
from video_editor_pipelines.explainer_timing import SceneTiming
from video_editor_pipelines.still_reuse import ReusePlan, apply_reuse, plan_still_reuse

logger = structlog.get_logger(__name__)

PLACEHOLDER_DURATION_SECONDS = 1.0
ELEVENLABS_MIN_SPEED = 0.7
ELEVENLABS_MAX_SPEED = 1.2

#: ``asset_id`` suffixes of a footage scene's two requests: the stock clip and
#: the AI still that replaces it when no clip is accepted.
FOOTAGE_VIDEO_SUFFIX = "_footage"
#: ``asset_id`` prefix of a scene's own narration clip (``narration_mode: per_scene``).
NARRATION_PREFIX = "narration_"
FALLBACK_IMAGE_SUFFIX = "_fallback_image"
#: Durations are measured after the voice exists, but the clip is picked before:
#: estimate from the narration so the library returns a clip that covers it.
_WORDS_PER_SECOND = 2.0
MIN_FOOTAGE_SECONDS = 4.0


def build_explainer_plan(
    script: ExplainerScript, job_id: str, *, reuse: bool = True
) -> tuple[dict[str, Any], ReusePlan]:
    """Build the plan document plus the reuse decision that shaped it."""
    reuse_plan = plan_still_reuse(script.scenes, threshold=script.reuse_threshold, enabled=reuse)
    scenes: list[dict[str, Any]] = []
    for scene in script.scenes:
        props = dict(scene.props)
        if scene.visual == "typographic":
            props["layout"] = scene.layout
            scenes.append(
                {
                    "id": scene.id,
                    "type": "typographic",
                    "duration_seconds": PLACEHOLDER_DURATION_SECONDS,
                    "narration": scene.narration,
                    "visual_description": "",
                    "props": props,
                }
            )
        elif scene.visual == "map":
            # The map is drawn from bundled geometry, so there is no IMAGE
            # request and no visual description for the asset stage to chase.
            scenes.append(
                {
                    "id": scene.id,
                    "type": "map",
                    "duration_seconds": PLACEHOLDER_DURATION_SECONDS,
                    "narration": scene.narration,
                    "visual_description": "",
                    "props": props,
                }
            )
        elif scene.visual == "footage":
            # No visual description: the up-front pass must not render a still.
            # The fallback prompt rides in props and becomes a request only if
            # no stock clip is accepted for this scene.
            props["stock_query"] = str(props.pop("query")).strip()
            props["fallback_image"] = scene.image
            scenes.append(
                {
                    "id": scene.id,
                    "type": "footage",
                    "duration_seconds": PLACEHOLDER_DURATION_SECONDS,
                    "narration": scene.narration,
                    "visual_description": "",
                    "props": props,
                }
            )
        elif scene.visual == "composite":
            props["layout"] = scene.layout
            if scene.image_b:
                # The second still is a separate IMAGE request; the asset stage
                # reads this prompt after the scene's own visual description.
                props["image_b_prompt"] = scene.image_b
            scenes.append(
                {
                    "id": scene.id,
                    "type": "composite",
                    "duration_seconds": PLACEHOLDER_DURATION_SECONDS,
                    "narration": scene.narration,
                    "visual_description": scene.image,
                    "props": props,
                }
            )
        else:
            if scene.subject:
                props["subject"] = scene.subject
            # A still that authors display copy is a designed plate, not a bare
            # full-bleed image. Compiling it as the over_media composite here
            # means one renderer draws the scrim and title block, cue QA checks
            # its headline, and the plan says what will actually render.
            designed = is_designed_still(props)
            if designed:
                props["layout"] = "over_media"
            scenes.append(
                {
                    "id": scene.id,
                    "type": "composite" if designed else "narration",
                    "duration_seconds": PLACEHOLDER_DURATION_SECONDS,
                    "narration": scene.narration,
                    "visual_description": scene.image,
                    "props": props,
                }
            )
    plan: dict[str, Any] = {
        "job_id": job_id,
        "script": script.title,
        "scenes": scenes,
        "aspect_ratio": script.aspect_ratio,
        "music_mood": script.music_mood,
        "music_path": "",
        "design": script.design,
        "narration_path": "",
        "narration_duration": 0.0,
    }
    apply_reuse(plan, reuse_plan)
    return plan, reuse_plan


def plan_model(plan: dict[str, Any]) -> ScenePlan:
    """Validate the document as a :class:`ScenePlan`."""
    return ScenePlan.model_validate(plan)


def _narration_params(script: ExplainerScript) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if script.voice:
        params["voice_id"] = script.voice
    if script.voice_model:
        params["model"] = script.voice_model
    if script.voice_language:
        params["language"] = script.voice_language
    if script.voice_pace is not None:
        if _pick_provider(script.voice or "", script.voice_language) == "elevenlabs":
            # ElevenLabs calls it ``speed`` and accepts 0.7-1.2.
            params["speed"] = min(
                ELEVENLABS_MAX_SPEED, max(ELEVENLABS_MIN_SPEED, script.voice_pace)
            )
        else:
            params["pace"] = script.voice_pace
    return params


def explainer_requests(
    plan: dict[str, Any],
    script: ExplainerScript,
    job_id: str,
    *,
    tts_provider: str = "stub_tts",
    image_provider: str = "stub_image",
) -> list[AssetRequest]:
    """The paid requests for ``plan``: the narration, one still per cluster.

    The narration is one request, or one per scene under
    ``narration_mode: per_scene``. Either way it runs at job scope
    (``scene_id="job"``), so no scene carries its own audio cue.
    """
    if script.narration_mode == "per_scene":
        narration = [
            AssetRequest(
                asset_id=f"{NARRATION_PREFIX}{scene.id}",
                job_id=job_id,
                scene_id="job",
                asset_type=AssetType.TTS,
                prompt=script.spoken(scene.narration),
                provider=tts_provider,
                output_ext="mp3",
                params=_narration_params(script),
            )
            for scene in script.scenes
        ]
    else:
        narration = [
            AssetRequest(
                asset_id="job_narration",
                job_id=job_id,
                scene_id="job",
                asset_type=AssetType.TTS,
                prompt=script.spoken_narration,
                provider=tts_provider,
                output_ext="mp3",
                params=_narration_params(script),
            )
        ]
    return [
        *narration,
        *requests_from_plan(
            plan_model(plan),
            job_id,
            tts_provider=tts_provider,
            image_provider=image_provider,
            emit_tts=False,
        ),
        *footage_requests(plan, job_id, image_provider=image_provider),
        *map_media_requests(plan, job_id, image_provider=image_provider),
    ]


_MAP_MEDIA_ID = re.compile(r"_(?:region|sticker)_\d+$")


def counts_as_scene_still(asset_id: str) -> bool:
    """Whether an IMAGE result is a scene still the reuse plan accounted for.

    Footage fallback stills and map photos (regions, stickers) are not.
    """
    return not (asset_id.endswith(FALLBACK_IMAGE_SUFFIX) or _MAP_MEDIA_ID.search(asset_id))


#: Map props whose entries may ask the pipeline for a photo.
_MAP_MEDIA_KEYS = (("regions", "region"), ("stickers", "sticker"))


def map_media_requests(
    plan: dict[str, Any], job_id: str, *, image_provider: str = "auto"
) -> list[AssetRequest]:
    """IMAGE requests for map regions/stickers that name ``image_query`` or ``image_prompt``.

    ``image_query`` searches the free stock libraries (``provider: stock``);
    ``image_prompt`` goes to the image chain. An entry with a local ``image``
    asks for nothing.
    """
    aspect = str(plan.get("aspect_ratio") or "").strip()
    requests: list[AssetRequest] = []
    for scene in plan.get("scenes", []) or []:
        if scene.get("type") != "map":
            continue
        props = scene.get("props") or {}
        for key, kind in _MAP_MEDIA_KEYS:
            for position, entry in enumerate(props.get(key) or []):
                if not isinstance(entry, dict) or entry.get("image"):
                    continue
                query = str(entry.get("image_query") or "").strip()
                prompt = str(entry.get("image_prompt") or "").strip()
                if not query and not prompt:
                    continue
                params: dict[str, Any] = {}
                if prompt and aspect and aspect != "16:9":
                    params["aspect_ratio"] = aspect
                requests.append(
                    AssetRequest(
                        asset_id=f"{scene['id']}_{kind}_{position}",
                        job_id=job_id,
                        scene_id=scene["id"],
                        asset_type=AssetType.IMAGE,
                        prompt=query or prompt,
                        provider="stock" if query else image_provider,
                        output_ext="jpg" if query else "png",
                        params=params,
                    )
                )
    return requests


def attach_map_media(plan: dict[str, Any], results: Sequence[Any]) -> None:
    """Point each requesting region/sticker at its fetched file; drop the request keys."""
    by_id = {str(result.asset_id): result for result in results}
    for scene in plan.get("scenes", []) or []:
        if scene.get("type") != "map":
            continue
        props = scene.get("props") or {}
        for key, kind in _MAP_MEDIA_KEYS:
            for position, entry in enumerate(props.get(key) or []):
                if not isinstance(entry, dict):
                    continue
                found = by_id.get(f"{scene['id']}_{kind}_{position}")
                if found is None:
                    continue
                entry["image"] = str(found.local_path)
                entry.pop("image_query", None)
                entry.pop("image_prompt", None)


def estimated_seconds(narration: str) -> float:
    """A pre-voice guess at a scene's length, for choosing a clip that covers it."""
    return max(MIN_FOOTAGE_SECONDS, round(len(narration.split()) / _WORDS_PER_SECOND, 1))


def footage_requests(
    plan: dict[str, Any], job_id: str, *, image_provider: str = "auto"
) -> list[AssetRequest]:
    """Per footage scene: a stock VIDEO request and its fallback IMAGE request.

    Both are priced by the gate (the worst case is every scene falling back),
    but only the VIDEO requests run up front; see :func:`initial_requests`.
    """
    aspect = str(plan.get("aspect_ratio") or "").strip()
    requests: list[AssetRequest] = []
    for scene in plan.get("scenes", []) or []:
        if scene.get("type") != "footage":
            continue
        props = scene.get("props") or {}
        requests.append(
            AssetRequest(
                asset_id=f"{scene['id']}{FOOTAGE_VIDEO_SUFFIX}",
                job_id=job_id,
                scene_id=scene["id"],
                asset_type=AssetType.VIDEO,
                prompt=str(props["stock_query"]),
                provider="stock",
                output_ext="mp4",
                params={"duration_seconds": estimated_seconds(str(scene.get("narration", "")))},
            )
        )
        image_params: dict[str, Any] = {}
        if aspect and aspect != "16:9":
            image_params["aspect_ratio"] = aspect
        requests.append(
            AssetRequest(
                asset_id=f"{scene['id']}{FALLBACK_IMAGE_SUFFIX}",
                job_id=job_id,
                scene_id=scene["id"],
                asset_type=AssetType.IMAGE,
                prompt=str(props["fallback_image"]),
                provider=image_provider,
                output_ext="png",
                params=image_params,
            )
        )
    return requests


def initial_requests(requests: Sequence[AssetRequest]) -> list[AssetRequest]:
    """Everything except the footage fallbacks, which wait for the stock pass."""
    return [r for r in requests if not r.asset_id.endswith(FALLBACK_IMAGE_SUFFIX)]


def footage_fallbacks(
    requests: Sequence[AssetRequest], *, scenes_without_clip: set[str]
) -> list[AssetRequest]:
    """The fallback stills for the footage scenes that ended without a clip."""
    return [
        r
        for r in requests
        if r.asset_id.endswith(FALLBACK_IMAGE_SUFFIX) and r.scene_id in scenes_without_clip
    ]


def rewrite_timings(plan: dict[str, Any], timings: Sequence[SceneTiming]) -> None:
    """Replace the placeholder durations with the measured ones.

    The durations are written as measured: a word may end ``0.12 s`` past an
    interior scene's boundary, and a scene held up by ``MIN_SCENE_SECONDS`` can
    start after its own first word, so no containment is asserted here and no
    word is dropped or re-timed against its window. Each word keeps the
    ``start``/``end`` the transcript produced.
    """
    by_id = {timing.id: timing for timing in timings}
    scenes = plan.get("scenes", []) or []
    scene_ids = {str(scene.get("id")) for scene in scenes}
    for unmatched in timings:
        if str(unmatched.id) not in scene_ids:
            # Nothing consumes this timing; say so rather than silently
            # leaving that scene at the 1.0 s placeholder.
            logger.warning(
                "explainer_timing_unmatched",
                job_id=plan.get("job_id"),
                scene_id=unmatched.id,
            )
    for scene in scenes:
        resolved = by_id.get(str(scene.get("id")))
        if resolved is None:
            continue
        scene["duration_seconds"] = resolved.duration
        props = scene.setdefault("props", {})
        if isinstance(props, dict):
            props["word_times"] = [
                {
                    "word": str(word["word"]),
                    "start": float(word["start"]),
                    "end": float(word["end"]),
                }
                for word in resolved.words
            ]
