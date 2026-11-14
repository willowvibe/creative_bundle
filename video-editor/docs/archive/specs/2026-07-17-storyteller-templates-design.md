# Storyteller Templates — Design

**Date:** 2026-07-17
**Status:** Draft (pending user review)
**Scope:** Two new video templates (`storyteller_hd`, `storyteller_budget`) that turn a persona-tagged markdown script into a multi-voice multilingual story video with timeline preview and final cut. Plus three new provider wrappers (PoYo music, Sarvam TTS, TTS router), one new planner module, one FableCut bridge extension, and an LLM config cleanup.

## Motivation

The engine's 11 existing templates cover single-voice explainers (`explainer_reel`, `image_story`, `voice_canvas`), one-voice-per-run multilingual dubbing (`multi_language_dub`), and music-driven montages (`musical_video`) — but no template supports **multi-voice multi-language storytelling** where different "characters" each have their own voice, language, and image style, and where the user wants to **preview the timeline in a browser editor** before committing to a render.

Two cost levers also need pulling:

- **Music generation** is currently ElevenLabs-only at **$0.640/min** (`docs/POYO_ROUTING.md` lists `elevenlabs-music` at 128 credits/min). PoYo resells the same class of models at much lower cost: `minimax-music-2-6` and `generate-music` are both $0.10/gen (20 credits). For a 10-minute story that's **~$6.30 saved per video**.
- **Multilingual TTS** for Indian languages is currently routed through ElevenLabs at $0.08/1K chars. Sarvam Bulbul v3 is **$0.036/1K chars** and has native voices for 11 Indian languages (Hindi, Bengali, Gujarati, Kannada, Malayalam, Marathi, Odia, Punjabi, Tamil, Telugu, English-India) that are purpose-built for those languages.

This work adds the templates and pulls both cost levers.

## Decisions (from brainstorming)

- **Script format:** markdown with YAML frontmatter (`cast:`, `language:`, `music_mood:`, `title:`) and `## Speaker: <name>` section headings. Per-scene directives as blockquotes: `> visual: ...`, `> sfx: ...`, `> language: hi`, `> motion: true`. A bare blockquote directly under a speaker heading is treated as an SFX prompt shorthand.
- **Two templates sharing one pipeline kind:** `storyteller_hd` and `storyteller_budget`, both `pipeline_kind = "storyteller"`. Differences: provider model defaults and motion-decision policy.
- **Motion policy:**
  - `storyteller_hd`: `needs_motion` defaults to `false` per scene (still + Ken Burns). User opts in with `> motion: true`. Default motion model: `seedance-2-fast` 720p, 4 s.
  - `storyteller_budget`: LLM (`minimax-m3:cloud`) decides per scene aiming for ~50% motion. Default motion model: `seedance-2-mini` 480p, 4 s.
- **Image-prompt generation:** LLM-derives `visual_description` from `(text, speaker, prev_visual_description)` per scene unless the scene has a `> visual:` override. Hash-based cache so re-runs are free.
- **Background music:** always auto-generated via PoYo `minimax-music-2-6` (one track per story). FableCut's final cut sidechain-ducks it under voice.
- **SFX:** opt-in, generated only when a scene has a `> sfx:` directive. ElevenLabs SFX.
- **Multilingual TTS:** new `TTSRouter` applies a language-based rule — Indian-language scenes (the Sarvam set + `en-IN`) use Sarvam Bulbul v3; everything else uses ElevenLabs. Explicit `sarvam:` or `elevenlabs:` voice prefix in frontmatter always wins.
- **Timeline preview:** reuse the existing FableCut bridge (`packages/fablecut_bridge/`) + `tools/FableCut/`. New CLI command `ve storyteller-export <job>` opens FableCut in the browser; user edits; existing `ve render-fablecut` produces the final MP4.
- **LLM default:** swap `kimi-k2.6:cloud` → `minimax-m3:cloud` for `plan`, `decompose`, `edit_plan`, `scene_plan` tasks. Same provider family as the cheap music model for style consistency.
- **Config cleanup:** one-time audit. Update `config.yaml` to reference only models that exist in the live `ollama list` (today: `kimi-k2.7-code:cloud`, `deepseek-v4-pro:cloud`, `glm-5.2:cloud`, `minimax-m3:cloud`, `qwen3-coder-next:cloud`, `gemini-3-flash-preview:latest`). Add a soft warning in `ModelRouter` when a configured model is missing from the live list (log only, not raise).

