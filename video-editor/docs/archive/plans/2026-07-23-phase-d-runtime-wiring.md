# Phase D Plan: Wire scored provider selector + budget governor into live `ve run` / `ve assets`

## Goal
Make `provider="auto"` and `--budget` the default runtime behavior for the main CLI paths, and surface the spend ledger in the exported `report.md`.

## Current state
- `AssetProviderConfig.select_provider(...)` and `ScoredProviderSelector` exist and are unit-tested.
- `AssetIngestionManager` already routes `provider_name == "auto"` through `config.select_provider(...)`.
- `AssetIngestionManager` accepts an optional `BudgetGovernor` and records actual costs.
- `ve run` already accepts `--budget`, creates `BudgetGovernor`, and passes it into:
  - `AssetIngestionManager` (storyteller path)
  - `MusicVideoPipeline`
  - `SkillsPipeline`
- `SkillsPipeline` accepts `budget` and passes it down through `AssetIngestionManager`.
- Export `report.md` is built in `ExportSkill._build_report` but has no budget section.
- `ve assets` accepts providers via CLI flags and currently defaults to empty strings; it does **not** accept `--budget` and does **not** create a `BudgetGovernor`.

## What is missing / what we will implement

### 1. `ve assets` gains `--budget` and auto-provider defaults
- Add `--budget` CLI option (float, default `None`).
- When `--budget` is set, instantiate `BudgetGovernor` and pass it to `AssetIngestionManager`.
- Change default CLI provider flags from `""` to `"auto"` so the scored selector is exercised by default.
- If a user explicitly passes a named provider, that named provider is used directly (existing behavior).
- Result: `ve assets --config config.yaml --budget 10.0 <job>` runs the full scored selection with a spend cap.

### 2. `ve run` single-file path passes `BudgetGovernor` into asset generation
- `SkillsPipeline` already stores `self.budget` but `AssetIngestionManager` inside it needs to receive it.
- Confirm that `SkillsPipeline` constructs its internal `AssetIngestionManager` with `budget=self.budget`.
- If not, add the pass-through.
- Same check/confirm for `MusicVideoPipeline`.

### 3. Default `provider="auto"` for TTS/image/video/data_viz in `requests_from_scene`
- Today `requests_from_scene(..., tts_provider="", image_provider="", ...)` produces requests with `provider=None`.
- Change defaults in the CLI option constants and/or helper to `"auto"`.
- Asset request with `provider=None` falls back to the config default. If the config default is `"auto"`, scoring runs. If the config default is a named provider, that provider runs. This preserves explicit override control.
- Document that `config.yaml` can set `defaults.image: auto`, etc., to get scored selection per asset type.

### 4. Spend ledger surfaced in `report.md`
- `ExportSkill._build_report` currently reads the job manifest and verification reports.
- Add a "Budget / Spend" section that loads `budget.json` from the job work dir (written by `BudgetGovernor.save_snapshot()` or similar).
- If `BudgetGovernor` did not run (no budget file), omit the section or print "_No budget tracking for this job._".
- The section should show:
  - Budget limit (or "unlimited")
  - Total spent (actual + estimated)
  - Breakdown by provider/task type with estimated vs actual where available.

### 5. Persist budget snapshot during a run
- Ensure `BudgetGovernor` writes `budget.json` to `ctx.work_dir / "budget" / "budget.json"` whenever `record_actual` is called or at manager shutdown.
- Provide `BudgetGovernor.snapshot() -> BudgetSnapshot` and `BudgetGovernor.save(path)` helpers if absent.
- Unit-test the snapshot serialization.

### 6. Default `--budget` to a sensible soft cap?
- **Decision**: do NOT default `--budget` to a hard value in this phase. Keep it opt-in so existing tests and ad-hoc runs are unaffected. The goal of this phase is wiring, not policy. We can add a config-level default later.
- Instead, change `--budget` help text to mention it enables scored provider selection and spend tracking.

## Files to modify
1. `apps/cli/src/video_editor_cli/main.py`
   - Add `_BUDGET_OPT` / `_BUDGET_ASSETS_OPT` constant(s) if not already present.
   - Add `--budget` to `assets` command.
   - Update `assets` default providers to `"auto"`.
   - Verify `run` path passes budget into pipeline/manager.
   - Update help text for `--budget` on `run`.
2. `apps/cli/src/video_editor_cli/pipeline.py`
   - Verify `SkillsPipeline` constructs `AssetIngestionManager(..., budget=self.budget)`.
