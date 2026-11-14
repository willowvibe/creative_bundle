# Cost Gate + FableCut Preview Pipeline

**Date:** 2026-08-02
**Status:** Design approved, awaiting implementation plan

## Overview

Add two new pipeline stages that apply across all templates:

1. **Cost estimation gate** (`ve estimate`) — tallies all asset costs before generation, prompts for approval
2. **FableCut preview loop** (`ve preview`) — exports to FableCut web editor, waits for user edits, re-renders on changes
3. **Final export** (`ve finalize`) — produces the final deliverable after preview approval

### New Pipeline Flow

```
plan/plan-script → estimate → assets → render → preview → finalize
                      │                              │
                 [approval gate]                [edit loop]
                 y/n prompt                     FableCut browser
```

## 1. Provider Model Pricing Registry

### New file: `packages/asset_ingestion/src/video_editor_asset_ingestion/pricing.py`

A module that loads pricing from a YAML/JSON file and provides model-level cost lookups.

### Pricing data file: `pricing.yaml` (repo root)

```yaml
last_updated: "2026-08-02"
providers:
  elevenlabs_tts:
    models:
      eleven_turbo_v2_5: { cost_per_1k_chars: 0.05, currency: USD }
      eleven_flash_v2_5: { cost_per_1k_chars: 0.01, currency: USD }
      eleven_multilingual_v2: { cost_per_1k_chars: 0.05, currency: USD }
  poyo_ai:
    models:
      gpt-image-2: { cost_per_image: 0.04, currency: USD }
      flux-schnell: { cost_per_image: 0.005, currency: USD }
      seedance-2-fast: { cost_per_second: 0.02, currency: USD }
      seedance-2: { cost_per_second: 0.05, currency: USD }
  fal_ai:
    models:
      fal-ai/flux/dev: { cost_per_image: 0.005, currency: USD }
      fal-ai/flux-pro: { cost_per_image: 0.05, currency: USD }
  pexels: { cost_per_asset: 0.0, currency: USD, note: free stock }
  coverr: { cost_per_asset: 0.0, currency: USD, note: free stock }
  mixkit: { cost_per_asset: 0.0, currency: USD, note: free stock }
  poyo_music:
    models:
      minimax-music-2-6: { cost_per_second: 0.01, currency: USD }
      generate-music: { cost_per_second: 0.005, currency: USD }
  elevenlabs_music:
    models:
      eleven_music_v1: { cost_per_second: 0.03, currency: USD }
  openrouter:
    models:
      anthropic/claude-sonnet-4: { cost_per_1M_input: 3.0, cost_per_1M_output: 15.0 }
      google/gemini-2.5-flash: { cost_per_1M_input: 0.15, cost_per_1M_output: 0.60 }
```

### Freshness check

- At every `ve estimate` or `ve run` invocation, check `pricing.yaml`'s `last_updated` field
- If older than 30 days, emit a warning: "Pricing data is N days old. Run `ve update-pricing` to refresh."
- `ve update-pricing` command: fetches latest pricing from provider APIs/docs and updates the file
- Stale pricing does NOT block the pipeline — it's a warning only

### Cost calculation functions

```python
def estimate_tts_cost(provider: str, model: str, char_count: int, pricing: PricingRegistry) -> float:
    """cost_per_1k_chars × ceil(chars/1000)"""

def estimate_image_cost(provider: str, model: str, count: int, pricing: PricingRegistry) -> float:
    """cost_per_image × count"""

def estimate_video_cost(provider: str, model: str, duration_s: float, pricing: PricingRegistry) -> float:
    """cost_per_second × duration_seconds"""

def estimate_music_cost(provider: str, model: str, duration_s: float, pricing: PricingRegistry) -> float:
    """cost_per_second × duration_seconds"""

def estimate_llm_cost(provider: str, model: str, input_tokens: int, output_tokens: int, pricing: PricingRegistry) -> float:
    """(input × cost_per_1M_input + output × cost_per_1M_output) / 1_000_000"""
```

### Provider priority for asset selection

When tallying costs, the estimator uses these defaults (overridable via config):

| Asset Type | Primary (paid) | Fallback (free) |
|-----------|----------------|-----------------|
| Image | poyo_ai (gpt-image-2) | pexels, coverr, mixkit |
| Video | poyo_ai (seedance-2-fast) | pexels |
| TTS | elevenlabs_tts (eleven_flash_v2_5) | — |
| Music | poyo_music (minimax-music-2-6) | — |

### Voice selection

Harish has two ElevenLabs voice IDs surfaced at estimate time:
- **Professional**: `ATYMJR0SnaN1mSrsnZEi` (higher quality, higher cost)
- **Instant**: `32EwRSV41WufKgzh80Ri` (lower cost, faster)

The `ve estimate` command asks which voice to use. Default to instant for cost-conscious runs.

## 2. `ve estimate` Command

### CLI signature

