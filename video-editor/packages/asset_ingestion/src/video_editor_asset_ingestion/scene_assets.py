"""Derive asset requests from scene plans and attach results back to scenes."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import structlog
from video_editor_core.models.motion import motion_duration_for
from video_editor_core.models.scene_plan import Scene, ScenePlan

from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType

logger = structlog.get_logger(__name__)


def scene_wants_motion(scene: Scene) -> bool:
    """Return True when the scene asks for real motion video.

    Two prop names reach this stage. ``requires_motion`` is the *intent* a
    script-to-video planner records; ``needs_motion`` is the *state* the
    storyteller planner records once a clip actually landed. An explicit
    ``requires_motion`` is authoritative; otherwise either name counts.
    """
    for key in ("requires_motion", "needs_motion"):
        value = scene.props.get(key)
        if isinstance(value, bool):
            return value
        # JSON plans sometimes serialize booleans as strings.
        if isinstance(value, str):
            return value.strip().lower() == "true"
    return False


def _planned_model(scene: Scene, key: str) -> str | None:
    """Read a per-scene model choice (``image_model`` / ``motion_model``).

    The tiered planners record these in ``props``; the request carries the
    model so the provider generates what was planned and ``ve estimate`` prices
    the request's own model instead of the provider's default (#262).
    """
    value = scene.props.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


# TTS casting keys a scene may carry in ``props``. They are forwarded to the
# provider as request params so a plan can pick the model, the voice and the
# language per scene (e.g. Sarvam's v4 Flash needs a model-specific speaker
# such as ``sarang_en_narration`` rather than the bare ``shubh`` default).
_TTS_CASTING_KEYS = ("voice_id", "language", "model", "speed")


def _tts_params(scene: Scene) -> dict[str, Any]:
    """Build the TTS request params for a scene, including per-scene casting."""
    params: dict[str, Any] = {"duration": scene.duration_seconds}
    for key in _TTS_CASTING_KEYS:
        value = scene.props.get(key)
        if value not in (None, ""):
            params[key] = value
    return params


# Generation options a scene may carry in ``props`` as ``image_<key>``, e.g.
# ``image_resolution: "2K"``. They become the request's ``model_kwargs`` —
# what the provider sends, and what the price is tiered on. A plan that leaves
# the tier out gets the provider's default, which is also the base price.
_IMAGE_KWARG_KEYS = ("resolution", "size", "quality")


def _image_params(scene: Scene) -> dict[str, Any]:
    """Build the IMAGE request params for a scene, including its price tier."""
    params: dict[str, Any] = {}
    image_model = _planned_model(scene, "image_model")
    if image_model:
        params["model"] = image_model
    model_kwargs = {
        key: scene.props[f"image_{key}"]
        for key in _IMAGE_KWARG_KEYS
        if scene.props.get(f"image_{key}") not in (None, "")
    }
    if model_kwargs:
        params["model_kwargs"] = model_kwargs
    return params


def requests_from_scene(
    scene: Scene,
    job_id: str,
    tts_provider: str = "stub_tts",
    image_provider: str = "stub_image",
    data_viz_provider: str = "matplotlib_data_viz",
    video_provider: str = "",
    emit_image: bool = True,
    emit_tts: bool = True,
) -> list[AssetRequest]:
    """Build asset requests for a single scene based on its populated fields.

    When ``video_provider`` is non-empty AND the scene has a
    non-empty ``visual_description``, a VIDEO request is appended
    **before** the IMAGE request so the renderer (which picks the
    first ``image | video | data_viz`` asset) wins on the video path.
    The IMAGE request is still emitted as a low-cost fallback so that
    if the video request fails the renderer has something to show.

    ``emit_image=False`` skips the IMAGE request for a scene showing another
    scene's still; ``emit_tts=False`` skips its TTS request when the caller
    narrates the whole job in one request.
    """
    requests: list[AssetRequest] = []

    if emit_tts and scene.narration.strip():
        requests.append(
            AssetRequest(
                asset_id=f"{scene.id}_tts",
                job_id=job_id,
                scene_id=scene.id,
                asset_type=AssetType.TTS,
                prompt=scene.narration,
                provider=tts_provider,
                output_ext="mp3",
                params=_tts_params(scene),
            )
        )

    if scene.visual_description.strip():
        motion_scene = scene_wants_motion(scene)
        # Append VIDEO first when opted in so it wins the
        # ``scene.assets.find(image|video|data_viz)`` race in the renderer.
        if video_provider and motion_scene:
            params: dict[str, Any] = {
                # Providers accept whole seconds in a fixed window, so a
                # 20 s scene asks for 10 s and the renderer loops the rest
                # (#257, same rule as the storyteller planner).
                "duration_seconds": motion_duration_for(scene.duration_seconds),
            }
            motion_model = _planned_model(scene, "motion_model")
            if motion_model:
                params["model"] = motion_model
            requests.append(
                AssetRequest(
                    asset_id=f"{scene.id}_video",
                    job_id=job_id,
                    scene_id=scene.id,
                    asset_type=AssetType.VIDEO,
                    prompt=scene.visual_description,
                    provider=video_provider,
                    output_ext="mp4",
                    params=params,
                )
            )
        image_params: dict[str, Any] = _image_params(scene)
        if emit_image:
            requests.append(
                AssetRequest(
                    asset_id=f"{scene.id}_image",
                    job_id=job_id,
                    scene_id=scene.id,
                    asset_type=AssetType.IMAGE,
                    prompt=scene.visual_description,
                    provider=image_provider,
                    output_ext="png",
                    params=image_params,
                )
            )
        # A composite ``compare`` scene shows two stills. The second prompt is
        # carried on the scene's props and is a separate paid request; results
        # attach to the same scene in request order, so index 0 is the scene's
        # own visual description and index 1 is this one.
        second_prompt = str(scene.props.get("image_b_prompt") or "").strip()
        if emit_image and second_prompt:
            requests.append(
                AssetRequest(
                    asset_id=f"{scene.id}_image_b",
                    job_id=job_id,
                    scene_id=scene.id,
                    asset_type=AssetType.IMAGE,
                    prompt=second_prompt,
                    provider=image_provider,
                    output_ext="png",
                    params=dict(image_params),
                )
            )

    if scene.asset_query.strip() and scene.asset_query.startswith("data:"):
        requests.append(
            AssetRequest(
                asset_id=f"{scene.id}_dataviz",
                job_id=job_id,
                scene_id=scene.id,
                asset_type=AssetType.DATA_VIZ,
                prompt=scene.asset_query,
                provider=data_viz_provider,
                params={"data": scene.props.get("chart_data", {})},
                output_ext="png",
            )
        )

    return requests


def attach_results_to_scene(scene: Scene, results: list[AssetResult]) -> Scene:
    """Attach asset results that belong to ``scene`` back into the scene model."""
    scene.assets = [r for r in results if r.scene_id == scene.id]
    return scene


# Scene props that name the scene whose still this one shows. ``image_scene`` is
# what the saju/storyteller builds write; ``reuse_image_from`` is accepted as
# the more explicit spelling.
_IMAGE_REUSE_KEYS = ("image_scene", "reuse_image_from")


def image_reuse_target(scene: Scene) -> str | None:
    """The scene id whose still ``scene`` shows, if it declares one.

    A self-reference means "my own still", not a reuse, so it returns ``None``.
    """
    for key in _IMAGE_REUSE_KEYS:
        value = scene.props.get(key)
        if isinstance(value, str) and value.strip():
            target = value.strip()
            if target != scene.id:
                return target
    return None


def resolve_image_reuse(plan: ScenePlan) -> dict[str, str]:
    """Map each reusing scene id to the scene that owns the still it shows.

    Chains are followed to their owner. A target that is missing from the plan
    or that forms a cycle is dropped, so the scene falls back to generating its
    own still — a typo can cost one render, never a blank scene (#271).
    """
    by_id = {scene.id: scene for scene in plan.scenes}
    resolved: dict[str, str] = {}
    for scene in plan.scenes:
        target = image_reuse_target(scene)
        if target is None:
            continue
        seen = {scene.id}
        while True:
            if target in seen or target not in by_id:
                target = None
                break
            seen.add(target)
            following = image_reuse_target(by_id[target])
            if following is None:
                break
            target = following
        if target is not None:
            resolved[scene.id] = target
    return resolved


def requests_from_plan(
    plan: ScenePlan,
    job_id: str,
    tts_provider: str = "stub_tts",
    image_provider: str = "stub_image",
    data_viz_provider: str = "matplotlib_data_viz",
    video_provider: str = "",
    emit_tts: bool = True,
) -> list[AssetRequest]:
    """Every asset request a plan implies, one still per distinct image.

    The plan-level entry point for pipelines and the cost gate: a scene that
    shows another scene's still (``props.image_scene``) does not emit its own
    IMAGE request, so a five-still job is five renders and not one per scene
    (#271).
    """
    reused = resolve_image_reuse(plan)
    requests = [
        request
        for scene in plan.scenes
        for request in requests_from_scene(
            scene,
            job_id,
            tts_provider=tts_provider,
            image_provider=image_provider,
            data_viz_provider=data_viz_provider,
            video_provider=video_provider,
            emit_image=scene.id not in reused,
            emit_tts=emit_tts,
        )
    ]
    # #291: a still with no framing of its own was drawn at the providers'
    # 16:9 default (and square by agy) even for a 9:16 plan. Only non-16:9
    # plans get the key, so the cache keys of 16:9 jobs do not move.
    if plan.aspect_ratio and plan.aspect_ratio.strip() != "16:9":
        for request in requests:
            if request.asset_type is AssetType.IMAGE and request.aspect() is None:
                request.params["aspect_ratio"] = plan.aspect_ratio.strip()
    return requests


def attach_results_to_plan(plan: ScenePlan, results: list[AssetResult]) -> None:
    """Attach results to their scenes, then share a reused still with its scenes.

    A reusing scene gets its own copy of the owner's IMAGE result — new asset
    id and scene id, same file — because every renderer reads ``scene.assets``
    and none of them knows about ``image_scene`` (#271). TTS and motion video
    stay per scene; only the still is shared.
    """
    for scene in plan.scenes:
        attach_results_to_scene(scene, results)

    by_id = {scene.id: scene for scene in plan.scenes}
    for scene_id, owner_id in resolve_image_reuse(plan).items():
        scene = by_id[scene_id]
        owner = by_id[owner_id]
        image = next((a for a in owner.assets if a.asset_type == AssetType.IMAGE), None)
        if image is None:
            logger.warning(
                "image_reuse_without_source",
                scene_id=scene_id,
                owner_id=owner_id,
            )
            continue
        scene.assets.append(
            image.model_copy(
                update={
                    "asset_id": f"{scene.id}_image",
                    "scene_id": scene.id,
                    "metadata": {**image.metadata, "reused_from": owner_id},
                }
            )
        )


def local_media_requests_for_scene(
    scene: Scene,
    job_id: str,
    provider: str = "local_media",
) -> list[AssetRequest]:
    """Build IMAGE/VIDEO asset requests from a scene's local ``asset_query``.

    The ``musical_video`` planner stores the filesystem path of the user
    media in ``scene.asset_query``. This helper turns each path into an
    ``AssetRequest`` of the correct type so the renderer can consume it.
    Non-media extensions are ignored with a warning so a stray file does not
    break the whole pipeline.
    """
    if not scene.asset_query.strip():
        return []

    path = Path(scene.asset_query)
    ext = path.suffix.lower().lstrip(".")
    image_exts = {"png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff"}
    video_exts = {"mp4", "mov", "m4v", "mkv", "webm", "avi"}

    if ext in image_exts:
        asset_type = AssetType.IMAGE
    elif ext in video_exts:
        asset_type = AssetType.VIDEO
    else:
        import structlog

        logger = structlog.get_logger(__name__)
        logger.warning(
            "local_media_unknown_extension",
            scene_id=scene.id,
            path=str(path),
            ext=ext,
        )
        return []

    return [
        AssetRequest(
            asset_id=f"{scene.id}_local_media",
            job_id=job_id,
            scene_id=scene.id,
            asset_type=asset_type,
            prompt=str(path),
            provider=provider,
            output_ext=ext,
        )
    ]


def music_request(
    job_id: str,
    prompt: str,
    duration_seconds: int = 30,
    provider: str = "poyo_music",
) -> AssetRequest:
    """Build a single MUSIC asset request for an entire job.

    Music is a job-level (not per-scene) asset — one track plays
    under the whole reel. ``asset_id`` is a fixed ``"job_music"`` so
    the cache reuses the same track across pipeline re-runs as long
    as ``prompt`` / ``provider`` / ``duration_seconds`` are stable.
    """
    return AssetRequest(
        asset_id="job_music",
        job_id=job_id,
        scene_id="job",
        asset_type=AssetType.MUSIC,
        prompt=prompt,
        provider=provider,
        output_ext="mp3",
        params={"duration_seconds": duration_seconds},
    )


#: SFX providers that *search* a sound index instead of synthesising from a
#: text prompt. The two want opposite inputs: a search takes a few keywords
#: ("map whoosh"), a generative model takes the prose that describes the scene.
#: Handing Freesound a 200-character diffusion prompt matches on words like
#: "no people" and "layered", so the clip is wrong even when the search hits.
SEARCH_SFX_PROVIDERS: frozenset[str] = frozenset({"freesound_sfx", "openverse_sfx", "earshot_sfx"})

#: The short transition sound each scene type takes from a search provider.
#: These are *in-cues* — one lands on the beat as the scene arrives — rather
#: than ambience, which would sit under the narration for its whole length.
TYPE_SFX_QUERY: dict[str, str] = {
    "map": "map whoosh",
    "typographic": "soft impact",
    "composite": "paper slide",
    "broll": "soft whoosh",
    "footage": "soft whoosh",
    "transition": "soft whoosh",
    "opening": "soft impact",
    "closing": "soft impact",
}

#: Words in ``visual_description`` that are worth searching for on their own,
#: tried before the scene type's generic cue. First match wins.
_CONTENT_SFX_QUERY: tuple[tuple[tuple[str, ...], str], ...] = (
    (("rain", "monsoon"), "rain"),
    (("dune", "desert", "sand"), "desert wind"),
    (("lava", "basalt", "plateau"), "low rumble"),
    (("river", "delta", "waterfall"), "water"),
    (("coast", "sea", "shore", "beach", "wave"), "waves"),
    (("peak", "snow", "summit", "ridge", "mountain"), "wind mountain"),
    (("forest", "mangrove", "jungle"), "forest"),
    (("city", "street", "crowd", "traffic"), "city street"),
)


def sfx_query_for_scene(scene: Scene, provider: str = "") -> str:
    """The text to send a SFX provider for ``scene`` ("" means no cue).

    A generative provider gets the scene's own visual description, which is
    what it was always given. A search provider gets keywords, in order:

    1. a content word the description names that also has a usable sound
       (a delta scene asks for water, not "no people");
    2. the scene type's transition cue, for plates that carry no description
       at all — typographic cards and maps used to get nothing, which left the
       most cut-heavy scenes silent;
    3. the opening words of the description, so a scene type this table does
       not know still asks for something related rather than dropping out.
    """
    description = scene.visual_description.strip()
    search = provider in SEARCH_SFX_PROVIDERS
    if description and not search:
        return description
    lowered = description.lower()
    for words, query in _CONTENT_SFX_QUERY:
        if any(word in lowered for word in words):
            return query
    by_type = TYPE_SFX_QUERY.get(scene.type.strip().lower())
    if by_type:
        return by_type
    return _leading_words(description)


def _leading_words(description: str, limit: int = 5) -> str:
    """The first few words of a description, as a search query."""
    words = re.findall(r"[A-Za-z]+", description)
    return " ".join(words[:limit]).lower()


def sfx_requests_for_scene(
    scene: Scene,
    job_id: str,
    sfx_provider: str = "",
    duration_seconds: float = 5.0,
) -> list[AssetRequest]:
    """Build a SFX asset request for a single scene.

    Returns an empty list when ``sfx_provider`` is empty (opt-out) or the
    scene offers no cue at all (see :func:`sfx_query_for_scene`). The cache
    key is keyed on the scene id so each scene gets its own SFX clip.
    """
    if not sfx_provider:
        return []
    query = sfx_query_for_scene(scene, sfx_provider)
    if not query:
        return []
    return [
        AssetRequest(
            asset_id=f"{scene.id}_sfx",
            job_id=job_id,
            scene_id=scene.id,
            asset_type=AssetType.SFX,
            prompt=query,
            provider=sfx_provider,
            output_ext="mp3",
            params={"duration_seconds": duration_seconds},
        )
    ]


def alignment_request(
    job_id: str,
    audio_path: str,
    text: str,
    provider: str = "",
    with_alignment: bool = True,
    enabled_speech_timestamps: list[str] | None = None,
) -> AssetRequest:
    """Build a job-level ALIGNMENT request.

    Forced alignment is a single job-level operation: take one
    audio file and one transcript text, return word-level (and
    optionally character-level) timestamps. Like
    ``music_request()``, this returns a single ``AssetRequest``
    with a stable ``asset_id="job_alignment"`` so re-runs of the
    same audio/text are cache-hits.

    Args:
        job_id: Pipeline job id.
        audio_path: Path to the audio file to align against.
        text: Known transcript text.
        provider: Registered provider name. Default is the
            ElevenLabs forced-alignment endpoint.
        with_alignment: When True (default), include character-
            level alignment data in the response. Set False for
            word-only alignment (smaller response).
        enabled_speech_timestamps: Optional list of words to mark
            as speech boundaries (passed through to the API).
    """
    params: dict[str, object] = {
        "audio_path": audio_path,
        "text": text,
        "with_alignment": with_alignment,
    }
    if enabled_speech_timestamps is not None:
        params["enabled_speech_timestamps"] = enabled_speech_timestamps

    return AssetRequest(
        asset_id="job_alignment",
        job_id=job_id,
        scene_id="job",
        asset_type=AssetType.ALIGNMENT,
        prompt=text,
        provider=provider,
        output_ext="json",
        params=params,
    )


def isolation_request(
    job_id: str,
    audio_path: str,
    provider: str = "",
    file_format: str = "other",
    output_ext: str = "mp3",
) -> AssetRequest:
    """Build a job-level ISOLATION request.

    Audio isolation is a single job-level operation: take one audio or
    video file and return an isolated audio track with background noise
    and music suppressed. Like ``music_request()`` and ``alignment_request()``
    it uses a stable ``asset_id="job_isolation"`` so re-runs of the same
    input are cache hits.

    Args:
        job_id: Pipeline job id.
        audio_path: Path to the audio/video file to isolate.
        provider: Registered provider name. Default is the ElevenLabs
            ``/v1/audio-isolation`` endpoint.
        file_format: Optional API hint (``"pcm_s16le_16"`` or ``"other"``).
        output_ext: Desired output extension (default ``mp3``).
    """
    from video_editor_asset_ingestion.providers.audio.elevenlabs_isolation import (
        _reconcile_file_format,
    )

    return AssetRequest(
        asset_id="job_isolation",
        job_id=job_id,
        scene_id="job",
        asset_type=AssetType.ISOLATION,
        prompt="",
        provider=provider,
        output_ext=output_ext,
        params={
            "audio_path": audio_path,
            "file_format": _reconcile_file_format(file_format, output_ext),
        },
    )


def speech_to_speech_request(
    job_id: str,
    audio_path: str,
    voice_id: str,
    provider: str = "",
    model_id: str = "eleven_english_sts_v2",
    output_format: str = "mp3_44100_128",
    remove_background_noise: bool = False,
    output_ext: str = "mp3",
    voice_settings: dict[str, Any] | None = None,
    seed: int = 0,
) -> AssetRequest:
    """Build a job-level SPEECH_TO_SPEECH request.

    Speech-to-speech is a single job-level operation: take one audio
    file and a target ``voice_id``, return the same content spoken in
    the target voice. Uses a stable ``asset_id="job_speech_to_speech"``
    so re-runs with the same input/voice/model are cache hits.

    Args:
        job_id: Pipeline job id.
        audio_path: Path to the source audio file.
        voice_id: ElevenLabs voice id to convert into.
        provider: Registered provider name. Default is the ElevenLabs
            ``/v1/speech-to-speech/{voice_id}`` endpoint.
        model_id: STS model id (``eleven_english_sts_v2`` or
            ``eleven_multilingual_sts_v2``).
        output_format: ElevenLabs output-format string.
        remove_background_noise: Whether to run audio isolation before
            the voice conversion.
        output_ext: Desired output extension (default ``mp3``).
        voice_settings: Optional ElevenLabs voice-settings dict.
        seed: Optional deterministic seed (stored in params).
    """
    from video_editor_asset_ingestion.providers.audio.elevenlabs_speech_to_speech import (
        _reconcile_output_format,
    )

    params: dict[str, Any] = {
        "audio_path": audio_path,
        "voice_id": voice_id,
        "model_id": model_id,
        "output_format": _reconcile_output_format(output_format, output_ext),
        "remove_background_noise": remove_background_noise,
    }
    if voice_settings:
        params["voice_settings"] = voice_settings
    if seed:
        params["seed"] = seed
    return AssetRequest(
        asset_id="job_speech_to_speech",
        job_id=job_id,
        scene_id="job",
        asset_type=AssetType.SPEECH_TO_SPEECH,
        prompt=voice_id,
        provider=provider,
        output_ext=output_ext,
        params=params,
    )


def forced_alignment_response_to_transcript(
    response: dict[str, Any],
    source_audio: str = "",
    language: str | None = None,
) -> dict[str, Any]:
    """Convert an ElevenLabs alignment response to a WhisperX-shaped transcript.

    The CaptionsSkill consumes ``transcript.json`` with
    ``{words: [{word, start, end, probability}]}`` (the WhisperX
    shape). ElevenLabs returns ``{words: [{text, start, end, ...}]}``.
    This helper does the one-line ``text`` → ``word`` rename and
    adds a synthetic ``probability=1.0`` (ElevenLabs doesn't
    expose per-word confidence, so we use 1.0 — alignment is
    deterministic given the input audio + text).

    Args:
        response: The parsed JSON response from
            ``/v1/forced-alignment`` (or a stub equivalent).
        source_audio: The path to the audio file the alignment
            was run against. Stored in the transcript's
            ``source_audio`` field for traceability.
        language: Override the language. Defaults to whatever
            the response set (``"en"`` from ElevenLabs).

    Returns:
        A transcript dict matching the WhisperX schema:

        .. code-block:: python

            {
                "source_audio": "/path/to/audio.mp3",
                "language": "en",
                "language_probability": 1.0,
                "word_timestamps": True,
                "segments": [...],   # reconstructed from words
                "words": [
                    {"word": "hello", "start": 0.0, "end": 0.5,
                     "probability": 1.0},
                    ...
                ],
            }
    """
    raw_words = response.get("words", [])
    words: list[dict[str, Any]] = []
    for w in raw_words:
        text = w.get("text") or w.get("word") or ""
        if not text.strip():
            continue
        start = w.get("start")
        end = w.get("end")
        if start is None or end is None:
            # Skip words with missing timestamps; they cannot be positioned
            # on the timeline. This avoids a TypeError from float(None) while
            # preserving the rest of the transcript (Bug #16).
            continue
        words.append(
            {
                "word": text.strip(),
                "start": float(start),
                "end": float(end),
                "probability": 1.0,  # ElevenLabs doesn't expose per-word confidence
            }
        )

    # Reconstruct minimal segments from the words. WhisperX segments
    # carry a contiguous range of words; we use one segment per
    # 30-word group as a reasonable default for downstream consumers
    # that want segments for navigation.
    segments: list[dict[str, Any]] = []
    chunk_size = 30
    for i in range(0, len(words), chunk_size):
        chunk = words[i : i + chunk_size]
        if not chunk:
            continue
        segments.append(
            {
                "id": len(segments),
                "start": chunk[0]["start"],
                "end": chunk[-1]["end"],
                "text": " ".join(w["word"] for w in chunk),
                "words": chunk,
            }
        )

    return {
        "source_audio": source_audio,
        "language": language or response.get("language", "en"),
        "language_probability": 1.0,
        "word_timestamps": True,
        "segments": segments,
        "words": words,
    }
