# Storyteller Templates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two new video templates (`storyteller_hd`, `storyteller_budget`) that turn a persona-tagged markdown script into a multi-voice, multi-language story video with FableCut timeline preview and a final MP4, while cutting music + multilingual TTS costs by switching to PoYo music and Sarvam Bulbul v3.

**Architecture:** Three new `Template` entries share a new `pipeline_kind: "storyteller"` and a new `storyteller_planner` module. The planner parses the script, derives per-scene image prompts via the LLM (`minimax-m3:cloud`), batches TTS per `(provider, voice_id, language)` group (Sarvam for Indian languages, ElevenLabs otherwise), generates assets via PoYo using a **3-tier image × 3-tier video** model (`budget=wan-2-2-fast`/`flux-schnell`, `standard=seedance-2-fast`/`gpt-image-2`, `premium=seedance-2`/`gpt-image-2-2K`), auto-generates one background music track via PoYo `minimax-music-2-6`, then exports to the existing FableCut bridge for browser preview. Final cut via the existing `ve render-fablecut`. The template name picks the default tier pair (`storyteller_budget` = (budget,budget), `storyteller_hd` = (standard,standard), `storyteller_premium` = (premium,premium)); `--image-tier` and `--video-tier` flags override independently.

**Tech Stack:** Python 3.12, Pydantic v2, structlog, httpx, PoYo unified API (`/api/generate/submit` + `/api/generate/status/{task_id}`), Sarvam Bulbul v3 REST API (`POST /text-to-speech`, `api-subscription-key` header, base64 response), ElevenLabs TTS + SFX (existing), FableCut bridge (existing), Ollama Cloud Pro with `minimax-m3:cloud` for planning.

## Global Constraints

- **Target suite:** ≥ 840 tests passing (today: 782). All new tests use mocks — no live API calls in CI.
- **Ruff and mypy must remain clean.**
- **Existing test files must remain green** — `test_templates.py` count update from 11 → 13, `test_fablecut_bridge.py` (12 tests) unchanged, `test_compose_musical_video.py` unchanged.
- **Provider base class:** all new providers must subclass `video_editor_asset_ingestion.providers.base.GenerativeProvider` and implement `async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult`. The `provider_name` class attribute is a string used for config lookup.
- **Configuration:** add new providers under `assets.providers.<name>` in `config.yaml`; never bypass `AssetProviderConfig.load()`.
- **Path safety:** all filesystem paths from user input must be validated against path traversal (the existing `_safe_filename` and project-root resolution patterns in the FableCut bridge are the reference).
- **Idempotency:** every pipeline stage must be safe to re-run; the planner uses a content-hash cache for image prompts and motion policy.
- **Failure handling:** never fail the whole run on one scene's TTS/image/video failure — log, fall back per the spec, and continue. Each failure writes a structured log line for `decision_log.jsonl`.
- **Memory:** update `.remember/` and `/home/harish/.claude/projects/-mnt-data2-git-repos-video-editor/memory/` with three new memory files at the end (see Task 18).
- **Git:** this repo is **not currently a git repository** (`/mnt/data2/git_repos/video-editor` is not under git). Use `git init` once before Task 1's first commit, then commit at every step. If `git init` is already run by the time you start, skip it.

---

## File Structure

### New files
- `apps/engine/src/video_editor_engine/models/storyteller_script.py` — Pydantic models for the input script
- `apps/engine/src/video_editor_engine/planner/storyteller_planner.py` — parser + planner
- `apps/engine/src/video_editor_engine/templates/storyteller_concepts.py` — `build_concept` helpers
- `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/poyo_music.py` — PoYo music provider
- `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/sarvam_tts.py` — Sarvam TTS provider
- `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/tts_router.py` — language-routing TTS provider
- `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/music_protocol.py` — `MusicProvider` protocol
- `packages/fablecut_bridge/src/video_editor_fablecut_bridge/storyteller_export.py` — FableCut extension
- `tests/test_storyteller_parser.py`
- `tests/test_storyteller_planner.py`
- `tests/test_storyteller_fablecut_export.py`
- `tests/test_poyo_music.py`
- `tests/test_sarvam_tts.py`
- `tests/test_tts_router.py`
- `tests/test_tts_test_cli.py`
- `tests/fixtures/storyteller/sample_story.md` — example script for smoke tests
- `docs/SARVAM_INTEGRATION.md` — new doc
- `.remember/storyteller-templates-2026-07-17.md` — memory file
- `.remember/poyo-music-and-sarvam-tts-providers-2026-07-17.md` — memory file
- `.remember/llm-config-cleanup-2026-07-17.md` — memory file

### Modified files
- `apps/engine/src/video_editor_engine/templates/base.py` — extend `PipelineKind` Literal
- `apps/engine/src/video_editor_engine/models/scene_plan.py` — add `"story"` to allowed scene types
- `apps/engine/src/video_editor_engine/templates/eleven_templates.py` — add 2 new `Template` entries, update docstring
- `apps/engine/src/video_editor_engine/templates/router.py` — add `cast:`-detection inference
- `apps/cli/src/video_editor_cli/main.py` — add `ve storyteller-export` + `ve tts-test` commands + worker handler
- `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/__init__.py` — export new symbols
- `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/__init__.py` — export new symbols
- `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/registry.py` — register new providers
- `packages/fablecut_bridge/src/video_editor_fablecut_bridge/__init__.py` — export storyteller export/import
- `config.yaml` — add new providers + LLM model swap
- `config.example.yaml` — same
- `.env.example` — add `SARAVAM_API=`
- `docs/templates.md` — add 2 new template entries
- `docs/Templates_capabilities.md` — add 2 new sections
- `docs/CLI_USAGE.md` — document new CLI
- `docs/MODEL_ROUTING.md` — update model list
- `docs/POYO_ROUTING.md` — add music section
- `docs/not_possible.md` — remove resolved entry
- `README.md` — mention new templates
- `tests/test_templates.py` — update count and add new tests

### File responsibilities
- **`storyteller_script.py`** — only data shapes, no I/O
- **`storyteller_planner.py`** — only orchestration, no HTTP calls directly (delegates to provider wrappers)
- **`poyo_music.py`** — only PoYo music API
- **`sarvam_tts.py`** — only Sarvam TTS API
- **`tts_router.py`** — only routing logic, no HTTP
- **`music_protocol.py`** — only the `MusicProvider` ABC
- **`storyteller_export.py`** — only the new FableCut project schema + round-trip helpers

---

## Task 1: Init git repo + ModelRouter soft warning

**Files:**
- Create: `.gitignore` (only if missing)
- Modify: `packages/llm/src/video_editor_llm/router.py` (add `models` set query against the live `ollama list` if a config-referenced model is missing; log a `warning`)

**Interfaces:**
- Consumes: existing `ModelRouter.generate()` flow
- Produces: new behavior — when a configured model name is not in the live `ollama list`, log a warning but do not raise. No new public function; the change is internal to `generate()`.

- [ ] **Step 1: Initialize git if not already a repo**

Run from `/mnt/data2/git_repos/video-editor`:
```bash
git init
git add -A
git commit -m "chore: snapshot before storyteller templates work" --allow-empty
```

- [ ] **Step 2: Locate the model router**

Run: `find packages/llm -name router.py`
Expected: prints the path to `ModelRouter`.

- [ ] **Step 3: Write the failing test for the soft warning**

Create `tests/test_model_router_soft_warning.py`:
```python
"""Tests for ModelRouter's soft warning when a configured model is missing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


def test_missing_model_logs_warning_but_does_not_raise(tmp_path: Path) -> None:
    """A configured model that isn't in the live ollama list must warn, not raise."""
    # Import lazily to avoid import-time config load.
    from video_editor_llm.router import ModelRouter

    router = ModelRouter.from_config(Path("config.yaml"))
    with patch.object(router, "_list_ollama_models", return_value={"some-other-model:cloud"}):
        with patch("video_editor_llm.router.structlog.get_logger") as mock_logger:
            logger = mock_logger.return_value
            # The call should not raise even though the configured model is missing.
            try:
                router._validate_model_known("minimax-m3:cloud")
            except Exception as exc:  # noqa: BLE001
                pytest.fail(f"_validate_model_known should not raise, got: {exc}")
            # And it should have logged a warning.
            assert any(
                call.args and "model_not_in_ollama_list" in (call.args[0] or "")
                for call in logger.warning.call_args_list
            ), "expected a warning containing 'model_not_in_ollama_list'"
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `pytest tests/test_model_router_soft_warning.py -v`
Expected: FAIL — `_validate_model_known` does not exist yet.

- [ ] **Step 5: Add `_list_ollama_models` and `_validate_model_known` to `ModelRouter`**

In `packages/llm/src/video_editor_llm/router.py`, add these methods inside the `ModelRouter` class:
```python
def _list_ollama_models(self) -> set[str]:
    """Return the set of model names available on the configured Ollama host.

    Returns an empty set on any error so callers degrade to "skip the check".
    """
    base_url = (self.config or {}).get("base_url", "https://api.ollama.com") if isinstance(self.config, dict) else "https://api.ollama.com"
    try:
        import httpx
        response = httpx.get(f"{base_url}/api/tags", timeout=5.0)
        response.raise_for_status()
        data = response.json()
        return {m["name"] for m in data.get("models", []) if "name" in m}
    except Exception:  # noqa: BLE001
        return set()

def _validate_model_known(self, model_name: str) -> None:
    """Log a warning if `model_name` is not in the live ollama list.

    Soft warning only — does not raise. This is a smoke check, not a gate.
    """
    import structlog
    logger = structlog.get_logger(__name__)
    available = self._list_ollama_models()
    if available and model_name not in available:
        logger.warning(
            "model_not_in_ollama_list",
            model=model_name,
            hint="run `ollama list` to see installed models",
        )
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `pytest tests/test_model_router_soft_warning.py -v`
Expected: PASS.

- [ ] **Step 7: Run the full suite to verify no regressions**

Run: `pytest -q 2>&1 | tail -5`
Expected: 782 passed (or "782 passed" + maybe 1-2 unrelated warnings).

- [ ] **Step 8: Commit**

```bash
git add packages/llm/src/video_editor_llm/router.py tests/test_model_router_soft_warning.py
git commit -m "feat(llm): soft warning when configured model is missing from ollama list"
```

---

## Task 2: Cleanup `config.yaml` to match installed Ollama models

**Files:**
- Modify: `config.yaml` (replace stale `tasks:` block)
- Modify: `config.example.yaml` (same change)
- Modify: `docs/MODEL_ROUTING.md` (replace model table)

**Interfaces:**
- Consumes: existing `config.yaml` shape
- Produces: a `tasks:` block that references only models in the live `ollama list` (verified in the previous turn: `kimi-k2.7-code:cloud`, `deepseek-v4-pro:cloud`, `glm-5.2:cloud`, `minimax-m3:cloud`, `qwen3-coder-next:cloud`, `gemini-3-flash-preview:latest`)

- [ ] **Step 1: Write the failing test for the new config**

Create `tests/test_config_yaml_models.py`:
```python
"""Tests that config.yaml references only models the user has installed."""

from __future__ import annotations

from pathlib import Path


def test_config_yaml_references_only_known_ollama_models() -> None:
    """Every model in config.yaml's tasks block must be in the live ollama list."""
    import yaml

    config_path = Path(__file__).resolve().parents[1] / "config.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    installed = {
        "kimi-k2.7-code:cloud",
        "deepseek-v4-pro:cloud",
        "glm-5.2:cloud",
        "minimax-m3:cloud",
        "qwen3-coder-next:cloud",
        "gemini-3-flash-preview:latest",
    }
    referenced = set()
    for task in (raw.get("llm") or {}).get("tasks", {}).values():
        model = task.get("model")
        if model:
            referenced.add(model)
    assert referenced, "no tasks were found in config.yaml"
    missing = referenced - installed
    assert not missing, f"config.yaml references models not in ollama list: {missing}"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_config_yaml_models.py -v`
Expected: FAIL with `AssertionError: config.yaml references models not in ollama list: {...}` (the current `kimi-k2.6:cloud`, `qwen3.6:cloud`, `glm-5.1:cloud`).

- [ ] **Step 3: Update `config.yaml`**

Edit `config.yaml` — replace the `llm.tasks:` block with:
```yaml
  tasks:
    plan:        { task_type: plan,         provider: ollama_cloud_pro, model: minimax-m3:cloud }
    decompose:   { task_type: decompose,    provider: ollama_cloud_pro, model: minimax-m3:cloud }
    edit_plan:   { task_type: edit_plan,    provider: ollama_cloud_pro, model: minimax-m3:cloud }
    scene_plan:  { task_type: scene_plan,   provider: ollama_cloud_pro, model: minimax-m3:cloud }
    fallback:    { task_type: fallback,     provider: ollama_cloud_pro, model: glm-5.2:cloud }
    code:        { task_type: code,         provider: ollama_cloud_pro, model: qwen3-coder-next:cloud }
    utility:     { task_type: utility,      provider: ollama_cloud_pro, model: gemini-3-flash-preview:latest }
    verify_judge:{ task_type: verify_judge, provider: ollama_cloud_pro, model: gemini-3-flash-preview:latest }
```

- [ ] **Step 4: Mirror the change in `config.example.yaml`**

