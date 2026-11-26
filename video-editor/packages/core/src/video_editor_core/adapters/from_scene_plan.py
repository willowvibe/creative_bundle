"""Adapt a script-to-video scene-plan dict into a :class:`Composition`.

The plan is the raw dict written to ``plan/scene_plan.json``, duck-typed
against the ``ScenePlan`` schema — the same input the old compiler took.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from video_editor_core.calc_card import (
    MIN_ROW_HOLD_SECONDS,
    CalcRow,
    parse_calc,
    render_calc_card,
    render_ledger,
)
from video_editor_core.composite_scenes import CompositeSceneError, render_composite
from video_editor_core.composition import (
    AudioCue,
    Canvas,
    Composition,
    CompositionLayer,
    CompositionScene,
    LayerContent,
    LayerTime,
    MotionSpec,
    canvas_for_aspect,
    quantise,
)
from video_editor_core.designs import DesignTokens, load_design
from video_editor_core.hook_card import parse_hook, render_hook
from video_editor_core.progress_strip import parse_progress, render_progress
from video_editor_core.render_graph import Rect
from video_editor_core.scene_renderers import scene_renderer
from video_editor_core.sfx_synth import coin_path, hit_path, pop_path, swoosh_path
from video_editor_core.tally_chip import (
    COUNT_DELAY_SECONDS,
    COUNT_SECONDS,
    TALLY_AFTER_RESULT_SECONDS,
    parse_tally,
    render_tally,
)
from video_editor_core.typographic_scenes import SceneRenderError, render_typographic

DEFAULT_FPS = 30

#: The coin clink sits this far under the narration.
COIN_GAIN = 10 ** (-4 / 20)
#: Own mixer track range for tally coins (narration 150, music 200, tts 100+n).
COIN_TRACK_BASE = 400
#: Longest the coin cue plays; the bundled WAVs run about 0.45 s.
COIN_MAX_SECONDS = 0.5

#: Chart swooshes and number hits are UI sounds, quieter than the coins.
SWOOSH_GAIN = 10 ** (-10 / 20)
HIT_GAIN = 10 ** (-8 / 20)
#: Own mixer track range for UI cues, clear of the tally coins.
UI_TRACK_BASE = 500
SWOOSH_MAX_SECONDS = 0.4
HIT_MAX_SECONDS = 0.4
#: Photo-card pops sit a little above the swoosh (about -4 dB under the narration).
POP_GAIN = 10 ** (-4 / 20)
POP_MAX_SECONDS = 0.12
#: One mixer track per pop so close-together cards never share a slot.
POP_TRACK_BASE = 600
POP_TRACKS_PER_SCENE = 20
#: A swish lands just after the cut, not on top of it.
SWOOSH_LEAD_SECONDS = 0.05
#: The fallback for a big stat whose own reveal time cannot be read.
BIG_STAT_FALLBACK_SECONDS = 0.4


def from_scene_plan(plan: dict[str, Any], *, fps: int = DEFAULT_FPS) -> Composition:
    """Build the Composition for a scene plan."""
    width, height = canvas_for_aspect(plan.get("aspect_ratio"))
    raw_scenes = [s for s in plan.get("scenes", []) or [] if isinstance(s, dict)]
    design = _design_for(plan, raw_scenes)

    scenes: list[CompositionScene] = []
    audio: list[AudioCue] = []
    cursor = 0.0
    previous_tally: int | None = None
    previous_typographic = False
    tally_sound = plan.get("tally_sound", True) is not False
    card_sound = tally_sound and plan.get("card_sound", True) is not False
    for index, scene in enumerate(raw_scenes):
        duration = float(scene.get("duration_seconds", 0.0))
        if duration <= 0:
            continue
        start = quantise(cursor, fps)
        end = quantise(cursor + duration, fps)
        layers = _scene_layers(scene, index, start, end, duration, width, height, design, fps)
        layers += _hook_layers(scene, index, LayerTime(start=start, end=end), layers, width, height)
        layers += _tally_layers(
            scene,
            previous_tally,
            index,
            start,
            duration,
            LayerTime(start=start, end=end),
            layers,
            width,
            height,
        )
        layers += _calc_layers(
            scene, index, start, duration, LayerTime(start=start, end=end), layers, width, height
        )
        layers += _progress_layers(
            scene, index, LayerTime(start=start, end=end), layers, width, height
        )
        scenes.append(
            CompositionScene(id=_scene_id(scene, index), start=start, end=end, layers=layers)
        )
        props = scene.get("props") or {}
        declared = parse_tally(props.get("tally"))
        if declared is not None:
            if tally_sound and previous_tally is not None and declared.value != previous_tally:
                cue = _coin_cue(
                    index,
                    start + (_tally_delay(scene, start, duration) or 0.0),
                    end,
                    rising=declared.value > previous_tally,
                )
                if cue is not None:
                    audio.append(cue)
            previous_tally = declared.value
        typographic = _is_typographic(scene)
        if tally_sound and typographic:
            if not previous_typographic:
                cue = _ui_cue(
                    index,
                    "swoosh",
                    start + SWOOSH_LEAD_SECONDS,
                    end,
                    source=swoosh_path(),
                    gain=SWOOSH_GAIN,
                    max_seconds=SWOOSH_MAX_SECONDS,
                )
                if cue is not None:
                    audio.append(cue)
            if props.get("layout") == "big_stat":
                cue = _ui_cue(
                    index,
                    "hit",
                    start + _big_stat_reveal(layers),
                    end,
                    source=hit_path(),
                    gain=HIT_GAIN,
                    max_seconds=HIT_MAX_SECONDS,
                )
                if cue is not None:
                    audio.append(cue)
        previous_typographic = typographic
        if card_sound:
            for position, offset in enumerate(_card_starts(layers)):
                cue = _ui_cue(
                    index,
                    f"card_pop_{position}",
                    start + offset,
                    end,
                    source=pop_path(),
                    gain=POP_GAIN,
                    max_seconds=POP_MAX_SECONDS,
                    track=POP_TRACK_BASE + index * POP_TRACKS_PER_SCENE + position,
                )
                if cue is not None:
                    audio.append(cue)
        tts = _asset_by_type(scene, "tts")
        if tts is not None:
            audio.append(
                AudioCue(
                    id=f"scene_{index}_tts",
                    source=str(tts.get("local_path", "")),
                    start=start,
                    duration=end - start,
                    track=100 + index,
                )
            )
        cursor += duration

    total = quantise(cursor, fps)
    # The narration file can end before the composition does (the final scene
    # keeps a tail after the last word). Give the cue the measured length so
    # its slot matches the media; the music bed still covers the full film.
    try:
        narration_seconds = float(plan.get("narration_duration") or 0.0)
    except (TypeError, ValueError):
        narration_seconds = 0.0
    narration_cue = narration_seconds if 0.0 < narration_seconds < total else total
    for cue_id, key, track in (
        ("narration", "narration_path", 150),
        ("music", "music_path", 200),
    ):
        path = plan.get(key) or ""
        if path and Path(path).exists():
            duration = narration_cue if cue_id == "narration" else total
            audio.append(
                AudioCue(id=cue_id, source=str(path), start=0.0, duration=duration, track=track)
            )

    return Composition(
        job_id=str(plan.get("job_id", "scene_plan")),
        canvas=Canvas(width=width, height=height, fps=fps),
        duration=total,
        scenes=scenes,
        audio=audio,
        design=design,
    )


def _tally_delay(scene: Mapping[str, Any], start: float, duration: float) -> float | None:
    """Clip-relative second the tally's count begins; ``None`` without a tally.

    With a calc card it waits for the last cash row's result to land and read
    (``TALLY_AFTER_RESULT_SECONDS``), keeps the whole count on screen with a
    beat left after it, and never precedes the result.
    """
    props = scene.get("props") or {}
    if parse_tally(props.get("tally")) is None:
        return None
    delay = COUNT_DELAY_SECONDS
    if props.get("calc"):
        rows = parse_calc(props["calc"])
        reveal_at = _calc_reveal_times(scene, start, duration)
        moved = [reveal_at[i][1] for i, row in enumerate(rows) if row.in_tally]
        if moved:
            delay = moved[-1] + TALLY_AFTER_RESULT_SECONDS
            # Keep the whole count on screen with a beat left after it,
            # but never before its result has landed.
            delay = max(moved[-1] + 0.2, min(delay, duration - COUNT_SECONDS - 0.3))
    return delay


def _coin_cue(index: int, at: float, scene_end: float, *, rising: bool) -> AudioCue | None:
    """The coin clink for a tally that counts up (money in) or down (money out)."""
    if at >= scene_end:
        return None
    return AudioCue(
        id=f"scene_{index}_tally_coin",
        source=str(coin_path("in" if rising else "out")),
        start=at,
        duration=min(COIN_MAX_SECONDS, scene_end - at),
        track=COIN_TRACK_BASE + index,
        volume=COIN_GAIN,
    )


def _ui_cue(
    index: int,
    name: str,
    at: float,
    scene_end: float,
    *,
    source: Path,
    gain: float,
    max_seconds: float,
    track: int | None = None,
) -> AudioCue | None:
    """A short UI sound cue (chart swoosh, number hit or card pop) inside its scene."""
    if at >= scene_end:
        return None
    return AudioCue(
        id=f"scene_{index}_{name}",
        source=str(source),
        start=at,
        duration=min(max_seconds, scene_end - at),
        track=UI_TRACK_BASE + index if track is None else track,
        volume=gain,
    )


def _is_typographic(scene: Mapping[str, Any]) -> bool:
    """Plans normally use ``type``; accept a script-style ``visual`` too."""
    return scene.get("type") == "typographic" or scene.get("visual") == "typographic"


_CARD_START = re.compile(r'data-card-start="([\d.]+)"')


def _card_starts(layers: list[CompositionLayer]) -> list[float]:
    """Scene-relative entry seconds of each photo card, read from the map's own markup."""
    starts: list[float] = []
    for layer in layers:
        if layer.preset == "map":
            starts += [float(m) for m in _CARD_START.findall(layer.content.text or "")]
    return starts