## Part 1 — Script format and Pydantic model

### Source format

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
    volume: 0.9
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

### Parser rules

| Construct | Meaning |
|---|---|
| YAML frontmatter | global `language`, `music_mood`, `title`, `cast` map |
| `## Speaker: <name>` | starts a new scene; name must exist in `cast` (else error) |
| `> visual: ...` | override the LLM-derived `visual_description` for this scene |
| `> sfx: ...` | generate an ElevenLabs SFX for this scene; appears on A2 |
| `> language: <code>` | per-scene language override (defaults to global `language` or cast entry's language) |
| `> motion: true` | HD-only directive; force `needs_motion: true` for this scene |
| Blockquote (no `key:`) directly under a speaker | treated as `> sfx:` (single-line SFX shorthand) |

### Pydantic model

New file `apps/engine/src/video_editor_engine/models/storyteller_script.py`:

```python
class CastMember(BaseModel):
    voice_id: str                       # e.g. "elevenlabs:Brian", "sarvam:shubh"
    language: str                       # e.g. "en", "hi-IN"
    volume: float = 1.0                 # per-speaker gain; 0.0–2.0

class StorytellerScene(BaseModel):
    id: str                             # "scene_001", auto-generated
    speaker: str                        # must be a key in StorytellerScript.cast
    text: str                           # narration text (verbatim, used for TTS)
    language: str                       # resolved (per-scene > cast > global)
    voice_id: str                       # resolved from cast[speaker]
    visual_override: str | None         # raw "visual: ..." text
    sfx_prompts: list[str]              # one or more SFX descriptions
    motion_override: bool | None        # only set when `> motion: true` was present

class StorytellerScript(BaseModel):
    language: str = "en"
    music_mood: str = "cinematic"
    title: str = ""
    cast: dict[str, CastMember]
    scenes: list[StorytellerScene]      # populated by parse_storyteller_script
```

### ScenePlan mapping

Each `StorytellerScene` becomes a `Scene` in a `ScenePlan` with:

- `id` = `StorytellerScene.id`
- `type` = `"story"` (new value; added to `_ALLOWED_SCENE_TYPES` in `apps/engine/src/video_editor_engine/models/scene_plan.py`)
- `duration_seconds` = computed from TTS audio length with a 1.15× safety pad
- `narration` = `scene.text` (verbatim, used for captions)
- `visual_description` = `visual_override` if present, else LLM-derived
- `asset_query` = empty (PoYo generation, not stock search)
- `props`:
  ```python
  {
    "speaker": scene.speaker,
    "language": scene.language,
    "voice_id": scene.voice_id,
    "tts_text": scene.text,
    "needs_motion": bool,             # true/false for HD; LLM-decided for budget
    "motion_model": str | None,
    "image_model": str,
    "sfx_prompts": list[str],
    "music_mood": str,
    "render_track": "image" | "video",
    "visual_prompt_source": "llm" | "override",
  }
  ```
- `assets` = `[TTSResult, ImageResult, VideoResult?, SFXResult?, ...]` populated at render time

## Part 2 — Planner module

New file `apps/engine/src/video_editor_engine/planner/storyteller_planner.py`.

Public API:

```python
async def parse_storyteller_script(path: Path) -> StorytellerScript
async def build_storyteller_scene_plan(
    script: StorytellerScript,
    llm_router: ModelRouter,
    *,
    template_name: Literal["storyteller_hd", "storyteller_budget"],
    asset_manager: AssetIngestionManager,
    music_provider: MusicProvider,
    sfx_provider: SFXProvider,
    tts_provider: TTSProvider,
    on_scene_ready: Callable[[Scene], Awaitable[None]] | None = None,
) -> ScenePlan
```

### 10-step pipeline

1. **Parse** markdown → `StorytellerScript`. No I/O.
2. **Resolve TTS provider per scene** using `TTSRouter` + voice-prefix precedence. Group scenes by `(provider, voice_id, language)` for batching.
3. **TTS pass** — for each group, concatenate texts with `[0.4s silence]` markers, call the provider once, split the returned audio back to per-scene files by silence-marker timestamps (via `ffprobe`; fall back to per-scene TTS if split fails). Output to `projects/<job>/assets/tts/scene_NNN.mp3`.
4. **Compute scene durations** = TTS length × 1.15.
5. **Image-prompt derivation** — LLM per scene without `> visual:` override. Cache by hash of `(text, speaker, prev_visual)` so re-runs are free. Cache key: `sha256(scene.text + "|" + scene.speaker + "|" + prev_visual_description)`. Cache lives at `projects/<job>/.cache/image_prompts.json`.
6. **Motion policy**:
   - HD: `> motion: true` directive → `true`; otherwise `false`.
   - Budget: one LLM call with all scenes' `(id, text, speaker)` and the 50% motion budget prompt → per-scene `needs_motion` boolean. Cached at `projects/<job>/.cache/motion_policy.json` keyed by `sha256(all_scenes_text + title)`.
7. **Asset generation pass** — per scene:
   - Still path: PoYo image (`gpt-image-2` 1K low for HD, `flux-schnell` for budget).
   - Motion path: PoYo video (`seedance-2-fast` 720p for HD, `seedance-2-mini` 480p for budget). A thumbnail still is always generated from the same prompt so the Ken Burns fallback works if video gen fails.
   - SFX path: ElevenLabs SFX, one call per `sfx_prompts[i]`.
8. **Background music** — one call to `PoyoMusicProvider` with `prompt = f"{title or 'story'} ({music_mood}), ~{total_duration}s"` and `model = "minimax-music-2-6"`. Output to `projects/<job>/assets/music/bg.mp3`. The final-cut FFmpeg graph applies sidechain ducking (FFmpeg `sidechaincompress` filter with the voice track as the sidechain key, ~8 dB duck during speech) so the music does not overpower the voice.
9. **Finalize `ScenePlan`** and write to `projects/<job>/scene_plan.json`.
10. **Hand off to FableCut exporter** (Part 4).

### Provider defaults per template

| Slot | `storyteller_hd` | `storyteller_budget` |
|---|---|---|
| `tts` | `tts_router` (Sarvam for Indian langs, ElevenLabs otherwise) | same |
| `image` | `poyo_ai` (`gpt-image-2` 1K low) | `poyo_ai` (`flux-schnell`) |
| `video` | `poyo_ai` (`seedance-2-fast` 720p) | `poyo_ai` (`seedance-2-mini` 480p) |
| `music` | `poyo_music` (`minimax-music-2-6`) | same |
| `sfx` | `elevenlabs_sfx` | same |
| `llm` | `minimax-m3:cloud` | same |
| `motion_policy` | `flag_only` | `llm_decided_50pct` |
| `default_preset` | `long_form` | `long_form` |
| `default_aspect_ratio` | `16:9` | `16:9` |

### Failure handling

- TTS failure for a single scene → retry 2× with backoff → fall back to silent scene with caption only.
- PoYo image/video timeout → retry once with same params, then retry once with cheaper model → fall back to placeholder still with prompt text burned on it.
- LLM image-prompt failure → deterministic prompt: `f"Cinematic illustration of: {scene.text[:200]}"`.
- ElevenLabs music failure (PoyoMusicProvider) → emit 5-second silence track + manifest note.
- SFX failure per scene → skip that SFX but keep the scene; log to `decision_log.jsonl`.

Each failure writes a structured log line so `decision_log.jsonl` audit trail stays accurate.

## Part 3 — New provider wrappers

### 3.1 `PoyoMusicProvider`

New file `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/poyo_music.py`. Implements a new `MusicProvider` protocol (`async def generate_music(prompt, duration_seconds, output_path) -> MusicResult`). Uses PoYo's unified submit/poll API (`POST /api/generate/submit`, `GET /api/generate/status/{task_id}`) with `model: "minimax-music-2-6"`. Configurable via `config.yaml`:

```yaml
assets:
  providers:
    poyo_music:
      api_key_env: POYO_API_KEY
      music_model: minimax-music-2-6
      fallback_music_model: generate-music   # Suno-backed
      timeout: 300.0
      poll_interval_seconds: 5.0
      poll_timeout_seconds: 600.0
```

### 3.2 `SarvamTTSProvider`

New file `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/sarvam_tts.py`. Implements a new `TTSProvider` protocol (`async def synthesize(text, voice_id, language, output_path) -> TTSResult`). Calls `POST https://api.sarvam.ai/text-to-speech` with header `api-subscription-key: $SARAVAM_API`. Decodes the base64 `audios[0]` to `output_path`. Maps `voice_id` like `sarvam:shubh` → `{speaker: "shubh", model: "bulbul:v3"}` and `language` like `hi` → `target_language_code: "hi-IN"`. Falls back to `bulbul:v2` on `model_unavailable`.

```yaml
assets:
  providers:
    sarvam_tts:
      api_key_env: SARAVAM_API
      base_url: https://api.sarvam.ai
      tts_model: bulbul:v3
      fallback_model: bulbul:v2
      timeout: 60.0
```

### 3.3 `TTSRouter`

New file `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/tts_router.py`. Implements the same `TTSProvider` interface and delegates based on the routing rule:

- If voice_id has an explicit `sarvam:` or `elevenlabs:` prefix → use that provider regardless of language.
- Else if resolved language is in `{hi, bn, gu, kn, ml, mr, od, pa, ta, te, en-IN}` (any with `*-IN` form) → Sarvam.
- Else → ElevenLabs.

### 3.4 Updated `config.yaml` defaults

```yaml
llm:
  default_provider: ollama_cloud_pro
  providers: { ollama_cloud_pro: { ... } }
  tasks:
    plan:        { task_type: plan,        provider: ollama_cloud_pro, model: minimax-m3:cloud }
    decompose:   { task_type: decompose,   provider: ollama_cloud_pro, model: minimax-m3:cloud }
    edit_plan:   { task_type: edit_plan,   provider: ollama_cloud_pro, model: minimax-m3:cloud }
    scene_plan:  { task_type: scene_plan,  provider: ollama_cloud_pro, model: minimax-m3:cloud }
    fallback:    { task_type: fallback,    provider: ollama_cloud_pro, model: glm-5.2:cloud }
    code:        { task_type: code,        provider: ollama_cloud_pro, model: qwen3-coder-next:cloud }
    utility:     { task_type: utility,     provider: ollama_cloud_pro, model: gemini-3-flash-preview:latest }
    verify_judge:{ task_type: verify_judge,provider: ollama_cloud_pro, model: gemini-3-flash-preview:latest }

assets:
  providers:
    poyo_ai:        { ... existing ... }
    fal_ai:         { ... existing ... }
    poyo_music:     { api_key_env: POYO_API_KEY, music_model: minimax-music-2-6, fallback_music_model: generate-music, timeout: 300.0 }
    sarvam_tts:     { api_key_env: SARAVAM_API, base_url: https://api.sarvam.ai, tts_model: bulbul:v3, fallback_model: bulbul:v2, timeout: 60.0 }
  defaults:
    image: poyo_ai
    video: poyo_ai
    music: poyo_music
    tts: tts_router
```

## Part 4 — Template entries and FableCut bridge extension

### 4.1 New `Template` entries

Extend `apps/engine/src/video_editor_engine/templates/eleven_templates.py` (file name kept; docstring updated to "thirteen templates"). Two new entries:

> **Code change prerequisite:** extend the `PipelineKind` Literal in `apps/engine/src/video_editor_engine/templates/base.py` to include `"storyteller"`:
> ```python
> PipelineKind = Literal["v2_skills", "v1_raw", "script_to_video", "music_video", "storyteller"]
> ```
> And extend `_ALLOWED_SCENE_TYPES` in `apps/engine/src/video_editor_engine/models/scene_plan.py` to include `"story"`.

```python
Template(
    name="storyteller_hd",
    description="Turn a multi-voice, multi-language markdown story into a polished HD video with PoYo visuals, FableCut preview, and ElevenLabs SFX.",
    inputs=[
        InputSpec(name="script.md", description="Story markdown with YAML frontmatter (cast, language, music_mood) and `## Speaker:` sections.", example="./story.md"),
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
    build_concept=_storyteller_concept,
    caveats=[
        "Multi-voice multi-language: per-speaker voice_id + language; explicit prefixes (sarvam:/elevenlabs:) win over the language routing rule.",
        "Motion scenes require the `> motion: true` directive per scene; the default is still image with Ken Burns.",
        "Uses the existing FableCut bridge for timeline preview; after browser edits, run `ve render-fablecut <job>` for the final cut.",
    ],
),
Template(
    name="storyteller_budget",
    description="Same as storyteller_hd but uses PoYo budget-tier image/video models and lets the LLM pick which scenes get cheap motion (~50% of scenes).",
    inputs=[ ...same as hd... ],
    pipeline_kind="storyteller",
    default_preset="long_form",
    default_aspect_ratio="16:9",
    default_providers={
        "tts": "tts_router",
        "image": "poyo_ai",        # model override: flux-schnell
        "video": "poyo_ai",        # model override: seedance-2-mini 480p
        "music": "poyo_music",
        "sfx": "elevenlabs_sfx",
    },
    default_music_mood="cinematic",
    scene_type_hints=["story"],
    build_concept=_storyteller_concept,
    caveats=[
        "LLM picks motion scenes; the choice is deterministic per (text, speaker) but not user-tunable. Use `> motion: true|false` to override per scene.",
        "Budget image model `flux-schnell` is faster but lower fidelity than HD's `gpt-image-2`.",
        "Budget motion model `seedance-2-mini` is capped at 480p / 4 s per clip.",
    ],
),
```

`scene_type_hints` references the new `"story"` value added to `_ALLOWED_SCENE_TYPES` in `models/scene_plan.py`.

### 4.2 New scene type and router inference

In `apps/engine/src/video_editor_engine/templates/router.py`, update `_infer` so a markdown file with `cast:` in its frontmatter is mapped to `storyteller_hd` by default (the user can override with `--template storyteller_budget`). The current default for plain markdown is `explainer_reel`; the new branch is checked first.

### 4.3 FableCut bridge extension

New module `packages/fablecut_bridge/src/video_editor_fablecut_bridge/storyteller_export.py`. Provides:

```python
def storyteller_to_fablecut(
    scene_plan: ScenePlan,
    project_dir: Path,
    *,
    aspect_ratio: str = "16:9",
) -> StorytellerProject
def fablecut_to_storyteller(
    project: StorytellerProject,
    scene_plan: ScenePlan,
) -> ScenePlan
```

`StorytellerProject` extends the existing `FableCutProject` with:

- `tracks.audio_speakers: list[str]` — the set of speakers in playback order.
- `tracks.audio_languages: list[str]` — the set of BCP-47 language codes.
- Per-clip `meta: { speaker, language, sfx_prompts, motion_override }`.

The exporter reads each scene's `props` to:

- Pick V1 image or video clip (based on `render_track`)
- Set per-clip `volume` from `cast[speaker].volume`
- Put SFX MP3s on A2 with `gain` from `sfx_prompts` count (default `1.0`)
- Put all voice audio on A1, splitting into A1+A2 if two languages are present so the user can adjust per-language gain in the browser
- Put the background music on A3

The importer merges FableCut's user-edited clip metadata back into the `ScenePlan` (image_prompt override, motion toggle, scene duration, gain).

### 4.4 New CLI commands

In `apps/cli/src/video_editor_cli/main.py`:

```bash
# Plan + asset gen + FableCut export (used by `ve run --template storyteller_hd|budget`)
ve run <script.md> --template storyteller_hd --job-id <job-id>
ve run <script.md> --template storyteller_budget --job-id <job-id>