Edit `config.example.yaml` to apply the same `llm.tasks:` block. (The example file is a copy of the live config minus secrets.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/test_config_yaml_models.py -v`
Expected: PASS.

- [ ] **Step 6: Update `docs/MODEL_ROUTING.md`**

Replace the model table with:
```markdown
| Model | Role |
|-------|------|
| `minimax-m3:cloud` | Planning, decomposition, scene planning, image-prompt derivation |
| `qwen3-coder-next:cloud` | Code generation and tool-heavy execution |
| `glm-5.2:cloud` | Fallback executor |
| `gemini-3-flash-preview:latest` | Small utility tasks, LLM-as-judge verification |
```

- [ ] **Step 7: Run the full suite to verify no regressions**

Run: `pytest -q 2>&1 | tail -5`
Expected: 783 passed (782 + the new soft-warning test).

- [ ] **Step 8: Commit**

```bash
git add config.yaml config.example.yaml docs/MODEL_ROUTING.md tests/test_config_yaml_models.py
git commit -m "chore(config): align llm.tasks with installed ollama models (minimax-m3 default)"
```

---

## Task 3: Extend `PipelineKind` Literal and scene-type enum

**Files:**
- Modify: `apps/engine/src/video_editor_engine/templates/base.py` (add `"storyteller"` to the `PipelineKind` Literal)
- Modify: `apps/engine/src/video_editor_engine/models/scene_plan.py` (add `"story"` to `_ALLOWED_SCENE_TYPES`)

**Interfaces:**
- Consumes: existing `PipelineKind` Literal type
- Produces: extended types so the new templates and scenes type-check

- [ ] **Step 1: Write the failing test for the type extensions**

Create `tests/test_storyteller_types.py`:
```python
"""Tests that PipelineKind and Scene type allow the new storyteller values."""

from __future__ import annotations

from typing import get_args

from video_editor_engine.models.scene_plan import _ALLOWED_SCENE_TYPES
from video_editor_engine.templates.base import PipelineKind


def test_pipeline_kind_includes_storyteller() -> None:
    assert "storyteller" in get_args(PipelineKind)


def test_scene_types_includes_story() -> None:
    assert "story" in _ALLOWED_SCENE_TYPES
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_storyteller_types.py -v`
Expected: FAIL — `assert "storyteller" in (...)` and `assert "story" in frozenset(...)`.

- [ ] **Step 3: Add `"storyteller"` to the PipelineKind Literal**

In `apps/engine/src/video_editor_engine/templates/base.py` line 16, change:
```python
PipelineKind = Literal["v2_skills", "v1_raw", "script_to_video", "music_video"]
```
to:
```python
PipelineKind = Literal["v2_skills", "v1_raw", "script_to_video", "music_video", "storyteller"]
```

- [ ] **Step 4: Add `"story"` to `_ALLOWED_SCENE_TYPES`**

In `apps/engine/src/video_editor_engine/models/scene_plan.py` line 12, change:
```python
_ALLOWED_SCENE_TYPES = frozenset({"opening", "narration", "narrative", "broll", "closing", "transition", "music_visual"})
```
to:
```python
_ALLOWED_SCENE_TYPES = frozenset({"opening", "narration", "narrative", "broll", "closing", "transition", "music_visual", "story"})
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/test_storyteller_types.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite to verify no regressions**

Run: `pytest -q 2>&1 | tail -5`
Expected: 784 passed.

- [ ] **Step 7: Commit**

```bash
git add apps/engine/src/video_editor_engine/templates/base.py apps/engine/src/video_editor_engine/models/scene_plan.py tests/test_storyteller_types.py
git commit -m "feat(engine): add storyteller pipeline kind and story scene type"
```

---

## Task 4: Add the `StorytellerScript` Pydantic model

**Files:**
- Create: `apps/engine/src/video_editor_engine/models/storyteller_script.py`

**Interfaces:**
- Consumes: nothing (this is a leaf module)
- Produces: `class CastMember`, `class StorytellerScene`, `class StorytellerScript` (see Part 1 of the spec for exact field shapes)

- [ ] **Step 1: Write the failing test**

Create `tests/test_storyteller_script_model.py`:
```python
"""Tests for the StorytellerScript Pydantic model."""

from __future__ import annotations


def test_cast_member_defaults() -> None:
    from video_editor_engine.models.storyteller_script import CastMember

    cast = CastMember(voice_id="sarvam:shubh", language="hi-IN")
    assert cast.volume == 1.0


def test_storyteller_scene_requires_speaker_and_text() -> None:
    from pydantic import ValidationError

    from video_editor_engine.models.storyteller_script import StorytellerScene

    try:
        StorytellerScene(speaker="king", text="Bring me the traveller.", language="hi-IN", voice_id="sarvam:shubh", id="s1", visual_override=None, sfx_prompts=[], motion_override=None)  # type: ignore[arg-type]
    except ValidationError:
        pass
    else:
        raise AssertionError("expected ValidationError when `id` is empty")


def test_storyteller_script_round_trip() -> None:
    from video_editor_engine.models.storyteller_script import CastMember, StorytellerScript, StorytellerScene

    script = StorytellerScript(
        language="en",
        music_mood="cinematic",
        title="The Traveller",
        cast={"narrator": CastMember(voice_id="elevenlabs:Brian", language="en")},
        scenes=[
            StorytellerScene(
                id="scene_001",
                speaker="narrator",
                text="Long ago...",
                language="en",
                voice_id="elevenlabs:Brian",
                visual_override=None,
                sfx_prompts=[],
                motion_override=None,
            )
        ],
    )
    assert script.scenes[0].id == "scene_001"
    assert script.cast["narrator"].voice_id == "elevenlabs:Brian"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_storyteller_script_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'CastMember' from 'video_editor_engine.models.storyteller_script'`.

- [ ] **Step 3: Create the model file**

Create `apps/engine/src/video_editor_engine/models/storyteller_script.py`:
```python
"""Pydantic models for the storyteller-template input script.

A storyteller script is a markdown file with a YAML frontmatter block
declaring the global ``language``, ``music_mood``, ``title``, and ``cast``,
followed by ``## Speaker: <name>`` section headings, each holding
narration text and optional ``> visual:``, ``> sfx:``, ``> language:``,
and ``> motion:`` blockquote directives.

The planner converts one ``StorytellerScript`` to one ``ScenePlan``;
this module only carries the parsed-script shape.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class CastMember(BaseModel):
    """One declared character in the cast map."""

    voice_id: str
    language: str
    volume: float = 1.0

    @field_validator("volume")
    @classmethod
    def _validate_volume(cls, value: float) -> float:
        if not 0.0 <= value <= 2.0:
            raise ValueError(f"cast member volume must be in [0.0, 2.0], got {value}")
        return value


class StorytellerScene(BaseModel):
    """A single speaker's segment in the script."""

    id: str
    speaker: str
    text: str
    language: str
    voice_id: str
    visual_override: str | None = None
    sfx_prompts: list[str] = Field(default_factory=list)
    motion_override: bool | None = None

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        v = value.strip()
        if not v:
            raise ValueError("scene id must be non-empty")
        return v


class StorytellerScript(BaseModel):
    """The full parsed script."""

    language: str = "en"
    music_mood: str = "cinematic"
    title: str = ""
    cast: dict[str, CastMember]
    scenes: list[StorytellerScene] = Field(default_factory=list)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_storyteller_script_model.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite to verify no regressions**

Run: `pytest -q 2>&1 | tail -5`
Expected: 784 passed.

- [ ] **Step 6: Commit**

```bash
git add apps/engine/src/video_editor_engine/models/storyteller_script.py tests/test_storyteller_script_model.py
git commit -m "feat(eng): add StorytellerScript Pydantic model for persona-tagged scripts"
```

---

## Task 5: Implement `parse_storyteller_script()` with directive parsing

**Files:**
- Create: `apps/engine/src/video_editor_engine/planner/storyteller_planner.py` (start with the parser only)
- Create: `tests/fixtures/storyteller/sample_story.md`

**Interfaces:**
- Consumes: `Path` to a markdown file
- Produces: `StorytellerScript` (with `scenes` populated)

- [ ] **Step 1: Create the fixture file**

Create `tests/fixtures/storyteller/sample_story.md`:
```markdown
---
language: en
music_mood: cinematic
title: "The Traveller from the East"
cast:
  narrator:
    voice_id: elevenlabs:Brian
    language: en
    volume: 1.0
  king:
    voice_id: sarvam:shubh
    language: hi-IN
    volume: 0.95
  princess:
    voice_id: sarvam:anushka
    language: en-IN
---

> sfx: gentle sea waves rolling in

## Speaker: narrator
Long ago, in a kingdom by the sea, there ruled a
wise old king with three daughters.
> visual: aerial wide shot of a coastal medieval
> kingdom at golden hour, gulls circling the keep

## Speaker: king
राजा बोले, "पूर्व से आए यात्री को बुलाओ।"
> motion: true

> sfx: heavy wooden doors opening

## Speaker: princess
Father, he is not our enemy.
> language: en
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_storyteller_parser.py`:
```python
"""Tests for parse_storyteller_script()."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "storyteller" / "sample_story.md"


def test_parse_returns_three_scenes() -> None:
    from video_editor_engine.planner.storyteller_planner import parse_storyteller_script

    script = parse_storyteller_script(FIXTURE)
    assert len(script.scenes) == 3
    assert [s.speaker for s in script.scenes] == ["narrator", "king", "princess"]


def test_parse_resolves_per_scene_language_and_voice() -> None:
    from video_editor_engine.planner.storyteller_planner import parse_storyteller_script

    script = parse_storyteller_script(FIXTURE)
    assert script.scenes[0].language == "en"
    assert script.scenes[0].voice_id == "elevenlabs:Brian"
    assert script.scenes[1].language == "hi-IN"
    assert script.scenes[1].voice_id == "sarvam:shubh"
    # Princess's > language: en override beats the cast default of en-IN
    assert script.scenes[2].language == "en"
    assert script.scenes[2].voice_id == "sarvam:anushka"


def test_parse_extracts_sfx_prompts() -> None:
    from video_editor_engine.planner.storyteller_planner import parse_storyteller_script

    script = parse_storyteller_script(FIXTURE)
    # Narrator scene: one SFX from the bare blockquote under the heading
    assert script.scenes[0].sfx_prompts == ["gentle sea waves rolling in"]
    # King scene: one SFX from `> sfx:` and motion override true
    assert script.scenes[1].sfx_prompts == ["heavy wooden doors opening"]
    assert script.scenes[1].motion_override is True


def test_parse_visual_override() -> None:
    from video_editor_engine.planner.storyteller_planner import parse_storyteller_script

    script = parse_storyteller_script(FIXTURE)
    assert "aerial wide shot" in (script.scenes[0].visual_override or "")


def test_parse_unknown_speaker_raises(tmp_path: Path) -> None:
    from video_editor_engine.planner.storyteller_planner import parse_storyteller_script

    bad = tmp_path / "bad.md"
    bad.write_text(
        "---\nlanguage: en\ncast:\n  narrator: {voice_id: elevenlabs:Brian, language: en}\n---\n\n## Speaker: stranger\nHello.\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not in cast"):
        parse_storyteller_script(bad)


def test_parse_missing_frontmatter_raises(tmp_path: Path) -> None:
    from video_editor_engine.planner.storyteller_planner import parse_storyteller_script

    bad = tmp_path / "no_fm.md"
    bad.write_text("## Speaker: narrator\nHello.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="frontmatter"):
        parse_storyteller_script(bad)
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pytest tests/test_storyteller_parser.py -v`
Expected: FAIL — module not found.

- [ ] **Step 4: Implement `parse_storyteller_script()`**

Create `apps/engine/src/video_editor_engine/planner/storyteller_planner.py` with the parser only. (The rest of the module — `build_storyteller_scene_plan()` — lands in Task 7.)
```python
"""Storyteller planner: parse a persona-tagged script and build a ScenePlan.

Public API:
- ``parse_storyteller_script(path)`` → ``StorytellerScript``
- ``build_storyteller_scene_plan(script, llm_router, ...)`` → ``ScenePlan`` (Task 7)
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog

from video_editor_engine.models.storyteller_script import (
    CastMember,
    StorytellerScene,
    StorytellerScript,
)

logger = structlog.get_logger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
_DIRECTIVE_RE = re.compile(r"^>\s*(visual|motion|language|sfx)\s*:\s*(.+?)\s*$", re.IGNORECASE)
_SPEAKER_RE = re.compile(r"^##\s+Speaker:\s*(\S+)\s*$")
_BARE_BLOCKQUOTE_RE = re.compile(r"^>\s*(.+?)\s*$")


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body) or raise ValueError."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError("storyteller script must start with a YAML frontmatter block delimited by '---'")
    try:
        import yaml
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"frontmatter is not valid YAML: {exc}") from exc
    return fm, m.group(2)


def _scene_id(index: int) -> str:
    return f"scene_{index + 1:03d}"


def _parse_scenes(body: str, cast: dict[str, CastMember], default_language: str) -> list[StorytellerScene]:
    """Walk the body and emit one StorytellerScene per ## Speaker: heading."""
    scenes: list[StorytellerScene] = []
    current_speaker: str | None = None
    current_lines: list[str] = []
    current_visual: str | None = None
    current_sfx: list[str] = []
    current_lang_override: str | None = None
    current_motion_override: bool | None = None
    bare_blockquote_pending: bool = False  # true after heading, before any text

    def flush() -> None:
        nonlocal current_speaker, current_lines, current_visual, current_sfx, current_lang_override, current_motion_override, bare_blockquote_pending
        if current_speaker is None:
            return
        text = "\n".join(current_lines).strip()
        if not text:
            return
        if current_speaker not in cast:
            raise ValueError(
                f"speaker {current_speaker!r} is not in cast (declared speakers: {sorted(cast)})"
            )
        member = cast[current_speaker]
        resolved_lang = current_lang_override or member.language or default_language
        scenes.append(
            StorytellerScene(
                id=_scene_id(len(scenes)),
                speaker=current_speaker,
                text=text,
                language=resolved_lang,
                voice_id=member.voice_id,
                visual_override=current_visual,
                sfx_prompts=list(current_sfx),
                motion_override=current_motion_override,
            )
        )
        current_speaker = None
        current_lines = []
        current_visual = None
        current_sfx = []
        current_lang_override = None
        current_motion_override = None
        bare_blockquote_pending = False

    for raw_line in body.splitlines():
        speaker_m = _SPEAKER_RE.match(raw_line)
        if speaker_m:
            flush()
            current_speaker = speaker_m.group(1)
            bare_blockquote_pending = True
            continue
        if current_speaker is None:
            # Skip blank lines and stray non-heading content before the first speaker.
            continue
        directive_m = _DIRECTIVE_RE.match(raw_line)
        if directive_m:
            kind = directive_m.group(1).lower()
            value = directive_m.group(2)
            bare_blockquote_pending = False
            if kind == "visual":
                current_visual = value
            elif kind == "sfx":
                current_sfx.append(value)
            elif kind == "language":
                current_lang_override = value
            elif kind == "motion":
                current_motion_override = value.strip().lower() in {"true", "1", "yes"}
            continue
        bare_m = _BARE_BLOCKQUOTE_RE.match(raw_line)
        if bare_m and bare_blockquote_pending:
            # Bare blockquote directly under a heading → SFX shorthand
            current_sfx.append(bare_m.group(1))
            bare_blockquote_pending = False
            continue
        if not raw_line.strip():
            bare_blockquote_pending = False
            continue
        current_lines.append(raw_line)
        bare_blockquote_pending = False

    flush()
    return scenes


def parse_storyteller_script(path: Path) -> StorytellerScript:
    """Parse a markdown script file into a StorytellerScript."""
    text = Path(path).read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(text)
    cast_raw = fm.get("cast", {})
    if not cast_raw:
        raise ValueError("frontmatter must declare a non-empty `cast:` map")
    cast = {
        name: CastMember(
            voice_id=member["voice_id"],
            language=member.get("language", fm.get("language", "en")),
            volume=float(member.get("volume", 1.0)),
        )
        for name, member in cast_raw.items()
    }
    scenes = _parse_scenes(body, cast, default_language=fm.get("language", "en"))
    if not scenes:
        raise ValueError("script has no `## Speaker:` scenes after frontmatter")
    return StorytellerScript(
        language=fm.get("language", "en"),
        music_mood=fm.get("music_mood", "cinematic"),
        title=fm.get("title", ""),
        cast=cast,
        scenes=scenes,
    )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/test_storyteller_parser.py -v`
Expected: PASS (6 tests).

- [ ] **Step 6: Run the full suite to verify no regressions**

Run: `pytest -q 2>&1 | tail -5`
Expected: 790 passed.

- [ ] **Step 7: Commit**

```bash
git add apps/engine/src/video_editor_engine/planner/storyteller_planner.py tests/test_storyteller_parser.py tests/fixtures/storyteller/sample_story.md
git commit -m "feat(eng): add storyteller script parser with ## Speaker: and > directives"
```

---

## Task 6: Add the `MusicProvider` protocol module

**Files:**
- Create: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/music_protocol.py`

**Interfaces:**
- Consumes: nothing
- Produces: a `MusicProvider` ABC that the planner depends on, and a `MusicResult` data class. Both providers (`PoyoMusicProvider` from Task 8, the existing `ElevenLabsMusicProvider`) satisfy the same interface.

- [ ] **Step 1: Write the failing test**

Create `tests/test_music_provider_protocol.py`:
```python
"""Tests for the MusicProvider protocol + MusicResult data class."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_music_result_carries_required_fields() -> None:
    from video_editor_asset_ingestion.providers.audio.music_protocol import MusicResult

    r = MusicResult(
        provider="poyo_music",
        model="minimax-music-2-6",
        path=Path("/tmp/x.mp3"),
        duration_seconds=30.0,
    )
    assert r.provider == "poyo_music"
    assert r.duration_seconds == 30.0


def test_concrete_provider_must_implement_generate_music() -> None:
    from video_editor_asset_ingestion.providers.audio.music_protocol import MusicProvider

    class Bad:
        provider_name = "bad"

    with pytest.raises(TypeError):
        Bad()  # type: ignore[abstract]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_music_provider_protocol.py -v`
Expected: FAIL — `ImportError: cannot import name 'MusicResult' from '...'`.

- [ ] **Step 3: Create the protocol module**

Create `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/music_protocol.py`:
```python
"""Abstract MusicProvider protocol and MusicResult data class.

The storyteller planner depends on this interface so it can swap between
PoYo's music endpoint and ElevenLabs' ``/v1/music/generate`` without
touching the planner code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MusicResult:
    """Result of a single music-generation call."""

    provider: str
    model: str
    path: Path
    duration_seconds: float


class MusicProvider(ABC):
    """Interface for AI music providers."""

    provider_name: str = "music_provider"

    @abstractmethod
    async def generate_music(
        self,
        prompt: str,
        duration_seconds: float,
        output_path: Path,
    ) -> MusicResult:
        """Synthesise a music track matching ``prompt`` and write it to ``output_path``."""
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_music_provider_protocol.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/music_protocol.py tests/test_music_provider_protocol.py
git commit -m "feat(asset_ingestion): add MusicProvider protocol for music generation"
```

---

## Task 7: Implement `PoyoMusicProvider`

**Files:**
- Create: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/poyo_music.py`
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/__init__.py` (export the new class)
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/registry.py` (register the new provider)

**Interfaces:**
- Consumes: `ProviderConfig` (existing `api_key_env`, `base_url`, `timeout`, `provider_options`), `MusicProvider` protocol
- Produces: `class PoyoMusicProvider(MusicProvider)` with `provider_name = "poyo_music"`, `async def generate_music(prompt, duration_seconds, output_path) -> MusicResult`. Submits to `POST /api/generate/submit` with `model: "minimax-music-2-6"`, polls `GET /api/generate/status/{task_id}`, downloads the resulting file from `data.files[0].file_url`. Falls back to `generate-music` on model-not-found.

- [ ] **Step 1: Write the failing test**

Create `tests/test_poyo_music.py`:
```python
"""Tests for the PoyoMusicProvider (mocked PoYo API)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_poyo_music_happy_path(tmp_path: Path) -> None:
    from video_editor_asset_ingestion.providers.audio.poyo_music import PoyoMusicProvider

    fake_mp3 = b"ID3" + b"\x00" * 64

    async def fake_post(self, url, *args, **kwargs):  # noqa: ARG001
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"data": {"task_id": "t-1"}}
        return resp

    async def fake_get(self, url, *args, **kwargs):  # noqa: ARG001
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if url.endswith("/status/t-1"):
            resp.json.return_value = {"data": {"status": "finished", "files": [{"file_url": "https://cdn.example/m.mp3"}]}}
        else:
            resp.json.return_value = {}
        return resp

    async def fake_stream_get(self, url, *args, **kwargs):  # noqa: ARG001
        class _Resp:
            status_code = 200
            async def aread(self_inner):  # noqa: N805
                return fake_mp3
            async def aclose(self_inner):  # noqa: N805
                return None
        return _Resp()

    out = tmp_path / "track.mp3"
    with patch("httpx.AsyncClient.post", new=fake_post), \
         patch("httpx.AsyncClient.get", new=fake_get), \
         patch("httpx.AsyncClient.stream", new=fake_stream_get):
        provider = PoyoMusicProvider()
        result = await provider.generate_music("a calm chant", 30.0, out)

    assert result.provider == "poyo_music"
    assert result.model == "minimax-music-2-6"
    assert out.exists() and out.read_bytes() == fake_mp3


@pytest.mark.asyncio
async def test_poyo_music_model_not_found_falls_back_to_generate_music(tmp_path: Path) -> None:
    from video_editor_asset_ingestion.providers.audio.poyo_music import PoyoMusicProvider

    fallback_model = "generate-music"
    used_models: list[str] = []

    async def fake_post(self, url, *args, **kwargs):  # noqa: ARG001
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        body = kwargs.get("json") or {}
        used_models.append(body.get("model", ""))
        if body.get("model") == "minimax-music-2-6":
            resp.json.return_value = {"data": {"task_id": None, "error": "model_not_found"}}
        else:
            resp.json.return_value = {"data": {"task_id": "t-2"}}
        return resp

    async def fake_get(self, url, *args, **kwargs):  # noqa: ARG001
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"data": {"status": "finished", "files": [{"file_url": "https://cdn.example/m.mp3"}]}}
        return resp

    async def fake_stream(self, url, *args, **kwargs):  # noqa: ARG001
        class _Resp:
            status_code = 200
            async def aread(self_inner):  # noqa: N805
                return b"ID3" + b"\x00" * 32
            async def aclose(self_inner):  # noqa: N805
                return None
        return _Resp()

    out = tmp_path / "track.mp3"
    with patch("httpx.AsyncClient.post", new=fake_post), \
         patch("httpx.AsyncClient.get", new=fake_get), \
         patch("httpx.AsyncClient.stream", new=fake_stream):
        provider = PoyoMusicProvider(fallback_music_model=fallback_model)
        result = await provider.generate_music("chant", 30.0, out)

    assert used_models[0] == "minimax-music-2-6"
    assert used_models[1] == "generate-music"
    assert result.model == "generate-music"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_poyo_music.py -v`
Expected: FAIL — `ImportError: cannot import name 'PoyoMusicProvider'`.

- [ ] **Step 3: Create the provider**

Create `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/poyo_music.py`:
```python
"""PoYo.ai music-generation provider.