def _big_stat_reveal(layers: list[CompositionLayer]) -> float:
    """The big stat's headline reveal time read from the renderer's own markup."""
    for layer in layers:
        if layer.preset != "typographic":
            continue
        markup = layer.content.text or ""
        for pattern in (_COUNT_DELAY, _HEADLINE_DELAY):
            match = pattern.search(markup)
            if match is not None:
                return float(match.group(1))
    return BIG_STAT_FALLBACK_SECONDS


def _hook_layers(
    scene: Mapping[str, Any],
    index: int,
    window: LayerTime,
    below: list[CompositionLayer],
    width: int,
    height: int,
) -> list[CompositionLayer]:
    """The hook card over the scene's first seconds: above footage, below the chip and card."""
    hook = parse_hook((scene.get("props") or {}).get("hook"))
    if hook is None:
        return []
    card_w, card_h = round(width * 0.7), round(height * 0.2)
    markup, css = render_hook(hook, index=index, width=width)
    return [
        CompositionLayer(
            id=f"scene_{index}_hook",
            kind="html",
            preset="hook",
            rect=Rect(
                x=round((width - card_w) / 2),
                # Vertical centre at 38% of the height.
                y=round(height * 0.38 - card_h / 2),
                width=card_w,
                height=card_h,
            ),
            time=window,
            content=LayerContent(text=markup),
            style={"extra_css": css},
            track=index * 10 + 7,
            z=max((layer.z for layer in below), default=0) + 1,
        )
    ]


