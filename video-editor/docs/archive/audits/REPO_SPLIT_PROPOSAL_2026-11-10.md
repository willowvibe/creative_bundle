# Lightening the engine — what to split out of `video-editor`

**Date:** 2026-11-10 · **Baseline:** `main` at `e48aadc` (now `760a592`) · **Status:** steps 1–6 done
2026-11-11 (step 5 without the repo move; see its result).

> **Step 1 result (2026-11-11).** Full backup first:
> `../video-editor-full-2026-11-11.bundle` (654 MB, verified, restores by `git clone`). Then
> `git filter-repo --strip-blobs-with-ids` removed the 43 blobs of 500 KB or more that no branch tip
> contains (693 MB, all under the old `projects/` and `videos/`). `.git` went from 916 MB to **20 MB**.
> All 527 commits kept; every branch tip has the identical tree; `git fsck` clean. Every commit hash
> changed: hashes quoted in older docs refer to the old history, and
> `../video-editor-commit-map-2026-11-11.txt` maps old to new (e.g. `b88007d` → `fee3c1f`,
> `ebe6636` → `6a344d1`).

> **Steps 2–4 result (2026-11-11).** Three sibling repos under `creative_repos/`, each built with
> `git filter-repo` from this repo so its history comes along:
> - `video-projects`: `videos/` and `shorts/`. The real 8.5 GB folder moved in; it versions 195 scripts,
>   notes and configs (renders, caches and build output ignored). `video-editor/videos` and `/shorts` are
>   ignored symlinks to it, so `ve explain videos/...` still works. `demo/` stayed: 11 engine tests use it.
> - `vocal-to-song`: the skill, linked as `~/.claude/skills/vocal-to-song`.
> - `drama-studio`: the review app (with its local `.wrangler` state), the six drama scripts (including
>   `story_rules.py` and `episode_state.py`), their five test files (40 tests, all pass there), the
>   `drama-episode` skill and the `DRAMA_*` docs. Stdlib only; its `.env` and `videos` are symlinks.

> **Step 6 result (2026-11-11).** New workspace package `packages/maps` (`video_editor_maps`): `geo/` (code and
> the bundled Natural Earth data) and `scenes.py` (was `video_editor_core/map_scenes.py`), moved with `git mv`.
> It depends on core; core does not import it. The scene-plan adapter gets `render_map` through the
> `video_editor.scene_renderers` entry point (`video_editor_core.scene_renderers`), and a new import-linter
> contract forbids `video_editor_core` → `video_editor_maps`. Pipelines import `video_editor_maps` directly.
> Install with `uv sync --all-packages` (as CI and `scripts/bootstrap.sh` already do).

> **Step 5 result (2026-11-11).** The FableCut and ComfyStudio bridges are optional. The CLI no longer
> imports them at start-up: `video_editor_cli.bridge_loader.bridge()` imports one only when a bridge command
> runs, and prints an install hint (exit 1) when it is absent. `apps/cli` lists them under a `bridges` extra
> instead of its dependencies (`uv sync --all-packages` still installs them); pipelines already imported them
> inside the handlers. A new import-linter contract lets only the CLI and pipelines import the bridges. They
> stay in this repo as workspace packages: moving them to a `video-editor-bridges` repo is now a file move
> plus an entry point, not a refactor, and is left until there is a reason to version them apart.

## 1. Where the weight actually is

| Measure | Size |
|---|---|
| Tracked files (working tree) | ~28 MB: `packages` 8.0 MB, `shorts` 6.2 MB, `videos` 4.7 MB, `tests` 2.8 MB, `docs` 2.4 MB, `demo` 0.8 MB |
| `.git` (history) | **914 MB** (895 MB packed) |
| History by top-level folder | `projects/` ~436 MB, `videos/` ~233 MB, everything else ~90 MB |
| Largest historical files | `projects/ligastakam/captions/with_captions.mp4` 319 MB; four `videos/2026-07-12/suryastakam/*.mp4` renders at 49–55 MB each; dozens of 7–9 MB cached PNGs under `projects/*/.cache/` |
| Ignored but on disk | `videos/` 8.6 GB, `.venv` 648 MB, `qa/` 506 MB, `tools/` 495 MB, `node_modules` 415 MB, `packages/tools/FableCut` 380 MB |

**The engine is not heavy; its history is.** About 670 MB of `.git` is renders and caches that were
committed once (`projects/`, `videos/`) and are no longer tracked: `projects/` is now a symlink to
`/mnt/data2/video-editor-data/projects`. No code split comes close to that saving.

## 2. Code coupling