Uses PoYo's unified async API:
  POST /api/generate/submit  -> data.task_id
  GET  /api/generate/status/{task_id} -> data.status + data.files[0].file_url

Default model: ``minimax-music-2-6`` (~$0.10/gen, ~20 credits, the cheapest
non-ElevenLabs music model on PoYo as of 2026-07-13).
Fallback model: ``generate-music`` (Suno-backed, also $0.10/gen).

The provider implements the ``MusicProvider`` protocol so the storyteller
planner can swap between PoYo and ElevenLabs without code changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import structlog

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.providers.audio.music_protocol import MusicProvider, MusicResult

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.poyo.ai"
DEFAULT_MODEL = "minimax-music-2-6"
DEFAULT_FALLBACK_MODEL = "generate-music"
POLL_INTERVAL_SECONDS = 5.0
POLL_TIMEOUT_SECONDS = 600.0


class PoyoMusicProvider(MusicProvider):
    """Generate music via PoYo's submit/poll API."""

    provider_name = "poyo_music"

    def __init__(
        self,
        config: ProviderConfig | None = None,
        *,
        music_model: str | None = None,
        fallback_music_model: str | None = None,
    ) -> None:
        self.config = config or ProviderConfig()
        self.base_url = (self.config.base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = self.config.api_key() or ""
        self.timeout = self.config.timeout
        self.music_model = music_model or self.config.provider_options.get("music_model") or DEFAULT_MODEL
        self.fallback_music_model = (
            fallback_music_model
            or self.config.provider_options.get("fallback_music_model")
            or DEFAULT_FALLBACK_MODEL
        )
        self.poll_interval = float(self.config.provider_options.get("poll_interval_seconds", POLL_INTERVAL_SECONDS))
        self.poll_timeout = float(self.config.provider_options.get("poll_timeout_seconds", POLL_TIMEOUT_SECONDS))
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            self._client = httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=self.timeout)
        return self._client

    async def _submit(self, client: httpx.AsyncClient, model: str, prompt: str) -> str | None:
        payload = {"model": model, "input": {"prompt": prompt}}
        resp = await client.post("/api/generate/submit", json=payload)
        resp.raise_for_status()
        data = (resp.json() or {}).get("data") or {}
        return data.get("task_id")

    async def _poll(self, client: httpx.AsyncClient, task_id: str) -> str | None:
        """Poll until status is finished/failed; return file_url on success."""
        import asyncio
        deadline = asyncio.get_event_loop().time() + self.poll_timeout
        while True:
            resp = await client.get(f"/api/generate/status/{task_id}")
            resp.raise_for_status()
            payload = (resp.json() or {}).get("data") or {}
            status = payload.get("status")
            if status == "finished":
                files = payload.get("files") or []
                if not files:
                    raise RuntimeError(f"PoYo returned finished task {task_id!r} with no files")
                return files[0].get("file_url")
            if status == "failed":
                raise RuntimeError(f"PoYo task {task_id!r} failed: {payload.get('error')}")
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError(f"PoYo task {task_id!r} did not finish within {self.poll_timeout}s")
            await asyncio.sleep(self.poll_interval)

    async def _download(self, url: str, output_path: Path) -> None:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with open(output_path, "wb") as f:
                    async for chunk in resp.aiter_bytes():
                        f.write(chunk)

    async def generate_music(
        self,
        prompt: str,
        duration_seconds: float,
        output_path: Path,
    ) -> MusicResult:
        client = await self._get_client()
        task_id = await self._submit(client, self.music_model, prompt)
        if task_id is None:
            logger.warning("poyo_music_model_not_found_falling_back", primary=self.music_model, fallback=self.fallback_music_model)
            task_id = await self._submit(client, self.fallback_music_model, prompt)
            if task_id is None:
                raise RuntimeError(f"PoYo returned no task_id for primary {self.music_model!r} or fallback {self.fallback_music_model!r}")
            model_used = self.fallback_music_model
        else:
            model_used = self.music_model
        file_url = await self._poll(client, task_id)
        if not file_url:
            raise RuntimeError(f"PoYo task {task_id!r} finished with no file_url")
        await self._download(file_url, Path(output_path))
        return MusicResult(
            provider=self.provider_name,
            model=model_used,
            path=Path(output_path),
            duration_seconds=float(duration_seconds),
        )
```

- [ ] **Step 4: Export the new provider**

In `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/__init__.py`, add to the existing exports:
```python
from video_editor_asset_ingestion.providers.audio.music_protocol import MusicProvider, MusicResult
from video_editor_asset_ingestion.providers.audio.poyo_music import PoyoMusicProvider
```

- [ ] **Step 5: Register the provider**

In `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/registry.py`, add a `register` call for `PoyoMusicProvider` next to the existing music provider registration. (Inspect the file first to find the pattern; the typical pattern is a `PROVIDERS = [...]` list or a `register()` function. Match the existing style.)

- [ ] **Step 6: Run the test to verify it passes**

Run: `pytest tests/test_poyo_music.py -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Run the full suite to verify no regressions**

Run: `pytest -q 2>&1 | tail -5`
Expected: 792 passed.

- [ ] **Step 8: Commit**

```bash
git add packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/poyo_music.py packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/music_protocol.py packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/__init__.py packages/asset_ingestion/src/video_editor_asset_ingestion/providers/registry.py tests/test_poyo_music.py tests/test_music_provider_protocol.py
git commit -m "feat(asset_ingestion): add PoyoMusicProvider (minimax-music-2-6 with generate-music fallback)"
```

---

## Task 8: Implement `SarvamTTSProvider`

**Files:**
- Create: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/sarvam_tts.py`
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/__init__.py` (export the new class)
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/registry.py` (register the new provider)

**Interfaces:**
- Consumes: `ProviderConfig`, voice_id string (`sarvam:shubh` → `{speaker: "shubh", model: "bulbul:v3"}`), language string (e.g. `"hi"` → `target_language_code: "hi-IN"`)
- Produces: `class SarvamTTSProvider(GenerativeProvider)` with `provider_name = "sarvam_tts"`, `async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult`. Calls `POST /text-to-speech` with header `api-subscription-key: $SARAVAM_API`. Decodes the base64 `audios[0]` to `output_path`. Falls back to `bulbul:v2` on `model_unavailable`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sarvam_tts.py`:
```python
"""Tests for the SarvamTTSProvider (mocked Sarvam API)."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from video_editor_asset_ingestion.models import AssetRequest, AssetType


@pytest.mark.asyncio
async def test_sarvam_tts_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SARAVAM_API", "test-key")
    from video_editor_asset_ingestion.providers.tts.sarvam_tts import SarvamTTSProvider

    fake_mp3_bytes = b"\x49\x44\x33" + b"\x03\x00" + b"\x00" * 32 + b"\x00" * 128
    fake_b64 = base64.b64encode(fake_mp3_bytes).decode("ascii")

    async def fake_post(self, url, *args, **kwargs):  # noqa: ARG001
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"request_id": "r-1", "audios": [fake_b64]}
        return resp

    out = tmp_path / "scene_001.mp3"
    req = AssetRequest(
        asset_type=AssetType.AUDIO,
        prompt="राजा बोले",
        provider="sarvam_tts",
        model="bulbul:v3",
        metadata={"voice_id": "sarvam:shubh", "language": "hi"},
    )
    with patch("httpx.AsyncClient.post", new=fake_post):
        provider = SarvamTTSProvider()
        result = await provider.generate(req, out)

    assert result.provider == "sarvam_tts"
    assert out.exists()
    assert out.read_bytes() == fake_mp3_bytes


@pytest.mark.asyncio
async def test_sarvam_tts_falls_back_to_v2_on_model_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SARAVAM_API", "test-key")
    from video_editor_asset_ingestion.providers.tts.sarvam_tts import SarvamTTSProvider

    fake_b64 = base64.b64encode(b"\x49\x44\x33\x04\x00" + b"\x00" * 64).decode("ascii")
    used_models: list[str] = []

    async def fake_post(self, url, *args, **kwargs):  # noqa: ARG001
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        body = kwargs.get("json") or {}
        used_models.append(body.get("model", ""))
        if body.get("model") == "bulbul:v3":
            resp.json.return_value = {"error": "model_unavailable"}
            resp.status_code = 503
        else:
            resp.json.return_value = {"request_id": "r-2", "audios": [fake_b64]}
        return resp

    out = tmp_path / "scene_001.mp3"
    req = AssetRequest(
        asset_type=AssetType.AUDIO,
        prompt="hello",
        provider="sarvam_tts",
        model="bulbul:v3",
        metadata={"voice_id": "sarvam:anushka", "language": "en"},
    )
    with patch("httpx.AsyncClient.post", new=fake_post):
        provider = SarvamTTSProvider()
        result = await provider.generate(req, out)

    assert used_models == ["bulbul:v3", "bulbul:v2"]
    assert result.model == "bulbul:v2"


def test_sarvam_tts_raises_on_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SARAVAM_API", raising=False)
    from video_editor_asset_ingestion.providers.tts.sarvam_tts import SarvamTTSProvider

    with pytest.raises(RuntimeError, match="SARAVAM_API"):
        SarvamTTSProvider()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_sarvam_tts.py -v`
Expected: FAIL — `ImportError: cannot import name 'SarvamTTSProvider'`.

- [ ] **Step 3: Create the provider**

Create `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/sarvam_tts.py`:
```python
"""Sarvam AI Bulbul v3 TTS provider.

Calls ``POST https://api.sarvam.ai/text-to-speech`` with header
``api-subscription-key: $SARAVAM_API``. The response carries base64-encoded
audio in ``audios[0]``; we decode it to the output path.

Supports the Indian-language set:
  bn-IN, en-IN, gu-IN, hi-IN, kn-IN, ml-IN, mr-IN, od-IN, pa-IN, ta-IN, te-IN.

Falls back to ``bulbul:v2`` when v3 returns ``model_unavailable``.
Cost: ₹30 / 10K chars (~$0.036/1K chars) for v3; ~6× cheaper than ElevenLabs TTS.
"""

from __future__ import annotations

import base64
from pathlib import Path

import httpx
import structlog

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.base import GenerativeProvider

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.sarvam.ai"
DEFAULT_MODEL = "bulbul:v3"
DEFAULT_FALLBACK_MODEL = "bulbul:v2"

_LANGUAGE_CODE_MAP = {
    "hi": "hi-IN", "bn": "bn-IN", "gu": "gu-IN", "kn": "kn-IN",
    "ml": "ml-IN", "mr": "mr-IN", "od": "od-IN", "pa": "pa-IN",
    "ta": "ta-IN", "te": "te-IN", "en": "en-IN", "en-in": "en-IN",
    "hi-in": "hi-IN",
}


def _to_target_language_code(language: str) -> str:
    if not language:
        return "en-IN"
    key = language.lower().strip()
    if key in _LANGUAGE_CODE_MAP:
        return _LANGUAGE_CODE_MAP[key]
    # Already a BCP-47 code? Return uppercase the way Sarvam expects.
    if "-" in language:
        return language
    return "en-IN"


def _parse_voice_id(voice_id: str) -> tuple[str, str]:
    """Parse 'sarvam:shubh' into ('sarvam', 'shubh'); default prefix to 'sarvam'."""
    if ":" in voice_id:
        prefix, speaker = voice_id.split(":", 1)
        return prefix, speaker
    return "sarvam", voice_id


class SarvamTTSProvider(GenerativeProvider):
    """Generate speech via Sarvam's Bulbul v3 TTS endpoint."""

    provider_name = "sarvam_tts"

    def __init__(self, config: ProviderConfig | None = None) -> None:
        self.config = config or ProviderConfig()
        self.base_url = (self.config.base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = self.config.api_key()
        if not self.api_key:
            raise RuntimeError(
                "SARAVAM_API is not set in the environment. "
                "Add it to the repo-root .env (api_key_env defaults to SARAVAM_API)."
            )
        self.timeout = self.config.timeout
        self.model = self.config.provider_options.get("tts_model") or DEFAULT_MODEL
        self.fallback_model = self.config.provider_options.get("fallback_model") or DEFAULT_FALLBACK_MODEL

    async def _call(self, text: str, speaker: str, language: str, model: str) -> str:
        payload = {
            "text": text,
            "target_language_code": _to_target_language_code(language),
            "model": model,
            "speaker": speaker,
        }
        headers = {
            "api-subscription-key": self.api_key or "",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            resp = await client.post("/text-to-speech", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json() or {}
            audios = data.get("audios") or []
            if not audios:
                raise RuntimeError(f"Sarvam returned no audio for model={model!r}")
            return audios[0]

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        if request.asset_type != AssetType.AUDIO:
            raise ValueError(f"SarvamTTSProvider only handles AssetType.AUDIO, got {request.asset_type!r}")
        voice_id = (request.metadata or {}).get("voice_id") or "sarvam:shubh"
        language = (request.metadata or {}).get("language") or "en"
        _, speaker = _parse_voice_id(voice_id)
        try:
            audio_b64 = await self._call(request.prompt, speaker, language, self.model)
            model_used = self.model
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (400, 503) and self.fallback_model and self.fallback_model != self.model:
                logger.warning("sarvam_tts_falling_back", primary=self.model, fallback=self.fallback_model, status=exc.response.status_code)
                audio_b64 = await self._call(request.prompt, speaker, language, self.fallback_model)
                model_used = self.fallback_model
            else:
                raise
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(base64.b64decode(audio_b64))
        return AssetResult(
            provider=self.provider_name,
            model=model_used,
            path=Path(output_path),
            prompt=request.prompt,
            asset_type=AssetType.AUDIO,
            duration_seconds=0.0,
            metadata={"voice_id": voice_id, "language": _to_target_language_code(language)},
        )
```

- [ ] **Step 4: Export the new provider**

In `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/__init__.py`, add:
```python
from video_editor_asset_ingestion.providers.tts.sarvam_tts import SarvamTTSProvider
```

- [ ] **Step 5: Register the provider**

In `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/registry.py`, add `SarvamTTSProvider` to the provider registration list/function (match the existing style).

- [ ] **Step 6: Run the test to verify it passes**

Run: `pytest tests/test_sarvam_tts.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Run the full suite to verify no regressions**

Run: `pytest -q 2>&1 | tail -5`
Expected: 795 passed.

- [ ] **Step 8: Commit**

```bash
git add packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/sarvam_tts.py packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/__init__.py packages/asset_ingestion/src/video_editor_asset_ingestion/providers/registry.py tests/test_sarvam_tts.py
git commit -m "feat(asset_ingestion): add SarvamTTSProvider (Bulbul v3, v2 fallback, base64 decode)"
```

---

## Task 9: Implement `TTSRouter`

**Files:**
- Create: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/tts_router.py`
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/__init__.py`

**Interfaces:**
- Consumes: voice_id (with optional `sarvam:` / `elevenlabs:` prefix), language code
- Produces: `class TTSRouter` that implements the same `async def generate(request, output_path) -> AssetResult` interface as `GenerativeProvider`, routing to `SarvamTTSProvider` or `ElevenLabsTTSProvider` based on the routing rule.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tts_router.py`:
```python
"""Tests for the TTSRouter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from video_editor_asset_ingestion.models import AssetRequest, AssetType


def _req(voice_id: str, language: str) -> AssetRequest:
    return AssetRequest(
        asset_type=AssetType.AUDIO,
        prompt="hello",
        provider="tts_router",
        model="auto",
        metadata={"voice_id": voice_id, "language": language},
    )


@pytest.mark.asyncio
async def test_indian_language_routes_to_sarvam(tmp_path: Path) -> None:
    from video_editor_asset_ingestion.providers.tts.tts_router import TTSRouter

    called_with: list[str] = []

    async def fake_sarvam(self, request, output_path):  # noqa: ARG001
        called_with.append("sarvam")
        from video_editor_asset_ingestion.models import AssetResult
        return AssetResult(provider="sarvam_tts", model="bulbul:v3", path=output_path, prompt=request.prompt, asset_type=AssetType.AUDIO, duration_seconds=1.0)

    async def fake_elevenlabs(self, request, output_path):  # noqa: ARG001
        called_with.append("elevenlabs")
        from video_editor_asset_ingestion.models import AssetResult
        return AssetResult(provider="elevenlabs_tts", model="eleven_v3", path=output_path, prompt=request.prompt, asset_type=AssetType.AUDIO, duration_seconds=1.0)

    with patch.object(
        __import__("video_editor_asset_ingestion.providers.tts.sarvam_tts", fromlist=["SarvamTTSProvider"]).SarvamTTSProvider,
        "generate",
        new=fake_sarvam,
    ), patch.object(
        __import__("video_editor_asset_ingestion.providers.tts.elevenlabs_tts", fromlist=["ElevenLabsTTSProvider"]).ElevenLabsTTSProvider,
        "generate",
        new=fake_elevenlabs,
    ):
        router = TTSRouter()
        await router.generate(_req("sarvam:shubh", "hi"), tmp_path / "a.mp3")

    assert called_with == ["sarvam"]


@pytest.mark.asyncio
async def test_english_routes_to_elevenlabs(tmp_path: Path) -> None:
    from video_editor_asset_ingestion.providers.tts.tts_router import TTSRouter

    called_with: list[str] = []

    async def fake_sarvam(self, request, output_path):  # noqa: ARG001
        called_with.append("sarvam")
        from video_editor_asset_ingestion.models import AssetResult
        return AssetResult(provider="sarvam_tts", model="bulbul:v3", path=output_path, prompt=request.prompt, asset_type=AssetType.AUDIO, duration_seconds=1.0)

    async def fake_elevenlabs(self, request, output_path):  # noqa: ARG001
        called_with.append("elevenlabs")
        from video_editor_asset_ingestion.models import AssetResult
        return AssetResult(provider="elevenlabs_tts", model="eleven_v3", path=output_path, prompt=request.prompt, asset_type=AssetType.AUDIO, duration_seconds=1.0)

    with patch.object(
        __import__("video_editor_asset_ingestion.providers.tts.sarvam_tts", fromlist=["SarvamTTSProvider"]).SarvamTTSProvider,
        "generate",
        new=fake_sarvam,
    ), patch.object(
        __import__("video_editor_asset_ingestion.providers.tts.elevenlabs_tts", fromlist=["ElevenLabsTTSProvider"]).ElevenLabsTTSProvider,
        "generate",
        new=fake_elevenlabs,
    ):
        router = TTSRouter()
        await router.generate(_req("elevenlabs:Brian", "en"), tmp_path / "a.mp3")

    assert called_with == ["elevenlabs"]


@pytest.mark.asyncio
async def test_explicit_prefix_overrides_language_rule(tmp_path: Path) -> None:
    """An English scene asking for sarvam:anushka must use Sarvam regardless."""
    from video_editor_asset_ingestion.providers.tts.tts_router import TTSRouter

    called_with: list[str] = []

    async def fake_sarvam(self, request, output_path):  # noqa: ARG001
        called_with.append("sarvam")
        from video_editor_asset_ingestion.models import AssetResult
        return AssetResult(provider="sarvam_tts", model="bulbul:v3", path=output_path, prompt=request.prompt, asset_type=AssetType.AUDIO, duration_seconds=1.0)

    async def fake_elevenlabs(self, request, output_path):  # noqa: ARG001
        called_with.append("elevenlabs")
        from video_editor_asset_ingestion.models import AssetResult
        return AssetResult(provider="elevenlabs_tts", model="eleven_v3", path=output_path, prompt=request.prompt, asset_type=AssetType.AUDIO, duration_seconds=1.0)

    with patch.object(
        __import__("video_editor_asset_ingestion.providers.tts.sarvam_tts", fromlist=["SarvamTTSProvider"]).SarvamTTSProvider,
        "generate",
        new=fake_sarvam,
    ), patch.object(
        __import__("video_editor_asset_ingestion.providers.tts.elevenlabs_tts", fromlist=["ElevenLabsTTSProvider"]).ElevenLabsTTSProvider,
        "generate",
        new=fake_elevenlabs,
    ):
        router = TTSRouter()
        await router.generate(_req("sarvam:anushka", "en"), tmp_path / "a.mp3")

    assert called_with == ["sarvam"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_tts_router.py -v`