def _progress_layers(
    scene: Mapping[str, Any],
    index: int,
    window: LayerTime,
    below: list[CompositionLayer],
    width: int,
    height: int,
) -> list[CompositionLayer]:
    """The cost checklist strip, directly under the tally chip's rectangle."""
    progress = parse_progress((scene.get("props") or {}).get("progress"))
    if progress is None:
        return []
    markup, css = render_progress(progress, index=index, width=width)
    # The tally chip owns x=4% width, y=6% height, w=30%, h=16%; sit just below it.
    tally_bottom = round(height * 0.06) + round(height * 0.16)
    gap = round(height * 0.01)
    # Panel height from the strip's own metrics (see render_progress): padding
    # 2 x 12 units, then 26-unit text at 1.25 line height plus a 6-unit gap per row.
    unit = max(0.5, width / 1920.0)
    panel_h = round((24 + 38.5 * len(progress.items) - 6) * unit)
    return [
        CompositionLayer(
            id=f"scene_{index}_progress",
            kind="html",
            preset="progress",
            rect=Rect(
                x=round(width * 0.04),
                y=tally_bottom + gap,
                width=round(width * 0.24),
                height=panel_h,
            ),
            time=window,
            content=LayerContent(text=markup),
            style={"extra_css": css},
            track=index * 10 + 6,
            z=max((layer.z for layer in below), default=0) + 1,
        )
    ]


