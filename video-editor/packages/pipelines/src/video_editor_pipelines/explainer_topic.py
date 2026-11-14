"""Draft a ``script.md`` from a one-line topic.

This is the only place the explainer pipeline calls a language model. It runs
*before* the estimate gate, writes ``build/script.generated.md``, and stops:
the build that follows has no LLM in it, so the price quoted at the gate is the
price that ships.

The planner is asked for the explainer's own grammar (``still``,
``typographic``, ``map``, ``composite``), so a topic can draft the editorial
plates directly instead of every scene becoming a paid still. A drafted plan
is rendered by the real plate renderers — in memory, before the gate — so a
map with an unknown country or a composite with a missing prop fails here,
while a failed draft has cost nothing.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import structlog
import yaml
from video_editor_core.adapters.from_scene_plan import from_scene_plan
from video_editor_core.composite_scenes import (
    composite_scene_cues,
    silence_unspoken_composite_cues,
)
from video_editor_core.cues import find_cue
from video_editor_core.models.scene_plan import Scene, ScenePlan
from video_editor_core.typographic_scenes import (
    scene_cues,
    silence_unspoken_typographic_cues,
)
from video_editor_maps.scenes import map_scene_cues, silence_unspoken_map_cues

from video_editor_pipelines.explainer_plan import build_explainer_plan
from video_editor_pipelines.explainer_script import (
    COMPOSITE_IMAGES,
    ScriptError,
    is_designed_still,
    parse_explainer_script,
)
from video_editor_pipelines.scene_planner import build_scene_plan

#: Where the drafted script lands, relative to the job's work directory.
GENERATED_SCRIPT_PATH = Path("build") / "script.generated.md"

#: Where the topic handed to the planner is staged, relative to the work directory.
TOPIC_STAGE_PATH = Path("build") / "topic.md"

#: Cues the draft switched off because the narration never says them, relative
#: to the job's work directory. QA reads this to advise without failing.
SILENCED_CUES_PATH = Path("build") / "silenced_cues.json"

#: Planner attempts. A draft that fails the free in-memory render is re-drafted
#: rather than handed to the caller: the layout schema is easy to miss, and the
#: retry costs nothing that the cost gate prices.
DRAFT_ATTEMPTS = 3

logger = structlog.get_logger(__name__)

#: A bare scalar YAML reads without changing it. Anything else (``:``, ``#``,
#: quotes, leading indicators) is quoted so the round-trip is lossless.
_SAFE_SCALAR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.,'/()-]*$")

#: Planner/pipeline bookkeeping that is not authored plate grammar. A plan
#: scene's props may carry these (the shot-type classifier adds ``shot_type``
#: and ``requires_motion``); copying them into a script would smuggle a
#: routing hint into a scene the renderer draws as HTML.
_PIPELINE_PROPS = frozenset(
    {
        "layout",
        "title",
        "word_times",
        "image_b_prompt",
        "shot_type",
        "shot_type_confidence",
        "requires_motion",
        "needs_motion",
        "motion",
        "image_scene",
        "reuse_image_from",
    }
)


class TopicDraftError(RuntimeError):
    """A topic could not be turned into a script that parses back."""


def _scalar(value: str) -> str:
    """Render ``value`` for front matter, quoting only when YAML would misread it."""
    if _SAFE_SCALAR_RE.match(value):
        return value
    return json.dumps(value, ensure_ascii=False)


def _title_for(scene: Scene, index: int) -> str:
    """A scene heading: the planner's own title, or the opening of its speech."""
    title = str(scene.props.get("title") or "").strip()
    if title:
        return title
    words = scene.narration.split()
    return " ".join(words[:7]).rstrip(".,;:!?") or f"Scene {index}"


def _block(text: str) -> str:
    """Indent ``text`` as a YAML block scalar body (two spaces, no tabs)."""
    return "\n".join(f"  {line}" for line in text.strip().splitlines())