3. `packages/asset_ingestion/src/video_editor_asset_ingestion/budget.py`
   - Add `save_snapshot(path)`, `load_snapshot(path)`, and budget file helpers.
4. `skills/export/src/export/export.py`
   - Add budget section in `_build_report`.
5. `packages/asset_ingestion/src/video_editor_asset_ingestion/manager.py`
   - Persist budget snapshot after `_generate_one` returns.
6. Tests:
   - `tests/test_asset_ingestion.py` or new `tests/test_budget_wiring.py`:
     - `ve assets --budget 10.0` uses scored selector.
     - `BudgetGovernor` rejects an over-budget request.
     - `report.md` contains budget section after export.

## Validation plan
1. `uv run pytest tests/test_asset_ingestion.py tests/test_pipeline.py tests/test_templates.py tests/test_music_video_cli.py -q` passes.
2. `uv run ruff check .` clean.
3. `uv run mypy <modified source files>` clean on touched files.
4. New test: invoke `ve assets --budget 5.0` with a stub scene plan and assert `budget.json` is written and `report.md` contains a budget section after a follow-up `ve export`.

## Risks / mitigations
- Changing default provider to `"auto"` could break tests that expect a specific provider. Mitigation: only change the CLI defaults; tests that use the helper directly still pass `""` and get config defaults. Update affected tests if any.
- Scored provider selection might pick a provider that requires an API key not present in CI. Mitigation: keep `provider="auto"` gated by `--config`/config.yaml; in the default no-config path, scoring is only triggered if the config default is `"auto"`. Alternatively, make `select_provider` fall back to `stub_*` providers when no keys are available.
- Budget rejection can halt a run mid-pipeline. Mitigation: log clearly and raise `BudgetExceededError` as a typed exception so the CLI can exit with a helpful message (`typer.echo("Budget exceeded ...", err=True); raise typer.Exit(1)`).

## Out of scope for this phase
- Default spend cap in config.yaml.
- Cost-based model tier selection inside a provider (e.g., switching from Fal to PoYo based on remaining budget).
- Real-time cost display in the CLI progress bar.

---

# Phase E Plan: Project Ledger / SQLite Registry

## Goal
Persist every project run, its per-stage status, actual/estimated provider costs, issues/audits, and final deliverable metadata in a lightweight, queryable SQLite ledger. Make the database **optional and file-backed** so local/dev workflows keep working without any infrastructure.

## Why now
- Phase D produces live cost data; Phase E persists it across runs.
- The engine already tracks stages in `JobContext.manifest()`, verification/review reports, and budget snapshots.
- A central ledger lets us answer questions like "How much did I spend on ElevenLabs TTS this month?" or "Which templates most often fail QA?" without scanning `output/`.

## Ledger location and activation
- Default path: `output/projects.db` (configurable in `config.yaml` as `ledger.path`).
- Activation: auto-initialize on first CLI use if the directory exists or `ledger.enabled: true` is set.
- If the ledger is disabled and no path is configured, writes fall back to a no-op `NullLedger` so the engine never fails because of missing DB setup.

## Schema

### `projects` — one row per finished (or dropped) project
| column | type | notes |
|---|---|---|
| `id` | TEXT PK | same as `job_id` for single-job projects; user-supplied project slug |
| `name` | TEXT | optional human label |
| `template` | TEXT | selected template name |
| `pipeline_kind` | TEXT | `skills`, `raw_footage`, `music_video`, `storyteller` |
| `status` | TEXT | `planned` \| `running` \| `complete` \| `partial` \| `dropped` \| `failed` |
| `created_at` | TEXT ISO | first time the project was seen |
| `started_at` | TEXT ISO | when generation began |
| `completed_at` | TEXT ISO | final status transition |
| `source_path` | TEXT | original source path (contained) |
| `output_video_path` | TEXT | final MP4 path |
| `final_duration_s` | REAL | probed duration |
| `budget_limit` | REAL | `--budget` value, NULL if unlimited |
| `total_estimated_cost` | REAL | sum of cost_lines.estimated |
| `total_actual_cost` | REAL | sum of cost_lines.actual |
| `currency` | TEXT | default `credits` |
| `notes` | TEXT | free-form; dropped/failed reason goes here |