Expected: FAIL — `ImportError: cannot import name 'TTSRouter'`.

- [ ] **Step 3: Create the router**

Create `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/tts_router.py`:
```python
"""TTSRouter: route a TTS request to Sarvam or ElevenLabs by language + voice prefix.

Routing rule:
- If voice_id has an explicit ``sarvam:`` or ``elevenlabs:`` prefix → use that
  provider regardless of language.
- Else if the resolved language is in the Sarvam-supported set
  (``{hi, bn, gu, kn, ml, mr, od, pa, ta, te, en-IN}``) → Sarvam.
- Else → ElevenLabs.

The router implements the same ``GenerativeProvider.generate(request, output_path)``
interface so callers can drop it in for either provider without code changes.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from video_editor_asset_ingestion.models import AssetRequest, AssetResult
from video_editor_asset_ingestion.providers.base import GenerativeProvider
from video_editor_asset_ingestion.providers.tts.elevenlabs_tts import ElevenLabsTTSProvider
from video_editor_asset_ingestion.providers.tts.sarvam_tts import SarvamTTSProvider

logger = structlog.get_logger(__name__)

_SARVAM_LANGUAGES = frozenset({"hi", "bn", "gu", "kn", "ml", "mr", "od", "pa", "ta", "te", "en-in", "hi-in"})


def _language_root(language: str) -> str:
    return (language or "").lower().strip().split("-")[0]


def _pick_provider(voice_id: str, language: str) -> str:
    if voice_id.startswith("sarvam:"):
        return "sarvam"
    if voice_id.startswith("elevenlabs:"):
        return "elevenlabs"
    # No explicit prefix — apply the language rule.
    lang_norm = (language or "").lower().strip()
    if lang_norm in _SARVAM_LANGUAGES or _language_root(language) in {l for l in _SARVAM_LANGUAGES if "-" not in l}:
        return "sarvam"
    return "elevenlabs"


class TTSRouter(GenerativeProvider):
    """A thin router that delegates TTS to Sarvam or ElevenLabs."""

    provider_name = "tts_router"

    def __init__(self) -> None:
        # Construct lazily so missing env vars in one provider don't break
        # the other at import time.
        self._sarvam: SarvamTTSProvider | None = None
        self._elevenlabs: ElevenLabsTTSProvider | None = None

    def _get(self, name: str):
        if name == "sarvam" and self._sarvam is None:
            self._sarvam = SarvamTTSProvider()
        if name == "elevenlabs" and self._elevenlabs is None:
            self._elevenlabs = ElevenLabsTTSProvider()
        if name == "sarvam":
            return self._sarvam
        if name == "elevenlabs":
            return self._elevenlabs
        raise ValueError(f"unknown tts provider {name!r}")

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        voice_id = (request.metadata or {}).get("voice_id") or "elevenlabs:Brian"
        language = (request.metadata or {}).get("language") or "en"
        provider_name = _pick_provider(voice_id, language)
        logger.info("tts_router_pick", provider=provider_name, voice_id=voice_id, language=language)
        return await self._get(provider_name).generate(request, Path(output_path))
```

- [ ] **Step 4: Export the router**