def _filled(value: Any) -> bool:
    """False for a value that would only add noise to the script."""
    if value is None or value == "":
        return False
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


#: A planner sometimes labels a cue ("narration: strait") instead of quoting
#: the words. The label is formatting, not something the voice says.
_CUE_LABEL_RE = re.compile(r"^(?:narration|scene|spoken|voice)\s*:\s*", re.IGNORECASE)


def _narration_words(narration: str) -> list[dict[str, Any]]:
    """Token dicts with dummy times, so ``find_cue`` can match against text.

    The draft is validated before narration exists; a phrase the script does
    not contain will never be spoken, so the same matcher used by QA is the
    right authority for whether a cue can resolve.
    """
    return [{"word": token, "start": 0.0, "end": 0.0} for token in narration.split()]


def _drop_unspoken_cues(value: Any, words: list[dict[str, Any]]) -> Any:
    """Remove a ``cue`` that names words its own scene never says.

    The callout/fact itself stays; the plate falls back to its bounded
    stagger. Keeping an impossible cue would fail the composition gate after
    the narration had already been paid for, so it is removed at draft time.
    """
    if isinstance(value, dict):
        for key in list(value):
            item = value[key]
            if (key == "cue" or key.endswith("_cue")) and isinstance(item, str):
                phrase = _CUE_LABEL_RE.sub("", item).strip()
                if phrase and find_cue(words, phrase) is not None:
                    value[key] = phrase
                else:
                    value.pop(key)
            else:
                _drop_unspoken_cues(item, words)
    elif isinstance(value, list):
        for item in value:
            _drop_unspoken_cues(item, words)
    return value