### `jobs` — one row per pipeline stage execution
| column | type | notes |
|---|---|---|
| `id` | TEXT PK | `<project_id>:<stage>` or UUID |
| `project_id` | TEXT FK | |
| `stage` | TEXT | `ingest`, `rough_cut`, `graphics`, `captions`, `sfx`, `music`, `export`, `verify`, `review`, `asset_ingest` |
| `status` | TEXT | `pending` \| `running` \| `done` \| `failed` \| `skipped` \| `partial` |
| `started_at` | TEXT ISO | |
| `completed_at` | TEXT ISO | |
| `inputs` | TEXT JSON | input paths/refs |
| `outputs` | TEXT JSON | output paths/refs |

### `cost_lines` — one row per provider invocation
| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `project_id` | TEXT FK | |
| `job_id` | TEXT FK | nullable |
| `asset_id` | TEXT | e.g. `scene_1_tts` |
| `provider` | TEXT | `elevenlabs_tts`, `poyo_ai`, `fal_ai`, etc. |
| `task_type` | TEXT | `image`, `video`, `tts`, `music`, `sfx`, `alignment`, `isolation`, `speech_to_speech`, `data_viz` |
| `model` | TEXT | provider model id if known |
| `estimated_cost` | REAL | from `BudgetGovernor` estimate |
| `actual_cost` | REAL | from provider metadata; NULL until known |
| `currency` | TEXT | default `credits` |
| `latency_ms` | INTEGER | optional round-trip time |
| `generated_at` | TEXT ISO | |
| `status` | TEXT | `success` \| `fallback` \| `failure` \| `cached` |

### `issues` — audit findings, QA/review failures, budget overruns
| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `project_id` | TEXT FK | |
| `job_id` | TEXT FK | nullable |
| `severity` | TEXT | `critical`, `high`, `medium`, `low`, `info` |
| `category` | TEXT | `budget`, `provider`, `render`, `audio`, `qa`, `review`, `template`, `config` |
| `code` | TEXT | optional short code, e.g. `BUG_84` |
| `description` | TEXT | human-readable |
| `detected_at` | TEXT ISO | |
| `resolved_at` | TEXT ISO | nullable |
| `resolution` | TEXT | how it was closed, if known |

### `provider_usage` — rolled-up usage per provider per project
| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `project_id` | TEXT FK | |
| `provider` | TEXT | |
| `task_type` | TEXT | |
| `call_count` | INTEGER | |
| `estimated_total` | REAL | |
| `actual_total` | REAL | |
| `first_used_at` | TEXT ISO | |
| `last_used_at` | TEXT ISO | |

### `artifacts` — final and intermediate deliverables
| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `project_id` | TEXT FK | |
| `job_id` | TEXT FK | nullable |
| `kind` | TEXT | `final_video`, `report`, `decision_log`, `scene_plan`, `transcript`, `alignment`, `music`, `sfx_map` |
| `path` | TEXT | absolute or work-dir-relative path |
| `mime_type` | TEXT | optional |
| `size_bytes` | INTEGER | optional |
| `created_at` | TEXT ISO | |

### `project_tags` — user/template tags for grouping
| column | type | notes |
|---|---|---|
| `project_id` | TEXT FK | |
| `tag` | TEXT | PK composite with project_id |

## What we will implement

### 1. `ProjectLedger` data-access layer
- New module: `packages/state/src/video_editor_state/ledger.py`.
- Class `ProjectLedger(path: Path)` using stdlib `sqlite3`.
- Methods:
  - `init_db()` — create tables and indexes; idempotent.
  - `upsert_project(...)` — insert/update project header.
  - `set_project_status(project_id, status, notes=None)`.
  - `record_job(...)` — upsert a stage row.
  - `record_cost(...)` — insert a `cost_lines` row.
  - `record_issue(...)` — insert an `issues` row.
  - `record_artifact(...)` — insert an `artifacts` row.
  - `rollup_provider_usage(project_id)` — recompute `provider_usage` for the project.
  - `get_project(project_id)` — full project summary with joined cost/issue counts.
  - `list_projects(status=None, since=None, limit=100)` — queryable list.
  - `close()` — context-manager support.
- `NullLedger` with the same interface that does nothing; used when ledger is disabled.

### 2. Configuration wiring
- `ProjectConfig` gains an optional `ledger` subsection:
  ```yaml
  ledger:
    enabled: true
    path: output/projects.db
  ```
- `JobContext` gains `ledger: ProjectLedger | NullLedger`.
- `JobContext._resolve_ledger()` reads config and returns `ProjectLedger(path)` or `NullLedger()`.