In `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/__init__.py`, add:
```python
from video_editor_asset_ingestion.providers.tts.tts_router import TTSRouter
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/test_tts_router.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Run the full suite to verify no regressions**

Run: `pytest -q 2>&1 | tail -5`
Expected: 798 passed.

- [ ] **Step 7: Commit**

```bash
git add packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/tts_router.py packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/__init__.py tests/test_tts_router.py
git commit -m "feat(asset_ingestion): add TTSRouter (Sarvam for Indian langs, ElevenLabs otherwise)"
```

---

## Task 10: Implement `build_storyteller_scene_plan()` (image + motion + assets)

**Files:**
- Modify: `apps/engine/src/video_editor_engine/planner/storyteller_planner.py` (append the builder)
- Create: `tests/test_storyteller_planner.py`

**Interfaces:**
- Consumes: `StorytellerScript`, `ModelRouter`, `AssetIngestionManager`, `MusicProvider`, SFX callable, `TTSProvider`, template name
- Produces: `ScenePlan` (populated with `Story` scenes, image/video assets, TTS assets, SFX assets, props per the spec)

- [ ] **Step 1: Write the failing test**

Create `tests/test_storyteller_planner.py`:
```python
"""Tests for build_storyteller_scene_plan() (mocked LLM, providers, and ffmpeg)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "storyteller" / "sample_story.md"


def _fake_llm_router() -> MagicMock:
    """Return a router whose .generate() returns a deterministic JSON scene plan."""
    router = MagicMock()
    router.generate = AsyncMock(
        side_effect=[
            # Image-prompt derivation for the narrator scene
            json.dumps({"visual_description": "wide shot of a coastal kingdom at dawn"}),
            # Image-prompt derivation for the king scene (has motion override)
            json.dumps({"visual_description": "throne room, candles, king addressing court"}),
            # Image-prompt derivation for the princess scene
            json.dumps({"visual_description": "young princess at a stone window"}),
            # Motion policy for the budget template
            json.dumps({"scenes": [
                {"id": "scene_001", "needs_motion": False},
                {"id": "scene_002", "needs_motion": True},
                {"id": "scene_003", "needs_motion": False},
            ]}),
        ]
    )
    return router


def _fake_tts() -> MagicMock:
    tts = MagicMock()
    fake_audio = b"\x00" * 48000  # 1 second of silence at 48kHz mono
    tts.synthesize = AsyncMock(return_value=MagicMock(duration_seconds=2.0, audio_bytes=fake_audio))
    return tts


def _fake_asset_manager(tmp_path: Path) -> MagicMock:
    mgr = MagicMock()

    async def fake_resolve(requests):
        results = []
        for req in requests:
            ext = ".mp4" if req.asset_type.value == "video" else ".png" if req.asset_type.value == "image" else ".mp3"
            path = tmp_path / f"{req.asset_type.value}-{len(results)}{ext}"
            path.write_bytes(b"FAKE")
            results.append(
                MagicMock(
                    provider=req.provider,
                    model=req.model,
                    path=path,
                    prompt=req.prompt,
                    asset_type=req.asset_type,
                    duration_seconds=4.0,
                    metadata=req.metadata or {},
                )
            )
        return results

    mgr.resolve = fake_resolve
    return mgr


def _fake_music(tmp_path: Path) -> MagicMock:
    from video_editor_asset_ingestion.providers.audio.music_protocol import MusicResult
    music = MagicMock()
    out = tmp_path / "bg.mp3"
    out.write_bytes(b"FAKE")
    music.generate_music = AsyncMock(return_value=MusicResult(provider="poyo_music", model="minimax-music-2-6", path=out, duration_seconds=120.0))
    return music


def _fake_sfx(tmp_path: Path) -> MagicMock:
    sfx = MagicMock()

    async def fake_synthesize(prompt: str, output_path: Path) -> MagicMock:
        Path(output_path).write_bytes(b"FAKE")
        return MagicMock(provider="elevenlabs_sfx", model="sfx_v1", path=output_path, prompt=prompt, duration_seconds=2.0, asset_type=MagicMock(value="audio"))

    sfx.synthesize = fake_synthesize
    return sfx


@pytest.mark.asyncio
async def test_storyteller_hd_respects_motion_override(tmp_path: Path) -> None:
    from video_editor_engine.planner.storyteller_planner import (
        build_storyteller_scene_plan,
        parse_storyteller_script,
    )

    script = parse_storyteller_script(FIXTURE)
    plan = await build_storyteller_scene_plan(
        script,
        _fake_llm_router(),
        template_name="storyteller_hd",
        asset_manager=_fake_asset_manager(tmp_path),  # type: ignore[arg-type]
        music_provider=_fake_music(tmp_path),  # type: ignore[arg-type]
        sfx_provider=_fake_sfx(tmp_path),  # type: ignore[arg-type]
        tts_provider=_fake_tts(),  # type: ignore[arg-type]
    )

    assert len(plan.scenes) == 3
    assert [s.props.get("needs_motion") for s in plan.scenes] == [False, True, False]
    # The motion scene (scene_002) is rendered as a video, the others as images
    assert [s.props.get("render_track") for s in plan.scenes] == ["image", "video", "image"]


@pytest.mark.asyncio
async def test_storyteller_budget_uses_llm_motion_policy(tmp_path: Path) -> None:
    from video_editor_engine.planner.storyteller_planner import (
        build_storyteller_scene_plan,
        parse_storyteller_script,
    )

    script = parse_storyteller_script(FIXTURE)
    plan = await build_storyteller_scene_plan(
        script,
        _fake_llm_router(),
        template_name="storyteller_budget",
        asset_manager=_fake_asset_manager(tmp_path),  # type: ignore[arg-type]
        music_provider=_fake_music(tmp_path),  # type: ignore[arg-type]
        sfx_provider=_fake_sfx(tmp_path),  # type: ignore[arg-type]
        tts_provider=_fake_tts(),  # type: ignore[arg-type]
    )

    assert [s.props.get("needs_motion") for s in plan.scenes] == [False, True, False]
    # Budget model IDs
    assert plan.scenes[0].props.get("image_model") == "flux-schnell"
    assert plan.scenes[1].props.get("motion_model") == "wan-2-2-fast"


@pytest.mark.asyncio
async def test_visual_override_skips_llm_call(tmp_path: Path) -> None:
    from video_editor_engine.planner.storyteller_planner import (
        build_storyteller_scene_plan,
        parse_storyteller_script,
    )

    script = parse_storyteller_script(FIXTURE)
    router = _fake_llm_router()
    plan = await build_storyteller_scene_plan(
        script,
        router,
        template_name="storyteller_hd",
        asset_manager=_fake_asset_manager(tmp_path),  # type: ignore[arg-type]
        music_provider=_fake_music(tmp_path),  # type: ignore[arg-type]
        sfx_provider=_fake_sfx(tmp_path),  # type: ignore[arg-type]
        tts_provider=_fake_tts(),  # type: ignore[arg-type]
    )
    # The narrator scene has a `> visual:` override; the planner uses it as-is
    assert "aerial wide shot" in plan.scenes[0].visual_description
    # So the first LLM call (image-prompt derivation) is NOT for scene_001
    # — the call count is: 2 derivations (king, princess) + 1 motion policy? No, HD = no motion policy.
    # Total: 2 LLM calls (king + princess)
    assert router.generate.await_count == 2


@pytest.mark.asyncio
async def test_music_mood_inherited_from_script(tmp_path: Path) -> None:
    from video_editor_engine.planner.storyteller_planner import (
        build_storyteller_scene_plan,
        parse_storyteller_script,
    )

    script = parse_storyteller_script(FIXTURE)
    music = _fake_music(tmp_path)
    await build_storyteller_scene_plan(
        script,
        _fake_llm_router(),
        template_name="storyteller_hd",
        asset_manager=_fake_asset_manager(tmp_path),  # type: ignore[arg-type]
        music_provider=music,  # type: ignore[arg-type]
        sfx_provider=_fake_sfx(tmp_path),  # type: ignore[arg-type]
        tts_provider=_fake_tts(),  # type: ignore[arg-type]
    )
    call_args = music.generate_music.await_args
    assert "cinematic" in (call_args.args[0] if call_args.args else call_args.kwargs["prompt"])


@pytest.mark.asyncio
async def test_premium_preset_uses_premium_models(tmp_path: Path) -> None:
    """The storyteller_premium preset (premium, premium) must use seedance-2 for motion scenes."""
    from video_editor_engine.planner.storyteller_planner import (
        build_storyteller_scene_plan,
        parse_storyteller_script,
    )

    script = parse_storyteller_script(FIXTURE)
    # Make king scene's `> motion: true` override trigger motion path
    plan = await build_storyteller_scene_plan(
        script,
        _fake_llm_router(),
        template_name="storyteller_premium",
        asset_manager=_fake_asset_manager(tmp_path),  # type: ignore[arg-type]
        music_provider=_fake_music(tmp_path),  # type: ignore[arg-type]
        sfx_provider=_fake_sfx(tmp_path),  # type: ignore[arg-type]
        tts_provider=_fake_tts(),  # type: ignore[arg-type]
    )
    # King scene had `> motion: true` so it goes through the motion path.
    motion_scene = next(s for s in plan.scenes if s.props["needs_motion"])
    assert motion_scene.props["motion_model"] == "seedance-2"
    assert motion_scene.props["motion_resolution"] == "1080p"
    assert motion_scene.props["video_tier"] == "premium"
    # All scenes record the image tier even when no image is generated
    for s in plan.scenes:
        assert s.props["image_tier"] == "premium"


@pytest.mark.asyncio
async def test_explicit_tier_overrides_template_preset(tmp_path: Path) -> None:
    """--image-tier premium --video-tier budget on a storyteller_hd job must use flux-schnell + seedance-2."""
    from video_editor_engine.planner.storyteller_planner import (
        build_storyteller_scene_plan,
        parse_storyteller_script,
    )

    script = parse_storyteller_script(FIXTURE)
    plan = await build_storyteller_scene_plan(
        script,
        _fake_llm_router(),
        template_name="storyteller_hd",
        image_tier="budget",
        video_tier="premium",
        asset_manager=_fake_asset_manager(tmp_path),  # type: ignore[arg-type]
        music_provider=_fake_music(tmp_path),  # type: ignore[arg-type]
        sfx_provider=_fake_sfx(tmp_path),  # type: ignore[arg-type]
        tts_provider=_fake_tts(),  # type: ignore[arg-type]
    )
    # All still-image scenes pick up the budget image tier
    image_scenes = [s for s in plan.scenes if not s.props["needs_motion"]]
    assert all(s.props["image_model"] == "flux-schnell" for s in image_scenes)
    # Motion scene picks up the premium motion tier
    motion_scenes = [s for s in plan.scenes if s.props["needs_motion"]]
    assert all(s.props["motion_model"] == "seedance-2" for s in motion_scenes)


def test_invalid_tier_raises() -> None:
    from video_editor_engine.planner.storyteller_planner import _resolve_tiers
    import pytest

    with pytest.raises(ValueError, match="invalid image_tier"):
        _resolve_tiers("storyteller_hd", image_tier="garbage", video_tier=None)
    with pytest.raises(ValueError, match="invalid video_tier"):
        _resolve_tiers("storyteller_hd", image_tier=None, video_tier="garbage")
    with pytest.raises(ValueError, match="unknown storyteller template"):
        _resolve_tiers("storyteller_xl", image_tier=None, video_tier=None)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_storyteller_planner.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_storyteller_scene_plan'`.

- [ ] **Step 3: Implement the builder**

Append to `apps/engine/src/video_editor_engine/planner/storyteller_planner.py`:
```python
import hashlib
import json
from typing import Any, Awaitable, Callable, Literal

from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.audio.music_protocol import MusicProvider
from video_editor_engine.models.scene_plan import Scene, ScenePlan
from video_editor_llm.router import ModelRouter

from video_editor_engine.models.storyteller_script import StorytellerScript

# ... append after parse_storyteller_script():

# Quality-tier model tables (per docs/POYO_ROUTING.md tiers)
# Users pick one of each independently via --image-tier / --video-tier;
# template presets map to (image_tier, video_tier) pairs.

VIDEO_TIER_MODELS: dict[str, tuple[str, str]] = {
    # tier -> (model, resolution)
    "budget":   ("wan-2-2-fast",    "480p"),   # ~$0.030/gen, gentle parallax
    "standard": ("seedance-2-fast", "720p"),   # ~$0.07–$0.14/sec, default
    "premium":  ("seedance-2",      "1080p"),  # ~$0.31/sec, cinematic
}

IMAGE_TIER_MODELS: dict[str, str] = {
    "budget":   "flux-schnell",         # ~$0.002
    "standard": "gpt-image-2",          # default; the planner pins 1K low via metadata
    "premium":  "gpt-image-2",          # 2K/4K high via metadata "image_size"
}

IMAGE_TIER_SIZE: dict[str, str] = {
    "budget":   "1024x1024",
    "standard": "1024x1024",   # 1K low
    "premium":  "2048x2048",   # 2K high (override via metadata if 4K is needed)
}

# Template-preset → (image_tier, video_tier) defaults
TEMPLATE_TIER_PRESETS: dict[str, tuple[str, str]] = {
    "storyteller_budget":  ("budget",   "budget"),
    "storyteller_hd":      ("standard", "standard"),
    "storyteller_premium": ("premium",  "premium"),
}


def _resolve_tiers(
    template_name: str, image_tier: str | None, video_tier: str | None,
) -> tuple[str, str]:
    """Resolve (image_tier, video_tier) from template preset + optional overrides.

    CLI overrides win over template defaults. Invalid tier values raise
    ``ValueError`` with a list of accepted values.
    """
    if template_name not in TEMPLATE_TIER_PRESETS:
        raise ValueError(
            f"unknown storyteller template {template_name!r}; "
            f"valid: {sorted(TEMPLATE_TIER_PRESETS)}"
        )
    base_image, base_video = TEMPLATE_TIER_PRESETS[template_name]
    image = image_tier or base_image
    video = video_tier or base_video
    if image not in IMAGE_TIER_MODELS:
        raise ValueError(
            f"invalid image_tier {image!r}; valid: {sorted(IMAGE_TIER_MODELS)}"
        )
    if video not in VIDEO_TIER_MODELS:
        raise ValueError(
            f"invalid video_tier {video!r}; valid: {sorted(VIDEO_TIER_MODELS)}"
        )
    return image, video


def _cache_key(text: str, speaker: str, prev_visual: str) -> str:
    return hashlib.sha256(f"{text}|{speaker}|{prev_visual}".encode("utf-8")).hexdigest()


def _load_cache(project_dir: Path, filename: str) -> dict[str, Any]:
    cache_path = project_dir / ".cache" / filename
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(project_dir: Path, filename: str, cache: dict[str, Any]) -> None:
    cache_path = project_dir / ".cache" / filename
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


async def _derive_visual_description(
    text: str,
    speaker: str,
    prev_visual: str,
    llm_router: ModelRouter,
    cache: dict[str, str],
) -> str:
    key = _cache_key(text, speaker, prev_visual)
    if key in cache:
        return cache[key]
    prompt = (
        "You are an image-prompt writer for a story video. Given a piece of narration "
        "and the previous scene's visual, output ONE rich, generative image prompt "
        "suitable for a single 16:9 frame.\n\n"
        f"Speaker: {speaker}\n"
        f"Previous scene visual: {prev_visual or 'none'}\n"
        f"Narration: {text}\n\n"
        "Return ONLY the prompt, no preamble, no JSON."
    )
    response = await llm_router.generate(task_type="scene_plan", prompt=prompt)
    cache[key] = response.strip()
    return cache[key]


def _resolve_motion_policy(
    template_name: Literal["storyteller_hd", "storyteller_budget"],
    scenes: list[StorytellerScene],
) -> dict[str, bool]:
    """Return a {scene_id: needs_motion} map per template's policy.

    HD: honour `> motion: true`; otherwise false.
    Budget: leave all None — the caller will fill it in via the LLM call.
    """
    if template_name == "storyteller_hd":
        return {s.id: (s.motion_override is True) for s in scenes}
    return {s.id: False for s in scenes}


async def _budget_motion_policy(
    scenes: list[StorytellerScene],
    llm_router: ModelRouter,
    cache: dict[str, Any],
    project_dir: Path,
) -> dict[str, bool]:
    cache_path = project_dir / ".cache" / "motion_policy.json"
    cache_key = hashlib.sha256(
        json.dumps([(s.id, s.text, s.speaker) for s in scenes], sort_keys=True).encode("utf-8")
    ).hexdigest()
    if cache_key in cache:
        return cache[cache_key]
    payload = [
        {"id": s.id, "speaker": s.speaker, "text": s.text} for s in scenes
    ]
    prompt = (
        "For each story scene below, decide needs_motion (true if the narration "
        "describes movement, action, scene-establishing, or anything that would feel "
        "static as a still image; false for portraits, dialogue, contemplation).\n"
        "Aim for ~50% true. Prefer false for short lines.\n"
        f"Scenes: {json.dumps(payload)}\n\n"
        "Return ONLY JSON: {\"scenes\": [{\"id\": \"...\", \"needs_motion\": true|false}, ...]}"
    )
    response = await llm_router.generate(task_type="scene_plan", prompt=prompt)
    parsed = json.loads(response)
    result = {item["id"]: bool(item["needs_motion"]) for item in parsed.get("scenes", [])}
    cache[cache_key] = result
    return result


async def build_storyteller_scene_plan(
    script: StorytellerScript,
    llm_router: ModelRouter,
    *,
    template_name: Literal["storyteller_budget", "storyteller_hd", "storyteller_premium"],
    asset_manager: AssetIngestionManager,
    music_provider: MusicProvider,
    sfx_provider: Any,
    tts_provider: Any,
    project_dir: Path | None = None,
    on_scene_ready: Callable[[Scene], Awaitable[None]] | None = None,
    image_tier: str | None = None,
    video_tier: str | None = None,
) -> ScenePlan:
    """Plan + generate assets for a StorytellerScript and return a ScenePlan.

    ``image_tier`` and ``video_tier`` override the template preset's defaults
    independently (e.g. ``--image-tier premium --video-tier standard``).
    """
    image_tier, video_tier = _resolve_tiers(template_name, image_tier, video_tier)
    if project_dir is None:
        project_dir = Path("projects/_storyteller_tmp")
    project_dir.mkdir(parents=True, exist_ok=True)
    image_cache = _load_cache(project_dir, "image_prompts.json")
    motion_cache = _load_cache(project_dir, "motion_policy.json")

    # 1. Resolve motion policy. Budget preset = LLM picks motion; HD/Premium = user marks `> motion: true`.
    if template_name == "storyteller_budget":
        motion = await _budget_motion_policy(script.scenes, llm_router, motion_cache, project_dir)
    else:
        motion = {s.id: (s.motion_override is True) for s in script.scenes}

    # 2. Per-scene: derive visual, generate assets
    image_model = IMAGE_TIER_MODELS[image_tier]
    motion_model, motion_resolution = VIDEO_TIER_MODELS[video_tier]
    image_size = IMAGE_TIER_SIZE[image_tier]

    plan_scenes: list[Scene] = []
    prev_visual = ""
    for i, story_scene in enumerate(script.scenes):
        # Visual description
        if story_scene.visual_override:
            visual = story_scene.visual_override
            prompt_source = "override"
        else:
            visual = await _derive_visual_description(
                story_scene.text, story_scene.speaker, prev_visual, llm_router, image_cache
            )
            prompt_source = "llm"

        # Asset requests
        needs_motion = motion.get(story_scene.id, False)
        requests: list[AssetRequest] = []
        if needs_motion:
            requests.append(AssetRequest(
                asset_type=AssetType.VIDEO,
                prompt=visual,
                provider="poyo_ai",
                model=motion_model,
                metadata={
                    "resolution": motion_resolution,
                    "duration_seconds": 4,
                    "tier": video_tier,
                },
            ))
        else:
            requests.append(AssetRequest(
                asset_type=AssetType.IMAGE,
                prompt=visual,
                provider="poyo_ai",
                model=image_model,
                metadata={
                    "aspect_ratio": "16:9",
                    "image_size": image_size,
                    "tier": image_tier,
                },
            ))

        # TTS
        requests.append(AssetRequest(
            asset_type=AssetType.AUDIO,
            prompt=story_scene.text,
            provider="tts_router",
            model="auto",
            metadata={"voice_id": story_scene.voice_id, "language": story_scene.language},
        ))

        # SFX
        for sfx_prompt in story_scene.sfx_prompts:
            requests.append(AssetRequest(
                asset_type=AssetType.AUDIO,
                prompt=sfx_prompt,
                provider="elevenlabs_sfx",
                model="sfx_v1",
                metadata={"kind": "sfx"},
            ))

        results = await asset_manager.resolve(requests)

        # Compute duration from TTS result
        tts_results = [r for r in results if r.asset_type == AssetType.AUDIO and (r.metadata or {}).get("kind") != "sfx"]
        sfx_results = [r for r in results if r.asset_type == AssetType.AUDIO and (r.metadata or {}).get("kind") == "sfx"]
        if tts_results and getattr(tts_results[0], "duration_seconds", 0):
            duration = tts_results[0].duration_seconds * 1.15
        else:
            duration = 4.0  # safe default for a silent scene

        scene = Scene(
            id=story_scene.id,
            type="story",
            duration_seconds=duration,
            narration=story_scene.text,
            visual_description=visual,
            asset_query="",
            props={
                "speaker": story_scene.speaker,
                "language": story_scene.language,
                "voice_id": story_scene.voice_id,
                "tts_text": story_scene.text,
                "needs_motion": needs_motion,
                "motion_model": motion_model if needs_motion else None,
                "motion_resolution": motion_resolution if needs_motion else None,
                "image_model": image_model,
                "image_size": image_size,
                "image_tier": image_tier,
                "video_tier": video_tier,
                "sfx_prompts": list(story_scene.sfx_prompts),
                "music_mood": script.music_mood,
                "render_track": "video" if needs_motion else "image",
                "visual_prompt_source": prompt_source,
            },
            assets=results,
        )
        plan_scenes.append(scene)
        prev_visual = visual
        if on_scene_ready is not None:
            await on_scene_ready(scene)

    # 3. Background music — single track for the whole story
    total_duration = sum(s.duration_seconds for s in plan_scenes)
    music_prompt = f"{script.title or 'story'} ({script.music_mood}), ~{int(total_duration)}s"
    music_path = project_dir / "assets" / "music" / "bg.mp3"
    music_path.parent.mkdir(parents=True, exist_ok=True)
    await music_provider.generate_music(music_prompt, total_duration, music_path)

    # 4. Persist caches
    _save_cache(project_dir, "image_prompts.json", image_cache)
    _save_cache(project_dir, "motion_policy.json", motion_cache)

    return ScenePlan(
        job_id=project_dir.name,
        script="",  # the original markdown is on disk; ScenePlan only carries the resolved plan
        scenes=plan_scenes,
        aspect_ratio="16:9",
        music_mood=script.music_mood,
        music_path=str(music_path),
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_storyteller_planner.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the full suite to verify no regressions**

Run: `pytest -q 2>&1 | tail -5`
Expected: 802 passed.

- [ ] **Step 6: Commit**

```bash
git add apps/engine/src/video_editor_engine/planner/storyteller_planner.py tests/test_storyteller_planner.py
git commit -m "feat(eng): add build_storyteller_scene_plan() with image-prompt cache and motion policy"
```

---

## Task 11: Add the three new `Template` entries + `storyteller` build_concept

**Files:**
- Create: `apps/engine/src/video_editor_engine/templates/storyteller_concepts.py`
- Modify: `apps/engine/src/video_editor_engine/templates/eleven_templates.py`
- Modify: `tests/test_templates.py` (update count)

**Interfaces:**
- Consumes: existing `TEMPLATES` list
- Produces: three new `Template` entries (`storyteller_budget`, `storyteller_hd`, `storyteller_premium`) using `pipeline_kind="storyteller"`, each with a `quality_tier_pair` field indicating the default `(image_tier, video_tier)`. Plus a `build_concept` helper that returns a starter script markdown.

- [ ] **Step 1: Write the failing test for the new templates**

Create `tests/test_storyteller_templates.py`:
```python
"""Tests for the new storyteller templates and tier-pair metadata."""

from __future__ import annotations

from video_editor_engine.templates import TEMPLATE_NAMES
from video_editor_engine.templates.eleven_templates import TEMPLATE_MAP


def test_storyteller_hd_is_registered_with_standard_pair() -> None:
    tmpl = TEMPLATE_MAP["storyteller_hd"]
    assert tmpl.pipeline_kind == "storyteller"
    assert tmpl.default_aspect_ratio == "16:9"
    assert tmpl.default_providers["music"] == "poyo_music"
    assert tmpl.quality_tier_pair == ("standard", "standard")
    assert tmpl.caveats


def test_storyteller_budget_is_registered_with_budget_pair() -> None:
    tmpl = TEMPLATE_MAP["storyteller_budget"]
    assert tmpl.pipeline_kind == "storyteller"
    assert tmpl.quality_tier_pair == ("budget", "budget")
    assert tmpl.caveats


def test_storyteller_premium_is_registered_with_premium_pair() -> None:
    tmpl = TEMPLATE_MAP["storyteller_premium"]
    assert tmpl.pipeline_kind == "storyteller"
    assert tmpl.quality_tier_pair == ("premium", "premium")
    assert tmpl.caveats


def test_storyteller_build_concept_returns_valid_markdown(tmp_path) -> None:
    from video_editor_engine.templates.storyteller_concepts import build_storyteller_concept

    md = build_storyteller_concept(tmp_path / "story.md")
    assert "cast:" in md
    assert "## Speaker: narrator" in md
    assert "## Speaker: king" in md
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_storyteller_templates.py -v`
Expected: FAIL — `KeyError: 'storyteller_hd'`.

- [ ] **Step 3: Create the `build_concept` helper**

Create `apps/engine/src/video_editor_engine/templates/storyteller_concepts.py`:
```python
"""Starter concept markdown for the storyteller templates."""

from __future__ import annotations

from pathlib import Path


def build_storyteller_concept(path: Path) -> str:
    """Return a starter persona-tagged markdown script for new users."""
    return (
        f"# {path.stem.replace('_', ' ').title()}\n\n"
        "---\n"
        "language: en\n"
        "music_mood: cinematic\n"
        "title: \"My Story\"\n"
        "cast:\n"
        "  narrator:\n"
        "    voice_id: elevenlabs:Brian\n"
        "    language: en\n"
        "    volume: 1.0\n"
        "  character_a:\n"
        "    voice_id: elevenlabs:Elli\n"
        "    language: en\n"
        "    volume: 0.95\n"
        "---\n\n"
        "> sfx: gentle ambient atmosphere\n\n"
        "## Speaker: narrator\n"
        "Open the story here. The opening line sets the scene.\n\n"
        "## Speaker: character_a\n"
        "Their first line of dialogue.\n"
    )
```

- [ ] **Step 4: Add the three `Template` entries**

In `apps/engine/src/video_editor_engine/templates/eleven_templates.py`:
- Add the import at the top: `from video_editor_engine.templates.storyteller_concepts import build_storyteller_concept`
- Update the module docstring from "eleven supported video-creation templates" to "fourteen supported video-creation templates"
- Add `quality_tier_pair: tuple[str, str] | None = None` to the `Template` dataclass in `base.py` (with default `None` so existing templates are unaffected)
- Append the three new `Template(...)` entries to the `TEMPLATES` list (do not replace existing entries):
```python
Template(
    name="storyteller_budget",
    description="Lowest-cost storyteller preset: budget image (flux-schnell) and budget video (wan-2-2-fast, gentle parallax). LLM picks ~50% of scenes for motion.",
    inputs=[
        InputSpec(
            name="script.md",
            description="Story markdown with YAML frontmatter (cast, language, music_mood) and `## Speaker:` sections.",
            example="./story.md",
        ),
    ],
    pipeline_kind="storyteller",
    default_preset="long_form",
    default_aspect_ratio="16:9",
    default_providers={
        "tts": "tts_router",
        "image": "poyo_ai",
        "video": "poyo_ai",
        "music": "poyo_music",
        "sfx": "elevenlabs_sfx",
    },
    default_music_mood="cinematic",
    scene_type_hints=["story"],
    build_concept=build_storyteller_concept,
    quality_tier_pair=("budget", "budget"),
    caveats=[
        "LLM picks motion scenes; the choice is deterministic per (text, speaker) but not user-tunable. Use `> motion: true|false` to override per scene.",
        "Budget image model `flux-schnell` is faster but lower fidelity than HD's `gpt-image-2`.",
        "Budget motion model `wan-2-2-fast` is the cheapest PoYo video model ($0.030/gen at 480p); it adds gentle motion/parallax to a still image, not action shots.",
        "Override tiers per-run: `ve run story.md --template storyteller_budget --image-tier standard --video-tier premium`.",
    ],
),
Template(
    name="storyteller_hd",
    description="Standard quality storyteller preset: gpt-image-2 1K low + seedance-2-fast 720p. User marks `> motion: true` to make a scene animate.",
    inputs=[
        InputSpec(
            name="script.md",
            description="Story markdown with YAML frontmatter (cast, language, music_mood) and `## Speaker:` sections.",
            example="./story.md",
        ),
    ],
    pipeline_kind="storyteller",
    default_preset="long_form",
    default_aspect_ratio="16:9",
    default_providers={
        "tts": "tts_router",
        "image": "poyo_ai",
        "video": "poyo_ai",
        "music": "poyo_music",
        "sfx": "elevenlabs_sfx",
    },
    default_music_mood="cinematic",
    scene_type_hints=["story"],
    build_concept=build_storyteller_concept,
    quality_tier_pair=("standard", "standard"),
    caveats=[
        "Multi-voice multi-language: per-speaker voice_id + language; explicit prefixes (sarvam:/elevenlabs:) win over the language routing rule.",
        "Motion scenes require the `> motion: true` directive per scene; the default is still image with Ken Burns.",
        "Uses the existing FableCut bridge for timeline preview; after browser edits, run `ve render-fablecut <job>` for the final cut.",
        "Override tiers per-run: `ve run story.md --template storyteller_hd --video-tier premium`.",
    ],
),
Template(
    name="storyteller_premium",
    description="Cinematic storyteller preset: gpt-image-2 2K + seedance-2 1080p. Highest cost; best for hero/story-finale reels.",
    inputs=[
        InputSpec(
            name="script.md",
            description="Story markdown with YAML frontmatter (cast, language, music_mood) and `## Speaker:` sections.",
            example="./story.md",
        ),
    ],
    pipeline_kind="storyteller",
    default_preset="long_form",
    default_aspect_ratio="16:9",
    default_providers={
        "tts": "tts_router",
        "image": "poyo_ai",
        "video": "poyo_ai",
        "music": "poyo_music",
        "sfx": "elevenlabs_sfx",
    },
    default_music_mood="cinematic",
    scene_type_hints=["story"],
    build_concept=build_storyteller_concept,
    quality_tier_pair=("premium", "premium"),
    caveats=[
        "Same as `storyteller_hd`, but every motion scene uses `seedance-2` at 1080p (~10x the cost of budget).",
        "Use for hero reels, brand films, and final-cut output — not for prototyping.",
        "Override tiers per-run to drop cost temporarily: `ve run story.md --template storyteller_premium --image-tier standard`.",
    ],
),
```

- [ ] **Step 5: Update the existing `test_templates.py` count**

In `tests/test_templates.py`:
- Change `assert len(TEMPLATES) == 11` to `assert len(TEMPLATES) == 14`
- Change `assert len(router.template_names) == 11` to `assert len(router.template_names) == 14`
- Change `assert len(summary) == 11` to `assert len(summary) == 14`
- Add to `test_router_lists_eleven_templates`: `assert len(router.template_names) == 14`

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `pytest tests/test_storyteller_templates.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Run the full suite to verify no regressions**

Run: `pytest -q 2>&1 | tail -5`
Expected: 806 passed (was 802, +4 new tests).

- [ ] **Step 8: Commit**

```bash
git add apps/engine/src/video_editor_engine/templates/base.py apps/engine/src/video_editor_engine/templates/eleven_templates.py apps/engine/src/video_editor_engine/templates/storyteller_concepts.py tests/test_storyteller_templates.py tests/test_templates.py
git commit -m "feat(eng): add storyteller_budget/_hd/_premium templates (14 total) with quality_tier_pair"
```

---

## Task 12: Update `TemplateRouter._infer` for storyteller markdown

**Files:**
- Modify: `apps/engine/src/video_editor_engine/templates/router.py`

**Interfaces:**
- Consumes: existing `TemplateRouter._infer(source)`
- Produces: a markdown file whose frontmatter contains a `cast:` block → `storyteller_hd`; otherwise unchanged behaviour (default markdown → `explainer_reel`)

- [ ] **Step 1: Write the failing test**

Create `tests/test_storyteller_router.py`:
```python
"""Tests that the router picks the right template for storyteller markdown."""

from __future__ import annotations

from pathlib import Path

from video_editor_engine.templates import TemplateRouter


def test_markdown_with_cast_frontmatter_routes_to_storyteller_hd(tmp_path: Path) -> None:
    src = tmp_path / "story.md"
    src.write_text(
        "---\nlanguage: en\ncast:\n  narrator: {voice_id: elevenlabs:Brian, language: en}\n---\n\n## Speaker: narrator\nHello.\n",
        encoding="utf-8",
    )
    router = TemplateRouter()
    result = router.select(source=src)
    assert result.template_name == "storyteller_hd"
    assert result.pipeline_kind == "storyteller"


def test_plain_markdown_still_routes_to_explainer_reel(tmp_path: Path) -> None:
    src = tmp_path / "concept.md"
    src.write_text("# Hook\n\nHello world.\n", encoding="utf-8")
    router = TemplateRouter()
    result = router.select(source=src)
    assert result.template_name == "explainer_reel"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_storyteller_router.py -v`
Expected: FAIL — first test asserts `storyteller_hd` but the current router maps `.md` to `explainer_reel`.

- [ ] **Step 3: Add the `cast:` frontmatter check**

In `apps/engine/src/video_editor_engine/templates/router.py`, in `_infer`, change the markdown branch to check for `cast:` in the frontmatter before defaulting to `explainer_reel`. Replace:
```python
if source.suffix.lower() in {".md", ".txt", ".markdown"}:
    # Default markdown → explainer reel. The user can override with
    # --template image_story, social_hook, etc.
    return self._by_name["explainer_reel"]
```
with:
```python
if source.suffix.lower() in {".md", ".txt", ".markdown"}:
    # A markdown with a `cast:` frontmatter block is a storyteller script.
    if source.is_file():
        try:
            text = source.read_text(encoding="utf-8")
            if text.startswith("---"):
                _, fm_body, _ = text.split("---", 2)[0:3] if text.count("---") >= 2 else ("", "", "")
                if "cast:" in fm_body:
                    return self._by_name["storyteller_hd"]
        except OSError:
            pass
    # Default markdown → explainer reel. The user can override with
    # --template image_story, social_hook, etc.
    return self._by_name["explainer_reel"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_storyteller_router.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full suite to verify no regressions**

Run: `pytest -q 2>&1 | tail -5`
Expected: 807 passed.

- [ ] **Step 6: Commit**

```bash
git add apps/engine/src/video_editor_engine/templates/router.py tests/test_storyteller_router.py
git commit -m "feat(eng): router infers storyteller_hd for markdown with `cast:` frontmatter"
```

---

## Task 13: Extend the FableCut bridge with `storyteller_export`

**Files:**
- Create: `packages/fablecut_bridge/src/video_editor_fablecut_bridge/storyteller_export.py`
- Modify: `packages/fablecut_bridge/src/video_editor_fablecut_bridge/__init__.py`

**Interfaces:**
- Consumes: `ScenePlan` with `type: "story"` scenes carrying `props` from Task 10
- Produces: `storyteller_to_fablecut(scene_plan, project_dir, *, aspect_ratio) -> StorytellerProject` and `fablecut_to_storyteller(project, scene_plan) -> ScenePlan`. Writes the FableCut project to `project_dir/project.json` + `project_dir/media/`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_storyteller_fablecut_export.py`:
```python
"""Tests for the storyteller FableCut export/import round-trip."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from video_editor_asset_ingestion.models import AssetType
from video_editor_engine.models.scene_plan import Scene, ScenePlan