def _tally_layers(
    scene: Mapping[str, Any],
    previous: int | None,
    index: int,
    start: float,
    duration: float,
    window: LayerTime,
    below: list[CompositionLayer],
    width: int,
    height: int,
) -> list[CompositionLayer]:
    """The running tally chip, above everything else in the scene."""
    props = scene.get("props") or {}
    tally = parse_tally(props.get("tally"))
    if tally is None:
        return []
    delay = _tally_delay(scene, start, duration)
    if delay is None:
        return []
    markup, css = render_tally(tally, previous, index=index, width=width, delay=delay)
    margin_x, margin_y = round(width * 0.04), round(height * 0.06)
    return [
        CompositionLayer(
            id=f"scene_{index}_tally",
            kind="html",
            preset="tally",
            rect=Rect(
                x=margin_x, y=margin_y, width=round(width * 0.3), height=round(height * 0.16)
            ),
            time=window,
            content=LayerContent(text=markup),
            style={"extra_css": css},
            track=index * 10 + 9,
            z=max((layer.z for layer in below), default=0) + 1,
        )
    ]


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
#: The headline reveal time the typographic renderer writes into its markup.
_COUNT_DELAY = re.compile(r'class="typo-count-step"[^>]*animation-delay:\s*([\d.]+)s')
_HEADLINE_DELAY = re.compile(r'class="typo-line[^"]*"[^>]*animation-delay:\s*([\d.]+)s')
#: How far back from a result the words that set up its calculation may sit.
_EXPR_WINDOW_SECONDS = 6.0
#: The expression appears this long before its result when nothing is heard.
_EXPR_LEAD_SECONDS = 0.8
#: A row's calculation appears at least this long after the row above lands.
_ROW_GAP_SECONDS = 0.4


def _number_tokens(text: str) -> set[str]:
    """The digits in ``text`` without currency marks, commas or trailing punctuation."""
    return {match.replace(",", "") for match in _NUMBER.findall(text)}