```
ve estimate <job-id> [--work-root <path>] [--output-root <path>]
           [--config <path>] [--yes/-y]
           [--tts-provider <name>] [--image-provider <name>]
           [--video-provider <name>] [--music-provider <name>]
           [--voice <voice-id>]
```

### Behavior

1. Read `scene_plan.json` from `<work>/plan/`
2. Call `requests_from_scene()` for each scene (same logic as `ve assets`)
3. For each request, look up model-level cost from pricing registry
4. Present a table:

```
Estimated costs for <job-id> (N scenes, ~Xs):
┌────────────────────┬──────────────────────┬───────┬─────────┐
│ Provider           │ Model                │ Count │ Est. Cost│
├────────────────────┼──────────────────────┼───────┼─────────┤
│ elevenlabs_tts     │ eleven_flash_v2_5    │ 9     │ $0.05   │
│ poyo_ai            │ gpt-image-2          │ 9     │ $0.36   │
│ poyo_ai            │ seedance-2-fast      │ 5     │ $0.50   │
│ poyo_music         │ minimax-music-2-6    │ 1     │ $0.71   │
│ openrouter         │ claude-sonnet-4      │ 1     │ $0.02   │
├────────────────────┼──────────────────────┼───────┼─────────┤
│ TOTAL              │                      │ 25    │ $1.64   │
└────────────────────┴──────────────────────┴───────┴─────────┘

Voice: instant (32EwRSV41WufKgzh80Ri)
Proceed? [Y/n]:
```

5. If `--yes`/`-y`: skip prompt, auto-approve
6. On approval: write `plan/cost_estimate.json` with the approved tally
7. On decline: exit with message "Edit the scene plan or re-run with different providers."
8. Mark manifest stage `estimate` as complete

### Output file: `plan/cost_estimate.json`

```json
{
  "job_id": "saju-lead-gen",
  "approved_at": "2026-08-02T16:30:00Z",
  "voice_id": "32EwRSV41WufKgzh80Ri",
  "voice_name": "instant",
  "line_items": [
    {
      "provider": "elevenlabs_tts",
      "model": "eleven_flash_v2_5",
      "task_type": "tts",
      "count": 9,
      "unit_cost": 0.005,
      "total_cost": 0.045,
      "currency": "USD"
    }
  ],
  "total_cost": 1.64,
  "currency": "USD"
}
```

## 3. `ve preview` Command

### CLI signature

```
ve preview <job-id> [--work-root <path>] [--output-root <path>]
           [--config <path>] [--project-dir <path>]
           [--fps <n>] [--width <px>] [--height <px>]
```

### Behavior

1. **Export to FableCut:**
   - Read `scene_plan_with_assets.json`
   - Call `export_to_fablecut()` to produce `project.json` + `media/` in `<project-dir>` (default: `<work>/fablecut/`)
   - Write `engine_bridge.json` metadata

2. **Start FableCut server:**
   - Check if FableCut is already running on localhost:7777
   - If not, start `node server.js` from the FableCut directory as a background process
   - Write PID file for cleanup

3. **Open browser:**
   - Open `http://localhost:7777` (or `xdg-open` / `open` depending on OS)

4. **Interactive edit loop:**
   - Display prompt: "FableCut editor is open. Edit the timeline, then type a command:"
   - Accept commands:
     - `done` or `finalize` → import edited project, re-render, proceed to finalize
     - `change <scene_id> <field> to <value>` → re-generate specific asset, re-export, re-open
     - `change voice to <instant|professional>` → re-generate all TTS, re-export, re-open
     - `change music to <mood>` → re-generate music, re-export, re-open
     - `status` → show current state
     - `help` → show available commands

5. **On `done`:**
   - Call `import_from_fablecut()` to merge timeline edits into ScenePlan
   - Write updated `scene_plan_edited.json`
   - Call `render_fablecut_project()` for final render
   - Mark manifest stage `preview` as complete
   - Proceed to `ve finalize`

6. **On asset changes:**
   - Re-generate only the affected assets (not the whole plan)
   - Re-export to FableCut
   - Refresh browser (or notify user to refresh)
   - Continue the edit loop

7. **Cleanup:**
   - On exit (Ctrl+C or `done`), stop the FableCut server
   - Remove PID file

### Manifest stage: `preview`

```json
{
  "preview": {
    "completed_at": "2026-08-02T17:00:00Z",
    "outputs": {
      "fablecut_project": "<work>/fablecut/",
      "edited_plan": "<work>/plan/scene_plan_edited.json",
      "final_video": "<output>/final.mp4"
    },
    "edit_count": 3
  }
}
```

## 4. `ve finalize` Command

### CLI signature

```
ve finalize <job-id> [--work-root <path>] [--output-root <path>]
            [--output-name <name>] [--config <path>]
            [--review] [--verify]
```

### Behavior

1. If preview stage completed: use the edited plan and rendered video
2. If preview was skipped: use the original rendered video
3. Run `ExportSkill` to produce final MP4 + report + decision log
4. Optionally run post-render review (`--review`) and verification gate (`--verify`)
5. Record final costs to ledger
6. Mark manifest stage `finalize` as complete