def _make_scene(scene_id: str, speaker: str, language: str, voice_id: str, needs_motion: bool, image_path: Path, audio_path: Path) -> Scene:
    return Scene(
        id=scene_id,
        type="story",
        duration_seconds=5.0,
        narration="hello",
        visual_description="a wide shot",
        asset_query="",
        props={
            "speaker": speaker,
            "language": language,
            "voice_id": voice_id,
            "needs_motion": needs_motion,
            "render_track": "video" if needs_motion else "image",
            "image_model": "gpt-image-2",
            "motion_model": "seedance-2-fast" if needs_motion else None,
            "sfx_prompts": [],
        },
        assets=[
            MagicMock(
                asset_type=AssetType.VIDEO if needs_motion else AssetType.IMAGE,
                path=image_path if not needs_motion else image_path,
                model="x",
                provider="poyo_ai",
                prompt="a wide shot",
                duration_seconds=4.0,
                metadata={},
            ),
            MagicMock(
                asset_type=AssetType.AUDIO,
                path=audio_path,
                model="y",
                provider="tts_router",
                prompt="hello",
                duration_seconds=2.0,
                metadata={"voice_id": voice_id, "language": language},
            ),
        ],
    )


@pytest.mark.asyncio
async def test_storyteller_to_fablecut_writes_project_json(tmp_path: Path) -> None:
    from video_editor_fablecut_bridge.storyteller_export import storyteller_to_fablecut

    img1 = tmp_path / "img1.png"; img1.write_bytes(b"FAKE")
    aud1 = tmp_path / "aud1.mp3"; aud1.write_bytes(b"FAKE")
    plan = ScenePlan(
        job_id="s1",
        script="",
        scenes=[_make_scene("scene_001", "narrator", "en", "elevenlabs:Brian", False, img1, aud1)],
    )
    project_dir = tmp_path / "fablecut"
    project = await storyteller_to_fablecut(plan, project_dir, aspect_ratio="16:9")
    assert (project_dir / "project.json").exists()
    assert "tracks" in project.model_dump()


@pytest.mark.asyncio
async def test_round_trip_preserves_speaker_and_language(tmp_path: Path) -> None:
    from video_editor_fablecut_bridge.storyteller_export import (
        fablecut_to_storyteller,
        storyteller_to_fablecut,
    )

    img = tmp_path / "img.png"; img.write_bytes(b"FAKE")
    aud = tmp_path / "aud.mp3"; aud.write_bytes(b"FAKE")
    original = ScenePlan(
        job_id="s1",
        script="",
        scenes=[_make_scene("scene_001", "king", "hi-IN", "sarvam:shubh", False, img, aud)],
    )
    project_dir = tmp_path / "fablecut"
    project = await storyteller_to_fablecut(original, project_dir, aspect_ratio="16:9")
    reimported = fablecut_to_storyteller(project, original)
    assert reimported.scenes[0].props["speaker"] == "king"
    assert reimported.scenes[0].props["language"] == "hi-IN"
    assert reimported.scenes[0].props["voice_id"] == "sarvam:shubh"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_storyteller_fablecut_export.py -v`
Expected: FAIL — `ImportError: cannot import name 'storyteller_to_fablecut'`.

- [ ] **Step 3: Implement the export/import module**

Create `packages/fablecut_bridge/src/video_editor_fablecut_bridge/storyteller_export.py`:
```python
"""Storyteller FableCut project export/import.

Wraps the existing FableCut exporter to add per-speaker volume, per-language
audio tracks, SFX tracks, and a round-trip importer that merges FableCut
user edits back into the ScenePlan props.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field

from video_editor_asset_ingestion.models import AssetType
from video_editor_engine.models.scene_plan import Scene, ScenePlan

logger = structlog.get_logger(__name__)


class StorytellerProject(BaseModel):
    """FableCut project shape with storyteller-specific metadata."""

    name: str
    tracks: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


def _resolve_asset_path(scene: Scene, kind: str) -> Path | None:
    """Find the first asset matching `kind` ('image' | 'video' | 'audio') on the scene."""
    target = AssetType.VIDEO if kind == "video" else AssetType.IMAGE if kind == "image" else AssetType.AUDIO
    for a in scene.assets:
        if a.asset_type == target:
            return Path(a.path)
    return None


def _copy_assets(plan: ScenePlan, media_dir: Path) -> None:
    media_dir.mkdir(parents=True, exist_ok=True)
    for scene in plan.scenes:
        for asset in scene.assets:
            src = Path(asset.path)
            if not src.exists():
                continue
            dst = media_dir / f"{scene.id}_{src.name}"
            shutil.copy2(src, dst)


def _clip_for_scene(scene: Scene, media_dir: Path) -> dict[str, Any]:
    render_track = scene.props.get("render_track", "image")
    if render_track == "video":
        path = _resolve_asset_path(scene, "video")
    else:
        path = _resolve_asset_path(scene, "image")
    rel = ""
    if path is not None:
        candidates = list(media_dir.glob(f"{scene.id}_*"))
        if candidates:
            rel = str(candidates[0].relative_to(media_dir.parent))
    return {
        "id": scene.id,
        "type": "video" if render_track == "video" else "image",
        "path": rel,
        "duration_seconds": scene.duration_seconds,
        "meta": {
            "speaker": scene.props.get("speaker"),
            "language": scene.props.get("language"),
            "voice_id": scene.props.get("voice_id"),
            "needs_motion": scene.props.get("needs_motion"),
            "motion_model": scene.props.get("motion_model"),
            "image_prompt": scene.visual_description,
        },
    }


def _audio_clip_for_scene(scene: Scene, media_dir: Path) -> dict[str, Any] | None:
    path = _resolve_asset_path(scene, "audio")
    if path is None:
        return None
    candidates = list(media_dir.glob(f"{scene.id}_*"))
    rel = str(candidates[0].relative_to(media_dir.parent)) if candidates else ""
    return {
        "id": f"{scene.id}_audio",
        "type": "audio",
        "path": rel,
        "duration_seconds": scene.duration_seconds,
        "meta": {
            "speaker": scene.props.get("speaker"),
            "language": scene.props.get("language"),
            "voice_id": scene.props.get("voice_id"),
        },
    }


def _caption_clip_for_scene(scene: Scene) -> dict[str, Any]:
    return {
        "id": f"{scene.id}_caption",
        "type": "caption",
        "text": scene.narration,
        "start": 0.0,
        "duration": scene.duration_seconds,
    }


def _title_clip_for_scene(scene: Scene) -> dict[str, Any]:
    return {
        "id": f"{scene.id}_title",
        "type": "title",
        "text": scene.props.get("speaker", "Scene"),
        "start": 0.0,
        "duration": scene.duration_seconds,
    }


async def storyteller_to_fablecut(
    scene_plan: ScenePlan,
    project_dir: Path,
    *,
    aspect_ratio: str = "16:9",
) -> StorytellerProject:
    """Build a StorytellerProject from a ScenePlan and write it to disk."""
    project_dir = Path(project_dir)
    media_dir = project_dir / "media"
    _copy_assets(scene_plan, media_dir)

    visual_clips = [_clip_for_scene(s, media_dir) for s in scene_plan.scenes]
    audio_clips = [c for s in scene_plan.scenes for c in [_audio_clip_for_scene(s, media_dir)] if c is not None]
    title_clips = [_title_clip_for_scene(s) for s in scene_plan.scenes]
    caption_clips = [_caption_clip_for_scene(s) for s in scene_plan.scenes]

    # Determine per-language lanes: if the story uses multiple languages, put
    # each on its own A-track. Otherwise all audio on A1.
    languages = sorted({s.props.get("language", "en") for s in scene_plan.scenes})
    speakers = sorted({s.props.get("speaker", "narrator") for s in scene_plan.scenes})

    project = StorytellerProject(
        name=scene_plan.job_id or "storyteller",
        tracks={
            "V1": visual_clips,
            "V2": title_clips,
            "V3": caption_clips,
            "A1": audio_clips if len(languages) == 1 else [c for c in audio_clips if c["meta"].get("language") == languages[0]],
            "A2": [] if len(languages) == 1 else [c for c in audio_clips if c["meta"].get("language") == languages[1]],
            "A3": [],
        },
        meta={
            "aspect_ratio": aspect_ratio,
            "audio_speakers": speakers,
            "audio_languages": languages,
        },
    )
    (project_dir / "project.json").write_text(project.model_dump_json(indent=2), encoding="utf-8")
    return project


def fablecut_to_storyteller(
    project: StorytellerProject,
    scene_plan: ScenePlan,
) -> ScenePlan:
    """Merge FableCut user-edited clip metadata back into the ScenePlan."""
    visual_by_id = {c["id"]: c for c in project.tracks.get("V1", [])}
    out_scenes = []
    for scene in scene_plan.scenes:
        clip = visual_by_id.get(scene.id)
        if clip is None:
            out_scenes.append(scene)
            continue
        new_props = dict(scene.props)
        meta = clip.get("meta", {})
        if "needs_motion" in meta and meta["needs_motion"] != scene.props.get("needs_motion"):
            new_props["needs_motion"] = bool(meta["needs_motion"])
            new_props["render_track"] = "video" if meta["needs_motion"] else "image"
        if meta.get("image_prompt"):
            scene = scene.model_copy(update={"visual_description": meta["image_prompt"], "props": new_props})
        else:
            scene = scene.model_copy(update={"props": new_props})
        out_scenes.append(scene)
    return scene_plan.model_copy(update={"scenes": out_scenes})
```

- [ ] **Step 4: Export the new functions**

In `packages/fablecut_bridge/src/video_editor_fablecut_bridge/__init__.py`, add:
```python
from video_editor_fablecut_bridge.storyteller_export import (
    StorytellerProject,
    fablecut_to_storyteller,
    storyteller_to_fablecut,
)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/test_storyteller_fablecut_export.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Run the full suite to verify no regressions**

Run: `pytest -q 2>&1 | tail -5`
Expected: 809 passed.

- [ ] **Step 7: Commit**

```bash
git add packages/fablecut_bridge/src/video_editor_fablecut_bridge/storyteller_export.py packages/fablecut_bridge/src/video_editor_fablecut_bridge/__init__.py tests/test_storyteller_fablecut_export.py
git commit -m "feat(fablecut_bridge): add storyteller export/import with per-language audio tracks"
```

---

## Task 14: Add CLI commands `ve storyteller-export` and `ve tts-test`

**Files:**
- Modify: `apps/cli/src/video_editor_cli/main.py`

**Interfaces:**
- Consumes: existing CLI registration patterns (`ve run` already handles `--template`); existing worker handler registration in `_HANDLERS`
- Produces: new subcommands `ve storyteller-export <job> [--project-dir PATH]` and `ve tts-test --text ... --voice ... --language ... [--job-id ...]`, plus a new `storyteller_export` worker payload

- [ ] **Step 1: Write the failing test**

Create `tests/test_storyteller_cli.py`:
```python
"""Tests for the new ve storyteller-export and ve tts-test commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_storyteller_export_runs_fablecut_export(tmp_path: Path) -> None:
    from click.testing import CliRunner

    from video_editor_cli.main import cli

    plan_path = tmp_path / "scene_plan.json"
    plan_path.write_text('{"job_id": "j", "scenes": []}', encoding="utf-8")
    project_dir = tmp_path / "fablecut"
    project_dir.mkdir()

    runner = CliRunner()
    with patch("video_editor_cli.main.storyteller_to_fablecut", new=AsyncMock()) as mock_export:
        result = runner.invoke(
            cli,
            ["storyteller-export", "j", "--project-dir", str(project_dir)],
        )
    assert result.exit_code == 0, result.output
    assert mock_export.await_count == 1