Python lines and cross-package imports (`from video_editor_*`), from `git ls-files`:

| Package | Lines | Imported by | Imports |
|---|---|---|---|
| core | 17,041 | pipelines (67), engine (18), cli (19), skills (27), … | ffmpeg |
| skills | 14,980 | (entry points) | ffmpeg (73), core, state, llm, asset_ingestion |
| asset_ingestion | 12,158 | cli, pipelines, engine, mcp_server, skills | ffmpeg, state, core |
| pipelines | 9,091 | cli, engine, mcp_server | core, ffmpeg, asset_ingestion, state, … |
| cli | 6,957 | — | everything |
| state 3,210 · ffmpeg 2,019 · llm 1,190 · config 476 · tools 321 | | used everywhere | |
| engine | 2,409 | cli, mcp_server | core, asset_ingestion, state, … |
| mcp_server | 2,107 | nothing | asset_ingestion, state, engine, … |
| fablecut_bridge | 1,647 | cli (8), pipelines (4) | core, ffmpeg, asset_ingestion |
| comfystudio_bridge | 523 | cli (2), pipelines (2) | core, asset_ingestion |
| apps/drama-review | JS app | nothing | nothing (Cloudflare Workers) |

The core packages (state, ffmpeg, config, llm, core, asset_ingestion, pipelines, skills, cli, engine) form
one tightly coupled engine: pipelines alone imports core 67 times. Splitting them into repos would add
version pinning and cross-repo releases for no runtime gain. **Keep them together.**

## 3. Recommendations

### Do now (high value, low risk)

1. **Shrink the history.** Rewrite history to drop `projects/` and the large `videos/**/*.mp4|png|mp3`
   blobs (`git filter-repo --path projects --invert-paths` plus a size filter). Expected `.git`: roughly
   150–250 MB instead of 914 MB. The repo has no remote, so nobody else's clone breaks. This is
   irreversible: take a full backup (`git bundle create ../video-editor-full.bundle --all`) first, and run
   it only on your explicit go-ahead.
2. **Move the drama stack to its own repo (`drama-studio`).** `apps/drama-review` (Cloudflare app),
   `scripts/drama_board.py`, `scripts/drama_review.py`, `scripts/dialogue_check.py`,
   `scripts/flow_call.py`, their tests, `.claude/skills/drama-episode` and the `DRAMA_*` docs. None of them imports the
   engine (no `video_editor_*` imports), and nothing in the engine imports them. This is the cleanest seam in the repo.
3. **Move the `vocal-to-song` skill out** (`.claude/skills/vocal-to-song`, ~76 KB of audio scripts).
   It is a music pipeline unrelated to video editing, and it was the only source of the repo-wide lint
   findings (#336). Put it in its own small repo, or in `~/.claude/skills/` if it is personal.
4. **Move content out of the code repo.** `videos/` (scripts and tracked frames), `shorts/` (including a
   6 MB music file) and `demo/raw.mp4` are content, not engine. Give them a `video-projects` repo or keep
   them under `/mnt/data2/video-editor-data/`, as `projects/` already is. The engine keeps `fixtures/` and
   `examples/` for its tests.

### Consider (medium value)

5. **External-editor bridges as optional plugins** (`fablecut_bridge`, `comfystudio_bridge`, plus the
   ignored 380 MB `packages/tools/FableCut` checkout). They are optional integrations used only by a few
   CLI commands and pipelines. Move them to a `video-editor-bridges` repo, loaded through an entry point
   (`video_editor.bridges`), so the core installs without them. This first needs the imports in cli and
   pipelines made lazy and optional.
6. **Map engine as its own workspace package, not its own repo.** `map_scenes.py` is now ~4,500 lines and
   the bundled geography is ~2.8 MB. A `packages/maps` package (`video_editor_maps`: geo, map_scenes, data,
   the compaction scripts) keeps `core` lean and gives the map work clear ownership. It is still compiled
   by core's adapter, so it should stay in this repo.

### Do not split

- The core packages listed in section 2.
- `mcp_server` (2.1k lines, imports the engine directly; it is the engine's own MCP face).
- `apps/transcribe` (127 lines) and `packages/tools` (321 lines): too small to stand alone.

## 4. Order of work

1. Backup bundle, then the history rewrite (step 1): the biggest win.
2. Content out (step 4) and `vocal-to-song` out (step 3): no code changes.
3. `drama-studio` repo (step 2): move the files, keep a short pointer in `docs/README.md`.
4. Optional: the maps package (step 6), then bridges as plugins (step 5).

Each move gets its own commit, with the full test suite run before and after.