def _yaml_lines(props: Mapping[str, Any]) -> list[str]:
    """Render authored plate props as a YAML mapping body.

    Only declared plate props are kept; planner bookkeeping is dropped. YAML
    handles nested ``stat``/``callouts``/``facts`` values and quotes strings
    that would otherwise be misread (a ``:``, a leading digit pair, ``9:16``).
    """
    carried = {
        key: value for key, value in props.items() if key not in _PIPELINE_PROPS and _filled(value)
    }
    if not carried:
        return []
    dumped = yaml.safe_dump(carried, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return dumped.rstrip("\n").splitlines()


def _cue_map_for(scene: Scene, props: Mapping[str, Any]) -> dict[str, str]:
    """The renderer's cue map for ``props``, or ``{}`` when it cannot be read."""
    document = {"id": scene.id, "type": scene.type, "props": dict(props)}
    try:
        if scene.type == "map":
            return map_scene_cues(document)
        if scene.type == "typographic":
            return scene_cues(document)
        if scene.type == "composite" or is_designed_still(props):
            return composite_scene_cues(document)
    except (TypeError, ValueError):
        return {}
    return {}


def _record_silenced(
    scene: Scene,
    before: Mapping[str, str],
    after: Mapping[str, str],
    silenced: list[dict[str, str]] | None,
) -> None:
    """Log and collect every cue the silence pass switched off."""
    for item, phrase in before.items():
        if after.get(item) == phrase:
            continue
        logger.warning("cue_silenced", scene_id=scene.id, item=item, phrase=phrase)
        if silenced is not None:
            silenced.append({"scene": scene.id, "item": item, "phrase": phrase})


def _scene_lines(
    scene: Scene, title: str, silenced: list[dict[str, str]] | None = None
) -> list[str]:
    """The scene kind and authored props, before ``narration``."""
    props = copy.deepcopy(dict(scene.props))
    words = _narration_words(scene.narration)
    _drop_unspoken_cues(props, words)
    before = _cue_map_for(scene, props)
    # Display text the narration never speaks (a title, a chart label) must not
    # keep a default cue: the reveal falls back to the plate's stagger instead
    # of failing the cue gate after narration has been paid for.
    if scene.type == "composite":
        props = silence_unspoken_composite_cues(props, words)
    elif scene.type == "typographic":
        props = silence_unspoken_typographic_cues(props, words)
    elif scene.type == "map":
        props = silence_unspoken_map_cues(props, words)
    elif is_designed_still(props):
        # A titled still renders as the over_media composite, whose headline
        # defaults to a cue on its own words; silence it when the narration
        # never says them, exactly as a composite scene does.
        props = silence_unspoken_composite_cues(props, words)
    _record_silenced(scene, before, _cue_map_for(scene, props), silenced)

    if scene.type == "typographic":
        layout = str(props.pop("layout", "") or "").strip()
        return ["visual: typographic", f"layout: {_scalar(layout)}", *_yaml_lines(props)]

    if scene.type == "map":
        # A map is drawn from bundled geometry: never let a planner's
        # visual_description turn it into a paid still.
        country = str(props.pop("country", "") or "").strip()
        return ["visual: map", *_yaml_lines({"country": country, **props})]

    if scene.type == "composite":
        layout = str(props.pop("layout", "") or "").strip()
        image = scene.visual_description.strip()
        image_b = str(props.pop("image_b_prompt", "") or "").strip()
        lines = ["visual: composite", f"layout: {_scalar(layout)}"]
        if image:
            lines += ["image: |", _block(image)]
        if image_b:
            lines += ["image_b: |", _block(image_b)]
        return lines + _yaml_lines(props)

    image = scene.visual_description.strip() or scene.narration.strip() or title
    # Display copy (eyebrow/headline/body/subtitle) is what makes the plan
    # build this still as an over_media plate; drop it here and the title
    # block the planner asked for is silently lost.
    return ["visual: still", "image: |", _block(image), *_yaml_lines(props)]


def scene_plan_to_script(
    plan: ScenePlan,
    *,
    design: str = "cream_editorial",
    music_mood: str = "",
    silenced: list[dict[str, str]] | None = None,
) -> str:
    """Render a ``ScenePlan`` as an editable explainer script.

    ``silenced``, when given, collects every cue the draft had to switch off:
    ``{"scene": id, "item": cue key, "phrase": the words it wanted}``.
    """
    lines = [
        "---",
        f"title: {json.dumps(plan.script, ensure_ascii=False)}",
        f"aspect_ratio: {plan.aspect_ratio}",
        f"design: {_scalar(design)}",
    ]
    if music_mood:
        lines.append(f"music_mood: {_scalar(music_mood)}")
    lines += ["---", ""]
    for index, scene in enumerate(plan.scenes, start=1):
        title = _title_for(scene, index)
        lines.append(f"## {index}. {title}")
        lines += _scene_lines(scene, title, silenced)
        narration = scene.narration.strip() or title
        lines += ["narration: |", _block(narration), ""]
    return "\n".join(lines).rstrip() + "\n"


def _validate_draft(text: str, source: str) -> None:
    """Parse and render the draft before it can reach the estimate gate.

    The renderers are pure, so this exercises the same map lookup, composite
    geometry and typographic layout the build will use, without buying
    anything. Composite scenes get placeholder asset entries because the real
    stills are only requested after approval; the renderer only needs to know
    how many clips a layout places.
    """
    try:
        script = parse_explainer_script(text, source=source)
    except ScriptError as exc:
        raise TopicDraftError(f"the draft is not a valid explainer script: {exc}") from exc

    # Imported here so ``composition_qa`` can import ``SILENCED_CUES_PATH``
    # from this module without a cycle at import time.
    from video_editor_pipelines.composition_qa import cue_problems

    try:
        plan_doc, _ = build_explainer_plan(script, "topic_draft")
        for scene in plan_doc["scenes"]:
            if scene.get("type") != "composite":
                continue
            layout = str((scene.get("props") or {}).get("layout") or "")
            needed = COMPOSITE_IMAGES.get(layout, 0)
            if needed:
                # A media layer must name a source the composition can carry;
                # nothing reads the file during validation.
                scene["assets"] = [
                    {"asset_type": "image", "local_path": "validation-placeholder.png"}
                    for _ in range(needed)
                ]
        from_scene_plan(plan_doc)

        # Cue the plates against the words the draft itself speaks. A phrase
        # the narration never says fails the composed build after the voice is
        # bought; with the draft's own text as the word list the same matcher
        # rejects it here, before the estimate is priced.
        for scene in plan_doc["scenes"]:
            props = scene.setdefault("props", {})
            props["word_times"] = _narration_words(str(scene.get("narration") or ""))
        problems = cue_problems(plan_doc)
        if problems:
            raise TopicDraftError(
                "the draft cues words its scenes never say: " + ", ".join(problems)
            )
    except TopicDraftError:
        raise
    except Exception as exc:  # noqa: BLE001 - any unusable plate is a draft failure
        raise TopicDraftError(f"the draft cannot be rendered before the cost gate: {exc}") from exc


async def draft_script_from_topic(
    topic: str,
    router: Any,
    work_dir: Path,
    *,
    job_id: str = "topic_draft",
    aspect_ratio: str = "9:16",
    design: str = "cream_editorial",
    music_mood: str = "",
) -> str:
    """Ask the planner to decompose ``topic`` and return it as script text.

    The rendered draft must parse back and render in memory as an explainer
    script before it is written, so a malformed plan fails here — before the
    estimate gate prices anything. A rejected draft is re-drafted up to
    :data:`DRAFT_ATTEMPTS` times, because a model occasionally gets a layout's
    required props wrong and one more free attempt is cheaper than failing.
    Every persistent planner failure (network, invalid JSON, an unusable plan)
    surfaces as :class:`TopicDraftError`; nothing is silently degraded into an
    empty script.
    """
    topic = topic.strip()
    if not topic:
        raise TopicDraftError("a non-empty topic is required to draft a script")

    build_dir = Path(work_dir) / GENERATED_SCRIPT_PATH.parent
    build_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(work_dir) / TOPIC_STAGE_PATH
    staging.write_text(f"# {topic}\n\nExplain {topic} in about fifty seconds.\n", encoding="utf-8")

    generated = Path(work_dir) / GENERATED_SCRIPT_PATH
    last_error: TopicDraftError | None = None
    for attempt in range(1, DRAFT_ATTEMPTS + 1):
        try:
            plan = await build_scene_plan(
                job_id,
                staging,
                router,
                aspect_ratio=aspect_ratio,
                music_mood=music_mood,
                fallback_on_error=False,
                editorial=True,
            )
        except Exception as exc:  # noqa: BLE001 - every planner failure is a draft failure
            raise TopicDraftError(f"could not draft a script for {topic!r}: {exc}") from exc

        silent = [scene.id for scene in plan.scenes if not scene.narration.strip()]
        if silent:
            last_error = TopicDraftError(
                f"the plan has scenes with no narration ({', '.join(silent)})"
            )
        else:
            # The planner echoes its input file as ``script``; the topic is the
            # title. The caller's aspect ratio and mood win over whatever the
            # model returns, so the draft feeds the existing gate unchanged.
            plan = plan.model_copy(
                update={"script": topic, "aspect_ratio": aspect_ratio, "music_mood": music_mood}
            )
            silenced: list[dict[str, str]] = []
            text = scene_plan_to_script(
                plan, design=design, music_mood=music_mood, silenced=silenced
            )
            try:
                _validate_draft(text, str(generated))
            except TopicDraftError as exc:
                last_error = exc
            else:
                generated.write_text(text, encoding="utf-8")
                silenced_path = Path(work_dir) / SILENCED_CUES_PATH
                if silenced:
                    silenced_path.write_text(
                        json.dumps(silenced, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                elif silenced_path.exists():
                    silenced_path.unlink()
                return text

        logger.warning(
            "topic_draft_rejected",
            job_id=job_id,
            attempt=attempt,
            attempts=DRAFT_ATTEMPTS,
            error=str(last_error),
        )

    raise TopicDraftError(f"could not draft a script for {topic!r}: {last_error}") from last_error
