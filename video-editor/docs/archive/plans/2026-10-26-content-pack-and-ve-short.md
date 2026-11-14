# Content Pack and `ve short` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Cosmic Saju short end to end from a committed YAML content pack, through one `Composition`, one render and one QA pass — no per-project Python.

**Architecture:** A pack (`shorts/<slug>/short.yaml`) carries words and asset prompts only; a series design (`designs/saju_series.json`) carries every styling decision. `adapters/from_content_pack.py` turns a pack plus its design into the slice-1 `Composition` IR with placed scrim / chip / pill / caption / end-card layers, `compile_composition` emits one HyperFrames project, and `ve short` drives validate → cost gate → stills → TTS → alignment → compile → render → QA.

**Tech Stack:** Python 3.12, pydantic v2, Typer, PyYAML, HyperFrames CLI v0.8.186, FFmpeg, pytest.

## Global Constraints

Copied verbatim from the spec (`docs/archive/specs/2026-10-23-hyperframes-composition-architecture-design.md`); every task's requirements implicitly include this section.

- Canvas 1080 × 1920 at 30 fps; aspect `9:16`; three-decimal times.
- `vertical_short_strict` = insets `top 0.1146, right 0.08, bottom 0.25, left 0.08` (220 / 86 / 480 px at 1080×1920). Bottom safe edge is y = 1440; a caption box must top out at ≤ 1260 for 180 px of height.
- Fonts are restricted to `BUNDLED_FONTS`: `Inter`, `Montserrat`, `Cormorant Garamond`, `Noto Serif KR`. `Noto Sans KR` is **not** bundled and must not appear in a design.
- A pack carries **no styling keys**: colours, fonts, safe area, pill/chip/end-card shape and caption preset come from the design.
- Cue semantics are slice 1's: `cues.find_cue(words, phrase, prefix=True)` with per-token prefix matching; a cue that matches no spoken word fails the build.
- The karaoke caption preset stays the default caption style.
- No test may make a network call or spend money. Provider calls in tests are always faked.
- Every request the build makes must be priceable: an unpriced provider is refused, never assumed free. `models.yaml` is the only place a price is written, and `pricing.yaml` is generated from it.
- `uv run mypy apps packages` must stay clean (`no_implicit_reexport`: re-exports need `X as X`; `# noqa: F401` is ruff-only).
- HyperFrames output contract is unchanged: root `div[data-composition-id=main][data-no-timeline]`, media as direct root children with `data-start` / `data-duration` / `data-track-index` / `class="clip"` and explicit `z-index`, no double quotes in inline styles, no JS-dependent motion.
- Commits end with `Co-Authored-By: Claude Code <noreply@anthropic.com>`.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `shorts/<slug>/short.yaml` | One video's words and asset prompts (tracked source) |
| `designs/saju_series.json` | Series identity: palette, type, motion, `layers` block |
| `packages/core/src/video_editor_core/content_pack.py` | Pack and alignment pydantic models + `load_content_pack` |
| `packages/core/src/video_editor_core/layer_presets.py` | design `layers` block → placed rects and style dicts |
| `packages/core/src/video_editor_core/adapters/from_content_pack.py` | pack + design + alignment → `Composition` (stills, scrims, chip, pills, end card) |
| `apps/cli/src/video_editor_cli/short_captions.py` | alignment → karaoke `rich_text` caption layers (CLI-only: imports the captions skill) |
| `apps/cli/src/video_editor_cli/short_pipeline.py` | `ve short` stage driver (validate → gate → stills → TTS → align → compile → render → QA) |
| `apps/cli/src/video_editor_cli/main.py` | `ve short` command declaration |
| `tests/test_content_pack.py` | Pack and alignment validation |
| `tests/test_designs.py` | `Layers.caption_line` and the shipped design |
| `tests/test_layer_presets.py` | Preset styles, safe-area compliance |
| `tests/test_from_content_pack.py` | Adapter: layer mapping, rects, motions, cues |
| `tests/test_short_captions.py` | Caption layer rebasing and overlap clamp |
| `tests/test_short_pipeline.py` | Requests, preflight, cost gate, stills, voice, alignment, stage order |
| `tests/test_short_e2e.py` | The offline proof: stub providers + canned transcript → a gated MP4 |
| `tests/test_saju_v1_pack.py` | The committed pack's contract and authoring guards |
| `tests/fixtures/shorts/e2e-pack/` | Offline pack + canned transcript (no stills: `stub_image` draws them) |
| `shorts/saju-v1-your-animal/short.yaml` | Video 1 of the series (tracked source) |
| `designs/glyphs/noto_serif_kr.json` | Declared CJK glyphs; the bundle draws only these |

---

## Tasks

### Task 1: Pack model, alignment model, loader

**Files:**
- Create: `packages/core/src/video_editor_core/content_pack.py`
- Test: `tests/test_content_pack.py`

**Interfaces:**
- Produces: `ContentPack`, `PackVoice`, `PackImage`, `PackStill`, `PackLine`, `PackScene`, `PackEndCard`, `TimedWord`, `TimedScene`, `Alignment`, `ContentPackError`, `END_CARD_ID`, `load_content_pack(path) -> ContentPack`, `still_specs(pack) -> list[PackStill]`, `still_for(pack, scene_id) -> str`, `cue_phrases(pack) -> dict[str, str]`, `spoken_lines(pack) -> list[tuple[str, str]]`.
- `Alignment` mirrors `Composition`'s tiling rule, so the adapter's scene windows always satisfy `Composition._scenes_are_contiguous`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_content_pack.py`:

```python
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from video_editor_core.content_pack import (
    END_CARD_ID,
    Alignment,
    ContentPackError,
    PackStill,
    TimedScene,
    TimedWord,
    cue_phrases,
    load_content_pack,
    spoken_lines,
    still_for,
    still_specs,
)

MINIMAL = """\
job_id: demo-short
design: saju_series
aspect: "9:16"
voice:
  voice_id: "sarvam:sarang_en_narration"
  gap_seconds: 0.28
image:
  provider: stub_image
  style_suffix: "ink wash"
end_card:
  say: "Find yours free at cosmicsaju.com."
  text: "Free chart snapshot"
scenes:
  - id: hook
    still:
      key: rat
      prompt: "A rat on a plum branch"
    lines:
      - say: "You think you're a Rat?"
        show: "You're not just a Rat."
        cue: "Rat"
  - id: branch
    still: {reuse: rat}
    lines:
      - say: "Your animal is the year branch."
        speak_as: "Your animal is the year bunch."
"""


def write_pack(tmp_path: Path, body: str = MINIMAL) -> Path:
    path = tmp_path / "short.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_load_content_pack_reads_voice_scenes_and_end_card(tmp_path: Path) -> None:
    pack = load_content_pack(write_pack(tmp_path))
    assert pack.job_id == "demo-short"
    assert pack.voice.voice_id == "sarvam:sarang_en_narration"
    assert [scene.id for scene in pack.scenes] == ["hook", "branch"]
    assert pack.end_card.text == "Free chart snapshot"


def test_spoken_text_prefers_speak_as(tmp_path: Path) -> None:
    pack = load_content_pack(write_pack(tmp_path))
    assert pack.scenes[1].lines[0].spoken == "Your animal is the year bunch."


def test_a_still_needs_exactly_one_of_key_or_reuse() -> None:
    with pytest.raises(ValidationError):
        PackStill()
    with pytest.raises(ValidationError):
        PackStill(key="rat", prompt="x", reuse="rat")
    with pytest.raises(ValidationError):
        PackStill(reuse="rat", prompt="a prompt the alias cannot carry")


def test_a_reuse_before_its_generated_still_is_rejected(tmp_path: Path) -> None:
    body = MINIMAL.replace("still:\n      key: rat", "still: {reuse: gem}\n    unused:\n      key: rat")
    with pytest.raises(ContentPackError, match="reuses still 'gem'"):
        load_content_pack(write_pack(tmp_path, body))


def test_a_pack_cannot_carry_styling(tmp_path: Path) -> None:
    body = MINIMAL.replace("aspect:", "layers:\n  safe_area: vertical_short_strict\naspect:")
    with pytest.raises(ContentPackError, match="extra_forbidden|layers"):
        load_content_pack(write_pack(tmp_path, body))


def test_load_reports_the_path_of_a_broken_pack(tmp_path: Path) -> None:
    path = write_pack(tmp_path, "job_id: [oops")
    with pytest.raises(ContentPackError, match=str(path)):
        load_content_pack(path)


def test_still_specs_are_ordered_and_aliases_resolve(tmp_path: Path) -> None:
    pack = load_content_pack(write_pack(tmp_path))
    assert [still.key for still in still_specs(pack)] == ["rat"]
    assert still_for(pack, "branch") == "rat"


def test_cue_phrases_key_each_line(tmp_path: Path) -> None:
    pack = load_content_pack(write_pack(tmp_path))
    assert cue_phrases(pack) == {"hook:0": "Rat"}


def test_spoken_lines_put_the_end_card_last(tmp_path: Path) -> None:
    pack = load_content_pack(write_pack(tmp_path))
    assert spoken_lines(pack) == [
        ("hook", "You think you're a Rat?"),
        ("branch", "Your animal is the year bunch."),
        (END_CARD_ID, "Find yours free at cosmicsaju.com."),
    ]


def test_an_unknown_aspect_is_refused_at_load() -> None:
    # ``canvas_for_aspect`` would silently render a "9x16" pack at 1920×1080.
    with pytest.raises(ValidationError, match="aspect"):
        ContentPack.model_validate({**PACK, "aspect": "9x16"})


