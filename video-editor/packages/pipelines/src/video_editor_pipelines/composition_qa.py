"""The composition gates: glyph, cue, safe area, composition and visual.

These run on every explainer build, alongside the measured-file QA in
``explainer_qa``. Each gate is fail-closed: a problem that could not be
measured is a failing check, never a silent pass.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from video_editor_core.adapters.from_scene_plan import from_scene_plan
from video_editor_core.composite_scenes import (
    CompositeSceneError,
    composite_scene_cues,
)
from video_editor_core.composition import Composition, quantise
from video_editor_core.cues import find_cue
from video_editor_core.glyphs import load_glyph_manifest, undeclared_glyphs
from video_editor_core.hyperframes_cli import (
    HyperFramesCheckError,
    check_project,
    snapshot_project,
)
from video_editor_core.look_bans import look_ban_findings
from video_editor_core.safe_area import composition_safe_area_violations
from video_editor_core.typographic_scenes import SceneRenderError, scene_cues
from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, run_ffmpeg
from video_editor_maps.scenes import (
    MapSceneError,
    map_scene_cues,
    map_scene_has_camera_moves,
    map_scene_marker_problems,
    map_scene_motion_problems,
    map_scene_motion_warnings,
)

from video_editor_pipelines.explainer_qa import QaCheck
from video_editor_pipelines.explainer_topic import SILENCED_CUES_PATH

logger = structlog.get_logger(__name__)

#: A frame whose luma standard deviation is below this is effectively blank.
MIN_FRAME_STDDEV = 6.0
#: Two frames this far apart (mean absolute luma difference) show motion. A
#: frozen parallax renders identical frames and fails the layers gate.
MIN_FRAME_DIFF = 0.5
#: How far apart the two parallax samples sit inside a scene.
PARALLAX_SAMPLE_SECONDS = 0.4


@dataclass(frozen=True)
class CompositionQaInputs:
    undeclared: tuple[tuple[str, str], ...] = ()
    missing_cues: tuple[str, ...] = ()
    #: False when the caller supplied neither a plan nor a resolved-cue count.
    #: An unmeasured cue gate must fail, never pass on an empty tuple.
    cues_measured: bool = True
    safe_area: tuple[str, ...] = ()
    composition_report: str = ""
    frame_stddevs: tuple[float, ...] = ()
    #: Why the frames could not be captured, when the visual stage failed.
    visual_reason: str = ""
    #: Mean absolute luma difference per sampled pair; empty when unmeasured.
    motion_diffs: tuple[float, ...] = ()
    #: True when the composition has at least one parallax layer.
    motion_expected: bool = False
    motion_reason: str = ""
    #: Light leaks (and future shader transitions); the registry advises two.
    flourish_count: int = 0
    ran: bool = True


def evaluate_composition_qa(inputs: CompositionQaInputs) -> list[QaCheck]:
    if not inputs.ran:
        return [QaCheck("composition_gates", False, "a composition gate could not run")]
    blank = [value for value in inputs.frame_stddevs if value < MIN_FRAME_STDDEV]
    return [
        QaCheck(
            name="glyphs",
            ok=not inputs.undeclared,
            detail=(
                "every CJK character is declared"
                if not inputs.undeclared
                else "; ".join(f"{char} in {text[:20]}" for char, text in inputs.undeclared)
            ),
        ),
        QaCheck(
            name="cues",
            ok=inputs.cues_measured and not inputs.missing_cues,
            detail=(
                "cues were not measured"
                if not inputs.cues_measured
                else "every cue matched a spoken word"
                if not inputs.missing_cues
                else "no match for " + ", ".join(inputs.missing_cues)
            ),
        ),
        QaCheck(
            name="safe_area",
            ok=not inputs.safe_area,
            detail="text inside the profile"
            if not inputs.safe_area
            else "; ".join(inputs.safe_area),
        ),
        QaCheck(
            name="composition",
            ok=not inputs.composition_report.startswith("FAILED"),
            detail=(inputs.composition_report or "check passed")[:200],
        ),
        QaCheck(
            name="visual",
            ok=bool(inputs.frame_stddevs) and not blank,
            detail=(
                f"{len(inputs.frame_stddevs)} frames, min luma stddev "
                f"{min(inputs.frame_stddevs):.1f}"
                if inputs.frame_stddevs
                else inputs.visual_reason or "no frames captured"
            ),
        ),
        _motion_check(inputs),
        QaCheck(
            "flourishes",
            True,
            (
                f"{inputs.flourish_count} flourish layer(s); the registry advises at most two"
                if inputs.flourish_count
                else "no flourishes"
            ),
        ),
    ]


def _motion_check(inputs: CompositionQaInputs) -> QaCheck:
    """Fail a layerized composition whose parallax frames do not move."""
    if not inputs.motion_expected:
        return QaCheck("layers", True, "no parallax scenes")
    if not inputs.motion_diffs:
        return QaCheck(
            "layers",
            False,
            inputs.motion_reason or "the parallax frames could not be sampled",
        )
    frozen = [value for value in inputs.motion_diffs if value < MIN_FRAME_DIFF]
    return QaCheck(
        "layers",
        ok=not frozen,
        detail=(
            f"{len(inputs.motion_diffs)} parallax pairs, min luma diff "
            f"{min(inputs.motion_diffs):.2f}"
            if not frozen
            else f"{len(frozen)} parallax pair(s) are frozen (min luma diff "
            f"{min(frozen):.2f} < {MIN_FRAME_DIFF})"
        ),
    )


def layer_texts(composition: Composition) -> list[str]:
    """Every string a layer draws: its own text plus any rich-text spans.

    ``html`` layers carry their text in ``content.text``; ``rich_text`` layers
    carry it only in ``content.spans``, which the glyph gate must see too.
    """
    texts: list[str] = []
    for scene in composition.scenes:
        for layer in scene.layers:
            if layer.content.text:
                texts.append(layer.content.text)
            texts.extend(span.text for span in layer.content.spans if span.text)
    return texts


def cue_problems(plan: dict[str, Any]) -> list[str]:
    """Cues that name a phrase their own scene never speaks.

    Every cue the renderer resolves is checked: a scene's declared ``cue`` and,
    for a scene that names a layout, the layout's own cue map (big-stat cue,
    bar labels, definition term and sequence steps). A layout whose cues cannot
    be built fails closed rather than being skipped.
    """
    problems: list[str] = []
    for scene in plan.get("scenes", []) or []:
        props = scene.get("props") or {}
        words = props.get("word_times") or []
        scene_id = scene.get("id")
        reported: set[str] = set()

        cue = props.get("cue")
        if cue and str(cue) not in reported:
            if find_cue(words, str(cue), after=0.0) is None:
                problems.append(f"{scene_id}: {cue!r}")
            reported.add(str(cue))

        if scene.get("type") == "map":
            try:
                cues = map_scene_cues(scene)
            except (MapSceneError, TypeError, ValueError) as exc:
                problems.append(f"{scene_id}: map cues unreadable: {exc}")
                continue
        elif scene.get("type") == "composite":
            try:
                cues = composite_scene_cues(scene)
            except (CompositeSceneError, TypeError, ValueError) as exc:
                problems.append(f"{scene_id}: composite cues unreadable: {exc}")
                continue
        else:
            layout = props.get("layout")
            if not layout or layout == "ledger":
                # A ledger reveals its rows on a timed stagger, not on cue words.
                continue
            try:
                cues = scene_cues(scene)
            except (SceneRenderError, TypeError, ValueError) as exc:
                # A cue map that cannot be built is an unmeasured cue, so it is a
                # problem, never a silent pass.
                problems.append(f"{scene_id}: layout {layout!r} cues unreadable: {exc}")
                continue
        for phrase in cues.values():
            if phrase in reported:
                continue
            reported.add(phrase)
            if find_cue(words, phrase, after=0.0) is None:
                problems.append(f"{scene_id}: {phrase!r}")
    return problems


def _gray_bytes(path: Path) -> bytes:
    """Decode one PNG to raw gray pixels."""
    result = run_ffmpeg(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vf",
            "format=gray",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        check=False,
        text=False,
        timeout=RENDER_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise ValueError(f"ffmpeg failed to decode {path} (exit {result.returncode})")
    samples = result.stdout or b""
    if not samples:
        raise ValueError(f"no luma samples for {path}")
    return samples


def frame_stddev(path: Path) -> float:
    """Luma standard deviation of one PNG, measured on its raw gray pixels.

    The FFmpeg builds this repo runs expose no standard-deviation key from
    ``signalstats``, so the frame is decoded to raw gray and the deviation is
    computed here. Both sums are exact integers, so only the final division
    rounds.
    """
    samples = _gray_bytes(path)
    count = len(samples)
    total = sum(samples)
    total_squared = sum(value * value for value in samples)
    mean = total / count
    variance = max(total_squared / count - mean * mean, 0.0)
    return math.sqrt(variance)


def frame_difference(first: Path, second: Path) -> float:
    """Mean absolute luma difference between two equally sized PNGs."""
    a = _gray_bytes(first)
    b = _gray_bytes(second)
    if len(a) != len(b):
        raise ValueError(f"frame sizes differ: {len(a)} vs {len(b)} pixels")
    return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a)


def _relative_words(word_times: Any, start: float) -> list[dict[str, Any]]:
    """Plan word times rebased onto the scene's own clock, as the compiler does."""
    words: list[dict[str, Any]] = []
    for word in word_times or []:
        if not isinstance(word, dict):
            continue
        try:
            word_start = float(word["start"])
            word_end = float(word["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(word_start) and math.isfinite(word_end)):
            continue
        words.append({**word, "start": word_start - start, "end": word_end - start})
    return words


def _map_scene_entries(
    plan: dict[str, Any], *, fps: int
) -> list[tuple[dict[str, Any], float, list[dict[str, Any]]]]:
    """Every map scene with its duration and rebased word times.

    Plan word times are absolute narration-track times. The compiler rebases
    them onto the frame-quantised scene start, so QA uses the same ``quantise``
    here; a raw float cursor can differ by a frame and change which zoom plays.
    """
    entries: list[tuple[dict[str, Any], float, list[dict[str, Any]]]] = []
    cursor = 0.0
    for scene in plan.get("scenes", []) or []:
        if not isinstance(scene, dict):
            continue
        try:
            duration = float(scene.get("duration_seconds") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        duration = max(0.0, duration)
        if scene.get("type") == "map":
            props = scene.get("props") or {}
            if not isinstance(props, dict):
                props = {}
            start = quantise(cursor, fps)
            entries.append((scene, duration, _relative_words(props.get("word_times"), start)))
        cursor += duration
    return entries


def _item_index(item: str, prefix: str) -> int | None:
    """The index in ``prefix_N``, or None when the key is not that item."""
    if not item.startswith(prefix):
        return None
    try:
        index = int(item[len(prefix) :])
    except ValueError:
        return None
    return index if index >= 0 else None


def _false_cue_for_phrase(value: Any, phrase: str) -> bool:
    """True when ``value`` still holds a false cue for ``phrase``.

    Composite and typographic items are not addressable by index without
    rebuilding their layout, so match the display text the silenced cue was
    derived from instead.
    """
    if isinstance(value, dict):
        if value.get("cue") is False:
            for key in ("text", "label", "value"):
                text = value.get(key)
                if isinstance(text, str) and text.strip() == phrase:
                    return True
        for key, item in value.items():
            if key.endswith("_cue") and item is False:
                text = value.get(key[:-4])
                if isinstance(text, str) and text.strip() == phrase:
                    return True
            if _false_cue_for_phrase(item, phrase):
                return True
        return False
    if isinstance(value, list):
        return any(_false_cue_for_phrase(item, phrase) for item in value)
    return False


def _silenced_cue_is_current(scene: dict[str, Any], item: str, phrase: str) -> bool:
    """True when the plan still has this item with its cue switched off."""
    props = scene.get("props") or {}
    if not isinstance(props, dict):
        return False
    if scene.get("type") == "map":
        index = _item_index(item, "item_")
        if index is not None:
            entries = props.get("callouts")
            return (
                isinstance(entries, list)
                and index < len(entries)
                and isinstance(entries[index], dict)
                and entries[index].get("cue") is False
            )
        index = _item_index(item, "marker_")
        if index is not None:
            entries = props.get("markers")
            return (
                isinstance(entries, list)
                and index < len(entries)
                and isinstance(entries[index], dict)
                and entries[index].get("cue") is False
            )
        if item == "route":
            return props.get("route_cue") is False
        index = _item_index(item, "zoom_")
        if index is not None:
            entries = props.get("zooms")
            return (
                isinstance(entries, list)
                and index < len(entries)
                and isinstance(entries[index], dict)
                and entries[index].get("cue") is False
            )
        return False
    return _false_cue_for_phrase(props, phrase)


def _map_markers_check(plan: dict[str, Any] | None) -> QaCheck:
    """Map markers must sit on land unless marked ``sea: true`` (#342)."""
    try:
        problems = [
            problem
            for scene, _duration, _words in (
                _map_scene_entries(plan, fps=30) if plan is not None else []
            )
            for problem in map_scene_marker_problems(scene)
        ]
    except Exception as exc:  # noqa: BLE001 - a gate that cannot run must fail
        logger.warning("composition_gate_map_markers_failed", error=str(exc))
        return QaCheck("map_markers", False, f"map markers could not be checked: {exc}")
    if problems:
        return QaCheck("map_markers", False, "; ".join(problems))
    return QaCheck("map_markers", True, "every map marker is on land")


def _map_motion_check(plan: dict[str, Any] | None, composition: Composition) -> QaCheck:
    """The pure-geometry mid-motion gate for map scenes (#334)."""
    try:
        return _measure_map_motion(plan, composition)
    except Exception as exc:  # noqa: BLE001 - a gate that cannot run must fail
        logger.warning("composition_gate_map_motion_failed", error=str(exc))
        return QaCheck("map_motion", False, f"map motion could not be measured: {exc}")


def _measure_map_motion(plan: dict[str, Any] | None, composition: Composition) -> QaCheck:
    if plan is None or composition.design is None:
        return QaCheck("map_motion", True, "no camera moves")
    width = composition.canvas.width
    height = composition.canvas.height
    moved = False
    problems: list[str] = []
    for scene, duration, words in _map_scene_entries(plan, fps=composition.canvas.fps):
        if not map_scene_has_camera_moves(
            scene,
            composition.design,
            width=width,
            height=height,
            duration=duration,
            words=words,
        ):
            continue
        moved = True
        problems.extend(
            map_scene_motion_problems(
                scene,
                composition.design,
                width=width,
                height=height,
                duration=duration,
                words=words,
            )
        )
    if problems:
        return QaCheck("map_motion", False, "; ".join(problems))
    return QaCheck("map_motion", True, "map motion is clear" if moved else "no camera moves")


def _advisories_check(
    plan: dict[str, Any] | None,
    composition_dir: Path,
    composition: Composition,
) -> QaCheck:
    """Always-passing report of dropped zooms and silenced cues (#332)."""
    try:
        return _measure_advisories(plan, composition_dir, composition)
    except Exception as exc:  # noqa: BLE001 - a gate that cannot run must fail
        logger.warning("composition_gate_advisories_failed", error=str(exc))
        return QaCheck("advisories", False, f"advisories could not be read: {exc}")


_TITLE_ARTICLES = ("the ", "a ", "an ")


def _concept_key(title: str) -> str:
    """A title reduced to its concept: lowercase, no article, no plural s."""
    key = re.sub(r"[^a-z0-9 ]+", "", title.lower()).strip()
    for article in _TITLE_ARTICLES:
        if key.startswith(article):
            key = key[len(article) :]
    if key.endswith("s") and not key.endswith("ss") and len(key) > 3:
        key = key[:-1]
    return key


def repeated_concepts(scenes: Sequence[Any]) -> list[str]:
    """Scenes that feature the same concept as their title, not side by side (#348).

    A title is a scene's ``headline`` or defined ``term``. Adjacent scenes may
    share one (a setup and its payoff); two distant scenes introducing the same
    concept usually means the script explains it twice.
    """
    first_seen: dict[str, tuple[int, str]] = {}
    repeats: list[str] = []
    for position, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        props = scene.get("props") or {}
        if not isinstance(props, dict):
            continue
        for field_name in ("headline", "term"):
            title = props.get(field_name)
            if not isinstance(title, str) or not title.strip():
                continue
            key = _concept_key(title)
            if not key or not re.search(r"[a-z]", key):
                continue
            seen = first_seen.get(key)
            if seen is None:
                first_seen[key] = (position, str(scene.get("id") or position))
            elif position - seen[0] > 1:
                repeats.append(f"{seen[1]} and {scene.get('id') or position} both feature {key!r}")
    return repeats


def _measure_advisories(
    plan: dict[str, Any] | None,
    composition_dir: Path,
    composition: Composition,
) -> QaCheck:
    dropped: list[str] = []
    if plan is not None and composition.design is not None:
        for scene, duration, words in _map_scene_entries(plan, fps=composition.canvas.fps):
            dropped.extend(
                map_scene_motion_warnings(
                    scene,
                    composition.design,
                    width=composition.canvas.width,
                    height=composition.canvas.height,
                    duration=duration,
                    words=words,
                )
            )
    silenced: list[str] = []
    scenes = {
        scene.get("id"): scene
        for scene in (plan or {}).get("scenes", []) or []
        if isinstance(scene, dict)
    }
    silenced_path = Path(composition_dir).parent / SILENCED_CUES_PATH
    if silenced_path.exists():
        try:
            raw = json.loads(silenced_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            silenced.append(f"silenced cues unreadable: {exc}")
        else:
            if isinstance(raw, list):
                for entry in raw:
                    if not isinstance(entry, dict):
                        continue
                    plan_scene = scenes.get(entry.get("scene"))
                    item = entry.get("item")
                    phrase = entry.get("phrase")
                    if (
                        plan_scene is None
                        or not isinstance(item, str)
                        or not isinstance(phrase, str)
                    ):
                        continue
                    if not _silenced_cue_is_current(plan_scene, item, phrase):
                        continue
                    silenced.append(f"{entry.get('scene')}: {item} silenced {phrase!r}")
            else:
                silenced.append("silenced cues unreadable: expected a list")
    parts: list[str] = []
    repeats = repeated_concepts((plan or {}).get("scenes", []) or [])
    if repeats:
        parts.append("repeated concepts: " + "; ".join(repeats))
    if dropped:
        parts.append("dropped zooms: " + "; ".join(dropped))
    if silenced:
        parts.append("silenced cues: " + "; ".join(silenced))
    return QaCheck("advisories", True, " | ".join(parts) if parts else "none")


def gather_composition_qa(
    *,
    composition_dir: Path,
    plan: dict[str, Any] | None = None,
    composition: Composition | None = None,
    declared_cues: int | None = None,
    resolved_cues: int | None = None,
    cli_path: str = "hyperframes",
    glyph_manifest: str = "noto_serif_kr",
    frame_dir: Path | None = None,
    strict_look: bool = False,
    expect_depth: bool = False,
    end_screen: bool = False,
) -> list[QaCheck]:
    """Measure the five gates for a finished composition project.

    A gate whose measurement fails becomes a failing check carrying the
    reason; the build never passes by accident. A pack build passes the
    composition it compiled; a scene-plan build passes the plan and the gate
    derives the composition from it.
    """
    if plan is not None and composition is not None:
        # One artifact's cues must not be gated against another artifact's
        # glyphs, safe area and frames.
        raise ValueError("pass plan or composition, not both")
    if composition is not None:
        resolved = composition
    elif plan is not None:
        resolved = from_scene_plan(plan)
    else:
        return [
            QaCheck(
                "composition_gates",
                False,
                "no composition and no scene plan; nothing to measure",
            )
        ]
    if plan is not None:
        missing_cues = tuple(cue_problems(plan))
        cues_measured = True
    elif declared_cues is not None and resolved_cues is not None:
        # The adapter resolved cues with ``strict=True``, so this is defence in
        # depth: the count proves the gate measured rather than assumed.
        missing_cues = (
            ()
            if resolved_cues >= declared_cues
            else (f"{declared_cues - resolved_cues} declared cues did not resolve",)
        )
        cues_measured = True
    else:
        missing_cues = ()
        cues_measured = False
    try:
        manifest = load_glyph_manifest(glyph_manifest)
        undeclared = tuple(undeclared_glyphs(layer_texts(resolved), manifest))
    except Exception as exc:  # noqa: BLE001 - a gate that cannot run must fail
        logger.warning("composition_gate_glyphs_failed", error=str(exc))
        return [QaCheck("glyphs", False, f"manifest unavailable: {exc}")]

    # The scene-plan path emits one full-canvas ``html`` layer per typographic
    # scene, so it has no per-text rect to measure. The pack path places
    # captions, chips, pills and the end card as real layers, and this gate
    # measures those rects.
    violations = composition_safe_area_violations(resolved)
    safe_area = tuple(f"{item['name']} at {item['rect']}" for item in violations)

    try:
        report = check_project(composition_dir, cli_path=cli_path)
    except HyperFramesCheckError as exc:
        report = "FAILED: " + "; ".join(exc.messages[:5])

    stddevs: tuple[float, ...] = ()
    visual_reason = ""
    motion_diffs: tuple[float, ...] = ()
    motion_reason = ""
    flourish_count = sum(
        1 for scene in resolved.scenes for layer in scene.layers if layer.style.get("flourish")
    )
    motion_expected = any(
        motion.name == "parallax"
        for scene in resolved.scenes
        for layer in scene.layers
        for motion in layer.motions
    )
    frames_dir = frame_dir or (Path(composition_dir) / "qa-frames")
    try:
        shots = snapshot_project(
            composition_dir,
            frames_dir,
            at=[(scene.start + scene.end) / 2 for scene in resolved.scenes],
            cli_path=cli_path,
        )
        stddevs = tuple(frame_stddev(shot) for shot in shots)
    except Exception as exc:  # noqa: BLE001 - a missing frame fails the visual gate
        visual_reason = str(exc)
        logger.warning("composition_gate_visual_failed", error=str(exc))

    if motion_expected:
        parallax = [
            scene
            for scene in resolved.scenes
            if any(motion.name == "parallax" for layer in scene.layers for motion in layer.motions)
        ]
        try:
            first_times = [(scene.start + scene.end) / 2 for scene in parallax]
            second_times = [
                min(
                    (scene.start + scene.end) / 2 + PARALLAX_SAMPLE_SECONDS,
                    scene.end - 0.01,
                )
                for scene in parallax
            ]
            first = snapshot_project(composition_dir, frames_dir, at=first_times, cli_path=cli_path)
            second = snapshot_project(
                composition_dir, frames_dir, at=second_times, cli_path=cli_path
            )
            motion_diffs = tuple(frame_difference(a, b) for a, b in zip(first, second, strict=True))
        except Exception as exc:  # noqa: BLE001 - an unmeasured motion gate fails
            motion_reason = str(exc)
            logger.warning("composition_gate_layers_failed", error=str(exc))

    return [
        *evaluate_composition_qa(
            CompositionQaInputs(
                undeclared=undeclared,
                missing_cues=missing_cues,
                cues_measured=cues_measured,
                safe_area=safe_area,
                composition_report=report,
                frame_stddevs=stddevs,
                visual_reason=visual_reason,
                motion_diffs=motion_diffs,
                motion_expected=motion_expected,
                motion_reason=motion_reason,
                flourish_count=flourish_count,
            )
        ),
        _map_motion_check(plan, resolved),
        _map_markers_check(plan),
        _advisories_check(plan, composition_dir, resolved),
        _look_ban_check(resolved, strict_look=strict_look, expect_depth=expect_depth),
        *([_end_screen_check(resolved)] if end_screen else []),
    ]


def _end_screen_check(composition: Composition, *, seconds: float = 20.0) -> QaCheck:
    """YouTube's end screen owns the right half of the last ``seconds`` (#316)."""
    window_start = max(0.0, composition.duration - seconds)
    half = composition.canvas.width / 2.0
    offenders: list[str] = []
    for scene in composition.scenes:
        for layer in scene.layers:
            if layer.kind not in {"text", "rich_text", "svg", "html"}:
                continue
            end = layer.time.end if layer.time.end is not None else scene.end
            if end <= window_start:
                continue
            if layer.rect.x >= half:
                offenders.append(layer.id)
    detail = (
        "right half is clear"
        if not offenders
        else f"elements overlap the end-screen zone: {', '.join(offenders)}"
    )
    return QaCheck("end_screen", not offenders, detail)


def _look_ban_check(composition: Composition, *, strict_look: bool, expect_depth: bool) -> QaCheck:
    """Advisory monotony report; fails the build only under ``strict_look``."""
    findings = look_ban_findings(composition, expect_depth=expect_depth)
    detail = "; ".join(findings) if findings else "no monotony detected"
    return QaCheck("look_bans", not findings or not strict_look, detail)