## 5. Template-Agnostic Integration

### New manifest stages

| Stage | Created by | Purpose |
|-------|-----------|---------|
| `plan` | `ve plan-script` / `ve run` | Scene plan written |
| `estimate` | `ve estimate` | Cost estimate approved |
| `assets` | `ve assets` / `ve run` | Assets generated |
| `render` | `ve render` / `ve run` | Initial render complete |
| `preview` | `ve preview` | FableCut edit loop complete |
| `finalize` | `ve finalize` | Final export complete |

### Per-template mapping

| Pipeline Kind | plan | estimate | assets | render | preview | finalize |
|--------------|------|----------|--------|--------|---------|----------|
| `script_to_video` | `ve plan-script` | `ve estimate` | `ve assets` | `ve render` | `ve preview` | `ve finalize` |
| `storyteller` | `ve run` (inline) | `ve estimate` | (in run) | `ve render` | `ve preview` | `ve finalize` |
| `music_video` | `ve run` (inline) | `ve estimate` | `ve assets` | `ve render` | `ve preview` | `ve finalize` |
| `v2_skills` | (source is video) | N/A | N/A | `ve run` | `ve preview` | `ve finalize` |
| `v1_raw` | `ve ingest` | N/A | N/A | `ve run` | `ve preview` | `ve finalize` |

### `ve run` changes

- Add `--auto-approve`/`-y` flag to skip the estimate prompt
- Add `--skip-preview` flag to skip the FableCut preview stage
- When both flags are set, `ve run` behaves like the old flow (plan → assets → render → export)

## 6. Files to Create/Modify

### New files

| File | Purpose |
|------|---------|
| `pricing.yaml` | Provider model pricing registry (repo root) |
| `packages/asset_ingestion/src/video_editor_asset_ingestion/pricing.py` | Pricing registry loader, freshness check, cost calculators |
| `apps/cli/src/video_editor_cli/commands/estimate.py` | `ve estimate` command |
| `apps/cli/src/video_editor_cli/commands/preview.py` | `ve preview` command with FableCut edit loop |
| `apps/cli/src/video_editor_cli/commands/finalize.py` | `ve finalize` command |
| `apps/cli/src/video_editor_cli/commands/update_pricing.py` | `ve update-pricing` command |
| `tests/test_estimate.py` | Tests for cost estimation |
| `tests/test_preview.py` | Tests for FableCut preview flow |

### Modified files

| File | Change |
|------|--------|
| `apps/cli/src/video_editor_cli/main.py` | Register new commands; add `--auto-approve`/`--skip-preview` to `ve run` |
| `apps/cli/src/video_editor_cli/commands/__init__.py` | Export new command modules |
| `packages/asset_ingestion/src/video_editor_asset_ingestion/budget.py` | Update `DEFAULT_PRICING` to reference pricing registry |
| `packages/asset_ingestion/src/video_editor_asset_ingestion/__init__.py` | Export pricing module |
| `packages/state/src/video_editor_state/manifest.py` | Add `estimate`, `preview`, `finalize` as known stages |
| `config.yaml` | Add `pricing` section with path and refresh interval |

## 7. Migration: Credits → USD

The existing `BudgetGovernor` uses abstract "credits" (e.g., `elevenlabs_tts: 18.0`). The new pricing registry uses real USD (e.g., `eleven_turbo_v2_5: $0.05/1k chars`).

**Migration plan:**
- `pricing.yaml` is the new source of truth for costs
- `BudgetGovernor` is updated to read from the pricing registry instead of `DEFAULT_PRICING`
- The ledger's `currency` field changes from `"credits"` to `"USD"`
- `DEFAULT_PRICING` is deprecated but kept for backward compatibility
- `ve cost-report` shows USD amounts

## 8. Edge Cases & Error Handling

- **Missing pricing.yaml**: Generate a default one with built-in fallback values
- **Stale pricing**: Warn but don't block; user can proceed with stale data
- **FableCut not installed**: `ve preview` exits with "FableCut not found. Clone it to tools/FableCut/ or set FABLECUT_PATH."
- **FableCut server already running**: Reuse existing server, sync project files
- **No browser available**: Print the URL and wait for the user to open it manually
- **Empty scene plan**: `ve estimate` exits with "No scenes to estimate. Run ve plan-script first."
- **Budget exceeded at estimate time**: Show the overage and ask if user wants to proceed anyway or adjust providers
- **Preview loop interrupted (Ctrl+C)**: Save current state, stop FableCut server, exit gracefully
- **No changes made in FableCut**: Skip re-import, proceed directly to finalize

## 8. Testing Strategy

- **Unit tests**: Pricing registry loading, freshness check, cost calculation for each asset type
- **Integration tests**: `ve estimate` with a mock scene plan, `ve preview` with mock FableCut project
- **E2E test**: Full flow `plan-script → estimate → assets → render → preview → finalize` with a minimal 2-scene script
- **Edge case tests**: Missing pricing file, stale pricing, FableCut not installed, empty plan, budget exceeded