### 3. CLI commands
New `apps/cli/src/video_editor_cli/main.py` commands:
- `ve project-init` — create/verify the ledger file.
- `ve project-list [--status STATUS] [--since YYYY-MM-DD] [--limit N]` — table output.
- `ve project-status <project_id>` — show project, jobs, costs, issues, artifacts.
- `ve cost-report [--provider PROVIDER] [--template TEMPLATE] [--since YYYY-MM-DD]` — aggregate spend.

### 4. Pipeline hooks
- **Project start**: `_context()` / pipeline entry records/upserts project row with status `running`.
- **Stage transitions**: `JobContext.manifest().stage_done(...)` also writes/updates a `jobs` row.
- **Asset generation**: `AssetIngestionManager._generate_one` writes a `cost_lines` row after each provider call (use `budget` estimate + provider metadata actual cost).
- **Fallbacks**: when FAL falls back to stub, cost line status is `fallback` and notes the original provider.
- **Issues**: `PostRenderReviewSkill`, `VerifySkill`, and `QASkill` write `issues` rows for any failing check.
- **Export**: `ExportSkill.run` records `artifacts` rows for final video, report, and decision log; updates project status to `complete`, `partial` (if issues exist), or `dropped`.
- **Failures**: top-level CLI exception handlers catch `BudgetExceededError`, `RuntimeError`, and friends and mark project `failed` or `dropped` with the error text in `notes`.

### 5. Worker/queue integration
- Worker `_handle_run` and other handlers receive the same ledger instance from `JobContext`.
- Because SQLite file access is local, the worker mode must use the same host filesystem for `output/projects.db`; document this limitation.

### 6. Tests
- `tests/test_ledger.py`:
  - init, upsert project, record cost, record issue, list/query.
  - verify `NullLedger` no-op behavior.
  - verify provider usage rollup math.
- `tests/test_cli_ledger.py`:
  - `ve project-init` creates the DB.
  - A full `ve run` writes a `complete` project row, cost lines, and artifacts.
  - `ve project-list` and `ve project-status` return expected data.

## Files to modify / create
1. **New**: `packages/state/src/video_editor_state/ledger.py`
2. **New**: `packages/state/src/video_editor_state/ledger_models.py` (optional Pydantic models for ledger rows)
3. **Modify**: `packages/state/src/video_editor_state/job.py` — add ledger to `JobContext`.
4. **Modify**: `video_editor_config/config.py` (or wherever `ProjectConfig` lives) — add `ledger` section.
5. **Modify**: `apps/cli/src/video_editor_cli/main.py` — add CLI commands and pass ledger-aware context.
6. **Modify**: `packages/asset_ingestion/src/video_editor_asset_ingestion/manager.py` — record cost lines.
7. **Modify**: `packages/state/src/video_editor_state/manifest.py` (or `JobContext.manifest`) — write stage rows.
8. **Modify**: `skills/export/src/export/export.py` — record artifacts and final status.
9. **Modify**: `skills/qa/qa.py`, `verify/verify.py`, `review/review.py` — record issues.
10. **New tests**: `tests/test_ledger.py`, `tests/test_cli_ledger.py`.

## Validation plan
1. `uv run pytest tests/test_ledger.py tests/test_cli_ledger.py tests/test_asset_ingestion.py tests/test_pipeline.py -q` passes.
2. `uv run ruff check .` clean.
3. `uv run mypy packages/state/src/video_editor_state/ledger.py` clean.
4. Manual smoke: `ve project-init && ve run <source> -j ledger-smoke --budget 5.0 && ve project-status ledger-smoke` shows complete status and non-empty cost lines.

## Risks / mitigations
- **SQLite concurrent writes from worker pool**: SQLite handles multiple readers but serializes writers. Mitigation: keep writes short and use a timeout; document that worker concurrency should stay low or use WAL mode (`PRAGMA journal_mode=WAL`).
- **Path escaping**: ledger path must be resolved relative to repo root/output root. Mitigation: validate the resolved path is under the output directory or repo root, reject absolute paths outside.
- **Privacy**: project DB accumulates source paths and prompts. Mitigation: ledger is local file; never upload. Document retention policy.
- **Test isolation**: tests must use temp DB. Mitigation: `ProjectLedger` accepts a path; tests create a `tmp_path / "ledger.db"`.

## Out of scope for Phase E
- Web dashboard or REST API for the ledger.
- Cloud-hosted backend (Postgres, Supabase, etc.).
- Migration framework beyond idempotent `CREATE TABLE IF NOT EXISTS`.
- Real-time streaming ledger updates from remote workers.
