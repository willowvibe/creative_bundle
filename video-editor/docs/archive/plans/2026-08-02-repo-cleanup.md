# Repo Cleanup & Reorganization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clean up the video-editor monorepo: update .gitignore, remove junk files, consolidate packages under `packages/`, merge output directories, organize scripts, and commit all changes.

**Architecture:** Six independent tasks that can be executed sequentially. Each task is self-contained with its own verification step. The package move (Task 3) is the highest-risk task since it touches `pyproject.toml` workspace config and mypy paths.

**Tech Stack:** Python monorepo with uv workspaces, git

## Global Constraints

- All imports use package names (`video_editor_core`, `video_editor_tools`), not disk paths — moving directories doesn't break imports
- `pyproject.toml` has 5 references to `engine` and `tools` as disk paths that must be updated
- Tests must pass after each task
- Commit after each task with descriptive message

---

### Task 1: Update .gitignore

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add missing patterns to .gitignore**

Append these lines to `.gitignore`:

```gitignore
# Node.js
node_modules/

# Runtime outputs
output/
*.db
*.db-shm
*.db-wal

# Project work directories (scratch space)
projects/*/output/
projects/*/work/
videos/*/work/

# Temp files
*.tmp_*
```

- [ ] **Step 2: Verify git status is clean for these patterns**

Run: `git status --short`
Expected: `node_modules/`, `output/`, `outputs/*.db*`, `projects/*/output/`, `projects/*/work/`, `videos/*/work/` should no longer appear as untracked.

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: add node_modules, output, db, work dirs to .gitignore"
```

---

### Task 2: Clean Loose Root Files

**Files:**
- Delete: `EOF`, `.tmp_download_flux_comfy.py`
- Move: `frame.md` → `docs/design/capsule-frame.md`

- [ ] **Step 1: Delete junk files**

```bash
rm EOF
rm .tmp_download_flux_comfy.py
```

- [ ] **Step 2: Create docs/design/ and move frame.md**

```bash
mkdir -p docs/design
git mv frame.md docs/design/capsule-frame.md
```

- [ ] **Step 3: Verify**

Run: `ls EOF .tmp_download_flux_comfy.py frame.md 2>&1`
Expected: All three should report "No such file or directory"

Run: `ls docs/design/capsule-frame.md`
Expected: File exists

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: remove junk files, move frame.md to docs/design/"
```

---

### Task 3: Consolidate Packages — Move engine/ and tools/ into packages/

**Files:**
- Move: `engine/` → `packages/core/`
- Move: `tools/` → `packages/tools/`
- Modify: `pyproject.toml` (5 locations)

