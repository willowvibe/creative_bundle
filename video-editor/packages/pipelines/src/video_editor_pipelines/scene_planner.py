"""Build a ``ScenePlan`` from a raw markdown script using an LLM."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import structlog
from pydantic import ValidationError
from video_editor_core.models.scene_plan import Scene, ScenePlan
from video_editor_llm import ClassifyClient, question_choice, question_noul
from video_editor_llm.router import ModelRouter

logger = structlog.get_logger(__name__)

DEFAULT_SYSTEM = (
    "You are an expert script-to-video planner. "
    "You output only valid JSON scene plans. "
    "Every scene must have an id, type, duration_seconds, narration, "
    "and a visual_description suitable for generative image/video models."
)

#: Appended to the system prompt when the caller renders the plan with the
#: explainer's editorial scene kinds. Kept separate so the generic planner's
#: prompt (and every plan it produces) is unchanged.
EDITORIAL_SYSTEM = (
    " You also compose editorial explainer plates: maps drawn from bundled "
    "geography and designed still-and-type layouts. Put every authored plate "
    "property in the scene's props object, exactly as the schema names it."
)

#: The scene kinds and props the explainer renderer accepts. This is prompt
#: copy, not validation: the generated script is parsed back and rendered by
#: the real renderers before it can reach the estimate gate.
EDITORIAL_RULES = """
Editorial mode:
This script is rendered by the explainer's HTML plate renderers, so a scene
may also use these types and put the authored plate props in `props`:
- "typographic": a flat type plate. props.layout is one of these, with the
  props that layout requires (the renderer rejects a plate that is missing
  them, and the draft fails before it is priced):
  - big_stat: headline; optional unit, sub, cue, align (center|top; top keeps
    the text in the upper ~40% so the lower 60% is clear for end-screen cards).
  - bar_compare: bars, a list of 2-4 {label, value} maps; optional cue on a
    bar that should reveal on different words.
  - definition: term, gloss.
  - sequence: steps, a list of 3-5 strings or {label, cue} maps.
  - array: cells, an integer from 4 to 60; cols, an integer from 1 to cells;
    optional highlight, a list of integer zero-based cell indexes.
- "map": a country map drawn from bundled geography, with no image cost.
  props.country is required (an English country name, e.g. "Japan").
  Optional: focus ("lon,lat") and label for one highlighted point, bbox
  ("west,south,east,north"), projection ("mercator" or "equirectangular"),
  eyebrow, headline, subtitle, badge, badge_sub, badge_mark, watermark, and
  callouts (1-3 entries, each a short string or {text, cue}).
  To name places on the map use markers: 1-6 {text, lon, lat, cue?} maps,
  revealed in list order, each defaulting to cue on its own name. To draw a
  route between them use route: at least two [lon, lat] points, with an
  optional route_cue. Markers and route points must fall inside the map
  window. To push in between regions use zooms: 1-3 {bbox, cue?} maps, each
  bbox ("west,south,east,north") the window the camera lands on, applied in
  order and optionally cued on spoken words. Pins and place names keep their
  reading size while the map scales.
- "composite": a designed plate built around a still. props.layout is one of:
  - split: one still beside a text column. Needs visual_description (the
    still prompt). Text props: headline, body, stat {value, label}, facts
    (strings or {text, cue}), credit, side ("left" or "right").
  - over_media: a full-bleed still under a title block. Needs
    visual_description. Text props: headline, body, subtitle, badge,
    badge_sub.
  - compare: two stills. visual_description is the first prompt;
    props.image_b_prompt is the second. Props: labels (two strings),
    headline, caption.
  - quote: a pull quote, no still. props.quote is required; optional
    attribution, attribution_cue, role.
  - section: a chapter divider, no still. props.headline is required;
    props.number (e.g. "01") and props.subtitle are optional.