def _spoken_at(row: CalcRow, words: list[Any], after: float = -1.0) -> float | None:
    """Clip time of the first word after ``after`` that says this row's amount.

    Digits must match exactly (``₹150.`` says 150, ``₹3,764`` does not say 3), and
    each row searches after the previous row's word, so two rows with the same
    amount land on the two places it is said.
    """
    if row.unit == "%":
        token = f"{row.amount:g}%"
    elif float(row.amount).is_integer():
        token = str(abs(int(row.amount)))
    else:
        token = f"{abs(row.amount):g}"
    for word in words:
        if not isinstance(word, Mapping):
            continue
        text = str(word.get("word", ""))
        said = token in text if row.unit == "%" else token in _number_tokens(text)
        if not said:
            continue
        try:
            at = float(word["start"])
        except (KeyError, TypeError, ValueError):
            continue
        if at > after:
            return at
    return None


def _expr_at(row: CalcRow, words: list[Any], result_at: float, after: float = -1.0) -> float:
    """When the row's label and expression appear: the word that sets up the result."""
    numbers = _number_tokens(row.expr)
    if numbers:
        earliest: float | None = None
        for word in words:
            if not isinstance(word, Mapping):
                continue
            if not _number_tokens(str(word.get("word", ""))) & numbers:
                continue
            try:
                at = float(word["start"])
            except (KeyError, TypeError, ValueError):
                continue
            # Only words after the row above: its own amount is not this row's setup.
            if at <= after or not result_at - _EXPR_WINDOW_SECONDS <= at <= result_at:
                continue
            if earliest is None or at < earliest:
                earliest = at
        if earliest is not None:
            return max(0.0, earliest)
    return max(0.0, result_at - _EXPR_LEAD_SECONDS)


def _calc_reveal_times(
    scene: Mapping[str, Any], start: float, duration: float
) -> list[tuple[float, float]]:
    """Clip-relative ``(expr_at, result_at)`` for each calc row.

    The result lands when its amount is spoken; the expression appears at the
    word that sets it up, so the calculation reads before its answer.
    """
    props = scene.get("props") or {}
    if not props.get("calc"):
        return []
    rows = parse_calc(props["calc"])
    words = _clip_relative_words(props.get("word_times"), start)
    stagger = max(0.4, duration * 0.6) / max(1, len(rows))
    latest = max(0.3, duration - MIN_ROW_HOLD_SECONDS)
    reveal_at: list[tuple[float, float]] = []
    heard = -1.0
    for position, row in enumerate(rows):
        spoken = _spoken_at(row, words, after=heard)
        fallback = spoken is None
        if spoken is not None:
            heard = spoken
        at = spoken if spoken is not None else 0.4 + position * stagger
        result_at = min(at, latest)
        expr_at = (
            max(0.0, result_at - _EXPR_LEAD_SECONDS)
            if fallback
            else _expr_at(row, words, result_at, after=reveal_at[-1][1] if reveal_at else -1.0)
        )
        if reveal_at:
            # The next calculation starts only once the row above has landed.
            expr_at = min(result_at, max(expr_at, reveal_at[-1][1] + _ROW_GAP_SECONDS))
        reveal_at.append((expr_at, result_at))
    return reveal_at


def _calc_layers(
    scene: Mapping[str, Any],
    index: int,
    start: float,
    duration: float,
    window: LayerTime,
    below: list[CompositionLayer],
    width: int,
    height: int,
) -> list[CompositionLayer]:
    """The calculation card, top-right; each row appears as its amount is spoken."""
    props = scene.get("props") or {}
    if not props.get("calc"):
        return []
    rows = parse_calc(props["calc"])
    reveal_at = _calc_reveal_times(scene, start, duration)
    markup, css = render_calc_card(rows, index=index, width=width, reveal_at=reveal_at)
    card_w, card_h = round(width * 0.44), round(height * 0.4)
    return [
        CompositionLayer(
            id=f"scene_{index}_calc",
            kind="html",
            preset="calc",
            rect=Rect(
                x=width - round(width * 0.04) - card_w,
                y=round(height * 0.06),
                width=card_w,
                height=card_h,
            ),
            time=window,
            content=LayerContent(text=markup),
            style={"extra_css": css + f"      #scene_{index}_calc {{ text-align: right; }}\n"},
            track=index * 10 + 8,
            z=max((layer.z for layer in below), default=0) + 1,
        )
    ]