def test_alignment_scenes_must_tile_the_duration() -> None:
    words = [TimedWord(text="hi", start=0.0, end=0.4)]
    alignment = Alignment(
        duration=1.0,
        scenes=[TimedScene(id="hook", start=0.0, end=1.0, words=words)],
    )
    assert [w["word"] for w in alignment.word_dicts()] == ["hi"]
    with pytest.raises(ValidationError, match="expected 1.000"):
        Alignment(
            duration=2.0,
            scenes=[TimedScene(id="hook", start=0.0, end=1.0, words=words)],
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_content_pack.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'video_editor_core.content_pack'`.

- [ ] **Step 3: Implement `content_pack.py`**

Create `packages/core/src/video_editor_core/content_pack.py`:

```python
"""A committed content pack: one video's words and asset prompts.

A pack is the authoring surface for a short. It carries narration, on-screen
text, image prompts and the voice choice — never a colour, a font or a rect.
Styling lives in the series design (``designs/<name>.json``) and reaches the
compiler through :mod:`video_editor_core.layer_presets`.

The alignment models live here too because the adapter consumes both: an
:class:`Alignment` is the measured timeline the pack's words were spoken on.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from video_editor_core.motion import PRIMITIVES

#: Reserved scene id for the end card; a pack scene may not take it.
END_CARD_ID = "end_card"

#: The aspects ``canvas_for_aspect`` knows. Closed here because that helper
#: falls back to 16:9 for anything unrecognised: a pack typo would otherwise
#: render a vertical short as a 1920×1080 landscape file and pass every gate.
Aspect = Literal["9:16", "16:9", "1:1", "4:5", "4:3"]

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class ContentPackError(ValueError):
    """A pack that cannot be read or breaks the pack contract."""


class PackVoice(BaseModel):
    """Which voice reads the narration, and the beat between scene lines."""

    model_config = ConfigDict(extra="forbid")

    provider: str = "sarvam_tts"
    #: Provider-specific voice id. Sarvam takes ``sarvam:<speaker>``.
    voice_id: str = Field(min_length=1)
    model: str = ""
    language: str = "en-IN"
    gap_seconds: float = Field(default=0.28, ge=0.0, le=2.0)


class PackImage(BaseModel):
    """The still provider and the style suffix appended to every prompt."""

    model_config = ConfigDict(extra="forbid")

    provider: str = "poyo_ai"
    model: str = ""
    resolution: str = ""
    style_suffix: str = ""


class PackStill(BaseModel):
    """Either a still to generate (``key`` + ``prompt``) or an alias to one."""

    model_config = ConfigDict(extra="forbid")

    key: str = ""
    prompt: str = ""
    reuse: str = ""

    @model_validator(mode="after")
    def _generate_xor_reuse(self) -> PackStill:
        if bool(self.key) == bool(self.reuse):
            raise ValueError(
                "a still needs exactly one of `key:` (generate) or `reuse:` (alias)"
            )
        if self.key:
            if not _KEY_RE.match(self.key):
                raise ValueError(
                    f"still key {self.key!r} must be lowercase letters, digits "
                    "and underscores"
                )
            if not self.prompt.strip():
                raise ValueError(f"still {self.key!r} has no prompt")
        elif self.prompt.strip():
            raise ValueError(
                f"still alias {self.reuse!r} cannot carry a prompt; reuse is free"
            )
        return self


class PackLine(BaseModel):
    """One spoken line and, optionally, the words it puts on screen."""

    model_config = ConfigDict(extra="forbid")

    say: str = Field(min_length=1)
    show: str = ""
    cue: str = ""
    speak_as: str = ""

    @property
    def spoken(self) -> str:
        """What the voice reads: ``speak_as`` when set, else ``say``."""
        return (self.speak_as or self.say).strip()


class PackScene(BaseModel):
    """One still, its lines, and the ambient motion the still drifts with."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    still: PackStill
    chip: str = ""
    motion: str = "drift"
    lines: list[PackLine] = Field(min_length=1)

    @model_validator(mode="after")
    def _motion_is_known(self) -> PackScene:
        if self.motion not in PRIMITIVES:
            raise ValueError(
                f"scene {self.id!r}: unknown motion {self.motion!r}; known: "
                f"{', '.join(sorted(PRIMITIVES))}"
            )
        return self


class PackEndCard(BaseModel):
    """The closing card: one spoken line, the on-screen text, and the tail."""

    model_config = ConfigDict(extra="forbid")

    say: str = ""
    text: str = ""
    url: str = "cosmicsaju.com"
    #: Silence held after the last spoken word. The QA tail gate accepts
    #: 0.8–2.0 s, so the window is closed here rather than discovered later.
    tail_seconds: float = Field(default=1.2, ge=0.8, le=2.0)


class ContentPack(BaseModel):
    """One video's full authoring surface."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    design: str = "saju_series"
    aspect: Aspect = "9:16"
    voice: PackVoice
    image: PackImage = Field(default_factory=PackImage)
    end_card: PackEndCard = Field(default_factory=PackEndCard)
    scenes: list[PackScene] = Field(min_length=1)

    @model_validator(mode="after")
    def _scene_ids_are_unique(self) -> ContentPack:
        seen: set[str] = set()
        for scene in self.scenes:
            if scene.id == END_CARD_ID:
                raise ValueError(f"scene id {END_CARD_ID!r} is reserved for the end card")
            if scene.id in seen:
                raise ValueError(f"duplicate scene id {scene.id!r}")
            seen.add(scene.id)
        return self

    @model_validator(mode="after")
    def _reuse_follows_its_still(self) -> ContentPack:
        generated: set[str] = set()
        for scene in self.scenes:
            if scene.still.key:
                generated.add(scene.still.key)
            elif scene.still.reuse not in generated:
                raise ValueError(
                    f"scene {scene.id!r} reuses still {scene.still.reuse!r} "
                    "before any scene generates it"
                )
        return self


class TimedWord(BaseModel):
    """One spoken word on the absolute timeline."""

    model_config = ConfigDict(extra="forbid")

    text: str
    start: float = Field(ge=0)
    end: float = Field(ge=0)


class TimedScene(BaseModel):
    """A scene's window and the words spoken inside it."""

    model_config = ConfigDict(extra="forbid")

    id: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    words: list[TimedWord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _window(self) -> TimedScene:
        if self.end <= self.start:
            raise ValueError(f"scene {self.id!r} ends at {self.end:.3f} but starts at {self.start:.3f}")
        return self


class Alignment(BaseModel):
    """The measured timeline: contiguous scene windows tiling the duration."""

    model_config = ConfigDict(extra="forbid")

    duration: float = Field(gt=0)
    scenes: list[TimedScene] = Field(min_length=1)

    @model_validator(mode="after")
    def _scenes_tile_the_duration(self) -> Alignment:
        # Same rule as ``Composition._scenes_are_contiguous``: the adapter maps
        # these windows straight onto composition scenes, so a gap here would
        # only fail later, in the compiler.
        cursor = 0.0
        for scene in self.scenes:
            if abs(scene.start - cursor) > 1e-3:
                raise ValueError(
                    f"scene {scene.id!r} starts at {scene.start:.3f}s, expected "
                    f"{cursor:.3f}s"
                )
            cursor = scene.end
        if abs(cursor - self.duration) > 1e-3:
            raise ValueError(
                f"scenes cover {cursor:.3f}s but the duration is {self.duration:.3f}s"
            )
        return self

    def word_dicts(self) -> list[dict[str, Any]]:
        """The flat word stream ``cues.find_cue`` expects."""
        return [
            {"word": word.text, "start": word.start, "end": word.end}
            for scene in self.scenes
            for word in scene.words
        ]


def load_content_pack(path: Path | str) -> ContentPack:
    """Read ``short.yaml`` and validate it; every failure names the file."""
    source = Path(path)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ContentPackError(f"pack {source} cannot be read: {exc}") from exc
    if not isinstance(raw, dict):
        raise ContentPackError(f"pack {source} must be a YAML mapping")
    try:
        return ContentPack.model_validate(raw)
    except ValidationError as exc:
        raise ContentPackError(f"pack {source} is invalid:\n{exc}") from exc


def still_specs(pack: ContentPack) -> list[PackStill]:
    """The stills to generate, in first-appearance order, one per key."""
    specs: list[PackStill] = []
    seen: set[str] = set()
    for scene in pack.scenes:
        if scene.still.key and scene.still.key not in seen:
            specs.append(scene.still)
            seen.add(scene.still.key)
    return specs


def still_for(pack: ContentPack, scene_id: str) -> str:
    """The generated still key a scene renders with (aliases resolved)."""
    aliases = {
        scene.id: scene.still.reuse
        for scene in pack.scenes
        if scene.still.reuse
    }
    generated = {scene.still.key for scene in pack.scenes if scene.still.key}
    for scene in pack.scenes:
        if scene.id != scene_id:
            continue
        key = scene.still.key or scene.still.reuse
        resolved = aliases.get(key, key)
        if resolved not in generated:
            raise ContentPackError(f"still {key!r} does not resolve to a generated still")
        return resolved
    raise ContentPackError(f"unknown scene {scene_id!r}")


def cue_phrases(pack: ContentPack) -> dict[str, str]:
    """``{"<scene_id>:<line_index>": phrase>`` for every declared cue."""
    return {
        f"{scene.id}:{index}": line.cue
        for scene in pack.scenes
        for index, line in enumerate(scene.lines)
        if line.cue
    }


def spoken_lines(pack: ContentPack) -> list[tuple[str, str]]:
    """Every narrated line in order, with the end card last."""
    lines = [
        (scene.id, line.spoken)
        for scene in pack.scenes
        for line in scene.lines
    ]
    if pack.end_card.say.strip():
        lines.append((END_CARD_ID, pack.end_card.say.strip()))
    return lines
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_content_pack.py -v`
Expected: `11 passed`.

- [ ] **Step 5: Run the type and lint gates**

Run: `uv run mypy apps packages && uv run ruff check`
Expected: `Success: no issues found in 187 source files` and `All checks passed!`.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/video_editor_core/content_pack.py tests/test_content_pack.py
git commit -m "feat(core): load a content pack and its measured alignment

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: `Layers.caption_line` and the Saju series design

**Files:**
- Modify: `packages/core/src/video_editor_core/designs.py:74-92`
- Create: `designs/saju_series.json`
- Modify: `tests/test_designs.py`

**Interfaces:**
- Produces: `Layers.caption_line: dict[str, Any]` (default empty). Task 3 reads it via `DesignTokens.layers.caption_line`.
- The shipped design is the only place the series' colours, fonts and rects exist.

**Why a new field:** `Layers` allows extra keys, so `caption_line` currently lands in `model_extra` and is invisible to type-checked code. Naming it makes the compiler's caption styling a first-class part of the design contract.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_designs.py`:

```python
def test_layers_carry_the_caption_line_block() -> None:
    layers = Layers(caption_line={"font_size": 64, "box": {"y": 1240}})
    assert layers.caption_line["font_size"] == 64
    assert layers.caption_line["box"] == {"y": 1240}


def test_layers_default_to_no_caption_line() -> None:
    assert Layers().caption_line == {}


def test_the_saju_design_is_a_valid_short_design() -> None:
    tokens = load_design("saju_series")
    assert tokens.layers.safe_area == "vertical_short_strict"
    assert tokens.layers.caption_preset == "karaoke"
    assert tokens.type.display == "Cormorant Garamond"
    assert tokens.type.body == "Inter"
    box = tokens.layers.caption_line["box"]
    # 1240 + 180 = 1420, inside the 1440 safe bottom edge, and 1240 <= 1260.
    assert box["y"] + box["height"] <= 1440
    assert box["y"] <= 1260
    assert tokens.layers.end_card["url_font_size"] == 36
    assert "Noto Sans KR" not in {tokens.type.display, tokens.type.body}
```

Add `Layers`, `load_design` to the imports at the top of `tests/test_designs.py` if they are not already there (the file already imports both for its existing tests; check before adding).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_designs.py -v -k "caption_line or saju"`
Expected: FAIL — `AttributeError: 'Layers' object has no attribute 'caption_line'`, and `DesignError: unknown design 'saju_series'`.

- [ ] **Step 3: Add the field to `Layers`**

In `packages/core/src/video_editor_core/designs.py`, inside `class Layers` after `pill`:

```python
    #: The karaoke caption band: font, size, highlight colour and box. Named
    #: rather than left to ``extra`` so the compiler reads a typed contract.
    caption_line: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 4: Write `designs/saju_series.json`**

Create `designs/saju_series.json`:

```json
{
  "name": "saju_series",
  "palette": {
    "bg": "#0F0B1E",
    "ink": "#F5F3FF",
    "accent": "#F59E0B",
    "muted": "#B9B3D0"
  },
  "type": {
    "display": "Cormorant Garamond",
    "body": "Inter",
    "display_weight": 600
  },
  "theme": "dark",
  "motion": {
    "tempo": 1.0,
    "entry": "rise",
    "drift": 1.08
  },
  "layers": {
    "safe_area": "vertical_short_strict",
    "caption_preset": "karaoke",
    "scrim": {
      "top_height": 320,
      "bottom_height": 620,
      "top": "linear-gradient(#0F0B1E 0%, rgba(15,11,30,0.72) 55%, rgba(15,11,30,0) 100%)",
      "bottom": "linear-gradient(rgba(15,11,30,0) 0%, rgba(15,11,30,0.80) 45%, #0F0B1E 100%)"
    },
    "chip": {
      "font": "Noto Serif KR",
      "font_size": 96,
      "font_color": "#F59E0B",
      "y": 220
    },
    "pill": {
      "font": "Inter",
      "font_size": 52,
      "font_weight": 600,
      "font_color": "#F5F3FF",
      "background": "rgba(15,11,30,0.72)",
      "x": 86,
      "width": 908,
      "first_y": 520,
      "gap": 92
    },
    "caption_line": {
      "font": "Inter",
      "font_size": 64,
      "font_weight": 700,
      "font_color": "#F5F3FF",
      "highlight_color": "#F59E0B",
      "upcoming_opacity": 0.6,
      "box": {"x": 86, "y": 1240, "width": 908, "height": 180}
    },
    "end_card": {
      "font": "Cormorant Garamond",
      "font_size": 68,
      "font_weight": 600,
      "font_color": "#F5F3FF",
      "url_color": "#F59E0B",
      "url_font_size": 36,
      "box": {"x": 86, "y": 760, "width": 908, "height": 320}
    }
  }
}
```

Two deliberate omissions, both because the emitter cannot draw them on a text layer:

- the pill's `border` and `radius` — `compile_composition._text_css` emits font and shadow only, and `_layer_html` adds `background`; a border on a text layer would be dead styling;
- the chip's `bar_color` — the chip is one centred text layer, not a rule plus a glyph.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_designs.py -v`
Expected: all design tests pass, including the three new ones.

- [ ] **Step 6: Lint, type-check, commit**

```bash
uv run mypy apps packages && uv run ruff check
git add packages/core/src/video_editor_core/designs.py designs/saju_series.json tests/test_designs.py
git commit -m "feat(design): name the caption-line block and ship the Saju design

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: Layer presets — design block to placed rects

**Files:**
- Create: `packages/core/src/video_editor_core/layer_presets.py`
- Test: `tests/test_layer_presets.py`

**Interfaces:**
- Consumes: `DesignTokens` (Task 2), `Canvas` / `Rect` from the composition IR, `safe_area.safe_box`.
- Produces: `Placed(rect: Rect, style: dict[str, Any], content: str = "")`, `LayerPresetError`, and `scrims(tokens, canvas) -> list[Placed]`, `chip(tokens, canvas) -> Placed | None`, `pill(tokens, canvas, *, index=0) -> Placed`, `caption(tokens, canvas) -> Placed`, `end_card(tokens, canvas) -> tuple[Placed, Placed]`.
- `content` is preset-owned text only (a scrim's gradient). For text presets the adapter supplies the words.

The style dicts must target exactly the keys `compile_composition` reads: `font`, `font_size`, `font_weight`, `font_color`, `text_effect`, `border_width`, `border_color`, `background`, `highlight_color`, `upcoming_opacity`, `radius`, `object_fit`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_layer_presets.py`:

```python
from __future__ import annotations

import pytest

from video_editor_core.composition import Canvas
from video_editor_core.designs import load_design
from video_editor_core.layer_presets import (
    LayerPresetError,
    caption,
    chip,
    end_card,
    pill,
    scrims,
)
from video_editor_core.render_graph import Rect

CANVAS = Canvas(width=1080, height=1920, fps=30)
SAFE = (86, 220, 908, 1220)


@pytest.fixture
def tokens():
    return load_design("saju_series")


def test_scrims_cover_each_edge_full_bleed(tokens) -> None:
    top, bottom = scrims(tokens, CANVAS)
    assert top.rect == Rect(x=0, y=0, width=1080, height=320)
    assert bottom.rect == Rect(x=0, y=1300, width=1080, height=620)
    assert top.content.startswith("linear-gradient(")
    assert top.style["radius"] == 0


def test_chip_sits_inside_the_safe_area(tokens) -> None:
    placed = chip(tokens, CANVAS)
    assert placed is not None
    assert placed.rect.x == SAFE[0]
    assert placed.rect.width == SAFE[2]
    assert placed.rect.y >= SAFE[1]
    assert placed.style["font"] == "Noto Serif KR"
    assert placed.style["font_size"] == 96
    assert placed.style["text_effect"] == "shadow"


def test_pills_stack_down_from_the_first_row(tokens) -> None:
    first = pill(tokens, CANVAS, index=0)
    second = pill(tokens, CANVAS, index=1)
    assert first.rect.y == 520
    assert second.rect.y == 520 + 92
    assert first.rect.width == 908
    assert first.style["background"] == "rgba(15,11,30,0.72)"


def test_caption_box_stays_inside_the_safe_bottom_edge(tokens) -> None:
    placed = caption(tokens, CANVAS)
    assert placed.rect.y + placed.rect.height <= 1440
    assert placed.rect.y <= 1260
    assert placed.style["highlight_color"] == "#F59E0B"
    assert placed.style["upcoming_opacity"] == 0.6


def test_a_caption_box_over_the_safe_edge_is_refused(tokens) -> None:
    tokens.layers.caption_line["box"] = {"x": 86, "y": 1300, "width": 908, "height": 180}
    with pytest.raises(LayerPresetError, match="safe"):
        caption(tokens, CANVAS)


def test_end_card_places_text_and_url_in_two_layers(tokens) -> None:
    text, url = end_card(tokens, CANVAS)
    assert text.rect.y == 760
    assert url.rect.y == text.rect.y + text.rect.height
    assert text.rect.height + url.rect.height == 320
    assert text.style["font"] == "Cormorant Garamond"
    assert url.style["font_size"] == 36
    assert url.style["font_color"] == "#F59E0B"


def test_a_scrim_without_a_gradient_is_refused(tokens) -> None:
    tokens.layers.scrim["top"] = ""
    with pytest.raises(LayerPresetError, match="gradient"):
        scrims(tokens, CANVAS)


def test_an_unknown_safe_area_profile_is_refused(tokens) -> None:
    tokens.layers.safe_area = "no_such_profile"
    with pytest.raises(LayerPresetError, match="no_such_profile"):
        chip(tokens, CANVAS)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_layer_presets.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'video_editor_core.layer_presets'`.

- [ ] **Step 3: Check what `safe_box` does with an unknown profile**

Run: `uv run python -c "from video_editor_core.safe_area import safe_box; print(safe_box(1080, 1920, profile='nope'))"`
Expected: `KeyError` or `ValueError` naming `nope`. The implementation below catches both and re-raises `LayerPresetError`; if the call returns silently, add the name check shown in the code instead of relying on the exception.

- [ ] **Step 4: Implement `layer_presets.py`**

Create `packages/core/src/video_editor_core/layer_presets.py`:

```python
"""Translate a design's ``layers`` block into placed rects and styles.

Every styling decision lives here or in the design JSON. The adapter decides
*when* a preset is on screen; this module decides *where* it sits and what it
looks like. Each function re-checks the safe area, so a design edit that
pushes a box over the platform edge fails at build time rather than in review.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from video_editor_core.composition import Canvas
from video_editor_core.designs import DesignTokens
from video_editor_core.render_graph import Rect
from video_editor_core.safe_area import safe_box

#: The caption band never grows past this: 180 px of two-line Inter at 64 px.
CAPTION_MAX_HEIGHT = 180


class LayerPresetError(ValueError):
    """A design's ``layers`` block cannot produce a valid placement."""


@dataclass(frozen=True)
class Placed:
    """A rect plus the style dict ``compile_composition`` understands."""

    rect: Rect
    style: dict[str, Any]
    #: Preset-owned content (a scrim gradient). Empty for text presets, where
    #: the words come from the pack, never from the design.
    content: str = ""


def _safe(canvas: Canvas, tokens: DesignTokens) -> tuple[int, int, int, int]:
    profile = tokens.layers.safe_area or "auto"
    try:
        return safe_box(canvas.width, canvas.height, profile=profile)
    except (KeyError, ValueError) as exc:
        raise LayerPresetError(
            f"design {tokens.name!r} names unknown safe-area profile {profile!r}"
        ) from exc


def _require_safe(rect: Rect, canvas: Canvas, tokens: DesignTokens, name: str) -> None:
    x, y, width, height = _safe(canvas, tokens)
    inside = (
        rect.x >= x
        and rect.y >= y
        and rect.x + rect.width <= x + width
        and rect.y + rect.height <= y + height
    )
    if not inside:
        raise LayerPresetError(
            f"{name} rect ({rect.x},{rect.y},{rect.width},{rect.height}) leaves the "
            f"safe area ({x},{y},{width},{height}) of design {tokens.name!r}"
        )


def _text_style(
    block: dict[str, Any],
    tokens: DesignTokens,
    *,
    size: int,
    weight: int,
    color: str,
) -> dict[str, Any]:
    """The style keys ``compile_composition._text_css`` reads, and no others."""
    style: dict[str, Any] = {
        "font": str(block.get("font") or tokens.type.body),
        "font_size": int(block.get("font_size") or size),
        "font_weight": int(block.get("font_weight") or weight),
        "font_color": str(block.get("font_color") or color),
        "text_effect": str(block.get("text_effect") or "shadow"),
    }
    if "border_width" in block:
        style["border_width"] = int(block["border_width"])
    if "border_color" in block:
        style["border_color"] = str(block["border_color"])
    return style


def scrims(tokens: DesignTokens, canvas: Canvas) -> list[Placed]:
    """Top and bottom gradient scrims, full-bleed by design, not safe-boxed."""
    block = tokens.layers.scrim
    top_height = int(block.get("top_height") or 0)
    bottom_height = int(block.get("bottom_height") or 0)
    if top_height <= 0 and bottom_height <= 0:
        return []
    if top_height + bottom_height > canvas.height:
        raise LayerPresetError(
            f"scrims total {top_height + bottom_height}px on a "
            f"{canvas.height}px canvas"
        )
    placed: list[Placed] = []
    if top_height > 0:
        placed.append(
            Placed(
                Rect(x=0, y=0, width=canvas.width, height=top_height),
                {"radius": 0},
                str(block.get("top") or ""),
            )
        )
    if bottom_height > 0:
        placed.append(
            Placed(
                Rect(
                    x=0,
                    y=canvas.height - bottom_height,
                    width=canvas.width,
                    height=bottom_height,
                ),
                {"radius": 0},
                str(block.get("bottom") or ""),
            )
        )
    for item in placed:
        if not item.content:
            raise LayerPresetError(
                f"design {tokens.name!r} has a scrim with no gradient; the shape "
                "layer would render as an opaque block"
            )
    return placed


def chip(tokens: DesignTokens, canvas: Canvas) -> Placed | None:
    """The CJK scene glyph, centred at the design's y."""
    block = tokens.layers.chip
    if not block:
        return None
    size = int(block.get("font_size") or 96)
    x, top, width, _ = _safe(canvas, tokens)
    rect = Rect(
        x=x,
        y=int(block.get("y") or top),
        width=width,
        height=int(round(size * 1.5)),
    )
    _require_safe(rect, canvas, tokens, "chip")
    style = _text_style(block, tokens, size=size, weight=600, color=tokens.palette.accent)
    return Placed(rect, style)


def pill(tokens: DesignTokens, canvas: Canvas, *, index: int = 0) -> Placed:
    """The ``show`` line as a filled band; ``index`` stacks down the frame."""
    block = tokens.layers.pill
    if not block:
        raise LayerPresetError(f"design {tokens.name!r} has no pill block")
    size = int(block.get("font_size") or 52)
    x, top, width, _ = _safe(canvas, tokens)
    rect = Rect(
        x=int(block.get("x", x)),
        y=int(block.get("first_y", top)) + index * int(block.get("gap") or 0),
        width=int(block.get("width", width)),
        height=int(block.get("height") or round(size * 1.6)),
    )
    _require_safe(rect, canvas, tokens, f"pill {index}")
    style = _text_style(block, tokens, size=size, weight=600, color=tokens.palette.ink)
    background = str(block.get("background") or "")
    if not background:
        raise LayerPresetError(
            f"design {tokens.name!r} has no pill background; the band would be "
            "unreadable over the still"
        )
    style["background"] = background
    return Placed(rect, style)


def caption(tokens: DesignTokens, canvas: Canvas) -> Placed:
    """The karaoke band, clamped inside the platform's bottom edge."""
    block = tokens.layers.caption_line
    if not block:
        raise LayerPresetError(f"design {tokens.name!r} has no caption_line block")
    box = block.get("box") or {}
    x, top, width, height = _safe(canvas, tokens)
    cap_height = int(box.get("height", CAPTION_MAX_HEIGHT))
    if cap_height > CAPTION_MAX_HEIGHT:
        raise LayerPresetError(
            f"caption box is {cap_height}px tall; the band is capped at "
            f"{CAPTION_MAX_HEIGHT}px"
        )
    rect = Rect(
        x=int(box.get("x", x)),
        y=int(box.get("y", top + height - cap_height)),
        width=int(box.get("width", width)),
        height=cap_height,
    )
    _require_safe(rect, canvas, tokens, "caption")
    style = _text_style(block, tokens, size=64, weight=700, color=tokens.palette.ink)
    style["highlight_color"] = str(block.get("highlight_color") or tokens.palette.accent)
    style["upcoming_opacity"] = float(block.get("upcoming_opacity", 0.6))
    return Placed(rect, style)


def end_card(tokens: DesignTokens, canvas: Canvas) -> tuple[Placed, Placed]:
    """Two stacked text layers: the card's words over its URL."""
    block = tokens.layers.end_card
    if not block:
        raise LayerPresetError(f"design {tokens.name!r} has no end_card block")
    box = block.get("box") or {}
    x, top, width, _ = _safe(canvas, tokens)
    card_x = int(box.get("x", x))
    card_y = int(box.get("y", top))
    card_width = int(box.get("width", width))
    card_height = int(box.get("height") or 320)
    text_height = int(round(card_height * 0.6))
    text = Placed(
        Rect(x=card_x, y=card_y, width=card_width, height=text_height),
        _text_style(block, tokens, size=68, weight=600, color=tokens.palette.ink),
    )
    url_style = _text_style(block, tokens, size=36, weight=500, color=tokens.palette.accent)
    url_style["font_size"] = int(block.get("url_font_size") or 36)
    url_style["font_color"] = str(block.get("url_color") or tokens.palette.accent)
    url = Placed(
        Rect(
            x=card_x,
            y=card_y + text_height,
            width=card_width,
            height=card_height - text_height,
        ),
        url_style,
    )
    _require_safe(text.rect, canvas, tokens, "end card text")
    _require_safe(url.rect, canvas, tokens, "end card url")
    return text, url
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_layer_presets.py -v`
Expected: `8 passed`.

- [ ] **Step 6: Lint, type-check, commit**

```bash
uv run mypy apps packages && uv run ruff check
git add packages/core/src/video_editor_core/layer_presets.py tests/test_layer_presets.py
git commit -m "feat(core): place design presets inside the safe area

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 4: Pack adapter — stills, scrims, chip, pills

**Files:**
- Create: `packages/core/src/video_editor_core/adapters/from_content_pack.py`
- Test: `tests/test_from_content_pack.py`

**Interfaces:**
- Consumes: `ContentPack`, `still_for`, `END_CARD_ID`, `Alignment` (Task 1); `scrims`, `chip`, `pill` (Task 3); `canvas_for_aspect`, `quantise`.
- Produces: `pack_to_composition(pack, tokens, alignment, *, stills: Mapping[str, str | Path], fps: int = 30) -> Composition`, plus the z-band constants `Z_STILL`, `Z_SCRIM_TOP`, `Z_SCRIM_BOTTOM`, `Z_CHIP`, `Z_PILL`, `Z_CAPTION`, `Z_END_CARD`, `Z_END_URL` that Tasks 5 and 6 reuse.
- Scene windows come straight from `Alignment`, which tiles the duration, so `Composition._scenes_are_contiguous` cannot fail. The end card always gets its own final scene.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_from_content_pack.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from video_editor_core.adapters.from_content_pack import pack_to_composition
from video_editor_core.content_pack import Alignment, ContentPack, ContentPackError
from video_editor_core.designs import load_design

PACK = {
    "job_id": "demo",
    "voice": {"voice_id": "sarvam:test"},
    "scenes": [
        {
            "id": "hook",
            "still": {"key": "rat", "prompt": "a rat on a branch"},
            "lines": [
                {"say": "You think you're a Rat?", "show": "Not just a Rat", "cue": "Rat"}
            ],
        },
        {
            "id": "branch",
            "still": {"reuse": "rat"},
            "chip": "子",
            "lines": [{"say": "Your animal is the year branch.", "show": "Rat = 1 of 8"}],
        },
    ],
    "end_card": {
        "say": "Find yours free at cosmicsaju.com.",
        "text": "Free chart snapshot",
    },
}

ALIGNMENT = {
    "duration": 6.0,
    "scenes": [
        {
            "id": "hook",
            "start": 0.0,
            "end": 2.0,
            "words": [
                {"text": "You", "start": 0.1, "end": 0.3},
                {"text": "Rat", "start": 1.0, "end": 1.2},
            ],
        },
        {
            "id": "branch",
            "start": 2.0,
            "end": 4.0,
            "words": [{"text": "branch", "start": 2.4, "end": 2.8}],
        },
        {
            "id": "end_card",
            "start": 4.0,
            "end": 6.0,
            "words": [{"text": "cosmicsaju.com", "start": 4.2, "end": 5.4}],
        },
    ],
}


def build(stills: dict[str, Path] | None = None):
    pack = ContentPack.model_validate(PACK)
    tokens = load_design("saju_series")
    alignment = Alignment.model_validate(ALIGNMENT)
    # `is None`, not `or`: the missing-still test passes `{}`, which is falsy, so
    # `stills or default` would silently substitute the default and the test
    # would exercise the opposite of what it names.
    return pack_to_composition(
        pack,
        tokens,
        alignment,
        stills={"rat": Path("/tmp/rat.png")} if stills is None else stills,
    )


def test_scenes_are_the_pack_scenes_then_the_end_card() -> None:
    composition = build()
    assert [scene.id for scene in composition.scenes] == ["hook", "branch", "end_card"]
    assert composition.duration == pytest.approx(7.2)
    assert composition.scenes[-1].start == pytest.approx(4.0)
    assert composition.audio_mode == "external"


def test_each_scene_carries_a_full_canvas_still_with_drift() -> None:
    composition = build()
    still = composition.scenes[0].layers[0]
    assert still.id == "still_hook"
    assert still.kind == "media"
    assert still.rect.model_dump() == {"x": 0, "y": 0, "width": 1080, "height": 1920}
    assert still.style["object_fit"] == "cover"
    assert still.media_kind == "image"
    assert [motion.name for motion in still.motions] == ["drift"]
    assert still.motions[0].params["to"] == "scale(1.08)"


def test_scrims_sit_between_the_still_and_the_copy() -> None:
    composition = build()
    presets = [layer.preset for layer in composition.scenes[0].layers]
    assert presets[:3] == ["base", "scrim", "scrim"]
    scrims = [layer for layer in composition.scenes[0].layers if layer.preset == "scrim"]
    assert [scrim.z for scrim in scrims] == [10, 12]
    assert scrims[0].content.gradient.startswith("linear-gradient(")


def test_the_chip_appears_only_where_the_pack_asks_for_it() -> None:
    composition = build()
    chips = [
        layer
        for scene in composition.scenes
        for layer in scene.layers
        if layer.preset == "chip"
    ]
    assert len(chips) == 1
    assert chips[0].content.text == "子"
    assert chips[0].style["font"] == "Noto Serif KR"


def test_pills_stack_per_scene_from_the_design_rows() -> None:
    composition = build()
    hook_pill = next(
        layer for layer in composition.scenes[0].layers if layer.preset == "pill"
    )
    assert hook_pill.content.text == "Not just a Rat"
    assert hook_pill.rect.y == 520
    assert hook_pill.style["background"] == "rgba(15,11,30,0.72)"
    branch_pill = next(
        layer for layer in composition.scenes[1].layers if layer.preset == "pill"
    )
    assert branch_pill.rect.y == 520


def test_every_layer_id_is_unique() -> None:
    composition = build()
    ids = [layer.id for scene in composition.scenes for layer in scene.layers]
    assert len(ids) == len(set(ids))


def test_a_missing_still_is_refused() -> None:
    with pytest.raises(ContentPackError, match="no still"):
        build(stills={})


def test_an_unknown_alignment_scene_is_refused() -> None:
    pack = ContentPack.model_validate(PACK)
    tokens = load_design("saju_series")
    alignment = Alignment.model_validate(
        {
            "duration": 2.0,
            "scenes": [{"id": "ghost", "start": 0.0, "end": 2.0, "words": []}],
        }
    )
    with pytest.raises(ContentPackError, match="ghost"):
        pack_to_composition(
            pack, tokens, alignment, stills={"rat": Path("/tmp/rat.png")}
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_from_content_pack.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'video_editor_core.adapters.from_content_pack'`.

- [ ] **Step 3: Implement the adapter**

Create `packages/core/src/video_editor_core/adapters/from_content_pack.py`:

```python
"""Pack + design + measured alignment → one :class:`Composition`.

This adapter owns *timing*: which layer is on screen and when. Placement and
styling come from :mod:`video_editor_core.layer_presets`; the pack supplies
only words and asset keys. The result is the same IR every other path renders,
so `ve short` and `ve explain` share one compiler, one renderer and one QA gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from video_editor_core.composition import (
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
from video_editor_core.content_pack import (
    END_CARD_ID,
    Alignment,
    ContentPack,
    ContentPackError,
    PackScene,
    still_for,
)
from video_editor_core.designs import DesignTokens
from video_editor_core.layer_presets import Placed, chip, pill, scrims
from video_editor_core.render_graph import Rect

#: z-order bands: the still is the floor, the end card the ceiling.
Z_STILL = 0
Z_SCRIM_TOP = 10
Z_SCRIM_BOTTOM = 12
Z_CHIP = 20
Z_PILL = 30
Z_CAPTION = 40
Z_END_CARD = 50
Z_END_URL = 55


def _drift(tokens: DesignTokens) -> MotionSpec:
    """The ambient zoom that keeps a still from looking frozen."""
    return MotionSpec(
        name="drift",
        params={"from": "scale(1.0)", "to": f"scale({tokens.motion.drift})"},
    )


def _entry(tokens: DesignTokens) -> MotionSpec:
    return MotionSpec(name=tokens.motion.entry or "rise")


def _windows(
    pack: ContentPack, alignment: Alignment, fps: int
) -> tuple[list[tuple[PackScene, float, float]], float]:
    """Pack scene windows plus the end-card start, all frame-quantised."""
    aligned = {scene.id: scene for scene in alignment.scenes}
    known = {scene.id for scene in pack.scenes} | {END_CARD_ID}
    unknown = sorted(set(aligned) - known)
    if unknown:
        raise ContentPackError(
            "the alignment names scenes the pack does not have: "
            + ", ".join(repr(name) for name in unknown)
        )
    windows: list[tuple[PackScene, float, float]] = []
    for scene in pack.scenes:
        window = aligned.get(scene.id)
        if window is None:
            raise ContentPackError(f"pack scene {scene.id!r} has no aligned window")
        windows.append(
            (scene, quantise(window.start, fps), quantise(window.end, fps))
        )
    card = aligned.get(END_CARD_ID)
    card_start = quantise(card.start if card else alignment.duration, fps)
    return windows, card_start


def _still_layer(
    pack: ContentPack,
    scene: PackScene,
    start: float,
    end: float,
    stills: Mapping[str, str | Path],
    tokens: DesignTokens,
    canvas: Canvas,
) -> CompositionLayer:
    key = still_for(pack, scene.id)
    source = stills.get(key)
    if source is None:
        raise ContentPackError(f"no still was generated for key {key!r}")
    return CompositionLayer(
        id=f"still_{scene.id}",
        kind="media",
        preset="base",
        rect=Rect(x=0, y=0, width=canvas.width, height=canvas.height),
        time=LayerTime(start=start, end=end),
        content=LayerContent(source=str(source), alt=scene.id),
        style={"object_fit": "cover"},
        motions=[_drift(tokens)],
        media_kind="image",
        track=0,
        z=Z_STILL,
    )


def _scrim_layers(
    scene: PackScene,
    start: float,
    end: float,
    tokens: DesignTokens,
    canvas: Canvas,
) -> list[CompositionLayer]:
    layers: list[CompositionLayer] = []
    for index, placed in enumerate(scrims(tokens, canvas)):
        layers.append(
            CompositionLayer(
                id=f"scrim_{index}_{scene.id}",
                kind="shape",
                preset="scrim",
                rect=placed.rect,
                time=LayerTime(start=start, end=end),
                content=LayerContent(gradient=placed.content),
                style=dict(placed.style),
                track=0,
                z=Z_SCRIM_TOP if index == 0 else Z_SCRIM_BOTTOM,
            )
        )
    return layers


def _chip_layer(
    scene: PackScene, placed: Placed, start: float, end: float, tokens: DesignTokens
) -> CompositionLayer:
    return CompositionLayer(
        id=f"chip_{scene.id}",
        kind="text",
        preset="chip",
        rect=placed.rect,
        time=LayerTime(start=start, end=end),
        content=LayerContent(text=scene.chip),
        style=dict(placed.style),
        motions=[_entry(tokens)],
        track=0,
        z=Z_CHIP,
    )


def _pill_layers(
    scene: PackScene, start: float, end: float, tokens: DesignTokens, canvas: Canvas
) -> list[CompositionLayer]:
    layers: list[CompositionLayer] = []
    shown = [line for line in scene.lines if line.show]
    for index, line in enumerate(shown):
        placed = pill(tokens, canvas, index=index)
        layers.append(
            CompositionLayer(
                id=f"pill_{index}_{scene.id}",
                kind="text",
                preset="pill",
                rect=placed.rect,
                time=LayerTime(start=start, end=end),
                content=LayerContent(text=line.show),
                style=dict(placed.style),
                motions=[_entry(tokens)],
                track=0,
                z=Z_PILL + index,
            )
        )
    return layers


def pack_to_composition(
    pack: ContentPack,
    tokens: DesignTokens,
    alignment: Alignment,
    *,
    stills: Mapping[str, str | Path],
    fps: int = 30,
) -> Composition:
    """Build the composition the pack describes, on the measured timeline."""
    width, height = canvas_for_aspect(pack.aspect)
    canvas = Canvas(width=width, height=height, fps=fps)
    windows, card_start = _windows(pack, alignment, fps)

    scenes: list[CompositionScene] = []
    for scene, start, end in windows:
        layers = [
            _still_layer(pack, scene, start, end, stills, tokens, canvas),
            *_scrim_layers(scene, start, end, tokens, canvas),
        ]
        placed_chip = chip(tokens, canvas)
        if placed_chip is not None and scene.chip:
            layers.append(_chip_layer(scene, placed_chip, start, end, tokens))
        layers.extend(_pill_layers(scene, start, end, tokens, canvas))
        scenes.append(
            CompositionScene(id=scene.id, start=start, end=end, layers=layers)
        )

    card_end = quantise(alignment.duration + pack.end_card.tail_seconds, fps)
    scenes.append(CompositionScene(id=END_CARD_ID, start=card_start, end=card_end))

    return Composition(
        job_id=pack.job_id,
        canvas=canvas,
        duration=card_end,
        scenes=scenes,
        audio=[],
        audio_mode="external",
        design=tokens,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_from_content_pack.py -v`
Expected: `8 passed`.

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run mypy apps packages && uv run ruff check
git add packages/core/src/video_editor_core/adapters/from_content_pack.py tests/test_from_content_pack.py
git commit -m "feat(core): adapt a content pack to a Composition

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: Karaoke caption layers from the alignment

**Files:**
- Create: `apps/cli/src/video_editor_cli/short_captions.py`
- Modify: `packages/core/src/video_editor_core/adapters/from_content_pack.py`
- Test: `tests/test_short_captions.py`, `tests/test_from_content_pack.py`

**Interfaces:**
- Consumes: `caption(tokens, canvas) -> Placed` (Task 3), `Alignment` (Task 1), `CaptionStyle.preset`, `generate_segment_caption_lines`.
- Produces: `caption_style(tokens) -> CaptionStyle`, `caption_layers(tokens, alignment, *, canvas, fps=30) -> list[CompositionLayer]`; the adapter gains `captions: Sequence[CompositionLayer] = ()`, placed into the scene that contains each layer's start.

**Why this module lives in `apps/cli`:** `packages/core` may not import a skill package (its declared dependencies are pydantic, structlog, state and ffmpeg). `apps/cli` already declares `video-editor-skill-captions`, so the captions skill is imported here and nowhere else in the short path.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_short_captions.py`:

```python
from __future__ import annotations

import pytest

from video_editor_cli.short_captions import caption_layers, caption_style
from video_editor_core.composition import Canvas
from video_editor_core.content_pack import Alignment
from video_editor_core.designs import load_design
from video_editor_core.layer_presets import caption as caption_placement

CANVAS = Canvas(width=1080, height=1920, fps=30)

# Four words with a long tail on the last one: without a clamp the linger of
# line one would run past the start of line two.
ALIGNMENT = {
    "duration": 4.0,
    "scenes": [
        {
            "id": "hook",
            "start": 0.0,
            "end": 4.0,
            "words": [
                {"text": "You", "start": 0.1, "end": 0.5},
                {"text": "think", "start": 0.5, "end": 1.0},
                {"text": "you're", "start": 1.0, "end": 1.6},
                {"text": "a", "start": 3.6, "end": 3.9},
            ],
        }
    ],
}


@pytest.fixture
def tokens():
    return load_design("saju_series")


def build(tokens):
    return caption_layers(
        tokens,
        Alignment.model_validate(ALIGNMENT),
        canvas=CANVAS,
    )


def test_the_style_reads_the_design(tokens) -> None:
    style = caption_style(tokens)
    assert style.font == "Inter"
    assert style.font_size == 64
    assert style.font_weight == 700
    assert style.highlight_color == "#F59E0B"
    assert style.animation == "karaoke"
    assert style.max_chars == 34


def test_caption_layers_use_the_designed_box(tokens) -> None:
    layers = build(tokens)
    assert layers, "the alignment produced no caption lines"
    placed = caption_placement(tokens, CANVAS)
    assert all(layer.rect == placed.rect for layer in layers)
    assert all(layer.kind == "rich_text" for layer in layers)
    assert all(layer.preset == "caption_line" for layer in layers)
    assert all(layer.z == 40 for layer in layers)
    assert layers[0].style["highlight_color"] == "#F59E0B"


def test_word_spans_are_relative_to_their_line(tokens) -> None:
    layers = build(tokens)
    first = layers[0]
    assert first.content.spans[0].start == pytest.approx(0.0, abs=1e-6)
    assert all(span.start < span.end for span in first.content.spans)
    duration = first.time.end - first.time.start
    assert all(span.end <= duration + 1e-6 for span in first.content.spans)


def test_a_linger_never_runs_past_the_next_line(tokens) -> None:
    layers = build(tokens)
    for current, following in zip(layers, layers[1:], strict=False):
        assert current.time.end <= following.time.start


def test_an_alignment_with_no_words_emits_no_captions(tokens) -> None:
    alignment = Alignment.model_validate(
        {"duration": 1.0, "scenes": [{"id": "silent", "start": 0.0, "end": 1.0}]}
    )
    assert caption_layers(tokens, alignment, canvas=CANVAS) == []
```

Append to `tests/test_from_content_pack.py`:

```python
def test_caption_layers_land_in_the_scene_that_contains_them() -> None:
    from video_editor_core.composition import CompositionLayer, LayerContent, LayerTime, WordSpan

    pack = ContentPack.model_validate(PACK)
    tokens = load_design("saju_series")
    caption = CompositionLayer(
        id="caption_000",
        kind="rich_text",
        preset="caption_line",
        rect=Rect(x=86, y=1240, width=908, height=180),
        time=LayerTime(start=2.2, end=3.4),
        content=LayerContent(spans=[WordSpan(text="branch", start=0.0, end=0.6)]),
        z=40,
    )
    composition = pack_to_composition(
        pack,
        tokens,
        Alignment.model_validate(ALIGNMENT),
        stills={"rat": Path("/tmp/rat.png")},
        captions=[caption],
    )
    branch = next(scene for scene in composition.scenes if scene.id == "branch")
    assert "caption_000" in [layer.id for layer in branch.layers]


def test_a_caption_outside_every_scene_is_refused() -> None:
    from video_editor_core.composition import CompositionLayer, LayerTime

    pack = ContentPack.model_validate(PACK)
    tokens = load_design("saju_series")
    stray = CompositionLayer(
        id="caption_900",
        kind="text",
        preset="caption_line",
        rect=Rect(x=86, y=1240, width=908, height=180),
        time=LayerTime(start=8.0, end=8.5),
        z=40,
    )
    with pytest.raises(ContentPackError, match="caption_900"):
        pack_to_composition(
            pack,
            tokens,
            Alignment.model_validate(ALIGNMENT),
            stills={"rat": Path("/tmp/rat.png")},
            captions=[stray],
        )
```

Add `Rect` to the imports of `tests/test_from_content_pack.py` (`from video_editor_core.render_graph import Rect`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_short_captions.py tests/test_from_content_pack.py -v -k "caption"`
Expected: `ModuleNotFoundError: No module named 'video_editor_cli.short_captions'`, and `TypeError: pack_to_composition() got an unexpected keyword argument 'captions'`.

- [ ] **Step 3: Implement `short_captions.py`**

Create `apps/cli/src/video_editor_cli/short_captions.py`:

```python
"""Karaoke caption layers built from the measured alignment.

The captions skill owns grouping words into lines and the karaoke look; this
module is the only short-path code that imports it, because ``video_editor_core``
may not depend on a skill package.
"""

from __future__ import annotations

from captions.models import CaptionStyle, CaptionWord
from captions.timing import generate_segment_caption_lines

from video_editor_core.adapters.from_content_pack import Z_CAPTION
from video_editor_core.composition import (
    Canvas,
    CompositionLayer,
    LayerContent,
    LayerTime,
    WordSpan,
    quantise,
)
from video_editor_core.content_pack import Alignment
from video_editor_core.designs import DesignTokens
from video_editor_core.layer_presets import caption


def caption_style(tokens: DesignTokens) -> CaptionStyle:
    """The design's caption block layered over the named preset's grouping."""
    block = tokens.layers.caption_line
    overrides: dict[str, object] = {}
    if block.get("font"):
        overrides["font"] = str(block["font"])
    if block.get("font_size"):
        overrides["font_size"] = int(block["font_size"])
    if block.get("font_weight"):
        overrides["font_weight"] = int(block["font_weight"])
    if block.get("font_color"):
        overrides["font_color"] = str(block["font_color"])
    if block.get("highlight_color"):
        overrides["highlight_color"] = str(block["highlight_color"])
    if block.get("upcoming_opacity") is not None:
        overrides["upcoming_opacity"] = float(block["upcoming_opacity"])
    return CaptionStyle.preset(tokens.layers.caption_preset or "karaoke", **overrides)


def caption_layers(
    tokens: DesignTokens,
    alignment: Alignment,
    *,
    canvas: Canvas,
    fps: int = 30,
) -> list[CompositionLayer]:
    """One rich_text layer per caption line, spans rebased to the line."""
    words = [
        CaptionWord(text=word.text, start=word.start, end=word.end)
        for scene in alignment.scenes
        for word in scene.words
    ]
    if not words:
        return []
    style = caption_style(tokens)
    placed = caption(tokens, canvas)
    lines = generate_segment_caption_lines(words, 0.0, alignment.duration, style)
    layers: list[CompositionLayer] = []
    for index, line in enumerate(lines):
        start = quantise(line.start, fps)
        next_start = (
            quantise(lines[index + 1].start, fps)
            if index + 1 < len(lines)
            else quantise(alignment.duration, fps)
        )
        # ``linger`` holds a line after its last word, but never into the next
        # line: two live layers would double every word on screen.
        end = quantise(min(line.end + style.linger, next_start), fps)
        if end <= start:
            end = quantise(start + 1.0 / fps, fps)
        duration = round(end - start, 3)
        spans = [
            WordSpan(
                text=word.text,
                start=round(max(0.0, word.start - start), 3),
                end=round(min(duration, max(1.0 / fps, word.end - start)), 3),
            )
            for word in line.words
        ]
        layers.append(
            CompositionLayer(
                id=f"caption_{index:03d}",
                kind="rich_text",
                preset="caption_line",
                rect=placed.rect,
                time=LayerTime(start=start, end=end),
                content=LayerContent(spans=spans),
                style=dict(placed.style),
                z=Z_CAPTION,
            )
        )
    return layers
```

- [ ] **Step 4: Add `captions=` to the adapter**

In `packages/core/src/video_editor_core/adapters/from_content_pack.py`, update the imports and the signature, and add the placement helper:

```python
from collections.abc import Sequence
```

```python
def _place_captions(
    scenes: list[CompositionScene],
    captions: Sequence[CompositionLayer],
    duration: float,
) -> None:
    """Attach each caption layer to the scene whose window contains its start."""
    for layer in captions:
        if layer.time.start >= duration:
            raise ContentPackError(
                f"caption layer {layer.id!r} starts at {layer.time.start:.3f}s, "
                f"past the composition duration {duration:.3f}s"
            )
        for scene in scenes:
            if scene.start <= layer.time.start < scene.end:
                scene.layers.append(layer)
                break
        else:
            raise ContentPackError(
                f"caption layer {layer.id!r} at {layer.time.start:.3f}s is not "
                "inside any scene window"
            )
```

```python
def pack_to_composition(
    pack: ContentPack,
    tokens: DesignTokens,
    alignment: Alignment,
    *,
    stills: Mapping[str, str | Path],
    captions: Sequence[CompositionLayer] = (),
    fps: int = 30,
) -> Composition:
```

and before `return Composition(...)`:

```python
    _place_captions(scenes, captions, card_end)
```

Note: `scenes` is a plain list, so the helper mutates the scenes it is given; `Composition` validates after the fact.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_short_captions.py tests/test_from_content_pack.py -v`
Expected: `15 passed` (5 new caption tests + 10 in `test_from_content_pack.py`).

- [ ] **Step 6: Lint, type-check, commit**

```bash
uv run mypy apps packages && uv run ruff check
git add apps/cli/src/video_editor_cli/short_captions.py packages/core/src/video_editor_core/adapters/from_content_pack.py tests/test_short_captions.py tests/test_from_content_pack.py
git commit -m "feat(cli): build karaoke caption layers on the measured alignment

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 6: Cue-driven reveals and the end card

**Files:**
- Modify: `packages/core/src/video_editor_core/adapters/from_content_pack.py`
- Modify: `tests/test_from_content_pack.py`

**Interfaces:**
- Consumes: `cues.resolve_cues(..., strict=True)`, `cues.CueMismatchError`, `cue_phrases(pack)`; `layer_presets.end_card(tokens, canvas) -> tuple[Placed, Placed]`.
- Produces: pill reveal times from the pack's cues (a pill starts when its line's cue is spoken, else at its scene's start) and the end card's two text layers in the final scene.

- [ ] **Step 1: Write the failing tests**

In `tests/test_from_content_pack.py`, change the hook pill assertion and add the new tests:

```python
def test_pills_reveal_on_their_cue() -> None:
    composition = build()
    hook_pill = next(
        layer for layer in composition.scenes[0].layers if layer.preset == "pill"
    )
    # "Rat" is spoken at 1.0s; resolve_cues leads by 0.08s so the reveal
    # lands with the word rather than a beat behind it. 0.92s then snaps to the
    # 30 fps frame grid: frame 28 is 0.933s, so the raw 0.92 would never appear.
    assert hook_pill.time.start == pytest.approx(28 / 30)
    assert hook_pill.time.end == pytest.approx(2.0)

    branch_pill = next(
        layer for layer in composition.scenes[1].layers if layer.preset == "pill"
    )
    # No cue on that line: the pill is up for the whole scene.
    assert branch_pill.time.start == pytest.approx(2.0)
```

```python
def test_a_cue_that_matches_no_spoken_word_fails_the_build() -> None:
    from video_editor_core.cues import CueMismatchError

    pack = ContentPack.model_validate(PACK)
    pack.scenes[0].lines[0].cue = "Year branch"
    with pytest.raises(CueMismatchError, match="hook:0"):
        pack_to_composition(
            pack,
            load_design("saju_series"),
            Alignment.model_validate(ALIGNMENT),
            stills={"rat": Path("/tmp/rat.png")},
        )


def test_the_end_card_carries_the_words_and_the_url() -> None:
    composition = build()
    card = composition.scenes[-1]
    assert card.id == "end_card"
    assert card.start == pytest.approx(4.0)
    assert card.end == pytest.approx(7.2)
    text, url = card.layers
    assert text.id == "end_card_text"
    assert text.content.text == "Free chart snapshot"
    assert text.z == 50
    assert url.id == "end_card_url"
    assert url.content.text == "cosmicsaju.com"
    assert url.style["font_color"] == "#F59E0B"
    assert url.z == 55
    assert url.rect.y == text.rect.y + text.rect.height


def test_a_cue_is_resolved_inside_its_own_scene() -> None:
    """A phrase spoken in another scene must not answer this scene's cue.

    "Rat" is spoken only in ``hook``. Cueing it on ``branch``'s line has to
    fail: a global search would silently time that pill from a word spoken
    two scenes earlier.
    """
    from video_editor_core.cues import CueMismatchError

    pack = ContentPack.model_validate(PACK)
    pack.scenes[1].lines[0].cue = "Rat"
    with pytest.raises(CueMismatchError, match="branch:0"):
        pack_to_composition(
            pack,
            load_design("saju_series"),
            Alignment.model_validate(ALIGNMENT),
            stills={"rat": Path("/tmp/rat.png")},
        )


def test_a_blank_end_card_is_refused() -> None:
    from video_editor_core.content_pack import ContentPackError

    pack = ContentPack.model_validate(PACK)
    pack.end_card.text = "  "
    with pytest.raises(ContentPackError, match="end_card.text"):
        pack_to_composition(
            pack,
            load_design("saju_series"),
            Alignment.model_validate(ALIGNMENT),
            stills={"rat": Path("/tmp/rat.png")},
        )


def test_a_cue_word_at_a_quantised_scene_boundary_still_resolves() -> None:
    """The defect this guards: scene windows are snapped to the frame grid,
    word starts are not.

    Here ``branch`` opens at the raw 1.02 s but quantises to 1.0333 s, and the
    cue word begins at 1.02 s — before the quantised start. Searching the
    quantised window drops that word and the strict resolve aborts a valid
    build, after the paid stages have run.
    """
    pack = ContentPack.model_validate(PACK)
    pack.scenes[0].lines[0].cue = ""
    pack.scenes[1].lines[0].cue = "Rat"
    alignment = Alignment.model_validate(
        {
            "duration": 6.0,
            "scenes": [
                {"id": "hook", "start": 0.0, "end": 1.02, "words": []},
                {
                    "id": "branch",
                    "start": 1.02,
                    "end": 4.0,
                    "words": [{"text": "Rat", "start": 1.02, "end": 1.2}],
                },
                {"id": "end_card", "start": 4.0, "end": 6.0, "words": []},
            ],
        }
    )
    composition = pack_to_composition(
        pack,
        load_design("saju_series"),
        alignment,
        stills={"rat": Path("/tmp/rat.png")},
    )
    branch_pill = next(
        layer for layer in composition.scenes[1].layers if layer.preset == "pill"
    )
    # The reveal (1.02 - 0.08 lead) lands before the window, so it snaps up to
    # the branch window's first frame: frame 31 = 1.0333 s.
    assert branch_pill.time.start == pytest.approx(31 / 30)
    assert branch_pill.time.end == pytest.approx(4.0)
```

Also change `test_pills_stack_per_scene_from_the_design_rows` to assert only geometry (its old timing assumption is superseded):

```python
def test_pills_stack_per_scene_from_the_design_rows() -> None:
    composition = build()
    hook_pill = next(
        layer for layer in composition.scenes[0].layers if layer.preset == "pill"
    )
    assert hook_pill.content.text == "Not just a Rat"
    assert hook_pill.rect.y == 520
    assert hook_pill.style["background"] == "rgba(15,11,30,0.72)"
    branch_pill = next(
        layer for layer in composition.scenes[1].layers if layer.preset == "pill"
    )
    assert branch_pill.rect.y == 520
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_from_content_pack.py -v`
Expected: FAIL — the pill starts at 0.0 rather than 0.92, `CueMismatchError` is not raised, and `compositions.scenes[-1].layers` is empty (`ValueError: not enough values to unpack`). The per-scene cue test also fails to raise (a global search finds "Rat" in `hook`), and the blank-card test fails with `DID NOT RAISE`.

- [ ] **Step 3: Resolve cues and time the pills**

In `from_content_pack.py`, add imports. `cue_phrases` and `TimedScene` join the
existing `content_pack` block (isort keeps constants, then classes, then
functions), and `end_card` joins the existing `layer_presets` line — a second
`from video_editor_core.layer_presets import …` statement trips ruff's isort
rule (`I001`), which this repo selects:

```python
from video_editor_core.content_pack import (
    END_CARD_ID,
    Alignment,
    ContentPack,
    ContentPackError,
    PackScene,
    TimedScene,
    cue_phrases,
    still_for,
)
from video_editor_core.cues import resolve_cues
# ...and in the existing line, alphabetically:
from video_editor_core.layer_presets import Placed, chip, end_card, pill, scrims
```

Replace `_pill_layers` with a cue-aware version:

```python
def _scene_cue_times(
    scene: PackScene,
    timed: TimedScene,
    start: float,
    end: float,
    phrases: Mapping[str, str],
    fps: int,
) -> dict[str, float]:
    """Cue times for *this* scene, searched among its own spoken words only.

    A time-window search cannot be made exact here: scene windows are
    frame-quantised while measured word starts are not, so at every boundary
    the two disagree by up to half a frame. ``after=start`` then drops a cue
    word that begins a fraction before the quantised start, and the strict
    resolve aborts an otherwise valid build — after the paid stages have run.
    ``timed.words`` is the alignment's own answer to "which words belong to
    this scene", so no tolerance is needed and a phrase spoken in an earlier
    scene cannot answer this scene's cue.

    The reveal is clamped into ``[start, end - 1/fps]``: ``LayerTime``
    requires ``end > start``, and the lead-in can otherwise land before the
    scene opens.

    ``strict``: a cue that matches no spoken word is a content bug the build
    must stop on — the pill would otherwise silently never appear.
    """
    keys = {key: phrase for key, phrase in phrases.items() if key.startswith(f"{scene.id}:")}
    words = [{"word": word.text, "start": word.start, "end": word.end} for word in timed.words]
    ceiling = max(start, end - 1.0 / fps)
    return {
        key: min(max(quantise(time, fps), start), ceiling)
        for key, time in resolve_cues(words, keys, strict=True).items()
    }


def _pill_layers(
    scene: PackScene,
    start: float,
    end: float,
    tokens: DesignTokens,
    canvas: Canvas,
    cue_times: Mapping[str, float],
) -> list[CompositionLayer]:
    layers: list[CompositionLayer] = []
    pill_index = 0
    for line_index, line in enumerate(scene.lines):
        if not line.show:
            continue
        placed = pill(tokens, canvas, index=pill_index)
        # ``_scene_cue_times`` already returns a frame-aligned reveal inside
        # the window; a cue-less line reveals at the scene's start.
        reveal = cue_times.get(f"{scene.id}:{line_index}", start)
        layers.append(
            CompositionLayer(
                id=f"pill_{pill_index}_{scene.id}",
                kind="text",
                preset="pill",
                rect=placed.rect,
                time=LayerTime(start=reveal, end=end),
                content=LayerContent(text=line.show),
                style=dict(placed.style),
                motions=[_entry(tokens)],
                track=0,
                z=Z_PILL + pill_index,
            )
        )
        pill_index += 1
    return layers
```

In `pack_to_composition`, compute the phrases and the aligned scenes once
before the loop and resolve per scene inside it:

```python
    phrases = cue_phrases(pack)
    aligned = {timed.id: timed for timed in alignment.scenes}
```

```python
        layers.extend(
            _pill_layers(
                scene,
                start,
                end,
                tokens,
                canvas,
                _scene_cue_times(
                    scene, aligned[scene.id], start, end, phrases, fps
                ),
            )
        )
```

`aligned[scene.id]` is safe: `_windows` already refuses a pack scene with no
aligned window.

- [ ] **Step 4: Emit the end card layers**

Add the builder:

```python
def _end_card_layers(
    pack: ContentPack,
    tokens: DesignTokens,
    canvas: Canvas,
    start: float,
    end: float,
) -> list[CompositionLayer]:
    text, url = end_card(tokens, canvas)
    return [
        CompositionLayer(
            id="end_card_text",
            kind="text",
            preset="end_card",
            rect=text.rect,
            time=LayerTime(start=start, end=end),
            content=LayerContent(text=pack.end_card.text),
            style=dict(text.style),
            motions=[_entry(tokens)],
            track=0,
            z=Z_END_CARD,
        ),
        CompositionLayer(
            id="end_card_url",
            kind="text",
            preset="end_card",
            rect=url.rect,
            time=LayerTime(start=start, end=end),
            content=LayerContent(text=pack.end_card.url),
            style=dict(url.style),
            motions=[_entry(tokens)],
            track=0,
            z=Z_END_URL,
        ),
    ]
```

and in `pack_to_composition`, replace the bare end-card scene append:

```python
    card_end = quantise(alignment.duration + pack.end_card.tail_seconds, fps)
    scenes.append(
        CompositionScene(
            id=END_CARD_ID,
            start=card_start,
            end=card_end,
            layers=_end_card_layers(pack, tokens, canvas, card_start, card_end),
        )
    )
```

An empty `pack.end_card.text` still produces the layer with empty content; a card with no words is a content defect, so add the guard in `_end_card_layers`:

```python
    if not pack.end_card.text.strip():
        raise ContentPackError(
            "the pack has no end_card.text; the closing card would be blank"
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_from_content_pack.py -v`
Expected: `16 passed` (10 after Task 5, plus 6 here: the cue reveal, the
unknown cue, the end card, the per-scene cue, the blank card, and the
quantised-boundary regression).

- [ ] **Step 6: Run the wider suites these changes touch**

Run: `uv run pytest tests/test_content_pack.py tests/test_layer_presets.py tests/test_from_content_pack.py tests/test_short_captions.py tests/test_composition.py -q`
Expected: all pass.

- [ ] **Step 7: Lint, type-check, commit**

```bash
uv run mypy apps packages && uv run ruff check
git add packages/core/src/video_editor_core/adapters/from_content_pack.py tests/test_from_content_pack.py
git commit -m "feat(core): reveal pills on their spoken cue and close on the end card

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 7: `short_pipeline` — requests, preflight, cost gate

**Files:**
- Create: `apps/cli/src/video_editor_cli/short_pipeline.py`
- Modify: `apps/cli/src/video_editor_cli/explainer_pipeline.py` (`check_approved_spend` gains an injectable pricer)
- Test: `tests/test_short_pipeline.py`

**Interfaces:**
- Consumes: `short_job_requests` inputs — `still_specs`, `spoken_lines`, `END_CARD_ID` (Task 1); `load_design`, `layer_presets` (Tasks 2–3); `caption_style` (Task 5); `price_requests`, `check_approved_spend`, `approved_total` from `explainer_pipeline`; `_format_table` from `commands/estimate.py`.
- Produces: `SHORT_FPS = 30`, `ShortPipelineError`, `ShortPreflightError`, `short_job_requests(pack) -> list[AssetRequest]`, `price_short_job(requests, registry, *, image_chain=()) -> list[dict[str, Any]]`, `preflight_short(pack) -> DesignTokens`, `estimate_short(ctx, pack, *, config_path=None, auto_approve=False) -> Path`. Task 10's run-side gate passes `price=price_short_job` to `check_approved_spend` so estimate and run price the same list the same way.

**Why the pricing is short-specific.** Sarvam bills `ceil(chars / 1000)` **per call**, and Task 9 makes one call per pack line. `price_requests` merges every TTS request of a list into one group with `pricing_params = {}` and charges that formula once — correct for the explainer, which deliberately merges its scenes into one narration call (`tests/test_estimate.py::test_estimate_prices_the_narration_once_not_per_scene`, issue #263), wrong for a pack whose every line is its own call. Pricing a four-line pack at one call's rate understates the spend by roughly the line count: the saju build would be approved at $0.023 for voice and cost $0.161. So the short path prices TTS per request and merges the rows for display, and the run-side gate is given that same pricer rather than moving the shared default.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_short_pipeline.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest
from video_editor_asset_ingestion.models import AssetType
from video_editor_state.job import JobContext

from video_editor_cli.short_pipeline import (
    ShortPreflightError,
    estimate_short,
    preflight_short,
    short_job_requests,
)
from video_editor_core.content_pack import ContentPack

PACK = {
    "job_id": "demo",
    "design": "saju_series",
    "voice": {
        "provider": "sarvam_tts",
        "voice_id": "sarvam:sarang_en_narration",
        "model": "bulbul:v4-flash",
        "language": "en-IN",
        "gap_seconds": 0.28,
    },
    "image": {
        "provider": "poyo_ai",
        "model": "gpt-image-2.5-fixed",
        "resolution": "2K",
        "style_suffix": "ink wash, no text",
    },
    "scenes": [
        {
            "id": "hook",
            "still": {"key": "rat", "prompt": "a rat on a branch"},
            "lines": [{"say": "You think you're a Rat?", "show": "Not just a Rat"}],
        },
        {
            "id": "branch",
            "still": {"reuse": "rat"},
            "lines": [{"say": "Your animal is the year branch."}],
        },
    ],
    "end_card": {"say": "Find yours free at cosmicsaju.com.", "text": "Free chart snapshot"},
}


def make_pack() -> ContentPack:
    return ContentPack.model_validate(PACK)


def make_ctx(tmp_path: Path) -> JobContext:
    ctx = JobContext(
        job_id="demo", work_root=tmp_path / "work", output_root=tmp_path / "out"
    )
    ctx.ensure_dirs()
    return ctx


def test_requests_are_one_image_per_still_and_one_voice_line_each() -> None:
    requests = short_job_requests(make_pack())
    images = [r for r in requests if r.asset_type == AssetType.IMAGE]
    tts = [r for r in requests if r.asset_type == AssetType.TTS]
    assert [r.asset_id for r in images] == ["still_rat"]
    assert [r.asset_id for r in tts] == [
        "line_hook_0",
        "line_branch_0",
        "line_end_card_0",
    ]
    assert images[0].params == {
        "model": "gpt-image-2.5-fixed",
        "model_kwargs": {"resolution": "2K"},
    }
    assert tts[0].params == {
        "voice_id": "sarvam:sarang_en_narration",
        "model": "bulbul:v4-flash",
        "language": "en-IN",
    }


def test_the_style_suffix_is_appended_to_every_still_prompt() -> None:
    image = short_job_requests(make_pack())[0]
    assert image.prompt == "a rat on a branch. ink wash, no text"


def test_preflight_returns_the_design_it_validated() -> None:
    tokens = preflight_short(make_pack())
    assert tokens.name == "saju_series"


def test_preflight_rejects_an_unknown_design() -> None:
    pack = make_pack()
    pack.design = "no_such_design"
    with pytest.raises(ShortPreflightError, match="no_such_design"):
        preflight_short(pack)


def test_estimate_writes_an_approved_total(tmp_path: Path) -> None:
    path = estimate_short(make_ctx(tmp_path), make_pack(), auto_approve=True)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["job_id"] == "demo"
    assert data["line_items"]
    assert data["total_cost"] > 0


def test_estimate_refuses_when_the_prompt_is_declined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Never read the real terminal: under pytest stdin is captured, so an
    # un-patched ``typer.prompt`` raises OSError/EOFError instead of returning.
    monkeypatch.setattr(typer, "prompt", lambda *args, **kwargs: "n")
    with pytest.raises(typer.Exit):
        estimate_short(make_ctx(tmp_path), make_pack(), auto_approve=False)
    assert not (tmp_path / "work" / "demo" / "plan" / "cost_estimate.json").exists()


def _tts_total(items: list[dict[str, Any]]) -> float:
    return round(
        sum(float(item["total_cost"]) for item in items if item["task_type"] == "tts"),
        4,
    )


def test_the_gate_prices_each_voice_line_as_its_own_call() -> None:
    """Sarvam bills per call, and this build makes one call per line (#263).

    ``price_requests`` merges every TTS request into one group and charges one
    call's rate — correct for the explainer's single narration track, wrong
    here by roughly the line count. The pack's three lines each cost one block
    of 1 000 characters ($0.023), so voice costs $0.069, not $0.023.
    """
    from video_editor_asset_ingestion.pricing import PricingRegistry

    from video_editor_cli.explainer_pipeline import price_requests
    from video_editor_cli.short_pipeline import price_short_job

    registry = PricingRegistry.load()
    requests = short_job_requests(make_pack())
    assert _tts_total(price_requests(requests, registry)) == pytest.approx(0.023)
    items = price_short_job(requests, registry)
    voice = next(item for item in items if item["task_type"] == "tts")
    assert voice["count"] == 3
    assert voice["total_cost"] == pytest.approx(0.069)


def test_the_estimate_file_charges_voice_per_line(tmp_path: Path) -> None:
    path = estimate_short(make_ctx(tmp_path), make_pack(), auto_approve=True)
    data = json.loads(path.read_text(encoding="utf-8"))
    voice = next(item for item in data["line_items"] if item["task_type"] == "tts")
    assert voice["count"] == 3
    assert voice["total_cost"] == pytest.approx(0.069)
    assert data["total_cost"] == pytest.approx(
        round(sum(float(item["total_cost"]) for item in data["line_items"]), 4)
    )


def test_estimate_refuses_an_unpriced_provider(tmp_path: Path) -> None:
    pack = make_pack()
    pack.voice.provider = "no_such_provider"
    with pytest.raises(typer.BadParameter, match="no price"):
        estimate_short(make_ctx(tmp_path), pack, auto_approve=True)


def _broken_design(
    monkeypatch: pytest.MonkeyPatch, mutate: Callable[[DesignTokens], None]
) -> None:
    """Point ``preflight_short`` at a mutated copy of the real design."""
    from video_editor_cli import short_pipeline
    from video_editor_core.designs import load_design

    tokens = load_design("saju_series").model_copy(deep=True)
    mutate(tokens)
    monkeypatch.setattr(short_pipeline, "load_design", lambda name: tokens)


def test_preflight_wraps_a_bad_caption_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bad preset raises a raw ``ValueError`` from the captions skill at
    compile time — after every paid stage. Preflight must catch it."""
    _broken_design(
        monkeypatch,
        lambda tokens: setattr(tokens.layers, "caption_preset", "no_such_preset"),
    )
    with pytest.raises(ShortPreflightError, match="caption"):
        preflight_short(make_pack())


def test_preflight_checks_every_pill_the_pack_can_show(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scene with two ``show`` lines stacks two bands; only the first was
    checked, so a gap that pushes the second off-frame passed preflight."""
    _broken_design(monkeypatch, lambda tokens: tokens.layers.pill.update({"gap": 4000}))
    pack = make_pack()
    pack.scenes[0].lines.append(PackLine(say="Second.", show="Second pill"))
    with pytest.raises(ShortPreflightError, match="pill 1"):
        preflight_short(pack)


def test_preflight_refuses_a_blank_end_card_text() -> None:
    pack = make_pack()
    pack.end_card.text = "  "
    with pytest.raises(ShortPreflightError, match="end_card.text"):
        preflight_short(pack)


def test_a_prompt_keeps_its_period_when_no_suffix_is_set() -> None:
    pack = make_pack()
    pack.image.style_suffix = ""
    pack.scenes[0].still.prompt = "a rat on a branch."
    image = short_job_requests(pack)[0]
    assert image.prompt == "a rat on a branch."
```

Also add to the test module's imports: `import typer`, `from collections.abc import Callable`,
`from typing import Any`, `from video_editor_core.content_pack import ContentPack, PackLine`,
and `from video_editor_core.designs import DesignTokens` — fold `PackLine` into the
existing `content_pack` line rather than a second statement (`I001`). Bare `dict`
in an annotation fails this repo's `mypy --strict` (`disallow_any_generics`), so
the helper annotates `list[dict[str, Any]]`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_short_pipeline.py -v`
Expected: `ModuleNotFoundError: No module named 'video_editor_cli.short_pipeline'`.

- [ ] **Step 3: Implement the module**

Create `apps/cli/src/video_editor_cli/short_pipeline.py`:

```python
"""`ve short`: build a short from a committed content pack.

Stage order is fixed: validate → price → stills → voice → align → compile →
render → mux → normalise → QA. Every paid call is preceded by an approval the
run re-prices itself, and the QA pass measures the file that ships.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import typer
from video_editor_asset_ingestion.config import AssetProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.pricing import PricingRegistry
from video_editor_core.composition import Canvas, canvas_for_aspect
from video_editor_core.content_pack import (
    END_CARD_ID,
    ContentPack,
    still_specs,
)
from video_editor_core.designs import DesignError, DesignTokens, load_design
from video_editor_core.layer_presets import caption, chip, end_card, pill, scrims
from video_editor_state.job import JobContext

from video_editor_cli.commands.estimate import _format_table
from video_editor_cli.explainer_pipeline import price_requests
from video_editor_cli.short_captions import caption_style

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

    A terminal period is folded into the join rather than stripped outright:
    without a suffix the prompt is sent exactly as the pack wrote it.
    """
    body = prompt.strip()
    suffix = pack.image.style_suffix.strip()
    if not suffix:
        return body
    return f"{body.rstrip('.')}. {suffix}"


def short_job_requests(pack: ContentPack) -> list[AssetRequest]:
    """Every paid request this build makes — the list the gate prices."""
    requests: list[AssetRequest] = []
    for still in still_specs(pack):
        scene = next(
            scene for scene in pack.scenes if scene.still.key == still.key
        )
        requests.append(
            AssetRequest(
                asset_id=f"still_{still.key}",
                job_id=pack.job_id,
                scene_id=scene.id,
                asset_type=AssetType.IMAGE,
                prompt=_still_prompt(pack, still.prompt),
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
    if pack.end_card.say.strip():
        requests.append(
            AssetRequest(
                asset_id=f"line_{END_CARD_ID}_0",
                job_id=pack.job_id,
                scene_id=END_CARD_ID,
                asset_type=AssetType.TTS,
                prompt=pack.end_card.say.strip(),
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
    try:
        tokens = load_design(pack.design)
    except DesignError as exc:
        raise ShortPreflightError(str(exc)) from exc
    width, height = canvas_for_aspect(pack.aspect)
    canvas = Canvas(width=width, height=height, fps=SHORT_FPS)
    # Every pill the pack can reach: ``pill`` stacks by index, so a scene with
    # three ``show`` lines needs rows 0-2 inside the safe area, not just row 0.
    pills = max(
        (sum(1 for line in scene.lines if line.show) for scene in pack.scenes),
        default=1,
    )
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
        (f"pill {index}", partial(pill, tokens, canvas, index=index))
        for index in range(max(pills, 1))
    )
    for name, check in checks:
        try:
            check()
        except ValueError as exc:
            # ``LayerPresetError`` is a ``ValueError``; so are the captions
            # skill's unknown preset and a non-numeric design value.
            raise ShortPreflightError(f"design {pack.design!r} {name}: {exc}") from exc
    if not pack.end_card.text.strip():
        raise ShortPreflightError(
            "the pack has no end_card.text; the closing card would be blank"
        )
    return tokens


def estimate_short(
    ctx: JobContext,
    pack: ContentPack,
    *,
    config_path: Path | None = None,
    auto_approve: bool = False,
) -> Path:
    """Price the build's real request list, ask for approval, and record it."""
    requests = short_job_requests(pack)
    registry = PricingRegistry.load()
    registry.check_freshness()
    items = price_short_job(
        requests,
        registry,
        image_chain=AssetProviderConfig.load(config_path).image_chain,
    )
    total_cost = round(sum(float(item["total_cost"]) for item in items), 4)

    typer.echo(
        f"\nEstimated costs for {ctx.job_id} ({len(pack.scenes)} scenes, "
        f"{len(still_specs(pack))} stills):"
    )
    typer.echo(_format_table(items, total_cost))
    unpriced = [
        item
        for item in items
        if registry.unit_cost(item["provider"], item["model"], item["task_type"])
        is None
    ]
    if unpriced:
        missing = ", ".join(
            f"{item['provider']}/{item['model']} ({item['task_type']})"
            for item in unpriced
        )
        raise typer.BadParameter(
            f"no price for {missing}; add it to models.yaml or choose a different "
            "provider. Refusing to approve an estimate that omits this spend."
        )
    typer.echo(f"\nVoice: {pack.voice.voice_id}")

    if not auto_approve:
        typer.echo(f"\nTotal: ${total_cost:.4f}")
        answer = typer.prompt("Proceed?", default="n", show_default=False)
        if answer.lower() not in ("y", "yes"):
            typer.echo("Aborted. Edit the pack or re-run with different providers.")
            raise typer.Exit(0)

    estimate_path = ctx.work_dir / "plan" / "cost_estimate.json"
    estimate_path.parent.mkdir(parents=True, exist_ok=True)
    estimate_path.write_text(
        json.dumps(
            {
                "job_id": ctx.job_id,
                "approved_at": datetime.now(UTC).isoformat(),
                "voice_id": pack.voice.voice_id,
                "line_items": items,
                "total_cost": total_cost,
                "currency": "USD",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    ctx.manifest().stage_done(
        "estimate", {"cost_estimate": str(estimate_path), "total_cost": total_cost}
    )
    ctx.store.save()
    typer.echo(f"Cost estimate approved and saved to {estimate_path}")
    return estimate_path
```

Then give the **run-side** gate the same pricer, so a re-price cannot disagree
with the approval it is compared against. In
`apps/cli/src/video_editor_cli/explainer_pipeline.py`, the module already
imports `Sequence` from `collections.abc`; add `Callable` to that line, then
change `check_approved_spend`'s signature and its one pricing call:

```python
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
    """
    registry = PricingRegistry.load()
    items = price(requests, registry, image_chain=image_chain)
```

Keep the rest of the function — the unpriced refusal, `approved_total`, the
comparison and its error message — exactly as it is. The default keeps
`estimate_explainer` and its tests on `price_requests`, which is the correct
billing unit for the explainer's single narration call.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_short_pipeline.py -v`
Expected: `13 passed`.

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run mypy apps packages && uv run ruff check
git add apps/cli/src/video_editor_cli/short_pipeline.py \
    apps/cli/src/video_editor_cli/explainer_pipeline.py tests/test_short_pipeline.py
git commit -m "feat(cli): price and preflight a content-pack build

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 8: Stills stage

**Files:**
- Modify: `apps/cli/src/video_editor_cli/short_pipeline.py`
- Modify: `tests/test_short_pipeline.py`

**Interfaces:**
- Produces: `StillAssets(paths: dict[str, Path], results: tuple[AssetResult, ...])` and `generate_stills(ctx, pack, *, config_path=None) -> StillAssets`. `paths` is keyed by still key, so the adapter's `stills=` mapping takes it directly.
- The cache is content-addressed on `(asset_id, prompt, provider, params)`, so an edited prompt generates again while an unchanged one is free.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_short_pipeline.py`:

```python
def stub_pack() -> ContentPack:
    data = json.loads(json.dumps(PACK))
    data["image"]["provider"] = "stub_image"
    data["voice"]["provider"] = "stub_tts"
    data["voice"]["voice_id"] = "en_US-lessac-medium"
    return ContentPack.model_validate(data)


def test_stills_are_generated_once_per_key(tmp_path: Path) -> None:
    from video_editor_cli.short_pipeline import generate_stills

    assets = generate_stills(make_ctx(tmp_path), stub_pack())
    assert set(assets.paths) == {"rat"}
    assert assets.paths["rat"].is_file()
    assert [result.asset_id for result in assets.results] == ["still_rat"]


def test_a_second_build_reuses_the_cached_still(tmp_path: Path) -> None:
    from video_editor_cli.short_pipeline import generate_stills

    ctx = make_ctx(tmp_path)
    first = generate_stills(ctx, stub_pack())
    second = generate_stills(ctx, stub_pack())
    assert second.paths["rat"] == first.paths["rat"]
    assert second.results[0].cached is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_short_pipeline.py -v -k "generated_once or cached_still"`
Expected: `2 failed`, each with `ImportError: cannot import name 'generate_stills'`.
(`-k still` would also select the three pre-existing tests whose names mention
"still", which is why the selector names the two new tests.)

- [ ] **Step 3: Implement the stage**

Task 7's module dropped the imports its first half did not use (ruff `F401`),
so add these back alongside the code. The exact final import block (ruff sorts
straight imports before from-imports inside a section, so `asyncio` goes above
`json` and `dataclass` between `collections.abc` and `datetime`):

```python
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import typer
from video_editor_asset_ingestion.config import AssetProviderConfig
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
```

The rest of the module's imports are unchanged; `AssetResult` joins the
existing `models` line rather than a second statement (`I001`).

Append to `short_pipeline.py`:

```python
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
) -> list[AssetResult]:
    """Buy (or reuse) every request in one manager batch."""
    config = AssetProviderConfig.load(config_path)
    manager = AssetIngestionManager(ctx, config=config)
    return asyncio.run(manager.resolve(requests))


def generate_stills(
    ctx: JobContext,
    pack: ContentPack,
    *,
    config_path: Path | None = None,
) -> StillAssets:
    """One image per generated still key; aliases cost nothing."""
    requests = [
        request
        for request in short_job_requests(pack)
        if request.asset_type == AssetType.IMAGE
    ]
    results = _resolve(ctx, requests, config_path=config_path)
    by_id = {result.asset_id: result for result in results}
    paths: dict[str, Path] = {}
    for request in requests:
        result = by_id.get(request.asset_id)
        if result is None:
            raise ShortPipelineError(
                f"the still request {request.asset_id!r} returned no asset"
            )
        paths[request.asset_id.removeprefix("still_")] = result.resolved_path()
    return StillAssets(paths=paths, results=tuple(results))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_short_pipeline.py -v`
Expected: `15 passed` (the 13 Task 7 tests plus these two).

- [ ] **Step 5: Commit**

```bash
uv run mypy apps packages && uv run ruff check
git add apps/cli/src/video_editor_cli/short_pipeline.py tests/test_short_pipeline.py
git commit -m "feat(cli): generate one still per content-pack key

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 9: Voiceover and alignment stage

**Files:**
- Modify: `apps/cli/src/video_editor_cli/short_pipeline.py`
- Modify: `apps/cli/src/video_editor_cli/explainer_timing.py`
- Modify: `tests/test_short_pipeline.py`

**Interfaces:**
- Consumes: `align_words`, `spread_pinned_tail` (`explainer_timing`), `probe_media`; `_resolve(ctx, requests, *, config_path=None) -> list[AssetResult]` and the test helper `stub_pack()` — both shipped by Task 8, already in the module and the test file.
- Produces: `group_words_by_narration(narrations, words) -> list[list[dict]]` (public wrapper over the existing `_split_by_offset`); `VoiceTrack(path: Path, duration: float, results: tuple[AssetResult, ...])`; `generate_voice(ctx, pack, *, config_path=None) -> VoiceTrack`; `align_scenes(pack, *, audio_duration: float, transcript: Mapping[str, Any]) -> Alignment`; `_transcribe(audio, ctx, config_path=None) -> dict[str, Any]` (the module-level seam tests monkeypatch).
- `stub_pack()` points both providers at stubs (`stub_image`, `stub_tts`), so the voice test renders three 1.0 s tones and makes no network call.
- Per-line TTS is deliberate: one request per line honours `speak_as` and lets `gap_seconds` land between lines. The lines are concatenated into one track so the transcript and the captions stay continuous.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_short_pipeline.py`:

```python
TRANSCRIPT = {
    "words": [
        {"word": "You", "start": 0.10, "end": 0.30},
        {"word": "think", "start": 0.30, "end": 0.50},
        {"word": "you're", "start": 0.50, "end": 0.70},
        {"word": "a", "start": 0.70, "end": 0.80},
        {"word": "Rat", "start": 1.10, "end": 1.40},
        {"word": "Your", "start": 2.10, "end": 2.30},
        {"word": "animal", "start": 2.30, "end": 2.60},
        {"word": "is", "start": 2.60, "end": 2.70},
        {"word": "the", "start": 2.70, "end": 2.80},
        {"word": "year", "start": 2.80, "end": 3.00},
        {"word": "branch", "start": 3.00, "end": 3.40},
        {"word": "Find", "start": 4.10, "end": 4.30},
        {"word": "yours", "start": 4.30, "end": 4.60},
        {"word": "free", "start": 4.60, "end": 4.80},
        {"word": "at", "start": 4.80, "end": 4.90},
        {"word": "cosmicsaju", "start": 4.90, "end": 5.60},
    ]
}


def test_align_scenes_windows_start_where_each_scene_is_spoken() -> None:
    from video_editor_cli.short_pipeline import align_scenes

    alignment = align_scenes(make_pack(), audio_duration=6.0, transcript=TRANSCRIPT)
    assert [scene.id for scene in alignment.scenes] == ["hook", "branch", "end_card"]
    assert alignment.duration == pytest.approx(6.0)
    assert alignment.scenes[0].start == pytest.approx(0.0)
    assert alignment.scenes[1].start == pytest.approx(2.10)
    assert alignment.scenes[-1].end == pytest.approx(6.0)
    assert alignment.scenes[0].words[0].text == "You"


def test_a_scene_with_no_timed_words_fails_the_build() -> None:
    from video_editor_cli.short_pipeline import ShortPipelineError, align_scenes

    # The guard walks the scenes in order, so with a transcript that heard
    # nothing at all it names the first one; the trailing case (only the end
    # card unheard) is covered by the same check, one iteration later.
    with pytest.raises(ShortPipelineError, match="hook"):
        align_scenes(make_pack(), audio_duration=6.0, transcript={"words": []})


def test_group_words_by_narration_splits_on_the_narration_text() -> None:
    from video_editor_cli.explainer_timing import (
        align_words,
        group_words_by_narration,
    )

    narrations = ["You think you're a Rat?", "Your animal is the year branch."]
    words = align_words(narrations, TRANSCRIPT["words"])
    groups = group_words_by_narration(narrations, words)
    assert [" ".join(word["word"] for word in group) for group in groups] == narrations


def test_the_voice_track_holds_the_gap_between_lines(tmp_path: Path) -> None:
    from video_editor_cli.short_pipeline import generate_voice

    track = generate_voice(make_ctx(tmp_path), stub_pack())
    # Three lines of the stub's 1.0 s tone, two 0.28 s gaps, ±40 ms of codec.
    assert track.duration == pytest.approx(3.56, abs=0.04)
    assert track.path.is_file()
    assert len(track.results) == 3


def test_a_line_the_voice_dropped_fails_the_build() -> None:
    """An unheard interior line is not an empty group — it is a pinned one.

    ``_offset_to_time`` maps an unmatched region onto the next matched block,
    so dropping the branch line leaves its words sharing the end card's first
    start. The empty-group guard alone would let a one-frame flash scene ship.
    """
    from video_editor_cli.short_pipeline import ShortPipelineError, align_scenes

    dropped = [
        word
        for word in TRANSCRIPT["words"]
        if word["start"] < 1.28 or word["start"] >= 4.10
    ]
    with pytest.raises(ShortPipelineError, match="branch"):
        align_scenes(make_pack(), audio_duration=6.0, transcript={"words": dropped})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_short_pipeline.py -v`
Expected: `5 failed, 15 passed` — the five new tests fail with
`ImportError: cannot import name 'align_scenes'` (or
`group_words_by_narration`, or `generate_voice`).

- [ ] **Step 3: Expose the narration grouping**

In `apps/cli/src/video_editor_cli/explainer_timing.py`, after `_split_by_offset`, add:

```python
def group_words_by_narration(
    narrations: Sequence[str], words: Sequence[Mapping[str, Any]]
) -> list[list[dict[str, Any]]]:
    """Group aligned display words by the narration they came from.

    Public because the short pipeline splits a pack's lines the same way the
    explainer splits its scenes, and duplicating the offset walk would be a
    second source of truth for where one line ends and the next begins.
    """
    return _split_by_offset(narrations, words)
```

- [ ] **Step 4: Implement the voice and alignment stages**

Append to `short_pipeline.py`:

```python
@dataclass(frozen=True)
class VoiceTrack:
    """The concatenated narration, its measured length and its purchases."""

    path: Path
    duration: float
    results: tuple[AssetResult, ...]


def _concat_narration(segments: list[Path], gap: float, output: Path) -> Path:
    """Join the per-line files into one track with ``gap`` seconds between."""
    output.parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    filters: list[str] = []
    labels: list[str] = []
    index = 0
    for position, segment in enumerate(segments):
        command.extend(["-i", str(segment)])
        filters.append(
            f"[{index}:a]aresample=44100,"
            f"aformat=sample_fmts=fltp:channel_layouts=mono[a{index}]"
        )
        labels.append(f"[a{index}]")
        index += 1
        if gap > 0 and position + 1 < len(segments):
            command.extend(
                ["-f", "lavfi", "-t", f"{gap:.3f}", "-i", "anullsrc=r=44100:cl=mono"]
            )
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
    run_ffmpeg(command)
    return output


def generate_voice(
    ctx: JobContext,
    pack: ContentPack,
    *,
    config_path: Path | None = None,
) -> VoiceTrack:
    """Read every line, then concatenate them into one narration track."""
    requests = [
        request
        for request in short_job_requests(pack)
        if request.asset_type == AssetType.TTS
    ]
    results = _resolve(ctx, requests, config_path=config_path)
    by_id = {result.asset_id: result for result in results}
    segments: list[Path] = []
    for request in requests:
        result = by_id.get(request.asset_id)
        if result is None:
            raise ShortPipelineError(
                f"the voice request {request.asset_id!r} returned no asset"
            )
        segments.append(result.resolved_path())
    narration = _concat_narration(
        segments, pack.voice.gap_seconds, ctx.work_dir / "narration.wav"
    )
    duration = probe_media(narration).duration_ms() / 1000.0
    ctx.manifest().stage_done(
        "voice", {"narration": str(narration), "seconds": duration}
    )
    return VoiceTrack(path=narration, duration=duration, results=tuple(results))


def _transcribe(
    audio: Path, ctx: JobContext, config_path: Path | None = None
) -> dict[str, Any]:
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


def align_scenes(
    pack: ContentPack,
    *,
    audio_duration: float,
    transcript: Mapping[str, Any],
) -> Alignment:
    """Turn the transcript into contiguous scene windows tiling the audio."""
    spoken = spoken_lines(pack)
    narrations = [text for _, text in spoken]
    words = align_words(narrations, transcript.get("words") or [])
    spread_pinned_tail(words, audio_duration)
    groups = group_words_by_narration(narrations, words)

    order: list[str] = []
    per_scene: dict[str, list[dict[str, Any]]] = {}
    for (scene_id, _), group in zip(spoken, groups, strict=True):
        if scene_id not in per_scene:
            per_scene[scene_id] = []
            order.append(scene_id)
        per_scene[scene_id].extend(group)

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
                        start=float(word["start"]),
                        end=float(word["end"]),
                    )
                    for word in scene_words
                ],
            )
        )
    return Alignment(duration=ends[-1], scenes=scenes)
```

Extend the module's imports to exactly this set (Task 7's lines, plus the new
members; ruff's `order-by-type` puts `END_CARD_ID` before the classes and the
classes before the functions inside a from-import, so `Alignment`, `TimedScene`
and `TimedWord` sit between them — a second statement for any of these trips
`I001`):

```python
import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import typer
from video_editor_asset_ingestion.config import AssetProviderConfig
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.pricing import PricingRegistry
from video_editor_core.composition import Canvas, canvas_for_aspect, quantise
from video_editor_core.content_pack import (
    END_CARD_ID,
    Alignment,
    ContentPack,
    TimedScene,
    TimedWord,
    spoken_lines,
    still_specs,
)
from video_editor_core.designs import DesignError, DesignTokens, load_design
from video_editor_core.layer_presets import caption, chip, end_card, pill, scrims
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.run import run_ffmpeg
from video_editor_state.job import JobContext
from video_editor_tools.transcription import resolve_transcription_settings

from video_editor_cli.commands.estimate import _format_table
from video_editor_cli.explainer_pipeline import price_requests
from video_editor_cli.explainer_timing import (
    align_words,
    group_words_by_narration,
    spread_pinned_tail,
)
from video_editor_cli.short_captions import caption_style
```

**One defect this stage must not hide:** `_transcribe` is the only whisper call in the build, so the QA "max silent gap" measurement reads the raw transcript. `run_short_job` keeps that transcript (Task 10) rather than re-measuring the aligned stream, whose ends are pinned to the next word's start.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_short_pipeline.py -v`
Expected: `20 passed` (the 15 from Tasks 7–8 plus these five).

- [ ] **Step 6: Commit**

```bash
uv run mypy apps packages && uv run ruff check
git add apps/cli/src/video_editor_cli/short_pipeline.py apps/cli/src/video_editor_cli/explainer_timing.py tests/test_short_pipeline.py
git commit -m "feat(cli): read the pack line by line and align its scenes

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 10: Compile, render and QA wiring, plus `ve short`

**Files:**
- Modify: `apps/cli/src/video_editor_cli/short_pipeline.py`
- Modify: `apps/cli/src/video_editor_cli/composition_qa.py:180-250`
- Modify: `apps/cli/src/video_editor_cli/explainer_pipeline.py` (rename two private helpers)
- Modify: `apps/cli/src/video_editor_cli/main.py` (add the command)
- Modify: `tests/test_short_pipeline.py`, `tests/test_composition_qa.py`

**Interfaces:**
- Consumes: `pack_to_composition`, `caption_layers`, `compile_composition`, `render_project`, `mux_voiceover`, `normalize_loudness`, `gather_composition_qa`, `evaluate_qa`, `qa_report`, `format_qa`, `check_approved_spend`, `price_short_job` (Task 7), `build_cost`, `cost_check_values`, `measure_loudness`, `max_silent_gap`.
- Produces: `ShortResult(final_path, composition_path, qa_path, passed, checks)`, `run_short_job(ctx, pack, tokens, *, config_path=None, cli_path="hyperframes") -> ShortResult`, and the `ve short` command. `cli_path` exists so the offline end-to-end test in Task 11 can hand over an absolute path the conftest mask cannot hide.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_composition_qa.py` (this file already imports `Path`,
`pytest`, `Canvas`, `Composition`, `CompositionScene` and `gather_composition_qa`
— do not re-import them in the test bodies):

Every test here calls the file's own `_stub_externals(monkeypatch)` helper
first: without it `gather_composition_qa` execs the real `hyperframes` binary,
which CI does not install, and the suite goes red on a host that lacks it while
passing silently on one that has it.

```python
def test_gather_composition_qa_fails_closed_without_a_cue_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_externals(monkeypatch)
    composition = Composition(
        job_id="demo",
        canvas=Canvas(width=1080, height=1920, fps=30),
        duration=1.0,
        scenes=[CompositionScene(id="hook", start=0.0, end=1.0)],
    )
    checks = gather_composition_qa(
        composition_dir=tmp_path,
        composition=composition,
    )
    cues = next(check for check in checks if check.name == "cues")
    assert not cues.ok
    assert "cues were not measured" in cues.detail


def test_gather_composition_qa_accepts_resolved_cues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_externals(monkeypatch)
    composition = Composition(
        job_id="demo",
        canvas=Canvas(width=1080, height=1920, fps=30),
        duration=1.0,
        scenes=[CompositionScene(id="hook", start=0.0, end=1.0)],
    )
    checks = gather_composition_qa(
        composition_dir=tmp_path,
        composition=composition,
        declared_cues=2,
        resolved_cues=2,
    )
    assert next(check for check in checks if check.name == "cues").ok


def test_gather_composition_qa_fails_the_cue_gate_on_a_shortfall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without this case an implementation that always reported "measured, no
    # misses" would pass both tests above.
    _stub_externals(monkeypatch)
    composition = Composition(
        job_id="demo",
        canvas=Canvas(width=1080, height=1920, fps=30),
        duration=1.0,
        scenes=[CompositionScene(id="hook", start=0.0, end=1.0)],
    )
    checks = gather_composition_qa(
        composition_dir=tmp_path,
        composition=composition,
        declared_cues=2,
        resolved_cues=1,
    )
    cues = next(check for check in checks if check.name == "cues")
    assert not cues.ok
    assert "1 declared cues did not resolve" in cues.detail


def test_gather_composition_qa_refuses_a_plan_and_a_composition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_externals(monkeypatch)
    composition = Composition(
        job_id="demo",
        canvas=Canvas(width=1080, height=1920, fps=30),
        duration=1.0,
        scenes=[CompositionScene(id="hook", start=0.0, end=1.0)],
    )
    with pytest.raises(ValueError, match="pass plan or composition, not both"):
        gather_composition_qa(
            composition_dir=tmp_path,
            plan=_one_scene_plan(),
            composition=composition,
        )
```

Append to `tests/test_short_pipeline.py`:

```python
def test_run_short_job_runs_each_stage_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from video_editor_cli import short_pipeline

    order: list[str] = []

    def record(name: str):
        def step(*args, **kwargs):
            order.append(name)
            raise AssertionError(f"stop after {name}")

        return step

    # The approval gate is the first thing the job does and it reads a file
    # this test never writes, so stub it: an unstubbed gate raises
    # ``ShortPipelineError`` before ``generate_stills`` is ever called and the
    # test would pass for the wrong reason.
    monkeypatch.setattr(short_pipeline, "check_approved_spend", lambda *a, **k: 0.0)
    monkeypatch.setattr(
        short_pipeline, "generate_stills", record("stills")
    )
    with pytest.raises(AssertionError, match="stop after stills"):
        short_pipeline.run_short_job(
            make_ctx(tmp_path), make_pack(), preflight_short(make_pack())
        )
    assert order == ["stills"]


def test_run_short_job_gates_spend_before_any_paid_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordering test above stubs the gate to succeed, so it still passes
    if the gate were moved after the paid stills stage. This one pins the
    refusal: nothing that can spend money may be reached without approval."""
    from video_editor_cli import short_pipeline
    from video_editor_cli.short_pipeline import ShortPipelineError

    reached: list[str] = []

    def no_approval(*args: object, **kwargs: object) -> float:
        raise ValueError("no approval on file")

    def record_stills(*args: object, **kwargs: object) -> object:
        reached.append("stills")
        raise AssertionError("generate_stills ran before the spend gate")

    monkeypatch.setattr(short_pipeline, "check_approved_spend", no_approval)
    monkeypatch.setattr(short_pipeline, "generate_stills", record_stills)
    with pytest.raises(ShortPipelineError, match="no approval on file"):
        short_pipeline.run_short_job(
            make_ctx(tmp_path), make_pack(), preflight_short(make_pack())
        )
    assert reached == []
```

The first test in this file is a scaffold: it pins the *first* stage. The full order is asserted once the offline end-to-end test in Task 11 exercises every stage.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_composition_qa.py tests/test_short_pipeline.py -v -k "gather or run_short"`
Expected: `6 failed, 4 passed` — the four existing `gather_composition_qa`
tests still pass on `plan=`, while the four new gather tests fail because
`gather_composition_qa` refuses the keyword `composition` (three times) and
does not yet raise on a plan-plus-composition pair, and both `run_short_job`
tests fail with `AttributeError` because the function is not defined.

- [ ] **Step 3: Extend `gather_composition_qa`**

In `apps/cli/src/video_editor_cli/composition_qa.py`, add the import:

```python
from video_editor_core.composition import Composition
```

Replace the signature and first lines:

```python
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
```

Then, in the same function, rename the local uses:

```python
    undeclared = tuple(undeclared_glyphs(layer_texts(resolved), manifest))
```

```python
    violations = composition_safe_area_violations(resolved)
```

```python
        at=[(scene.start + scene.end) / 2 for scene in resolved.scenes],
```

and the final call:

```python
        CompositionQaInputs(
            undeclared=undeclared,
            missing_cues=missing_cues,
            cues_measured=cues_measured,
            safe_area=safe_area,
            composition_report=report,
            frame_stddevs=stddevs,
            visual_reason=visual_reason,
        )
```

Then give the gate a way to report an unmeasured cue pass. In the same file, add the field to `CompositionQaInputs`:

```python
    missing_cues: tuple[str, ...] = ()
    #: False when the caller supplied neither a plan nor a resolved-cue count.
    #: An unmeasured cue gate must fail, never pass on an empty tuple.
    cues_measured: bool = True
```

and replace the `cues` check in `evaluate_composition_qa`:

```python
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
```

Also replace the stale comment above `violations` with:

```python
    # The scene-plan path emits one full-canvas ``html`` layer per typographic
    # scene, so it has no per-text rect to measure. The pack path places
    # captions, chips, pills and the end card as real layers, and this gate
    # measures those rects.
```

- [ ] **Step 4: Rename the two explainer helpers**

In `apps/cli/src/video_editor_cli/explainer_pipeline.py`:

- `def _loudness(path: Path) -> tuple[float, float]:` → `def measure_loudness(path: Path) -> tuple[float, float]:` and update its single call site (`loudness_lufs, true_peak_db = _loudness(final)` → `measure_loudness(final)`).
- `def _max_silent_gap(words: Sequence[dict[str, Any]]) -> float:` → `def max_silent_gap(words: Sequence[dict[str, Any]]) -> float:` and update its call site (`max_silent_gap=_max_silent_gap(raw_words) if raw_words else math.inf`).

- [ ] **Step 5: Implement `run_short_job`**

Append to `short_pipeline.py`:

```python
@dataclass(frozen=True)
class ShortResult:
    final_path: Path
    composition_path: Path
    qa_path: Path
    passed: bool
    checks: list[QaCheck]


def _caption_coverage(
    alignment: Alignment, captions: Sequence[CompositionLayer]
) -> float:
    words = sum(len(scene.words) for scene in alignment.scenes)
    if not words:
        return 0.0
    covered = sum(len(layer.content.spans) for layer in captions)
    return min(1.0, covered / words)


def run_short_job(
    ctx: JobContext,
    pack: ContentPack,
    tokens: DesignTokens,
    *,
    config_path: Path | None = None,
    cli_path: str = "hyperframes",
) -> ShortResult:
    """Stills → voice → alignment → compile → render → mux → normalise → QA."""
    ctx.ensure_dirs()
    requests = short_job_requests(pack)
    estimate_path = ctx.work_dir / "plan" / "cost_estimate.json"
    image_chain = AssetProviderConfig.load(config_path).image_chain
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

    stills = generate_stills(ctx, pack, config_path=config_path)
    voice = generate_voice(ctx, pack, config_path=config_path)
    transcript = _transcribe(voice.path, ctx, config_path)
    alignment = align_scenes(
        pack, audio_duration=voice.duration, transcript=transcript
    )
    (ctx.work_dir / "plan" / "alignment.json").write_text(
        json.dumps(alignment.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    width, height = canvas_for_aspect(pack.aspect)
    canvas = Canvas(width=width, height=height, fps=SHORT_FPS)
    captions = caption_layers(tokens, alignment, canvas=canvas, fps=SHORT_FPS)
    composition = pack_to_composition(
        pack, tokens, alignment, stills=stills.paths, captions=captions, fps=SHORT_FPS
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
    final = normalize_loudness(voiced, ctx.output_dir / "final.mp4")

    probe = probe_media(final)
    duration = probe.duration_ms() / 1000.0
    try:
        loudness_lufs, true_peak_db = measure_loudness(final)
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        logger.warning("short_loudness_unmeasured", error=str(exc))
        loudness_lufs, true_peak_db = math.nan, math.nan
    results = [*stills.results, *voice.results]
    cost_usd, estimate_usd = cost_check_values(
        estimate_path, build_cost(results, requests)
    )

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
            )
        except Exception as exc:  # noqa: BLE001 - a gate that cannot run must fail
            logger.warning("short_composition_gates_failed", error=str(exc))
            composition_checks = [QaCheck("composition_gates", False, str(exc))]
    else:
        composition_checks = [
            QaCheck("composition_gates", False, "no composition project to gate")
        ]

    first_word_starts = [
        scene.words[0].start if scene.words else math.inf
        for scene in alignment.scenes
    ]
    raw_words = transcript.get("words") or []
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
        caption_coverage=_caption_coverage(alignment, captions),
        still_scenes=len(pack.scenes),
        still_renders=sum(
            1 for result in stills.results if result.asset_type == AssetType.IMAGE
        ),
        expected_still_renders=len(still_specs(pack)),
        max_silent_gap=max_silent_gap(raw_words) if raw_words else math.inf,
        tail_seconds=(
            duration - alignment.scenes[-1].words[-1].end
            if alignment.scenes[-1].words
            else math.inf
        ),
        cost_usd=cost_usd,
        estimate_usd=estimate_usd,
    )
    checks = evaluate_qa(inputs, composition_checks)
    qa_path = ctx.work_dir / "qa.json"
    qa_path.write_text(json.dumps(qa_report(inputs, checks), indent=2), encoding="utf-8")
    typer.echo(format_qa(checks))
    passed = all(check.ok for check in checks)
    if passed:
        ctx.manifest().stage_done("qa", {"qa": str(qa_path)})
    logger.info("short_complete", job_id=ctx.job_id, final=str(final), passed=passed)
    return ShortResult(
        final_path=final,
        composition_path=composition_path,
        qa_path=qa_path,
        passed=passed,
        checks=checks,
    )
```

Bring the module's imports to exactly this set. `math` joins the stdlib group
after `json`, `structlog` sits immediately above `typer`, and
`CompositionLayer` joins the composition from-import after `Canvas` (the
class-before-functions order ruff's `order-by-type` enforces) — a second
statement for any of them trips `I001`. `_caption_coverage`'s annotation names
`CompositionLayer`, so it is not optional even under
`from __future__ import annotations` (`mypy --strict` resolves it):
`price_requests` stays imported because `price_short_job` calls it.

```python
import asyncio
import json
import math
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import structlog
import typer
from video_editor_asset_ingestion.config import AssetProviderConfig
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.pricing import PricingRegistry
from video_editor_core.adapters.from_content_pack import pack_to_composition
from video_editor_core.compile_composition import compile_composition
from video_editor_core.composition import (
    Canvas,
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
    spoken_lines,
    still_specs,
)
from video_editor_core.cues import resolve_cues
from video_editor_core.designs import DesignError, DesignTokens, load_design
from video_editor_core.hyperframes_cli import render_project
from video_editor_core.layer_presets import caption, chip, end_card, pill, scrims
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.voiceover import mux_voiceover
from video_editor_state.job import JobContext
from video_editor_tools.transcription import resolve_transcription_settings

from video_editor_cli.commands.estimate import _format_table
from video_editor_cli.composition_qa import gather_composition_qa
from video_editor_cli.explainer_pipeline import (
    build_cost,
    check_approved_spend,
    cost_check_values,
    measure_loudness,
    max_silent_gap,
    normalize_loudness,
    price_requests,
)
from video_editor_cli.explainer_qa import (
    QaCheck,
    QaInputs,
    evaluate_qa,
    format_qa,
    qa_report,
)
from video_editor_cli.explainer_timing import (
    align_words,
    group_words_by_narration,
    spread_pinned_tail,
)
from video_editor_cli.short_captions import caption_layers, caption_style

logger = structlog.get_logger(__name__)
```

- [ ] **Step 6: Add the `ve short` command**

In `apps/cli/src/video_editor_cli/main.py`, after the `explain` command:

```python
@app.command()
def short(
    pack: Path = typer.Argument(
        ..., exists=True, dir_okay=False, help="Path to a pack's short.yaml"
    ),
    job_id: str = typer.Option("", "--job-id", help="Override the pack's job id."),
    work_root: str = _WORK_ROOT_OPT,
    output_root: str = _OUTPUT_ROOT_OPT,
    config_path: Path | None = _CONFIG_RUN_OPT,
    auto_approve: bool = _AUTO_APPROVE_OPT,
) -> None:
    """Build a short from a content pack: stills, voice, one render, QA."""
    from video_editor_cli.short_pipeline import (
        ShortPipelineError,
        ShortPreflightError,
        estimate_short,
        preflight_short,
        run_short_job,
    )
    from video_editor_core.content_pack import ContentPackError, load_content_pack

    try:
        content = load_content_pack(pack)
        tokens = preflight_short(content)
    except (ContentPackError, ShortPreflightError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    ctx = _context(job_id or content.job_id, work_root, output_root, config_path=config_path)
    estimate_short(ctx, content, config_path=config_path, auto_approve=auto_approve)
    try:
        result = run_short_job(ctx, content, tokens, config_path=config_path)
    except ShortPipelineError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"\nFinal: {result.final_path}")
    typer.echo(f"QA:    {result.qa_path} ({'PASS' if result.passed else 'FAIL'})")
    if not result.passed:
        raise typer.Exit(1)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_short_pipeline.py tests/test_composition_qa.py -q`
Expected: all pass.

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass, no new skips.

- [ ] **Step 9: Lint, type-check, commit**

```bash
uv run mypy apps packages && uv run ruff check
git add apps/cli/src/video_editor_cli/short_pipeline.py apps/cli/src/video_editor_cli/composition_qa.py apps/cli/src/video_editor_cli/explainer_pipeline.py apps/cli/src/video_editor_cli/main.py tests/test_short_pipeline.py tests/test_composition_qa.py
git commit -m "feat(cli): compile, render and gate a short from one Composition

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 11: The offline end-to-end proof

**Files:**
- Modify: `models.yaml` and `pricing.yaml` (the generated file)
- Create: `tests/fixtures/shorts/e2e-pack/short.yaml`
- Create: `tests/fixtures/shorts/e2e-pack/transcript.json`
- Create: `tests/test_short_e2e.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Consumes: everything from Tasks 1–10, plus a new `hyperframes_binary` fixture.
- Produces: `hyperframes_binary` (session-scoped `str` fixture) for any test that must render.

**Why the fixture pack declares no chip:** the bundled Noto Serif KR subset only gains 子 and 辛 in Task 12, and the composition gate fails on an undeclared glyph. The chip layer's placement is already covered by Task 4's unit tests; the e2e proves the *pipeline*.

**Why `models.yaml` changes:** the spend gate refuses a build containing a request it cannot price — `check_approved_spend` raises on `unit_cost(...) is None`. `stub_tts` and `stub_image` have no price today, so no offline build can pass the gate this plan exists to strengthen. Price them at exactly their cost, zero, the way `kokoro_tts` and `pexels` already are.

- [ ] **Step 1: Make the offline providers priceable**

In `models.yaml`, under `providers:`, beside the other local providers:

```yaml
  stub_tts:
    cost_per_1k_chars: 0.0
    currency: USD
    note: offline test tone; free by construction
  stub_image:
    cost_per_image: 0.0
    currency: USD
    note: offline test gradient; free by construction
```

Then regenerate the derived file — `pricing.yaml` says "GENERATED FILE - do not edit by hand":

```bash
uv run ve gen-models
git diff --stat pricing.yaml models.yaml
```

Expected: `pricing.yaml` gains the two provider blocks; `tests/test_models_registry_sync.py` is the guard that proves the two files agree. `last_updated` does **not** change — `ve gen-models` copies `models.yaml:last_updated` rather than stamping today, and this step does not bump the source date. Do not hand-edit the generated file.

- [ ] **Step 2: Write the fixture pack**

Create `tests/fixtures/shorts/e2e-pack/short.yaml`:

```yaml
# The offline proof pack: stub providers, two scenes, one alias still.
job_id: short-e2e
design: saju_series
aspect: "9:16"

voice:
  provider: stub_tts
  voice_id: en_US-lessac-medium
  language: en-US
  gap_seconds: 0.28

image:
  provider: stub_image
  resolution: ""
  style_suffix: "ink wash, no text"

end_card:
  say: "Find yours free at cosmicsaju.com."
  text: "Free chart snapshot"
  url: cosmicsaju.com
  tail_seconds: 1.2

scenes:
  - id: hook
    still:
      key: rat
      prompt: "a rat on a plum branch"
    lines:
      - say: "You think you're a Rat?"
        show: "Not just a Rat"
        cue: "Rat"
    motion: drift

  - id: branch
    still: {reuse: rat}
    lines:
      - say: "Your animal is the year branch."
        show: "1 of 8"
    motion: drift
```

Create `tests/fixtures/shorts/e2e-pack/transcript.json`. The stub voice reads each line as 1.0 s of tone with a 0.28 s gap, so the three lines occupy 0.0–1.0, 1.28–2.28 and 2.56–3.56. These words sit inside those windows:

```json
{
  "words": [
    {"word": "You", "start": 0.05, "end": 0.3},
    {"word": "think", "start": 0.3, "end": 0.55},
    {"word": "you're", "start": 0.55, "end": 0.78},
    {"word": "a", "start": 0.78, "end": 0.9},
    {"word": "Rat", "start": 0.9, "end": 1.0},
    {"word": "Your", "start": 1.35, "end": 1.6},
    {"word": "animal", "start": 1.6, "end": 1.95},
    {"word": "is", "start": 1.95, "end": 2.05},
    {"word": "the", "start": 2.05, "end": 2.15},
    {"word": "year", "start": 2.15, "end": 2.3},
    {"word": "branch", "start": 2.3, "end": 2.5},
    {"word": "Find", "start": 2.6, "end": 2.85},
    {"word": "yours", "start": 2.85, "end": 3.05},
    {"word": "free", "start": 3.05, "end": 3.2},
    {"word": "at", "start": 3.2, "end": 3.3},
    {"word": "cosmicsaju.com", "start": 3.3, "end": 3.55}
  ]
}
```

- [ ] **Step 3: Add the real-binary fixture**

In `tests/conftest.py`, after the existing `shutil.which` mask, add:

```python
@pytest.fixture(scope="session")
def hyperframes_binary() -> str:
    """The real ``hyperframes`` executable, found without ``shutil.which``.

    The autouse fixture above masks the binary from ``shutil.which`` so no
    test renders by accident. A render test needs the opposite, so this scans
    ``PATH`` itself and skips — never fails — when the tool is not installed.
    """
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry) / "hyperframes"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    pytest.skip("hyperframes is not installed; this test's render leg needs it")
```

Confirm `os`, `Path` and `pytest` are imported at the top of `tests/conftest.py`; add whichever is missing.

- [ ] **Step 4: Write the failing test**

Create `tests/test_short_e2e.py`:

```python
"""The whole short path, offline: stubs in, a gated MP4 out."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from video_editor_core.adapters.from_content_pack import pack_to_composition
from video_editor_core.composition import Canvas
from video_editor_core.content_pack import ContentPack, load_content_pack
from video_editor_state.job import JobContext

from video_editor_cli import short_pipeline
from video_editor_cli.short_captions import caption_layers
from video_editor_cli.short_pipeline import (
    align_scenes,
    estimate_short,
    preflight_short,
    run_short_job,
)

PACK_DIR = Path(__file__).parent / "fixtures" / "shorts" / "e2e-pack"


@pytest.fixture
def pack() -> ContentPack:
    return load_content_pack(PACK_DIR / "short.yaml")


@pytest.fixture
def transcript() -> dict[str, Any]:
    return json.loads((PACK_DIR / "transcript.json").read_text(encoding="utf-8"))


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    context = JobContext(
        job_id="short-e2e",
        work_root=tmp_path / "work",
        output_root=tmp_path / "out",
    )
    context.ensure_dirs()
    return context


def test_the_pipeline_builds_a_gated_short(
    ctx: JobContext,
    pack: ContentPack,
    transcript: dict[str, Any],
    hyperframes_binary: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        short_pipeline,
        "_transcribe",
        lambda audio, job, config=None: transcript,
    )
    tokens = preflight_short(pack)
    estimate_short(ctx, pack, auto_approve=True)
    result = run_short_job(ctx, pack, tokens, cli_path=hyperframes_binary)

    assert result.final_path.is_file()
    report = json.loads(result.qa_path.read_text(encoding="utf-8"))
    checks = {check["name"]: check for check in report["checks"]}
    failures = {
        name: check["detail"] for name, check in checks.items() if not check["ok"]
    }
    assert report["passed"], f"failing gates: {failures}"
    assert {"canvas", "loudness", "true_peak", "duration", "scene_starts",
            "captions", "stills", "silent_gaps", "tail", "cost",
            "cues"} <= set(checks)
    assert report["inputs"]["duration"] == pytest.approx(4.77, abs=0.15)
    assert result.passed is True


def test_the_composition_keeps_captions_above_the_safe_edge(
    pack: ContentPack, transcript: dict[str, Any]
) -> None:
    tokens = preflight_short(pack)
    alignment = align_scenes(pack, audio_duration=3.56, transcript=transcript)
    canvas = Canvas(width=1080, height=1920, fps=30)
    captions = caption_layers(tokens, alignment, canvas=canvas)
    composition = pack_to_composition(
        pack, tokens, alignment, stills={"rat": Path("rat.png")}, captions=captions
    )

    assert composition.duration == pytest.approx(4.7667, abs=1e-3)
    assert [scene.id for scene in composition.scenes] == ["hook", "branch", "end_card"]
    layers = {
        layer.id: layer for scene in composition.scenes for layer in scene.layers
    }
    assert {"end_card_text", "end_card_url"} <= set(layers)
    # The branch scene starts where "Your" is spoken (1.35 s). ``quantise``
    # rounds to the 30 fps grid, so 1.35 s is frame 40 = 1.3333 s, not 1.3667.
    assert layers["still_hook"].time.end == pytest.approx(1.3333, abs=1e-3)
    caption_layers_only = [
        layer for layer_id, layer in layers.items() if layer_id.startswith("caption_")
    ]
    assert caption_layers_only, "the composition carries no caption layers"
    for layer in caption_layers_only:
        assert layer.rect.y + layer.rect.height <= 1440
        assert layer.preset == "caption_line"
```

- [ ] **Step 5: Run the test and read what it measures**

Run: `uv run pytest tests/test_short_e2e.py -v`

Expected: either `2 passed`, or a failure that names a real gap — a
`failing gates: {...}` dict, a stage error, or a skipped render leg
("hyperframes is not installed"). Tasks 1–10 already implement every stage, so
this test is the measurement, not a to-do: a failure here is the task's real
work. Fix the stage that produced it; never loosen the assertion and never
widen a threshold in `explainer_qa`.

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/test_short_e2e.py -v`
Expected: `2 passed`, or **`1 passed, 1 skipped`** when `hyperframes` is absent — only the first test requests the `hyperframes_binary` fixture, so the render leg skips while the composition test still runs. A **failing gate** here is the point of the task: read the `failing gates: {...}` message and fix the stage that produced it — never loosen the assertion and never widen a threshold in `explainer_qa`.

- [ ] **Step 7: Run the whole suite, then commit**

```bash
uv run pytest -q && uv run mypy apps packages && uv run ruff check
git add models.yaml pricing.yaml tests/conftest.py tests/test_short_e2e.py tests/fixtures/shorts/e2e-pack
git commit -m "test(short): prove the pack path offline, stubs to gated MP4

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 12: The series design's glyphs and the first real pack

**Files:**
- Modify: `designs/glyphs/noto_serif_kr.json`
- Modify: `designs/saju_series.json` (reconcile with Task 2's shipped shape)
- Modify: `packages/core/src/video_editor_core/fonts/NotoSerifKR.ttf` (the rebuilt subset; the family list in `fonts.py` does not change)
- Create: `shorts/saju-v1-your-animal/short.yaml`
- Test: `tests/test_saju_v1_pack.py`

**Interfaces:**
- Consumes: `load_content_pack`, `still_specs`, `cue_phrases`, `spoken_lines`, `END_CARD_ID` (Task 1); `preflight_short`, `short_job_requests` (Task 7).
- Produces: the committed pack the paid build (Task 13) runs, and a bundled font that can render its chips.

**Why the manifest changes:** the pack stamps 子 (the Rat branch) and 辛 (the Yin Metal stem) on screen, and neither is in the bundle. `undeclared_glyphs` fails the composition gate on exactly this. Adding the two characters the pack uses — rather than all twelve branches and ten stems — keeps the subset honest to its consumers; the next short adds its own.

- [ ] **Step 1: Add the two chip glyphs to the manifest**

In `designs/glyphs/noto_serif_kr.json`, inside `characters`, add:

```json
    "子": {"reading": "ja", "meaning": "rat branch"},
    "辛": {"reading": "sin", "meaning": "yin metal stem"}
```

- [ ] **Step 2: Rebuild the bundled Noto Serif KR subset**

The builder is deterministic and reads this manifest (`"".join(sorted(characters))`), so the same inputs reproduce the same bytes.

```bash
curl -fL -o /tmp/CormorantGaramond.ttf \
  'https://raw.githubusercontent.com/google/fonts/main/ofl/cormorantgaramond/CormorantGaramond%5Bwght%5D.ttf'
curl -fL -o /tmp/NotoSerifKR.ttf \
  'https://raw.githubusercontent.com/google/fonts/main/ofl/notoserifkr/NotoSerifKR%5Bwght%5D.ttf'
uv run python scripts/build_font_subsets.py \
  --cormorant /tmp/CormorantGaramond.ttf --noto /tmp/NotoSerifKR.ttf
git status --short packages/core/src/video_editor_core/fonts
```

Expected: `packages/core/src/video_editor_core/fonts/NotoSerifKR.ttf` modified (two glyphs more), `CormorantGaramond.ttf` **unchanged** (its input list did not move). If Cormorant also changes, stop — the builder is not deterministic and that is its own defect.

- [ ] **Step 3: Reconcile the design file**

`designs/saju_series.json` must match Task 2's shipped shape: `chip` carries `font`/`size`/`color`/`y` (no `bar_color`), `pill` carries no `border` or `radius`, and `end_card` carries `url_font_size`. Compare against the JSON block in Task 2 and bring the file into line; `uv run pytest tests/test_designs.py tests/test_layer_presets.py -q` must stay green.

- [ ] **Step 4: Write the real pack**

Create `shorts/saju-v1-your-animal/short.yaml`:

```yaml
# Short 1 of the Cosmic Saju series: "You're not just one animal."
# Words and prompts only. Every colour, font and rect lives in
# designs/saju_series.json; this file may not carry styling.
job_id: saju-v1-your-animal
design: saju_series
aspect: "9:16"

voice:
  provider: sarvam_tts
  # Sarvam takes "sarvam:<speaker>"; the speaker id is the whole contract.
  voice_id: "sarvam:sarang_en_narration"
  model: bulbul:v4-flash
  language: en-IN
  gap_seconds: 0.28

image:
  provider: poyo_ai
  model: gpt-image-2.5-fixed
  resolution: 2K
  style_suffix: >-
    Korean ink-wash and minhwa folk-painting style, hand-painted on warm hanji
    paper texture, deep charcoal background, accents in the five traditional
    Korean colours (blue-green, red, yellow, white, black), soft gold
    highlights, calm and elegant, flat illustration, not photorealistic, no
    text, no letters, no characters, no watermark, vertical 9:16 composition,
    empty space in the top 15% and bottom 25% of the frame

end_card:
  say: "Find yours free at cosmicsaju.com."
  text: "Free chart snapshot"
  url: cosmicsaju.com
  tail_seconds: 1.2

scenes:
  - id: hook
    still:
      key: rat
      prompt: >-
        A single small rat sitting on an ink-wash plum branch under a pale
        moon, centred, lots of negative space
    lines:
      - say: "You think you're a Rat? That's one-eighth of you."
        show: "You're not just a Rat."
        cue: "Rat"
    motion: drift

  - id: eight_characters
    still:
      key: tiles
      prompt: >-
        Eight blank square wooden tiles laid in a 2 x 4 grid on hanji paper,
        soft overhead light, tiles empty
    lines:
      - say: "In Korean Saju, your birth gives you eight characters, not one."
        speak_as: "In Korean Sah-joo, your birth gives you eight characters, not one."
        show: "8 characters"
    motion: drift

  - id: four_pillars
    still:
      key: pillars
      prompt: >-
        Four tall carved stone pillars on a misty mountain ledge at dawn,
        pillars evenly spaced, plain surfaces
    lines:
      - say: >-
          Four pillars: the year, month, day and hour you were born. Each
          pillar has two characters.
        show: "Year · Month · Day · Hour"
    motion: drift

  - id: year_branch
    still: {reuse: pillars}
    chip: "子"
    lines:
      - say: "Your animal comes from just one of them, the year branch."
        show: "Rat = 1 of 8"
        cue: "year branch"
    motion: drift

  - id: day_master
    still:
      key: gem
      prompt: >-
        A single faceted white gemstone resting on dark silk, gentle gold rim
        light, macro view
    chip: "辛"
    lines:
      - say: >-
          The one that's actually you is the top of the day pillar, your Day
          Master. This person's is Yin Metal: a polished gem. Precise, refined,
          sensitive to rough handling.
        show: "Day Master: Yin Metal"
        cue: "Day Master"
    motion: drift

  - id: question
    still:
      key: bridge
      prompt: >-
        A figure seen from behind walking toward a softly glowing traditional
        Korean gate (hanok), dusk
    lines:
      - say: "There are ten Day Masters. Which one are you?"
        show: "Which one are you?"
        cue: "Which one are you"
    motion: drift
```

Two edits from the draft that matter: no `show` text carries a CJK glyph (the pill uses the Latin body font and its subset has no 八字), and the end card's text is short enough to sit on one line at phone size.

- [ ] **Step 5: Write the failing test**

Create `tests/test_saju_v1_pack.py`:

```python
"""The committed pack: contract, cost shape and authoring guards."""

from __future__ import annotations

import json
from pathlib import Path

from video_editor_core.content_pack import (
    END_CARD_ID,
    cue_phrases,
    load_content_pack,
    spoken_lines,
    still_specs,
)

from video_editor_cli.short_pipeline import preflight_short, short_job_requests

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "shorts" / "saju-v1-your-animal" / "short.yaml"


def test_the_pack_loads_with_the_expected_shape() -> None:
    pack = load_content_pack(PACK)
    assert pack.job_id == "saju-v1-your-animal"
    assert pack.design == "saju_series"
    assert pack.aspect == "9:16"
    assert [scene.id for scene in pack.scenes] == [
        "hook",
        "eight_characters",
        "four_pillars",
        "year_branch",
        "day_master",
        "question",
    ]
    assert [still.key for still in still_specs(pack)] == [
        "rat",
        "tiles",
        "pillars",
        "gem",
        "bridge",
    ]


def test_every_chip_glyph_is_in_the_bundled_font() -> None:
    manifest = json.loads(
        (ROOT / "designs" / "glyphs" / "noto_serif_kr.json").read_text(encoding="utf-8")
    )["characters"]
    pack = load_content_pack(PACK)
    missing = {
        character
        for scene in pack.scenes
        for character in scene.chip
        if character not in manifest
    }
    assert not missing, f"chips need glyphs the bundle cannot draw: {sorted(missing)}"


def test_the_pack_passes_preflight_against_the_shipped_design() -> None:
    tokens = preflight_short(load_content_pack(PACK))
    assert tokens.layers.caption_preset == "karaoke"


def test_the_pack_ends_on_the_call_to_action() -> None:
    pack = load_content_pack(PACK)
    spoken = spoken_lines(pack)
    assert spoken[-1] == (END_CARD_ID, "Find yours free at cosmicsaju.com.")
    assert all(text.strip() for _, text in spoken)


def test_every_cue_phrase_is_actually_said() -> None:
    pack = load_content_pack(PACK)
    ids = [scene.id for scene in pack.scenes]
    for key, phrase in cue_phrases(pack).items():
        scene_id, index = key.split(":")
        spoken = pack.scenes[ids.index(scene_id)].lines[int(index)].spoken
        assert phrase.lower() in spoken.lower(), (
            f"cue {key!r} names {phrase!r}, which its line never says"
        )


def test_the_build_is_five_stills_and_seven_lines() -> None:
    requests = short_job_requests(load_content_pack(PACK))
    assert len(requests) == 12


def test_the_stills_are_told_not_to_letter_the_frame() -> None:
    pack = load_content_pack(PACK)
    assert "no text" in pack.image.style_suffix
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_saju_v1_pack.py -v`
Expected: `7 passed` after Steps 1–4; before them, the first failure names the missing pack file or the undeclared glyph.

- [ ] **Step 7: Run the suite, lint, type-check, commit**

```bash
uv run pytest -q && uv run mypy apps packages && uv run ruff check
git add designs/glyphs/noto_serif_kr.json \
  packages/core/src/video_editor_core/fonts/NotoSerifKR.ttf \
  designs/saju_series.json shorts/saju-v1-your-animal/short.yaml \
  tests/test_saju_v1_pack.py
git commit -m "feat(short): author the first saju pack and the glyphs its chips need

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 13: The paid build, behind the cost gate

**Files:** none — this is the first real run of the machinery.

**Interfaces:**
- Consumes: `ve short` (Task 10), the pack (Task 12), the rebuilt font (Task 12).
- Produces: `final.mp4`, `qa.json`, and the measured numbers the ledger records.

- [ ] **Step 1: Run the build**

```bash
uv run ve short shorts/saju-v1-your-animal/short.yaml
```

Expected: the estimate table lists 5 image requests and 7 TTS requests, a total of about **$0.311**, and the prompt `Proceed?`. Type `y` only after the arithmetic below is confirmed against `models.yaml`.

The arithmetic, at the rates in `models.yaml` on 2026-10-26:
- **Images** — 5 distinct stills × `gpt-image-2.5-fixed` at the pack's `resolution: 2K` = 5 × `$0.03` = **$0.15**. The `price_by.resolution` tier is what makes this 2K; the base `cost_per_image: 0.0085` is the 1K rate (#269).
- **Voice** — 7 spoken lines, Sarvam `bulbul:v4-flash` at `$0.023` per 1 000-character block, and Sarvam bills `ceil(chars/1000)` **per call** (#263). Every line is under 1 000 characters, so each is one block: 7 × `$0.023` = **$0.161**.
- **Total ≈ $0.311.** An earlier draft of this plan said "$0.20 ($0.17–0.25)", which is the same arithmetic with the **1K** image rate (5 × $0.0085 + $0.161 = $0.204) — stale, because the pack asks for 2K.

If the total is materially above **$0.35**, stop and reconcile before approving: `uv run python -c` print of `short_job_requests(...)`'s providers/models against `models.yaml`. A doubling usually means a request per line where the pack intended one per scene, or an image regenerated for a `reuse:` alias.

- [ ] **Step 2: Watch the stages land**

Read the printed `Final:` and `QA:` lines. Then:

```bash
ls "$(dirname <final.mp4>)"        # final.mp4 + the job's build dir
cat <build>/qa.json | python -m json.tool | head -60
```

Expected: every check in `qa.json` has `"ok": true`. If one does not:

| Failing check | Cause | Fix |
|---|---|---|
| `cues` | a pack `cue:` names a phrase the voice did not say | edit the cue, not the gate |
| `captions` | coverage under 98% | inspect `plan/alignment.json`; a word with a zero-length window means the transcript lost it |
| `scene_starts` | a scene's first word sits > 0.35 s after its window starts | the transcript is misaligned; re-read `_transcribe`'s `vad_filter` |
| `tail` | the mp4 ends before the end card's tail | `end_card.tail_seconds` against `Composition.duration` |
| `duration` | the file and the plan disagree by > 10% | a render that dropped frames |
| `stills` | 5 requests but fewer files | the cache returned a stale id; clear `.cache/assets` for that key |
| `loudness` / `true_peak` | normalisation skipped | check `normalize_loudness` ran on the muxed file |

- [ ] **Step 3: Inspect the artifact by eye**

```bash
ffprobe -v error -show_entries format=duration:stream=width,height,r_frame_rate -of default=nw=1 <final.mp4>
ffmpeg -y -v error -ss 2 -i <final.mp4> -frames:v 1 /tmp/short-frame.png
```

Read the frame. Three things the gates cannot see: the chip glyph renders as a glyph rather than tofu; the pill text does not collide with the caption box; the end card's URL is legible at phone size. Record what you see.

- [ ] **Step 4: Record the run**

Append to `.superpowers/sdd/progress.md`:

```
| paid build 1 | saju-v1-your-animal: <duration>s, QA <passed>/<total> gates, $<spent> of $<estimate> approved, <stills> stills from <provider>, voice <voice_id>. Defects: <none | bug #NNN>. |
```

Then commit the record **only** (media stays out of git):

```bash
git add .superpowers/sdd/progress.md
git commit -m "docs(sdd): record the first paid content-pack build

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 14: Documentation and the slice-2 ledger

**Files:**
- Modify: `docs/ARCHITECTURE.md` (a new subsection under `## Composition pipeline`)
- Modify: `.superpowers/sdd/progress.md`
- Modify: `README.md` only if its command list enumerates `ve` subcommands

**Interfaces:** none — this task changes no code.

- [ ] **Step 1: Document the pack path**

Under `docs/ARCHITECTURE.md`'s `## Composition pipeline` heading, add:

```markdown
### Content packs (`ve short`)

A short is authored as one `shorts/<job-id>/short.yaml` and built by
`ve short <pack>`. The pack carries words, on-screen text, image prompts and
the voice choice — never a colour, font or rect; styling comes from the series
design in `designs/<name>.json` through `video_editor_core.layer_presets`.

```
short.yaml ─┐
designs/*.json ─┼→ preflight → cost gate → stills ─┐
                │                                  ├→ narration.wav → whisper
                └→ per-line TTS ───────────────────┘         │
                                                              ↓
                        Alignment (contiguous scene windows, word times)
                                                              ↓
        pack_to_composition + caption_layers → Composition ─→ one compile
                                                              ↓
                        render → mux → loudnorm → five composition
                        gates + measured-file QA → final.mp4 + qa.json
```

Two rules make the path safe to spend money on. The estimate is built from
`short_job_requests`, the same list the run executes, so the approved number
and the spend cannot drift; and every QA value is measured from the shipped
file, with an unmeasurable gate failing rather than passing.

The two model fields worth knowing: `voice.voice_id` is provider-specific
(Sarvam takes `sarvam:<speaker>`), and `end_card.tail_seconds` (0.8–2.0) is the
window the tail gate will accept, so the pack cannot choose a value the build
must then reject.
```

- [ ] **Step 2: Close the ledger**

Append a section to `.superpowers/sdd/progress.md` listing Tasks 1–14 with their commit ranges, and a final line with the verification numbers from Step 3. Keep the existing slice-1 section untouched.

- [ ] **Step 3: Final verification**

```bash
uv run pytest -q
uv run mypy apps packages
uv run ruff check
git status --short
```

Expected: the suite green with no new skips beyond the `hyperframes`-absent one, `Success: no issues found`, `All checks passed!`, and a clean tree apart from `videos/` (untracked media).

- [ ] **Step 4: Commit and hand off**

```bash
git add docs/ARCHITECTURE.md .superpowers/sdd/progress.md
git commit -m "docs: record the content-pack path and close the slice-2 ledger

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

Then use `superpowers:finishing-a-development-branch` to present merge/PR/keep/discard.

---

## Self-review record

Run after writing: spec coverage, placeholder scan, type consistency.

| Check | Result |
|---|---|
| Spec §12 requirements each map to a task | every bullet has a home: pack model → 1, design `caption_line` → 2, presets → 3, adapter → 4, captions → 5, cues/end card → 6, gate → 7, stages → 8-9, orchestration + CLI → 10, offline proof → 11, first pack → 12, paid run → 13, docs → 14 |
| Placeholders ("TBD", "add validation", "similar to Task N") | none; every code step carries the code |
| Types consistent across tasks | `Alignment`/`TimedScene`/`TimedWord` (1) feed `align_scenes` (9) and `pack_to_composition` (4); `StillAssets.paths` is the `stills=` mapping (8→4); `caption_layers` returns the `captions=` sequence (5→4); `ShortResult` fields are asserted in 11 |
| A task may not be executed out of order | Tasks 1-2 gate everything; 11 needs 10; 13 needs 12 |

### Defects the review caught, and where the fix landed

Each of these was checked against the code rather than assumed, and each would have stopped an implementer mid-task:

| Defect | Fix |
|---|---|
| `gather_composition_qa` derived cues from a plan the pack path does not have, and an empty `missing_cues` tuple would have *passed* | `cues_measured` field + `resolved_cues` measured independently in `run_short_job` (Task 10) |
| `QaInputs` without `target_seconds` falls back to the 30-90 s format bound, so a 4.8 s offline build fails the duration gate | `target_seconds=composition.duration` (Task 10) |
| `stub_tts` / `stub_image` have no price, so `check_approved_spend` refuses every offline build | price them at 0.0 in `models.yaml` and regenerate (Task 11) |
| The bundled Noto Serif KR subset declares 23 characters and neither 子 nor 辛 is among them, so the real pack's chips fail the glyph gate | extend the manifest and rebuild the subset (Task 12) |
| The bundled fonts live in `packages/core/src/video_editor_core/fonts/`, not `designs/fonts/` | Task 12's paths corrected |
| `canvas_for_aspect` maps any unknown aspect to 16:9, so a pack typo ships a landscape video that passes every gate | `ContentPack.aspect` is a closed `Literal` with a test (Task 1) |
| The conftest mask hides the `hyperframes` binary from `shutil.which`, so no test can render | `hyperframes_binary` fixture + an explicit `cli_path` on `run_short_job` (Tasks 10-11) |
| `_loudness` / `_max_silent_gap` are private, but the short path must measure the shipped file | renamed public in `explainer_pipeline` (Task 10) |
| A text layer cannot draw a border or a radius in this emitter | the design drops `pill.border`, `pill.radius` and `chip.bar_color` rather than shipping dead styling (Task 2) |

---

## Execution status — 2026-10-26

| Task | State |
|---|---|
| 1–11 | **Complete and reviewed.** Commits `a48aefd`…`9d29e70`; per-task detail in `.superpowers/sdd/progress.md`. |
| 12 | **Complete and reviewed.** `9d29e70..872db46`; spec ✅ + quality Approved first pass. Two Minors carried (the chip test's name overpromises the cmap; the cue test uses substring rather than `find_cue`). Detail in `.superpowers/sdd/progress.md`. |
| 13 | **Complete.** Run 2026-10-26 at the approved **$0.3110** (5 images $0.1500 + 7 voice lines $0.1610; 12 requests). QA **15/15** on a 42.500 s `final.mp4`; build 2 after the #272 fix was 12/12 cache-served ($0.00 incremental). The by-eye check passed (chips 子/辛, pill/caption clearance, end-card URL). |
| 14 | **Complete.** `docs/ARCHITECTURE.md` gained the content-pack subsection, `README.md` the `ve short` command, and `.superpowers/sdd/progress.md` the slice-2 ledger close. |

Total slice-2 spend: **$0.3110**. The paid build is recorded in `.superpowers/sdd/progress.md`; bug #272 is fixed with `tests/test_loudness_normalization.py`.