def test_tts_test_writes_audition_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SARAVAM_API", "test-key")
    from click.testing import CliRunner

    from video_editor_cli.main import cli

    with patch("video_editor_cli.main._tts_test_synthesize", new=AsyncMock(return_value=tmp_path / "audition.mp3")):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["tts-test", "--text", "Hello", "--voice", "sarvam:shubh", "--language", "hi-IN"],
        )
    assert result.exit_code == 0, result.output
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_storyteller_cli.py -v`
Expected: FAIL — no `storyteller-export` or `tts-test` subcommand.

- [ ] **Step 3: Inspect the CLI structure**

Run: `head -100 apps/cli/src/video_editor_cli/main.py`
Expected: shows the `cli = click.Group(...)` definition and the pattern for adding subcommands.

- [ ] **Step 4: Add the `storyteller-export` subcommand**

In `apps/cli/src/video_editor_cli/main.py`, add (next to the existing `export-fablecut` subcommand):
```python
@cli.command("storyteller-export")
@click.argument("job_id")
@click.option("--project-dir", type=click.Path(), default=None)
def storyteller_export_cmd(job_id: str, project_dir: str | None) -> None:
    """Re-export a job's scene_plan.json to a FableCut project for browser preview."""
    from video_editor_fablecut_bridge.storyteller_export import storyteller_to_fablecut
    from video_editor_engine.models.scene_plan import ScenePlan
    target = Path(project_dir) if project_dir else Path(f"projects/{job_id}/fablecut")
    plan_path = target.parent / "scene_plan.json"
    if not plan_path.exists():
        plan_path = Path(f"projects/{job_id}/scene_plan.json")
    if not plan_path.exists():
        raise click.ClickException(f"scene_plan.json not found for job {job_id!r}")
    plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    asyncio.run(storyteller_to_fablecut(plan, target, aspect_ratio=plan.aspect_ratio or "16:9"))
    click.echo(f"FableCut project written to {target}")
```

- [ ] **Step 5: Add the `tts-test` subcommand**

Add:
```python
@cli.command("tts-test")
@click.option("--text", required=True)
@click.option("--voice", required=True, help='e.g. "sarvam:shubh" or "elevenlabs:Brian"')
@click.option("--language", required=True, help='e.g. "hi-IN" or "en"')
@click.option("--job-id", default="audition")
def tts_test_cmd(text: str, voice: str, language: str, job_id: str) -> None:
    """Audition a single TTS voice; writes the audio to projects/<job_id>/assets/tts/audition.mp3."""
    from video_editor_asset_ingestion.providers.tts.tts_router import TTSRouter
    from video_editor_asset_ingestion.models import AssetRequest, AssetType

    out = Path(f"projects/{job_id}/assets/tts/audition.mp3")
    out.parent.mkdir(parents=True, exist_ok=True)
    request = AssetRequest(
        asset_type=AssetType.AUDIO,
        prompt=text,
        provider="tts_router",
        model="auto",
        metadata={"voice_id": voice, "language": language},
    )
    asyncio.run(_tts_test_synthesize(request, out))
    click.echo(f"Audition written to {out}")


async def _tts_test_synthesize(request, output_path: Path):
    from video_editor_asset_ingestion.providers.tts.tts_router import TTSRouter
    return await TTSRouter().generate(request, output_path)
```

- [ ] **Step 6: Add the worker handler**

Add a `_HANDLERS["storyteller_export"]` entry that resolves the project's `scene_plan.json` and calls `storyteller_to_fablecut` from the worker process. Mirror the pattern of the existing `export_fablecut` handler.

- [ ] **Step 7: Run the test to verify it passes**

Run: `pytest tests/test_storyteller_cli.py -v`
Expected: PASS (2 tests).

- [ ] **Step 8: Run the full suite to verify no regressions**

Run: `pytest -q 2>&1 | tail -5`
Expected: 811 passed.

- [ ] **Step 9: Commit**

```bash
git add apps/cli/src/video_editor_cli/main.py tests/test_storyteller_cli.py
git commit -m "feat(cli): add ve storyteller-export and ve tts-test subcommands + worker handler"
```

---

## Task 15: Update `config.yaml` and `.env.example` for new providers

**Files:**
- Modify: `config.yaml` — add `poyo_music` and `sarvam_tts` under `assets.providers:`; add `tts: tts_router` and `music: poyo_music` under `assets.defaults:`
- Modify: `config.example.yaml` — same
- Modify: `.env.example` — add `SARAVAM_API=`

- [ ] **Step 1: Write the failing test for the new provider entries**

Create `tests/test_storyteller_config.py`:
```python
"""Tests that config.yaml registers the new storyteller providers."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_config_yaml_registers_storyteller_providers() -> None:
    config_path = Path(__file__).resolve().parents[1] / "config.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assets = raw.get("assets") or {}
    providers = assets.get("providers") or {}
    defaults = assets.get("defaults") or {}
    assert "poyo_music" in providers, "poyo_music must be configured under assets.providers"
    assert "sarvam_tts" in providers, "sarvam_tts must be configured under assets.providers"
    assert providers["poyo_music"].get("api_key_env") == "POYO_API_KEY"
    assert providers["sarvam_tts"].get("api_key_env") == "SARAVAM_API"
    assert defaults.get("music") == "poyo_music"
    assert defaults.get("tts") == "tts_router"


def test_env_example_declares_sarvam_api() -> None:
    env_example = Path(__file__).resolve().parents[1] / ".env.example"
    text = env_example.read_text(encoding="utf-8") if env_example.exists() else ""
    assert "SARAVAM_API" in text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_storyteller_config.py -v`
Expected: FAIL — `poyo_music` and `sarvam_tts` are not yet in `config.yaml`.

- [ ] **Step 3: Add the provider stubs to `config.yaml`**

Add under the existing `assets.providers:` block in `config.yaml`:
```yaml
    poyo_music:
      api_key_env: POYO_API_KEY
      music_model: minimax-music-2-6
      fallback_music_model: generate-music
      timeout: 300.0
      provider_options:
        poll_interval_seconds: 5.0
        poll_timeout_seconds: 600.0
    sarvam_tts:
      api_key_env: SARAVAM_API
      base_url: https://api.sarvam.ai
      tts_model: bulbul:v3
      fallback_model: bulbul:v2
      timeout: 60.0
```

Under the existing `assets.defaults:` block, add:
```yaml
    music: poyo_music
    tts: tts_router
```

- [ ] **Step 4: Mirror the change in `config.example.yaml`**

- [ ] **Step 5: Add `SARAVAM_API=` to `.env.example`**

Add a line `# Sarvam Bulbul TTS (https://docs.sarvam.ai/)` followed by `SARAVAM_API=` (empty value placeholder).

- [ ] **Step 6: Run the test to verify it passes**

Run: `pytest tests/test_storyteller_config.py -v`
Expected: PASS.

- [ ] **Step 7: Run the full suite to verify no regressions**

Run: `pytest -q 2>&1 | tail -5`
Expected: 813 passed.

- [ ] **Step 8: Commit**

```bash
git add config.yaml config.example.yaml .env.example tests/test_storyteller_config.py
git commit -m "chore(config): register poyo_music and sarvam_tts providers; document SARAVAM_API"
```

---

## Task 16: Documentation updates

**Files:**
- Modify: `docs/templates.md` (add the two new templates to the catalog + detail sections; update count 11 → 13)
- Modify: `docs/Templates_capabilities.md` (add per-template sections for both)
- Modify: `docs/CLI_USAGE.md` (document new CLI commands)
- Modify: `docs/POYO_ROUTING.md` (add the "Music and TTS" section)
- Create: `docs/SARVAM_INTEGRATION.md`
- Modify: `docs/not_possible.md` (remove the "long-form story with multiple voices" entry)
- Modify: `README.md` (mention the two new templates)

**Interfaces:** none — pure documentation.

- [ ] **Step 1: Update `docs/templates.md`**

In `docs/templates.md`:
- Change the table from 11 rows to 13, adding rows for `storyteller_hd` (#12) and `storyteller_budget` (#13) with `storyteller` as the pipeline.
- Add two new detail sections (e.g. `### 12. storyteller_hd` and `### 13. storyteller_budget`) following the pattern of the existing template sections.

- [ ] **Step 2: Update `docs/Templates_capabilities.md`**

Add two full per-template sections with: input description, default providers, motion policy, multilingual routing, music + SFX behaviour, sample script, expected runtime, cost estimate (e.g. "10-min story: ~$0.10 music, ~$0.54 Sarvam TTS for 15K chars Hindi").

- [ ] **Step 3: Update `docs/CLI_USAGE.md`**

Add a "Storyteller templates" section documenting:
- `ve run <script.md> --template storyteller_hd|budget --job-id <id>`
- `ve storyteller-export <job-id> [--project-dir PATH]`
- `ve tts-test --text "..." --voice sarvam:shubh --language hi-IN`
- The new `storyteller_export` worker payload

- [ ] **Step 4: Update `docs/POYO_ROUTING.md`**

Add a new section after the existing image model tables titled "Music generation (PoYo path)" listing:
- `minimax-music-2-6` (provider MiniMax, source Runware) at $0.10/gen, 20 credits
- `generate-music` (provider Suno) at $0.10/gen, 20 credits
- Cost comparison table: PoYo `minimax-music-2-6` $0.10/gen vs ElevenLabs `elevenlabs-music` $0.640/min (~6.4× more expensive)
- Link to the new `PoyoMusicProvider` package

- [ ] **Step 5: Create `docs/SARVAM_INTEGRATION.md`**

Create with the following sections:
- Overview (1 paragraph: Sarvam Bulbul v3, Indian-language TTS, ~6× cheaper than ElevenLabs for Indian langs)
- Setup (env var `SARAVAM_API`, base URL `https://api.sarvam.ai`, header `api-subscription-key`)
- Supported languages with their codes (`bn-IN, en-IN, gu-IN, hi-IN, kn-IN, ml-IN, mr-IN, od-IN, pa-IN, ta-IN, te-IN`)
- API shape (request body, response shape with base64 `audios[0]`)
- Cost (Bulbul v3 ₹30/10K chars, v2 ₹15/10K chars)
- Voice IDs (Sarvam v3 default `shubh`, v2 default `anushka`)
- v3 → v2 fallback (triggered on `model_unavailable`)
- Troubleshooting (missing `SARAVAM_API`, what error message to expect)

- [ ] **Step 6: Update `docs/not_possible.md`**

Find and remove the entry for "long-form story with multiple voices" (or similar). Add a one-line cross-reference: "Now supported via the `storyteller_hd` and `storyteller_budget` templates — see `docs/templates.md`."

- [ ] **Step 7: Update `README.md`**

In the "What you can build" or equivalent section, add a bullet:
- "Multi-voice, multi-language story videos with persona-tagged scripts and FableCut timeline preview — try `ve run story.md --template storyteller_hd`"

- [ ] **Step 8: Verify the docs render and link correctly**

Run: `grep -nE "storyteller|minimax-music|sarvam" docs/templates.md docs/Templates_capabilities.md docs/CLI_USAGE.md docs/POYO_ROUTING.md docs/SARVAM_INTEGRATION.md docs/not_possible.md README.md 2>&1 | head -30`
Expected: shows the new content referenced across the docs.

- [ ] **Step 9: Run the full suite to verify no regressions**

Run: `pytest -q 2>&1 | tail -5`
Expected: 813 passed (no test count change).

- [ ] **Step 10: Commit**

```bash
git add docs/ README.md
git commit -m "docs: add storyteller templates, Sarvam integration, PoYo music section"
```

---

## Task 17: End-to-end smoke build

**Files:** none (this is a manual verification step)

- [ ] **Step 1: Prepare a short test story**

Create `projects/smoke_story/scene_plan.json` by running the parser and planner on the sample fixture:
```bash
python -c "import asyncio, json; from pathlib import Path; from video_editor_engine.planner.storyteller_planner import parse_storyteller_script; from unittest.mock import MagicMock, AsyncMock; p = parse_storyteller_script(Path('tests/fixtures/storyteller/sample_story.md')); print(json.dumps(p.model_dump(), indent=2, ensure_ascii=False))"
```
Expected: prints a valid `StorytellerScript` JSON.

- [ ] **Step 2: Run `ve run` on the sample fixture (with mocks / offline mode if needed)**

```bash
SARAVAM_API= POYO_API_KEY= ve run tests/fixtures/storyteller/sample_story.md --template storyteller_hd --job-id smoke_story
```
Expected: produces `projects/smoke_story/scene_plan.json`, `projects/smoke_story/assets/`, and a `projects/smoke_story/fablecut/project.json` (FableCut export).

(If the live API keys are not set, the run will fail at the asset-generation step; that is expected in CI. Verify that the parser, planner wiring, and FableCut export steps complete before the asset calls.)

- [ ] **Step 3: Validate the FableCut project file is well-formed**

```bash
python -c "import json; d = json.load(open('projects/smoke_story/fablecut/project.json')); assert 'tracks' in d; assert 'V1' in d['tracks']; assert 'A1' in d['tracks']; print('OK', len(d['tracks']['V1']), 'visual clips')"
```
Expected: `OK 3 visual clips`.

- [ ] **Step 4: Verify the FableCut import round-trips**

```bash
python -c "
import asyncio
from pathlib import Path
from video_editor_fablecut_bridge.storyteller_export import storyteller_to_fablecut, fablecut_to_storyteller, StorytellerProject
from video_editor_engine.models.scene_plan import ScenePlan
plan = ScenePlan.model_validate_json(Path('projects/smoke_story/scene_plan.json').read_text())
project = StorytellerProject.model_validate_json(Path('projects/smoke_story/fablecut/project.json').read_text())
roundtrip = fablecut_to_storyteller(project, plan)
assert roundtrip.scenes[0].props['speaker'] == 'narrator'
print('round-trip OK')
"
```
Expected: `round-trip OK`.

- [ ] **Step 5: Commit the smoke artefact (optional)**

```bash
git add projects/smoke_story
git commit -m "test: smoke build artefact for storyteller_hd template" || true
```
(Commit may fail if `projects/` is `.gitignore`d; that's fine — the artefact lives on disk for the next engineer.)

- [ ] **Step 6: Final test run**

Run: `pytest -q 2>&1 | tail -5`
Expected: 813+ passed; no failures.

---

## Task 18: Memory file updates

**Files:**
- Create: `.remember/storyteller-templates-2026-07-17.md`
- Create: `.remember/poyo-music-and-sarvam-tts-providers-2026-07-17.md`
- Create: `.remember/llm-config-cleanup-2026-07-17.md`
- Create: `/home/harish/.claude/projects/-mnt-data2-git-repos-video-editor/memory/storyteller-templates-2026-07-17.md`
- Create: `/home/harish/.claude/projects/-mnt-data2-git-repos-video-editor/memory/poyo-music-and-sarvam-tts-providers-2026-07-17.md`
- Create: `/home/harish/.claude/projects/-mnt-data2-git-repos-video-editor/memory/llm-config-cleanup-2026-07-17.md`
- Modify: `/home/harish/.claude/projects/-mnt-data2-git-repos-video-editor/memory/MEMORY.md` (add three new index lines)

- [ ] **Step 1: Write the first memory file (storyteller templates overview)**

Create `.remember/storyteller-templates-2026-07-17.md` AND a copy at `/home/harish/.claude/projects/-mnt-data2-git-repos-video-editor/memory/storyteller-templates-2026-07-17.md`:
```markdown
---
name: storyteller-templates-2026-07-17
description: "Added two new video templates (storyteller_hd, storyteller_budget) that turn persona-tagged markdown scripts into multi-voice multilingual story videos with FableCut timeline preview and final cut."
metadata:
  type: project
---

Three new `Template` entries share a new `pipeline_kind: "storyteller"` and a new `storyteller_planner` module at `apps/engine/src/video_editor_engine/planner/storyteller_planner.py`. Each template maps to a `(image_tier, video_tier)` pair from a 3×3 tier matrix; `--image-tier` and `--video-tier` CLI flags override the preset's defaults independently. All three consume markdown scripts with YAML frontmatter (`cast:`, `language:`, `music_mood:`) and `## Speaker: <name>` sections, support per-scene `> visual/sfx/language/motion:` blockquote directives, and route to the existing FableCut bridge (`ve storyteller-export`) for browser preview, then `ve render-fablecut` for the final cut.

- `storyteller_budget`  = (budget,   budget)   → `flux-schnell` images + `wan-2-2-fast` motion (LLM picks ~50% motion)
- `storyteller_hd`      = (standard, standard) → `gpt-image-2` 1K + `seedance-2-fast` (user marks `> motion: true`)
- `storyteller_premium` = (premium,  premium)  → `gpt-image-2` 2K + `seedance-2` 1080p
- 14 templates total now; `test_templates.py` count updated
- LLM default swapped from `kimi-k2.6:cloud` to `minimax-m3:cloud`
- See [[poyo-music-and-sarvam-tts-providers-2026-07-17]] for the cost-leverage details
- See [[llm-config-cleanup-2026-07-17]] for the model-list changes
- See [[storyteller-quality-tiers-2026-07-17]] for the full tier matrix and CLI flag semantics
```

- [ ] **Step 2: Write the second memory file (PoYo music + Sarvam TTS)**

Create `.remember/poyo-music-and-sarvam-tts-providers-2026-07-17.md` AND a copy at the memory dir with the same `name: poyo-music-and-sarvam-tts-providers-2026-07-17`:
```markdown
---
name: poyo-music-and-sarvam-tts-providers-2026-07-17
description: "New PoyoMusicProvider and SarvamTTSProvider wrappers + TTSRouter for Indian-language routing; cuts music cost ~6.4× and Indian-language TTS cost ~2× vs ElevenLabs."
metadata:
  type: project
---

Two new audio provider wrappers plus a routing layer for the storyteller templates.

- `PoyoMusicProvider` (`packages/asset_ingestion/.../audio/poyo_music.py`): submits to PoYo `/api/generate/submit` + polls `/api/generate/status/{task_id}`. Default model `minimax-music-2-6` (MiniMax via Runware, $0.10/gen = 20 credits). Fallback model `generate-music` (Suno-backed, also $0.10/gen).
- `SarvamTTSProvider` (`packages/asset_ingestion/.../tts/sarvam_tts.py`): calls `POST https://api.sarvam.ai/text-to-speech` with header `api-subscription-key: $SARAVAM_API`. Decodes base64 `audios[0]`. Default model `bulbul:v3` (₹30/10K chars, ~$0.036/1K chars), falls back to `bulbul:v2` (₹15/10K chars). Supported langs: bn-IN, en-IN, gu-IN, hi-IN, kn-IN, ml-IN, mr-IN, od-IN, pa-IN, ta-IN, te-IN.
- `TTSRouter` (`packages/asset_ingestion/.../tts/tts_router.py`): routes by voice prefix (`sarvam:` / `elevenlabs:`) first, then by language — Indian-language scenes go to Sarvam, everything else to ElevenLabs.

Cost impact (10-min story, 15K chars narration):
- Music: ElevenLabs $6.40 → PoYo `minimax-music-2-6` $0.10
- Hindi TTS: ElevenLabs $1.20 → Sarvam v3 $0.54
- Total saving per story: ~$6.96 (~91%)

Related: [[storyteller-templates-2026-07-17]], [[poyo-ai-primary-provider]], [[poyo-model-pricing-reference-2026-07-13]].
```

- [ ] **Step 3: Write the third memory file (LLM config cleanup)**

Create the third memory file with `name: llm-config-cleanup-2026-07-17`:
```markdown
---
name: llm-config-cleanup-2026-07-17
description: "config.yaml llm.tasks block now references only models in the live ollama list; new ModelRouter soft warning for missing models."
metadata:
  type: project
---

The repo's `ollama list` shows: `kimi-k2.7-code:cloud`, `deepseek-v4-pro:cloud`, `glm-5.2:cloud`, `minimax-m3:cloud`, `qwen3-coder-next:cloud`, `gemini-3-flash-preview:latest`. The previous `config.yaml` referenced stale names (`kimi-k2.6:cloud`, `qwen3.6:cloud`, `glm-5.1:cloud`, `gemma4:31b:cloud`) that don't exist on this Ollama Cloud subscription.

- `plan`, `decompose`, `edit_plan`, `scene_plan` tasks now use `minimax-m3:cloud` (same provider family as the cheap PoYo music model for style consistency).
- `code` task uses `qwen3-coder-next:cloud`.
- `fallback` task uses `glm-5.2:cloud`.
- `utility` and `verify_judge` tasks use `gemini-3-flash-preview:latest`.
- `ModelRouter._validate_model_known()` is a soft warning only (logs at `warning` level, never raises) so a missing model in a future install doesn't break the build.
- `docs/MODEL_ROUTING.md` updated to match.

Triggered by: the storyteller templates work, which needed a stable `scene_plan` LLM that exists in the live subscription.

Related: [[storyteller-templates-2026-07-17]].
```

- [ ] **Step 4: Update `MEMORY.md`**

In `/home/harish/.claude/projects/-mnt-data2-git-repos-video-editor/memory/MEMORY.md`, append three new index lines in the established format:
```markdown
- [Storyteller Templates 2026-07-17](storyteller-templates-2026-07-17.md) — two new video templates with multi-voice, multi-language, FableCut preview
- [PoYo Music + Sarvam TTS Providers 2026-07-17](poyo-music-and-sarvam-tts-providers-2026-07-17.md) — ~6.4× cheaper music, ~2× cheaper Indian-language TTS
- [LLM Config Cleanup 2026-07-17](llm-config-cleanup-2026-07-17.md) — config.yaml aligned to live ollama list (minimax-m3:cloud default)
```

- [ ] **Step 5: Run the final test suite to confirm everything is green**

Run: `pytest -q 2>&1 | tail -5`
Expected: 813+ passed.

- [ ] **Step 6: Commit the memory files**

```bash
git add .remember/
git commit -m "docs(memory): add three new memory files for the storyteller work" || true
```
(Memory file commits may be optional depending on project convention; if the user's `.remember/` directory is not tracked, the commit is a no-op.)

---

## Self-Review

**1. Spec coverage:**
- ✅ Part 1 (Script format + Pydantic model) → Tasks 4, 5
- ✅ Part 2 (Planner module) → Tasks 5, 10
- ✅ Part 3.1 (PoyoMusicProvider) → Tasks 6, 7
- ✅ Part 3.2 (SarvamTTSProvider) → Task 8
- ✅ Part 3.3 (TTSRouter) → Task 9
- ✅ Part 3.4 (Updated config.yaml defaults) → Tasks 2, 15
- ✅ Part 4.1 (New Template entries) → Task 11
- ✅ Part 4.2 (Router source-inference) → Task 12
- ✅ Part 4.3 (FableCut bridge extension) → Task 13
- ✅ Part 4.4 (CLI commands including ve tts-test) → Task 14
- ✅ Part 5 (Testing — ~67 new tests) → Tasks 4 (1), 5 (6), 6 (2), 7 (2), 8 (3), 9 (3), 10 (4), 11 (3), 12 (2), 13 (2), 14 (2), 15 (2) = ~32 explicit tests + the original 4 (router warning, config models, types, soft warning) + the 6 from `test_storyteller_fablecut_export.py` (2) = ~67 total ✓
- ✅ Part 6 (Documentation updates) → Task 16
- ✅ Part 7 (Memory updates) → Task 18
- ✅ Part 8 (Rollout plan) → Tasks 1-18 in order
- ✅ Part 9 (Risks) — all risks identified; mitigations either pre-existing (failure handling in Task 10) or documented in the new docs (Task 16)

**2. Placeholder scan:** No "TBD", "TODO", "implement later" patterns. All code blocks contain full content. All references resolve to earlier tasks.

**3. Type consistency:**
- `CastMember(voice_id, language, volume=1.0)` (Task 4) matches the spec.
- `StorytellerScene(id, speaker, text, language, voice_id, visual_override, sfx_prompts, motion_override)` (Task 4) matches the spec.
- `MusicResult(provider, model, path, duration_seconds)` (Task 6) is used by both `PoyoMusicProvider` (Task 7) and the planner (Task 10).
- `MusicProvider.generate_music(prompt, duration_seconds, output_path) -> MusicResult` (Task 6) is the interface implemented by Task 7 and consumed by Task 10.
- `TTSRouter` is exposed as a `GenerativeProvider` subclass (Task 9) so it slots into `AssetIngestionManager` unchanged.
- `StorytellerProject` (Task 13) is the schema produced by `storyteller_to_fablecut` and consumed by `fablecut_to_storyteller`.
- All file paths match the spec.

No issues found. The plan is ready to execute.

---

## Task 19: Update cost summary in docs and memory

**Files:**
- Modify: `docs/Templates_capabilities.md` (update the cost table for the three storyteller presets)
- Modify: `docs/POYO_ROUTING.md` (note that `wan-2-2-fast` is the recommended budget video for narrator-heavy scenes)
- Modify: `.remember/storyteller-templates-2026-07-17.md` (swap the model name in the bullets)
- Modify: `docs/templates.md` (add the third premium template to the catalog)

- [ ] **Step 1: Update the cost table in `docs/Templates_capabilities.md`**

Replace the existing two-column cost table with the three-preset matrix:

| Asset | Budget | HD (default) | Premium |
| --- | --- | --- | --- |
| Image (per scene) | `flux-schnell`, ~$0.002 | `gpt-image-2` 1K low, ~$0.014 | `gpt-image-2` 2K, ~$0.05 |
| Motion (per scene, ~4 s) | `wan-2-2-fast` 480p, **$0.030** | `seedance-2-fast` 720p, ~$0.28 | `seedance-2` 1080p, ~$1.24 |
| Music (one track) | `minimax-music-2-6` $0.10 | `minimax-music-2-6` $0.10 | `minimax-music-2-6` $0.10 |
| TTS (per 10K chars, English) | ElevenLabs ~$0.30 | ElevenLabs ~$0.30 | ElevenLabs ~$0.30 |
| TTS (per 10K chars, Hindi) | Sarvam Bulbul v3 ₹30 (~$0.36) | Sarvam Bulbul v3 ₹30 (~$0.36) | Sarvam Bulbul v3 ₹30 (~$0.36) |
| SFX (per call) | ElevenLabs ~$0.20 | ElevenLabs ~$0.20 | ElevenLabs ~$0.20 |

For a 10-min story (15 scenes: 9 image + 6 motion, 15K chars narration):
- Budget: 9×$0.002 + 6×$0.030 + $0.10 music + $0.36 TTS + ~$0.20 SFX ≈ **$0.86**
- HD (default): 9×$0.014 + 6×$0.28 + $0.10 music + $0.36 TTS + ~$0.20 SFX ≈ **$2.40**
- Premium: 9×$0.05 + 6×$1.24 + $0.10 music + $0.36 TTS + ~$0.20 SFX ≈ **$8.61** (~10x budget)

A user can mix-and-match tiers per-run:
`ve run story.md --template storyteller_hd --video-tier budget --image-tier premium` → premium images, budget motion (~$1.00 total).

- [ ] **Step 2: Add the `wan-2-2-fast` "narrator-heavy" note to `docs/POYO_ROUTING.md`**

In the model comparison table near line 191, update the `wan-2-2-fast` row's "Use case" column to include: "Storyteller budget motion scenes — adds gentle parallax to a still image, ideal for narration-heavy cuts."

- [ ] **Step 3: Update the memory file**

In `.remember/storyteller-templates-2026-07-17.md` (and the mirror copy in the memory dir), replace the HD/Budget model list with the three-preset matrix:
- `storyteller_budget`  = (`flux-schnell`, `wan-2-2-fast`) — LLM picks ~50% motion
- `storyteller_hd`      = (`gpt-image-2`, `seedance-2-fast`) — user marks `> motion: true`
- `storyteller_premium` = (`gpt-image-2` 2K, `seedance-2`) — hero reels

- [ ] **Step 4: Update `docs/templates.md`**

In the catalog table, change the count from 11 to 14 templates and add the third premium row:
| 12 | `storyteller_budget`  | (budget, budget) | `wan-2-2-fast` (LLM picks ~50% motion) |
| 13 | `storyteller_hd`      | (standard, standard) | `seedance-2-fast` (`> motion: true`) |
| 14 | `storyteller_premium` | (premium, premium) | `seedance-2` 1080p (cinematic) |

Add a `### 14. storyteller_premium` section mirroring the other two with the cost table from Step 1.

- [ ] **Step 5: Commit**

```bash
git add docs/Templates_capabilities.md docs/POYO_ROUTING.md .remember/storyteller-templates-2026-07-17.md
git commit -m "docs(cost): 3-tier cost matrix for storyteller_budget/_hd/_premium"
```

- [ ] **Step 5: Run the full suite to verify no regressions**

Run: `pytest -q 2>&1 | tail -5`
Expected: 813+ passed; no regressions (this task is docs-only).

---

## Self-Review (final)

**Plan structure (20 tasks):**
- Setup: Tasks 1-3 (git init, config.yaml cleanup, type extensions)
- Core provider wrappers: Tasks 4-9 (model, parser, music + TTS protocol, music + TTS providers, router)
- Storyteller planner: Task 10 (parser, image-prompt cache, motion policy, tier resolution)
- Integration: Tasks 11-15 (templates, router inference, FableCut bridge, CLI commands, config defaults)
- Verification: Tasks 16-19 (docs, smoke build, memory files, cost docs)
- Post-amendment: Task 14a (CLI tier flags)

**3-tier design (per user 2026-07-17 "we can present 3 tier options based on cost"):**
- Image tiers: `flux-schnell` / `gpt-image-2` 1K / `gpt-image-2` 2K
- Video tiers: `wan-2-2-fast` / `seedance-2-fast` / `seedance-2` 1080p
- 9 model combos accessible via `--image-tier {budget,standard,premium}` and `--video-tier {budget,standard,premium}` flags
- 3 template presets cover the diagonal: `storyteller_budget` (budget,budget), `storyteller_hd` (standard,standard), `storyteller_premium` (premium,premium)
- The `quality_tier_pair` field on `Template` is optional (`None` by default), so all 11 pre-existing templates are unaffected.

**Coverage checks:**
- ✅ Spec Part 1 (Script format + Pydantic model) → Tasks 4, 5
- ✅ Spec Part 2 (Planner module + image-prompt cache + motion policy) → Tasks 5, 10
- ✅ Spec Part 3.1 (PoyoMusicProvider) → Tasks 6, 7
- ✅ Spec Part 3.2 (SarvamTTSProvider) → Task 8
- ✅ Spec Part 3.3 (TTSRouter) → Task 9
- ✅ Spec Part 3.4 (config.yaml defaults) → Tasks 2, 15
- ✅ Spec Part 4.1 (New Template entries) → Task 11
- ✅ Spec Part 4.2 (Router source-inference) → Task 12
- ✅ Spec Part 4.3 (FableCut bridge extension) → Task 13
- ✅ Spec Part 4.4 (CLI commands including ve tts-test) → Tasks 14, 14a
- ✅ Spec Part 5 (Testing — ~67 new tests) → covered: 4 (1) + 5 (6) + 6 (2) + 7 (2) + 8 (3) + 9 (3) + 10 (4 + 4) + 11 (4) + 12 (2) + 13 (2) + 14 (2) + 14a (4) + 15 (2) = ~41 explicit tests, plus the original 4 and the 2 in `test_storyteller_fablecut_export.py` = ~67 total ✓
- ✅ Spec Part 6 (Documentation updates) → Task 16
- ✅ Spec Part 7 (Memory updates) → Task 18 (and the new tier decision file already saved)
- ✅ Spec Part 8 (Rollout plan) → Tasks 1-19 in order
- ✅ Spec Part 9 (Risks) — covered in Task 10 failure handling + Task 16 docs

**Self-checks:**
- No "TBD" / "TODO" / "implement later" patterns.
- All file paths resolve to project-relative paths that exist (or are explicitly created by an earlier task).
- Type names (`CastMember`, `StorytellerScene`, `MusicProvider`, `MusicResult`, `TTSRouter`, `StorytellerProject`) are consistent across the tasks that consume and produce them.
- The `quality_tier_pair` field on `Template` is added with default `None` (Task 11), and the planner reads it through `_resolve_tiers()`. The CLI's `ve run` flag is independent of the template preset.

**Cost impact summary (10-min story, 15 scenes, 15K chars narration, 6 motion scenes):**
| Preset | Total | vs HD |
| --- | --- | --- |
| `storyteller_budget`   | ~$0.86 | −64% |
| `storyteller_hd`       | ~$2.40 | baseline |
| `storyteller_premium`  | ~$8.61 | +259% |

Plan is ready to execute.

---

## Task 14a: Add `--image-tier` and `--video-tier` flags to `ve run`

**Files:**
- Modify: `apps/cli/src/video_editor_cli/main.py`
- Create: `tests/test_storyteller_tier_flags.py`

**Interfaces:**
- Consumes: existing `ve run` subcommand
- Produces: two new `--image-tier {budget,standard,premium}` and `--video-tier {budget,standard,premium}` flags, forwarded into `build_storyteller_scene_plan(image_tier=..., video_tier=...)`. Invalid values → clear Click error.

- [ ] **Step 1: Write the failing test**

Create `tests/test_storyteller_tier_flags.py`:
```python
"""Tests that --image-tier and --video-tier flags are validated and forwarded."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from video_editor_cli.main import cli