def _scene_id(scene: Mapping[str, Any], index: int) -> str:
    """Use the plan's id only when :class:`CompositionScene` will accept it.

    ``ScenePlan`` leaves ``id`` unconstrained, while the Composition model
    requires ``[A-Za-z0-9_-]+``; an odd-but-legal plan id must not abort the
    compile, so it falls back to the scene's index.
    """
    candidate = str(scene.get("id") or "")
    if re.fullmatch(r"[A-Za-z0-9_-]+", candidate):
        return candidate
    return f"scene_{index}"


def _design_for(plan: Mapping[str, Any], scenes: list[dict[str, Any]]) -> DesignTokens | None:
    needs_design = any(scene.get("type") in {"typographic", "map", "composite"} for scene in scenes)
    if not needs_design:
        return None
    return load_design(str(plan.get("design") or ""))


def _asset_by_type(scene: dict[str, Any], *types: str) -> dict[str, Any] | None:
    for asset in scene.get("assets", []) or []:
        if isinstance(asset, dict) and asset.get("asset_type") in types:
            return dict(asset)
    return None


#: A photo shot's Ken Burns: zoom 1.00 to 1.10 with a slow pan, alternating
#: direction shot by shot so neighbouring photos do not drift the same way.
_PHOTO_SCALE_END = 1.10
_PHOTO_PANS = (("0%, 0%", "2%, -1.5%"), ("2%, 1.5%", "-1.5%, 0%"))


def _shot_layers(
    scene: dict[str, Any],
    index: int,
    start: float,
    end: float,
    width: int,
    height: int,
    fps: int,
) -> list[CompositionLayer]:
    """Sequential media layers for a footage scene's ``shots``; ``[]`` if it has none.

    The scene's window is split evenly in shot order and the cuts are hard. Each
    shot covers the frame and is muted; a clip shorter than its slot loops and a
    longer one is cut off by the layer's duration. A photo gets a clear Ken Burns
    zoom (1.00 to 1.10) with a slow pan that alternates photo to photo.
    """
    shots = (scene.get("props") or {}).get("shots") if scene.get("type") == "footage" else None
    if not shots:
        return []
    if any(not (isinstance(shot, dict) and shot.get("path")) for shot in shots):
        raise SceneRenderError(
            f"scene {scene.get('id')!r} has shots without media; run the asset stage first"
        )
    count = len(shots)
    layers: list[CompositionLayer] = []
    photo_position = 0
    for position, shot in enumerate(shots):
        slot_start = (
            start if position == 0 else quantise(start + (end - start) * position / count, fps)
        )
        slot_end = (
            end
            if position == count - 1
            else quantise(start + (end - start) * (position + 1) / count, fps)
        )
        if slot_end <= slot_start:
            continue
        common: dict[str, Any] = {
            "id": f"scene_{index}_shot{position}",
            "kind": "media",
            "rect": _full_canvas(width, height),
            "time": LayerTime(start=slot_start, end=slot_end),
            "content": LayerContent(source=str(shot["path"])),
            "track": index,
            "z": 0,
        }
        if shot.get("kind") == "video":
            layers.append(
                CompositionLayer(
                    media_kind="video",
                    style={"object_fit": "cover", "loop": True},
                    **common,
                )
            )
        else:
            pan_from, pan_to = _PHOTO_PANS[photo_position % len(_PHOTO_PANS)]
            photo_position += 1
            layers.append(
                CompositionLayer(
                    media_kind="image",
                    style={"object_fit": "cover"},
                    motions=[
                        MotionSpec(
                            name="drift",
                            params={
                                "from": f"scale(1) translate({pan_from})",
                                "to": f"scale({_PHOTO_SCALE_END:.2f}) translate({pan_to})",
                            },
                            ease="ease-in-out",
                        )
                    ],
                    **common,
                )
            )
    return layers