- "narration" (and opening, closing, broll, transition): a full-bleed still.
  Add display copy in props to make it a designed plate: eyebrow, headline,
  body and subtitle render as a title block over the still under a scrim (the
  over_media plate). Leave them off only for a pure atmosphere shot.
Any editorial scene may also carry eyebrow, watermark, and highlight (a
colour override).

Rules for editorial mode:
- Do not make every scene a plate. Open with a designed still (a still with a
  headline) or a typographic scene; use a map when a place or route matters;
  use composite layouts for the body; use a section to divide parts; use a
  quote for a line worth pausing on.
- Body content belongs on a plate (typographic, map or composite), never a
  bare still with no copy. A bare still is for atmosphere between arguments.
- For typographic, map, quote and section scenes leave visual_description
  empty: they draw no image request.
- Only set props.requires_motion when a scene needs live motion video. An
  editorial plate is rendered as HTML even when its still is.
- A cue is one to three consecutive words copied from that scene's own
  narration, never a label or a "narration:" prefix. The plate reveals it when
  the word is spoken; a cue the scene never says fails the build.
- Introduce each concept once. Do not give two scenes far apart the same
  headline or defined term (a monsoon scene and, later, a monsoon definition);
  build on the earlier scene instead.
"""

DEFAULT_ASPECT_RATIO = "16:9"

# Fallback values used when the LLM repeatedly fails to produce a valid plan.
_FALLBACK_SCENE_DURATION_SECONDS: float = 10.0

# Jev shot-type rubric for the optional classifier pass. Keys become the values
# stored in ``props["shot_type"]``.
_SHOT_TYPE_CRITERIA: dict[str, str] = {
    "stock": (
        "Real-world footage: people, places, products, or action a camera "
        "could capture or that could be found as stock video."
    ),
    "talking-head": ("A person speaking directly to camera: a presenter, host, or interviewee."),
    "data-viz": (
        "Charts, graphs, numbers, statistics, or diagrams, e.g. a data "
        "visualization or infographic."
    ),
    "ai-still": (
        "An illustrative still image or abstract/atmospheric shot that does "
        "not require live footage."
    ),
    "text-card": ("On-screen text: a title, lower third, quote, list, or call-to-action card."),
}

# A scene is flagged for generative motion video at/above this probability.
_MOTION_NOUL_THRESHOLD: float = 0.5


def _extract_json(text: str) -> str:
    """Extract JSON from an LLM response that may wrap it in markdown fences."""
    # Try to find a ```json ... ``` fenced block first.
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Fall back to the raw text (strip any leading/trailing whitespace).
    return text.strip()


def _build_prompt(
    script: str, aspect_ratio: str, music_mood: str, *, editorial: bool = False
) -> str:
    """Render the scene-plan decomposition prompt.

    ``editorial`` adds the explainer's plate grammar (map, composite,
    typographic) to the scene-type list and the rules. The generic planner's
    prompt is unchanged when it is off.
    """
    scene_types = "opening | narration | broll | closing | transition"
    if editorial:
        scene_types += " | typographic | map | composite"
    schema = {
        "job_id": "string",
        "script": "string",
        "aspect_ratio": "string",
        "music_mood": "string",
        "scenes": [
            {
                "id": "unique scene id (e.g. scene_001)",
                "type": scene_types,
                "duration_seconds": "number > 0",
                "narration": "exact spoken text for TTS",
                "visual_description": "prompt for image/video generation",
                "asset_query": "optional search query for stock/generated assets",
                "props": {},
            }
        ],
    }
    editorial_rules = EDITORIAL_RULES if editorial else ""
    return f"""Turn the following script into a scene plan for a video.

Target aspect ratio: {aspect_ratio}
Music mood: {music_mood or "auto"}

Return ONLY valid JSON matching this schema:
{json.dumps(schema, indent=2)}