# Re-export a ScenePlan to FableCut (after planner re-run or hand-edit of scene_plan.json)
ve storyteller-export <job-id> [--project-dir PATH]

# Audition a single TTS voice before running a full story (writes the MP3 to
# projects/<job>/assets/tts/audition.mp3 so the user can play it back)
ve tts-test --text "Hello world" --voice sarvam:shubh --language hi-IN --job-id audition

# Existing — used for the final cut
ve render-fablecut <job-id> --project-dir projects/<job>/fablecut
```

New worker payload `storyteller_export` registered in `_HANDLERS`, mirroring `export_fablecut`. New CLI subcommand `ve tts-test` is registered in `main.py` and uses the same `tts_router` so it picks the right provider from the `--voice` prefix and `--language` flag.

## Part 5 — Testing

| Test file | Coverage | Approx. tests |
|---|---|---|
| `tests/test_storyteller_parser.py` | Frontmatter parse; `## Speaker:` heading split; `> visual/sfx/language/motion:` directives; blockquote-as-sfx shorthand; duplicate scene IDs; unknown speaker errors; missing cast errors | 15 |
| `tests/test_storyteller_planner.py` | LLM image-prompt derivation (mocked router); LLM motion policy for budget; scene duration from mocked TTS; SFX extraction; music prompt generation; `> visual:` override precedence; language routing; image-prompt cache hit; `needs_motion` defaults | 18 |
| `tests/test_storyteller_fablecut_export.py` | Round-trip planner → exporter → FableCut JSON → importer; multi-language audio tracks; per-speaker volume; SFX tracks; motion flag survival; user-edited `image_prompt` round-trip | 8 |
| `tests/test_templates.py` (extended) | Update `== 11` → `== 13`; add explicit-`--template` selection tests for both; add router source-inference test (markdown with `cast:` in frontmatter → `storyteller_hd`) | +5 |
| `tests/test_poyo_music.py` | Mocked PoYo submit/poll; success path; model-not-found retry to `generate-music`; timeout; partial credit exhaustion | 6 |
| `tests/test_sarvam_tts.py` | Mocked `POST /text-to-speech`; base64 decode; language code mapping; v2 fallback on v3 unavailability; header auth check | 6 |
| `tests/test_tts_router.py` | Indian-language scene → Sarvam; English → ElevenLabs; explicit prefix overrides; mixed-language grouping | 5 |
| `tests/test_tts_test_cli.py` | `ve tts-test` writes the audition MP3 to the expected path; `sarvam:` prefix routes through `SarvamTTSProvider`; `elevenlabs:` prefix routes through `ElevenLabsTTSProvider`; missing `--text` errors cleanly | 4 |
| **Total new tests** | | **~67** |