def _full_canvas(width: int, height: int) -> Rect:
    return Rect(x=0, y=0, width=width, height=height)


def _scene_layers(
    scene: dict[str, Any],
    index: int,
    start: float,
    end: float,
    duration: float,
    width: int,
    height: int,
    design: DesignTokens | None,
    fps: int = DEFAULT_FPS,
) -> list[CompositionLayer]:
    # The scene's window is the one the caller already quantised from the
    # running cursor. Re-deriving it here as ``quantise(start + duration)``
    # rounds a second time and can land a frame past the scene's own end, and
    # on the last scene past the composition's duration, which the Composition
    # validator rejects outright.
    window = LayerTime(start=start, end=end)
    layers: list[CompositionLayer] = []

    props_early = scene.get("props") or {}
    if scene.get("type") == "typographic" and props_early.get("layout") == "ledger":
        if design is None:
            raise SceneRenderError(
                f"scene {scene.get('id')!r} is a ledger but no design was loaded"
            )
        total = props_early.get("total") or {}
        markup, css = render_ledger(
            parse_calc(props_early.get("rows") or []),
            total_label=str(total.get("label") or ""),
            total=float(total.get("amount") or 0),
            index=index,
            width=width,
            duration=duration,
            heading=str(props_early.get("heading") or ""),
        )
        layers.append(
            CompositionLayer(
                id=f"scene_{index}_ledger",
                kind="html",
                preset="ledger",
                rect=_full_canvas(width, height),
                time=window,
                content=LayerContent(text=markup),
                style={"background": design.palette.bg, "extra_css": css},
                track=index,
            )
        )
        return layers

    if scene.get("type") == "typographic":
        if design is None:
            raise SceneRenderError(
                f"scene {scene.get('id')!r} is typographic but no design was loaded"
            )
        props = scene.get("props") or {}
        rendered = render_typographic(
            scene,
            design,
            index=index,
            duration=duration,
            width=width,
            words=_clip_relative_words(props.get("word_times"), start),
        )
        layers.append(
            CompositionLayer(
                id=f"scene_{index}_typo",
                kind="html",
                preset="typographic",
                rect=_full_canvas(width, height),
                time=window,
                content=LayerContent(text=rendered.markup),
                style={"background": design.palette.bg, "extra_css": rendered.css},
                track=index,
            )
        )
        return layers

    if scene.get("type") == "map":
        if design is None:
            raise SceneRenderError(f"scene {scene.get('id')!r} is a map but no design was loaded")
        props = scene.get("props") or {}
        map_render = scene_renderer("map")(
            scene,
            design,
            index=index,
            duration=duration,
            width=width,
            height=height,
            words=_clip_relative_words(props.get("word_times"), start),
        )
        layers.append(
            CompositionLayer(
                id=f"scene_{index}_map",
                kind="html",
                preset="map",
                rect=_full_canvas(width, height),
                time=window,
                content=LayerContent(text=map_render.markup),
                style={"background": design.palette.bg, "extra_css": map_render.css},
                track=index,
            )
        )
        return layers

    if scene.get("type") == "composite":
        if design is None:
            raise CompositeSceneError(
                f"scene {scene.get('id')!r} is composite but no design was loaded"
            )
        props = scene.get("props") or {}
        images = [
            asset
            for asset in scene.get("assets", []) or []
            if isinstance(asset, dict) and asset.get("asset_type") == "image"
        ]
        composite = render_composite(
            scene,
            design,
            index=index,
            duration=duration,
            width=width,
            height=height,
            image_count=len(images),
            words=_clip_relative_words(props.get("word_times"), start),
        )
        track = index * 10
        if composite.background:
            layers.append(
                CompositionLayer(
                    id=f"scene_{index}_paper",
                    kind="html",
                    preset="composite_background",
                    rect=_full_canvas(width, height),
                    time=window,
                    content=LayerContent(text=composite.background),
                    style={"extra_css": composite.css},
                    track=track,
                    z=0,
                )
            )
        # A mounted still (split, compare) sits inside a paper mat. Drifting it
        # scales the clip past the mount and eats the mat edge, so only the
        # full-bleed over_media still, which has no mount, gets room to move.
        mounted = str(props.get("layout") or "") in {"split", "compare"}
        for position, media in enumerate(composite.media):
            asset = images[media.asset_index] if media.asset_index < len(images) else {}
            x, y, media_w, media_h = media.rect
            layers.append(
                CompositionLayer(
                    id=f"scene_{index}_media{position}",
                    kind="media",
                    media_kind="image",
                    rect=Rect(x=x, y=y, width=media_w, height=media_h),
                    time=window,
                    content=LayerContent(source=str(asset.get("local_path", ""))),
                    style={"object_fit": "cover"},
                    motions=(
                        []
                        if mounted
                        else [
                            MotionSpec(
                                name="drift",
                                params={"move": index % 4},
                                ease="ease-in-out",
                            )
                        ]
                    ),
                    track=track + position + 1,
                    z=1,
                )
            )
        layers.append(
            CompositionLayer(
                id=f"scene_{index}_composite",
                kind="html",
                preset="composite",
                rect=_full_canvas(width, height),
                time=window,
                content=LayerContent(text=composite.markup),
                style={"extra_css": composite.css},
                track=track + len(composite.media) + 1,
                z=2,
            )
        )
        return layers

    shot_layers = _shot_layers(scene, index, start, end, width, height, fps)
    if shot_layers:
        return shot_layers

    visual = _asset_by_type(scene, "video", "image", "data_viz")
    if visual is not None:
        source = str(visual.get("local_path", ""))
        if visual.get("asset_type") == "video":
            layers.append(
                CompositionLayer(
                    id=f"scene_{index}_video",
                    kind="media",
                    media_kind="video",
                    rect=_full_canvas(width, height),
                    time=window,
                    content=LayerContent(source=source),
                    style={"object_fit": "cover", "loop": True},
                    track=index,
                    z=0,
                )
            )
        else:
            layers.append(
                CompositionLayer(
                    id=f"scene_{index}_img",
                    kind="media",
                    media_kind="image",
                    rect=_full_canvas(width, height),
                    time=window,
                    content=LayerContent(source=source),
                    style={"object_fit": "cover"},
                    motions=[
                        MotionSpec(
                            name="drift",
                            params={"move": index % 4},
                            ease="ease-in-out",
                        )
                    ],
                    track=index,
                    z=0,
                )
            )
        return layers

    text = str(scene.get("narration") or scene.get("visual_description") or scene.get("id", ""))
    layers.append(
        CompositionLayer(
            id=f"scene_{index}_placeholder",
            kind="html",
            preset="placeholder",
            rect=_full_canvas(width, height),
            time=window,
            content=LayerContent(text=text),
            track=index,
            z=0,
        )
    )
    return layers


def _clip_relative_words(word_times: Any, start: float) -> list[Any]:
    """Rebase transcript word times onto their scene's own clock.

    ``props["word_times"]`` carries absolute narration-track times, which is
    right on disk but wrong for CSS: HyperFrames seeks every clip from its own
    ``data-start``, so an ``animation-delay`` is clip-relative.
    """
    words: list[Any] = []
    for word in word_times or []:
        if not isinstance(word, Mapping):
            words.append(word)
            continue
        try:
            word_start = float(word["start"])
            word_end = float(word["end"])
        except (KeyError, TypeError, ValueError):
            words.append(word)
            continue
        if not (math.isfinite(word_start) and math.isfinite(word_end)):
            words.append(word)
            continue
        words.append({**word, "start": word_start - start, "end": word_end - start})
    return words
