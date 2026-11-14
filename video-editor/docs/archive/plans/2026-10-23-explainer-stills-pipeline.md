# Explainer Stills Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a `script.md` into a finished vertical explainer — stills, typographic scenes, one narration track, karaoke captions, ducked music, loudness-normalised — with an estimate gate before any spend and a QA report that passes or names what failed.

**Architecture:** A new `explainer_stills` pipeline kind parses the script into a `ScenePlan`, clusters still scenes by subject so a reprise renders once (#271), and prices that plan at the estimate gate. One TTS request narrates the whole script; Whisper word timings are aligned onto the display text and turned into scene durations. The existing HyperFrames compiler renders the plan, with a new `typographic` scene kind that draws layouts from a design-token JSON instead of an image.

**Tech Stack:** Python 3.12, Pydantic v2, PyYAML, faster-whisper (via `video_editor_transcribe`), HyperFrames HTML composition, FFmpeg (`music.mix`, `render_graph`), `captions` skill, pytest.

## Global Constraints

- Only environment-variable **names** may appear in code, docs, or commit messages. Never print, echo, or commit a value from `.env`.
- `packages/core` must not import `apps/engine` or `apps/cli`. New core modules import only `video_editor_core`.
- `compile_scene_plan_project(plan, project_dir, *, fps=30)` keeps its signature; callers are unchanged.
- Every typographic scene carries three motions: entry (0.35 s / `motion.tempo`), per-item reveals (word-cued with an even-stagger fallback), and a slow drift (`motion.drift`). No static held shot.
- Fonts are limited to `video_editor_core.fonts.BUNDLED_FONTS` (`Inter`, `Montserrat`). An unbundled family is a validation error, never a silent substitution.
- Narration is non-empty on every scene. Scene durations are never authored; they are derived from speech.
- `emit_image=False` and `emit_tts=False` are the only ways a scene skips a paid request. The estimate gate and the pipeline must build requests from the same function (`requests_from_plan`) so they cannot disagree.
- Commits: one per task, message style `feat(explainer): ...` / `test(explainer): ...`.

### Deviations from the spec (recorded deliberately)

| spec says | plan does | why |
|---|---|---|
| `designs.py` in `apps/engine/.../templates/` | `packages/core/src/video_editor_core/designs.py` | the compiler lives in core and core may not import engine |
| `ProjectConfig.designs_dir` | `video_editor_core.designs.DESIGNS_DIR` | core resolves its own repo root; no config change, no new coupling |
| `ve run --template explainer_stills` / `ve run --from-topic` | `ve explain script.md` / `ve explain --from-topic "..."` | `run`'s `source` is a required Typer `Argument`, so `--from-topic` cannot replace it cleanly; a dedicated command also keeps the estimate gate honest |
| display face "Playfair Display" | `Montserrat` (bundled) | v1 ships only bundled fonts; adding a serif is one OFL file in `fonts.py` later |
| transcription via `video_editor_tools.transcription.transcribe()` | direct `video_editor_transcribe.whisper_local.transcribe_audio`, with `video-editor-transcribe` added to the CLI deps | the wrapper does not forward `initial_prompt`/`vad_filter`/`word_timestamps`, which §9 requires (#268) |
| final plan carries `plan["design"]` | also `plan["narration_path"]` | one narration track means no per-scene TTS assets, so the compiler needs a root-level narration `<audio>` |
| interior tail "last word + 0.25 s" | the measured pause, clamped to at least 0.25 s and never past the next scene's 0.12 s lead-in | scenes share one continuous audio track; boundaries must follow the voice |

---

## File Structure

| file | responsibility |
|---|---|
| `apps/cli/src/video_editor_cli/explainer_script.py` | `script.md` → `ExplainerScript` |
| `apps/cli/src/video_editor_cli/still_reuse.py` | subject clustering → `ReusePlan`, `props.image_scene` |
| `packages/core/src/video_editor_core/designs.py` | design JSON → validated `DesignTokens` |
| `designs/cream_editorial.json`, `designs/ink_noir.json` | design data (repo root, next to `presets/`) |
| `packages/core/src/video_editor_core/typographic_scenes.py` | five layouts → HTML + keyframes |
| `apps/cli/src/video_editor_cli/explainer_timing.py` | narration text ↔ Whisper words → `SceneTiming` |
| `apps/cli/src/video_editor_cli/explainer_plan.py` | `ExplainerScript` → plan dict + asset requests |
| `apps/cli/src/video_editor_cli/explainer_qa.py` | measured values → pass/fail checks |
| `apps/cli/src/video_editor_cli/explainer_pipeline.py` | the run sequence (assets → timing → render → audio → QA) |
| `apps/cli/src/video_editor_cli/explainer_topic.py` | `--from-topic` → `script.generated.md` (the only LLM call) |

Modified: `scene_plan.py` (`typographic`, `design`), `scene_plan_composition.py` (branch, narration audio, fonts), `scene_assets.py` (`emit_tts`), `templates/base.py` + `yaml_templates.py` (`explainer_stills`), `main.py` (`ve explain`, estimate gate), `handlers.py` (worker branch), `apps/cli/pyproject.toml` (`video-editor-transcribe`), `templates/explainer_motion.yaml` (invalid hints), `docs/templates.md`, `docs/CLI_USAGE.md`.

---

### Task 1: Script parser

**Files:**
- Create: `apps/cli/src/video_editor_cli/explainer_script.py`
- Test: `tests/test_explainer_script.py`

**Interfaces:**
- Produces: `ScriptError`, `ScriptScene`, `ExplainerScript`, `parse_explainer_script(text: str, *, source: str = "<script>") -> ExplainerScript`, `load_explainer_script(path: Path) -> ExplainerScript`.
- `ScriptScene` fields: `id, title, visual, narration, layout, image, subject, props`.
- `ExplainerScript` fields: `title, scenes, aspect_ratio, voice, voice_model, voice_language, caption_preset, design, reuse_threshold, target_seconds, music_mood`; property `narration`.
- Consumed by Tasks 2, 7, 9.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_explainer_script.py
from __future__ import annotations

import pytest

from video_editor_cli.explainer_script import (
    ExplainerScript,
    ScriptError,
    load_explainer_script,
    parse_explainer_script,
)

SCRIPT = """\
---
title: Why Google Paid $32 Billion For Wiz
aspect_ratio: 9:16
voice: sarang_en_narration
design: cream_editorial
reuse_threshold: 0.35
target_seconds: 50
---

## Scene 1 — The number
visual: typographic
layout: big_stat
headline: $32B
sub: Google's largest acquisition ever
narration: |
  Thirty-two billion dollars. That's what Google just paid.

## Scene 2 — The racks
visual: still
image: |
  two black server racks in a warm cream data centre
narration: |
  Wiz is five years old.
"""


def test_parses_front_matter_and_scenes():
    script = parse_explainer_script(SCRIPT)
    assert isinstance(script, ExplainerScript)
    assert script.title == "Why Google Paid $32 Billion For Wiz"
    assert script.aspect_ratio == "9:16"
    assert script.voice == "sarang_en_narration"
    assert script.reuse_threshold == 0.35
    assert script.target_seconds == 50.0
    assert [scene.id for scene in script.scenes] == ["scene_1", "scene_2"]
    first, second = script.scenes
    assert first.visual == "typographic"
    assert first.layout == "big_stat"
    assert first.props == {
        "headline": "$32B",
        "sub": "Google's largest acquisition ever",
    }
    assert second.visual == "still"
    assert second.image.startswith("two black server racks")
    assert second.subject == ""
    assert script.narration.startswith("Thirty-two billion dollars.")


def test_defaults_apply_when_front_matter_is_thin():
    script = parse_explainer_script(
        "---\ntitle: T\n---\n\n## One\nvisual: typographic\nlayout: definition\n"
        "term: Cloud\nnarration: A place for data.\n"
    )
    assert script.aspect_ratio == "9:16"
    assert script.caption_preset == "karaoke"
    assert script.design == "cream_editorial"
    assert script.reuse_threshold == 0.30
    assert script.target_seconds is None
    assert script.scenes[0].props == {"term": "Cloud"}


def test_subject_is_recorded_and_not_leaked_into_props():
    script = parse_explainer_script(
        SCRIPT.replace("visual: still", "visual: still\nsubject: racks")
    )
    assert script.scenes[1].subject == "racks"
    assert "subject" not in script.scenes[1].props


@pytest.mark.parametrize(
    ("block", "message"),
    [
        ("visual: photo\nimage: x\nnarration: y\n", "visual"),
        ("visual: typographic\nheadline: x\nnarration: y\n", "layout"),
        ("visual: typographic\nlayout: pie_chart\nnarration: y\n", "pie_chart"),
        ("visual: still\nnarration: y\n", "image"),
        ("visual: still\nimage: x\nnarration: '   '\n", "narration"),
    ],
)
def test_invalid_scene_is_rejected_with_the_scene_named(block: str, message: str):
    text = f"---\ntitle: T\n---\n\n## Bad scene\n{block}"
    with pytest.raises(ScriptError) as excinfo:
        parse_explainer_script(text)
    assert "Bad scene" in str(excinfo.value)
    assert message in str(excinfo.value)


def test_missing_front_matter_is_rejected():
    with pytest.raises(ScriptError, match="front matter"):
        parse_explainer_script("## One\nvisual: still\nimage: x\nnarration: y\n")


def test_missing_title_is_rejected():
    with pytest.raises(ScriptError, match="title"):
        parse_explainer_script(
            "---\naspect_ratio: 9:16\n---\n\n## One\nvisual: still\nimage: x\nnarration: y\n"
        )


def test_no_scene_headings_is_rejected():
    with pytest.raises(ScriptError, match="scene heading"):
        parse_explainer_script("---\ntitle: T\n---\n\nnothing here\n")


def test_invalid_yaml_in_a_scene_names_the_scene():
    with pytest.raises(ScriptError) as excinfo:
        parse_explainer_script(
            "---\ntitle: T\n---\n\n## Broken\nvisual: still\nimage: [unclosed\nnarration: y\n"
        )
    assert "Broken" in str(excinfo.value)


def test_threshold_out_of_range_is_rejected():
    with pytest.raises(ScriptError, match="reuse_threshold"):
        parse_explainer_script(SCRIPT.replace("reuse_threshold: 0.35", "reuse_threshold: 1.5"))


def test_load_reads_the_file(tmp_path):
    path = tmp_path / "script.md"
    path.write_text(SCRIPT, encoding="utf-8")
    assert load_explainer_script(path).title.startswith("Why Google")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_explainer_script.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_editor_cli.explainer_script'`

- [ ] **Step 3: Write the implementation**

```python
# apps/cli/src/video_editor_cli/explainer_script.py
"""Parse an explainer ``script.md`` into a structured spec.

The file is YAML front matter between ``---`` fences, then one ``## `` heading
per scene whose body is itself YAML:

    ---
    title: Why Google Paid $32 Billion For Wiz
    ---

    ## Scene 1 — The number
    visual: typographic
    layout: big_stat
    headline: $32B
    narration: |
      Thirty-two billion dollars.

Durations are never authored here: the pipeline derives them from the spoken
narration (``explainer_timing``), because a written duration always drifts
from the voice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

VALID_VISUALS = frozenset({"typographic", "still"})
VALID_LAYOUTS = frozenset(
    {"big_stat", "bar_compare", "definition", "sequence", "array"}
)

DEFAULT_ASPECT_RATIO = "9:16"
DEFAULT_CAPTION_PRESET = "karaoke"
DEFAULT_DESIGN = "cream_editorial"
DEFAULT_REUSE_THRESHOLD = 0.30

#: Keys with a meaning to the pipeline. Everything else a scene declares is a
#: layout prop and is copied verbatim into ``props``.
_RESERVED_KEYS = frozenset({"visual", "narration", "image", "layout", "subject"})

_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(?P<body>.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)
_SCENE_HEADING_RE = re.compile(r"^##[ \t]+(?P<title>[^\n]+)$", re.MULTILINE)


class ScriptError(ValueError):
    """A script that cannot be turned into a plan."""


@dataclass(frozen=True)
class ScriptScene:
    """One ``## `` block of the script."""

    id: str
    title: str
    visual: str
    narration: str
    layout: str = ""
    image: str = ""
    subject: str = ""
    props: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExplainerScript:
    """The parsed script: front matter plus scenes."""

    title: str
    scenes: list[ScriptScene]
    aspect_ratio: str = DEFAULT_ASPECT_RATIO
    voice: str = ""
    voice_model: str = ""
    voice_language: str = ""
    caption_preset: str = DEFAULT_CAPTION_PRESET
    design: str = DEFAULT_DESIGN
    reuse_threshold: float = DEFAULT_REUSE_THRESHOLD
    target_seconds: float | None = None
    music_mood: str = ""

    @property
    def narration(self) -> str:
        """The whole script as one spoken string, scene order preserved."""
        return "\n\n".join(scene.narration for scene in self.scenes)


def parse_explainer_script(
    text: str, *, source: str = "<script>"
) -> ExplainerScript:
    """Parse ``text`` into an :class:`ExplainerScript`, or raise ``ScriptError``."""
    meta, body = _split_front_matter(text, source)
    scenes = _parse_scenes(body, source)
    title = str(meta.get("title") or "").strip()
    if not title:
        raise ScriptError(f"{source}: front matter needs a non-empty `title`")

    target_seconds: float | None = None
    raw_target = meta.get("target_seconds")
    if raw_target not in (None, ""):
        try:
            target_seconds = float(raw_target)
        except (TypeError, ValueError) as exc:
            raise ScriptError(f"{source}: `target_seconds` must be a number") from exc
        if target_seconds <= 0:
            raise ScriptError(f"{source}: `target_seconds` must be > 0")

    raw_threshold = meta.get("reuse_threshold", DEFAULT_REUSE_THRESHOLD)
    try:
        threshold = float(raw_threshold)
    except (TypeError, ValueError) as exc:
        raise ScriptError(f"{source}: `reuse_threshold` must be a number") from exc
    if not 0.0 < threshold <= 1.0:
        raise ScriptError(
            f"{source}: `reuse_threshold` must be in (0, 1], got {threshold}"
        )

    return ExplainerScript(
        title=title,
        scenes=scenes,
        aspect_ratio=str(meta.get("aspect_ratio") or DEFAULT_ASPECT_RATIO).strip(),
        voice=str(meta.get("voice") or "").strip(),
        voice_model=str(meta.get("voice_model") or "").strip(),
        voice_language=str(meta.get("voice_language") or "").strip(),
        caption_preset=str(
            meta.get("caption_preset") or DEFAULT_CAPTION_PRESET
        ).strip(),
        design=str(meta.get("design") or DEFAULT_DESIGN).strip(),
        reuse_threshold=threshold,
        target_seconds=target_seconds,
        music_mood=str(meta.get("music_mood") or "").strip(),
    )


def load_explainer_script(path: Path | str) -> ExplainerScript:
    """Read and parse ``path``."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScriptError(f"{path}: cannot read script: {exc}") from exc
    return parse_explainer_script(text, source=str(path))


def _split_front_matter(text: str, source: str) -> tuple[dict[str, Any], str]:
    """Return (front matter mapping, rest of the file)."""
    match = _FRONT_MATTER_RE.match(text)
    if match is None:
        raise ScriptError(
            f"{source}: missing front matter; the file must start with a `---` block"
        )
    try:
        meta = yaml.safe_load(match.group("body"))
    except yaml.YAMLError as exc:
        raise ScriptError(f"{source}: front matter is not valid YAML: {exc}") from exc
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise ScriptError(f"{source}: front matter must be a mapping")
    return dict(meta), text[match.end() :]


def _parse_scenes(body: str, source: str) -> list[ScriptScene]:
    """Split the body on ``## `` headings and parse each block as YAML."""
    headings = list(_SCENE_HEADING_RE.finditer(body))
    if not headings:
        raise ScriptError(f"{source}: no `## ` scene heading found")
    scenes: list[ScriptScene] = []
    for position, heading in enumerate(headings):
        start = heading.end()
        end = (
            headings[position + 1].start()
            if position + 1 < len(headings)
            else len(body)
        )
        title = heading.group("title").strip()
        try:
            data = yaml.safe_load(body[start:end])
        except yaml.YAMLError as exc:
            raise ScriptError(
                f"{source}: scene {title!r} has invalid YAML: {exc}"
            ) from exc
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ScriptError(f"{source}: scene {title!r} must be a YAML mapping")
        scenes.append(_scene_from(position + 1, title, dict(data), source))
    return scenes


def _scene_from(
    number: int, title: str, data: dict[str, Any], source: str
) -> ScriptScene:
    """Validate one scene mapping; the heading is always named in errors."""
    label = f"{source}: scene {title!r}"
    visual = str(data.get("visual") or "").strip()
    if visual not in VALID_VISUALS:
        raise ScriptError(
            f"{label}: `visual` must be one of {sorted(VALID_VISUALS)}, got {visual!r}"
        )
    narration = str(data.get("narration") or "").strip()
    if not narration:
        raise ScriptError(f"{label}: `narration` must be non-empty")

    layout = str(data.get("layout") or "").strip()
    image = str(data.get("image") or "").strip()
    if visual == "typographic":
        if not layout:
            raise ScriptError(f"{label}: a typographic scene needs `layout`")
        if layout not in VALID_LAYOUTS:
            raise ScriptError(
                f"{label}: unknown layout {layout!r}; choose one of "
                f"{sorted(VALID_LAYOUTS)}"
            )
    if visual == "still" and not image:
        raise ScriptError(f"{label}: a still scene needs an `image` prompt")

    props = {key: value for key, value in data.items() if key not in _RESERVED_KEYS}
    return ScriptScene(
        id=f"scene_{number}",
        title=title,
        visual=visual,
        narration=narration,
        layout=layout,
        image=image,
        subject=str(data.get("subject") or "").strip(),
        props=props,
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_explainer_script.py -v`
Expected: PASS (all tests pass, no warnings)

- [ ] **Step 5: Commit**

```bash
git add apps/cli/src/video_editor_cli/explainer_script.py tests/test_explainer_script.py
git commit -m "feat(explainer): parse script.md into scenes and front matter"
```

---

### Task 2: Automatic still reuse

**Files:**
- Create: `apps/cli/src/video_editor_cli/still_reuse.py`
- Test: `tests/test_still_reuse.py`

**Interfaces:**
- Consumes: `ScriptScene` from Task 1 (`.id`, `.visual`, `.image`, `.narration`, `.subject`).
- Produces: `subject_words(scene) -> set[str]`, `jaccard(a, b) -> float`, `ReuseCluster`, `ReusePlan` (with `.still_scenes`, `.renders`, `.saved`), `plan_still_reuse(scenes, *, threshold=0.30, enabled=True) -> ReusePlan`, `apply_reuse(plan: dict, reuse: ReusePlan) -> None`, `format_reuse_report(reuse, *, credits_per_render=5, usd_per_credit=0.005) -> str`.
- Consumed by Tasks 7, 9.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_still_reuse.py
from __future__ import annotations

from video_editor_cli.explainer_script import ScriptScene
from video_editor_cli.still_reuse import (
    apply_reuse,
    format_reuse_report,
    jaccard,
    plan_still_reuse,
    subject_words,
)


def still(scene_id: str, image: str, narration: str = "", subject: str = "") -> ScriptScene:
    return ScriptScene(
        id=scene_id,
        title=scene_id,
        visual="still",
        narration=narration or f"Narration for {scene_id}.",
        image=image,
        subject=subject,
    )


def typographic(scene_id: str) -> ScriptScene:
    return ScriptScene(
        id=scene_id,
        title=scene_id,
        visual="typographic",
        narration="A number.",
        layout="big_stat",
        props={"headline": "$32B"},
    )


def test_jaccard_counts_overlap_over_union():
    assert jaccard({"a", "b"}, {"b", "c"}) == 1 / 3
    assert jaccard(set(), {"b"}) == 0.0


def test_words_come_from_prompt_and_narration_with_stopwords_and_plurals_folded():
    scene = still(
        "scene_1",
        "two black server racks in a data centre",
        "The racks hold the cloud.",
    )
    words = subject_words(scene)
    assert "racks" not in words and "rack" in words
    assert "server" in words
    assert "the" not in words


def test_reprised_subject_joins_the_first_scenes_cluster():
    scenes = [
        still("scene_1", "black server racks in a cream data centre", "Server racks full of rented machines."),
        typographic("scene_2"),
        still("scene_3", "cream data centre server racks, red sticker", "The server racks again, rented."),
    ]
    reuse = plan_still_reuse(scenes, threshold=0.20)
    assert [cluster.owner for cluster in reuse.clusters] == ["scene_1"]
    assert reuse.clusters[0].scenes == ["scene_1", "scene_3"]
    assert reuse.still_scenes == 2
    assert reuse.renders == 1
    assert reuse.saved == 1


def test_different_subjects_never_merge():
    scenes = [
        still("scene_1", "cream data centre server racks", "Rented racks."),
        still("scene_2", "cream data centre server racks", "Rented racks.", subject="racks"),
    ]
    reuse = plan_still_reuse(scenes, threshold=0.20)
    assert reuse.clusters == []
    assert reuse.unclustered == ["scene_1", "scene_2"]


def test_shared_subject_always_merges_even_with_unrelated_prompts():
    scenes = [
        still("scene_1", "a gem on velvet", "A gem.", subject="gem"),
        still("scene_2", "a red gate at dusk", "The gate.", subject="gem"),
    ]
    reuse = plan_still_reuse(scenes, threshold=0.99)
    assert reuse.clusters[0].scenes == ["scene_1", "scene_2"]


def test_disabled_reuse_keeps_every_render():
    scenes = [
        still("scene_1", "black server racks", "Racks."),
        still("scene_2", "black server racks", "Racks."),
    ]
    reuse = plan_still_reuse(scenes, threshold=0.10, enabled=False)
    assert reuse.clusters == []
    assert reuse.renders == 2


def test_reuse_is_deterministic():
    scenes = [
        still("scene_1", "black server racks", "Racks."),
        still("scene_2", "black server racks", "Racks."),
    ]
    first = plan_still_reuse(scenes, threshold=0.20)
    second = plan_still_reuse(scenes, threshold=0.20)
    assert [(c.owner, c.scenes) for c in first.clusters] == [
        (c.owner, c.scenes) for c in second.clusters
    ]


def test_apply_reuse_writes_image_scene_for_members_only():
    scenes = [
        still("scene_1", "black server racks", "Racks."),
        still("scene_2", "black server racks", "Racks."),
    ]
    plan = {
        "scenes": [
            {"id": "scene_1", "props": {}},
            {"id": "scene_2", "props": {}},
        ]
    }
    apply_reuse(plan, plan_still_reuse(scenes, threshold=0.20))
    assert plan["scenes"][0]["props"] == {}
    assert plan["scenes"][1]["props"] == {"image_scene": "scene_1"}


def test_report_names_clusters_and_counts():
    scenes = [
        still("scene_1", "black server racks", "Racks."),
        still("scene_2", "black server racks", "Racks."),
        still("scene_3", "a gem on velvet", "A gem."),
    ]
    report = format_reuse_report(plan_still_reuse(scenes, threshold=0.20))
    assert "3 still scenes -> 2 renders (1 saved, 5 credits / $0.025)" in report
    assert "scene_1" in report and "scene_2" in report
    assert "unclustered" in report and "scene_3" in report
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_still_reuse.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_editor_cli.still_reuse'`

- [ ] **Step 3: Write the implementation**

```python
# apps/cli/src/video_editor_cli/still_reuse.py
"""Decide which stills can show the same rendered image.

The plan-level reuse mechanism already exists (#271): a scene whose
``props.image_scene`` names another scene shows that scene's still, the cost
gate skips its IMAGE request, and the renderer shares the file. This module
supplies the *decision*.

Two still scenes join a cluster when the Jaccard overlap of their word sets is
at least the script's ``reuse_threshold``. The word set is built from the
``image`` prompt **and** the narration, because a reprise usually restates its
subject in words rather than in the prompt. A ``subject:`` tag overrides the
score: tagged scenes merge with the same tag, and never with a different or
untagged scene, so "two racks that look alike but mean different things" is
one line in the script away from splitting.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

DEFAULT_THRESHOLD = 0.30
DEFAULT_CREDITS_PER_RENDER = 5
DEFAULT_USD_PER_CREDIT = 0.005

_WORD_RE = re.compile(r"[a-z0-9$%][a-z0-9$%'_-]*")

_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "for",
        "from", "has", "have", "her", "his", "in", "into", "is", "it", "its",
        "itself", "of", "on", "one", "or", "our", "out", "over", "so", "that",
        "the", "their", "them", "then", "there", "these", "they", "this",
        "those", "to", "too", "under", "up", "was", "we", "were", "what",
        "when", "where", "which", "while", "who", "with", "you", "your",
    }
)


def _fold_plural(token: str) -> str:
    """Fold a trailing plural so "racks"/"rack" compare equal."""
    if token.endswith("ies") and len(token) > 4:
        return f"{token[:-3]}y"
    if token.endswith("es") and len(token) > 4:
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        return token[:-1]
    return token


def subject_words(scene: Any) -> set[str]:
    """The comparable word set for a still scene: prompt plus narration."""
    text = f"{getattr(scene, 'image', '')} {getattr(scene, 'narration', '')}".lower()
    words: set[str] = set()
    for match in _WORD_RE.finditer(text):
        token = match.group(0).strip("'_-")
        if not token or token in _STOPWORDS:
            continue
        if len(token) == 1 and not token.isdigit():
            continue
        words.add(_fold_plural(token))
    return words


def jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    """Overlap over union; an empty side never matches."""
    left_set, right_set = set(left), set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


@dataclass
class ReuseCluster:
    """Still scenes that render once; the owner's image is shown by the rest."""

    owner: str
    scenes: list[str] = field(default_factory=list)
    words: set[str] = field(default_factory=set)
    subject: str = ""
    overlap: float = 0.0


@dataclass
class ReusePlan:
    """The clustering result, in script order."""

    clusters: list[ReuseCluster] = field(default_factory=list)
    unclustered: list[str] = field(default_factory=list)

    @property
    def still_scenes(self) -> int:
        return sum(len(cluster.scenes) for cluster in self.clusters) + len(
            self.unclustered
        )

    @property
    def renders(self) -> int:
        return len(self.clusters) + len(self.unclustered)

    @property
    def saved(self) -> int:
        return self.still_scenes - self.renders


def plan_still_reuse(
    scenes: Sequence[Any],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    enabled: bool = True,
) -> ReusePlan:
    """Cluster the still scenes of ``scenes``; deterministic for a given input."""
    groups: list[ReuseCluster] = []
    for scene in scenes:
        if getattr(scene, "visual", "") != "still":
            continue
        if not enabled:
            groups.append(
                ReuseCluster(owner=scene.id, scenes=[scene.id], words=subject_words(scene))
            )
            continue
        words = subject_words(scene)
        subject = str(getattr(scene, "subject", "") or "").strip()
        chosen: ReuseCluster | None = None
        for group in groups:
            if subject or group.subject:
                join = bool(subject) and subject == group.subject
                overlap = jaccard(words, group.words)
            else:
                overlap = jaccard(words, group.words)
                join = overlap >= threshold
            if join:
                group.scenes.append(scene.id)
                group.words |= words
                group.overlap = max(group.overlap, overlap)
                chosen = group
                break
        if chosen is None:
            groups.append(
                ReuseCluster(
                    owner=scene.id, scenes=[scene.id], words=words, subject=subject
                )
            )

    reuse = ReusePlan()
    for group in groups:
        if len(group.scenes) > 1:
            reuse.clusters.append(group)
        else:
            reuse.unclustered.extend(group.scenes)
    return reuse


def apply_reuse(plan: dict[str, Any], reuse: ReusePlan) -> None:
    """Write ``props.image_scene`` on every cluster member but the owner."""
    by_id = {
        str(scene.get("id")): scene
        for scene in plan.get("scenes", []) or []
        if isinstance(scene, dict)
    }
    for cluster in reuse.clusters:
        for scene_id in cluster.scenes[1:]:
            scene = by_id.get(scene_id)
            if scene is None:
                continue
            props = scene.setdefault("props", {})
            if isinstance(props, dict):
                props["image_scene"] = cluster.owner


def format_reuse_report(
    reuse: ReusePlan,
    *,
    credits_per_render: int = DEFAULT_CREDITS_PER_RENDER,
    usd_per_credit: float = DEFAULT_USD_PER_CREDIT,
) -> str:
    """Human-readable cluster table, printed before the purchase."""
    saved_usd = reuse.saved * credits_per_render * usd_per_credit
    lines = [
        f"still reuse: {reuse.still_scenes} still scenes -> {reuse.renders} renders "
        f"({reuse.saved} saved, {reuse.saved * credits_per_render} credits / "
        f"${saved_usd:.3f})"
    ]
    for cluster in reuse.clusters:
        numbers = ", ".join(scene.split("_")[-1] for scene in cluster.scenes)
        lines.append(
            f"  cluster {cluster.owner:<10} scenes {numbers:<14} "
            f"overlap {cluster.overlap:.2f}   -> render of {cluster.owner}"
        )
    if reuse.unclustered:
        numbers = ", ".join(scene.split("_")[-1] for scene in reuse.unclustered)
        lines.append(f"  unclustered          scenes {numbers:<14}   own render")
    return "\n".join(lines)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_still_reuse.py -v`
Expected: PASS (all tests pass)

- [ ] **Step 5: Commit**

```bash
git add apps/cli/src/video_editor_cli/still_reuse.py tests/test_still_reuse.py
git commit -m "feat(explainer): cluster still scenes by subject for plan-level reuse"
```

---

### Task 3: Design tokens

**Files:**
- Create: `packages/core/src/video_editor_core/designs.py`
- Create: `designs/cream_editorial.json`, `designs/ink_noir.json`
- Test: `tests/test_designs.py`

**Interfaces:**
- Produces: `DesignError`, `Palette`, `TypeScale`, `Motion`, `DesignTokens`, `DESIGNS_DIR`, `designs_dir() -> Path`, `load_design(name: str = "", directory: Path | str | None = None) -> DesignTokens`.
- `DesignTokens` fields: `.name`, `.palette.{bg,ink,accent,muted}`, `.type.{display,body,display_weight}`, `.theme`, `.motion.{tempo,entry,drift}`.
- Consumed by Tasks 4, 5.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_designs.py
from __future__ import annotations

import json

import pytest

from video_editor_core.designs import DesignError, designs_dir, load_design


def test_default_design_loads_with_bundled_fonts():
    design = load_design()
    assert design.name == "cream_editorial"
    assert design.palette.bg.startswith("#")
    assert design.type.display in {"Inter", "Montserrat"}
    assert 1.0 < design.motion.drift <= 1.30


def test_named_design_loads():
    assert load_design("ink_noir").theme == "dark"


def test_unknown_design_is_rejected():
    with pytest.raises(DesignError, match="unknown design"):
        load_design("no_such_design")


@pytest.mark.parametrize("name", ["../cream_editorial", "cream/editorial", "Cream Editor"])
def test_names_with_separators_or_spaces_are_rejected(name: str):
    with pytest.raises(DesignError, match="invalid design name"):
        load_design(name)


def test_unbundled_font_is_a_validation_error(tmp_path):
    path = tmp_path / "serif.json"
    path.write_text(
        json.dumps(
            {
                "name": "serif",
                "palette": {"bg": "#EFEAE3", "ink": "#141210", "accent": "#C8102E", "muted": "#8A8378"},
                "type": {"display": "Playfair Display", "body": "Inter"},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(DesignError, match="not bundled"):
        load_design("serif", directory=tmp_path)


def test_missing_palette_key_is_a_validation_error(tmp_path):
    path = tmp_path / "thin.json"
    path.write_text(
        json.dumps({"name": "thin", "palette": {"bg": "#fff", "ink": "#000"}}),
        encoding="utf-8",
    )
    with pytest.raises(DesignError, match="accent"):
        load_design("thin", directory=tmp_path)


def test_designs_dir_sits_at_the_repo_root():
    assert (designs_dir() / "cream_editorial.json").is_file()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_designs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_editor_core.designs'`

- [ ] **Step 3: Write the implementation**

```python
# packages/core/src/video_editor_core/designs.py
"""Design tokens for typographic scenes.

A design is one JSON file under ``designs/`` at the repo root::

    {"name": "cream_editorial",
     "palette": {"bg": "#EFEAE3", "ink": "#141210",
                 "accent": "#C8102E", "muted": "#8A8378"},
     "type": {"display": "Montserrat", "body": "Inter", "display_weight": 700},
     "theme": "light",
     "motion": {"tempo": 1.0, "entry": "rise", "drift": 1.06}}

The data lives next to ``presets/``; this loader resolves it from the package
location, exactly as ``video_editor_core.fonts`` resolves its font files, so
core never has to import the config package.

Fonts are restricted to :data:`video_editor_core.fonts.BUNDLED_FONTS`. A design
naming "Playfair Display" would render with whatever Chrome finds installed —
different on every machine — so an unbundled family is a validation error, not
a silent substitution.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, model_validator

from video_editor_core.fonts import BUNDLED_FONTS

DEFAULT_DESIGN = "cream_editorial"

#: ``packages/core/src/video_editor_core/designs.py`` -> repo root.
DESIGNS_DIR = Path(__file__).resolve().parents[4] / "designs"

_NAME_RE = re.compile(r"^[a-z0-9_]+$")


class DesignError(ValueError):
    """A design that cannot be loaded or is not a valid token set."""


class Palette(BaseModel):
    bg: str
    ink: str
    accent: str
    muted: str


class TypeScale(BaseModel):
    display: str = "Montserrat"
    body: str = "Inter"
    display_weight: int = Field(default=700, ge=100, le=900)

    @model_validator(mode="after")
    def _fonts_are_bundled(self) -> "TypeScale":
        for family in (self.display, self.body):
            if family not in BUNDLED_FONTS:
                raise ValueError(
                    f"font {family!r} is not bundled; choose from "
                    f"{sorted(BUNDLED_FONTS)}"
                )
        return self


class Motion(BaseModel):
    tempo: float = Field(default=1.0, gt=0.2, le=3.0)
    entry: str = "rise"
    drift: float = Field(default=1.06, gt=1.0, le=1.30)


class DesignTokens(BaseModel):
    """One design system: palette, type, theme and motion."""

    name: str
    palette: Palette
    type: TypeScale = Field(default_factory=TypeScale)
    theme: str = "light"
    motion: Motion = Field(default_factory=Motion)


def designs_dir() -> Path:
    """The directory holding the design JSON files."""
    return DESIGNS_DIR


def load_design(
    name: str = "", directory: Path | str | None = None
) -> DesignTokens:
    """Load ``<directory>/<name>.json`` and validate it."""
    resolved = (name or DEFAULT_DESIGN).strip()
    if not _NAME_RE.match(resolved):
        raise DesignError(
            f"invalid design name {resolved!r}: use lowercase letters, digits "
            "and underscores"
        )
    root = Path(directory) if directory is not None else DESIGNS_DIR
    path = root / f"{resolved}.json"
    if not path.is_file():
        raise DesignError(f"unknown design {resolved!r}: {path} is missing")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DesignError(f"design {resolved!r} cannot be read: {exc}") from exc
    if not isinstance(data, dict):
        raise DesignError(f"design {resolved!r} must be a JSON object")
    data.setdefault("name", resolved)
    try:
        return DesignTokens.model_validate(data)
    except ValidationError as exc:
        raise DesignError(f"design {resolved!r} is invalid: {exc}") from exc
```

```json
// designs/cream_editorial.json
{
  "name": "cream_editorial",
  "palette": {
    "bg": "#EFEAE3",
    "ink": "#141210",
    "accent": "#C8102E",
    "muted": "#8A8378"
  },
  "type": { "display": "Montserrat", "body": "Inter", "display_weight": 700 },
  "theme": "light",
  "motion": { "tempo": 1.0, "entry": "rise", "drift": 1.06 }
}
```

```json
// designs/ink_noir.json
{
  "name": "ink_noir",
  "palette": {
    "bg": "#0E0E10",
    "ink": "#F5F1E8",
    "accent": "#E4572E",
    "muted": "#7A7468"
  },
  "type": { "display": "Montserrat", "body": "Inter", "display_weight": 600 },
  "theme": "dark",
  "motion": { "tempo": 0.9, "entry": "rise", "drift": 1.05 }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_designs.py -v`
Expected: PASS (all tests pass)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/video_editor_core/designs.py designs tests/test_designs.py
git commit -m "feat(core): add validated design-token loader and two designs"
```

---

### Task 4: Typographic layouts

**Files:**
- Create: `packages/core/src/video_editor_core/typographic_scenes.py`
- Test: `tests/test_typographic_scenes.py`

**Interfaces:**
- Consumes: `DesignTokens` from Task 3; `video_editor_core.cues.resolve_cues`; `video_editor_core.fonts.font_stack`.
- Produces:
  - `SceneRenderError`, `SceneRender` (fields `markup: str`, `css: str`).
  - `render_typographic(scene: dict, design: DesignTokens, *, index: int, duration: float, width: int = 1080, words: Sequence[Mapping[str, Any]] | None = None) -> SceneRender`.
  - Layouts read from `scene["props"]`: `big_stat` (`headline`, `sub`, `unit`, optional `cue`), `bar_compare` (`bars` = 2–4 × `{label, value, unit}`), `definition` (`term`, `gloss`, `mark`), `sequence` (`steps` = 3–5 strings or `{label}`), `array` (`cells` 4–60, `cols`, `highlight` = cell indices).
- Consumed by Task 5.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_typographic_scenes.py
from __future__ import annotations

import re

import pytest

from video_editor_core.designs import load_design
from video_editor_core.typographic_scenes import SceneRenderError, render_typographic

DESIGN = load_design("cream_editorial")


def render(props: dict, *, duration: float = 4.0, words=None, scene_id: str = "scene_1"):
    return render_typographic(
        {"id": scene_id, "type": "typographic", "props": props},
        DESIGN,
        index=1,
        duration=duration,
        width=1080,
        words=words,
    )


def test_big_stat_renders_headline_sub_and_unit():
    result = render(
        {"layout": "big_stat", "headline": "$32B", "unit": "USD", "sub": "largest ever"}
    )
    assert "$32B" in result.markup
    assert "USD" in result.markup
    assert "largest ever" in result.markup
    assert "typo-headline" in result.css


def test_bar_compare_scales_bars_to_the_largest_value():
    result = render(
        {
            "layout": "bar_compare",
            "bars": [
                {"label": "Wiz", "value": 32, "unit": "B"},
                {"label": "Nest", "value": 3.2, "unit": "B"},
            ],
        }
    )
    widths = [float(w) for w in re.findall(r'class="typo-bar" style="width:([\d.]+)%"', result.markup)]
    assert widths[0] == 100.0
    assert widths[1] == pytest.approx(10.0)
    assert "Nest" in result.markup


def test_bar_compare_rejects_one_or_five_bars():
    with pytest.raises(SceneRenderError, match="2-4"):
        render({"layout": "bar_compare", "bars": [{"label": "A", "value": 1}]})


def test_definition_renders_term_and_gloss():
    result = render({"layout": "definition", "term": "A digital X-ray", "gloss": "Seeing inside a cloud."})
    assert "A digital X-ray" in result.markup
    assert "Seeing inside a cloud." in result.markup


def test_sequence_renders_steps_and_a_connector():
    result = render({"layout": "sequence", "steps": ["Find", "Watch", "Fix"]})
    assert result.markup.count("typo-step") == 3
    assert "typo-connector" in result.markup


def test_array_lights_the_requested_cells_in_order():
    result = render({"layout": "array", "cells": 12, "cols": 4, "highlight": [0, 3, 7]})
    assert result.markup.count("typo-cell") == 12
    assert result.markup.count("is-hot") == 3
    assert "nth-child(3)" in result.css


def test_text_is_escaped():
    result = render({"layout": "definition", "term": "<script>", "gloss": "a & b"})
    assert "<script>" not in result.markup
    assert "&lt;script&gt;" in result.markup
    assert "a &amp; b" in result.markup


def test_tokens_reach_the_html():
    dark = load_design("ink_noir")
    result = render_typographic(
        {"id": "s", "type": "typographic", "props": {"layout": "big_stat", "headline": "X"}},
        dark,
        index=2,
        duration=3.0,
        width=1920,
    )
    assert dark.palette.bg in result.css
    assert dark.palette.accent in result.css
    assert "Montserrat" in result.css


def test_unknown_layout_is_rejected():
    with pytest.raises(SceneRenderError, match="unknown layout"):
        render({"layout": "pie_chart"})


def test_word_cued_reveal_precedes_the_stagger_fallback():
    words = [{"word": "Nest", "start": 2.4, "end": 2.6}]
    result = render(
        {
            "layout": "bar_compare",
            "bars": [
                {"label": "Wiz", "value": 32},
                {"label": "Nest", "value": 3},
            ],
        },
        words=words,
    )
    delays = [float(d) for d in re.findall(r"animation-delay:([\d.]+)s", result.markup)]
    assert delays[1] == pytest.approx(2.4)
    assert delays[0] < delays[1]


def test_reveal_never_schedules_past_the_scene():
    words = [{"word": "Nest", "start": 9.5, "end": 9.7}]
    result = render(
        {
            "layout": "bar_compare",
            "bars": [
                {"label": "Wiz", "value": 32},
                {"label": "Nest", "value": 3},
            ],
        },
        duration=3.0,
        words=words,
    )
    delays = [float(d) for d in re.findall(r"animation-delay:([\d.]+)s", result.markup)]
    assert max(delays) <= 3.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_typographic_scenes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_editor_core.typographic_scenes'`

- [ ] **Step 3: Write the implementation**

```python
# packages/core/src/video_editor_core/typographic_scenes.py
"""Render a typographic scene as HTML for the HyperFrames compiler.

Every layout is a pure function of the scene's ``props`` and the design
tokens, so it can be asserted on with plain strings. The compiler wraps the
returned markup in the root-level ``<div class="clip">`` that HyperFrames needs
for seeking; everything here nests inside it.

Each layout returns its items in reveal order. An item's reveal time is the
moment its own words are spoken when the narration contains them
(``resolve_cues``); otherwise items fall in on a short stagger after the entry
motion. Both paths are bounded by the scene duration, so a late word can never
schedule a reveal after the scene has ended.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from video_editor_core.cues import resolve_cues
from video_editor_core.designs import DesignTokens
from video_editor_core.fonts import font_stack

#: Base entry motion. Divided by the design's ``motion.tempo`` so a bolder
#: design moves faster without every layout knowing about it.
ENTRY_SECONDS = 0.35


class SceneRenderError(ValueError):
    """A typographic scene whose props cannot be rendered."""


@dataclass(frozen=True)
class SceneRender:
    """The scene's inner markup plus the CSS/keyframes it needs."""

    markup: str
    css: str


@dataclass(frozen=True)
class _Item:
    """One revealable block: markup, the words that cue it, and its class."""

    html: str
    cue: str = ""
    class_name: str = "typo-line"


def _unit(width: int) -> float:
    """Scale factor from the 1080-wide design canvas to ``width``."""
    return max(0.5, width / 1080.0)


def _require(props: Mapping[str, Any], key: str) -> str:
    value = str(props.get(key) or "").strip()
    if not value:
        raise SceneRenderError(f"layout {props.get('layout')!r} needs a non-empty {key!r}")
    return value


def _big_stat(
    props: Mapping[str, Any], scene: Mapping[str, Any], index: int, duration: float, width: int
) -> tuple[list[_Item], str]:
    headline = html.escape(_require(props, "headline"))
    unit = str(props.get("unit") or "").strip()
    sub = str(props.get("sub") or "").strip()
    cue = str(props.get("cue") or "").strip()
    head = f'<span class="typo-headline">{headline}</span>'
    if unit:
        head += f'<span class="typo-unit">{html.escape(unit)}</span>'
    items = [_Item(head, cue=cue)]
    if sub:
        items.append(_Item(html.escape(sub), class_name="typo-line typo-sub"))
    return items, ""


def _bar_compare(
    props: Mapping[str, Any], scene: Mapping[str, Any], index: int, duration: float, width: int
) -> tuple[list[_Item], str]:
    bars = props.get("bars")
    if not isinstance(bars, list) or not 2 <= len(bars) <= 4:
        raise SceneRenderError("bar_compare needs `bars` with 2-4 entries")
    try:
        values = [float(bar["value"]) for bar in bars]
    except (KeyError, TypeError, ValueError) as exc:
        raise SceneRenderError(f"bar_compare bar without a numeric `value`: {exc}") from exc
    largest = max(values) or 1.0
    items: list[_Item] = []
    for bar, value in zip(bars, values, strict=True):
        label = html.escape(str(bar.get("label") or ""))
        unit = html.escape(str(bar.get("unit") or ""))
        percent = max(4.0, 100.0 * value / largest)
        items.append(
            _Item(
                f'<div class="typo-bar-label">{label}</div>'
                f'<div class="typo-bar-track"><div class="typo-bar" '
                f'style="width:{percent:.1f}%"></div></div>'
                f'<div class="typo-bar-value">{value:g}{unit}</div>',
                cue=str(bar.get("label") or ""),
                class_name="typo-bar-row",
            )
        )
    return items, ""


def _definition(
    props: Mapping[str, Any], scene: Mapping[str, Any], index: int, duration: float, width: int
) -> tuple[list[_Item], str]:
    term = _require(props, "term")
    gloss = _require(props, "gloss")
    mark = str(props.get("mark") or "—").strip()
    return (
        [
            _Item(f'<span class="typo-mark">{html.escape(mark)}</span>'),
            _Item(f'<span class="typo-term">{html.escape(term)}</span>', cue=term),
            _Item(html.escape(gloss), class_name="typo-line typo-gloss"),
        ],
        "",
    )


def _sequence(
    props: Mapping[str, Any], scene: Mapping[str, Any], index: int, duration: float, width: int
) -> tuple[list[_Item], str]:
    steps = props.get("steps")
    if not isinstance(steps, list) or not 3 <= len(steps) <= 5:
        raise SceneRenderError("sequence needs `steps` with 3-5 entries")
    labels: list[str] = []
    for raw in steps:
        label = raw.get("label") if isinstance(raw, Mapping) else raw
        text = str(label or "").strip()
        if not text:
            raise SceneRenderError("sequence steps must be non-empty")
        labels.append(text)
    items = [_Item('<div class="typo-connector"></div>', class_name="typo-connector-row")]
    for order, label in enumerate(labels, start=1):
        items.append(
            _Item(
                f'<div class="typo-step"><span class="typo-dot"></span>'
                f'<span class="typo-step-label">{order}. {html.escape(label)}</span></div>',
                cue=label,
                class_name="typo-step-row",
            )
        )
    return items, ""


def _array(
    props: Mapping[str, Any], scene: Mapping[str, Any], index: int, duration: float, width: int
) -> tuple[list[_Item], str]:
    try:
        cells = int(props.get("cells", 24))
        cols = int(props.get("cols", 6))
        highlight = [int(value) for value in (props.get("highlight") or [])]
    except (TypeError, ValueError) as exc:
        raise SceneRenderError(f"array props must be integers: {exc}") from exc
    if not 4 <= cells <= 60:
        raise SceneRenderError("array needs `cells` between 4 and 60")
    if not 1 <= cols <= cells:
        raise SceneRenderError("array needs `cols` between 1 and `cells`")
    hot = {value for value in highlight if 0 <= value < cells}
    grid = "".join(
        f'<div class="typo-cell{" is-hot" if cell in hot else ""}"></div>'
        for cell in range(cells)
    )
    step = min(0.12, max(0.03, (duration - ENTRY_SECONDS) / cells))
    extra = "\n".join(
        f"      .typo-{index} .typo-cell:nth-child({cell + 1}) "
        f"{{ animation-delay: {ENTRY_SECONDS + cell * step:.3f}s; }}"
        for cell in range(cells)
    )
    markup = (
        f'<div class="typo-grid" style="grid-template-columns:repeat({cols},1fr)">'
        f"{grid}</div>"
    )
    return [_Item(markup, class_name="typo-grid-row")], extra


_LAYOUTS: dict[str, Callable[..., tuple[list[_Item], str]]] = {
    "big_stat": _big_stat,
    "bar_compare": _bar_compare,
    "definition": _definition,
    "sequence": _sequence,
    "array": _array,
}


def _base_css(index: int, design: DesignTokens, duration: float, width: int) -> str:
    """The per-scene stylesheet; every selector is scoped to ``.typo-{index}``."""
    u = _unit(width)
    entry = ENTRY_SECONDS / design.motion.tempo
    palette = design.palette
    body = font_stack(design.type.body)
    display = font_stack(design.type.display)
    return f"""
      @keyframes tp_entry_{index} {{
        from {{ opacity: 0; transform: translate3d(0, {int(22 * u)}px, 0); }}
        to {{ opacity: 1; transform: translate3d(0, 0, 0); }}
      }}
      @keyframes tp_drift_{index} {{
        from {{ transform: scale(1); }}
        to {{ transform: scale({design.motion.drift}); }}
      }}
      @keyframes tp_grow_{index} {{ from {{ width: 0; }} }}
      .typo-{index} {{ position: absolute; inset: 0; background: {palette.bg}; overflow: hidden; }}
      .typo-{index} .typo-stage {{ position: absolute; inset: 0; display: flex;
        flex-direction: column; align-items: center; justify-content: center;
        gap: {int(26 * u)}px; padding: 0 {int(90 * u)}px; box-sizing: border-box;
        animation: tp_drift_{index} {duration:.3f}s linear both; }}
      .typo-{index} .typo-line {{ opacity: 0;
        animation: tp_entry_{index} {entry:.3f}s ease-out both;
        font-family: {body}; color: {palette.ink}; text-align: center;
        font-size: {int(46 * u)}px; line-height: 1.25; }}
      .typo-{index} .typo-headline {{ font-family: {display};
        font-weight: {design.type.display_weight}; font-size: {int(168 * u)}px;
        line-height: 0.95; letter-spacing: -0.02em; color: {palette.ink}; }}
      .typo-{index} .typo-unit {{ font-family: {display}; font-weight: 500;
        font-size: {int(48 * u)}px; color: {palette.accent}; margin-left: {int(10 * u)}px; }}
      .typo-{index} .typo-sub {{ font-size: {int(44 * u)}px; color: {palette.muted};
        max-width: 26ch; }}
      .typo-{index} .typo-mark {{ opacity: 0;
        animation: tp_entry_{index} {entry:.3f}s ease-out both;
        width: {int(64 * u)}px; height: {int(6 * u)}px; background: {palette.accent}; }}
      .typo-{index} .typo-term {{ opacity: 0;
        animation: tp_entry_{index} {entry:.3f}s ease-out both;
        font-family: {display}; font-weight: {design.type.display_weight};
        font-size: {int(96 * u)}px; color: {palette.ink}; text-align: center; }}
      .typo-{index} .typo-gloss {{ max-width: 24ch; color: {palette.muted}; }}
      .typo-{index} .typo-bar-row {{ opacity: 0;
        animation: tp_entry_{index} {entry:.3f}s ease-out both; width: 100%;
        display: grid; grid-template-columns: {int(180 * u)}px 1fr {int(150 * u)}px;
        align-items: center; gap: {int(18 * u)}px; font-family: {body};
        font-size: {int(38 * u)}px; color: {palette.ink}; }}
      .typo-{index} .typo-bar-label {{ text-align: right; font-weight: 600; }}
      .typo-{index} .typo-bar-track {{ height: {int(44 * u)}px; background: {palette.bg};
        border: {max(1, int(2 * u))}px solid {palette.muted};
        border-radius: {int(4 * u)}px; overflow: hidden; }}
      .typo-{index} .typo-bar {{ height: 100%; background: {palette.accent};
        animation: tp_grow_{index} {entry * 1.6:.3f}s ease-out both; }}
      .typo-{index} .typo-bar-value {{ font-family: {display};
        font-weight: {design.type.display_weight}; font-size: {int(44 * u)}px; }}
      .typo-{index} .typo-connector-row {{ opacity: 0;
        animation: tp_entry_{index} {entry:.3f}s ease-out both; flex: none; }}
      .typo-{index} .typo-connector {{ width: {max(2, int(4 * u))}px;
        height: {int(180 * u)}px; background: {palette.muted}; }}
      .typo-{index} .typo-step-row {{ opacity: 0;
        animation: tp_entry_{index} {entry:.3f}s ease-out both; width: 100%; }}
      .typo-{index} .typo-step {{ display: flex; align-items: center;
        gap: {int(26 * u)}px; font-family: {body}; font-size: {int(42 * u)}px;
        color: {palette.ink}; }}
      .typo-{index} .typo-dot {{ width: {int(26 * u)}px; height: {int(26 * u)}px;
        border-radius: 50%; background: {palette.accent}; flex: none; }}
      .typo-{index} .typo-grid-row {{ opacity: 0;
        animation: tp_entry_{index} {entry:.3f}s ease-out both; width: 100%; }}
      .typo-{index} .typo-grid {{ display: grid; gap: {int(14 * u)}px; width: 100%; }}
      .typo-{index} .typo-cell {{ aspect-ratio: 1 / 1; background: transparent;
        border: {max(1, int(2 * u))}px solid {palette.muted}; }}
      .typo-{index} .typo-cell.is-hot {{ background: {palette.accent};
        border-color: {palette.accent};
        animation: tp_entry_{index} {entry:.3f}s ease-out both; }}
    """


def render_typographic(
    scene: Mapping[str, Any],
    design: DesignTokens,
    *,
    index: int,
    duration: float,
    width: int = 1080,
    words: Sequence[Mapping[str, Any]] | None = None,
) -> SceneRender:
    """Render ``scene`` for the compiler; ``index`` scopes every selector."""
    props = dict(scene.get("props") or {})
    layout = str(props.get("layout") or "").strip()
    layout_fn = _LAYOUTS.get(layout)
    if layout_fn is None:
        raise SceneRenderError(
            f"scene {scene.get('id', index)!r} has unknown layout {layout!r}; "
            f"choose one of {sorted(_LAYOUTS)}"
        )
    items, extra_css = layout_fn(props, scene, index, duration, width)
    cues = {f"item_{position}": item.cue for position, item in enumerate(items) if item.cue}
    times = resolve_cues(words or [], cues, lead=0.0) if (words and cues) else {}

    stagger = min(0.40, max(0.10, (duration - ENTRY_SECONDS) / max(len(items), 1) / 2.5))
    latest = max(0.0, duration - ENTRY_SECONDS)
    delays: list[float] = []
    for position, item in enumerate(items):
        cued = times.get(f"item_{position}")
        fallback = ENTRY_SECONDS * 0.4 + position * stagger
        delays.append(min(latest, cued if cued is not None else fallback))

    body = "".join(
        f'<div class="{item.class_name}" style="animation-delay:{delay:.3f}s">'
        f"{item.html}</div>"
        for item, delay in zip(items, delays, strict=True)
    )
    markup = f'<div class="typo-{index}"><div class="typo-stage">{body}</div></div>'
    return SceneRender(
        markup=markup, css=_base_css(index, design, duration, width) + extra_css
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_typographic_scenes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/video_editor_core/typographic_scenes.py tests/test_typographic_scenes.py
git commit -m "feat(core): render five typographic layouts from design tokens"
```

---

### Task 5: Compiler branch, scene kind and narration audio

**Files:**
- Modify: `apps/engine/src/video_editor_engine/models/scene_plan.py:13` (`_ALLOWED_SCENE_TYPES`), `:60-64` (`ScenePlan.design`)
- Modify: `packages/core/src/video_editor_core/scene_plan_composition.py:63-119` (`compile_scene_plan_project`), `:120-176` (`_scene_elements`), `:178-222` (`_render_html`)
- Modify: `apps/engine/src/video_editor_engine/templates/yaml_templates.py:50` (`_validate_template`)
- Test: `tests/test_explainer_composition.py`

**Interfaces:**
- Consumes: `load_design`, `font_face_css`, `render_typographic` from Tasks 3–4.
- Produces: `Scene(type="typographic")` is valid; `ScenePlan.design: str = ""`; the compiler honours `plan["design"]`, `plan["narration_path"]` and `scene["props"]["word_times"]`.
- Consumed by Tasks 7, 9.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_explainer_composition.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from video_editor_core.scene_plan_composition import compile_scene_plan_project
from video_editor_engine.models.scene_plan import Scene, ScenePlan


def typographic_plan(**extra) -> dict:
    plan = {
        "job_id": "typo_job",
        "aspect_ratio": "9:16",
        "design": "cream_editorial",
        "scenes": [
            {
                "id": "scene_1",
                "type": "typographic",
                "duration_seconds": 4.0,
                "narration": "Thirty-two billion dollars.",
                "props": {"layout": "big_stat", "headline": "$32B"},
            }
        ],
    }
    plan.update(extra)
    return plan


def test_typographic_scene_type_and_design_field_are_accepted():
    scene = Scene(id="s1", type="typographic", duration_seconds=3.0, narration="x")
    plan = ScenePlan(job_id="j", script="s", scenes=[scene], design="ink_noir")
    assert plan.design == "ink_noir"


def test_unknown_scene_type_is_still_rejected():
    with pytest.raises(ValidationError):
        Scene(id="s1", type="motion_graphics", duration_seconds=3.0)


def test_typographic_scene_compiles_without_an_image(tmp_path):
    index = compile_scene_plan_project(typographic_plan(), tmp_path)
    html = index.read_text(encoding="utf-8")
    assert "<img" not in html
    assert 'data-start="0.000"' in html
    assert 'data-duration="4.000"' in html
    assert "$32B" in html
    assert "@font-face" in html
    assert "typo-0" in html


def test_plan_narration_is_emitted_once_at_the_root(tmp_path):
    narration = tmp_path / "narration.mp3"
    narration.write_bytes(b"ID3fake")
    index = compile_scene_plan_project(
        typographic_plan(narration_path=str(narration)), tmp_path / "project"
    )
    html = index.read_text(encoding="utf-8")
    assert html.count('id="narration"') == 1
    assert "assets/narration.mp3" in html


def test_word_times_reach_the_layout(tmp_path):
    plan = typographic_plan()
    plan["scenes"][0]["props"] = {
        "layout": "bar_compare",
        "bars": [{"label": "Wiz", "value": 32}, {"label": "Nest", "value": 3}],
        "word_times": [{"word": "Nest", "start": 2.5, "end": 2.7}],
    }
    html = compile_scene_plan_project(plan, tmp_path).read_text(encoding="utf-8")
    assert "animation-delay:2.500s" in html


def test_a_plan_with_no_design_still_compiles(tmp_path):
    plan = typographic_plan()
    del plan["design"]
    assert "typo-0" in compile_scene_plan_project(plan, tmp_path).read_text(encoding="utf-8")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_explainer_composition.py -v`
Expected: FAIL — `ValidationError: scene type must be one of ... got 'typographic'`, and `ScenePlan` rejects `design` as an unexpected field only if configured that way; either way the compile assertions fail first.

- [ ] **Step 3: Make the model changes**

In `apps/engine/src/video_editor_engine/models/scene_plan.py`, add `"typographic"` to the set:

```python
_ALLOWED_SCENE_TYPES = frozenset(
    {
        "opening",
        "narration",
        "narrative",
        "broll",
        "closing",
        "transition",
        "music_visual",
        "story",
        "typographic",
    }
)
```

And in `ScenePlan`, after `music_path`:

```python
    design: str = ""
    narration_path: str = ""
```

`narration_path` lives on the plan (not on a scene) so the model round-trips it for the compiler.

- [ ] **Step 4: Make the compiler changes**

At the top of `scene_plan_composition.py`, add the imports:

```python
from video_editor_core.designs import DesignTokens, load_design
from video_editor_core.fonts import font_face_css
from video_editor_core.typographic_scenes import SceneRenderError, render_typographic
```

In `compile_scene_plan_project`, after `scenes`/`total`:

```python
    needs_design = any(
        isinstance(scene, dict) and scene.get("type") == "typographic"
        for scene in scenes
    )
    design = load_design(str(plan.get("design") or "")) if needs_design else None
    extra_css = ""
    if design is not None:
        # A design may name both families; only bundled ones are embedded.
        extra_css = font_face_css(
            [design.type.display, design.type.body], assets_dir / "fonts"
        )
```

Change the per-scene call to pass the new keywords:

```python
        elements.extend(
            _scene_elements(
                scene, index, cursor, duration, copier, keyframes,
                width=width, design=design,
            )
        )
```

Add the narration element just before the music block:

```python
    narration_path = plan.get("narration_path") or ""
    if narration_path and Path(narration_path).exists():
        rel = copier.copy(str(narration_path))
        elements.append(
            f'    <audio id="narration" src="{html.escape(rel)}" data-start="0" '
            f'data-duration="{total:.3f}" data-track-index="150"></audio>'
        )
```

Pass `extra_css` to the shell:

```python
    index_html = _render_html(
        ...
        keyframes="\n".join(keyframes),
        extra_css=extra_css,
    )
```

Change `_scene_elements`'s signature and add the branch:

```python
def _scene_elements(
    scene: dict[str, Any],
    index: int,
    start: float,
    duration: float,
    copier: _AssetCopier,
    keyframes: list[str],
    *,
    width: int = 1920,
    design: DesignTokens | None = None,
) -> list[str]:
    elements: list[str] = []
    visual = _asset_by_type(scene, "video", "image", "data_viz")
    vstart = f'data-start="{start:.3f}" data-duration="{duration:.3f}"'

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
            words=list(props.get("word_times") or []),
        )
        keyframes.append(rendered.css)
        elements.append(
            f'    <div id="scene_{index}_typo" class="clip" {vstart} '
            f'data-track-index="{index}" style="background:{design.palette.bg}">'
            f"{rendered.markup}</div>"
        )
    elif visual is not None:
        ...  # unchanged
```

Finally `_render_html` gains `extra_css: str = ""` and the style block emits it after `{keyframes}`:

```python
      .placeholder {{ ... }}
{keyframes}
{extra_css}
    </style>
```

- [ ] **Step 5: Derive the template kind list instead of duplicating it**

In `apps/engine/src/video_editor_engine/templates/yaml_templates.py`, add `get_args` to the
imports and define the kind set once, above `_validate_template`:

```python
from typing import Any, get_args

_ALLOWED_KINDS: frozenset[str] = frozenset(get_args(PipelineKind))
```

Then replace the hardcoded `allowed_kinds` set and its check inside
`_validate_template` with:

```python
    if raw["pipeline_kind"] not in _ALLOWED_KINDS:
        raise ValueError(
            f"Template {raw['name']!r} has invalid pipeline_kind {raw['pipeline_kind']!r}"
        )
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_explainer_composition.py tests/test_scene_plan_composition.py -v`
Expected: PASS, and the existing composition tests stay green.

Run: `uv run pytest -q`
Expected: full suite passes.

- [ ] **Step 7: Commit**

```bash
git add apps/engine/src/video_editor_engine/models/scene_plan.py \
  apps/engine/src/video_editor_engine/templates/yaml_templates.py \
  packages/core/src/video_editor_core/scene_plan_composition.py \
  tests/test_explainer_composition.py
git commit -m "feat(compiler): render typographic scenes and a root narration track"
```

---

### Task 6: Timing from narration

**Files:**
- Create: `apps/cli/src/video_editor_cli/explainer_timing.py`
- Test: `tests/test_explainer_timing.py`

**Interfaces:**
- Produces:
  - `SceneTiming` (`id`, `start`, `end`, `words`; property `duration`).
  - `align_words(narrations: Sequence[str], recognised: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]` — display words with `word`, `start_char`, `start`, `end`.
  - `spread_pinned_tail(words: list[dict], audio_end: float, min_gap: float = 0.6) -> int`.
  - `derive_scene_timings(narrations: Sequence[str], words: list[dict], *, ids: Sequence[str] | None = None) -> list[SceneTiming]`.
  - Constants `LEAD_IN_SECONDS = 0.12`, `INTERIOR_TAIL_SECONDS = 0.25`, `FINAL_TAIL_SECONDS = 1.2`, `MIN_SCENE_SECONDS = 1.2`.
- Consumed by Tasks 8, 9.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_explainer_timing.py
from __future__ import annotations

import pytest

from video_editor_cli.explainer_timing import (
    FINAL_TAIL_SECONDS,
    LEAD_IN_SECONDS,
    align_words,
    derive_scene_timings,
    spread_pinned_tail,
)


def spoken(*pairs: tuple[str, float, float]) -> list[dict]:
    return [{"word": word, "start": start, "end": end} for word, start, end in pairs]


def test_align_words_times_the_display_spelling():
    words = align_words(
        ["Sah-joo is four pillars."],
        spoken(("saju", 0.2, 0.5), ("is", 0.5, 0.6), ("four", 0.6, 0.8), ("pillars", 0.8, 1.2)),
    )
    assert [word["word"] for word in words] == ["Sah-joo", "is", "four", "pillars."]
    assert words[0]["start"] == 0.2
    assert words[-1]["end"] == 1.2
    # A word holds until the next one starts, so karaoke has no gaps.
    assert words[1]["end"] == words[2]["start"]


def test_scene_starts_a_lead_in_before_the_first_word():
    narrations = ["One two three.", "Four five six."]
    words = align_words(
        narrations,
        spoken(("one", 0.0, 0.3), ("two", 0.3, 0.6), ("three", 0.6, 0.9),
               ("four", 2.0, 2.3), ("five", 2.3, 2.6), ("six", 2.6, 2.9)),
    )
    timings = derive_scene_timings(narrations, words)
    assert timings[1].start == pytest.approx(2.0 - LEAD_IN_SECONDS)
    assert timings[0].end == timings[1].start


def test_final_scene_holds_after_the_last_word():
    narrations = ["Alpha beta."]
    words = align_words(narrations, spoken(("alpha", 0.1, 0.4), ("beta", 0.4, 0.9)))
    timings = derive_scene_timings(narrations, words)
    assert timings[-1].end == pytest.approx(0.9 + FINAL_TAIL_SECONDS)


def test_a_single_word_scene_still_gets_a_minimum_length():
    narrations = ["Alpha beta gamma delta.", "Stop."]
    words = align_words(
        narrations,
        spoken(("alpha", 0.0, 0.3), ("beta", 0.3, 0.6), ("gamma", 0.6, 0.9),
               ("delta", 0.9, 1.2), ("stop", 1.25, 1.3)),
    )
    timings = derive_scene_timings(narrations, words)
    assert timings[1].duration >= 1.2
    # Scenes are contiguous: the first ends where the second begins.
    assert timings[0].end == timings[1].start


def test_a_scene_the_recogniser_dropped_falls_back_to_a_word_count_estimate():
    narrations = ["Alpha beta.", "Gamma delta epsilon zeta eta theta."]
    words = align_words(narrations, spoken(("alpha", 0.0, 0.3), ("beta", 0.3, 0.6)))
    timings = derive_scene_timings(narrations, words)
    assert timings[1].words == []
    assert timings[1].duration == pytest.approx(0.38 * 6)


def test_scene_ids_are_numbered_in_script_order():
    timings = derive_scene_timings(
        ["One.", "Two."], align_words(["One.", "Two."], spoken(("one", 0.0, 0.3), ("two", 1.0, 1.3)))
    )
    assert [timing.id for timing in timings] == ["scene_1", "scene_2"]


def test_spread_pinned_tail_gives_the_dropped_tail_real_times():
    words = [
        {"word": "alpha", "start": 0.0, "end": 0.4},
        {"word": "beta", "start": 0.4, "end": 0.8},
        {"word": "gamma", "start": 0.8, "end": 0.8},
        {"word": "delta", "start": 0.8, "end": 0.8},
    ]
    moved = spread_pinned_tail(words, audio_end=3.0)
    assert moved == 2
    assert words[-1]["end"] == pytest.approx(3.0)
    assert words[2]["start"] < words[3]["start"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_explainer_timing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_editor_cli.explainer_timing'`

- [ ] **Step 3: Write the implementation**

```python
# apps/cli/src/video_editor_cli/explainer_timing.py
"""Lay the written narration onto the words Whisper actually heard.

Synthetic narration is transcribed into the *phonetic* spelling we sent
("Sah-joo", not "Saju"), so captions written from the transcript would show
the wrong text. The known display narration is aligned onto the recognised
stream character by character (``difflib.SequenceMatcher``), and every display
word inherits the timing at its position — correct captions that still land on
the voice.

Scene boundaries then follow the words, never a written duration:

* a scene starts ``LEAD_IN_SECONDS`` before its first word, never earlier than
  the previous scene's end;
* it ends ``INTERIOR_TAIL_SECONDS`` after its last word, or
  ``FINAL_TAIL_SECONDS`` on the final scene;
* scenes are contiguous, and a single scene never runs shorter than
  ``MIN_SCENE_SECONDS``; a scene the recogniser dropped falls back to a
  word-count estimate.

The alignment and tail-spreading are the saju build's algorithm
(``videos/2026-10-22/saju-zodiac-animal/build/transcribe.py``), which also
produced the #268 lesson: run Whisper with ``vad_filter=False`` and an
``initial_prompt``, or it trims a synthetic tail and collapses the last scene.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

LEAD_IN_SECONDS = 0.12
INTERIOR_TAIL_SECONDS = 0.25
FINAL_TAIL_SECONDS = 1.2
MIN_SCENE_SECONDS = 1.2


@dataclass(frozen=True)
class SceneTiming:
    """Where one scene sits in the narration track, and the words inside it."""

    id: str
    start: float
    end: float
    words: list[dict[str, Any]] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)


def _norm(text: str) -> str:
    """Lowercase letters and digits only, so punctuation cannot break matching."""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _words_with_spans(text: str) -> list[tuple[str, int, int]]:
    """(word, start, end) offsets over ``_norm`` of the whole text."""
    spans: list[tuple[str, int, int]] = []
    cursor = 0
    for raw in text.split():
        width = len(_norm(raw))
        if width == 0:
            continue
        spans.append((raw, cursor, cursor + width))
        cursor += width
    return spans


def _matching_blocks(expected: str, recognised: str) -> list[tuple[int, int, int]]:
    matcher = difflib.SequenceMatcher(a=expected, b=recognised, autojunk=False)
    return [(block.a, block.b, block.size) for block in matcher.get_matching_blocks() if block.size]


def _recognised_spans(
    recognised: Sequence[Mapping[str, Any]],
) -> list[tuple[int, int, float, float]]:
    spans: list[tuple[int, int, float, float]] = []
    cursor = 0
    for word in recognised:
        width = len(_norm(str(word.get("word", ""))))
        if width == 0:
            continue
        spans.append(
            (cursor, cursor + width, float(word["start"]), float(word["end"]))
        )
        cursor += width
    return spans


def _offset_to_time(
    offset: int,
    blocks: Sequence[tuple[int, int, int]],
    spans: Sequence[tuple[int, int, float, float]],
) -> tuple[float, float] | None:
    """Map a character offset in the expected stream onto a recognised span."""
    if not spans:
        return None
    recorded: int | None = None
    for a, b, size in blocks:
        if a <= offset < a + size:
            recorded = b + (offset - a)
            break
        if offset < a:
            recorded = b
            break
    if recorded is None:
        return spans[-1][2], spans[-1][3]
    for low, high, start, end in spans:
        if low <= recorded < high:
            return start, end
    return spans[-1][2], spans[-1][3]


def align_words(
    narrations: Sequence[str],
    recognised: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Time the display narration using the recognised word stream."""
    expected_text = " ".join(narrations)
    expected_norm = _norm(expected_text)
    recognised_norm = "".join(_norm(str(word.get("word", ""))) for word in recognised)
    blocks = _matching_blocks(expected_norm, recognised_norm)
    spans = _recognised_spans(recognised)

    timed: list[dict[str, Any]] = []
    for word, start_char, _end_char in _words_with_spans(expected_text):
        mapped = _offset_to_time(start_char, blocks, spans)
        if mapped is None:
            continue
        timed.append(
            {
                "word": word,
                "start_char": start_char,
                "start": round(mapped[0], 3),
                "end": round(mapped[1], 3),
            }
        )
    # A word is current until the next one starts: that is what karaoke steps
    # through, and it removes the silent gaps between words.
    for position, word in enumerate(timed[:-1]):
        word["end"] = max(word["start"], timed[position + 1]["start"])
    return timed


def spread_pinned_tail(
    words: list[dict[str, Any]], audio_end: float, min_gap: float = 0.6
) -> int:
    """Give unmapped trailing words real times, weighted by word length.

    ``_offset_to_time`` falls back to the last recognised span, so a sentence
    the recogniser dropped leaves its words pinned to one instant. If that
    pinned run still leaves more than ``min_gap`` seconds unaccounted for,
    spread it across the remainder. Returns how many words were moved.
    """
    if len(words) < 2:
        return 0
    index = len(words) - 1
    while index > 0 and words[index]["start"] <= words[index - 1]["start"]:
        index -= 1
    pinned = words[index:]
    start = float(words[index]["start"])
    gap = audio_end - start
    if len(pinned) < 2 or gap < min_gap:
        return 0

    weights = [len(str(word["word"])) + 1 for word in pinned]
    total = sum(weights)
    cursor = start
    for word, weight in zip(pinned, weights, strict=True):
        span = gap * weight / total
        word["start"] = round(cursor, 3)
        word["end"] = round(cursor + span, 3)
        cursor += span
    for position, word in enumerate(words[:-1]):
        word["end"] = max(word["start"], words[position + 1]["start"])
    return len(pinned)


def _split_by_offset(
    narrations: Sequence[str], words: Sequence[Mapping[str, Any]]
) -> list[list[dict[str, Any]]]:
    """Group display words by the narration they came from."""
    groups: list[list[dict[str, Any]]] = [[] for _ in narrations]
    bounds: list[tuple[int, int]] = []
    cursor = 0
    for narration in narrations:
        width = len(_norm(narration))
        bounds.append((cursor, cursor + width))
        cursor += width
    for word in words:
        start_char = int(word.get("start_char", -1))
        for index, (low, high) in enumerate(bounds):
            if low <= start_char < high:
                groups[index].append(dict(word))
                break
    return groups


def derive_scene_timings(
    narrations: Sequence[str],
    words: list[dict[str, Any]],
    *,
    ids: Sequence[str] | None = None,
) -> list[SceneTiming]:
    """Split ``words`` across scenes and turn the split into durations."""
    scene_ids = list(ids) if ids is not None else [
        f"scene_{number}" for number in range(1, len(narrations) + 1)
    ]
    if len(scene_ids) != len(narrations):
        raise ValueError("ids must have one entry per narration")
    groups = _split_by_offset(narrations, words)
    last_index = len(narrations) - 1
    timings: list[SceneTiming] = []
    cursor = 0.0
    for index, narration in enumerate(narrations):
        group = groups[index]
        start = cursor
        if group:
            tail = FINAL_TAIL_SECONDS if index == last_index else INTERIOR_TAIL_SECONDS
            end = max(float(group[-1]["end"]) + tail, start + MIN_SCENE_SECONDS)
            if index < last_index:
                following = groups[index + 1]
                if following:
                    boundary = float(following[0]["start"]) - LEAD_IN_SECONDS
                    end = min(end, max(start + MIN_SCENE_SECONDS, boundary))
        else:
            end = start + max(1.5, 0.38 * len(narration.split()))
        end = round(end, 3)
        start = round(start, 3)
        timings.append(
            SceneTiming(id=scene_ids[index], start=start, end=end, words=list(group))
        )
        cursor = end
    return timings
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_explainer_timing.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/cli/src/video_editor_cli/explainer_timing.py tests/test_explainer_timing.py
git commit -m "feat(explainer): derive scene durations from aligned narration words"
```

---

### Task 7: Plan building and asset requests

**Files:**
- Create: `apps/cli/src/video_editor_cli/explainer_plan.py`
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/scene_assets.py` (`requests_from_scene`, `requests_from_plan`)
- Test: `tests/test_explainer_plan.py`

**Interfaces:**
- Consumes: `ExplainerScript` (Task 1), `plan_still_reuse`/`apply_reuse` (Task 2), `SceneTiming` (Task 6), `ScenePlan` (Task 5).
- Produces:
  - `build_explainer_plan(script: ExplainerScript, job_id: str, *, reuse: bool = True) -> tuple[dict[str, Any], ReusePlan]`.
  - `plan_model(plan: dict) -> ScenePlan`.
  - `explainer_requests(plan: dict, script: ExplainerScript, job_id: str, *, tts_provider: str = "stub_tts", image_provider: str = "stub_image") -> list[AssetRequest]`.
  - `rewrite_timings(plan: dict, timings: Sequence[SceneTiming]) -> None`.
  - `requests_from_scene(..., emit_tts: bool = True)`, `requests_from_plan(..., emit_tts: bool = True)`.
- Consumed by Tasks 8, 9.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_explainer_plan.py
from __future__ import annotations

from video_editor_asset_ingestion.models import AssetType
from video_editor_cli.explainer_plan import (
    build_explainer_plan,
    explainer_requests,
    plan_model,
    rewrite_timings,
)
from video_editor_cli.explainer_script import parse_explainer_script
from video_editor_cli.explainer_timing import SceneTiming

SCRIPT = """\
---
title: T
---
## One
visual: typographic
layout: big_stat
headline: $32B
narration: Thirty-two billion dollars.

## Two
visual: still
image: two black server racks in a cream data centre
narration: Wiz is five years old. It watches the whole cloud.

## Three
visual: still
image: two black server racks in a cream data centre
narration: The same racks, the same cloud, still watch.

## Four
visual: still
image: a gem on velvet
narration: A gem.
"""


def test_plan_carries_types_design_and_reuse():
    script = parse_explainer_script(SCRIPT)
    plan, reuse = build_explainer_plan(script, "job1")
    assert [scene["type"] for scene in plan["scenes"]] == [
        "typographic", "narration", "narration", "narration"
    ]
    assert plan["design"] == "cream_editorial"
    assert plan["scenes"][0]["visual_description"] == ""
    assert plan["scenes"][1]["visual_description"].startswith("two black")
    assert plan["scenes"][2]["props"]["image_scene"] == "scene_2"
    assert reuse.saved == 1


def test_one_narration_request_for_the_whole_script():
    script = parse_explainer_script(SCRIPT)
    plan, _ = build_explainer_plan(script, "job1")
    requests = explainer_requests(plan, script, "job1")
    tts = [request for request in requests if request.asset_type == AssetType.TTS]
    assert [request.asset_id for request in tts] == ["job_narration"]
    assert "Thirty-two billion" in tts[0].prompt
    assert "A gem." in tts[0].prompt


def test_a_reused_still_emits_one_image_request_less():
    script = parse_explainer_script(SCRIPT)
    plan, _ = build_explainer_plan(script, "job1")
    images = [
        request
        for request in explainer_requests(plan, script, "job1")
        if request.asset_type == AssetType.IMAGE
    ]
    assert [request.asset_id for request in images] == ["scene_2_image", "scene_4_image"]


def test_no_reuse_keeps_every_image_request():
    script = parse_explainer_script(SCRIPT)
    plan, reuse = build_explainer_plan(script, "job1", reuse=False)
    assert reuse.clusters == []
    images = [
        request
        for request in explainer_requests(plan, script, "job1")
        if request.asset_type == AssetType.IMAGE
    ]
    assert len(images) == 3


def test_voice_front_matter_reaches_the_narration_request():
    script = parse_explainer_script(
        SCRIPT.replace("title: T", "title: T\nvoice: sarang_en_narration\nvoice_model: bulbul:v3")
    )
    plan, _ = build_explainer_plan(script, "job1")
    tts = explainer_requests(plan, script, "job1")[0]
    assert tts.params["voice_id"] == "sarang_en_narration"
    assert tts.params["model"] == "bulbul:v3"


def test_rewrite_timings_sets_durations_and_word_times():
    script = parse_explainer_script(SCRIPT)
    plan, _ = build_explainer_plan(script, "job1")
    timings = [
        SceneTiming("scene_1", 0.0, 3.0, [{"word": "Thirty-two", "start": 0.1, "end": 0.5}]),
        SceneTiming("scene_2", 3.0, 6.0, [{"word": "Wiz", "start": 3.2, "end": 3.4}]),
        SceneTiming("scene_3", 6.0, 8.0, []),
        SceneTiming("scene_4", 8.0, 10.0, []),
    ]
    rewrite_timings(plan, timings)
    assert [scene["duration_seconds"] for scene in plan["scenes"]] == [3.0, 3.0, 2.0, 2.0]
    assert plan["scenes"][0]["props"]["word_times"] == [
        {"word": "Thirty-two", "start": 0.1, "end": 0.5}
    ]
    model = plan_model(plan)
    assert model.scenes[0].props["word_times"][0]["word"] == "Thirty-two"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_explainer_plan.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_editor_cli.explainer_plan'`

- [ ] **Step 3: Add `emit_tts` to `scene_assets.py`**

In `requests_from_scene`, add the keyword after `emit_image: bool = True`:

```python
    emit_tts: bool = True,
```

and guard the TTS block:

```python
    if emit_tts and scene.narration.strip():
```

In `requests_from_plan`, add the same keyword and forward it:

```python
def requests_from_plan(
    plan: ScenePlan,
    job_id: str,
    tts_provider: str = "stub_tts",
    image_provider: str = "stub_image",
    data_viz_provider: str = "matplotlib_data_viz",
    video_provider: str = "",
    emit_tts: bool = True,
) -> list[AssetRequest]:
```

```python
            emit_image=scene.id not in reused,
            emit_tts=emit_tts,
```

Update the docstring sentence that says "TTS, motion video and data viz are unaffected" to name `emit_tts`:

```
    ``emit_image=False`` skips the IMAGE request for a scene showing another
    scene's still; ``emit_tts=False`` skips its TTS request when the caller
    narrates the whole job in one request.
```

- [ ] **Step 4: Write `explainer_plan.py`**

```python
# apps/cli/src/video_editor_cli/explainer_plan.py
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

from collections.abc import Sequence
from typing import Any

from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.scene_assets import requests_from_plan
from video_editor_engine.models.scene_plan import ScenePlan

from video_editor_cli.explainer_script import ExplainerScript
from video_editor_cli.explainer_timing import SceneTiming
from video_editor_cli.still_reuse import ReusePlan, apply_reuse, plan_still_reuse

PLACEHOLDER_DURATION_SECONDS = 1.0


def build_explainer_plan(
    script: ExplainerScript, job_id: str, *, reuse: bool = True
) -> tuple[dict[str, Any], ReusePlan]:
    """Build the plan document plus the reuse decision that shaped it."""
    reuse_plan = plan_still_reuse(
        script.scenes, threshold=script.reuse_threshold, enabled=reuse
    )
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
        else:
            if scene.subject:
                props["subject"] = scene.subject
            scenes.append(
                {
                    "id": scene.id,
                    "type": "narration",
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
    return params


def explainer_requests(
    plan: dict[str, Any],
    script: ExplainerScript,
    job_id: str,
    *,
    tts_provider: str = "stub_tts",
    image_provider: str = "stub_image",
) -> list[AssetRequest]:
    """The paid requests for ``plan``: one narration, one still per cluster."""
    narration = AssetRequest(
        asset_id="job_narration",
        job_id=job_id,
        scene_id="job",
        asset_type=AssetType.TTS,
        prompt=script.narration,
        provider=tts_provider,
        output_ext="mp3",
        params=_narration_params(script),
    )
    return [narration, *requests_from_plan(
        plan_model(plan),
        job_id,
        tts_provider=tts_provider,
        image_provider=image_provider,
        emit_tts=False,
    )]


def rewrite_timings(
    plan: dict[str, Any], timings: Sequence[SceneTiming]
) -> None:
    """Replace the placeholder durations with the measured ones."""
    by_id = {timing.id: timing for timing in timings}
    for scene in plan.get("scenes", []) or []:
        timing = by_id.get(str(scene.get("id")))
        if timing is None:
            continue
        scene["duration_seconds"] = timing.duration
        props = scene.setdefault("props", {})
        if isinstance(props, dict):
            props["word_times"] = [
                {
                    "word": str(word["word"]),
                    "start": float(word["start"]),
                    "end": float(word["end"]),
                }
                for word in timing.words
            ]
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_explainer_plan.py tests/test_scene_assets.py -v`
Expected: PASS — the new tests pass and the existing request-builder tests stay green.

- [ ] **Step 6: Commit**

```bash
git add apps/cli/src/video_editor_cli/explainer_plan.py \
  packages/asset_ingestion/src/video_editor_asset_ingestion/scene_assets.py \
  tests/test_explainer_plan.py
git commit -m "feat(explainer): build the priced plan and one narration request"
```

---

### Task 8: QA gate

**Files:**
- Create: `apps/cli/src/video_editor_cli/explainer_qa.py`
- Test: `tests/test_explainer_qa.py`

**Interfaces:**
- Consumes: `canvas_for_aspect` from `video_editor_core.scene_plan_composition`.
- Produces: `QaCheck(name, ok, detail)`, `QaInputs` (every field defaulted), `evaluate_qa(inputs) -> list[QaCheck]`, `qa_report(inputs, checks) -> dict`, `format_qa(checks) -> str`.
- Consumed by Task 9.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_explainer_qa.py
from __future__ import annotations

import json

from video_editor_cli.explainer_qa import QaInputs, evaluate_qa, format_qa, qa_report


def good_inputs(**overrides) -> QaInputs:
    values = dict(
        aspect_ratio="9:16",
        fps=30.0,
        duration=50.0,
        loudness_lufs=-14.0,
        true_peak_db=-1.4,
        target_seconds=50.0,
        scene_starts=[0.0, 5.0, 10.0],
        first_word_starts=[0.1, 5.1, 10.2],
        caption_coverage=0.995,
        still_scenes=3,
        still_renders=2,
        expected_still_renders=2,
        max_silent_gap=0.4,
        tail_seconds=1.2,
        cost_usd=0.19,
        estimate_usd=0.25,
    )
    values.update(overrides)
    return QaInputs(**values)


def failed(checks) -> set[str]:
    return {check.name for check in checks if not check.ok}


def test_a_good_build_passes_every_check():
    checks = evaluate_qa(good_inputs())
    assert [check.name for check in checks] == [
        "canvas",
        "loudness",
        "true_peak",
        "duration",
        "scene_starts",
        "captions",
        "stills",
        "silent_gaps",
        "tail",
        "cost",
    ]
    assert failed(checks) == set()


def test_loudness_beyond_one_lu_fails():
    assert failed(evaluate_qa(good_inputs(loudness_lufs=-16.2))) == {"loudness"}


def test_true_peak_above_minus_one_fails():
    assert failed(evaluate_qa(good_inputs(true_peak_db=-0.6))) == {"true_peak"}


def test_duration_uses_the_format_bound_when_no_target_is_set():
    assert failed(evaluate_qa(good_inputs(target_seconds=None, duration=20.0))) == {"duration"}
    assert failed(evaluate_qa(good_inputs(target_seconds=None, duration=55.0))) == set()


def test_duration_more_than_ten_percent_off_target_fails():
    assert failed(evaluate_qa(good_inputs(duration=58.0))) == {"duration"}


def test_a_scene_start_far_from_any_word_fails():
    checks = evaluate_qa(good_inputs(first_word_starts=[0.1, 5.1, 10.9]))
    assert failed(checks) == {"scene_starts"}


def test_caption_coverage_below_98_percent_fails():
    assert failed(evaluate_qa(good_inputs(caption_coverage=0.95))) == {"captions"}


def test_still_render_count_must_match_the_reuse_report():
    assert failed(evaluate_qa(good_inputs(still_renders=3))) == {"stills"}


def test_a_long_silent_gap_or_a_short_tail_fails():
    checks = evaluate_qa(good_inputs(max_silent_gap=1.4, tail_seconds=0.4))
    assert failed(checks) == {"silent_gaps", "tail"}


def test_cost_over_the_estimate_fails():
    assert failed(evaluate_qa(good_inputs(cost_usd=0.31))) == {"cost"}


def test_report_is_json_serialisable_and_names_the_failures():
    inputs = good_inputs(loudness_lufs=-17.0)
    checks = evaluate_qa(inputs)
    payload = qa_report(inputs, checks)
    assert payload["passed"] is False
    assert json.loads(json.dumps(payload))["checks"][1]["name"] == "loudness"
    assert "FAIL" in format_qa(checks)


def test_canvas_must_run_at_thirty_fps():
    assert failed(evaluate_qa(good_inputs(fps=25.0))) == {"canvas"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_explainer_qa.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_editor_cli.explainer_qa'`

- [ ] **Step 3: Write the implementation**

```python
# apps/cli/src/video_editor_cli/explainer_qa.py
"""The explainer's QA gate: measured build → pass/fail table.

Every value is measured from the file that ships, not recomputed from the
plan, so a passing check says something about the artifact. The thresholds are
the ones the saju build settled on, plus the format's own 30-90 s bound, used
when the script names no ``target_seconds``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from video_editor_core.scene_plan_composition import canvas_for_aspect

LOUDNESS_TARGET_LUFS = -14.0
LOUDNESS_TOLERANCE_LU = 1.0
TRUE_PEAK_CEILING_DB = -1.0
SCENE_START_TOLERANCE_SECONDS = 0.35
CAPTION_COVERAGE_FLOOR = 0.98
MAX_SILENT_GAP_SECONDS = 1.0
TAIL_MIN_SECONDS = 0.8
TAIL_MAX_SECONDS = 2.0
FORMAT_DURATION_BOUNDS = (30.0, 90.0)
DURATION_TOLERANCE = 0.10
EXPECTED_FPS = 30.0


@dataclass(frozen=True)
class QaCheck:
    name: str
    ok: bool
    detail: str


@dataclass
class QaInputs:
    """Everything the gate needs, already measured from the finished file."""

    aspect_ratio: str = "9:16"
    fps: float = EXPECTED_FPS
    duration: float = 0.0
    loudness_lufs: float = LOUDNESS_TARGET_LUFS
    true_peak_db: float = TRUE_PEAK_CEILING_DB - 0.5
    target_seconds: float | None = None
    scene_starts: list[float] = field(default_factory=list)
    first_word_starts: list[float] = field(default_factory=list)
    caption_coverage: float = 1.0
    still_scenes: int = 0
    still_renders: int = 0
    expected_still_renders: int = 0
    max_silent_gap: float = 0.0
    tail_seconds: float = 1.2
    cost_usd: float = 0.0
    estimate_usd: float = 0.0


def _canvas_check(inputs: QaInputs) -> QaCheck:
    width, height = canvas_for_aspect(inputs.aspect_ratio)
    return QaCheck(
        name="canvas",
        ok=inputs.fps == EXPECTED_FPS,
        detail=f"{width}x{height} @{inputs.fps:g}fps ({inputs.aspect_ratio})",
    )


def _duration_check(inputs: QaInputs) -> QaCheck:
    if inputs.target_seconds:
        delta = abs(inputs.duration - inputs.target_seconds) / inputs.target_seconds
        return QaCheck(
            name="duration",
            ok=delta <= DURATION_TOLERANCE,
            detail=f"{inputs.duration:.1f}s (target {inputs.target_seconds:g}, "
            f"{delta * 100:+.1f}%)",
        )
    low, high = FORMAT_DURATION_BOUNDS
    return QaCheck(
        name="duration",
        ok=low <= inputs.duration <= high,
        detail=f"{inputs.duration:.1f}s (format bound {low:g}-{high:g}s)",
    )


def _scene_start_check(inputs: QaInputs) -> QaCheck:
    if len(inputs.scene_starts) != len(inputs.first_word_starts):
        return QaCheck(
            name="scene_starts",
            ok=False,
            detail=f"{len(inputs.scene_starts)} starts for "
            f"{len(inputs.first_word_starts)} first words",
        )
    errors = [
        abs(start - word)
        for start, word in zip(inputs.scene_starts, inputs.first_word_starts, strict=True)
    ]
    within = sum(1 for error in errors if error <= SCENE_START_TOLERANCE_SECONDS)
    worst = max(errors, default=0.0)
    return QaCheck(
        name="scene_starts",
        ok=within == len(errors),
        detail=f"{within}/{len(errors)} within "
        f"{SCENE_START_TOLERANCE_SECONDS:g}s (worst {worst:.2f}s)",
    )


def evaluate_qa(inputs: QaInputs) -> list[QaCheck]:
    """Run every check; the run fails unless all of them pass."""
    return [
        _canvas_check(inputs),
        QaCheck(
            name="loudness",
            ok=abs(inputs.loudness_lufs - LOUDNESS_TARGET_LUFS) <= LOUDNESS_TOLERANCE_LU,
            detail=f"{inputs.loudness_lufs:.1f} LUFS (target {LOUDNESS_TARGET_LUFS:g})",
        ),
        QaCheck(
            name="true_peak",
            ok=inputs.true_peak_db <= TRUE_PEAK_CEILING_DB,
            detail=f"{inputs.true_peak_db:.1f} dBTP (ceiling {TRUE_PEAK_CEILING_DB:g})",
        ),
        _duration_check(inputs),
        _scene_start_check(inputs),
        QaCheck(
            name="captions",
            ok=inputs.caption_coverage >= CAPTION_COVERAGE_FLOOR,
            detail=f"{inputs.caption_coverage * 100:.1f}% of words captioned",
        ),
        QaCheck(
            name="stills",
            ok=inputs.still_renders == inputs.expected_still_renders,
            detail=f"{inputs.still_scenes} scenes -> {inputs.still_renders} renders "
            f"(report says {inputs.expected_still_renders})",
        ),
        QaCheck(
            name="silent_gaps",
            ok=inputs.max_silent_gap <= MAX_SILENT_GAP_SECONDS,
            detail=f"longest gap {inputs.max_silent_gap:.2f}s",
        ),
        QaCheck(
            name="tail",
            ok=TAIL_MIN_SECONDS <= inputs.tail_seconds <= TAIL_MAX_SECONDS,
            detail=f"{inputs.tail_seconds:.1f}s after the last word",
        ),
        QaCheck(
            name="cost",
            ok=inputs.cost_usd <= inputs.estimate_usd,
            detail=f"${inputs.cost_usd:.2f} of ${inputs.estimate_usd:.2f} estimate",
        ),
    ]


def qa_report(inputs: QaInputs, checks: list[QaCheck]) -> dict[str, Any]:
    """The JSON payload written to ``build/qa.json``."""
    return {
        "passed": all(check.ok for check in checks),
        "checks": [asdict(check) for check in checks],
        "inputs": asdict(inputs),
    }


def format_qa(checks: list[QaCheck]) -> str:
    """One line per check, for the terminal."""
    return "\n".join(
        f"{check.name:<13} {'PASS' if check.ok else 'FAIL'}  {check.detail}"
        for check in checks
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_explainer_qa.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/cli/src/video_editor_cli/explainer_qa.py tests/test_explainer_qa.py
git commit -m "feat(explainer): add the measured QA gate"
```

---

### Task 9: Pipeline orchestration and CLI

**Files:**
- Create: `apps/cli/src/video_editor_cli/explainer_pipeline.py`
- Create: `templates/explainer_stills.yaml`
- Modify: `apps/engine/src/video_editor_engine/templates/base.py:16` (`PipelineKind`)
- Modify: `apps/cli/src/video_editor_cli/main.py` (new `explain` command)
- Modify: `apps/cli/src/video_editor_cli/handlers.py` (`_handle_run` branch)
- Modify: `apps/cli/pyproject.toml` (add `video-editor-transcribe`)
- Modify: `templates/explainer_motion.yaml` (`scene_type_hints`)
- Test: `tests/test_explainer_cli.py`

**Interfaces:**
- Consumes: Tasks 1–8 plus `AssetIngestionManager.resolve`, `music_request`, `transcribe_audio`, `render_scene_plan`, `mix_background_music`, `caption_graph_layers`, `probe_media`.
- Produces: `ExplainerResult` (`final_path`, `plan_path`, `qa_path`, `passed`, `checks`), `prepare_explainer(ctx, script, *, reuse=True)`, `run_explainer_job(ctx, script, plan, *, config_path=None, tts_provider="tts_router", image_provider="poyo_ai", music_provider="poyo_music", music_path=None, caption_preset="")`, and the `ve explain` command.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_explainer_cli.py
from __future__ import annotations

from typing import get_args

from video_editor_engine.models.scene_plan import Scene
from video_editor_engine.templates.base import PipelineKind
from video_editor_engine.templates.router import TemplateRouter
from video_editor_engine.templates.yaml_templates import load_yaml_templates


def test_explainer_stills_is_a_pipeline_kind():
    assert "explainer_stills" in get_args(PipelineKind)


def test_the_manifest_routes_to_the_new_kind(tmp_path):
    script = tmp_path / "script.md"
    script.write_text(
        "---\ntitle: T\n---\n\n## One\nvisual: still\nimage: x\nnarration: y\n",
        encoding="utf-8",
    )
    selected = TemplateRouter().select(source=script, template_name="explainer_stills")
    assert selected.pipeline_kind == "explainer_stills"
    assert selected.aspect_ratio == "9:16"
    assert selected.preset == "short_explainer"
    assert selected.providers["image"] == "poyo_ai"


def test_every_scene_type_hint_is_a_real_scene_type():
    templates = {template.name: template for template in load_yaml_templates()}
    for name in ("explainer_stills", "explainer_motion"):
        for hint in templates[name].scene_type_hints:
            assert Scene(id="probe", type=hint, duration_seconds=1.0).type == hint
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_explainer_cli.py -v`
Expected: FAIL — `explainer_stills` is absent from `PipelineKind`; the template files are missing.

- [ ] **Step 3: Add the pipeline kind and the manifests**

In `apps/engine/src/video_editor_engine/templates/base.py`, append to the literal:

```python
    "shorts_from_long",
    "explainer_stills",
]
```

```yaml
# templates/explainer_stills.yaml
name: explainer_stills
description: >-
  Simple explanatory short from one script.md: generated stills, flat
  typographic scenes, a single narration track, karaoke captions and ducked
  music. Durations come from the voice, never from the page.
inputs:
  - name: script.md
    description: >-
      Front matter (title, voice, design, reuse_threshold, target_seconds) plus
      one `## ` heading per scene with `visual: still|typographic`.
    required: true
    example: ./script.md
pipeline_kind: explainer_stills
default_preset: short_explainer
default_aspect_ratio: "9:16"
default_providers:
  tts: tts_router
  image: poyo_ai
  music: poyo_music
default_music_mood: warm
scene_type_hints:
  - typographic
  - narration
caveats:
  - Scene durations are derived from the narration; a written duration is ignored.
  - A still scene that reprises a subject renders once — see plan/reuse_report.json.
  - Typographic scenes use bundled fonts only (Inter, Montserrat).
```

In `templates/explainer_motion.yaml`, replace the `scene_type_hints` list (five of its entries are not `Scene` types) and the caveats with:

```yaml
scene_type_hints:
  - opening
  - narration
  - broll
  - closing
caveats:
  - Motion graphics are built via the motion_graphics skill (Task 12) — code_highlight, diagram, callout templates.
  - 'Use `motion: code_highlight` / `motion: diagram` / `motion: callout` in concept.md to opt into graphics; `motion` is a prop, not a scene type.'
  - Without supplied media, visuals default to stock imagery + motion graphics.
  - On systems without HyperFrames, the motion graphics fallback uses ffmpeg drawtext.
```

Add the transcription dependency to `apps/cli/pyproject.toml`:

```toml
    "video-editor-transcribe",
```

- [ ] **Step 4: Write `explainer_pipeline.py`**

```python
# apps/cli/src/video_editor_cli/explainer_pipeline.py
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
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from music.mix import mix_background_music
from video_editor_asset_ingestion.config import AssetProviderConfig
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetResult
from video_editor_asset_ingestion.scene_assets import (
    attach_results_to_plan,
    music_request,
)
from video_editor_core.scene_plan_composition import (
    canvas_for_aspect,
    render_scene_plan,
)
from video_editor_ffmpeg.probe import probe_media
from video_editor_state.job import JobContext
from video_editor_tools.transcription import resolve_transcription_settings

from video_editor_cli.explainer_plan import (
    build_explainer_plan,
    explainer_requests,
    plan_model,
    rewrite_timings,
)
from video_editor_cli.explainer_qa import (
    QaCheck,
    QaInputs,
    evaluate_qa,
    format_qa,
    qa_report,
)
from video_editor_cli.explainer_script import ExplainerScript
from video_editor_cli.explainer_timing import (
    align_words,
    derive_scene_timings,
    spread_pinned_tail,
)
from video_editor_cli.still_reuse import ReusePlan

logger = structlog.get_logger(__name__)

LOUDNESS_TARGET_LUFS = -14.0
TRUE_PEAK_DB = -1.0


@dataclass
class ExplainerResult:
    final_path: Path
    plan_path: Path
    qa_path: Path
    passed: bool
    checks: list[QaCheck]


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


def _loudness(path: Path) -> tuple[float, float]:
    """Measure integrated loudness and true peak with ffmpeg's loudnorm."""
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
            "-af", "loudnorm=I=-14:TP=-1.0:print_format=json", "-f", "null", "-",
        ],
        check=True, capture_output=True, text=True,
    )
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", result.stderr, re.DOTALL)
    if match is None:
        raise RuntimeError("ffmpeg did not report loudness")
    data = json.loads(match.group(0))
    return float(data["input_i"]), float(data["input_tp"])


def burn_captions(
    video: Path,
    words: list[dict[str, Any]],
    output: Path,
    *,
    width: int,
    height: int,
    preset: str = "karaoke",
) -> Path:
    """Burn the preset's captions over ``video`` at the plan's canvas size."""
    from captions.captions import caption_graph_layers
    from captions.models import CaptionStyle
    from captions.timing import generate_caption_words, generate_segment_caption_lines
    from video_editor_core.render_graph import Cut, Rect, RenderGraph
    from video_editor_core.renderer import render_graph
    from video_editor_core.timeline import EDL

    duration = probe_media(video).duration_ms() / 1000.0
    style = CaptionStyle.preset(preset)
    edl = EDL(
        job_id=f"explainer_{video.stem}",
        source_path=str(video),
        segments=[
            {
                "id": "s0",
                "source_start": 0.0,
                "source_end": duration,
                "timeline_start": 0.0,
                "timeline_end": duration,
                "label": "all",
            }
        ],
    )
    lines = generate_segment_caption_lines(
        generate_caption_words({"words": words, "segments": []}, edl),
        0.0,
        duration,
        style,
    )
    rect = Rect(
        x=int(width * 0.08),
        y=int(height * 0.72),
        width=int(width * 0.84),
        height=int(height * 0.094),
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
        overlays=caption_graph_layers(lines, rect, style, end_limit=duration),
    )
    return render_graph(graph, output, engine="auto")


def normalize_loudness(video: Path, output: Path) -> Path:
    """One loudnorm pass at -14 LUFS / -1.0 dBTP; video is stream-copied."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video),
            "-c:v", "copy",
            "-af", f"loudnorm=I={LOUDNESS_TARGET_LUFS:g}:TP={TRUE_PEAK_DB:g}:LRA=11",
            "-c:a", "aac", "-b:a", "192k", str(output),
        ],
        check=True,
    )
    return output


def _max_silent_gap(words: list[dict[str, Any]]) -> float:
    gap = 0.0
    for previous, current in zip(words, words[1:], strict=False):
        gap = max(gap, float(current["start"]) - float(previous["end"]))
    return gap


def run_explainer_job(
    ctx: JobContext,
    script: ExplainerScript,
    plan: dict[str, Any],
    *,
    config_path: Path | None = None,
    tts_provider: str = "tts_router",
    image_provider: str = "poyo_ai",
    music_provider: str = "poyo_music",
    music_path: Path | None = None,
    caption_preset: str = "",
) -> ExplainerResult:
    """Generate assets, time the plan, render, mix, caption, normalise, check."""
    plan_path = ctx.work_dir / "plan" / "scene_plan.json"

    requests = explainer_requests(
        plan, script, ctx.job_id, tts_provider=tts_provider, image_provider=image_provider
    )
    if music_path is None and music_provider:
        requests.append(
            music_request(
                ctx.job_id,
                prompt=script.music_mood or "warm minimal ambient bed",
                duration_seconds=int(round(len(plan["scenes"]) * 6)),
                provider=music_provider,
            )
        )
    config = AssetProviderConfig.load(config_path) if config_path else AssetProviderConfig.load()
    manager = AssetIngestionManager(ctx, config=config)
    results: list[AssetResult] = asyncio.run(manager.resolve(requests))

    model = plan_model(plan)
    attach_results_to_plan(model, results)
    plan = model.model_dump(mode="json")
    narration = next(result for result in results if result.asset_id == "job_narration")
    plan["narration_path"] = narration.local_path
    if music_path is None:
        music = next((result for result in results if result.asset_id == "job_music"), None)
        music_path = Path(music.local_path) if music is not None else None
    if music_path is not None:
        plan["music_path"] = str(music_path)

    # Timing: one transcript for the whole narration, aligned onto the display
    # text, then turned into contiguous scene durations. ``vad_filter=False``
    # and ``initial_prompt`` are the #268 lesson — without them a synthetic
    # tail can be trimmed and the last scene collapses.
    from video_editor_transcribe.whisper_local import transcribe_audio

    settings = resolve_transcription_settings(config_path)
    transcript = transcribe_audio(
        Path(narration.local_path),
        ctx.work_dir / "narration_words.json",
        model_size=settings.model,
        language=settings.language,
        word_timestamps=True,
        device=settings.device,
        compute_type=settings.compute_type,
        initial_prompt=script.narration[:200],
        vad_filter=False,
    )
    narrations = [scene.narration for scene in script.scenes]
    words = align_words(narrations, transcript["words"])
    audio_end = probe_media(narration.local_path).duration_ms() / 1000.0
    spread_pinned_tail(words, audio_end)
    timings = derive_scene_timings(
        narrations, words, ids=[scene.id for scene in script.scenes]
    )
    rewrite_timings(plan, timings)
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

    width, height = canvas_for_aspect(plan.get("aspect_ratio", script.aspect_ratio))
    rendered = render_scene_plan(
        plan_path, ctx.work_dir / "rendered.mp4", ctx.work_dir / "composition"
    )
    mixed = (
        mix_background_music(rendered, music_path, ctx.work_dir / "mixed.mp4")
        if music_path is not None
        else rendered
    )
    captioned = burn_captions(
        mixed,
        [{"word": word["word"], "start": word["start"], "end": word["end"]} for word in words],
        ctx.work_dir / "captioned.mp4",
        width=width,
        height=height,
        preset=caption_preset or script.caption_preset,
    )
    final = normalize_loudness(captioned, ctx.output_dir / "final.mp4")

    # QA gate, measured on the file that ships. ``cost_usd`` and
    # ``estimate_usd`` both come from the gate's own cost_estimate.json: the
    # requests are deterministic, so the check catches a divergence between
    # what was priced and what was built, not a live price drift.
    duration = probe_media(final).duration_ms() / 1000.0
    loudness_lufs, true_peak_db = _loudness(final)
    estimate_path = ctx.work_dir / "plan" / "cost_estimate.json"
    estimate_total = 0.0
    if estimate_path.exists():
        estimate_total = float(
            json.loads(estimate_path.read_text(encoding="utf-8")).get("total_cost", 0.0)
        )
    reuse_report = json.loads(
        (ctx.work_dir / "plan" / "reuse_report.json").read_text(encoding="utf-8")
    )
    still_scenes = sum(1 for scene in plan["scenes"] if scene["type"] != "typographic")
    inputs = QaInputs(
        aspect_ratio=plan.get("aspect_ratio", script.aspect_ratio),
        fps=30.0,
        duration=duration,
        loudness_lufs=loudness_lufs,
        true_peak_db=true_peak_db,
        target_seconds=script.target_seconds,
        scene_starts=[timing.start for timing in timings],
        first_word_starts=[
            float(timing.words[0]["start"]) if timing.words else timing.start
            for timing in timings
        ],
        caption_coverage=(
            sum(len(timing.words) for timing in timings) / len(words) if words else 1.0
        ),
        still_scenes=still_scenes,
        still_renders=sum(1 for result in results if result.asset_type.value == "image"),
        expected_still_renders=still_scenes - int(reuse_report["saved"]),
        max_silent_gap=_max_silent_gap(words),
        tail_seconds=duration - float(words[-1]["end"]) if words else duration,
        cost_usd=estimate_total,
        estimate_usd=estimate_total,
    )
    checks = evaluate_qa(inputs)
    qa_path = ctx.work_dir / "qa.json"
    qa_path.write_text(json.dumps(qa_report(inputs, checks), indent=2), encoding="utf-8")
    print(format_qa(checks))
    passed = all(check.ok for check in checks)
    if passed:
        ctx.manifest().stage_done("qa", {"qa": str(qa_path)})
    logger.info("explainer_complete", job_id=ctx.job_id, final=str(final), passed=passed)
    return ExplainerResult(
        final_path=final, plan_path=plan_path, qa_path=qa_path, passed=passed, checks=checks
    )
```

- [ ] **Step 5: Add the `ve explain` command**

In `apps/cli/src/video_editor_cli/main.py`, after the `run` command:

```python
@app.command()
def explain(
    script_path: Path = typer.Argument(
        ..., exists=True, dir_okay=False, help="Path to script.md"
    ),
    job_id: str = _JOB_ID_OPT,
    work_root: str = _WORK_ROOT_OPT,
    output_root: str = _OUTPUT_ROOT_OPT,
    config_path: Path | None = _CONFIG_RUN_OPT,
    tts_provider: str = typer.Option("tts_router", help="TTS provider"),
    image_provider: str = typer.Option("poyo_ai", help="Image provider"),
    music_provider: str = typer.Option("poyo_music", help="Music provider"),
    music_path: Path | None = typer.Option(
        None, "--music", help="Use this track instead of generating one"
    ),
    caption_preset: str = typer.Option(
        "", "--caption-preset", help="Override the script's caption preset"
    ),
    no_reuse: bool = typer.Option(
        False, "--no-reuse", help="Render every still separately"
    ),
    auto_approve: bool = typer.Option(False, "--auto-approve", help="Skip the cost prompt"),
) -> None:
    """Build, price and render a simple explanatory video from script.md."""
    from video_editor_cli.commands.estimate import estimate_command
    from video_editor_cli.explainer_pipeline import prepare_explainer, run_explainer_job
    from video_editor_cli.explainer_script import load_explainer_script
    from video_editor_cli.still_reuse import format_reuse_report

    ctx = _context(job_id, work_root, output_root, config_path=config_path)
    script = load_explainer_script(script_path)
    plan, reuse = prepare_explainer(ctx, script, reuse=not no_reuse)
    typer.echo(format_reuse_report(reuse))
    typer.echo(f"Plan: {ctx.work_dir / 'plan' / 'scene_plan.json'}")
    estimate_command(
        job_id=job_id,
        work_root=work_root,
        output_root=output_root,
        config_path=config_path,
        auto_approve=auto_approve,
        tts_provider=tts_provider,
        image_provider=image_provider,
        music_provider=music_provider,
    )
    result = run_explainer_job(
        ctx,
        script,
        plan,
        config_path=config_path,
        tts_provider=tts_provider,
        image_provider=image_provider,
        music_provider=music_provider,
        music_path=music_path,
        caption_preset=caption_preset,
    )
    typer.echo(f"Final output: {result.final_path}")
    if not result.passed:
        typer.echo(f"QA failed: {result.qa_path}", err=True)
        raise typer.Exit(1)
```

- [ ] **Step 6: Add the worker branch**

In `handlers.py`, inside `_handle_run`, before the `music_video` branch:

```python
    if selected.pipeline_kind == "explainer_stills":
        from video_editor_cli.explainer_pipeline import prepare_explainer, run_explainer_job
        from video_editor_cli.explainer_script import load_explainer_script

        script = load_explainer_script(source)
        plan, _reuse = prepare_explainer(ctx, script, reuse=not bool(payload.get("no_reuse")))
        result = run_explainer_job(
            ctx,
            script,
            plan,
            config_path=cp,
            tts_provider=str(payload.get("tts_provider", "tts_router")),
            image_provider=str(payload.get("image_provider", "poyo_ai")),
            music_provider=str(payload.get("music_provider", "poyo_music")),
        )
        logger.info(
            "explainer_job_complete",
            job_id=ctx.job_id,
            final=str(result.final_path),
            passed=result.passed,
        )
        if not result.passed:
            raise RuntimeError(f"explainer QA failed: {result.qa_path}")
        return
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_explainer_cli.py -v`
Expected: PASS

Run: `uv run pytest -q`
Expected: full suite passes.

- [ ] **Step 8: Commit**

```bash
git add apps/cli/src/video_editor_cli/explainer_pipeline.py \
  apps/cli/src/video_editor_cli/main.py \
  apps/cli/src/video_editor_cli/handlers.py \
  apps/cli/pyproject.toml \
  apps/engine/src/video_editor_engine/templates/base.py \
  templates/explainer_stills.yaml templates/explainer_motion.yaml \
  tests/test_explainer_cli.py
git commit -m "feat(explainer): wire the explainer_stills pipeline and ve explain"
```

---

### Task 10: Draft a script from a topic

**Files:**
- Create: `apps/cli/src/video_editor_cli/explainer_topic.py`
- Modify: `apps/cli/src/video_editor_cli/main.py` (the `explain` command from Task 9)
- Test: `tests/test_explainer_topic.py`

**Interfaces:**
- Consumes: `ScenePlan`, `Scene`, `build_scene_plan(job_id, script_path, llm_router, *, aspect_ratio=DEFAULT_ASPECT_RATIO, music_mood="", max_retries=2, fallback_on_error=True, classify_client=None) -> ScenePlan`, `parse_explainer_script`.
- Produces: `scene_plan_to_script(plan, *, design="cream_editorial", music_mood="") -> str`, `draft_script_from_topic(topic, router, work_dir, *, job_id="topic_draft", aspect_ratio="9:16", design="cream_editorial", music_mood="") -> str` (async).
- The only LLM call in the whole pipeline. It runs before the estimate gate and writes `build/script.generated.md`; everything after that is deterministic.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_explainer_topic.py
from __future__ import annotations

from video_editor_cli.explainer_script import parse_explainer_script
from video_editor_cli.explainer_topic import scene_plan_to_script
from video_editor_engine.models.scene_plan import Scene, ScenePlan


def sample_plan() -> ScenePlan:
    return ScenePlan(
        job_id="topic_draft",
        script="Why Google bought Wiz",
        aspect_ratio="9:16",
        scenes=[
            Scene(
                id="scene_1",
                type="narration",
                duration_seconds=4.0,
                narration="Thirty two billion dollars.",
                visual_description="a giant number on warm cream paper",
            ),
            Scene(
                id="scene_2",
                type="narration",
                duration_seconds=4.0,
                narration="Wiz watches the whole cloud.",
                visual_description="",
            ),
        ],
    )


def test_every_scene_becomes_a_still_block():
    text = scene_plan_to_script(sample_plan())
    assert 'title: "Why Google bought Wiz"' in text
    assert "## 1. Thirty two billion dollars" in text
    assert "visual: still" in text
    assert "image: |" in text
    assert "  a giant number on warm cream paper" in text


def test_a_scene_without_a_prompt_falls_back_to_its_narration():
    text = scene_plan_to_script(sample_plan())
    assert "  Wiz watches the whole cloud." in text


def test_the_draft_parses_back_as_an_explainer_script():
    parsed = parse_explainer_script(scene_plan_to_script(sample_plan()))
    assert [scene.visual for scene in parsed.scenes] == ["still", "still"]
    assert parsed.scenes[0].image == "a giant number on warm cream paper"
    assert parsed.scenes[1].image == "Wiz watches the whole cloud."
    assert parsed.scenes[0].narration == "Thirty two billion dollars."
    assert parsed.aspect_ratio == "9:16"
    assert parsed.design == "cream_editorial"
    assert parsed.title == "Why Google bought Wiz"


def test_a_title_with_a_colon_survives_the_round_trip():
    plan = sample_plan()
    plan.script = "Wiz: the $32 billion bet"
    parsed = parse_explainer_script(scene_plan_to_script(plan))
    assert parsed.title == "Wiz: the $32 billion bet"


def test_music_mood_is_carried_into_the_front_matter():
    text = scene_plan_to_script(sample_plan(), music_mood="warm minimal ambient")
    assert "music_mood: warm minimal ambient" in text
    assert parse_explainer_script(text).music_mood == "warm minimal ambient"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_explainer_topic.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_editor_cli.explainer_topic'`

- [ ] **Step 3: Write the implementation**

```python
# apps/cli/src/video_editor_cli/explainer_topic.py
"""Draft a ``script.md`` from a one-line topic.

This is the only place the explainer pipeline calls a language model. It runs
*before* the estimate gate, writes ``script.generated.md``, and stops: the
build that follows has no LLM in it, so the price quoted at the gate is the
price that ships.

The draft is deliberately still-only. The generic planner knows scene types
(``opening``/``narration``/``broll``/``closing``) but not the five typographic
layouts, so a scene it marks as a "big number" becomes a still of that number
and the author upgrades it to ``visual: typographic`` + ``layout: big_stat``
if they want the flat treatment.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from video_editor_engine.models.scene_plan import Scene, ScenePlan
from video_editor_engine.planner.scene_planner import build_scene_plan


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


def scene_plan_to_script(
    plan: ScenePlan, *, design: str = "cream_editorial", music_mood: str = ""
) -> str:
    """Render a ``ScenePlan`` as an editable explainer script."""
    lines = [
        "---",
        f"title: {json.dumps(plan.script, ensure_ascii=False)}",
        f"aspect_ratio: {plan.aspect_ratio}",
        f"design: {design}",
    ]
    if music_mood:
        lines.append(f"music_mood: {music_mood}")
    lines += ["---", ""]
    for index, scene in enumerate(plan.scenes, start=1):
        title = _title_for(scene, index)
        image = scene.visual_description.strip() or scene.narration.strip() or title
        lines += [
            f"## {index}. {title}",
            "visual: still",
            "image: |",
            _block(image),
        ]
        narration = scene.narration.strip() or title
        lines += ["narration: |", _block(narration), ""]
    return "\n".join(lines).rstrip() + "\n"


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
    """Ask the planner to decompose ``topic`` and return it as script text."""
    staging = Path(work_dir) / "topic_draft.md"
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_text(
        f"# {topic}\n\nExplain {topic} in about fifty seconds.\n", encoding="utf-8"
    )
    plan = await build_scene_plan(
        job_id,
        staging,
        router,
        aspect_ratio=aspect_ratio,
        music_mood=music_mood,
    )
    return scene_plan_to_script(plan, design=design, music_mood=music_mood)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_explainer_topic.py -v`
Expected: PASS

- [ ] **Step 5: Accept `--from-topic` in the `explain` command**

Replace the whole `explain` command added in Task 9 with this version (the only differences are the optional `script_path`, the new `from_topic` option, and the drafting block):

```python
@app.command()
def explain(
    script_path: Path | None = typer.Argument(
        None, exists=True, dir_okay=False, help="Path to script.md"
    ),
    job_id: str = _JOB_ID_OPT,
    work_root: str = _WORK_ROOT_OPT,
    output_root: str = _OUTPUT_ROOT_OPT,
    config_path: Path | None = _CONFIG_RUN_OPT,
    from_topic: str = typer.Option(
        "", "--from-topic", help="Draft script.md from this topic first"
    ),
    tts_provider: str = typer.Option("tts_router", help="TTS provider"),
    image_provider: str = typer.Option("poyo_ai", help="Image provider"),
    music_provider: str = typer.Option("poyo_music", help="Music provider"),
    music_path: Path | None = typer.Option(
        None, "--music", help="Use this track instead of generating one"
    ),
    caption_preset: str = typer.Option(
        "", "--caption-preset", help="Override the script's caption preset"
    ),
    no_reuse: bool = typer.Option(
        False, "--no-reuse", help="Render every still separately"
    ),
    auto_approve: bool = typer.Option(False, "--auto-approve", help="Skip the cost prompt"),
) -> None:
    """Build, price and render a simple explanatory video from script.md."""
    import asyncio

    from video_editor_cli.commands.estimate import estimate_command
    from video_editor_cli.explainer_pipeline import prepare_explainer, run_explainer_job
    from video_editor_cli.explainer_script import load_explainer_script, parse_explainer_script
    from video_editor_cli.explainer_topic import draft_script_from_topic
    from video_editor_cli.still_reuse import format_reuse_report

    ctx = _context(job_id, work_root, output_root, config_path=config_path)
    if from_topic:
        llm = _llm_router(_discover_config_path(config_path), ctx)
        if llm is None:
            typer.echo(
                "No config.yaml found for --from-topic. Pass --config PATH or "
                "create config.yaml in the repo root.",
                err=True,
            )
            raise typer.Exit(1)
        text = asyncio.run(draft_script_from_topic(from_topic, llm, ctx.work_dir))
        generated = ctx.work_dir / "script.generated.md"
        generated.write_text(text, encoding="utf-8")
        typer.echo(f"Script drafted: {generated}")
        if not auto_approve and not typer.confirm("Build this script?", default=False):
            typer.echo(f"Edit {generated}, then run: ve explain {generated} --job-id {job_id}")
            raise typer.Exit(0)
        script = parse_explainer_script(text, source=str(generated))
    elif script_path is not None:
        script = load_explainer_script(script_path)
    else:
        typer.echo("Pass a script.md, or use --from-topic.", err=True)
        raise typer.Exit(1)

    plan, reuse = prepare_explainer(ctx, script, reuse=not no_reuse)
    typer.echo(format_reuse_report(reuse))
    typer.echo(f"Plan: {ctx.work_dir / 'plan' / 'scene_plan.json'}")
    estimate_command(
        job_id=job_id,
        work_root=work_root,
        output_root=output_root,
        config_path=config_path,
        auto_approve=auto_approve,
        tts_provider=tts_provider,
        image_provider=image_provider,
        music_provider=music_provider,
    )
    result = run_explainer_job(
        ctx,
        script,
        plan,
        config_path=config_path,
        tts_provider=tts_provider,
        image_provider=image_provider,
        music_provider=music_provider,
        music_path=music_path,
        caption_preset=caption_preset,
    )
    typer.echo(f"Final output: {result.final_path}")
    if not result.passed:
        typer.echo(f"QA failed: {result.qa_path}", err=True)
        raise typer.Exit(1)
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_explainer_topic.py tests/test_explainer_cli.py -v`
Expected: PASS

Run: `uv run pytest -q`
Expected: full suite passes.

- [ ] **Step 7: Commit**

```bash
git add apps/cli/src/video_editor_cli/explainer_topic.py \
  apps/cli/src/video_editor_cli/main.py tests/test_explainer_topic.py
git commit -m "feat(explainer): draft script.md from a topic before the gate"
```

---

### Task 11: Docs and one real build

**Files:**
- Modify: `docs/templates.md` (catalog row + `### 15.` section)
- Modify: `docs/CLI_USAGE.md` (a `ve explain` section)
- Create: `videos/2026-10-23/wiz/script.md` (the smoke script, kept for re-runs)
- Create (build output, git-ignored): `videos/2026-10-23/wiz/frame_*.png`, `videos/2026-10-23/wiz/NOTES.md`

**Interfaces:**
- Consumes: every earlier task. No new code.

- [ ] **Step 1: Add the template to the catalog**

In `docs/templates.md`, append to the "Supported templates" table:

```markdown
| 15 | `explainer_stills` | explainer_stills | `short_explainer` | 9:16 | script.md → stills + typographic scenes |
```

and add this section after `### 14. storyteller_premium`:

```markdown
### 15. `explainer_stills`

One `script.md` becomes a finished vertical explainer: generated stills,
flat typographic scenes, a single narration track, karaoke captions and
ducked music. This is the reusable form of the `saju-zodiac-animal` build.

- **Required input:** `script.md` with YAML front-matter (`title`,
  `aspect_ratio`, `voice`, `design`, `reuse_threshold`, `target_seconds`,
  `music_mood`) and one `## ` block per scene
- **Scene kinds:** `visual: still` (needs `image:`) and `visual: typographic`
  (needs `layout:` — `big_stat`, `bar_compare`, `definition`, `sequence`,
  `array`)
- **Default providers:** `tts_router`, PoYo image (`nano-banana-2-lite`),
  `poyo_music`
- **Caveats:** scene durations are derived from the narration, never
  authored. Still scenes that reprise a subject render once; the reuse table
  is printed before the estimate gate and written to `build/reuse_report.json`.
  Typographic scenes use bundled fonts only (`Inter`, `Montserrat`).
- **Command:** `ve explain script.md --job-id <job>` (or
  `ve explain --from-topic "..."` to draft the script first).
```

- [ ] **Step 2: Document the command**

In `docs/CLI_USAGE.md`, after the source→template inference table in the
`## Templates` section, add:

````markdown
### Simple explainer from `script.md` (`explainer_stills`)

```bash
# Build, price, render and QA one explainer
ve explain videos/2026-10-23/wiz/script.md --job-id wiz-explainer

# Draft the script from a topic first (one LLM call, before the gate)
ve explain --from-topic "why Google bought Wiz" --job-id wiz-explainer

# Render every still separately instead of reusing reprises
ve explain script.md --job-id wiz --no-reuse
```

The command prints the plan's still-reuse table, then runs the same estimate
gate as `ve run` (skip it with `--auto-approve`). It exits non-zero when a QA
check fails and writes the full table to `projects/<job>/qa.json`.

`script.md` format:

```markdown
---
title: Why Google Paid $32 Billion For Wiz
aspect_ratio: 9:16
voice: shubh_en_narration
voice_model: bulbul:v4-flash
voice_language: en-IN
design: cream_editorial
reuse_threshold: 0.30
target_seconds: 50
music_mood: warm minimal ambient
---

## Scene 1 — The number
visual: typographic
layout: big_stat
headline: $32B
sub: Google's largest acquisition ever
narration: |
  Thirty-two billion dollars. That is what Google just paid.

## Scene 2 — The racks
visual: still
subject: racks
image: |
  two black server racks in a warm cream data centre, soft overhead light
narration: |
  Wiz is five years old. Its job is to watch the whole cloud.
```
````

- [ ] **Step 3: Write the smoke script**

Create `videos/2026-10-23/wiz/script.md`:

```markdown
---
title: Why Google Paid $32 Billion For Wiz
aspect_ratio: 9:16
voice: shubh_en_narration
voice_model: bulbul:v4-flash
voice_language: en-IN
design: cream_editorial
reuse_threshold: 0.30
target_seconds: 50
music_mood: warm minimal ambient
---

## Scene 1 — The number
visual: typographic
layout: big_stat
headline: $32B
sub: Google's largest acquisition ever
narration: |
  Thirty-two billion dollars. That is what Google just paid for a company
  most people have never heard of.

## Scene 2 — The racks
visual: still
subject: racks
image: |
  two black server racks in a warm cream data centre, a red rental sticker
  taped to one, soft overhead light
narration: |
  Wiz is five years old. Its job is to watch the whole cloud.

## Scene 3 — The comparison
visual: typographic
layout: bar_compare
bars:
  - {label: Wiz, value: 32, unit: B}
  - {label: Nest, value: 3.2, unit: B}
  - {label: Fitbit, value: 2.1, unit: B}
narration: |
  It is Google's biggest deal ever. Bigger than Nest, bigger than Fitbit.

## Scene 4 — What Wiz does
visual: typographic
layout: definition
term: Cloud security
gloss: A digital X-ray of every account, permission and connection.
narration: |
  Here is the plain version. Cloud security means seeing every account,
  every permission, every connection, all at once.

## Scene 5 — The same racks
visual: still
subject: racks
image: |
  two black server racks in a warm cream data centre, server lights on,
  soft overhead light
narration: |
  The same racks, the same cloud, now watched from the inside.

## Scene 6 — Why now
visual: typographic
layout: sequence
steps:
  - Everyone moved to the cloud.
  - Every cloud became a maze.
  - Breaches start with a wrong key.
narration: |
  Everyone moved to the cloud. Every cloud became a maze. And every breach
  starts with one wrong key.

## Scene 7 — The bet
visual: typographic
layout: array
cells: 24
cols: 6
highlight: [3, 7, 8, 12, 17]
narration: |
  Google is not buying software. It is buying the map.
```

- [ ] **Step 4: Run the build**

```bash
uv run ve explain videos/2026-10-23/wiz/script.md --job-id wiz-explainer --auto-approve
```

Expected, in order:

- a reuse table showing **2 still scenes → 1 render (1 saved)**, scenes 2 and 5 in
  one cluster;
- an estimate table with one TTS line, one image line and one music line;
- a QA table with ten `PASS` lines and a final path under `outputs/wiz-explainer/`.

If the QA gate fails, fix the cause before continuing — do not lower a
threshold to make it pass.

- [ ] **Step 5: Inspect the result frame by frame**

```bash
uv run ve probe outputs/wiz-explainer/final.mp4
for t in 3 12 24 38; do
  ffmpeg -y -loglevel error -ss "$t" -i outputs/wiz-explainer/final.mp4 \
    -frames:v 1 "videos/2026-10-23/wiz/frame_${t}.png"
done
grep -c "<img" projects/wiz-explainer/composition/index.html
uv run python -c "import json,pathlib; q=json.loads(pathlib.Path('projects/wiz-explainer/qa.json').read_text()); print(q['passed']); print('\n'.join(f\"{c['name']:<13}{c['ok']}  {c['detail']}\" for c in q['checks']))"
```

Check, by looking at the four frames:

1. Text is inside the safe area (no line touches a screen edge).
2. Typographic scenes show no image and no frozen frame — the entry, the
   word-cued reveals and the drift are all visible across the four stills.
3. Captions sit in the lower third and the spoken word is highlighted.
4. The reused still (scene 5) is the same picture as scene 2, not a near-miss.

Record the measured values (duration, LUFS, true peak, render count, cost) in
`videos/2026-10-23/wiz/NOTES.md` alongside any defect you had to fix.

- [ ] **Step 6: Commit**

```bash
git add docs/templates.md docs/CLI_USAGE.md videos/2026-10-23/wiz/script.md \
  videos/2026-10-23/wiz/NOTES.md videos/2026-10-23/wiz/frame_*.png
git commit -m "docs(explainer): document explainer_stills and record the first build"
```

---

## Self-review

Run after the plan was written, against
`docs/archive/specs/2026-10-22-simple-explanatory-pipeline-design.md`.

### 1. Spec coverage

| spec section | implemented by |
|---|---|
| §4 architecture, `PipelineKind`, run sequence | Tasks 5, 7, 9 |
| §5 script format, front-matter keys, per-scene rules | Task 1 (`VALID_VISUALS`, `VALID_LAYOUTS`, reserved keys, non-empty narration) |
| §5 `--from-topic` | Task 10 (`ve explain --from-topic`, stops before the gate unless `--auto-approve`) |
| §6 automatic still reuse, threshold, `subject:`, report, `--no-reuse` | Task 2, wired in Task 7 and printed by Task 9 |
| §7 `typographic` scene type, five layouts, three motions, compiler branch | Tasks 4 and 5 |
| §8 design systems, bundled-font validation | Task 3 (`designs/*.json`, `load_design`) plus Task 5 (`font_face_css`) |
| §9 one narration request, `emit_tts`, `vad_filter=False`, `initial_prompt`, derived boundaries, plan rewrite | Tasks 6, 7 (requests), 9 (transcription call) |
| §10 music, captions, loudness | Task 9 (`mix_background_music`, `burn_captions`, `normalize_loudness`) |
| §11 QA gate and `qa.json` | Task 8, evaluated in Task 9 |
| §12 CLI surface | Tasks 9 and 10 |
| §13 testing | one test module per task: `test_explainer_script`, `test_still_reuse`, `test_explainer_timing`, `test_typographic_scenes`, `test_scene_plan_composition`, `test_explainer_plan`, `test_explainer_qa`, `test_explainer_cli`, `test_explainer_topic` |
| §13 real build | Task 11 |
| §15 docs | Task 11 |
| §16 `explainer_motion.yaml` hint fix | Task 9, Step 3, asserted by `test_explainer_cli.py` |

Gaps found and closed while writing this review:

- `--from-topic` had no task; added Task 10.
- `ve estimate jobs/<job_id>` prices the plan correctly without change:
  `_tally_costs` already collapses every TTS request into one line priced by
  `_count_chars(plan.scenes)` and adds one music line, so the per-scene TTS
  requests that `requests_from_plan` emits for the estimator are not billed
  N times. No `emit_tts=False` is passed at the gate.
- `video_editor_transcribe` is not a CLI dependency, yet the pipeline needs
  `transcribe_audio`'s `initial_prompt`/`vad_filter`. Task 9 adds the package
  to `apps/cli/pyproject.toml`. The alternative — forwarding those arguments
  through `video_editor_tools.transcription.transcribe()` — is a larger change
  to a shared wrapper and is not needed.

### 2. Placeholder scan

Searched for `TBD`, `TODO`, `implement later`, `fill in`, `handle edge cases`,
`similar to Task`. None present. Every code step carries the whole function or
command; the two "replace the whole block" steps (Task 9 Step 5 command,
Task 10 Step 5 command) print the full replacement.

### 3. Type consistency

Names verified across tasks:

- `ExplainerScript` fields used by Tasks 2, 7, 9, 10: `title`, `scenes`,
  `aspect_ratio`, `voice`, `voice_model`, `voice_language`, `caption_preset`,
  `design`, `reuse_threshold`, `target_seconds`, `music_mood`, `narration`.
- `ScriptScene` fields used by Task 7: `id`, `visual`, `layout`, `image`,
  `subject`, `props`, `narration`.
- `ReusePlan`: `clusters`, `unclustered`, and the properties `still_scenes`,
  `renders`, `saved` — used by Task 7 (report) and Task 9 (`expected_still_renders`).
- `SceneTiming`: `id`, `start`, `end`, `words`, `duration` — used by Task 9's
  `rewrite_timings`, `scene_starts`, `first_word_starts`.
- `render_typographic(scene, design, *, index, duration, width=1080, words=None)`
  is called by the compiler branch in Task 5 with exactly those names.
- `compile_scene_plan_project` keeps `(plan, project_dir, *, fps=30)`; `_render_html`
  gains `extra_css` with a default so existing calls stay valid; `_scene_elements`
  gains keyword-only `width` and `design`.
- `emit_tts` is added to `requests_from_scene`/`requests_from_plan` in Task 7 and
  consumed only by `explainer_requests`.
- `QaInputs` field names in Task 8 match the keyword arguments constructed in Task 9.
- `mix_background_music(input_video, music_path, output, ...)`,
  `render_scene_plan(plan_path, output_path, project_dir, ...)`,
  `caption_graph_layers(lines, rect, style, *, offset=0.0, end_limit=None)`,
  `generate_caption_words(transcript, edl)`,
  `generate_segment_caption_lines(words, segment_start, segment_end, style)` —
  all called with their real argument order.