All new tests use mocks — no live API calls in CI. Full suite today: 782 passing. Target: 840+ passing.

## Part 6 — Documentation updates

| File | Change |
|---|---|
| `docs/templates.md` | Add `storyteller_hd` (#12) and `storyteller_budget` (#13) to catalog table and per-template detail sections. Update count 11 → 13. |
| `docs/Templates_capabilities.md` | Full per-template sections: inputs, providers, motion policy, multilingual routing, music/SFX, sample script, expected runtime, cost estimate. |
| `docs/CLI_USAGE.md` | Document `ve run --template storyteller_hd\|budget`, `ve storyteller-export`, the new `storyteller_export` worker payload. Cross-link to the FableCut preview flow. |
| `docs/MODEL_ROUTING.md` | Update to reflect cleanup: only models in live `ollama list`. Move `minimax-m3:cloud` to planner default. |
| `docs/POYO_ROUTING.md` | New "Music and TTS" section: `minimax-music-2-6`, `generate-music`, cost comparison vs ElevenLabs, link to `PoyoMusicProvider`. |
| `docs/SARVAM_INTEGRATION.md` (new) | Full Sarvam Bulbul v3 setup: API key, supported languages with codes, billing, base64 response handling, v3→v2 fallback, planner routing. |
| `docs/not_possible.md` | Remove the "long-form story with multiple voices" entry. |
| `README.md` | Mention the two new templates in the "What you can build" list. |
| `config.example.yaml` | Add `poyo_music` and `sarvam_tts` provider stubs and `tts: tts_router` default. |
| `.env.example` | Add `SARAVAM_API=` placeholder. |

## Part 7 — Memory updates

Three new memory files (one fact per file, following the existing convention):

- `storyteller-templates-2026-07-17.md` — high-level: what was added, where the planner lives, FableCut preview flow, model + provider defaults chosen.
- `poyo-music-and-sarvam-tts-providers-2026-07-17.md` — new audio providers, language-routing rule, cost comparison vs ElevenLabs, fallback behavior.
- `llm-config-cleanup-2026-07-17.md` — what changed in `config.yaml`, why, the `ModelRouter` soft-warning guard.

## Part 8 — Rollout plan

Single PR, 12 steps, each independently shippable to a feature branch:

1. `ModelRouter` soft warning + `config.yaml` cleanup + `MODEL_ROUTING.md` update. Existing 782 tests still green.
2. Pydantic models in `models/storyteller_script.py`.
3. `parse_storyteller_script()` + `test_storyteller_parser.py`.
4. `PoyoMusicProvider` + `test_poyo_music.py`.
5. `SarvamTTSProvider` + `test_sarvam_tts.py`.
6. `TTSRouter` + `test_tts_router.py`.
7. `build_storyteller_scene_plan()` + `test_storyteller_planner.py`.
8. FableCut bridge extension + `test_storyteller_fablecut_export.py`.
9. Two new `Template` entries + router source-inference rule + update `test_templates.py` count.
10. CLI commands (`ve run` extension, `ve storyteller-export`, worker handler) + `test_cli.py` updates.
11. Docs updates (all 10 files above).
12. End-to-end smoke build: 6-scene short story, two languages, one SFX, one motion scene, music + voice ducking, FableCut preview + edit + final cut.

## Part 9 — Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| `minimax-music-2-6` produces low-quality output for stories | Medium | `PoyoMusicProvider` is configurable; swap to `generate-music` (Suno) via one-line config change. A/B test both during the smoke build. |
| Sarvam Bulbul v3 voice identity doesn't match user expectations | Low | Frontmatter `voice_id` is user-settable per speaker. Add `ve tts-test --text "..." --voice sarvam:shubh --language hi` CLI command to audition. |
| LLM image-prompt derivation drifts between runs | Medium | Hash-based cache by `(text, speaker, prev_visual)`. First run is the only stochastic one; user can lock it by saving `scene_plan.json` after first run. |
| FableCut can't render the per-language dual audio track layout | Low | Round-trip test covers it. Fallback: both languages on a single A1 track with sidechain ducking (loses per-language gain, still sounds correct). |
| Config cleanup breaks an unrelated script that imports the old model names | Low | Soft warning only, not a hard error. Existing tests catch any actual breakage. |
| `minimax-m3:cloud` slower than expected for budget motion policy | Low | Motion policy is one batched call; even at 2 s/scene, 20 scenes = 40 s. Cache the motion policy alongside the image-prompt cache. |
| `.env` typo'd or missing `SARAVAM_API` | Low | `SarvamTTSProvider.__init__` raises a clear error if `SARAVAM_API` is unset. Listed in `docs/SARVAM_INTEGRATION.md` troubleshooting. |
| `> motion: true` directive collides with an unrelated `> ` blockquote | Low | Parser only matches `^>\s*(visual|motion|language|sfx)\s*:`. Bare blockquote is treated as SFX. Documented. |

## Definition of done

- All 4 new + 1 extended test files pass; full suite ≥ 840 tests.
- `ruff` and `mypy` clean.
- End-to-end smoke build produces a 30-60 s MP4 with ≥ 4 scenes, 2 distinct speakers, 1 SFX, 1 motion scene, 1 background music track with voice ducking.
- All 10 doc files updated and cross-linked.
- 3 memory files added.
- PR is one commit per rollout step for easy review.
