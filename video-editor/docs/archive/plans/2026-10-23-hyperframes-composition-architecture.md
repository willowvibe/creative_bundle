# HyperFrames Composition Architecture — Implementation Plan (Slice 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the three per-video HyperFrames compilers with one `Composition` model, one CSS-motion registry and one compiler, proven by offline parity rebuilds of two already-shipped videos.

**Architecture:** Every input (RenderGraph overlays, scene plans, and later content packs) is adapted into a single `Composition` intermediate representation. One compiler turns a `Composition` into a HyperFrames HTML project, emitting root-level `class="clip"` layers with explicit z-index and CSS keyframes from a named motion registry. The two existing compiler modules keep their public functions and delegate.

**Tech Stack:** Python 3.11+, pydantic v2, HyperFrames CLI v0.8.x, FFmpeg, pytest.

Spec: `docs/archive/specs/2026-10-23-hyperframes-composition-architecture-design.md`.

## Global Constraints

- No GSAP and no HyperFrames registry components. Deterministic CSS keyframes only.
- The root element keeps `data-no-timeline` (preserves the #239 45 s-poll fix).
- Every visual layer is a **root-level** `class="clip"` child with `data-start`, `data-duration`, `data-track-index` and an explicit `z-index`.
- Times are formatted with exactly three decimals (`f"{t:.3f}"`).
- No motion may depend on JavaScript. All animations must be seekable by `Animation.currentTime`.
- No test may make a network call or spend money.
- After every task the whole suite must pass: `uv run pytest`.
- Public functions keep their current signatures: `compile_hyperframes_project(graph, project_dir, base_video=None)`, `compile_scene_plan_project(plan, project_dir, *, fps=30)`, `render_scene_plan(...)`.
- Motion names are lowercase snake case; layer ids match `[A-Za-z0-9_-]+`.
- Inline `style` attributes never contain a double quote (existing tests assert `font-family:'Montserrat',` verbatim).

---

## File Structure

| File | Responsibility |
|---|---|
| `packages/core/src/video_editor_core/motion/spec.py` | `MotionCSS` value type + deterministic keyframe naming |
| `packages/core/src/video_editor_core/motion/registry.py` | primitive registry, `build_motion`, clamping, errors |
| `packages/core/src/video_editor_core/motion/primitives.py` | transform primitives (drift, rise, pop, fade, stamp, flip, lock, pulse) |
| `packages/core/src/video_editor_core/motion/text.py` | structural/text primitives (draw, count, pill, word_highlight) |
| `packages/core/src/video_editor_core/composition.py` | the `Composition` model, canvas mapping, quantise |
| `packages/core/src/video_editor_core/project_assets.py` | `AssetCopier` moved out of `hyperframes_project.py` |
| `packages/core/src/video_editor_core/compile_composition.py` | the one compiler |
| `packages/core/src/video_editor_core/adapters/from_render_graph.py` | RenderGraph → Composition |
| `packages/core/src/video_editor_core/adapters/from_scene_plan.py` | scene-plan dict → Composition |
| `packages/core/src/video_editor_core/designs.py` | `DesignTokens.layers` |
| `packages/core/src/video_editor_core/safe_area.py` | named safe-area profiles |
| `packages/core/src/video_editor_core/fonts.py` | Cormorant Garamond + Noto Serif KR bundled |
| `packages/core/src/video_editor_core/glyphs.py` | CJK glyph manifest + gate |
| `packages/core/src/video_editor_core/hyperframes_cli.py` | `check_project`, `snapshot_project` |
| `apps/cli/src/video_editor_cli/composition_qa.py` | the five gates as `QaCheck`s |
| `scripts/build_font_subsets.py` | reproducible font subsetting |
| `scripts/parity_rebuild.py` | the two offline parity rebuilds |

---

## Task 1: Motion value type and registry

**Files:**
- Create: `packages/core/src/video_editor_core/motion/__init__.py`
- Create: `packages/core/src/video_editor_core/motion/spec.py`
- Create: `packages/core/src/video_editor_core/motion/registry.py`
- Test: `tests/test_motion_spec.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `MotionCSS(keyframes, declaration, props, markup)` with `+`; `keyframes_name(name, params) -> str`; `register(name, *, default_duration=None)` decorator; `PRIMITIVES: dict[str, Primitive]`; `MotionError(ValueError)`; `build_motion(motion, *, clip_duration: float, tempo: float = 1.0) -> MotionCSS`, where `motion` is any object with `.name`, `.params`, `.delay`, `.duration`, `.ease` (the `MotionSpec` model arrives in Task 4).

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the motion registry: naming, defaults, clamping, validation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from video_editor_core.motion import (
    PRIMITIVES,
    MotionCSS,
    MotionError,
    build_motion,
    register,
)


def spec(name: str, **params: object) -> SimpleNamespace:
    return SimpleNamespace(name=name, params=params, delay=0.0, duration=None, ease=None)


@pytest.fixture
def dummy():
    @register("_dummy")
    def _build(params, name, duration, ease):
        return MotionCSS(
            keyframes=f"@keyframes {name} {{}}",
            declaration=f"{name} {duration:.3f}s {ease}",
        )

    yield
    PRIMITIVES.pop("_dummy", None)


def test_unknown_motion_lists_the_registry(dummy):
    with pytest.raises(MotionError) as exc:
        build_motion(spec("nope"), clip_duration=2.0)
    assert "nope" in str(exc.value)
    assert "_dummy" in str(exc.value)


def test_identical_name_and_params_share_a_keyframe_name(dummy):
    a = build_motion(spec("_dummy", distance=10), clip_duration=2.0)
    b = build_motion(spec("_dummy", distance=10), clip_duration=9.0)
    c = build_motion(spec("_dummy", distance=12), clip_duration=2.0)
    assert a.keyframes == b.keyframes
    assert a.keyframes != c.keyframes


def test_duration_defaults_to_the_clip(dummy):
    assert "3.500s" in build_motion(spec("_dummy"), clip_duration=3.5).declaration


def test_duration_is_clamped_to_the_remaining_clip(dummy):
    # A 2.0 s clip with a 0.5 s delay leaves 1.5 s. The two times must stay
    # distinct: when delay == duration a substring match cannot tell them
    # apart, which is how an inert clamp survived review once already.
    motion = spec("_dummy")
    motion.delay = 0.5
    motion.duration = 9.0
    declaration = build_motion(motion, clip_duration=2.0).declaration
    assert declaration.split()[1] == "1.500s"
    assert declaration.endswith(" 0.500s")


def test_delay_is_written_as_the_second_time(dummy):
    motion = spec("_dummy")
    motion.delay = 0.25
    assert build_motion(motion, clip_duration=2.0).declaration.endswith(" 0.250s")


def test_delay_is_clamped_inside_the_clip(dummy):
    motion = spec("_dummy")
    motion.delay = 9.0
    assert build_motion(motion, clip_duration=2.0).declaration.endswith(" 1.999s")


def test_a_primitive_with_no_declaration_is_returned_untouched():
    @register("_silent")
    def _build(params, name, duration, ease):
        return MotionCSS(keyframes="", declaration="", props="", markup="<i></i>")

    try:
        built = build_motion(spec("_silent"), clip_duration=2.0)
        assert built.declaration == ""
        assert built.markup == "<i></i>"
    finally:
        PRIMITIVES.pop("_silent", None)


def test_a_delay_with_no_declaration_is_rejected():
    # There is no ``animation`` value to append the delay to, so accepting it
    # would render the motion at t=0 while its duration was clamped by a delay
    # that went nowhere.
    @register("_silent")
    def _build(params, name, duration, ease):
        return MotionCSS(keyframes="", declaration="", props="", markup="<i></i>")

    try:
        motion = spec("_silent")
        motion.delay = 0.5
        with pytest.raises(MotionError, match="delay"):
            build_motion(motion, clip_duration=2.0)
    finally:
        PRIMITIVES.pop("_silent", None)


@pytest.mark.parametrize("tempo", [0, -1.0, float("nan"), float("inf"), "fast", None])
def test_a_bad_tempo_is_rejected(dummy, tempo):
    with pytest.raises(MotionError, match="tempo"):
        build_motion(spec("_dummy"), clip_duration=2.0, tempo=tempo)


def test_negative_delay_is_rejected(dummy):
    motion = spec("_dummy")
    motion.delay = -1.0
    with pytest.raises(MotionError):
        build_motion(motion, clip_duration=2.0)


def test_non_finite_duration_is_rejected(dummy):
    motion = spec("_dummy")
    motion.duration = float("nan")
    with pytest.raises(MotionError):
        build_motion(motion, clip_duration=2.0)


def test_motioncss_addition_concatenates_parts():
    css = MotionCSS(keyframes="a", declaration="one") + MotionCSS(keyframes="b", props="p")
    assert css.keyframes == "a\nb"
    assert css.declaration == "one"
    assert css.props == "p"
```

- [ ] **Step 2: Run the tests to watch them fail**

Run: `uv run pytest tests/test_motion_spec.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_editor_core.motion'`.

- [ ] **Step 3: Write `motion/spec.py`**

```python
"""Motion primitives compile to deterministic, seekable CSS.

A primitive is a pure function: given its parameters it returns the
``@keyframes`` it needs, the ``animation`` declaration that applies them, any
extra static properties, and any markup the effect requires. Nothing here
touches the DOM, so every effect stays seekable through
``Animation.currentTime`` and HyperFrames' deterministic frame seek keeps
working.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

DEFAULT_EASE = "cubic-bezier(.2,.8,.2,1)"


@dataclass(frozen=True)
class MotionCSS:
    """The CSS one motion contributes to a layer."""

    keyframes: str = ""
    declaration: str = ""
    props: str = ""
    markup: str = ""

    def __add__(self, other: MotionCSS) -> MotionCSS:
        return MotionCSS(
            keyframes="\n".join(p for p in (self.keyframes, other.keyframes) if p),
            declaration=", ".join(
                p for p in (self.declaration, other.declaration) if p
            ),
            props=" ".join(p for p in (self.props, other.props) if p),
            markup=self.markup + other.markup,
        )


def keyframes_name(name: str, params: Mapping[str, Any]) -> str:
    """A CSS identifier that is identical for identical name and parameters.

    Duration and easing are deliberately not part of the key: they live in the
    ``animation`` declaration, while keyframes stay percentage based, so ten
    staggered cells sharing one move emit a single ``@keyframes`` block.
    """
    payload = json.dumps([name, dict(params)], sort_keys=True, default=str)
    return f"ve_{name}_{hashlib.sha1(payload.encode()).hexdigest()[:8]}"
```

- [ ] **Step 4: Write `motion/registry.py`**

```python
"""The named-primitive registry and the code that resolves a motion spec."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from video_editor_core.motion.spec import DEFAULT_EASE, MotionCSS, keyframes_name

# ``declaration`` must describe exactly one animation whose only ``<time>`` is
# the duration: ``build_motion`` appends the delay as the shorthand's second
# ``<time>``. A builder that emits its own delay, or a comma-joined pair, would
# silently break that (a third ``<time>`` makes the browser drop the whole
# shorthand; a pair takes the delay on the last animation only).
Builder = Callable[[Mapping[str, Any], str, float, str], MotionCSS]


@dataclass(frozen=True)
class Primitive:
    """One registered motion: how to build it, and its usual length."""

    builder: Builder
    default_duration: float | None = None


PRIMITIVES: dict[str, Primitive] = {}


class MotionError(ValueError):
    """A motion spec the registry cannot compile."""


def register(
    name: str, *, default_duration: float | None = None
) -> Callable[[Builder], Builder]:
    """Register ``builder`` under ``name``.

    ``default_duration=None`` means "run for the whole clip".
    """

    def _wrap(builder: Builder) -> Builder:
        PRIMITIVES[name] = Primitive(builder=builder, default_duration=default_duration)
        return builder

    return _wrap


def build_motion(motion: Any, *, clip_duration: float, tempo: float = 1.0) -> MotionCSS:
    """Compile one motion spec against ``clip_duration`` seconds of clip.

    HyperFrames seeks CSS animations from the clip's own ``data-start``, so a
    motion that outlives its clip is silently cut off. Clamping here means the
    declaration always describes something the viewer can actually see.
    """
    primitive = PRIMITIVES.get(motion.name)
    if primitive is None:
        raise MotionError(
            f"unknown motion {motion.name!r}; known: {', '.join(sorted(PRIMITIVES))}"
        )
    if clip_duration <= 0:
        raise MotionError(f"clip duration must be positive, got {clip_duration!r}")
    if not isinstance(tempo, int | float) or not math.isfinite(tempo) or tempo <= 0:
        raise MotionError(f"tempo must be a positive finite number, got {tempo!r}")

    delay = _finite(motion.delay, "delay", motion.name)
    if delay < 0:
        raise MotionError(f"motion {motion.name!r} delay must be >= 0, got {delay}")

    requested = motion.duration
    if requested is None:
        duration = (
            clip_duration
            if primitive.default_duration is None
            else primitive.default_duration / tempo
        )
    else:
        duration = _finite(requested, "duration", motion.name)
    if duration <= 0:
        raise MotionError(
            f"motion {motion.name!r} duration must be positive, got {duration}"
        )

    delay = min(delay, max(0.0, clip_duration - 0.001))
    duration = min(duration, max(0.001, clip_duration - delay))
    name = keyframes_name(motion.name, motion.params)
    built = primitive.builder(
        dict(motion.params), name, duration, motion.ease or DEFAULT_EASE
    )
    if not built.declaration:
        # ``count`` and ``word_highlight`` write their own timing into their
        # markup, so there is no ``animation`` value here to postpone — and
        # therefore nowhere for the delay to go. Appending it would silently
        # vanish while the duration above has already been shortened by it,
        # which renders a motion at the wrong time with no error. Refuse
        # instead; a caller that needs a later start moves the clip later.
        if delay > 0:
            raise MotionError(
                f"motion {motion.name!r} draws its own markup and cannot take a "
                f"delay; start the clip later instead (got delay={delay})"
            )
        return built
    # The second ``<time>`` in the ``animation`` shorthand is the delay, so
    # appending it keeps one declaration per animation. A separate
    # ``animation-delay`` list would have to stay aligned with a list the
    # compiler builds by joining declarations, and would silently reset the
    # shorthand's own delay if emitted before it.
    return replace(built, declaration=f"{built.declaration} {delay:.3f}s")


def _finite(value: Any, field: str, motion: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise MotionError(
            f"motion {motion!r} {field} must be a number, got {value!r}"
        ) from None
    if not math.isfinite(number):
        raise MotionError(f"motion {motion!r} {field} must be finite, got {value!r}")
    return number
```

- [ ] **Step 5: Write `motion/__init__.py`**

```python
"""Named motion primitives compiled to deterministic CSS."""

from video_editor_core.motion.registry import (
    PRIMITIVES,
    MotionError,
    Primitive,
    build_motion,
    register,
)
from video_editor_core.motion.spec import DEFAULT_EASE, MotionCSS, keyframes_name

__all__ = [
    "DEFAULT_EASE",
    "PRIMITIVES",
    "MotionCSS",
    "MotionError",
    "Primitive",
    "build_motion",
    "keyframes_name",
    "register",
]
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_motion_spec.py -v`
Expected: PASS (12 test functions, 17 cases).

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/video_editor_core/motion tests/test_motion_spec.py
git commit -m "feat(motion): add the CSS motion value type and registry"
```

---

## Task 2: Transform primitives

**Files:**
- Create: `packages/core/src/video_editor_core/motion/primitives.py`
- Modify: `packages/core/src/video_editor_core/motion/__init__.py`
- Test: `tests/test_motion_primitives.py`

**Interfaces:**
- Consumes: `register`, `MotionError`, `MotionCSS`, `keyframes_name` from Task 1.
- Produces: `KEN_BURNS_MOVES: tuple[tuple[str, str], ...]` (the four legacy moves, verbatim) and registered primitives `drift`, `rise`, `pop`, `fade`, `stamp`, `flip`, `lock`, `pulse`. Importing `video_editor_core.motion` registers them.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the transform primitives."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from video_editor_core.motion import PRIMITIVES, MotionError, build_motion
from video_editor_core.motion.primitives import KEN_BURNS_MOVES


def spec(name: str, **params: object) -> SimpleNamespace:
    return SimpleNamespace(name=name, params=params, delay=0.0, duration=None, ease=None)


def test_every_transform_primitive_is_registered():
    for name in ("drift", "rise", "pop", "fade", "stamp", "flip", "lock", "pulse"):
        assert name in PRIMITIVES


def test_ken_burns_moves_are_the_legacy_four():
    assert KEN_BURNS_MOVES == (
        ("scale(1) translate(0, 0)", "scale(1.12) translate(0, -1%)"),
        ("scale(1.08) translate(-2.5%, 0)", "scale(1.08) translate(2.5%, 0)"),
        ("scale(1.12) translate(0, -1%)", "scale(1.02) translate(0, 0)"),
        ("scale(1.08) translate(2.5%, 1%)", "scale(1.08) translate(-2.5%, -1%)"),
    )


def test_drift_selects_the_requested_move_and_runs_for_the_clip():
    css = build_motion(spec("drift", move=2), clip_duration=4.0)
    assert "from { transform: scale(1.12) translate(0, -1%); }" in css.keyframes
    assert "to { transform: scale(1.02) translate(0, 0); }" in css.keyframes
    assert "4.000s" in css.declaration
    assert css.props == "transform-origin:center;"


def test_drift_wraps_the_move_index():
    assert "@keyframes" in build_motion(spec("drift", move=5), clip_duration=2.0).keyframes


def test_drift_accepts_an_explicit_transform_pair():
    css = build_motion(
        spec("drift", **{"from": "scale(1)", "to": "scale(1.07)"}), clip_duration=3.0
    )
    assert "to { transform: scale(1.07); }" in css.keyframes


def test_rise_uses_its_distance_and_default_length():
    css = build_motion(spec("rise", distance=40), clip_duration=4.0)
    assert "translate3d(0,40px,0)" in css.keyframes
    assert "0.450s" in css.declaration


def test_pop_and_fade_are_registered_with_their_own_lengths():
    assert "scale(.94)" in build_motion(spec("pop"), clip_duration=4.0).keyframes
    assert "0.400s" in build_motion(spec("pop"), clip_duration=4.0).declaration
    assert "opacity:0" in build_motion(spec("fade"), clip_duration=4.0).keyframes


def test_stamp_settles_from_120_percent():
    css = build_motion(spec("stamp"), clip_duration=4.0)
    assert "scale(1.2)" in css.keyframes
    assert "blur(10px)" in css.keyframes
    assert "blur(0)" in css.keyframes


def test_flip_turns_the_tile_over():
    css = build_motion(spec("flip"), clip_duration=4.0)
    assert "rotateY(90deg)" in css.keyframes
    assert "backface-visibility:hidden" in css.props


def test_lock_slides_from_the_named_side():
    left = build_motion(spec("lock", side="left"), clip_duration=4.0)
    right = build_motion(spec("lock", side="right"), clip_duration=4.0)
    assert "translate3d(-60px,0,0)" in left.keyframes
    assert "translate3d(60px,0,0)" in right.keyframes


def test_lock_rejects_an_unknown_side():
    with pytest.raises(MotionError):
        build_motion(spec("lock", side="up"), clip_duration=4.0)


def test_pulse_breathes_for_the_whole_clip():
    css = build_motion(spec("pulse"), clip_duration=3.0)
    assert "infinite alternate" in css.declaration
    assert "brightness(1.35)" in css.keyframes
```

- [ ] **Step 2: Run the tests to watch them fail**

Run: `uv run pytest tests/test_motion_primitives.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_editor_core.motion.primitives'`.

- [ ] **Step 3: Write `motion/primitives.py`**

```python
"""Transform motion primitives.

``drift`` reproduces the four Ken Burns moves the scene-plan compiler shipped
(``KEN_BURNS_MOVES``) bit-for-bit: the parity rebuild compares frames, so a
"similar" move is a bug.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from video_editor_core.motion.registry import MotionError, register
from video_editor_core.motion.spec import MotionCSS

KEN_BURNS_SCALE_END = 1.12
KEN_BURNS_MOVES: tuple[tuple[str, str], ...] = (
    ("scale(1) translate(0, 0)", f"scale({KEN_BURNS_SCALE_END}) translate(0, -1%)"),
    ("scale(1.08) translate(-2.5%, 0)", "scale(1.08) translate(2.5%, 0)"),
    (f"scale({KEN_BURNS_SCALE_END}) translate(0, -1%)", "scale(1.02) translate(0, 0)"),
    ("scale(1.08) translate(2.5%, 1%)", "scale(1.08) translate(-2.5%, -1%)"),
)


def _keyframes(name: str, start: str, end: str) -> str:
    return f"@keyframes {name} {{ from {{ {start}; }} to {{ {end}; }} }}"


def _num(params: Mapping[str, Any], key: str, default: float) -> float:
    value = params.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        raise MotionError(f"{key} must be a number, got {value!r}") from None


@register("drift")
def drift(params, name, duration, ease):
    """Slow zoom/pan. ``move`` picks a Ken Burns pair; from/to override it."""
    if "from" in params or "to" in params:
        start = str(params.get("from") or "scale(1)")
        end = str(params.get("to") or "scale(1.08)")
    else:
        move = int(_num(params, "move", 0))
        start, end = KEN_BURNS_MOVES[move % len(KEN_BURNS_MOVES)]
    return MotionCSS(
        keyframes=_keyframes(name, f"transform: {start}", f"transform: {end}"),
        declaration=f"{name} {duration:.3f}s {ease} both",
        props="transform-origin:center;",
    )


@register("rise", default_duration=0.45)
def rise(params, name, duration, ease):
    distance = _num(params, "distance", 22.0)
    return MotionCSS(
        keyframes=_keyframes(
            name,
            f"opacity:0; transform: translate3d(0, {distance:g}px, 0)",
            "opacity:1; transform: translate3d(0, 0, 0)",
        ),
        declaration=f"{name} {duration:.3f}s {ease} both",
    )


@register("pop", default_duration=0.4)
def pop(params, name, duration, ease):
    start = _num(params, "from_scale", 0.94)
    return MotionCSS(
        keyframes=_keyframes(
            name,
            f"opacity:0; transform: scale({start:g})",
            "opacity:1; transform: scale(1)",
        ),
        declaration=f"{name} {duration:.3f}s {ease} both",
    )


@register("fade", default_duration=0.45)
def fade(params, name, duration, ease):
    return MotionCSS(
        keyframes=_keyframes(name, "opacity:0", "opacity:1"),
        declaration=f"{name} {duration:.3f}s {ease} both",
    )


@register("stamp", default_duration=0.5)
def stamp(params, name, duration, ease):
    """The brief's Stamp: 120% -> 100% with an ink-bleed blur that settles."""
    start = _num(params, "from_scale", 1.2)
    blur = _num(params, "blur", 10.0)
    return MotionCSS(
        keyframes=_keyframes(
            name,
            f"opacity:0; transform: scale({start:g}); filter: blur({blur:g}px)",
            "opacity:1; transform: scale(1); filter: blur(0)",
        ),
        declaration=f"{name} {duration:.3f}s {ease} both",
    )


@register("flip", default_duration=0.6)
def flip(params, name, duration, ease):
    """A card turning over to reveal what is on its face."""
    return MotionCSS(
        keyframes=_keyframes(
            name,
            "opacity:0; transform: perspective(1200px) rotateY(90deg)",
            "opacity:1; transform: perspective(1200px) rotateY(0deg)",
        ),
        declaration=f"{name} {duration:.3f}s {ease} both",
        props="transform-origin:center;backface-visibility:hidden;",
    )


@register("lock", default_duration=0.55)
def lock(params, name, duration, ease):
    """Two halves that slide in from opposite sides and lock together."""
    side = str(params.get("side", "left")).lower()
    if side not in {"left", "right"}:
        raise MotionError(f"lock side must be 'left' or 'right', got {side!r}")
    distance = _num(params, "distance", 60.0)
    start = -distance if side == "left" else distance
    return MotionCSS(
        keyframes=_keyframes(
            name,
            f"opacity:0; transform: translate3d({start:g}px, 0, 0)",
            "opacity:1; transform: translate3d(0, 0, 0)",
        ),
        declaration=f"{name} {duration:.3f}s {ease} both",
    )


@register("pulse", default_duration=1.6)
def pulse(params, name, duration, ease):
    peak = _num(params, "peak", 1.35)
    return MotionCSS(
        keyframes=_keyframes(
            name, "filter: brightness(1)", f"filter: brightness({peak:g})"
        ),
        declaration=f"{name} {duration:.3f}s ease-in-out infinite alternate",
    )
```

- [ ] **Step 4: Register them on package import**

In `motion/__init__.py`, add after the existing imports:

```python
from video_editor_core.motion import primitives as _primitives  # noqa: F401  (registers)
```

and add `from video_editor_core.motion import primitives` above the `__all__` block so linting sees a real import.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_motion_primitives.py tests/test_motion_spec.py -v`
Expected: PASS (12 + 16 cases).

Where this task's builder code and its tests disagree on number formatting — the
code writes `translate3d(0, 40px, 0)` and `scale(0.94)`, while the tests assert
the compact `translate3d(0,40px,0)` and `scale(.94)` — **the tests govern**:
format through a `_css_num` helper that drops the leading zero and joins
translate arguments without spaces. `drift` must not go through it; its four
move strings are verbatim legacy text whose formatting is part of the parity
obligation.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/video_editor_core/motion tests/test_motion_primitives.py
git commit -m "feat(motion): add drift, stamp, flip, lock and the entry primitives"
```

---

## Task 3: Structural and text primitives

**Files:**
- Create: `packages/core/src/video_editor_core/motion/text.py`
- Modify: `packages/core/src/video_editor_core/motion/__init__.py`
- Test: `tests/test_motion_text.py`

**Interfaces:**
- Consumes: `register`, `MotionError`, `MotionCSS` from Task 1.
- Produces: registered primitives `draw`, `count`, `pill`, `word_highlight`. `count` returns empty `declaration` and carries its own markup and inline animation; the compiler must therefore not add an `animation:` declaration for it.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the draw, count, pill and word-highlight primitives."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from video_editor_core.motion import PRIMITIVES, MotionError, build_motion


def spec(name: str, **params: object) -> SimpleNamespace:
    return SimpleNamespace(name=name, params=params, delay=0.0, duration=None, ease=None)


def test_text_primitives_are_registered():
    for name in ("draw", "count", "pill", "word_highlight"):
        assert name in PRIMITIVES


def test_draw_strokes_a_path_without_measuring_it():
    css = build_motion(spec("draw"), clip_duration=4.0)
    assert "stroke-dashoffset:1" in css.keyframes
    assert "stroke-dashoffset:0" in css.keyframes
    assert "stroke-dasharray:1" in css.props
    assert "0.800s" in css.declaration


def test_count_builds_a_digit_strip_and_animates_it_itself():
    css = build_motion(spec("count", to=3), clip_duration=4.0)
    assert ">0<" in css.markup and ">3<" in css.markup
    assert "ve-count-strip" in css.markup
    assert "translateY(-3em)" in css.keyframes
    # The strip carries its own animation, so the layer must not get one.
    assert css.declaration == ""
    assert "animation:" in css.markup


def test_count_rejects_a_backwards_range():
    with pytest.raises(MotionError):
        build_motion(spec("count", **{"from": 5, "to": 2}), clip_duration=4.0)


def test_pill_pops_up_by_its_distance():
    css = build_motion(spec("pill"), clip_duration=4.0)
    assert "translate3d(0, 16px, 0)" in css.keyframes
    assert "0.340s" in css.declaration


def test_word_highlight_is_a_marker_the_rich_text_emitter_applies():
    css = build_motion(spec("word_highlight"), clip_duration=4.0)
    assert css.keyframes == ""
    assert css.declaration == ""
```

- [ ] **Step 2: Run the tests to watch them fail**

Run: `uv run pytest tests/test_motion_text.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_editor_core.motion.text'`.

- [ ] **Step 3: Write `motion/text.py`**

```python
"""Structural and text motion primitives.

``draw`` works on SVG strokes; ``count`` renders its own markup because CSS
cannot change text content; ``word_highlight`` is a marker — the rich-text
emitter applies it per word, and it exists here so validation accepts it.
"""

from __future__ import annotations

from video_editor_core.motion.primitives import _keyframes, _num
from video_editor_core.motion.registry import MotionError, register
from video_editor_core.motion.spec import MotionCSS


@register("draw", default_duration=0.8)
def draw(params, name, duration, ease):
    """Stroke a path in. The compiler sets ``pathLength="1"`` on each path."""
    return MotionCSS(
        keyframes=_keyframes(name, "stroke-dashoffset:1", "stroke-dashoffset:0"),
        declaration=f"{name} {duration:.3f}s {ease} both",
        props="stroke-dasharray:1;fill:none;",
    )


@register("count", default_duration=1.2)
def count(params, name, duration, ease):
    """A digit column translated inside a one-line window — no JavaScript.

    The animation lives on the strip inside ``markup``; the layer itself gets
    no ``animation`` declaration, which is why ``declaration`` is empty.
    """
    first = int(_num(params, "from", 0))
    last = int(_num(params, "to", 10))
    if last < first:
        raise MotionError(f"count 'to' ({last}) must be >= 'from' ({first})")
    steps = last - first
    declaration = f"{name} {duration:.3f}s {ease} both"
    digits = "".join(
        f'<span style="display:block;height:1em;">{value}</span>'
        for value in range(first, last + 1)
    )
    markup = (
        '<span class="ve-count" style="display:inline-block;overflow:hidden;'
        'height:1em;vertical-align:bottom;">'
        f'<span class="ve-count-strip" style="display:block;animation:{declaration};">'
        f"{digits}</span></span>"
    )
    return MotionCSS(
        keyframes=_keyframes(name, "transform: translateY(0)", f"transform: translateY(-{steps}em)"),
        declaration="",
        markup=markup,
    )


@register("pill", default_duration=0.34)
def pill(params, name, duration, ease):
    distance = _num(params, "distance", 16.0)
    return MotionCSS(
        keyframes=_keyframes(
            name,
            f"opacity:0; transform: translate3d(0, {distance:g}px, 0)",
            "opacity:1; transform: translate3d(0, 0, 0)",
        ),
        declaration=f"{name} {duration:.3f}s {ease} both",
    )


@register("word_highlight")
def word_highlight(params, name, duration, ease):
    """Marker: each word gets its highlight from the rich-text emitter."""
    return MotionCSS()
```

- [ ] **Step 4: Register them on package import**

In `motion/__init__.py` add next to the primitives import:

```python
from video_editor_core.motion import text as _motion_text  # noqa: F401  (registers)
```

Also add `from video_editor_core.motion import text as motion_text` above `__all__`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_motion_text.py tests/test_motion_primitives.py tests/test_motion_spec.py -v`
Expected: PASS (26 tests).

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/video_editor_core/motion tests/test_motion_text.py
git commit -m "feat(motion): add draw, count, pill and word_highlight"
```

---

## Task 4: The `Composition` model

**Files:**
- Create: `packages/core/src/video_editor_core/composition.py`
- Test: `tests/test_composition.py`

**Interfaces:**
- Consumes: `Rect` from `video_editor_core.render_graph`; `PRIMITIVES` from the motion package; `DesignTokens` from `video_editor_core.designs`.
- Produces: `Canvas`, `LayerTime`, `WordSpan`, `MotionSpec`, `LayerContent`, `CompositionLayer`, `CompositionScene`, `AudioCue`, `Composition`; `canvas_for_aspect(aspect_ratio) -> tuple[int, int]`; `quantise(seconds, fps) -> float`. Layer kinds are exactly `media | text | rich_text | shape | svg | html`; the optional `html` kind carries engine-generated markup (the typographic scenes) and its `style["extra_css"]` is appended to the composition stylesheet by the compiler.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the Composition model and its invariants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
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
from video_editor_core.render_graph import Rect


def canvas() -> Canvas:
    return Canvas(width=1080, height=1920, fps=30)


def text_layer(layer_id: str = "title", **overrides: object) -> CompositionLayer:
    base: dict[str, object] = {
        "id": layer_id,
        "kind": "text",
        "rect": Rect(x=0, y=200, width=1080, height=300),
        "time": LayerTime(start=0.0, end=4.0),
        "content": LayerContent(text="Hello"),
    }
    base.update(overrides)
    return CompositionLayer(**base)  # type: ignore[arg-type]


def composition(**overrides: object) -> Composition:
    base: dict[str, object] = {
        "job_id": "job",
        "canvas": canvas(),
        "duration": 4.0,
        "scenes": [
            CompositionScene(id="s0", start=0.0, end=4.0, layers=[text_layer()])
        ],
    }
    base.update(overrides)
    return Composition(**base)  # type: ignore[arg-type]


def test_a_minimal_composition_validates():
    comp = composition()
    assert comp.scenes[0].layers[0].content.text == "Hello"
    assert comp.audio_mode == "embed"


def test_scenes_must_be_contiguous():
    with pytest.raises(ValidationError, match="expected"):
        composition(
            scenes=[
                CompositionScene(id="s0", start=0.0, end=2.0, layers=[text_layer()]),
                CompositionScene(id="s1", start=2.5, end=4.0),
            ]
        )


def test_scenes_must_cover_the_declared_duration():
    with pytest.raises(ValidationError, match="duration"):
        composition(duration=6.0)


def test_duplicate_layer_ids_are_rejected():
    with pytest.raises(ValidationError, match="duplicate layer id"):
        composition(
            scenes=[
                CompositionScene(
                    id="s0", start=0.0, end=4.0, layers=[text_layer("same"), text_layer("same")]
                )
            ]
        )


def test_unknown_motion_names_are_rejected():
    with pytest.raises(ValidationError, match="unknown motion"):
        composition(
            scenes=[
                CompositionScene(
                    id="s0",
                    start=0.0,
                    end=4.0,
                    layers=[text_layer(motions=[MotionSpec(name="teleport")])],
                )
            ]
        )


def test_known_motion_names_are_accepted():
    comp = composition(
        scenes=[
            CompositionScene(
                id="s0",
                start=0.0,
                end=4.0,
                layers=[text_layer(motions=[MotionSpec(name="stamp", delay=0.2)])],
            )
        ]
    )
    assert comp.scenes[0].layers[0].motions[0].name == "stamp"


def test_a_layer_may_not_end_before_it_starts():
    with pytest.raises(ValidationError):
        LayerTime(start=2.0, end=1.0)


def test_a_layer_may_not_sit_outside_the_canvas():
    with pytest.raises(ValidationError, match="canvas"):
        composition(
            scenes=[
                CompositionScene(
                    id="s0",
                    start=0.0,
                    end=4.0,
                    layers=[
                        text_layer(rect=Rect(x=900, y=200, width=400, height=300))
                    ],
                )
            ]
        )


def test_a_media_layer_needs_a_source_and_a_kind():
    with pytest.raises(ValidationError):
        CompositionLayer(
            id="still",
            kind="media",
            rect=Rect(x=0, y=0, width=1080, height=1920),
            time=LayerTime(start=0.0, end=1.0),
            content=LayerContent(source="still.png"),
        )


def test_audio_cues_carry_id_track_and_volume():
    cue = AudioCue(id="narration", source="narration.mp3", track=150, volume=0.5)
    assert cue.start == 0.0 and cue.duration is None


@pytest.mark.parametrize(
    ("aspect", "expected"),
    [("9:16", (1080, 1920)), ("16:9", (1920, 1080)), ("1:1", (1080, 1080)), (None, (1920, 1080))],
)
def test_canvas_for_aspect_matches_the_scene_plan_mapping(aspect, expected):
    assert canvas_for_aspect(aspect) == expected


def test_quantise_snaps_to_the_frame_grid():
    assert quantise(2.51, 30) == pytest.approx(2.5)
    assert quantise(0.02, 30) == pytest.approx(0.033333, abs=1e-5)
```

- [ ] **Step 2: Run the tests to watch them fail**

Run: `uv run pytest tests/test_composition.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_editor_core.composition'`.

- [ ] **Step 3: Write `composition.py`**

```python
"""The single intermediate representation behind every HyperFrames render.

A :class:`Composition` is what an adapter produces and what
:func:`video_editor_core.compile_composition.compile_composition` consumes.
Times are absolute timeline seconds; rects are canvas pixels, never
scene-relative. Scenes are a compile-time grouping only — every visual layer
is emitted as a root-level clip — so a later modular emitter can change the
output without changing this model.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from video_editor_core.designs import DesignTokens
from video_editor_core.motion import PRIMITIVES
from video_editor_core.render_graph import Rect

LayerKind = Literal["media", "text", "rich_text", "shape", "svg", "html"]

_ASPECT_CANVAS: dict[str, tuple[int, int]] = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "4:3": (1440, 1080),
}


def canvas_for_aspect(aspect_ratio: str | None) -> tuple[int, int]:
    """Canvas pixels for an aspect ratio; unknown ratios fall back to 16:9."""
    return _ASPECT_CANVAS.get((aspect_ratio or "16:9").strip(), (1920, 1080))


def quantise(seconds: float, fps: int) -> float:
    """Snap a time to the frame grid so CSS time and encoded frames agree."""
    return round(float(seconds) * fps) / fps


class Canvas(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: int = Field(gt=0)


class LayerTime(BaseModel):
    start: float = Field(ge=0)
    end: float | None = None

    @model_validator(mode="after")
    def _window(self) -> LayerTime:
        if self.end is not None and self.end <= self.start:
            raise ValueError(
                f"layer time ends at {self.end} but starts at {self.start}"
            )
        return self


class WordSpan(BaseModel):
    """One caption word, timed relative to its clip's ``data-start``."""

    text: str
    start: float = 0.0
    end: float = 0.0
    highlight: bool = False


class MotionSpec(BaseModel):
    name: str
    params: dict[str, float | str] = Field(default_factory=dict)
    delay: float = 0.0
    duration: float | None = None
    ease: str = "cubic-bezier(.2,.8,.2,1)"


class LayerContent(BaseModel):
    """Exactly one of these is meaningful per layer kind."""

    text: str = ""                 # text | html | placeholder
    spans: list[WordSpan] = Field(default_factory=list)  # rich_text
    source: str = ""               # media
    gradient: str = ""             # shape / scrim
    path: str = ""                 # svg
    view_box: str = "0 0 100 100"  # svg
    alt: str = ""                  # media accessibility text


class CompositionLayer(BaseModel):
    id: str
    kind: LayerKind
    preset: str = ""  # chip | pill | scrim | end_card | caption_line | spotlight | placeholder | base
    rect: Rect
    time: LayerTime
    content: LayerContent = Field(default_factory=LayerContent)
    style: dict[str, Any] = Field(default_factory=dict)
    motions: list[MotionSpec] = Field(default_factory=list)
    media_kind: Literal["image", "video"] | None = None
    track: int = 20
    z: int = 0

    @model_validator(mode="after")
    def _media_is_complete(self) -> CompositionLayer:
        if self.kind == "media":
            if not self.content.source:
                raise ValueError(f"media layer {self.id!r} has no source")
            if self.media_kind is None:
                raise ValueError(f"media layer {self.id!r} has no media_kind")
        return self


class CompositionScene(BaseModel):
    id: str
    start: float
    end: float
    layers: list[CompositionLayer] = Field(default_factory=list)
    transition_in: MotionSpec | None = None

    @model_validator(mode="after")
    def _window(self) -> CompositionScene:
        if self.end <= self.start:
            raise ValueError(
                f"scene {self.id!r} ends at {self.end} but starts at {self.start}"
            )
        return self


class AudioCue(BaseModel):
    id: str
    source: str
    start: float = 0.0
    duration: float | None = None
    track: int = 150
    volume: float = 1.0


class Composition(BaseModel):
    job_id: str
    canvas: Canvas
    duration: float = Field(gt=0)
    scenes: list[CompositionScene] = Field(default_factory=list)
    audio: list[AudioCue] = Field(default_factory=list)
    audio_mode: Literal["embed", "external"] = "embed"
    design: DesignTokens | None = None

    @model_validator(mode="after")
    def _scenes_are_contiguous(self) -> Composition:
        cursor = 0.0
        for scene in self.scenes:
            if abs(scene.start - cursor) > 1e-6:
                raise ValueError(
                    f"scene {scene.id!r} starts at {scene.start}, expected {cursor}"
                )
            cursor = scene.end
        if abs(cursor - self.duration) > 1e-6:
            raise ValueError(
                f"scenes cover {cursor:.3f}s but the composition duration is "
                f"{self.duration:.3f}s"
            )
        return self

    @model_validator(mode="after")
    def _layers_fit(self) -> Composition:
        seen: set[str] = set()
        for scene in self.scenes:
            for layer in scene.layers:
                if layer.id in seen:
                    raise ValueError(f"duplicate layer id {layer.id!r}")
                seen.add(layer.id)
                end = layer.time.end
                if end is not None and end > self.duration + 1e-6:
                    raise ValueError(
                        f"layer {layer.id!r} ends at {end} past the composition "
                        f"duration {self.duration}"
                    )
                rect = layer.rect
                if (
                    rect.x < 0
                    or rect.y < 0
                    or rect.width <= 0
                    or rect.height <= 0
                    or rect.x + rect.width > self.canvas.width
                    or rect.y + rect.height > self.canvas.height
                ):
                    raise ValueError(
                        f"layer {layer.id!r} rect "
                        f"({rect.x},{rect.y},{rect.width},{rect.height}) is outside "
                        f"the {self.canvas.width}x{self.canvas.height} canvas"
                    )
        return self

    @model_validator(mode="after")
    def _motions_exist(self) -> Composition:
        for scene in self.scenes:
            for layer in scene.layers:
                for motion in layer.motions:
                    if motion.name not in PRIMITIVES:
                        raise ValueError(
                            f"unknown motion {motion.name!r} on layer {layer.id!r}; "
                            f"known: {', '.join(sorted(PRIMITIVES))}"
                        )
        return self
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_composition.py -v`
Expected: PASS (15 cases as first written; 25 with the post-review
amendments' tests).

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/video_editor_core/composition.py tests/test_composition.py
git commit -m "feat(core): add the Composition intermediate representation"
```

**Post-review amendments (Task 4 review):**

- `canvas_for_aspect` and `_ASPECT_CANVAS` get **one home** in `composition.py`.
  Replace the copy at `scene_plan_composition.py:37-58` with a re-export:

  ```python
  from video_editor_core.composition import _ASPECT_CANVAS as _ASPECT_CANVAS
  from video_editor_core.composition import canvas_for_aspect as canvas_for_aspect
  ```

  The redundant `X as X` aliases are required by mypy strict's
  `no_implicit_reexport`, which also rejects the plain form in the two
  existing importers (`explainer_qa.py`, `explainer_pipeline.py`). The
  re-export keeps those importers working and leaves the two copies with no
  way to diverge.
- `CompositionLayer.id` and `CompositionScene.id` are
  `Field(pattern=r"^[A-Za-z0-9_-]+$")`. Both reach HTML `id=`/`data-*`
  attributes, and the global constraint requires the pattern.
- The audio-cue test asserts the fields it names: `cue.id == "narration"`,
  `cue.track == 150`, `cue.volume == 0.5`, `cue.source == "narration.mp3"`.
  Pydantic ignores unknown kwargs, so without them deleting a field leaves the
  test green.
- Error messages use `:.3f` for every time, matching the project's three-decimal
  rule (a bare float prints `0.30000000000000004`).
- `MotionSpec.delay` is `Field(ge=0)` and `MotionSpec.duration` is
  `Field(default=None, gt=0)`, and `MotionSpec.ease` defaults to
  `DEFAULT_EASE` imported from `motion/spec.py` rather than repeating the
  literal, so the two cannot drift.
- An open-ended layer time still bounds `start` by the composition duration.

---

## Task 5: Extract `AssetCopier`

**Files:**
- Create: `packages/core/src/video_editor_core/project_assets.py`
- Modify: `packages/core/src/video_editor_core/hyperframes_project.py:27-107`
- Test: `tests/test_project_assets.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `AssetCopier(assets_dir)` with `.copy(source: str) -> str` returning a project-relative `assets/<name>` path. `hyperframes_project._AssetCopier` stays as an alias so `scene_plan_composition` keeps importing.

This move is required: once `hyperframes_project` delegates to the compiler, the compiler cannot import from it without a cycle.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the project asset copier."""

from __future__ import annotations

from pathlib import Path

from video_editor_core.project_assets import AssetCopier


def test_the_same_source_is_copied_once(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    # ``AssetCopier`` writes into an existing directory; every real caller
    # creates it first (``hyperframes_project``, ``scene_plan_composition`` and
    # the Task 6 compiler all ``mkdir(parents=True, exist_ok=True)``).
    (tmp_path / "assets").mkdir()
    copier = AssetCopier(tmp_path / "assets")
    assert copier.copy(str(source)) == "assets/clip.mp4"
    assert copier.copy(str(source)) == "assets/clip.mp4"
    assert len(list((tmp_path / "assets").iterdir())) == 1


def test_two_sources_sharing_a_basename_stay_distinct(tmp_path: Path) -> None:
    first = tmp_path / "a" / "still.png"
    second = tmp_path / "b" / "still.png"
    for path in (first, second):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
    (tmp_path / "assets").mkdir()
    copier = AssetCopier(tmp_path / "assets")
    names = [copier.copy(str(first)), copier.copy(str(second))]
    assert names[0] == "assets/still.png"
    assert names[1] != names[0]
    assert (tmp_path / "assets" / "still.png").is_file()
    assert (tmp_path / names[1]).is_file()
```

- [ ] **Step 2: Run it to watch it fail**

Run: `uv run pytest tests/test_project_assets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_editor_core.project_assets'`.

- [ ] **Step 3: Create `project_assets.py` with the class moved verbatim**

```python
"""Copy media into a HyperFrames project's ``assets/`` directory."""

from __future__ import annotations

import shutil
from pathlib import Path


class AssetCopier:
    """Copy media into the project's assets dir, de-duplicating by source."""

    def __init__(self, assets_dir: Path) -> None:
        self._assets_dir = assets_dir
        self._copied: dict[str, str] = {}
        self._used_names: set[str] = set()

    def copy(self, source: str) -> str:
        """Copy ``source`` into assets and return its relative path."""
        src = Path(source)
        key = str(src.resolve()) if src.exists() else source
        if key in self._copied:
            return self._copied[key]

        name = src.name or "asset.bin"
        if name in self._used_names:
            stem, suffix = src.stem or "asset", src.suffix
            name = f"{stem}_{len(self._used_names)}{suffix}"
        self._used_names.add(name)
        dest = self._assets_dir / name
        if src.exists():
            shutil.copyfile(src, dest)
        rel = f"assets/{name}"
        self._copied[key] = rel
        return rel
```

- [ ] **Step 4: Point `hyperframes_project.py` at it**

Delete the `_AssetCopier` class (lines 82-107) and replace it with:

```python
from video_editor_core.project_assets import AssetCopier

#: Kept for callers that imported the private name (``scene_plan_composition``).
_AssetCopier = AssetCopier
```

The `import shutil` at the top of `hyperframes_project.py` becomes unused — remove it.

- [ ] **Step 5: Run the tests and the suite**

Run: `uv run pytest tests/test_project_assets.py -v && uv run pytest -q`
Expected: PASS, whole suite green.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/video_editor_core/project_assets.py \
        packages/core/src/video_editor_core/hyperframes_project.py \
        tests/test_project_assets.py
git commit -m "refactor(core): move AssetCopier into its own module"
```

---

## Task 6: The one compiler

**Files:**
- Create: `packages/core/src/video_editor_core/compile_composition.py`
- Test: `tests/test_compile_composition.py`

**Interfaces:**
- Consumes: `Composition`, `CompositionLayer`, `MotionCSS`, `build_motion`, `AssetCopier`, `font_face_css`, `font_stack`.
- Produces: `compile_composition(composition, project_dir) -> Path` (the `index.html` path); `KeyframeBook` with `.add(css)` / `.css()`; `COMPOSITION_ID = "main"`.

The emitted HTML must reproduce today's contract exactly — the karaoke span strings, `--ve-hl`, `#base_video`, overlay tracks and `data-no-timeline` are pinned by `tests/test_hyperframes_project.py`.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the Composition -> HyperFrames project compiler."""

from __future__ import annotations

from pathlib import Path

from video_editor_core.compile_composition import compile_composition
from video_editor_core.composition import (
    AudioCue,
    Canvas,
    Composition,
    CompositionLayer,
    CompositionScene,
    LayerContent,
    LayerTime,
    MotionSpec,
    WordSpan,
)
from video_editor_core.render_graph import Rect


def _composition(tmp_path: Path, layers: list[CompositionLayer], **overrides) -> Composition:
    still = tmp_path / "still.png"
    still.write_bytes(b"png")
    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"mp3")
    base = CompositionLayer(
        id="base_video",
        kind="media",
        preset="base",
        media_kind="image",
        rect=Rect(x=0, y=0, width=1080, height=1920),
        time=LayerTime(start=0.0, end=4.0),
        content=LayerContent(source=str(still)),
        track=0,
        z=0,
    )
    data = {
        "job_id": "job-1",
        "canvas": Canvas(width=1080, height=1920, fps=30),
        "duration": 4.0,
        "scenes": [
            CompositionScene(id="s0", start=0.0, end=4.0, layers=[base, *layers])
        ],
        "audio": [],
    }
    data.update(overrides)
    return Composition(**data)  # type: ignore[arg-type]


def _text(layer_id: str = "title", **overrides) -> CompositionLayer:
    data = {
        "id": layer_id,
        "kind": "text",
        "rect": Rect(x=80, y=300, width=920, height=300),
        "time": LayerTime(start=0.0, end=4.0),
        "content": LayerContent(text="四柱"),
        "style": {"font": "Montserrat", "font_size": 72, "font_color": "#FFFFFF"},
    }
    data.update(overrides)
    return CompositionLayer(**data)  # type: ignore[arg-type]


def test_root_carries_the_composition_contract(tmp_path):
    index = compile_composition(_composition(tmp_path, [_text()]), tmp_path / "proj")
    html = index.read_text(encoding="utf-8")
    assert 'data-composition-id="main"' in html
    assert "data-no-timeline" in html
    assert 'data-width="1080"' in html and 'data-height="1920"' in html
    assert 'data-duration="4.000"' in html
    assert 'id="base_video"' in html
    assert "assets/still.png" in html


def test_every_visual_layer_is_a_root_level_clip(tmp_path):
    # ``z`` defaults to 0 and the base layer is 0 too, so a compiler that
    # hard-coded the index would still satisfy a bare "z-index:1" scan. Give
    # this layer a distinct z so the assertion can only pass when the emitted
    # value comes from the model.
    index = compile_composition(
        _composition(tmp_path, [_text(z=1)]), tmp_path / "proj"
    )
    html = index.read_text(encoding="utf-8")
    assert 'id="title" class="clip" data-start="0.000" data-duration="4.000"' in html
    assert "z-index:1;" in html


def test_text_layers_get_their_bundled_font_embedded(tmp_path):
    index = compile_composition(
        _composition(tmp_path, [_text()]), tmp_path / "proj"
    )
    html = index.read_text(encoding="utf-8")
    assert "@font-face" in html
    assert "font-family:'Montserrat'," in html
    assert (tmp_path / "proj" / "assets" / "fonts" / "Montserrat.ttf").is_file()


def test_identical_motions_share_one_keyframes_block(tmp_path):
    motion = {"name": "stamp", "params": {}}
    layers = [
        _text("first", motions=[MotionSpec(**motion)]),
        _text("second", rect=Rect(x=80, y=700, width=920, height=300), motions=[MotionSpec(**motion)]),
    ]
    html = compile_composition(_composition(tmp_path, layers), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    assert html.count("@keyframes ve_stamp_") == 1


def test_motion_delay_reaches_the_layer_animation_shorthand(tmp_path):
    layer = _text(motions=[MotionSpec(name="rise", delay=0.25)])
    html = compile_composition(_composition(tmp_path, [layer]), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    # Pin the layer's own ``animation`` value: a bare " 0.250s" anywhere in the
    # document could match an unrelated time.
    line = next(line for line in html.splitlines() if 'id="title"' in line)
    assert "animation:" in line
    assert line.split("animation:", 1)[1].split(";", 1)[0].endswith(" 0.250s")


def test_motion_is_clamped_to_the_layer_clip_not_the_composition(tmp_path):
    # The layer runs 1.0 s inside a 4.0 s composition. With a 0.9 s delay only
    # 0.1 s is left, so a clamp derived from the composition duration would
    # emit 3.000s and let the animation run past its own clip end.
    layer = _text(
        time=LayerTime(start=0.0, end=1.0),
        motions=[MotionSpec(name="rise", delay=0.9, duration=3.0)],
    )
    html = compile_composition(_composition(tmp_path, [layer]), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    line = next(line for line in html.splitlines() if 'id="title"' in line)
    animation = line.split("animation:", 1)[1].split(";", 1)[0]
    assert "0.100s" in animation
    assert animation.endswith(" 0.900s")


def test_a_count_primitive_animates_its_own_strip(tmp_path):
    layer = _text(content=LayerContent(text=""), motions=[MotionSpec(name="count", params={"to": 2})])
    html = compile_composition(_composition(tmp_path, [layer]), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    assert "ve-count-strip" in html
    assert "animation:" in html.split("ve-count-strip", 1)[1][:200]


def test_rich_text_emits_karaoke_spans(tmp_path):
    spans = [WordSpan(text="hello", start=0.4, end=0.8)]
    layer = _text(
        kind="rich_text",
        content=LayerContent(spans=spans),
        style={
            "font": "Montserrat",
            "font_size": 48,
            "highlight_color": "#FDB813",
        },
    )
    html = compile_composition(_composition(tmp_path, [layer]), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    assert "ve_word_hl 0.400s step-end 0.400s" in html
    assert "--ve-hl:#FDB813" in html
    assert html.count('class="ve-word"') == 1


def test_audio_cues_become_root_level_audio(tmp_path):
    cue = AudioCue(id="narration", source=str(tmp_path / "narration.mp3"), track=150)
    comp = _composition(tmp_path, [_text()])
    comp = comp.model_copy(update={"audio": [cue]})
    html = compile_composition(comp, tmp_path / "proj").read_text(encoding="utf-8")
    assert html.count('<audio id="narration"') == 1
    assert 'data-duration="4.000"' in html
    assert "assets/narration.mp3" in html


def test_extra_css_from_an_html_layer_reaches_the_stylesheet(tmp_path):
    layer = CompositionLayer(
        id="scene_0_typo",
        kind="html",
        rect=Rect(x=0, y=0, width=1080, height=1920),
        time=LayerTime(start=0.0, end=4.0),
        content=LayerContent(text="<div class='typo-0'>$32B</div>"),
        style={"background": "#EFEAE3", "extra_css": ".typo-0 { font-size: 120px; }"},
    )
    html = compile_composition(_composition(tmp_path, [layer]), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    assert ".typo-0 { font-size: 120px; }" in html
    assert "<div class='typo-0'>$32B</div>" in html
    assert "background:#EFEAE3" in html  # backgrounds come from style["background"]
```

- [ ] **Step 2: Run the tests to watch them fail**

Run: `uv run pytest tests/test_compile_composition.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_editor_core.compile_composition'`.

Note on the last two assertions: an `html` layer's markup is emitted verbatim
(it is engine-generated), while its background comes from `style["background"]`
and its rules from `style["extra_css"]`.

- [ ] **Step 3: Write `compile_composition.py`**

```python
"""Compile a :class:`Composition` into a HyperFrames HTML project.

One compiler for every input. The output contract:

* one root ``div[data-composition-id=main][data-no-timeline]`` carrying
  canvas, duration and fps;
* every visual layer is a root-level ``class="clip"`` element with
  ``data-start``, ``data-duration``, ``data-track-index`` and an explicit
  ``z-index`` — HyperFrames does not seek nested media;
* ``@keyframes`` are emitted once per distinct primitive instantiation.

Media must be root children; scenes are a compile-time grouping only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from video_editor_core.composition import Composition, CompositionLayer
from video_editor_core.fonts import font_face_css, font_stack
from video_editor_core.motion import MotionCSS, build_motion
from video_editor_core.project_assets import AssetCopier

logger = structlog.get_logger(__name__)

COMPOSITION_ID = "main"


def compile_composition(composition: Composition, project_dir: Path | str) -> Path:
    """Write a HyperFrames project for ``composition``; return the index path."""
    project_dir = Path(project_dir)
    assets_dir = project_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    copier = AssetCopier(assets_dir)

    book = KeyframeBook()
    stylesheet: list[str] = []
    elements = [
        _layer_html(composition, layer, copier, book, stylesheet)
        for scene in composition.scenes
        for layer in scene.layers
    ]
    elements.extend(_audio_html(composition, copier))

    index_path = project_dir / "index.html"
    index_path.write_text(
        _render_html(
            composition,
            "\n".join(elements),
            book.css(),
            "\n".join(part for part in stylesheet if part),
            font_face_css(_font_families(composition), assets_dir / "fonts"),
        ),
        encoding="utf-8",
    )
    logger.info(
        "composition_project_written",
        job_id=composition.job_id,
        project=str(project_dir),
        layers=len(elements),
    )
    return index_path


class KeyframeBook:
    """Collect ``@keyframes`` blocks, one per distinct primitive instantiation."""

    def __init__(self) -> None:
        self._blocks: dict[str, None] = {}

    def add(self, css: MotionCSS) -> None:
        if css.keyframes:
            self._blocks.setdefault(css.keyframes, None)

    def css(self, indent: str = "      ") -> str:
        return "\n".join(f"{indent}{block}" for block in self._blocks)


def _layer_html(
    composition: Composition,
    layer: CompositionLayer,
    copier: AssetCopier,
    book: KeyframeBook,
    stylesheet: list[str],
) -> str:
    duration = _layer_duration(composition, layer)
    css: list[str] = [_rect_css(layer), f"z-index:{layer.z};"]
    declarations: list[str] = []
    markup = ""

    if layer.kind in {"text", "rich_text"}:
        css.append(_text_css(layer))
    if layer.kind in {"text", "rich_text", "html"}:
        # ``html`` is engine-generated markup that carries its own stylesheet;
        # caption CSS on its wrapper would inherit a 4 px text stroke and a
        # drop shadow into every glyph (the legacy writers emitted neither).
        background = layer.style.get("background")
        if background:
            css.append(f"background:{_attr(background)};")
    elif layer.kind == "shape":
        css.append(_shape_css(layer, duration, declarations))
    elif layer.kind == "media":
        css.append(f"object-fit:{_object_fit(layer)};")

    if layer.preset == "placeholder":
        css.append(_placeholder_css(composition))

    for motion in layer.motions:
        built = build_motion(motion, clip_duration=duration, tempo=_tempo(composition))
        book.add(built)
        if built.props:
            css.append(built.props)
        if built.declaration:
            declarations.append(built.declaration)
        markup += built.markup

    extra_css = str(layer.style.get("extra_css") or "")
    if extra_css:
        stylesheet.append(extra_css)

    if declarations:
        css.append("animation:" + ", ".join(declarations) + ";")

    style = _style_attr("".join(css))
    common = (
        f'class="clip" data-start="{layer.time.start:.3f}" '
        f'data-duration="{duration:.3f}" data-track-index="{layer.track}"'
    )

    if layer.kind == "media":
        rel = copier.copy(layer.content.source)
        if layer.media_kind == "video":
            attributes = " muted playsinline"
            if layer.style.get("loop"):
                attributes += " loop"
            return (
                f'  <video id="{_attr(layer.id)}" {common} src="{_attr(rel)}" '
                f'style="{style}"{attributes}></video>'
            )
        return (
            f'  <img id="{_attr(layer.id)}" {common} src="{_attr(rel)}" '
            f'style="{style}" alt="{_attr(layer.content.alt)}" />'
        )

    if layer.kind == "svg":
        return (
            f'  <div id="{_attr(layer.id)}" {common} style="{style}">'
            f'<svg viewBox="{_attr(layer.content.view_box)}" width="100%" height="100%">'
            f"{_svg_markup(layer)}</svg></div>"
        )

    if layer.kind == "rich_text":
        body = _word_spans(layer)
    elif layer.kind == "html":
        body = (
            html_escape(layer.content.text)
            if layer.preset == "placeholder"
            else layer.content.text
        )
    else:
        body = html_escape(layer.content.text)
    return f'  <div id="{_attr(layer.id)}" {common} style="{style}">{body}{markup}</div>'
```

Add the import `from html import escape as html_escape` at the top.

- [ ] **Step 4: Add the remaining helpers to the same module**

```python
def _rect_css(layer: CompositionLayer) -> str:
    rect = layer.rect
    return (
        f"position:absolute;left:{rect.x}px;top:{rect.y}px;"
        f"width:{rect.width}px;height:{rect.height}px;"
    )


def _text_css(layer: CompositionLayer) -> str:
    style = layer.style
    font_size = _style_int(style, "font_size", 48)
    weight = _style_int(style, "font_weight", 700)
    color = _attr(style.get("font_color", "#FFFFFF"))
    family = font_stack(str(style.get("font") or "") or None)
    css = (
        "display:flex;align-items:center;justify-content:center;text-align:center;"
        f"font-family:{family};font-weight:{weight};font-size:{font_size}px;color:{color};"
    )
    if style.get("text_effect") == "shadow":
        # A soft drop shadow keeps glyph shapes intact; a centred stroke eats
        # into thin letters at caption sizes.
        css += "text-shadow:0 4px 18px rgba(0,0,0,0.9),0 2px 3px rgba(0,0,0,0.9);"
    else:
        border = _style_int(style, "border_width", 4)
        border_color = _attr(style.get("border_color", "#000000"))
        css += (
            f"-webkit-text-stroke:{border}px {border_color};"
            f"text-shadow:0 {border}px {border * 2}px rgba(0,0,0,0.75);"
        )
    if layer.kind == "rich_text":
        css += f"--ve-hl:{_attr(style.get('highlight_color', '#FDB813'))};"
    return css


def _shape_css(
    layer: CompositionLayer, duration: float, declarations: list[str]
) -> str:
    style = layer.style
    color = _attr(style.get("color", "#FDB813"))
    stroke = _style_int(style, "stroke_width", 5)
    radius = "50%" if style.get("shape") == "circle" else f"{_style_int(style, 'radius', 18)}px"
    fill = max(0.0, min(1.0, _style_float(style, "fill_opacity", 0.0)))

    if layer.preset == "scrim" and layer.content.gradient:
        return f"background:{_attr(layer.content.gradient)};"

    css = (
        f"box-sizing:border-box;border:{stroke}px solid {color};border-radius:{radius};"
        f"background:color-mix(in srgb, {color} {fill * 100:.0f}%, transparent);"
    )
    if layer.preset == "spotlight":
        fade_at = max(0.0, duration - 0.3)
        css += (
            f"box-shadow:0 0 28px {color}, "
            f"inset 0 0 18px color-mix(in srgb, {color} 45%, transparent);"
        )
        declarations.extend(
            [
                "ve_spot_in 0.35s cubic-bezier(0.2, 0.8, 0.2, 1) 0s both",
                "ve_spot_breathe 1.6s ease-in-out 0.35s infinite alternate",
                f"ve_spot_out 0.3s ease-in {fade_at:.3f}s forwards",
            ]
        )
    return css


def _word_spans(layer: CompositionLayer) -> str:
    """Per-word spans that take the highlight colour while each word is spoken.

    Three animations per word: a colour hold while spoken (``ve_word_hl``), a
    small scale pop at onset (``ve_word_pop``), and an opacity step from
    ``upcoming_opacity`` to full once reached (``ve_word_seen``).
    """
    upcoming = _style_float(layer.style, "upcoming_opacity", 1.0)
    spans: list[str] = []
    for word in layer.content.spans:
        duration = max(0.001, word.end - word.start)
        animations = [
            f"ve_word_hl {duration:.3f}s step-end {word.start:.3f}s none",
            f"ve_word_pop {min(0.22, max(0.12, duration)):.3f}s ease-out "
            f"{word.start:.3f}s none",
        ]
        css = "animation:" + ", ".join(animations)
        if upcoming < 1.0:
            css = (
                f"opacity:{upcoming:.2f};animation:"
                + ", ".join(
                    [*animations, f"ve_word_seen 0.001s linear {word.start:.3f}s forwards"]
                )
            )
        spans.append(
            f'<span class="ve-word" style="{css}">{html_escape(word.text)}</span>'
        )
    # One inline wrapper: the caption box is a flex container, and bare spans
    # would each become a flex item, collapsing the spaces between words.
    return '<span class="ve-line">' + " ".join(spans) + "</span>"


def _svg_markup(layer: CompositionLayer) -> str:
    # ``draw`` strokes a path without measuring it, which needs pathLength="1".
    # Its ``stroke-dasharray``/``stroke-dashoffset``/``fill`` props stay on the
    # wrapper ``div`` above: all three are inherited SVG presentation
    # properties, so setting them on the ancestor reaches this path (including
    # while animated) without duplicating them here.
    path_length = ' pathLength="1"' if any(m.name == "draw" for m in layer.motions) else ""
    return f'<path d="{_attr(layer.content.path)}"{path_length} />'


def _placeholder_css(composition: Composition) -> str:
    return (
        "display:flex;align-items:center;justify-content:center;text-align:center;"
        "padding:80px;box-sizing:border-box;color:#f8fafc;background:#0f172a;"
        "font-family:Inter,system-ui,sans-serif;font-weight:700;"
        f"font-size:{max(36, composition.canvas.width // 34)}px;"
    )


def _audio_html(composition: Composition, copier: AssetCopier) -> list[str]:
    out: list[str] = []
    for cue in composition.audio:
        rel = copier.copy(cue.source)
        duration = composition.duration if cue.duration is None else cue.duration
        volume = "" if abs(cue.volume - 1.0) < 1e-9 else f' data-volume="{cue.volume:.2f}"'
        out.append(
            f'    <audio id="{_attr(cue.id)}" src="{_attr(rel)}" '
            f'data-start="{cue.start:.3f}" data-duration="{duration:.3f}" '
            f'data-track-index="{cue.track}"{volume}></audio>'
        )
    return out


def _font_families(composition: Composition) -> list[str]:
    families: list[str] = []
    if composition.design is not None:
        families.extend([composition.design.type.display, composition.design.type.body])
    for scene in composition.scenes:
        for layer in scene.layers:
            if layer.kind in {"text", "rich_text"}:
                families.append(str(layer.style.get("font") or "Inter"))
    return families


def _tempo(composition: Composition) -> float:
    return composition.design.motion.tempo if composition.design else 1.0


def _layer_duration(composition: Composition, layer: CompositionLayer) -> float:
    end = composition.duration if layer.time.end is None else layer.time.end
    return max(0.001, end - layer.time.start)


def _object_fit(layer: CompositionLayer) -> str:
    fallback = "cover" if layer.media_kind == "video" else "contain"
    return str(layer.style.get("object_fit") or fallback)


def _attr(value: Any) -> str:
    return html_escape(str(value), quote=True)


def _style_attr(css: str) -> str:
    """Inline styles sit in double quotes, so a stray ``"`` would end them."""
    return css.replace('"', "&quot;")


def _style_int(style: dict[str, Any], key: str, default: int) -> int:
    value = style.get(key, default)
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _style_float(style: dict[str, Any], key: str, default: float) -> float:
    value = style.get(key, default)
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default
```

- [ ] **Step 5: Add `_render_html` to the module**

```python
def _render_html(
    composition: Composition,
    elements: str,
    keyframes_css: str,
    stylesheet_css: str,
    fonts_css: str,
) -> str:
    canvas = composition.canvas
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={canvas.width}, height={canvas.height}" />
    <title>{html_escape(composition.job_id)}</title>
    <style>
{fonts_css}
      html, body {{ margin: 0; padding: 0; background: #000; }}
      #root {{ position: relative; width: {canvas.width}px; height: {canvas.height}px;
        overflow: hidden; background: #000; }}
      .clip {{ position: absolute; }}
      video.clip, img.clip {{ object-fit: cover; }}
      .ve-line {{ display: inline-block; animation: ve_line_in 0.22s cubic-bezier(0.2, 0.8, 0.2, 1) 0s both; }}
      .ve-word {{ white-space: pre; display: inline-block; }}
      @keyframes ve_word_hl {{ from, to {{ color: var(--ve-hl); }} }}
      @keyframes ve_word_pop {{ 0% {{ transform: scale(1); }} 45% {{ transform: scale(1.12); }} 100% {{ transform: scale(1); }} }}
      @keyframes ve_word_seen {{ to {{ opacity: 1; }} }}
      @keyframes ve_line_in {{ from {{ opacity: 0; transform: translateY(14px) scale(0.96); }} to {{ opacity: 1; transform: none; }} }}
      @keyframes ve_spot_in {{ from {{ opacity: 0; transform: scale(1.12); }} to {{ opacity: 1; transform: scale(1); }} }}
      @keyframes ve_spot_breathe {{ from {{ filter: brightness(1); }} to {{ filter: brightness(1.35); }} }}
      @keyframes ve_spot_out {{ to {{ opacity: 0; }} }}
      #base_video {{ position: absolute; inset: 0; object-fit: cover;
        width: {canvas.width}px; height: {canvas.height}px; }}
{keyframes_css}
{stylesheet_css}
    </style>
  </head>
  <body>
    <div
      id="root"
      data-composition-id="{COMPOSITION_ID}"
      data-no-timeline
      data-start="0"
      data-width="{canvas.width}"
      data-height="{canvas.height}"
      data-duration="{composition.duration:.3f}"
      data-fps="{canvas.fps}"
    >
{elements}
    </div>
  </body>
</html>
"""
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_compile_composition.py -v`
Expected: PASS (12 tests: the ten written in Step 1 plus the two the
post-review amendments add). A failure on `background:#EFEAE3` means the
`style["background"]` branch in `_layer_html` is missing.

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/video_editor_core/compile_composition.py \
        tests/test_compile_composition.py
git commit -m "feat(core): add the single Composition compiler"
```

**Post-review amendments (Task 6 review):**

- **`audio_mode` is honoured.** `_audio_html` returns `[]` unless
  `composition.audio_mode == "embed"`. A composition that carries cues for an
  external muxer (`from_render_graph`, for ducking and loudnorm) must not embed
  them as well; emitting on cue presence is the inference the design forbids.
  Test: an `external` composition with one cue emits no `<audio>` element and
  still emits its visual layers.
- **A markup-producing motion on a kind that cannot host it is an error.**
  `_layer_html` raises `MotionError` when `built.markup` is non-empty on a
  `media` or `svg` layer: those branches return before the container is
  emitted, so the strip would vanish and the motion would animate nothing.
- The z-index test also asserts the base layer's `z-index:0;`, so a compiler
  that hard-coded `1` for every layer cannot pass.
- The audio test matches the `<audio>` element's own attributes
  (`data-start`, `data-duration`, `data-track-index="150"`), not a bare
  `data-duration` substring that the base image emits too.
- The render log labels its count `elements=`, because `elements` includes the
  `<audio>` tags.

---

## Task 7: Adapt `RenderGraph` and delegate

**Files:**
- Create: `packages/core/src/video_editor_core/adapters/__init__.py`
- Create: `packages/core/src/video_editor_core/adapters/from_render_graph.py`
- Modify: `packages/core/src/video_editor_core/hyperframes_project.py`
- Test: `tests/test_adapter_render_graph.py`

**Interfaces:**
- Consumes: Task 4 model, Task 6 compiler, `Layer`/`Rect`/`RenderGraph`.
- Produces: `from_render_graph(graph, base_video=None) -> Composition`; `layer_id(name, index) -> str`. `compile_hyperframes_project` keeps its signature and now returns `compile_composition(from_render_graph(...), project_dir)`.

`tests/test_hyperframes_project.py` must pass **unchanged** — it pins the emitted karaoke string, the ids, the tracks and `data-no-timeline`.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the RenderGraph -> Composition adapter."""

from __future__ import annotations

from pathlib import Path

from video_editor_core.adapters.from_render_graph import from_render_graph
from video_editor_core.render_graph import Cut, Layer, Rect, RenderGraph, Timing


def _graph(**overrides: object) -> RenderGraph:
    source = overrides.pop("__source", "source.mp4")
    base: dict[str, object] = {
        "job_id": "job-1",
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "source": str(source),
        "has_audio": True,
        "cuts": [Cut(source_start=0.0, source_end=5.0, timeline_start=0.0, timeline_end=5.0)],
    }
    base.update(overrides)
    return RenderGraph(**base)  # type: ignore[arg-type]


def _text(name: str, text: str, start: float, end: float, **style: object) -> Layer:
    return Layer(
        kind="text",
        name=name,
        rect=Rect(y=900, width=1920, height=120),
        text=text,
        timing=Timing(start=start, end=end),
        style={"font_size": 48, "font_color": "#FFFFFF", **style},
    )


def test_the_base_video_is_the_first_layer():
    comp = from_render_graph(_graph())
    layers = comp.scenes[0].layers
    assert layers[0].id == "base_video"
    assert layers[0].media_kind == "video"
    assert layers[0].track == 0 and layers[0].z == 0
    assert comp.audio_mode == "external"


def test_overlays_keep_their_ids_tracks_and_windows():
    layers = [_text("caption_line", "one", 0.0, 1.0), _text("caption_line", "two", 1.0, 2.0)]
    comp = from_render_graph(_graph(overlays=layers))
    overlays = comp.scenes[0].layers[1:]
    assert [layer.id for layer in overlays] == ["caption_line_0", "caption_line_1"]
    assert [layer.track for layer in overlays] == [20, 21]
    assert overlays[0].time.start == 0.0 and overlays[0].time.end == 1.0


def test_a_word_timed_text_layer_becomes_rich_text():
    layer = _text(
        "caption_line",
        "gas and ice",
        2.0,
        3.5,
        highlight_color="#FDB813",
        words=[
            {"text": "gas", "start": 0.0, "end": 0.4},
            {"text": "and", "start": 0.45, "end": 0.7},
            {"text": "ice", "start": 0.8, "end": 1.3},
        ],
    )
    comp = from_render_graph(_graph(overlays=[layer]))
    rich = comp.scenes[0].layers[1]
    assert rich.kind == "rich_text"
    assert [span.text for span in rich.content.spans] == ["gas", "and", "ice"]
    assert rich.content.spans[2].start == 0.8


def test_a_text_layer_without_words_stays_plain_text():
    comp = from_render_graph(_graph(overlays=[_text("card", "Hello", 0.0, 1.0)]))
    assert comp.scenes[0].layers[1].kind == "text"


def test_a_spotlight_shape_keeps_its_preset():
    layer = Layer(
        kind="shape",
        name="spot",
        rect=Rect(x=100, y=100, width=400, height=200),
        timing=Timing(start=0.5, end=2.0),
        style={"effect": "spotlight", "color": "#FDB813"},
    )
    comp = from_render_graph(_graph(overlays=[layer]))
    shape = comp.scenes[0].layers[1]
    assert shape.kind == "shape" and shape.preset == "spotlight"


def test_images_and_videos_map_to_their_media_kinds(tmp_path: Path):
    image = tmp_path / "brand.png"
    image.write_bytes(b"png")
    video = tmp_path / "broll.mp4"
    video.write_bytes(b"mp4")
    layers = [
        Layer(kind="logo", name="brand", rect=Rect(width=180, height=60), source=str(image),
              timing=Timing(start=0.0, end=None)),
        Layer(kind="video", name="broll", rect=Rect(x=1700, y=0, width=220, height=220),
              source=str(video), timing=Timing(start=0.0, end=2.0)),
    ]
    comp = from_render_graph(_graph(overlays=layers))
    overlays = comp.scenes[0].layers[1:]
    assert overlays[0].kind == "media" and overlays[0].media_kind == "image"
    assert overlays[0].style["object_fit"] == "contain"
    assert overlays[1].media_kind == "video"
    assert overlays[1].style["object_fit"] == "cover"


def test_layers_that_leave_the_canvas_are_clamped():
    layer = Layer(
        kind="text",
        name="edge",
        rect=Rect(x=1900, y=1000, width=400, height=200),
        text="edge",
        timing=Timing(start=0.0, end=1.0),
        style={"font_size": 40},
    )
    comp = from_render_graph(_graph(overlays=[layer]))
    rect = comp.scenes[0].layers[1].rect
    assert rect.x + rect.width <= 1920 and rect.y + rect.height <= 1080


def test_the_base_video_override_is_honoured(tmp_path: Path):
    cut = tmp_path / "cut.mp4"
    cut.write_bytes(b"cut")
    comp = from_render_graph(_graph(), base_video=cut)
    assert comp.scenes[0].layers[0].content.source == str(cut)
```

- [ ] **Step 2: Run them to watch them fail**

Run: `uv run pytest tests/test_adapter_render_graph.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_editor_core.adapters'`.

- [ ] **Step 3: Create `adapters/__init__.py`**

```python
"""Adapters from existing pipeline artifacts to :class:`Composition`."""
```

- [ ] **Step 4: Write `adapters/from_render_graph.py`**

```python
"""Adapt a :class:`RenderGraph` into a :class:`Composition`.

The render graph is the engine's cut + overlay description for a finished
base video. This adapter is the historical home of the HyperFrames overlay
compiler; keeping it separate means the compiler never learns what a
``RenderGraph`` is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from video_editor_core.composition import (
    Canvas,
    Composition,
    CompositionLayer,
    CompositionScene,
    LayerContent,
    LayerTime,
    WordSpan,
    quantise,
)
from video_editor_core.render_graph import Layer, RenderGraph

logger = structlog.get_logger(__name__)

OVERLAY_TRACK_BASE = 20


def layer_id(name: str, index: int) -> str:
    """A DOM id from a layer name, scoped by its overlay index."""
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name) or "layer"
    return f"{safe}_{index}"


def from_render_graph(
    graph: RenderGraph, base_video: str | Path | None = None
) -> Composition:
    """Build the Composition for ``graph``, with the base video at track 0."""
    source = str(base_video if base_video is not None else graph.source)
    duration = quantise(float(graph.duration), graph.fps)
    layers: list[CompositionLayer] = [
        CompositionLayer(
            id="base_video",
            kind="media",
            preset="base",
            media_kind="video",
            rect=_full_canvas(graph),
            time=LayerTime(start=0.0, end=duration),
            content=LayerContent(source=source),
            style={"object_fit": "cover", "muted": True},
            track=0,
            z=0,
        )
    ]
    for index, layer in enumerate(graph.overlays):
        built = _overlay_layer(graph, layer, index)
        if built is not None:
            layers.append(built)

    return Composition(
        job_id=graph.job_id,
        canvas=Canvas(width=graph.width, height=graph.height, fps=graph.fps),
        duration=duration,
        scenes=[CompositionScene(id="main", start=0.0, end=duration, layers=layers)],
        audio=[],
        audio_mode="external",
    )


def _full_canvas(graph: RenderGraph):
    from video_editor_core.render_graph import Rect

    return Rect(x=0, y=0, width=graph.width, height=graph.height)


def _overlay_layer(
    graph: RenderGraph, layer: Layer, index: int
) -> CompositionLayer | None:
    start = quantise(layer.timing.start, graph.fps)
    end = (
        quantise(float(graph.duration), graph.fps)
        if layer.timing.end is None
        else quantise(layer.timing.end, graph.fps)
    )
    common: dict[str, Any] = {
        "id": layer_id(layer.name, index),
        "rect": _clamped_rect(layer, graph),
        "time": LayerTime(start=start, end=max(end, start + 1.0 / graph.fps)),
        "style": dict(layer.style),
        "track": OVERLAY_TRACK_BASE + index,
        "z": index + 1,
    }

    if layer.kind == "text":
        words = layer.style.get("words")
        if layer.style.get("highlight_color") and isinstance(words, list) and words:
            spans = [
                WordSpan(
                    text=str(word.get("text", "")),
                    start=float(word.get("start", 0.0)),
                    end=float(word.get("end", word.get("start", 0.0))),
                )
                for word in words
                if isinstance(word, dict)
            ]
            return CompositionLayer(
                kind="rich_text", content=LayerContent(spans=spans), **common
            )
        return CompositionLayer(kind="text", content=LayerContent(text=layer.text), **common)

    if layer.kind == "shape":
        preset = "spotlight" if layer.style.get("effect") == "spotlight" else ""
        return CompositionLayer(kind="shape", preset=preset, **common)

    if layer.kind in {"image", "logo"} and layer.source:
        common["style"] = {**common["style"], "object_fit": "contain"}
        return CompositionLayer(
            kind="media",
            media_kind="image",
            content=LayerContent(source=layer.source),
            **common,
        )

    if layer.kind == "video" and layer.source:
        common["style"] = {**common["style"], "object_fit": "cover"}
        return CompositionLayer(
            kind="media",
            media_kind="video",
            content=LayerContent(source=layer.source),
            **common,
        )

    logger.warning("render_graph_layer_skipped", name=layer.name, kind=layer.kind)
    return None


def _clamped_rect(layer: Layer, graph: RenderGraph):
    from video_editor_core.render_graph import Rect

    rect = layer.rect
    width = max(1, min(rect.width, graph.width))
    height = max(1, min(rect.height, graph.height))
    x = max(0, min(rect.x, graph.width - width))
    y = max(0, min(rect.y, graph.height - height))
    return Rect(x=x, y=y, width=width, height=height)
```

Move the two local `Rect` imports to a single module-level `from video_editor_core.render_graph import Layer, Rect, RenderGraph` and drop them from the helpers.

- [ ] **Step 5: Make `hyperframes_project.py` delegate**

Replace the whole module body after the docstring with:

```python
from __future__ import annotations

from pathlib import Path

import structlog

from video_editor_core.adapters.from_render_graph import from_render_graph
from video_editor_core.compile_composition import compile_composition
from video_editor_core.project_assets import AssetCopier
from video_editor_core.render_graph import RenderGraph

logger = structlog.get_logger(__name__)

COMPOSITION_ID = "main"

#: Kept for callers that imported the private name.
_AssetCopier = AssetCopier


def compile_hyperframes_project(
    graph: RenderGraph,
    project_dir: Path | str,
    base_video: str | Path | None = None,
) -> Path:
    """Write a HyperFrames project for ``graph`` and return the index.html path.

    ``base_video`` overrides the base video source (e.g. a pre-cut
    intermediate); when omitted, ``graph.source`` is used directly.
    """
    composition = from_render_graph(graph, base_video=base_video)
    index_path = compile_composition(composition, project_dir)
    logger.info(
        "hyperframes_project_written",
        job_id=graph.job_id,
        project=str(project_dir),
        clips=len(composition.scenes[0].layers),
    )
    return index_path
```

- [ ] **Step 6: Run the adapter tests and the pinned suite**

Run: `uv run pytest tests/test_adapter_render_graph.py tests/test_hyperframes_project.py -v`
Expected: PASS (8 + 12 tests; this file has 12 test functions, measured — the earlier "14" was a typo). If a pinned assertion fails, fix the **compiler or adapter**, never the test.

- [ ] **Step 7: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/video_editor_core/adapters \
        packages/core/src/video_editor_core/hyperframes_project.py \
        tests/test_adapter_render_graph.py
git commit -m "refactor(core): compile RenderGraph overlays through Composition"
```

**Post-review amendments (Task 7 review).** Both fix parity breaks the code
above introduces against the legacy writer, and both are invisible to every
existing test. Apply them to the adapter.

- **An overlay window that reaches the composition end must be clamped, not
  rejected.** The code above quantises `start` and floors `end` at
  `start + 1/fps`, but never bounds either by the composition duration, so an
  overlay starting in the final frame (or one whose end rounds past the
  duration — `cues.spotlight_layer` extends `end` by 0.2 s) makes
  `Composition._layers_are_valid` raise where `_clip_duration` used to floor
  at 1 ms and emit the element. Replace the inline `LayerTime(...)` in
  `common` with a helper that bounds both ends:

  ```python
  def _layer_time(graph: RenderGraph, layer: Layer, duration: float) -> LayerTime:
      """The overlay's window, bounded to the composition.

      A clip that starts on the last frame or ends past the composition would
      be rejected by the model; the legacy writer floored its duration at 1 ms
      and emitted it, so clamp to the window that fits rather than fail.
      """
      frame = 1.0 / graph.fps
      start = quantise(layer.timing.start, graph.fps)
      end = duration if layer.timing.end is None else quantise(layer.timing.end, graph.fps)
      start = min(max(0.0, start), max(0.0, duration - frame))
      end = min(duration, max(end, start + frame))
      return LayerTime(start=start, end=end)
  ```

  Pass the `duration` computed at the top of `from_render_graph` into
  `_overlay_layer` (and from there into `_layer_time`) instead of
  re-quantising `graph.duration` inside it.

- **`layer_id` must produce ids the model accepts.** `ch.isalnum()` is true
  for CJK, Cyrillic and Greek letters, so a layer named `字幕` yields `字幕_0`
  and `CompositionLayer.id` (pattern `^[A-Za-z0-9_-]+$`) raises at compile
  time. The Global Constraint is the governing text here:

  ```python
  def layer_id(name: str, index: int) -> str:
      """A DOM id from a layer name, scoped by its overlay index."""
      safe = (
          "".join(ch if ch.isascii() and (ch.isalnum() or ch in "-_") else "_" for ch in name)
          or "layer"
      )
      return f"{safe}_{index}"
  ```

  Add one test per amendment to `tests/test_adapter_render_graph.py`:
  a text overlay starting at `duration - 0.01` compiles and ends at or before
  the composition duration; and `layer_id("字幕", 0) == "___0"`.

---

## Task 8: Adapt scene plans and delegate

**Files:**
- Create: `packages/core/src/video_editor_core/adapters/from_scene_plan.py`
- Modify: `packages/core/src/video_editor_core/scene_plan_composition.py`
- Modify: `tests/test_scene_plan_composition.py` (see Step 5's amendment)
- Test: `tests/test_adapter_scene_plan.py`

**Interfaces:**
- Consumes: Task 4 model (`canvas_for_aspect`, `quantise`), Task 6 compiler, `load_design`, `render_typographic`, `SceneRenderError`.
- Produces: `from_scene_plan(plan, *, fps=30) -> Composition`. `compile_scene_plan_project` keeps its signature; `canvas_for_aspect` is re-exported from `scene_plan_composition` so `explainer_qa` keeps importing it from there.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the scene-plan -> Composition adapter."""

from __future__ import annotations

from pathlib import Path

from video_editor_core.adapters.from_scene_plan import from_scene_plan


def _plan(**extra) -> dict:
    plan = {
        "job_id": "plan_job",
        "aspect_ratio": "9:16",
        "scenes": [
            {
                "id": "s1",
                "type": "image",
                "duration_seconds": 3.0,
                "narration": "one",
                "assets": [{"asset_type": "image", "local_path": "still.png"}],
            },
            {
                "id": "s2",
                "type": "video",
                "duration_seconds": 2.0,
                "narration": "two",
                "assets": [{"asset_type": "video", "local_path": "clip.mp4"}],
            },
        ],
    }
    plan.update(extra)
    return plan


def test_scene_windows_are_contiguous_and_ordered():
    comp = from_scene_plan(_plan())
    assert [(s.start, s.end) for s in comp.scenes] == [(0.0, 3.0), (3.0, 5.0)]
    assert comp.duration == 5.0
    assert (comp.canvas.width, comp.canvas.height) == (1080, 1920)


def test_a_still_gets_the_ken_burns_move_for_its_index():
    comp = from_scene_plan(_plan())
    still = comp.scenes[0].layers[0]
    assert still.kind == "media" and still.media_kind == "image"
    assert still.id == "scene_0_img"
    assert still.motions[0].name == "drift"
    assert still.motions[0].params["move"] == 0
    assert still.motions[0].ease == "ease-in-out"


def test_a_video_scene_loops_and_covers():
    comp = from_scene_plan(_plan())
    video = comp.scenes[1].layers[0]
    assert video.media_kind == "video"
    assert video.id == "scene_1_video"
    assert video.style["loop"] is True


def test_a_scene_without_media_becomes_a_placeholder():
    plan = _plan()
    plan["scenes"] = [{"id": "s1", "type": "image", "duration_seconds": 2.0, "narration": "x"}]
    comp = from_scene_plan(plan)
    placeholder = comp.scenes[0].layers[0]
    assert placeholder.kind == "html" and placeholder.preset == "placeholder"
    assert placeholder.content.text == "x"


def test_typographic_scenes_carry_their_markup_and_css():
    plan = _plan()
    plan["scenes"] = [
        {
            "id": "s1",
            "type": "typographic",
            "duration_seconds": 3.0,
            "narration": "Thirty-two billion dollars.",
            "props": {"layout": "big_stat", "headline": "$32B"},
            "design": "cream_editorial",
        }
    ]
    plan["design"] = "cream_editorial"
    comp = from_scene_plan(plan)
    typo = comp.scenes[0].layers[0]
    assert typo.id == "scene_0_typo"
    assert typo.kind == "html"
    assert "typo-0" in typo.content.text
    assert typo.style["extra_css"]


def test_audio_assets_become_cues(tmp_path: Path):
    tts = tmp_path / "line.mp3"
    tts.write_bytes(b"mp3")
    narration = tmp_path / "narration.mp3"
    narration.write_bytes(b"mp3")
    music = tmp_path / "music.mp3"
    music.write_bytes(b"mp3")
    plan = _plan()
    plan["scenes"][0]["assets"].append(
        {"asset_type": "tts", "local_path": str(tts)}
    )
    plan["narration_path"] = str(narration)
    plan["music_path"] = str(music)
    comp = from_scene_plan(plan)
    by_id = {cue.id: cue for cue in comp.audio}
    assert by_id["scene_0_tts"].track == 100
    assert by_id["narration"].track == 150 and by_id["narration"].duration == 5.0
    assert by_id["music"].track == 200


def test_a_missing_audio_path_is_not_emitted():
    plan = _plan(narration_path="/nope/missing.mp3")
    assert from_scene_plan(plan).audio == []


def test_the_design_reaches_the_composition():
    plan = _plan(design="cream_editorial")
    plan["scenes"].append(
        {
            "id": "s3",
            "type": "typographic",
            "duration_seconds": 2.0,
            "narration": "x",
            "props": {"layout": "big_stat", "headline": "X"},
        }
    )
    comp = from_scene_plan(plan)
    assert comp.design is not None and comp.design.name == "cream_editorial"
```

- [ ] **Step 2: Run them to watch them fail**

Run: `uv run pytest tests/test_adapter_scene_plan.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_editor_core.adapters.from_scene_plan'`.

- [ ] **Step 3: Write `adapters/from_scene_plan.py`**

```python
"""Adapt a script-to-video scene-plan dict into a :class:`Composition`.

The plan is the raw dict written to ``plan/scene_plan.json``, duck-typed
against the ``ScenePlan`` schema — the same input the old compiler took.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

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
from video_editor_core.render_graph import Rect
from video_editor_core.typographic_scenes import render_typographic

DEFAULT_FPS = 30


def from_scene_plan(plan: dict[str, Any], *, fps: int = DEFAULT_FPS) -> Composition:
    """Build the Composition for a scene plan."""
    width, height = canvas_for_aspect(plan.get("aspect_ratio"))
    raw_scenes = [s for s in plan.get("scenes", []) or [] if isinstance(s, dict)]
    design = _design_for(plan, raw_scenes)

    scenes: list[CompositionScene] = []
    audio: list[AudioCue] = []
    cursor = 0.0
    for index, scene in enumerate(raw_scenes):
        duration = float(scene.get("duration_seconds", 0.0))
        if duration <= 0:
            continue
        start = quantise(cursor, fps)
        end = quantise(cursor + duration, fps)
        scenes.append(
            CompositionScene(
                id=str(scene.get("id") or f"scene_{index}"),
                start=start,
                end=end,
                layers=_scene_layers(scene, index, start, duration, width, design),
            )
        )
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
    for cue_id, key, track in (
        ("narration", "narration_path", 150),
        ("music", "music_path", 200),
    ):
        path = plan.get(key) or ""
        if path and Path(path).exists():
            audio.append(
                AudioCue(id=cue_id, source=str(path), start=0.0, duration=total, track=track)
            )

    return Composition(
        job_id=str(plan.get("job_id", "scene_plan")),
        canvas=Canvas(width=width, height=height, fps=fps),
        duration=total,
        scenes=scenes,
        audio=audio,
        design=design,
    )


def _design_for(plan: Mapping[str, Any], scenes: list[dict[str, Any]]) -> DesignTokens | None:
    needs_design = any(scene.get("type") == "typographic" for scene in scenes)
    if not needs_design:
        return None
    return load_design(str(plan.get("design") or ""))


def _asset_by_type(scene: dict[str, Any], *types: str) -> dict[str, Any] | None:
    for asset in scene.get("assets", []) or []:
        if isinstance(asset, dict) and asset.get("asset_type") in types:
            return dict(asset)
    return None


def _full_canvas(width: int, height: int) -> Rect:
    return Rect(x=0, y=0, width=width, height=height)


def _scene_layers(
    scene: dict[str, Any],
    index: int,
    start: float,
    duration: float,
    width: int,
    design: DesignTokens | None,
) -> list[CompositionLayer]:
    from video_editor_core.typographic_scenes import SceneRenderError

    height = canvas_for_aspect("9:16")[1]
    window = LayerTime(start=start, end=quantise(start + duration, 30))
    layers: list[CompositionLayer] = []

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
                z=len(layers),
            )
        )
        return layers

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

    text = str(
        scene.get("narration") or scene.get("visual_description") or scene.get("id", "")
    )
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
```

Three fixes before running: import `SceneRenderError` at module level rather than inside the function; derive `height` from the caller's canvas instead of re-deriving 9:16; and pass `fps` into `_scene_layers` so the layer window uses it instead of the hard-coded `quantise(..., 30)` (a caller rendering at 24 fps would otherwise quantise the clip end on the wrong grid and fail the model's contiguity check). Change the signature to `_scene_layers(scene, index, start, duration, width, height, fps, design)` and pass all of them from `from_scene_plan`.

The `_ASPECT_CANVAS` re-export can go: `grep` finds no importer outside `scene_plan_composition.py` itself. `render_scene_plan` calls `compile_scene_plan_project` at the end, so it needs no change.

- [ ] **Step 4: Make `scene_plan_composition.py` delegate**

Delete `_ASPECT_CANVAS`, `_KB_SCALE_END`, `_KB_MOVES`, `_asset_by_type`, `_clip_relative_words`, `_scene_elements` and `_render_html`. Keep `COMPOSITION_ID`, `DEFAULT_FPS` and `render_scene_plan` unchanged, and leave:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from video_editor_core.adapters.from_scene_plan import from_scene_plan
from video_editor_core.compile_composition import compile_composition

#: Re-exported: callers (``explainer_qa``) import the canvas mapping from here.
from video_editor_core.composition import canvas_for_aspect  # noqa: F401

logger = structlog.get_logger(__name__)

COMPOSITION_ID = "main"
DEFAULT_FPS = 30


def compile_scene_plan_project(
    plan: dict[str, Any],
    project_dir: Path | str,
    *,
    fps: int = DEFAULT_FPS,
) -> Path:
    """Write a HyperFrames project for a scene plan; return the index.html path."""
    composition = from_scene_plan(plan, fps=fps)
    index_path = compile_composition(composition, project_dir)
    logger.info(
        "scene_plan_project_written",
        job_id=composition.job_id,
        project=str(project_dir),
        scenes=len(composition.scenes),
        duration=composition.duration,
    )
    return index_path
```

The module docstring stays; update the last two lines to say the compiler lives in `compile_composition.py`.

- [ ] **Step 5: Run the adapter tests and the pinned suites**

Run: `uv run pytest tests/test_adapter_scene_plan.py tests/test_explainer_composition.py tests/test_scene_plan_composition.py tests/test_video_motion_quality.py -v`
Expected: PASS (8 + 7 + 6 + 16 tests).

**Pre-dispatch amendment (keyframe ids).** Three assertions in
`tests/test_scene_plan_composition.py` name the legacy writer's byte output and
cannot pass under the unified compiler, which is the point of the migration:

- `assert "@keyframes kb_0" in html` and
  `assert "animation:kb_0 2.000s" in html` — the compiler now names keyframes
  `ve_drift_<sha1(params)[:8]>` (§5.2 mandates the dedupe key), so no `kb_0`
  can exist. Replace them with assertions on the contract, not the name:
  `assert "@keyframes ve_drift_" in html` and
  `assert "animation:ve_drift_" in html and " 2.000s ease-in-out both" in html`
  (the drift declaration is `<name> 2.000s ease-in-out both`, so the duration
  and easing are still pinned; only the generated name moved).
- `assert assets == ["bg.mp3", "scene1.mp3", "scene1.png"]` — the compiler
  copies each layer's media before it copies the composition's audio cues, so
  the order is now `["scene1.png", "scene1.mp3", "bg.mp3"]`. Assert the same
  *set* and the same count instead of the legacy order:
  `assert sorted(assets) == ["bg.mp3", "scene1.mp3", "scene1.png"]`.

Every other assertion in that file (ids, tracks, `data-start`, durations,
`data-no-timeline`, `loop`, the video's "no keyframes" rule) stays exactly as
it is — those are the parity obligations. If any of them needs editing, the
adapter is wrong: stop and report.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/video_editor_core/adapters/from_scene_plan.py \
        packages/core/src/video_editor_core/scene_plan_composition.py \
        tests/test_adapter_scene_plan.py \
        tests/test_scene_plan_composition.py \
        tests/test_video_motion_quality.py
git commit -m "refactor(core): compile scene plans through Composition"
```

**Post-review amendments (Task 8 review).**

- **The compiler must not put caption CSS on `html` layers.** Task 6's
  `_layer_html` applied `_text_css` to `{"text", "rich_text", "html"}`, so the
  typographic wrapper inherited `font-weight:700`, `font-size:48px`,
  `color:#FFFFFF`, `-webkit-text-stroke:4px #000000` and a drop shadow into
  every glyph of every typographic scene; `_base_css` resets none of them and
  the placeholder's own rule does not cover the typographic path either. The
  legacy writers emitted only `background:{palette.bg}` for typographic and
  only the placeholder declarations for placeholders. The Task 6 code block has
  been amended accordingly (background stays for `html`, `_text_css` does not).
  This task is the sole producer of `kind="html"` layers, so it is the only
  place the regression could surface.
- **Add a compile test for it** in `tests/test_compile_composition.py`: an
  `html` layer with a `background` and `extra_css` emits the background, its
  markup and its stylesheet, and its inline style contains neither
  `-webkit-text-stroke` nor `text-shadow` nor `font-size:`. A placeholder
  layer emits `display:flex` from `_placeholder_css` but no text stroke.
- **A schema-legal scene id that is not `[A-Za-z0-9_-]+` must not abort
  compilation.** `ScenePlan` leaves `id` an unconstrained string, while
  `CompositionScene.id` enforces the pattern. Fall back to the index when the
  id does not match: `scene.get("id")` is used only if
  `re.fullmatch(r"[A-Za-z0-9_-]+", str(...))`, else `f"scene_{index}"`.

---

## Task 9: `DesignTokens.layers`

**Files:**
- Modify: `packages/core/src/video_editor_core/designs.py:73-80`
- Test: `tests/test_design_layers.py`

**Interfaces:**
- Consumes: pydantic.
- Produces: `Layers` model and `DesignTokens.layers: Layers` (defaults to an empty block, so every existing design JSON stays valid). Fields: `safe_area: str`, `scrim/chip/pill/end_card: dict[str, Any]` (extra keys allowed), `caption_preset: str`.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the optional ``layers`` block on a design."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from video_editor_core.designs import DesignError, DesignTokens, load_design

PALETTE = {"bg": "#000000", "ink": "#FFFFFF", "accent": "#F59E0B", "muted": "#888888"}


def design_file(tmp_path: Path, **extra) -> Path:
    data = {"palette": PALETTE, **extra}
    path = tmp_path / "series.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path.parent


def test_a_design_without_layers_still_loads(tmp_path: Path):
    tokens = load_design("series", design_file(tmp_path))
    assert tokens.layers.safe_area == ""
    assert tokens.layers.caption_preset == "karaoke"


def test_the_layers_block_is_parsed(tmp_path: Path):
    tokens = load_design(
        "series",
        design_file(
            tmp_path,
            layers={
                "safe_area": "vertical_short_strict",
                "pill": {"bg": "rgba(38,17,74,0.82)", "size": 42},
                "caption_preset": "karaoke",
            },
        ),
    )
    assert tokens.layers.safe_area == "vertical_short_strict"
    assert tokens.layers.pill["size"] == 42


def test_unknown_layer_keys_are_allowed(tmp_path: Path):
    tokens = load_design("series", design_file(tmp_path, layers={"glow": {"color": "#fff"}}))
    assert tokens.layers.model_extra["glow"] == {"color": "#fff"}


def test_a_design_without_a_palette_is_still_rejected(tmp_path: Path):
    (tmp_path / "broken.json").write_text(json.dumps({"layers": {}}), encoding="utf-8")
    with pytest.raises(DesignError):
        load_design("broken", tmp_path)


def test_design_tokens_validate_directly():
    tokens = DesignTokens(name="x", palette=PALETTE)  # type: ignore[arg-type]
    assert tokens.layers.caption_preset == "karaoke"
```

- [ ] **Step 2: Run them to watch them fail**

Run: `uv run pytest tests/test_design_layers.py -v`
Expected: FAIL — `AttributeError: 'DesignTokens' object has no attribute 'layers'`.

- [ ] **Step 3: Add the model to `designs.py`**

Add `Any` and `ConfigDict` to the imports (`from typing import Any`, `from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator`), then above `DesignTokens`:

```python
class Layers(BaseModel):
    """Layer styling for the Composition compiler; every field is optional.

    Series identity lives here rather than in per-video Python: a design names
    the safe-area profile, the scrim/pill/end-card colours and the caption
    preset, and the content pack supplies only words and assets.

    Unknown keys are allowed so a design can carry a new preset before the
    compiler learns about it.
    """

    model_config = ConfigDict(extra="allow")

    safe_area: str = ""
    scrim: dict[str, Any] = Field(default_factory=dict)
    chip: dict[str, Any] = Field(default_factory=dict)
    pill: dict[str, Any] = Field(default_factory=dict)
    end_card: dict[str, Any] = Field(default_factory=dict)
    caption_preset: str = "karaoke"
```

and on `DesignTokens`:

```python
    motion: Motion = Field(default_factory=Motion)
    layers: Layers = Field(default_factory=Layers)
```

- [ ] **Step 4: Run the tests and the suite**

Run: `uv run pytest tests/test_design_layers.py tests/test_designs.py -v 2>/dev/null || uv run pytest tests/test_design_layers.py -v`
Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/video_editor_core/designs.py tests/test_design_layers.py
git commit -m "feat(designs): add the optional layers block"
```

---

## Task 10: Safe-area profiles

**Files:**
- Modify: `packages/core/src/video_editor_core/safe_area.py`
- Test: `tests/test_safe_area.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `PROFILES: Mapping[str, SafeInsets]` with `vertical_short`, `vertical_short_strict`, `title_safe`; `safe_insets`, `safe_box`, `fit_box`, `is_inside`, `safe_area_violations` all gain a keyword-only `profile: str = "auto"` where `"auto"` keeps today's behaviour. An unknown profile raises `ValueError` listing the known ones.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the named safe-area profiles."""

from __future__ import annotations

import pytest
from video_editor_core.safe_area import PROFILES, safe_box, safe_insets

VERTICAL = (1080, 1920)


def test_auto_keeps_the_existing_vertical_insets():
    assert safe_insets(*VERTICAL) == safe_insets(*VERTICAL, profile="auto")
    assert safe_insets(*VERTICAL).bottom == 0.20


def test_strict_matches_the_platform_numbers():
    insets = PROFILES["vertical_short_strict"]
    assert insets.top == pytest.approx(0.1146)
    assert insets.bottom == pytest.approx(0.25)
    assert insets.left == pytest.approx(0.08)


def test_strict_box_leaves_220_px_top_and_480_px_bottom():
    x, y, width, height = safe_box(*VERTICAL, profile="vertical_short_strict")
    assert y == 220
    assert y + height == 1920 - 480
    assert x == 86
    assert x + width == 1080 - 86


def test_landscape_auto_stays_title_safe():
    assert safe_insets(1920, 1080) == PROFILES["title_safe"]


def test_an_unknown_profile_lists_the_known_ones():
    with pytest.raises(ValueError) as exc:
        safe_insets(*VERTICAL, profile="vertical_short_strictest")
    assert "vertical_short_strict" in str(exc.value)


def test_a_strict_profile_fits_a_text_layer_that_the_default_would_allow():
    from video_editor_core.safe_area import fit_box

    # y=160 sits inside the loose profile's top inset (0.08 * 1920 = 154) and
    # above the strict one's (220), so only the strict profile moves it. A box
    # that both profiles moved would pass even with ``profile`` ignored.
    box = (86, 160, 908, 200)
    assert fit_box(box, *VERTICAL)[1] == 160
    assert fit_box(box, *VERTICAL, profile="vertical_short_strict")[1] == 220
```

- [ ] **Step 2: Run them to watch them fail**

Run: `uv run pytest tests/test_safe_area.py -v`
Expected: FAIL — `ImportError: cannot import name 'PROFILES'`.

- [ ] **Step 3: Add profiles to `safe_area.py`**

```python
from types import MappingProxyType

#: Named profiles. ``"auto"`` (the default everywhere) picks by orientation,
#: which is what every existing caller gets.
PROFILES: Mapping[str, SafeInsets] = MappingProxyType(
    {
        "vertical_short": VERTICAL_INSETS,
        # 220 px top / 480 px bottom at 1080x1920: the Cosmic Saju series'
        # burned-in caption and platform-UI reserve.
        "vertical_short_strict": SafeInsets(top=0.1146, right=0.08, bottom=0.25, left=0.08),
        "title_safe": TITLE_SAFE_INSETS,
    }
)


def safe_insets(width: int, height: int, *, profile: str = "auto") -> SafeInsets:
    """Return the safe-area insets for a canvas under ``profile``."""
    if profile in {"", "auto"}:
        return VERTICAL_INSETS if height >= width * _VERTICAL_RATIO else TITLE_SAFE_INSETS
    try:
        return PROFILES[profile]
    except KeyError:
        raise ValueError(
            f"unknown safe-area profile {profile!r}; known: {sorted(PROFILES)}"
        ) from None


def safe_box(width: int, height: int, *, profile: str = "auto") -> Box:
    insets = safe_insets(width, height, profile=profile)
    ...  # unchanged body


def fit_box(box: Box, width: int, height: int, *, profile: str = "auto") -> Box:
    sx, sy, sw, sh = safe_box(width, height, profile=profile)
    ...  # unchanged body


def is_inside(box: Box, width: int, height: int, *, profile: str = "auto") -> bool:
    return fit_box(box, width, height, profile=profile) == box


def safe_area_violations(
    layers: Iterable[Layer], width: int, height: int, *, profile: str = "auto"
) -> list[dict[str, object]]:
    safe = safe_box(width, height, profile=profile)
    ...  # unchanged body, using profile=profile in is_inside
```

Add `Mapping` to the `collections.abc` import.

- [ ] **Step 4: Run the tests and the suite**

Run: `uv run pytest tests/test_safe_area.py -v`
Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/video_editor_core/safe_area.py tests/test_safe_area.py
git commit -m "feat(safe-area): add strict vertical and title-safe profiles"
```

---

## Task 11: Bundle Cormorant Garamond and Noto Serif KR

**Files:**
- Create: `designs/glyphs/noto_serif_kr.json`
- Create: `scripts/build_font_subsets.py`
- Create: `packages/core/src/video_editor_core/fonts/CormorantGaramond.ttf` (built)
- Create: `packages/core/src/video_editor_core/fonts/NotoSerifKR.ttf` (built)
- Create: `packages/core/src/video_editor_core/fonts/OFL-Cormorant Garamond.txt` (copied)
- Create: `packages/core/src/video_editor_core/fonts/OFL-Noto Serif KR.txt` (copied)
- Modify: `packages/core/src/video_editor_core/fonts.py:18-33,35-59`
- Test: `tests/test_fonts.py`

**Interfaces:**
- Consumes: `fontTools` (already in `uv.lock`).
- Produces: `BUNDLED_FONTS` gains `"Cormorant Garamond"` and `"Noto Serif KR"`; `font_stack` gives CJK fallbacks for Noto Serif KR; `font_face_css` reports `format('woff2')` or `format('truetype')` from the file suffix. Outputs are `.ttf` on purpose: woff2 needs `brotli`, which is not installed.

- [ ] **Step 1: Write the glyph manifest**

`designs/glyphs/noto_serif_kr.json` — the series' declared character set. Every Hanja the brief uses, plus the Hangul the layer chips need:

```json
{
  "language": "ko",
  "font": "Noto Serif KR",
  "characters": {
    "사": {"reading": "sa", "meaning": "four"},
    "주": {"reading": "ju", "meaning": "pillar"},
    "팔": {"reading": "pal", "meaning": "eight"},
    "자": {"reading": "ja", "meaning": "character"},
    "오": {"reading": "o", "meaning": "five"},
    "행": {"reading": "haeng", "meaning": "element"},
    "천": {"reading": "cheon", "meaning": "heaven"},
    "간": {"reading": "gan", "meaning": "stem"},
    "지": {"reading": "ji", "meaning": "branch"},
    "음": {"reading": "eum", "meaning": "yin"},
    "양": {"reading": "yang", "meaning": "yang"},
    "四": {"reading": "sa", "meaning": "four"},
    "柱": {"reading": "ju", "meaning": "pillar"},
    "八": {"reading": "pal", "meaning": "eight"},
    "字": {"reading": "ja", "meaning": "character"},
    "五": {"reading": "o", "meaning": "five"},
    "行": {"reading": "haeng", "meaning": "element"},
    "天": {"reading": "cheon", "meaning": "heaven"},
    "干": {"reading": "gan", "meaning": "stem"},
    "地": {"reading": "ji", "meaning": "earth"},
    "支": {"reading": "ji", "meaning": "branch"},
    "陰": {"reading": "eum", "meaning": "yin"},
    "陽": {"reading": "yang", "meaning": "yang"}
  }
}
```

- [ ] **Step 2: Write `scripts/build_font_subsets.py`**

```python
"""Build the bundled font subsets from the upstream OFL variable fonts.

Reproducible by construction: the inputs are upstream Google Fonts files and
the character set is the committed glyph manifest, so re-running it on a fresh
checkout produces the same bytes.

    curl -fL -o /tmp/CormorantGaramond.ttf \
      'https://raw.githubusercontent.com/google/fonts/main/ofl/cormorantgaramond/CormorantGaramond%5Bwght%5D.ttf'
    curl -fL -o /tmp/NotoSerifKR.ttf \
      'https://raw.githubusercontent.com/google/fonts/main/ofl/notoserifkr/NotoSerifKR%5Bwght%5D.ttf'
    uv run python scripts/build_font_subsets.py \
      --cormorant /tmp/CormorantGaramond.ttf --noto /tmp/NotoSerifKR.ttf
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fontTools import subset

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "packages" / "core" / "src" / "video_editor_core" / "fonts"
GLYPHS = ROOT / "designs" / "glyphs" / "noto_serif_kr.json"

#: Printable ASCII plus the punctuation the series sets in display type.
LATIN = "".join(chr(code) for code in range(0x20, 0x7F)) + "·–—‘’“”…"


def build(source: Path, dest: Path, text: str) -> None:
    options = subset.Options()
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.notdef_outline = True
    font = subset.load_font(str(source), options)
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(text=text)
    subsetter.subset(font)
    subset.save_font(font, str(dest), options)
    print(f"{dest.name}: {dest.stat().st_size // 1024} KB")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cormorant", type=Path, required=True)
    parser.add_argument("--noto", type=Path, required=True)
    args = parser.parse_args()

    manifest = json.loads(GLYPHS.read_text(encoding="utf-8"))
    characters = set(manifest["characters"]) | set(LATIN)

    build(args.cormorant, FONT_DIR / "CormorantGaramond.ttf", LATIN)
    build(args.noto, FONT_DIR / "NotoSerifKR.ttf", "".join(sorted(characters)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Download the upstream fonts and the licences**

```bash
mkdir -p /tmp/ofl-fonts
curl -fL -o /tmp/ofl-fonts/CormorantGaramond.ttf \
  'https://raw.githubusercontent.com/google/fonts/main/ofl/cormorantgaramond/CormorantGaramond%5Bwght%5D.ttf'
curl -fL -o /tmp/ofl-fonts/NotoSerifKR.ttf \
  'https://raw.githubusercontent.com/google/fonts/main/ofl/notoserifkr/NotoSerifKR%5Bwght%5D.ttf'
curl -fL -o '/mnt/data2/git_repos/creative_repos/video-editor/packages/core/src/video_editor_core/fonts/OFL-Cormorant Garamond.txt' \
  'https://raw.githubusercontent.com/google/fonts/main/ofl/cormorantgaramond/OFL.txt'
curl -fL -o '/mnt/data2/git_repos/creative_repos/video-editor/packages/core/src/video_editor_core/fonts/OFL-Noto Serif KR.txt' \
  'https://raw.githubusercontent.com/google/fonts/main/ofl/notoserifkr/OFL.txt'
ls -la /tmp/ofl-fonts
```

Expected: both files are several MB. If a URL 404s, download the same family from <https://fonts.google.com> ("Cormorant Garamond", "Noto Serif KR") and pass the downloaded `.ttf` path to the script instead — do not substitute a different family.

- [ ] **Step 4: Build the subsets**

Run:

```bash
uv run python scripts/build_font_subsets.py \
  --cormorant /tmp/ofl-fonts/CormorantGaramond.ttf \
  --noto /tmp/ofl-fonts/NotoSerifKR.ttf
```

Expected: `CormorantGaramond.ttf: NN KB` and `NotoSerifKR.ttf: NN KB`, both under ~400 KB. A multi-megabyte NotoSerifKR output means the subset failed — check the manifest path.

- [ ] **Step 5: Write the failing tests**

Append to `tests/test_fonts.py`:

```python
def test_cjk_families_get_cjk_fallbacks() -> None:
    stack = font_stack("Noto Serif KR")
    assert stack.startswith("'Noto Serif KR', ")
    assert "Noto Serif CJK KR" in stack
    assert stack.endswith("sans-serif")


def test_the_series_fonts_are_bundled() -> None:
    assert "Cormorant Garamond" in BUNDLED_FONTS
    assert "Noto Serif KR" in BUNDLED_FONTS
    for family in ("Cormorant Garamond", "Noto Serif KR"):
        assert (FONT_DIR / BUNDLED_FONTS[family]).stat().st_size > 5_000


def test_font_face_css_reports_the_file_format(tmp_path: Path) -> None:
    css = font_face_css(["Noto Serif KR"], tmp_path / "fonts")
    assert "format('truetype')" in css
    assert "font-family: 'Noto Serif KR'" in css
    assert (tmp_path / "fonts" / "NotoSerifKR.ttf").is_file()
```

`test_bundled_font_files_and_licences_exist` now also covers the new families.

- [ ] **Step 6: Run them to watch them fail**

Run: `uv run pytest tests/test_fonts.py -v`
Expected: FAIL — `assert 'Cormorant Garamond' in BUNDLED_FONTS`.

- [ ] **Step 7: Update `fonts.py`**

```python
BUNDLED_FONTS: dict[str, str] = {
    "Inter": "Inter.ttf",
    "Montserrat": "Montserrat.ttf",
    "Cormorant Garamond": "CormorantGaramond.ttf",
    "Noto Serif KR": "NotoSerifKR.ttf",
}

DEFAULT_FAMILY = "Inter"
_FALLBACKS = "'Inter', 'Noto Sans', 'DejaVu Sans', system-ui, sans-serif"
_CJK_FAMILIES = frozenset({"Noto Serif KR"})
#: The bundled subset covers only the declared glyphs, so a system CJK face
#: stays in the stack for anything the series has not declared yet.
_CJK_FALLBACKS = "'Noto Serif CJK KR', 'Noto Sans KR', system-ui, sans-serif"


def font_stack(family: str | None) -> str:
    """Return a CSS ``font-family`` list led by ``family`` with safe fallbacks."""
    family = (family or DEFAULT_FAMILY).strip().replace("'", "")
    if family in _CJK_FAMILIES:
        return f"'{family}', {_CJK_FALLBACKS}"
    if family == DEFAULT_FAMILY:
        return _FALLBACKS
    return f"'{family}', {_FALLBACKS}"
```

and in `font_face_css`, replace the format string with:

```python
        css_format = "woff2" if filename.endswith(".woff2") else "truetype"
        rules.append(
            f"@font-face {{ font-family: '{family}'; "
            f"src: url('{url_prefix}/{filename}') format('{css_format}'); "
            f"font-weight: 100 900; font-display: block; }}"
        )
```

Update the module docstring: the fonts are now subsets, and the Noto subset only covers `designs/glyphs/noto_serif_kr.json`.

- [ ] **Step 8: Run the tests and the suite**

Run: `uv run pytest tests/test_fonts.py -v`
Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add designs/glyphs/noto_serif_kr.json scripts/build_font_subsets.py \
        packages/core/src/video_editor_core/fonts.py \
        packages/core/src/video_editor_core/fonts tests/test_fonts.py
git commit -m "feat(fonts): bundle Cormorant Garamond and a Noto Serif KR subset"
```

---

## Task 12: Wrap `hyperframes check` and `snapshot`

**Files:**
- Modify: `packages/core/src/video_editor_core/hyperframes_cli.py`
- Test: `tests/test_hyperframes_cli_check.py`

**Interfaces:**
- Consumes: `subprocess`, `Path`.
- Produces: `HyperFramesCheckError(messages: list[str])`; `check_project(project_dir, *, cli_path="hyperframes", timeout=DEFAULT_TIMEOUT, strict=False) -> str` (the report text); `snapshot_project(project_dir, output_dir, *, at: Sequence[float] | None = None, frames: int = 5, cli_path=..., timeout=...) -> list[Path]`.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the check/snapshot CLI wrappers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from video_editor_core import hyperframes_cli
from video_editor_core.hyperframes_cli import (
    HyperFramesCheckError,
    check_project,
    snapshot_project,
)


def test_check_project_runs_the_checker_and_returns_its_report(tmp_path: Path, monkeypatch):
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="0 issues\n", stderr="")

    monkeypatch.setattr(hyperframes_cli.subprocess, "run", fake_run)
    report = check_project(tmp_path, strict=True)
    assert report == "0 issues\n"
    assert seen[0][0] == "hyperframes"
    assert seen[0][1] == "check"
    assert "--strict" in seen[0]


def test_check_project_raises_with_the_cli_messages(tmp_path: Path, monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(
            1, cmd, output="", stderr="layout: text overflows\ncontrast: 2.1\n"
        )

    monkeypatch.setattr(hyperframes_cli.subprocess, "run", fake_run)
    with pytest.raises(HyperFramesCheckError) as exc:
        check_project(tmp_path)
    assert "text overflows" in str(exc.value)
    assert any("contrast" in message for message in exc.value.messages)


def test_snapshot_project_collects_pngs(tmp_path: Path, monkeypatch):
    def fake_run(cmd, **kwargs):
        output = Path(cmd[cmd.index("-o") + 1])
        output.mkdir(parents=True, exist_ok=True)
        (output / "at-0s.png").write_bytes(b"png")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(hyperframes_cli.subprocess, "run", fake_run)
    shots = snapshot_project(tmp_path / "proj", tmp_path / "shots", at=[0.0, 2.5])
    assert [path.name for path in shots] == ["at-0s.png"]


def test_snapshot_project_passes_exact_times(tmp_path: Path, monkeypatch):
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        output = Path(cmd[cmd.index("-o") + 1])
        output.mkdir(parents=True, exist_ok=True)
        (output / "at-0s.png").write_bytes(b"png")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(hyperframes_cli.subprocess, "run", fake_run)
    snapshot_project(tmp_path / "proj", tmp_path / "shots", at=[1.5, 12.25])
    assert "--at" in seen[0] and "1.5,12.25" in seen[0]
    assert "--no-end" in seen[0]


def test_snapshot_project_fails_when_no_frames_appear(tmp_path: Path, monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(hyperframes_cli.subprocess, "run", fake_run)
    with pytest.raises(hyperframes_cli.RenderSegmentError):
        snapshot_project(tmp_path / "proj", tmp_path / "shots", frames=3)
```

- [ ] **Step 2: Run them to watch them fail**

Run: `uv run pytest tests/test_hyperframes_cli_check.py -v`
Expected: FAIL — `ImportError: cannot import name 'check_project'`.

- [ ] **Step 3: Add the wrappers to `hyperframes_cli.py`**

```python
class HyperFramesCheckError(Exception):
    """Raised when ``hyperframes check`` reports a problem."""

    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        super().__init__("HyperFrames check failed: " + "; ".join(messages))


def _cli_messages(stdout: str | None, stderr: str | None) -> list[str]:
    text = (stderr or "").strip() or (stdout or "").strip()
    messages = [line.strip() for line in text.splitlines() if line.strip()]
    return messages or ["the CLI reported no detail"]


def check_project(
    project_dir: Path | str,
    *,
    cli_path: str = "hyperframes",
    timeout: int = DEFAULT_TIMEOUT,
    strict: bool = False,
) -> str:
    """Run ``hyperframes check``: lint, runtime, layout and contrast.

    Returns the CLI's report text; raises ``HyperFramesCheckError`` on a
    non-zero exit so a build cannot ignore a composition problem.
    """
    cmd = [cli_path, "check", str(Path(project_dir).resolve())]
    if strict:
        cmd.append("--strict")
    logger.info("hyperframes_check", cmd=" ".join(cmd))
    try:
        result = subprocess.run(cmd, check=True, timeout=timeout, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise HyperFramesCheckError(_cli_messages(exc.stdout, exc.stderr)) from exc
    except subprocess.TimeoutExpired as exc:
        raise HyperFramesCheckError([f"check timed out after {timeout}s"]) from exc
    return result.stdout


def snapshot_project(
    project_dir: Path | str,
    output_dir: Path | str,
    *,
    at: Sequence[float] | None = None,
    frames: int = 5,
    cli_path: str = "hyperframes",
    timeout: int = DEFAULT_TIMEOUT,
) -> list[Path]:
    """Capture PNG frames; returns the files, sorted by name."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    cmd = [cli_path, "snapshot", str(Path(project_dir).resolve()), "-o", str(output.resolve())]
    if at:
        cmd.extend(["--at", ",".join(f"{float(t):g}" for t in at), "--no-end"])
    else:
        cmd.extend(["--frames", str(frames)])
    logger.info("hyperframes_snapshot", cmd=" ".join(cmd))
    try:
        subprocess.run(cmd, check=True, timeout=timeout, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise RenderSegmentError(
            f"HyperFrames snapshot failed with exit status {exc.returncode}\n"
            f"stderr: {(exc.stderr or '')[-2000:]}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RenderSegmentError(f"HyperFrames snapshot timed out after {timeout}s") from exc

    shots = sorted(output.glob("*.png"))
    if not shots:
        raise RenderSegmentError(f"HyperFrames snapshot wrote no PNGs to {output}")
    return shots
```

Add `from collections.abc import Sequence` to the imports.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_hyperframes_cli_check.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/video_editor_core/hyperframes_cli.py \
        tests/test_hyperframes_cli_check.py
git commit -m "feat(hyperframes): wrap check and snapshot"
```

---

## Task 13: The five QA gates

**Files:**
- Create: `packages/core/src/video_editor_core/glyphs.py`
- Modify: `packages/core/src/video_editor_core/cues.py`
- Create: `apps/cli/src/video_editor_cli/composition_qa.py`
- Modify: `apps/cli/src/video_editor_cli/explainer_qa.py:145`
- Modify: `apps/cli/src/video_editor_cli/explainer_pipeline.py:1031`
- Test: `tests/test_glyphs.py`, `tests/test_cue_strict.py`, `tests/test_composition_qa.py`

**Interfaces:**
- Consumes: `load_glyph_manifest`, `undeclared_glyphs`, `resolve_cues(strict=True)`, `safe_area_violations`, `check_project`, `snapshot_project`, `from_scene_plan`.
- Produces: `glyphs.py` (`DEFAULT_MANIFEST`, `GlyphManifestError`, `is_cjk`, `load_glyph_manifest`, `cjk_characters`, `undeclared_glyphs`); `cues.CueMismatchError` and `resolve_cues(..., strict=False)`; `composition_qa.evaluate_composition_qa(inputs) -> list[QaCheck]`, `composition_qa.gather_composition_qa(...) -> list[QaCheck]`; `explainer_qa.evaluate_qa(inputs, extra=None)`.

- [ ] **Step 1: Write the failing glyph tests**

```python
"""Tests for the CJK glyph gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from video_editor_core.glyphs import (
    GlyphManifestError,
    cjk_characters,
    is_cjk,
    load_glyph_manifest,
    undeclared_glyphs,
)


def test_the_bundled_manifest_loads_and_declares_hanja():
    manifest = load_glyph_manifest()
    assert "四" in manifest and "사" in manifest
    assert manifest["四"]["reading"] == "sa"


def test_is_cjk_covers_han_and_hangul():
    assert is_cjk("四") and is_cjk("사")
    assert not is_cjk("a") and not is_cjk("4")


def test_cjk_characters_dedupes_and_ignores_latin():
    assert cjk_characters("四柱 four 사주 四") == {"四", "柱", "사", "주"}


def test_a_declared_character_passes():
    manifest = load_glyph_manifest()
    assert undeclared_glyphs(["四柱"], manifest) == []


def test_an_undeclared_hanja_fails():
    manifest = load_glyph_manifest()
    assert undeclared_glyphs(["龍"], manifest) == ["龍"]


def test_a_missing_manifest_is_an_error(tmp_path: Path):
    with pytest.raises(GlyphManifestError):
        load_glyph_manifest("nope", directory=tmp_path)


def test_a_manifest_without_characters_is_an_error(tmp_path: Path):
    (tmp_path / "bad.json").write_text(json.dumps({"characters": []}), encoding="utf-8")
    with pytest.raises(GlyphManifestError):
        load_glyph_manifest("bad", directory=tmp_path)
```

- [ ] **Step 2: Run them to watch them fail**

Run: `uv run pytest tests/test_glyphs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_editor_core.glyphs'`.

- [ ] **Step 3: Write `glyphs.py`**

```python
"""CJK glyph safety: every character a layer draws must be declared.

The bundled Noto Serif KR is a subset, so an undeclared character renders as a
blank box — and a wrong Hanja is the failure mode the series brief calls out.
The content pack declares each character with its reading and meaning; this
gate compares the composition's text against that table.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

GLYPH_DIR = Path(__file__).resolve().parents[4] / "designs" / "glyphs"
DEFAULT_MANIFEST = "noto_serif_kr"

#: Han (CJK unified + extension A), Hangul jamo and Hangul syllables.
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x1100, 0x11FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xAC00, 0xD7AF),
)


class GlyphManifestError(ValueError):
    """A glyph manifest that cannot be read or is malformed."""


def is_cjk(character: str) -> bool:
    return any(low <= ord(character) <= high for low, high in _CJK_RANGES)


def cjk_characters(text: str) -> set[str]:
    """The distinct CJK characters in ``text``."""
    return {character for character in text if is_cjk(character)}


def load_glyph_manifest(
    name: str = DEFAULT_MANIFEST, directory: Path | str | None = None
) -> dict[str, dict[str, Any]]:
    """Load ``<directory>/<name>.json`` and return its character table."""
    root = Path(directory) if directory is not None else GLYPH_DIR
    path = root / f"{name}.json"
    if not path.is_file():
        raise GlyphManifestError(f"glyph manifest {name!r} is missing: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GlyphManifestError(f"glyph manifest {name!r} cannot be read: {exc}") from exc
    characters = data.get("characters") if isinstance(data, dict) else None
    if not isinstance(characters, dict) or not characters:
        raise GlyphManifestError(
            f"glyph manifest {name!r} must hold a non-empty 'characters' object"
        )
    return {str(key): dict(value) for key, value in characters.items()}


def undeclared_glyphs(
    texts: Iterable[str], manifest: dict[str, dict[str, Any]]
) -> list[tuple[str, str]]:
    """Every (character, text) pair whose character the manifest does not declare."""
    missing: dict[str, str] = {}
    for text in texts:
        for character in cjk_characters(text):
            if character not in manifest:
                missing.setdefault(character, text)
    return [(character, missing[character]) for character in sorted(missing)]
```

Import `Iterable` from `collections.abc`, and change the tests to compare characters only.

- [ ] **Step 4: Make the cue resolver strict-optional**

Add to `cues.py`:

```python
class CueMismatchError(ValueError):
    """Raised when a declared cue matches no spoken word."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(
            "cue phrases matched no spoken word: " + ", ".join(repr(m) for m in missing)
        )
```

and in `resolve_cues` add `strict: bool = False`:

```python
    word_list = list(words)
    resolved: dict[str, float] = {}
    for key, phrase in cues.items():
        t = find_cue(word_list, phrase, after=after, before=before)
        if t is not None:
            resolved[key] = max(after, t - lead)
    if strict:
        missing = sorted(key for key in cues if key not in resolved)
        if missing:
            raise CueMismatchError(missing)
    return resolved
```

- [ ] **Step 5: Write the cue tests**

```python
"""Tests for the strict cue resolver."""

from __future__ import annotations

import pytest
from video_editor_core.cues import CueMismatchError, resolve_cues

WORDS = [
    {"word": "the", "start": 0.0, "end": 0.2},
    {"word": "plum", "start": 0.2, "end": 0.5},
    {"word": "blossom", "start": 0.5, "end": 0.9},
]


def test_a_matching_cue_resolves():
    resolved = resolve_cues(WORDS, {"bloom": "plum blossom"}, lead=0.0)
    assert resolved == {"bloom": pytest.approx(0.2)}


def test_strict_mode_raises_on_a_missing_cue():
    with pytest.raises(CueMismatchError) as exc:
        resolve_cues(WORDS, {"bloom": "bamboo grove"}, strict=True)
    assert exc.value.missing == ["bloom"]


def test_non_strict_mode_still_drops_the_missing_cue_silently():
    assert resolve_cues(WORDS, {"bloom": "bamboo grove"}) == {}
```

- [ ] **Step 6: Write `composition_qa.py`**

```python
"""The composition gates: glyph, cue, safe area, composition and visual.

These run on every explainer build, alongside the measured-file QA in
``explainer_qa``. Each gate is fail-closed: a problem that could not be
measured is a failing check, never a silent pass.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from video_editor_cli.explainer_qa import QaCheck
from video_editor_core.adapters.from_scene_plan import from_scene_plan
from video_editor_core.cues import find_cue
from video_editor_core.glyphs import load_glyph_manifest, undeclared_glyphs
from video_editor_core.hyperframes_cli import (
    HyperFramesCheckError,
    check_project,
    snapshot_project,
)
from video_editor_core.safe_area import safe_area_violations

logger = structlog.get_logger(__name__)

#: A frame whose luma standard deviation is below this is effectively blank.
MIN_FRAME_STDDEV = 6.0


@dataclass(frozen=True)
class CompositionQaInputs:
    undeclared: tuple[tuple[str, str], ...] = ()
    missing_cues: tuple[str, ...] = ()
    safe_area: tuple[str, ...] = ()
    composition_report: str = ""
    frame_stddevs: tuple[float, ...] = ()
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
            ok=not inputs.missing_cues,
            detail=(
                "every cue matched a spoken word"
                if not inputs.missing_cues
                else "no match for " + ", ".join(inputs.missing_cues)
            ),
        ),
        QaCheck(
            name="safe_area",
            ok=not inputs.safe_area,
            detail="text inside the profile" if not inputs.safe_area else "; ".join(inputs.safe_area),
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
                else "no frames captured"
            ),
        ),
    ]
```

- [ ] **Step 7: Add the gatherer that measures a real build**

Append to `composition_qa.py`:

```python
def cue_problems(plan: dict[str, Any]) -> list[str]:
    """Cues that name a phrase their own scene never speaks."""
    problems: list[str] = []
    for scene in plan.get("scenes", []) or []:
        props = scene.get("props") or {}
        cue = props.get("cue")
        if not cue:
            continue
        words = props.get("word_times") or []
        if find_cue(words, str(cue), after=0.0) is None:
            problems.append(f"{scene.get('id')}: {cue!r}")
    return problems


def frame_stddev(path: Path) -> float:
    """Luma standard deviation of one PNG, via FFmpeg's signalstats."""
    result = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "info",
            "-i", str(path), "-vf", "signalstats,metadata=print", "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in (result.stderr or "").splitlines():
        if "lavfi.signalstats.YSTD=" in line:
            return float(line.split("=", 1)[1])
    raise ValueError(f"no luma statistics for {path}")


def gather_composition_qa(
    *,
    composition_dir: Path,
    plan: dict[str, Any],
    cli_path: str = "hyperframes",
    glyph_manifest: str = "noto_serif_kr",
    frame_dir: Path | None = None,
) -> list[QaCheck]:
    """Measure the five gates for a finished composition project.

    A gate whose measurement fails becomes a failing check carrying the
    reason; the build never passes by accident.
    """
    composition = from_scene_plan(plan)
    try:
        manifest = load_glyph_manifest(glyph_manifest)
        undeclared = tuple(
            undeclared_glyphs(
                [
                    layer.content.text
                    for scene in composition.scenes
                    for layer in scene.layers
                    if layer.content.text
                ],
                manifest,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("composition_gate_glyphs_failed", error=str(exc))
        return [QaCheck("glyphs", False, f"manifest unavailable: {exc}")]

    violations = safe_area_violations(
        [
            _as_render_layer(layer)
            for scene in composition.scenes
            for layer in scene.layers
            if layer.kind in {"text", "rich_text"}
        ],
        composition.canvas.width,
        composition.canvas.height,
    )
    safe_area = tuple(
        f"{item['name']} at {item['rect']}" for item in violations
    )

    try:
        report = check_project(composition_dir, cli_path=cli_path)
    except HyperFramesCheckError as exc:
        report = "FAILED: " + "; ".join(exc.messages[:5])

    stddevs: tuple[float, ...] = ()
    try:
        shots = snapshot_project(
            composition_dir,
            frame_dir or (Path(composition_dir) / "qa-frames"),
            at=[(scene.start + scene.end) / 2 for scene in composition.scenes],
            cli_path=cli_path,
        )
        stddevs = tuple(frame_stddev(shot) for shot in shots)
    except Exception as exc:  # noqa: BLE001
        logger.warning("composition_gate_visual_failed", error=str(exc))

    return evaluate_composition_qa(
        CompositionQaInputs(
            undeclared=undeclared,
            missing_cues=tuple(cue_problems(plan)),
            safe_area=safe_area,
            composition_report=report,
            frame_stddevs=stddevs,
        )
    )
```

`safe_area_violations` takes render-graph `Layer`s. Rather than fake one, add a composition-aware helper to `safe_area.py`:

```python
def composition_safe_area_violations(
    composition, *, profile: str = "auto"
) -> list[dict[str, object]]:
    """Text layers of a Composition whose rect leaves the safe area."""
    safe = safe_box(composition.canvas.width, composition.canvas.height, profile=profile)
    out: list[dict[str, object]] = []
    for scene in composition.scenes:
        for layer in scene.layers:
            if layer.kind not in {"text", "rich_text"}:
                continue
            rect = layer.rect
            box = (rect.x, rect.y, rect.width, rect.height)
            if not is_inside(box, composition.canvas.width, composition.canvas.height, profile=profile):
                out.append({"name": layer.id, "rect": box, "safe": safe})
    return out
```

Use that instead of `_as_render_layer`. Add a test for it in `tests/test_safe_area.py`.

- [ ] **Step 8: Write `tests/test_composition_qa.py`**

```python
"""Tests for the composition gates."""

from __future__ import annotations

from video_editor_cli.composition_qa import (
    CompositionQaInputs,
    cue_problems,
    evaluate_composition_qa,
)


def _ok_inputs(**overrides) -> CompositionQaInputs:
    base = {
        "composition_report": "0 issues",
        "frame_stddevs": (40.0, 38.0),
    }
    base.update(overrides)
    return CompositionQaInputs(**base)  # type: ignore[arg-type]


def test_a_clean_build_passes_every_gate():
    checks = evaluate_composition_qa(_ok_inputs())
    assert [check.name for check in checks] == [
        "glyphs", "cues", "safe_area", "composition", "visual",
    ]
    assert all(check.ok for check in checks)


def test_a_gate_that_could_not_run_fails():
    checks = evaluate_composition_qa(CompositionQaInputs(ran=False))
    assert checks[0].ok is False


def test_an_undeclared_hanja_fails_the_glyph_gate():
    checks = evaluate_composition_qa(_ok_inputs(undeclared=(("龍", "天龍"),)))
    glyphs = next(check for check in checks if check.name == "glyphs")
    assert glyphs.ok is False and "龍" in glyphs.detail


def test_a_missing_cue_fails_the_cue_gate():
    checks = evaluate_composition_qa(_ok_inputs(missing_cues=("s3: 'bamboo'",)))
    assert next(c for c in checks if c.name == "cues").ok is False


def test_a_blank_frame_fails_the_visual_gate():
    checks = evaluate_composition_qa(_ok_inputs(frame_stddevs=(40.0, 1.2)))
    assert next(c for c in checks if c.name == "visual").ok is False


def test_no_frames_at_all_fails_the_visual_gate():
    checks = evaluate_composition_qa(_ok_inputs(frame_stddevs=()))
    assert next(c for c in checks if c.name == "visual").ok is False


def test_a_failed_check_report_fails_the_composition_gate():
    checks = evaluate_composition_qa(_ok_inputs(composition_report="FAILED: overflow"))
    assert next(c for c in checks if c.name == "composition").ok is False


def test_cue_problems_finds_a_phrase_the_scene_never_says():
    plan = {
        "scenes": [
            {
                "id": "s0",
                "props": {
                    "cue": "bamboo",
                    "word_times": [{"word": "plum", "start": 0.2, "end": 0.4}],
                },
            }
        ]
    }
    assert cue_problems(plan) == ["s0: 'bamboo'"]


def test_cue_problems_ignores_a_scene_without_a_cue():
    assert cue_problems({"scenes": [{"id": "s0", "props": {}}]}) == []
```

- [ ] **Step 9: Let `evaluate_qa` take extra checks**

In `explainer_qa.py`:

```python
def evaluate_qa(inputs: QaInputs, extra: list[QaCheck] | None = None) -> list[QaCheck]:
    """Run every check; the run fails unless all of them pass.

    ``extra`` carries checks measured outside this module (the composition
    gates), so one report covers the whole build.
    """
    checks = [ ... existing list ... ]
    if extra:
        checks.extend(extra)
    return checks
```

- [ ] **Step 10: Wire the gates into the explain pipeline**

In `explainer_pipeline.py`, immediately before `checks = evaluate_qa(inputs)`:

```python
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
        except Exception as exc:  # noqa: BLE001
            logger.warning("composition_gates_failed", error=str(exc))
            extra_checks = [QaCheck("composition_gates", False, str(exc))]
    checks = evaluate_qa(inputs, extra_checks)
```

Imports: `from video_editor_cli.composition_qa import gather_composition_qa` and `QaCheck` from `explainer_qa`.

- [ ] **Step 11: Run the gate tests and the suite**

Run: `uv run pytest tests/test_glyphs.py tests/test_cue_strict.py tests/test_composition_qa.py -v`
Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 12: Commit**

```bash
git add packages/core/src/video_editor_core/glyphs.py \
        packages/core/src/video_editor_core/cues.py \
        packages/core/src/video_editor_core/safe_area.py \
        apps/cli/src/video_editor_cli/composition_qa.py \
        apps/cli/src/video_editor_cli/explainer_qa.py \
        apps/cli/src/video_editor_cli/explainer_pipeline.py \
        tests/test_glyphs.py tests/test_cue_strict.py tests/test_composition_qa.py
git commit -m "feat(qa): add glyph, cue, safe-area, composition and visual gates"
```

**Post-review decisions (Task 13 review)**

1. **The safe-area gate is inert on the scene-plan path — accepted and recorded.** `composition_safe_area_violations` filters to `text` / `rich_text`, and `from_scene_plan` emits only a full-canvas `html` layer per typographic scene, whose text is laid out by CSS flex with no per-text pixel box in the model. Rather than fabricate boxes or fail every build, the gate stays as built and the limitation is recorded in the design doc §11: it starts measuring when slice 2 puts captions/chips/pills/end cards into the composition as placed layers, and rendered overflow is covered today by the `composition` gate (`hyperframes check`'s layout pass). `gather_composition_qa` must say this in a comment, not silently return `[]`.
2. **A missing `work_dir/composition` must fail, not pass.** Replace the plan's bare `if composition_dir.is_dir():` with an `else` that appends `QaCheck("composition_gates", False, "no composition project to gate")`. The explainer CLI test stubs must create the directory (or stub `gather_composition_qa`) so the fake pipeline stays green.
3. **The gatherer and `extra` path need tests.** The plan shipped no test for `gather_composition_qa`, `frame_stddev`, or `evaluate_qa(..., extra)`; add them, monkeypatching `check_project` / `snapshot_project` / `frame_stddev` so no test spawns the real CLI or FFmpeg.

---

## Task 14: Offline parity rebuilds

**Files:**
- Create: `scripts/parity_rebuild.py`
- Modify: `.gitignore` (add `qa/`)
- Test: `tests/test_parity_rebuild.py`

**Interfaces:**
- Consumes: `render_scene_plan`, the saju build's `overlays.py`, FFmpeg/FFprobe.
- Produces: `ssim(reference, candidate, *, start=None, duration=None) -> float`; `scene_windows(timeline: dict) -> list[tuple[float, float]]`; `main(argv=None) -> int` returning 0 only when every assertion passes.

Reference renders (verified present):

| Case | Reference | Rebuild |
|---|---|---|
| scene plan | `projects/wiz-explainer/composition/rendered.mp4` | `render_scene_plan` on `projects/wiz-explainer/plan/scene_plan.json` |
| render graph | `projects/saju-january-animal/hyperframes/captioned.mp4` | `videos/2026-10-23/saju-january-animal/build/overlays.py` |

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the parity harness helpers (no rendering)."""

from __future__ import annotations

import pytest
from scripts.parity_rebuild import scene_windows, within_one_frame


def test_scene_windows_come_from_the_timeline():
    timeline = {"scenes": [{"start": 0.0, "end": 3.0}, {"start": 3.0, "end": 5.5}]}
    assert scene_windows(timeline) == [(0.0, 3.0), (3.0, 5.5)]


def test_scene_windows_ignore_an_empty_timeline():
    assert scene_windows({}) == []


def test_one_frame_tolerance_accepts_rounding():
    assert within_one_frame(5.0, 5.033, 30)
    assert not within_one_frame(5.0, 5.1, 30)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_a_non_finite_duration_is_not_accepted(bad):
    assert not within_one_frame(5.0, bad, 30)
```

- [ ] **Step 2: Run them to watch them fail**

Run: `uv run pytest tests/test_parity_rebuild.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.parity_rebuild'`.

- [ ] **Step 3: Write `scripts/parity_rebuild.py`**

```python
"""Rebuild two shipped videos from their saved inputs and compare frames.

Both cases are fully offline: they read a saved scene plan, stills, narration
and a rendered base video, and use only HyperFrames and FFmpeg. No provider is
called and nothing is spent.

    uv run python scripts/parity_rebuild.py
    uv run python scripts/parity_rebuild.py --case scene-plan

Exit status is 0 only when every assertion passes. If SSIM misses, fix the
primitive defaults or the adapter mapping — never the threshold.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIZ = ROOT / "projects" / "wiz-explainer"
SAJU = ROOT / "projects" / "saju-january-animal"
SAJU_BUILD = ROOT / "videos" / "2026-10-23" / "saju-january-animal" / "build"
WORK = ROOT / "qa" / "parity"

WHOLE_MIN_SSIM = 0.98
SCENE_MIN_SSIM = 0.95
FPS = 30


def within_one_frame(expected: float, actual: float, fps: int = FPS) -> bool:
    if not (math.isfinite(expected) and math.isfinite(actual)):
        return False
    return abs(expected - actual) <= 1.0 / fps + 1e-6


def scene_windows(timeline: dict) -> list[tuple[float, float]]:
    return [
        (float(scene["start"]), float(scene["end"]))
        for scene in timeline.get("scenes", []) or []
        if "start" in scene and "end" in scene
    ]


def ssim(
    reference: Path,
    candidate: Path,
    *,
    start: float | None = None,
    duration: float | None = None,
) -> float:
    def _input(path: Path) -> list[str]:
        if start is None:
            return ["-i", str(path)]
        return ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(path)]

    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "info",
        *_input(reference), *_input(candidate),
        "-lavfi", "[0:v][1:v]ssim", "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    for line in (result.stderr or "").splitlines():
        if "SSIM" in line and "All:" in line:
            return float(line.split("All:", 1)[1].split()[0])
    raise RuntimeError(f"no SSIM produced for {reference} vs {candidate}")


def duration_of(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def copy_references() -> Path:
    """Copy the reference renders so a rebuild can never overwrite them."""
    target = WORK / "reference"
    target.mkdir(parents=True, exist_ok=True)
    pairs = (
        (WIZ / "composition" / "rendered.mp4", "wiz-rendered.mp4"),
        (SAJU / "hyperframes" / "captioned.mp4", "saju-captioned.mp4"),
        (SAJU / "hyperframes" / "rendered.mp4", "saju-rendered.mp4"),
    )
    for source, name in pairs:
        destination = target / name
        if not destination.exists():
            if not source.is_file():
                raise FileNotFoundError(f"missing parity input: {source}")
            shutil.copyfile(source, destination)
            print(f"reference: {destination.name}")
    return target


def run_scene_plan_case(reference: Path) -> dict:
    from video_editor_core.scene_plan_composition import render_scene_plan

    work = WORK / "scene-plan"
    work.mkdir(parents=True, exist_ok=True)
    output = work / "rendered.mp4"
    render_scene_plan(WIZ / "plan" / "scene_plan.json", output, work / "project")
    plan = json.loads((WIZ / "plan" / "scene_plan.json").read_text(encoding="utf-8"))
    return {
        "name": "scene-plan",
        "reference": reference / "wiz-rendered.mp4",
        "candidate": output,
        "windows": _windows_from_plan(plan),
    }


def run_render_graph_case(reference: Path) -> dict:
    work = WORK / "render-graph"
    work.mkdir(parents=True, exist_ok=True)
    # overlays.py rewrites captioned.mp4 in place; the reference copy is made.
    subprocess.run([sys.executable, str(SAJU_BUILD / "overlays.py")], check=True, cwd=SAJU_BUILD)
    candidate = work / "captioned.mp4"
    shutil.copyfile(SAJU / "hyperframes" / "captioned.mp4", candidate)
    timeline = json.loads(
        (SAJU / "assets" / "tts" / "timeline.json").read_text(encoding="utf-8")
    )
    return {
        "name": "render-graph",
        "reference": reference / "saju-captioned.mp4",
        "candidate": candidate,
        "windows": scene_windows(timeline),
    }


def _windows_from_plan(plan: dict) -> list[tuple[float, float]]:
    windows: list[tuple[float, float]] = []
    cursor = 0.0
    for scene in plan.get("scenes", []) or []:
        length = float(scene.get("duration_seconds", 0.0))
        if length <= 0:
            continue
        windows.append((cursor, cursor + length))
        cursor += length
    return windows


def check(case: dict) -> bool:
    reference = case["reference"]
    candidate = case["candidate"]
    name = case["name"]
    ok = True

    if not within_one_frame(duration_of(reference), duration_of(candidate)):
        print(f"[{name}] FAIL duration {duration_of(candidate):.3f} vs {duration_of(reference):.3f}")
        ok = False
    else:
        print(f"[{name}] duration {duration_of(candidate):.3f}s")

    whole = ssim(reference, candidate)
    print(f"[{name}] whole-video SSIM {whole:.4f} (min {WHOLE_MIN_SSIM})")
    ok = ok and whole >= WHOLE_MIN_SSIM

    for index, (start, end) in enumerate(case["windows"]):
        score = ssim(reference, candidate, start=start, duration=max(0.1, end - start))
        print(f"[{name}] scene {index} [{start:.2f}-{end:.2f}] SSIM {score:.4f}")
        ok = ok and score >= SCENE_MIN_SSIM

    print(f"[{name}] {'PASS' if ok else 'FAIL'}")
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--case", choices=["scene-plan", "render-graph", "all"], default="all"
    )
    args = parser.parse_args(argv)

    reference = copy_references()
    cases = []
    if args.case in {"scene-plan", "all"}:
        cases.append(run_scene_plan_case(reference))
    if args.case in {"render-graph", "all"}:
        cases.append(run_render_graph_case(reference))
    return 0 if all(check(case) for case in cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

Create `scripts/__init__.py` if it does not exist so the test can import the module.

- [ ] **Step 4: Keep the parity output out of git**

Add to `.gitignore`:

```
qa/
```

- [ ] **Step 5: Run the helper tests**

Run: `uv run pytest tests/test_parity_rebuild.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Run the scene-plan parity case**

Run: `uv run python scripts/parity_rebuild.py --case scene-plan`
Expected: whole-video SSIM ≥ 0.98 and every scene ≥ 0.95 — the first run of the unified compiler against a real video.

If it misses, the cause is one of: the `drift` move indexing (must be `index % 4`), the `ease-in-out` on stills, or the object-fit on the still. Fix the adapter or primitive; do not lower the threshold.

- [ ] **Step 7: Run the render-graph parity case**

Run: `uv run python scripts/parity_rebuild.py --case render-graph`
Expected: whole-video SSIM ≥ 0.98 and every scene ≥ 0.95, with captions in the same places.

- [ ] **Step 8: Run both and the whole suite**

Run: `uv run python scripts/parity_rebuild.py`
Run: `uv run pytest -q`
Expected: both cases PASS; suite green.

- [ ] **Step 9: Commit**

```bash
git add scripts/parity_rebuild.py scripts/__init__.py tests/test_parity_rebuild.py .gitignore
git commit -m "test(parity): rebuild two shipped videos from saved inputs"
```

---

## Self-Review

**Spec coverage**

| Spec requirement | Task |
|---|---|
| §5.1 `Composition` model, contiguity, `audio_mode`, quantised times | 4 |
| §5.2 one compiler, dedupe key, root-level clips, scene grouping only | 6 |
| §5.3 both adapters, old modules delegate | 7, 8 |
| §5.4 every primitive, `drift` == `_KB_MOVES`, clamping, unknown-name error | 1, 2, 3 |
| §5.5 `DesignTokens.layers`, no second brand-kit concept | 9 |
| §5.6 Cormorant + Noto Serif KR subsets, manifest, glyph gate | 11, 13 |
| §5.7 profiles incl. `vertical_short_strict` | 10 |
| §5.8 five gates + `check_project` / `snapshot_project` wrappers | 12, 13 |
| §7.1 machinery items 1-7 | 4-13 |
| §7.1 parity, two cases, SSIM thresholds, offline, cost abort | 14 |
| §8 unit / model / adapter / gate / integration tests | 1-14 |

The spec's "non-zero cost estimate aborts the script" is satisfied structurally: the harness has no provider call at all, so the estimate is always zero. The script's docstring states it; no runtime check is needed because there is nothing to check.

**Deviations from the spec text (intentional, both are simplifications found while planning)**

1. The layer `kind` list drops `group` and adds `html`. `group` had no emitter and no caller; `html` is needed to carry the existing typographic scene markup without rewriting five layouts, and it is engine-generated, never user text.
2. A layer's engine-generated stylesheet travels in `style["extra_css"]` rather than a new model field, because it belongs to exactly one kind and adding a top-level field for it would widen every adapter.

Both are recorded here; the spec's §5.1 code block is edited to match before implementation starts.

**Placeholder scan:** no "TBD", no "add error handling", no "similar to Task N" — every step carries the code or the exact command.

**Type consistency:** `layer_id` (Task 7) is the only id builder; `build_motion` (Task 1) is the only entry point used in Tasks 6-13; `canvas_for_aspect` lives in `composition.py` (Task 4) and is re-exported by `scene_plan_composition` (Task 8); `QaCheck` is defined in `explainer_qa` and imported, never redefined.

---

## Post-review decisions (final review)

1. **The cue gate was extended beyond Task 13 Step 7's shape.** Task 13 checked
   only `props["cue"]`, which no explainer plan writes; on the shipped
   `projects/wiz-explainer/plan/scene_plan.json` it therefore reported nothing
   while the renderer's own big-stat, bar-label, definition-term and
   sequence-step cues went unchecked. At the human partner's decision the gate
   now reads the cue map `render_typographic` builds (via a shared dispatch and
   the public `scene_cues`) and checks every cue against the scene's
   `word_times`, failing closed when a layout cannot be read. The existing
   `props["cue"]` check is retained and a phrase reported by both paths is
   listed once.

2. **Parity scope is recorded in spec §7.1.** The harness asserts the five
   composition gates on the scene-plan rebuild and caption span count and
   animation delays on the render-graph rebuild. The measured-file checks
   (`explainer_qa`) never touch the compiler — they measure the finished MP4 —
   so they stay covered by that module's own tests rather than re-running in
   the harness.

3. **The extended cue gate found two real content drifts, and the content was
   fixed.** With the gate reading the renderer's cue map, the shipped
   `projects/wiz-explainer/plan/scene_plan.json` reported
   `scene_3: 'Wiz'` (a bar label the narration never speaks) and
   `scene_6: 'Breaches start with a wrong key.'` (the voice says "breach
   starts with one wrong key"). `bar_compare` bars and `sequence` steps may now
   carry an explicit `cue` naming the spoken phrase; the plan sets it for the
   first bar and the step's wording was corrected. **The reference render was
   not replaced.** The corrected plan rebuilds against the pre-migration
   artifact at whole-video SSIM 0.9960 (worst scene 0.9886), so the case still
   compares the unified compiler against the video that shipped rather than
   against its own output; re-rendering the baseline would have made the
   scene-plan SSIM 1.0000 and the assertion circular. The pre-migration
   reference is kept at `qa/parity/reference/wiz-rendered-premigration.mp4`
   (git-ignored scratch) for comparison.