- [ ] **Step 1: Move engine/ to packages/core/**

```bash
git mv engine packages/core
```

- [ ] **Step 2: Move tools/ to packages/tools/**

```bash
git mv tools packages/tools
```

- [ ] **Step 3: Update pyproject.toml — coverage sources (lines 55-56)**

Change:
```toml
source = [
    "packages",
    "apps",
    "engine",
    "tools",
    "skills",
]
```
To:
```toml
source = [
    "packages",
    "apps",
    "skills",
]
```

`packages/` already covers `packages/core/` and `packages/tools/`.

- [ ] **Step 4: Update pyproject.toml — workspace members (lines 82-83)**

Change:
```toml
    "engine",
    "tools",
```
To:
```toml
    "packages/core",
    "packages/tools",
```

- [ ] **Step 5: Update pyproject.toml — mypy_path (line 149)**

Change:
```
$MYPY_CONFIG_FILE_DIR/engine/src:$MYPY_CONFIG_FILE_DIR/tools/src:
```
To:
```
$MYPY_CONFIG_FILE_DIR/packages/core/src:$MYPY_CONFIG_FILE_DIR/packages/tools/src:
```

- [ ] **Step 6: Verify package resolution**

Run: `uv sync`
Expected: Should complete without errors — uv resolves workspace members by name, not path

- [ ] **Step 7: Run tests to verify imports still work**

Run: `uv run pytest tests/ -x --tb=short -q 2>&1 | tail -5`
Expected: All tests pass (same count as before the move)

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: move engine/→packages/core/, tools/→packages/tools/"
```

---

### Task 4: Merge outputs/ into output/

**Files:**
- Move: `outputs/projects.db*` → `output/`
- Move: `outputs/ligastakam/final.mp4` → `output/ligastakam/final.mp4`
- Delete: `outputs/` (empty after moves)

- [ ] **Step 1: Move database files**

```bash
mv outputs/projects.db output/
mv outputs/projects.db-shm output/
mv outputs/projects.db-wal output/
```

- [ ] **Step 2: Move ligastakam render**

```bash
mkdir -p output/ligastakam
mv outputs/ligastakam/final.mp4 output/ligastakam/final.mp4
```

- [ ] **Step 3: Remove empty outputs/ directory**

```bash
rmdir outputs/ligastakam
rmdir outputs/
```

- [ ] **Step 4: Verify**

Run: `ls outputs/ 2>&1`
Expected: "No such file or directory"

Run: `ls output/projects.db output/ligastakam/final.mp4`
Expected: Both files exist

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: merge outputs/ into output/, remove empty outputs/"
```

---

### Task 5: Organize scripts/ into Subdirectories

**Files:**
- Move: 7 project-specific scripts → `scripts/projects/`
- Move: 11 pod management scripts → `scripts/pod/`

- [ ] **Step 1: Create subdirectories**

```bash
mkdir -p scripts/projects scripts/pod
```

- [ ] **Step 2: Move project-specific build scripts**

```bash
git mv scripts/saju_generate_voiceover.py scripts/projects/
git mv scripts/saju_run_pipeline.py scripts/projects/
git mv scripts/saju_run_pipeline_v2.py scripts/projects/
git mv scripts/saju_run_pipeline_v3.py scripts/projects/
git mv scripts/saju_transcribe.py scripts/projects/
git mv scripts/compose_anime_tram.py scripts/projects/
git mv scripts/compose_cosmic_saju.py scripts/projects/
git mv scripts/generate_shiva_bhajan.py scripts/projects/
```

- [ ] **Step 3: Move pod management scripts**

```bash
git mv scripts/install_acestep_on_pod.sh scripts/pod/
git mv scripts/install_indic_mio_on_pod.sh scripts/pod/
git mv scripts/install_jupyter_on_pod.sh scripts/pod/
git mv scripts/start_acestep_api.sh scripts/pod/
git mv scripts/start_comfyui_tunnel.sh scripts/pod/
git mv scripts/start_jupyter.sh scripts/pod/
git mv scripts/start_miotts_server.sh scripts/pod/
git mv scripts/start_vllm_tts_backend.sh scripts/pod/
git mv scripts/stop_acestep_api.sh scripts/pod/
git mv scripts/stop_comfyui_tunnel.sh scripts/pod/
git mv scripts/download_models_to_pod.py scripts/pod/
```

- [ ] **Step 4: Verify structure**

Run: `ls scripts/projects/ && echo "---" && ls scripts/pod/ && echo "---" && ls scripts/`
Expected: projects/ has 8 scripts, pod/ has 11 scripts, root scripts/ has remaining reusable tools

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: organize scripts into projects/ and pod/ subdirectories"
```

---

### Task 6: Stage and Commit All Remaining Changes

**Files:**
- New: `apps/cli/src/video_editor_cli/handlers.py`, `apps/cli/src/video_editor_cli/payloads.py`
- New: `docs/AUDIT_2026-08-01.md`, `docs/AUDIT_2026-08-01_v2.md`, `docs/prompt_for_optimal_results.md`, `docs/superpowers/plans/2026-08-01-audit-remediation.md`
- Modified: 19 files from audit remediation (see git status)

- [ ] **Step 1: Stage all remaining changes**

```bash
git add apps/cli/src/video_editor_cli/handlers.py
git add apps/cli/src/video_editor_cli/payloads.py
git add docs/AUDIT_2026-08-01.md
git add docs/AUDIT_2026-08-01_v2.md
git add docs/prompt_for_optimal_results.md
git add docs/superpowers/plans/2026-08-01-audit-remediation.md
git add docs/superpowers/plans/2026-08-02-repo-cleanup.md
git add -u  # stage all modified files
```

- [ ] **Step 2: Final verification — run full test suite**

Run: `uv run pytest tests/ -x --tb=short -q 2>&1 | tail -10`
Expected: All tests pass

- [ ] **Step 3: Final git status check**

Run: `git status`
Expected: All changes staged, nothing left untracked (except gitignored files)

- [ ] **Step 4: Commit**

```bash
git commit -m "chore: commit audit remediation changes, new CLI modules, and docs

- New CLI handlers.py and payloads.py modules
- Audit findings and remediation plan docs
- 19 modified files from audit remediation work
- Repo cleanup: gitignore, package consolidation, script organization

Co-Authored-By: Claude <noreply@anthropic.com>"
```