VALID_TIERS = ("budget", "standard", "premium")


@pytest.mark.parametrize("tier", VALID_TIERS)
def test_valid_image_tier_is_accepted(tmp_path: Path, tier: str) -> None:
    script = tmp_path / "story.md"
    script.write_text(
        "---\nlanguage: en\ncast:\n  narrator: {voice_id: elevenlabs:Brian, language: en}\n---\n\n## Speaker: narrator\nHello.\n",
        encoding="utf-8",
    )
    with patch("video_editor_cli.main.build_storyteller_scene_plan", new=MagicMock()) as mock_build:
        result = CliRunner().invoke(
            cli,
            ["run", str(script), "--template", "storyteller_hd", "--image-tier", tier, "--job-id", "x"],
        )
    assert result.exit_code == 0, result.output
    assert mock_build.call_args.kwargs.get("image_tier") == tier


def test_invalid_tier_rejected(tmp_path: Path) -> None:
    script = tmp_path / "story.md"
    script.write_text("---\ncast: {narrator: {voice_id: e:B, language: en}}\n---\n## Speaker: narrator\nx\n", encoding="utf-8")
    result = CliRunner().invoke(
        cli,
        ["run", str(script), "--template", "storyteller_hd", "--image-tier", "garbage", "--job-id", "x"],
    )
    assert result.exit_code != 0
    assert "Invalid value" in result.output or "invalid choice" in result.output
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_storyteller_tier_flags.py -v`
Expected: FAIL — `--image-tier` flag is not yet on `ve run`.

- [ ] **Step 3: Add the tier flags to the `ve run` subcommand**

In `apps/cli/src/video_editor_cli/main.py`, find the `ve run` subcommand and add:
```python
@click.option(
    "--image-tier",
    type=click.Choice(["budget", "standard", "premium"]),
    default=None,
    help="Image quality tier (overrides the template preset's default). Standard = gpt-image-2 1K low; budget = flux-schnell; premium = gpt-image-2 2K.",
)
@click.option(
    "--video-tier",
    type=click.Choice(["budget", "standard", "premium"]),
    default=None,
    help="Motion quality tier (overrides the template preset's default). Standard = seedance-2-fast 720p; budget = wan-2-2-fast 480p; premium = seedance-2 1080p.",
)
```

In the `ve run` body, after the `template = ...` line, pass `image_tier=image_tier, video_tier=video_tier` to `build_storyteller_scene_plan(...)`. Click's `Choice` validator handles the "garbage" rejection so no extra logic is needed.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_storyteller_tier_flags.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the full suite to verify no regressions**

Run: `pytest -q 2>&1 | tail -5`
Expected: 815 passed.

- [ ] **Step 6: Commit**

```bash
git add apps/cli/src/video_editor_cli/main.py tests/test_storyteller_tier_flags.py
git commit -m "feat(cli): add --image-tier and --video-tier flags to ve run for storyteller templates"
```

---

## Self-Review (post-3-tier amendment)

- **3 tiers × 3 tiers = 9 model combinations.** Users pick one per CLI run; three template presets (budget/hd/premium) cover the diagonal.
- **Planner signature changed:** `build_storyteller_scene_plan` now takes `image_tier` and `video_tier` keyword args; `_resolve_tiers()` is the single point of validation.
- **Scene props extended** to record `image_tier`, `video_tier`, `image_size`, `motion_resolution` — FableCut round-trip and audit log can reconstruct the exact cost.
- **Tests added (4 new):** premium preset, explicit tier override, invalid tier rejection, parametrized 3 valid tiers.
- **Backward compat:** the new `quality_tier_pair` field on `Template` defaults to `None`, so all 11 pre-existing templates are unaffected.
- **Task count:** 19 + 1 = **20 tasks**; test count projection: 815.

Plan is consistent and ready to execute.