Rules:
- Each scene should be 3-12 seconds long.
- The total narration across scenes should cover the full script.
- Keep narration concise and natural for spoken delivery.
- visual_description should be a rich, generative prompt.
- Use type "broll" for scenes that are primarily visual overlays.
- Use type "opening" for the first scene and "closing" for the last.
- Static image/data_viz scenes always render with a gentle Ken Burns pan/zoom
  in the renderer. You can customize the zoom path via props.motion.start and
  props.motion.end (scale, x, y offsets), but do not set props.motion.style.
- For scenes that need real motion (character moves, camera pushes in, scene
  changes atmosphere), set props.requires_motion to true. This routes the scene
  to the HyperFrames generative video provider instead of a still image.
{editorial_rules}
Script:
---
{script}
---
"""


def _build_fallback_scene_plan(
    job_id: str,
    script_text: str,
    aspect_ratio: str,
    music_mood: str,
) -> ScenePlan:
    """Return a deterministic single-scene plan when the LLM cannot.

    The fallback keeps the pipeline moving with a plain narration scene
    covering the full script. Callers can detect fallback plans by checking
    ``summary`` or by comparing scene count to expectations.
    """
    logger.warning(
        "scene_plan_fallback_used",
        job_id=job_id,
        aspect_ratio=aspect_ratio,
        music_mood=music_mood,
    )
    return ScenePlan(
        job_id=job_id,
        script=script_text,
        aspect_ratio=aspect_ratio,
        music_mood=music_mood,
        scenes=[
            Scene(
                id="scene_001",
                type="narration",
                duration_seconds=_FALLBACK_SCENE_DURATION_SECONDS,
                narration=script_text,
                visual_description="A clear visual representation of the narration.",
            )
        ],
    )


def _scene_classify_text(scene: Scene) -> str:
    """Return the visual description plus narration used to classify a scene."""
    parts = [p for p in (scene.visual_description.strip(), scene.narration.strip()) if p]
    return " | ".join(parts) or scene.id


async def _classify_scenes_with_jev(
    scenes: list[Scene],
    *,
    client: ClassifyClient | None = None,
) -> None:
    """Additively tag scene ``props`` with a Jev-classified shot type.

    Sends ONE ``classify`` call covering every scene (a ``choice`` shot-type
    question and a ``noul`` motion question per scene), then merges the
    answers into ``scene.props``. This is a no-op when no classifier is
    available or the call fails, so existing plans are byte-for-byte
    unchanged when ``TYPESAFE_API_KEY`` is unset. ``shot_type`` is a new key;
    ``requires_motion`` is only filled in when the planner has not already set
    it, so an explicit LLM choice always wins.
    """
    if not scenes:
        return
    active = client
    owns_client = active is None
    if active is None:
        try:
            active = ClassifyClient.from_config()
        except Exception:  # noqa: BLE001 - config probing must not raise
            active = ClassifyClient()
    try:
        if not active.available():
            return
        questions: dict[str, Any] = {}
        for i, scene in enumerate(scenes):
            text = _scene_classify_text(scene)
            questions[f"shot{i}"] = question_choice(
                "Which shot type best fits `scene`?",
                _SHOT_TYPE_CRITERIA,
                data={"scene": text},
            )
            questions[f"motion{i}"] = question_noul(
                "Does `scene` need real motion video rather than a still image?",
                data={"scene": text},
                true_label="Real motion video",
                false_label="Still image",
            )
        body = await active.classify({"state": "Scene shot-type review."}, questions)
        answers = body.get("answers") or {}
        for i, scene in enumerate(scenes):
            shot = answers.get(f"shot{i}") or {}
            choice = shot.get("choice")
            if isinstance(choice, str) and choice in _SHOT_TYPE_CRITERIA:
                scene.props["shot_type"] = choice
                confidence = shot.get("confidence")
                if isinstance(confidence, (int, float)):
                    scene.props["shot_type_confidence"] = float(confidence)
            motion = answers.get(f"motion{i}") or {}
            noul = motion.get("noul")
            if isinstance(noul, (int, float)) and "requires_motion" not in scene.props:
                scene.props["requires_motion"] = bool(noul >= _MOTION_NOUL_THRESHOLD)
        logger.info("scene_jev_classify", scenes=len(scenes), questions=len(questions))
    except Exception as exc:  # noqa: BLE001 - classification must never break planning
        logger.warning("scene_jev_classify_failed", error=str(exc))
    finally:
        if owns_client:
            await active.close()


async def build_scene_plan(
    job_id: str,
    script_path: Path,
    llm_router: ModelRouter,
    *,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    music_mood: str = "",
    max_retries: int = 2,
    fallback_on_error: bool = True,
    classify_client: ClassifyClient | None = None,
    editorial: bool = False,
) -> ScenePlan:
    """Decompose a markdown script into a validated ``ScenePlan``.

    The LLM is invoked up to ``max_retries + 1`` times. JSON parse failures
    and Pydantic validation errors (e.g. invalid scene types, duplicate IDs,
    negative durations) are retried. If all attempts fail and
    ``fallback_on_error`` is ``True``, a deterministic single-scene fallback
    plan is returned so the pipeline can continue. If ``fallback_on_error``
    is ``False``, the last error is raised as a ``RuntimeError``.

    When ``classify_client`` (a TypeSafe/Jev ``ClassifyClient``) is provided or
    can be built from config, each scene's ``props`` is additively annotated
    with a Jev-classified ``shot_type`` (and ``requires_motion`` when the
    planner has not set it) in a single batched call. The classifier is
    optional: without a key/classifier the produced plan is unchanged.

    ``editorial`` asks for the explainer's plate scene types (map, composite,
    typographic). It only changes the prompt; a generic caller leaves it off
    and gets the same plan it always did.
    """
    script_text = Path(script_path).read_text(encoding="utf-8")
    prompt = _build_prompt(script_text, aspect_ratio, music_mood, editorial=editorial)

    logger.info(
        "scene_plan_decomposition_start",
        job_id=job_id,
        aspect_ratio=aspect_ratio,
        max_retries=max_retries,
    )

    last_error: Exception | None = None
    attempts = max_retries + 1
    messages: list[dict[str, str]] = [
        {"role": "system", "content": DEFAULT_SYSTEM + (EDITORIAL_SYSTEM if editorial else "")},
        {"role": "user", "content": prompt},
    ]
    for attempt in range(1, attempts + 1):
        try:
            response = await llm_router.chat(
                task_type="scene_plan",
                messages=messages,
            )
            data: dict[str, Any] = json.loads(_extract_json(response))
            plan = ScenePlan(
                job_id=job_id,
                script=script_text,
                aspect_ratio=data.get("aspect_ratio", aspect_ratio),
                music_mood=data.get("music_mood", music_mood),
                scenes=[Scene(**scene_data) for scene_data in data.get("scenes", [])],
            )
            logger.info(
                "scene_plan_decomposition_complete",
                job_id=job_id,
                scenes=len(plan.scenes),
                duration=sum(s.duration_seconds for s in plan.scenes),
                attempt=attempt,
            )
            await _classify_scenes_with_jev(plan.scenes, client=classify_client)
            return plan
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            last_error = exc
            logger.warning(
                "scene_plan_attempt_failed",
                job_id=job_id,
                attempt=attempt,
                attempts=attempts,
                error=str(exc),
            )

    if fallback_on_error:
        fallback = _build_fallback_scene_plan(job_id, script_text, aspect_ratio, music_mood)
        await _classify_scenes_with_jev(fallback.scenes, client=classify_client)
        return fallback

    raise RuntimeError(
        f"LLM failed to produce a valid scene plan after {attempts} attempts: {last_error}"
    ) from last_error


def write_scene_plan(plan: ScenePlan, output_path: Path) -> Path:
    """Persist a scene plan to disk as JSON."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    logger.info("scene_plan_written", job_id=plan.job_id, path=str(output_path))
    return output_path
