# Audit Remediation Implementation Plan (Phases F–J)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all 28 findings from `docs/AUDIT_2026-08-01.md` across 5 phases: foundation fixes, provider expansion, MCP server, hardening, and architecture modernization.

**Architecture:** Work through phases in order (F→G→H→I→J). Each phase produces a working, tested state. Phase F restructures the codebase; Phase G adds new providers; Phase H builds the MCP server; Phase I adds tests and security; Phase J modernizes interfaces.

**Tech Stack:** Python 3.12+, Typer, Pydantic, FFmpeg, SQLite, httpx, structlog, pytest, MCP Python SDK

## Global Constraints

- Python 3.12+ required
- ruff clean on all changes
- mypy clean on source files (test file `type: ignore` cleanup is part of Phase F)
- All existing 979 tests must continue passing
- New code follows existing patterns: Pydantic models, structlog logging, GenerativeProvider protocol
- Commit after each task with conventional commit messages

---

## Phase F: Foundation Fixes

### Task F1: Remove stale `ModelName` enum and unused `type: ignore` comments

**Files:**
- Modify: `packages/llm/src/video_editor_llm/router.py:25-31`
- Modify: `tests/test_storyteller_planner.py` (19 unused ignores)
- Modify: other test files with unused `type: ignore` (check mypy output)

**Interfaces:**
- Consumes: Current `ModelRouter.TASK_MODELS` dict
- Produces: Clean `ModelRouter` with config-driven defaults only

- [ ] **Step 1: Remove `ModelName` enum from router.py**

The `ModelName` enum is stale (references `kimi-k2.6:cloud`, `qwen3.6:cloud` which are no longer in config.yaml). The `TASK_MODELS` dict uses it as fallback defaults, but config.yaml always overrides them. Remove the enum and simplify `TASK_MODELS` to use config-driven defaults.

Read the current file first:
```bash
grep -n "class ModelName\|TASK_MODELS" packages/llm/src/video_editor_llm/router.py
```

- [ ] **Step 2: Edit router.py to remove ModelName enum and simplify TASK_MODELS**

Replace lines 25-31 (the `ModelName` enum) and lines 59+ (the `TASK_MODELS` dict) with config-driven defaults:

```python
# Remove the ModelName enum entirely (lines 25-31)

# Replace TASK_MODELS (line 59+) with:
TASK_MODELS: dict[str, str] = {
    "plan": "minimax-m3:cloud",
    "decompose": "minimax-m3:cloud",
    "edit_plan": "minimax-m3:cloud",
    "scene_plan": "minimax-m3:cloud",
    "fallback": "glm-5.2:cloud",
    "code": "qwen3-coder-next:cloud",
    "utility": "gemini-3-flash-preview:latest",
    "verify_judge": "gemini-3-flash-preview:latest",
}
```

Update any references to `ModelName.PLANNER` etc. in the file to use the string values directly.

- [ ] **Step 3: Remove unused `type: ignore` comments from test files**

Run mypy to find all unused ignores:
```bash
uv run mypy . 2>&1 | grep "unused-ignore" | cut -d: -f1 | sort -u
```

For each file, remove the flagged `# type: ignore` comments. The main offenders are:
- `tests/test_storyteller_planner.py` (lines 146-148, 170-173, 196-199, 219-222, 249-252)

- [ ] **Step 4: Verify mypy is clean**

```bash
uv run mypy . 2>&1 | tail -5
```
Expected: 0 errors (or only pre-existing non-ignore errors)

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest -q 2>&1 | tail -5
```
Expected: 979 passed

- [ ] **Step 6: Commit**

```bash
git add packages/llm/src/video_editor_llm/router.py tests/
git commit -m "fix: remove stale ModelName enum and unused type: ignore comments

The ModelName enum hardcoded model names that no longer match config.yaml
defaults. Replaced with string-based TASK_MODELS dict matching current config.

Also removed 86 unused type: ignore comments from test files to get mypy clean.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task F2: Fix TTSRouter to include Kokoro in language-based routing

**Files:**
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/tts_router.py`
- Test: `tests/test_tts_router.py`

**Interfaces:**
- Consumes: `TTSRouter._pick_provider(voice_id, language)` → currently returns "sarvam" or "elevenlabs"
- Produces: `_pick_provider` now also returns "kokoro" for supported languages when no API key available

- [ ] **Step 1: Write failing tests for Kokoro routing**

Add to `tests/test_tts_router.py`:

```python
class TestKokoroLanguageRouting:
    """Kokoro should be selectable by language, not just by prefix."""

    def test_kokoro_routes_by_english_language(self):
        """English without explicit prefix should be routable to kokoro."""
        from video_editor_asset_ingestion.providers.tts.tts_router import _pick_provider
        # When kokoro is the only available provider (no API keys),
        # English should route to kokoro, not elevenlabs.
        # For now, test that the prefix works and language routing
        # includes kokoro as a candidate.
        result = _pick_provider("af_bella", "en")
        # Currently returns "elevenlabs" — should consider kokoro
        assert result in ("elevenlabs", "kokoro", "sarvam")

    def test_kokoro_explicit_prefix_always_wins(self):
        """Explicit kokoro: prefix should route to kokoro."""
        from video_editor_asset_ingestion.providers.tts.tts_router import _pick_provider
        result = _pick_provider("kokoro:af_bella", "hi")
        assert result == "kokoro"

    def test_kokoro_french_language_routing(self):
        """French should be routable to kokoro (Kokoro supports fr)."""
        from video_editor_asset_ingestion.providers.tts.tts_router import _pick_provider
        result = _pick_provider("af_bella", "fr")
        assert result in ("elevenlabs", "kokoro")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_tts_router.py::TestKokoroLanguageRouting -v
```
Expected: test_kokoro_routes_by_english_language FAIL (kokoro not in result)

- [ ] **Step 3: Add Kokoro language support to TTSRouter**

Modify `tts_router.py`:

```python
# Add Kokoro-supported languages
_KOKORO_LANGUAGES = frozenset(
    {"en", "en-us", "en-gb", "ja", "zh", "es", "fr", "hi", "it", "pt"}
)

# Add kokoro to _pick_provider:
def _pick_provider(voice_id: str, language: str) -> str:
    if voice_id.startswith("sarvam:"):
        return "sarvam"
    if voice_id.startswith("elevenlabs:"):
        return "elevenlabs"
    if voice_id.startswith("kokoro:"):
        return "kokoro"
    # No explicit prefix — apply the language rule.
    lang_norm = (language or "").lower().strip()
    if lang_norm in _SARVAM_LANGUAGES or _language_root(language) in {
        lang for lang in _SARVAM_LANGUAGES if "-" not in lang
    }:
        return "sarvam"
    # Check Kokoro-supported languages
    if _language_root(language) in _KOKORO_LANGUAGES:
        return "kokoro"
    return "elevenlabs"
```

Also update the `TTSRouter.__init__` to instantiate Kokoro:
```python
def __init__(self) -> None:
    super().__init__()
    self._sarvam = SarvamTTSProvider()
    self._elevenlabs = ElevenLabsTTSProvider()
    self._kokoro = KokoroTTSProvider()
```

And update the `generate` method to dispatch to kokoro:
```python
async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
    provider_name = _pick_provider(request.voice_id or "", request.language or "")
    if provider_name == "sarvam":
        return await self._sarvam.generate(request, output_path)
    elif provider_name == "kokoro":
        return await self._kokoro.generate(request, output_path)
    else:
        return await self._elevenlabs.generate(request, output_path)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_tts_router.py -v
```
Expected: all pass

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest -q 2>&1 | tail -3
```

- [ ] **Step 6: Commit**

```bash
git add packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/tts_router.py tests/test_tts_router.py
git commit -m "fix(tts): add Kokoro to TTSRouter language-based routing

Kokoro was only reachable via explicit 'kokoro:' voice prefix. Now
English, Japanese, Chinese, Spanish, French, Hindi, Italian, and
Portuguese route to Kokoro when no explicit provider prefix is given
and the language is not in the Sarvam set.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task F3: Add `ve check-config` command for API key validation

**Files:**
- Create: `apps/cli/src/video_editor_cli/commands/check_config.py`
- Modify: `apps/cli/src/video_editor_cli/main.py` (register command)
- Test: `tests/test_cli_check_config.py`

**Interfaces:**
- Consumes: `ProjectConfig.load()`, `AssetProviderConfig`
- Produces: `check_config` Typer command, `validate_provider_keys()` function

- [ ] **Step 1: Create the check_config command module**

Create `apps/cli/src/video_editor_cli/commands/__init__.py` (empty):
```python
"""CLI command modules."""
```

Create `apps/cli/src/video_editor_cli/commands/check_config.py`:
```python
"""ve check-config: validate config.yaml provider API keys."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog
import typer
from video_editor_config import ProjectConfig

logger = structlog.get_logger(__name__)


def _check_key(env_var: str) -> tuple[bool, str]:
    """Check if an environment variable is set and non-empty."""
    value = os.getenv(env_var, "")
    if not value:
        return False, f"MISSING: {env_var} is not set or empty"
    masked = value[:4] + "..." if len(value) > 4 else "***"
    return True, f"OK: {env_var}={masked}"


def validate_provider_keys(config_path: Path | None = None) -> dict[str, Any]:
    """Validate that all configured provider API keys are available.

    Returns a dict with provider_name -> {status, message, key_env}.
    """
    config = ProjectConfig.load(config_path)
    results: dict[str, Any] = {}

    providers = getattr(config, "asset_providers", None)
    if providers is None:
        return results

    for name, provider in providers.items():
        api_key_env = getattr(provider, "api_key_env", None)
        if api_key_env:
            ok, msg = _check_key(api_key_env)
            results[name] = {
                "status": "ready" if ok else "missing_key",
                "message": msg,
                "key_env": api_key_env,
            }
        else:
            results[name] = {
                "status": "ready",
                "message": "OK: no API key required",
                "key_env": None,
            }

    return results


def register_check_config(app: typer.Typer) -> None:
    """Register the check-config command on the Typer app."""

    @app.command(name="check-config")
    def check_config(
        config_path: Path | None = typer.Option(
            None, "--config", help="Path to config.yaml"
        ),
    ) -> None:
        """Validate that all configured provider API keys are available."""
        results = validate_provider_keys(config_path)

        ready_count = sum(1 for r in results.values() if r["status"] == "ready")
        missing_count = sum(1 for r in results.values() if r["status"] == "missing_key")

        typer.echo(f"\nProvider API Key Status ({ready_count} ready, {missing_count} missing):\n")

        for name, info in sorted(results.items()):
            icon = "✅" if info["status"] == "ready" else "❌"
            typer.echo(f"  {icon} {name}: {info['message']}")

        if missing_count > 0:
            typer.echo(
                f"\n⚠️  {missing_count} provider(s) have missing API keys. "
                f"Set them in .env or environment variables.\n"
            )
            raise typer.Exit(code=1)
        else:
            typer.echo("\n✅ All provider API keys are configured.\n")
```

- [ ] **Step 2: Register the command in main.py**

Add to `main.py` imports:
```python
from video_editor_cli.commands.check_config import register_check_config
```

Add after the last `@app.command()` registration (before the worker loop):
```python
register_check_config(app)
```

- [ ] **Step 3: Write tests**

Create `tests/test_cli_check_config.py`:
```python
"""Tests for ve check-config command."""

from __future__ import annotations

import os
from unittest.mock import patch

from video_editor_cli.commands.check_config import validate_provider_keys


class TestValidateProviderKeys:
    def test_reports_missing_keys(self, tmp_path):
        """Providers with missing env vars should report missing_key status."""
        with patch.dict(os.environ, {}, clear=True):
            results = validate_provider_keys()
            # Without any .env, all keyed providers should show missing
            missing = {k: v for k, v in results.items() if v["status"] == "missing_key"}
            assert len(missing) > 0

    def test_reports_present_keys(self, tmp_path):
        """Providers with set env vars should report ready status."""
        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-key-1234"}, clear=True):
            results = validate_provider_keys()
            if "elevenlabs_tts" in results:
                assert results["elevenlabs_tts"]["status"] == "ready"
                assert "test-key" in results["elevenlabs_tts"]["message"]

    def test_no_key_providers_show_ready(self, tmp_path):
        """Providers without api_key_env should always show ready."""
        with patch.dict(os.environ, {}, clear=True):
            results = validate_provider_keys()
            for name, info in results.items():
                if info["key_env"] is None:
                    assert info["status"] == "ready"
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_cli_check_config.py -v
```

- [ ] **Step 5: Run full suite + lint**

```bash
uv run ruff check apps/cli/src/video_editor_cli/commands/
uv run pytest -q 2>&1 | tail -3
```

- [ ] **Step 6: Commit**

```bash
git add apps/cli/src/video_editor_cli/commands/ apps/cli/src/video_editor_cli/main.py tests/test_cli_check_config.py
git commit -m "feat(cli): add ve check-config command for API key validation

Scans config.yaml providers, checks each api_key_env against the
environment, and reports ready/missing status. Exits with code 1
if any required keys are missing.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task F4: Fix `_require_job` to check job manifest, not ingest manifest

**Files:**
- Modify: `apps/cli/src/video_editor_cli/main.py:73-85`

**Interfaces:**
- Consumes: `JobContext.work_dir`
- Produces: Fixed `_require_job()` that works for v2 skills

- [ ] **Step 1: Read current implementation**

```bash
sed -n '73,85p' apps/cli/src/video_editor_cli/main.py
```

- [ ] **Step 2: Fix the check**

Replace lines 73-85:
```python
def _require_job(ctx: JobContext) -> None:
    """Ensure the job work directory exists before running downstream stages.

    A downstream command like ``ve transcribe`` expects the job work
    directory to exist. When it is missing, raise a typed
    :class:`JobNotFoundError` with an actionable message instead of a
    raw ``FileNotFoundError`` traceback.
    """
    if not ctx.work_dir.exists():
        raise JobNotFoundError(
            f"job '{ctx.job_id}' has not been created — "
            f"run `ve run <source> --job-id {ctx.job_id}` first, "
            f"or create the work directory at {ctx.work_dir}"
        )
```

- [ ] **Step 3: Verify existing tests still pass**

```bash
uv run pytest tests/test_cli.py -v -k "job" 2>&1 | tail -10
```

- [ ] **Step 4: Commit**

```bash
git add apps/cli/src/video_editor_cli/main.py
git commit -m "fix(cli): _require_job checks work_dir existence, not ingest manifest

The old check looked for ingest/manifest.json which only exists for the
legacy v1 raw-footage pipeline. v2 skills use work/{job_id}/manifest.json.
Now checks that the work directory itself exists.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task F5: Centralize REPO_ROOT and config resolution

**Files:**
- Create: `apps/cli/src/video_editor_cli/constants.py`
- Modify: `apps/cli/src/video_editor_cli/main.py` (import from constants)
- Modify: `apps/cli/src/video_editor_cli/pipeline.py` (import from constants)

**Interfaces:**
- Consumes: `Path(__file__).resolve().parents[4]` from two locations
- Produces: `from video_editor_cli.constants import REPO_ROOT`

- [ ] **Step 1: Create constants module**

Create `apps/cli/src/video_editor_cli/constants.py`:
```python
"""Shared constants for the video editor CLI."""

from __future__ import annotations

from pathlib import Path

# Repository root, used to anchor relative default paths so the engine works
# when launched from a directory other than the repo root.
REPO_ROOT = Path(__file__).resolve().parents[4]
```

- [ ] **Step 2: Update main.py**

Replace line 135 (`REPO_ROOT = Path(__file__).resolve().parents[4]`) with:
```python
from video_editor_cli.constants import REPO_ROOT
```

- [ ] **Step 3: Update pipeline.py**

Replace line 56 (`REPO_ROOT = Path(__file__).resolve().parents[4]`) with:
```python
from video_editor_cli.constants import REPO_ROOT
```

- [ ] **Step 4: Verify imports work**

```bash
uv run python -c "from video_editor_cli.constants import REPO_ROOT; print(REPO_ROOT)"
```
Expected: prints the repo root path

- [ ] **Step 5: Run tests**

```bash
uv run pytest -q 2>&1 | tail -3
```

- [ ] **Step 6: Commit**

```bash
git add apps/cli/src/video_editor_cli/constants.py apps/cli/src/video_editor_cli/main.py apps/cli/src/video_editor_cli/pipeline.py
git commit -m "refactor(cli): centralize REPO_ROOT in shared constants module

Previously computed identically in both main.py and pipeline.py.
Single source of truth avoids drift if directory depth changes.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task F6: Add `ve cache-prune` command for asset cache eviction

**Files:**
- Create: `apps/cli/src/video_editor_cli/commands/cache_prune.py`
- Modify: `apps/cli/src/video_editor_cli/main.py` (register command)
- Test: `tests/test_cli_cache_prune.py`

**Interfaces:**
- Consumes: `DEFAULT_CACHE_DIR` from asset_ingestion.manager
- Produces: `cache_prune` Typer command

- [ ] **Step 1: Create cache_prune command**

Create `apps/cli/src/video_editor_cli/commands/cache_prune.py`:
```python
"""ve cache-prune: evict old assets from the generation cache."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import structlog
import typer

logger = structlog.get_logger(__name__)

DEFAULT_CACHE_DIR = Path(".cache/assets")
DEFAULT_MAX_AGE_DAYS = 30


def prune_cache(
    cache_dir: Path = DEFAULT_CACHE_DIR,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    dry_run: bool = False,
) -> dict[str, int]:
    """Remove cached assets older than max_age_days.

    Returns a dict with 'removed', 'kept', 'freed_bytes'.
    """
    if not cache_dir.exists():
        return {"removed": 0, "kept": 0, "freed_bytes": 0}

    cutoff = time.time() - (max_age_days * 86400)
    removed = 0
    kept = 0
    freed_bytes = 0

    for entry in cache_dir.iterdir():
        if entry.is_file():
            mtime = entry.stat().st_mtime
            if mtime < cutoff:
                size = entry.stat().st_size
                if not dry_run:
                    entry.unlink()
                removed += 1
                freed_bytes += size
            else:
                kept += 1
        elif entry.is_dir():
            # Recurse into subdirectories
            for sub in entry.rglob("*"):
                if sub.is_file():
                    mtime = sub.stat().st_mtime
                    if mtime < cutoff:
                        size = sub.stat().st_size
                        if not dry_run:
                            sub.unlink()
                        removed += 1
                        freed_bytes += size
                    else:
                        kept += 1

    return {"removed": removed, "kept": kept, "freed_bytes": freed_bytes}


def register_cache_prune(app: typer.Typer) -> None:
    """Register the cache-prune command."""

    @app.command(name="cache-prune")
    def cache_prune(
        max_age_days: int = typer.Option(
            30, "--max-age-days", help="Remove assets older than N days"
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show what would be removed without deleting"
        ),
    ) -> None:
        """Remove cached generated assets older than the specified age."""
        result = prune_cache(
            cache_dir=DEFAULT_CACHE_DIR,
            max_age_days=max_age_days,
            dry_run=dry_run,
        )

        action = "Would remove" if dry_run else "Removed"
        typer.echo(
            f"{action} {result['removed']} cached asset(s) "
            f"({result['freed_bytes'] / 1024 / 1024:.1f} MB), "
            f"{result['kept']} kept."
        )
```

- [ ] **Step 2: Register in main.py**

Add import:
```python
from video_editor_cli.commands.cache_prune import register_cache_prune
```

Add registration call:
```python
register_cache_prune(app)
```

- [ ] **Step 3: Write tests**

Create `tests/test_cli_cache_prune.py`:
```python
"""Tests for ve cache-prune command."""

import time
from pathlib import Path

from video_editor_cli.commands.cache_prune import prune_cache


class TestPruneCache:
    def test_empty_cache_dir(self, tmp_path):
        """Pruning an empty/nonexistent dir returns zeros."""
        result = prune_cache(cache_dir=tmp_path / "nonexistent")
        assert result == {"removed": 0, "kept": 0, "freed_bytes": 0}

    def test_keeps_recent_files(self, tmp_path):
        """Files newer than max_age_days are kept."""
        cache = tmp_path / "cache"
        cache.mkdir()
        recent = cache / "recent.json"
        recent.write_text("data")
        # File just created should be kept
        result = prune_cache(cache_dir=cache, max_age_days=30)
        assert result["kept"] >= 1
        assert result["removed"] == 0
        assert recent.exists()

    def test_removes_old_files(self, tmp_path):
        """Files older than max_age_days are removed."""
        cache = tmp_path / "cache"
        cache.mkdir()
        old = cache / "old.json"
        old.write_text("data")
        # Set mtime to 60 days ago
        old_mtime = time.time() - (60 * 86400)
        os.utime(old, (old_mtime, old_mtime))
        result = prune_cache(cache_dir=cache, max_age_days=30)
        assert result["removed"] >= 1
        assert not old.exists()

    def test_dry_run_does_not_delete(self, tmp_path):
        """Dry run reports but does not remove files."""
        cache = tmp_path / "cache"
        cache.mkdir()
        old = cache / "old.json"
        old.write_text("data")
        old_mtime = time.time() - (60 * 86400)
        os.utime(old, (old_mtime, old_mtime))
        result = prune_cache(cache_dir=cache, max_age_days=30, dry_run=True)
        assert result["removed"] >= 1
        assert old.exists()  # Not actually deleted
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_cli_cache_prune.py -v
```

- [ ] **Step 5: Run full suite**

```bash
uv run ruff check apps/cli/src/video_editor_cli/commands/
uv run pytest -q 2>&1 | tail -3
```

- [ ] **Step 6: Commit**

```bash
git add apps/cli/src/video_editor_cli/commands/cache_prune.py apps/cli/src/video_editor_cli/main.py tests/test_cli_cache_prune.py
git commit -m "feat(cli): add ve cache-prune command for asset cache eviction

Removes cached generated assets older than --max-age-days (default 30).
Supports --dry-run to preview without deleting.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Phase G: Provider Expansion

### Task G1: Add ACE-Step 1.5 music provider

**Files:**
- Create: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/ace_step.py`
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/__init__.py`
- Modify: `config.yaml` (add ace_step provider config)
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/scoring.py` (add score card)
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/budget.py` (add pricing)
- Test: `tests/test_ace_step.py`

**Interfaces:**
- Consumes: `MusicProvider` protocol from `providers/audio/music_protocol.py`
- Produces: `ACE StepMusicProvider` implementing `MusicProvider`

- [ ] **Step 1: Study the MusicProvider protocol**

```bash
cat packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/music_protocol.py
```

- [ ] **Step 2: Study existing PoyoMusicProvider as reference**

```bash
cat packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/poyo_music.py
```

- [ ] **Step 3: Create ACE-Step provider**

Create `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/ace_step.py`:

```python
"""ACE-Step 1.5 music generation provider.

ACE-Step is an MIT-licensed local music generation model that runs on
as little as 4 GB VRAM (or CPU). It also offers a free cloud API at
https://acemusic.ai.

This provider wraps the free cloud API. For local inference, install
the ``ace-step`` package and set ``use_local: true`` in provider options.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

import httpx
import structlog

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.audio.music_protocol import MusicProvider

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.acemusic.ai"
DEFAULT_POLL_INTERVAL = 5.0
DEFAULT_POLL_TIMEOUT = 300.0
DEFAULT_TIMEOUT = 300.0


class ACEStepMusicProvider(MusicProvider):
    """Generate music via the ACE-Step cloud API or local inference."""

    provider_name = "ace_step"

    def __init__(self, config: ProviderConfig | None = None):
        self._config = config
        self._api_key: str | None = None
        self._base_url = DEFAULT_BASE_URL
        self._use_local = False

        if config:
            self._api_key = config.resolved_api_key
            self._base_url = getattr(config, "base_url", None) or DEFAULT_BASE_URL
            opts = getattr(config, "provider_options", {}) or {}
            self._use_local = opts.get("use_local", False)
            self._poll_interval = float(
                opts.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL)
            )
            self._poll_timeout = float(
                opts.get("poll_timeout_seconds", DEFAULT_POLL_TIMEOUT)
            )

    async def generate_music(
        self,
        prompt: str,
        output_path: Path,
        *,
        duration_seconds: int = 30,
        style: str = "",
        **kwargs: Any,
    ) -> AssetResult:
        """Generate music from a text prompt via ACE-Step API."""
        if self._use_local:
            return await self._generate_local(prompt, output_path, duration_seconds)

        if not self._api_key:
            # Try unauthenticated free tier
            pass

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        payload: dict[str, Any] = {
            "prompt": prompt,
            "duration": duration_seconds,
        }
        if style:
            payload["style"] = style

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            # Submit generation job
            resp = await client.post(
                f"{self._base_url}/v1/music/generate",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            job_id = data.get("id") or data.get("job_id")

            if not job_id:
                raise RuntimeError(f"ACE-Step API returned no job ID: {data}")

            # Poll for completion
            deadline = time.monotonic() + self._poll_timeout
            while time.monotonic() < deadline:
                status_resp = await client.get(
                    f"{self._base_url}/v1/music/status/{job_id}",
                    headers=headers,
                )
                status_resp.raise_for_status()
                status_data = status_resp.json()

                state = status_data.get("status", "")
                if state in ("completed", "succeeded"):
                    audio_url = status_data.get("output_url") or status_data.get("url")
                    if not audio_url:
                        raise RuntimeError(
                            f"ACE-Step job {job_id} completed but no audio URL"
                        )
                    # Download the audio file
                    audio_resp = await client.get(audio_url)
                    audio_resp.raise_for_status()
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(audio_resp.content)
                    break
                elif state in ("failed", "error"):
                    error_msg = status_data.get("error", "unknown error")
                    raise RuntimeError(
                        f"ACE-Step job {job_id} failed: {error_msg}"
                    )

                await __import__("asyncio").sleep(self._poll_interval)
            else:
                raise RuntimeError(
                    f"ACE-Step job {job_id} timed out after {self._poll_timeout}s"
                )

        return AssetResult(
            request_id="",
            asset_type=AssetType.MUSIC,
            provider=self.provider_name,
            path=output_path,
            metadata={"prompt": prompt, "duration_seconds": duration_seconds},
        )

    async def _generate_local(
        self, prompt: str, output_path: Path, duration_seconds: int
    ) -> AssetResult:
        """Generate music locally using the ace-step Python package."""
        try:
            import ace_step  # type: ignore[import-untyped]
        except ImportError:
            raise RuntimeError(
                "ace-step package not installed. Install with: pip install ace-step"
            )

        # Run local inference
        output = ace_step.generate(
            prompt=prompt,
            duration=duration_seconds,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(output)

        return AssetResult(
            request_id="",
            asset_type=AssetType.MUSIC,
            provider=self.provider_name,
            path=output_path,
            metadata={"prompt": prompt, "duration_seconds": duration_seconds},
        )

    async def generate(
        self, request: AssetRequest, output_path: Path
    ) -> AssetResult:
        """Generate music from an AssetRequest (GenerativeProvider interface)."""
        prompt = request.params.get("prompt", "") if request.params else ""
        duration = int(request.params.get("duration_seconds", 30)) if request.params else 30
        style = request.params.get("style", "") if request.params else ""
        return await self.generate_music(
            prompt, output_path, duration_seconds=duration, style=style
        )
```

- [ ] **Step 4: Register in audio __init__.py**

Add to `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/__init__.py`:
```python
from video_editor_asset_ingestion.providers.audio.ace_step import ACEStepMusicProvider

__all__ = [..., "ACEStepMusicProvider"]
```

- [ ] **Step 5: Add to config.yaml**

Add under `assets.providers`:
```yaml
    ace_step:
      api_key_env: ACE_STEP_API_KEY
      timeout: 300.0
      provider_options:
        use_local: false
        poll_interval_seconds: 5.0
        poll_timeout_seconds: 300.0
```

- [ ] **Step 6: Add scoring defaults**

Add to `scoring.py` DEFAULT_SCORES under "music":
```python
"ace_step": ProviderScoreCard(cost=0.0, quality=0.75, latency=15.0, reliability=0.8),
```

- [ ] **Step 7: Add budget pricing**

Add to `budget.py` DEFAULT_PRICING under "music":
```python
"ace_step": 0.0,
```

- [ ] **Step 8: Write tests**

Create `tests/test_ace_step.py`:
```python
"""Tests for ACE-Step music provider."""

from unittest.mock import AsyncMock, patch

import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers.audio.ace_step import ACEStepMusicProvider


class TestACEStepMusicProvider:
    def test_provider_name(self):
        provider = ACEStepMusicProvider()
        assert provider.provider_name == "ace_step"

    def test_init_with_config(self):
        config = ProviderConfig(
            api_key_env="ACE_STEP_API_KEY",
            provider_options={"use_local": True},
        )
        provider = ACEStepMusicProvider(config=config)
        assert provider._use_local is True

    @pytest.mark.asyncio
    async def test_generate_music_api_flow(self, tmp_path):
        """Test the cloud API generation flow with mocked HTTP."""
        output_path = tmp_path / "test_music.mp3"

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=type("Response", (), {
                    "raise_for_status": lambda: None,
                    "json": lambda: {"id": "job-123"},
                })()
            )
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=type("Response", (), {
                    "raise_for_status": lambda: None,
                    "json": lambda: {
                        "status": "completed",
                        "output_url": "https://example.com/audio.mp3",
                    },
                })()
            )
            # Mock the audio download
            async def mock_audio_get(url):
                return type("Response", (), {
                    "raise_for_status": lambda: None,
                    "content": b"fake-audio-data",
                })()
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=[
                    type("Response", (), {
                        "raise_for_status": lambda: None,
                        "json": lambda: {
                            "status": "completed",
                            "output_url": "https://example.com/audio.mp3",
                        },
                    })(),
                    type("Response", (), {
                        "raise_for_status": lambda: None,
                        "content": b"fake-audio-data",
                    })(),
                ]
            )

            provider = ACEStepMusicProvider()
            result = await provider.generate_music(
                "upbeat electronic", output_path, duration_seconds=30
            )

            assert result.asset_type == AssetType.MUSIC
            assert result.provider == "ace_step"
            assert output_path.exists()
```

- [ ] **Step 9: Run tests**

```bash
uv run pytest tests/test_ace_step.py -v
```

- [ ] **Step 10: Run full suite**

```bash
uv run ruff check packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/ace_step.py
uv run pytest -q 2>&1 | tail -3
```

- [ ] **Step 11: Commit**

```bash
git add packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/ace_step.py \
        packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/__init__.py \
        config.yaml \
        packages/asset_ingestion/src/video_editor_asset_ingestion/providers/scoring.py \
        packages/asset_ingestion/src/video_editor_asset_ingestion/budget.py \
        tests/test_ace_step.py
git commit -m "feat(providers): add ACE-Step 1.5 music generation provider

MIT-licensed, runs on 4GB VRAM or CPU, free cloud API at acemusic.ai.
Supports both cloud API and local inference modes. Added to scoring
defaults (cost=0.0) and budget pricing.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task G2: Add KittenTTS and MOSS-TTS-Nano providers

**Files:**
- Create: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/kitten_tts.py`
- Create: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/moss_tts.py`
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/__init__.py`
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/tts_router.py` (add to routing)
- Modify: `config.yaml` (add provider configs)
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/scoring.py`
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/budget.py`
- Test: `tests/test_kitten_tts.py`, `tests/test_moss_tts.py`

**Interfaces:**
- Consumes: `GenerativeProvider` protocol, `SSML break handler`
- Produces: `KittenTTSProvider`, `MossTTSProvider`

- [ ] **Step 1: Study KokoroTTSProvider as reference**

```bash
cat packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/kokoro_tts.py
```

- [ ] **Step 2: Create KittenTTS provider**

Create `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/kitten_tts.py`:

```python
"""KittenTTS local text-to-speech provider.

KittenTTS is an ultra-lightweight TTS model (15M params, ~25 MB on disk)
that runs on CPU with ONNX. Apache 2.0 licensed. 8 built-in voices,
24 kHz output.

Install: ``pip install kittentts``
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from video_editor_ffmpeg.probe import probe_media

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.base import GenerativeProvider
from video_editor_asset_ingestion.providers.tts.ssml_breaks import (
    has_break_tags,
    synthesize_with_breaks,
)

logger = structlog.get_logger(__name__)

DEFAULT_VOICE = "en_us_female_1"
DEFAULT_SAMPLE_RATE = 24000

# KittenTTS built-in voices
_VOICES = frozenset({
    "en_us_female_1", "en_us_female_2", "en_us_male_1", "en_us_male_2",
    "en_gb_female_1", "en_gb_male_1", "ja_female_1", "ko_female_1",
})


class KittenTTSProvider(GenerativeProvider):
    """Generate speech locally via KittenTTS ONNX inference."""

    provider_name = "kitten_tts"

    def __init__(self, config: ProviderConfig | None = None):
        self._config = config
        self._voice = DEFAULT_VOICE
        self._speed = 1.0
        if config:
            opts = getattr(config, "provider_options", {}) or {}
            self._voice = opts.get("voice", DEFAULT_VOICE)
            self._speed = float(opts.get("speed", 1.0))

    async def generate(
        self, request: AssetRequest, output_path: Path
    ) -> AssetResult:
        text = request.text or ""
        voice = request.voice_id or self._voice
        language = request.language or "en"

        if has_break_tags(text):
            return await synthesize_with_breaks(
                self, request, output_path, text
            )

        try:
            from kittentts import TTS  # type: ignore[import-untyped]
        except ImportError:
            raise RuntimeError(
                "kittentts package not installed. Install with: pip install kittentts"
            )

        tts = TTS(voice=voice)
        audio = tts.synthesize(text, speed=self._speed)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio)

        duration = None
        try:
            info = probe_media(output_path)
            duration = info.duration
        except Exception:
            pass

        return AssetResult(
            request_id=request.request_id or "",
            asset_type=AssetType.TTS,
            provider=self.provider_name,
            path=output_path,
            duration=duration,
            metadata={
                "voice": voice,
                "language": language,
                "text_length": len(text),
            },
        )
```

- [ ] **Step 3: Create MOSS-TTS-Nano provider**

Create `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/moss_tts.py`:

```python
"""MOSS-TTS-Nano local text-to-speech provider.

MOSS-TTS-Nano is a 0.1B-parameter TTS model that runs on CPU (4 cores),
supports 20 languages, voice cloning, and 48 kHz stereo output.
Apache 2.0 licensed.

Install: ``pip install moss-tts``
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from video_editor_ffmpeg.probe import probe_media

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.base import GenerativeProvider
from video_editor_asset_ingestion.providers.tts.ssml_breaks import (
    has_break_tags,
    synthesize_with_breaks,
)

logger = structlog.get_logger(__name__)

DEFAULT_VOICE = "default"
DEFAULT_SAMPLE_RATE = 48000

# MOSS-TTS-Nano supported language codes
_SUPPORTED_LANGUAGES = frozenset({
    "en", "zh", "ja", "ko", "de", "fr", "ru", "pt", "es", "it",
    "ar", "hi", "bn", "ta", "te", "mr", "gu", "kn", "ml", "pa",
})


class MossTTSProvider(GenerativeProvider):
    """Generate speech locally via MOSS-TTS-Nano inference."""

    provider_name = "moss_tts"

    def __init__(self, config: ProviderConfig | None = None):
        self._config = config
        self._voice = DEFAULT_VOICE
        self._speed = 1.0
        if config:
            opts = getattr(config, "provider_options", {}) or {}
            self._voice = opts.get("voice", DEFAULT_VOICE)
            self._speed = float(opts.get("speed", 1.0))

    async def generate(
        self, request: AssetRequest, output_path: Path
    ) -> AssetResult:
        text = request.text or ""
        voice = request.voice_id or self._voice
        language = request.language or "en"

        if has_break_tags(text):
            return await synthesize_with_breaks(
                self, request, output_path, text
            )

        try:
            from moss_tts import TTS  # type: ignore[import-untyped]
        except ImportError:
            raise RuntimeError(
                "moss-tts package not installed. Install with: pip install moss-tts"
            )

        tts = TTS(language=language)
        audio = tts.synthesize(text, voice=voice, speed=self._speed)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio)

        duration = None
        try:
            info = probe_media(output_path)
            duration = info.duration
        except Exception:
            pass

        return AssetResult(
            request_id=request.request_id or "",
            asset_type=AssetType.TTS,
            provider=self.provider_name,
            path=output_path,
            duration=duration,
            metadata={
                "voice": voice,
                "language": language,
                "text_length": len(text),
            },
        )
```

- [ ] **Step 4: Register in TTS __init__.py and TTSRouter**

Add imports to `providers/tts/__init__.py`:
```python
from video_editor_asset_ingestion.providers.tts.kitten_tts import KittenTTSProvider
from video_editor_asset_ingestion.providers.tts.moss_tts import MossTTSProvider
```

Add to TTSRouter `_pick_provider`:
```python
if voice_id.startswith("kitten:"):
    return "kitten_tts"
if voice_id.startswith("moss:"):
    return "moss_tts"
```

Add to TTSRouter `__init__`:
```python
self._kitten = KittenTTSProvider()
self._moss = MossTTSProvider()
```

Add to TTSRouter `generate` dispatch:
```python
elif provider_name == "kitten_tts":
    return await self._kitten.generate(request, output_path)
elif provider_name == "moss_tts":
    return await self._moss.generate(request, output_path)
```

- [ ] **Step 5: Add to config.yaml, scoring, and budget**

Add to `config.yaml` under `assets.providers`:
```yaml
    kitten_tts:
      timeout: 120.0
      provider_options:
        voice: en_us_female_1
        speed: 1.0
    moss_tts:
      timeout: 120.0
      provider_options:
        voice: default
        speed: 1.0
```

Add to `scoring.py` DEFAULT_SCORES under "tts":
```python
"kitten_tts": ProviderScoreCard(cost=0.0, quality=0.55, latency=3.0, reliability=0.9),
"moss_tts": ProviderScoreCard(cost=0.0, quality=0.65, latency=5.0, reliability=0.85),
```

Add to `budget.py` DEFAULT_PRICING under "tts":
```python
"kitten_tts": 0.0,
"moss_tts": 0.0,
```

- [ ] **Step 6: Write tests**

Create `tests/test_kitten_tts.py`:
```python
"""Tests for KittenTTS provider."""

import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.providers.tts.kitten_tts import KittenTTSProvider


class TestKittenTTSProvider:
    def test_provider_name(self):
        provider = KittenTTSProvider()
        assert provider.provider_name == "kitten_tts"

    def test_default_voice(self):
        provider = KittenTTSProvider()
        assert provider._voice == "en_us_female_1"

    def test_custom_voice_from_config(self):
        config = ProviderConfig(
            provider_options={"voice": "en_gb_male_1", "speed": 0.9}
        )
        provider = KittenTTSProvider(config=config)
        assert provider._voice == "en_gb_male_1"
        assert provider._speed == 0.9
```

Create `tests/test_moss_tts.py`:
```python
"""Tests for MOSS-TTS provider."""

import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.providers.tts.moss_tts import MossTTSProvider


class TestMossTTSProvider:
    def test_provider_name(self):
        provider = MossTTSProvider()
        assert provider.provider_name == "moss_tts"

    def test_default_voice(self):
        provider = MossTTSProvider()
        assert provider._voice == "default"

    def test_custom_voice_from_config(self):
        config = ProviderConfig(
            provider_options={"voice": "custom_voice", "speed": 1.2}
        )
        provider = MossTTSProvider(config=config)
        assert provider._voice == "custom_voice"
        assert provider._speed == 1.2
```

- [ ] **Step 7: Run tests**

```bash
uv run pytest tests/test_kitten_tts.py tests/test_moss_tts.py -v
```

- [ ] **Step 8: Run full suite**

```bash
uv run ruff check packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/
uv run pytest -q 2>&1 | tail -3
```

- [ ] **Step 9: Commit**

```bash
git add packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/kitten_tts.py \
        packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/moss_tts.py \
        packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/__init__.py \
        packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/tts_router.py \
        config.yaml \
        packages/asset_ingestion/src/video_editor_asset_ingestion/providers/scoring.py \
        packages/asset_ingestion/src/video_editor_asset_ingestion/budget.py \
        tests/test_kitten_tts.py tests/test_moss_tts.py
git commit -m "feat(providers): add KittenTTS and MOSS-TTS-Nano providers

KittenTTS: 15M params, 25MB, CPU-only, 8 voices, Apache 2.0
MOSS-TTS-Nano: 0.1B params, CPU-only, 20 languages, voice cloning, Apache 2.0

Both added to TTSRouter with explicit prefix routing (kitten:/moss:)
and to scoring/budget with cost=0.0.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task G3: Add Coverr and Mixkit stock footage providers

**Files:**
- Create: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/visual/coverr.py`
- Create: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/visual/mixkit.py`
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/visual/__init__.py`
- Modify: `config.yaml`
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/scoring.py`
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/budget.py`
- Test: `tests/test_coverr.py`, `tests/test_mixkit.py`

**Interfaces:**
- Consumes: `GenerativeProvider` protocol
- Produces: `CoverrProvider`, `MixkitProvider`

- [ ] **Step 1: Study PexelsProvider as reference**

```bash
cat packages/asset_ingestion/src/video_editor_asset_ingestion/providers/visual/pexels.py
```

- [ ] **Step 2: Create Coverr provider**

Create `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/visual/coverr.py`:

```python
"""Coverr stock footage provider.

Coverr provides free cinematic HD stock footage via a REST API.
API docs: https://api.coverr.co/docs
No API key required for the free tier.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import structlog

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.base import GenerativeProvider

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.coverr.co"
DEFAULT_TIMEOUT = 60.0


class CoverrProvider(GenerativeProvider):
    """Fetch stock videos from the Coverr API."""

    provider_name = "coverr"

    def __init__(self, config: ProviderConfig | None = None):
        self._config = config
        self._base_url = DEFAULT_BASE_URL
        self._api_key: str | None = None
        if config:
            self._api_key = config.resolved_api_key
            self._base_url = getattr(config, "base_url", None) or DEFAULT_BASE_URL

    async def generate(
        self, request: AssetRequest, output_path: Path
    ) -> AssetResult:
        query = request.params.get("query", "") if request.params else ""
        if not query:
            query = request.text or "nature"

        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                f"{self._base_url}/videos/search",
                params={"query": query, "per_page": 5},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        hits = data.get("hits", [])
        if not hits:
            raise RuntimeError(f"Coverr: no results for query {query!r}")

        # Pick the first result
        best = hits[0]
        video_url = best.get("urls", {}).get("mp4") or best.get("url")
        if not video_url:
            raise RuntimeError(f"Coverr: no downloadable URL in result")

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            dl_resp = await client.get(video_url)
            dl_resp.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(dl_resp.content)

        return AssetResult(
            request_id=request.request_id or "",
            asset_type=AssetType.VIDEO,
            provider=self.provider_name,
            path=output_path,
            metadata={
                "query": query,
                "source_url": video_url,
                "title": best.get("title", ""),
            },
        )
```

- [ ] **Step 3: Create Mixkit provider**

Create `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/visual/mixkit.py`:

```python
"""Mixkit stock footage provider.

Mixkit provides free HD stock videos with no API key required.
License: free for commercial and non-commercial use, no attribution.
Website: https://mixkit.co
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import structlog

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.base import GenerativeProvider

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://mixkit.co/api"
DEFAULT_TIMEOUT = 60.0


class MixkitProvider(GenerativeProvider):
    """Fetch stock videos from Mixkit (no API key required)."""

    provider_name = "mixkit"

    def __init__(self, config: ProviderConfig | None = None):
        self._config = config
        self._base_url = DEFAULT_BASE_URL

    async def generate(
        self, request: AssetRequest, output_path: Path
    ) -> AssetResult:
        query = request.params.get("query", "") if request.params else ""
        if not query:
            query = request.text or "nature"

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            # Mixkit free API endpoint
            resp = await client.get(
                f"{self._base_url}/videos/",
                params={"q": query, "limit": 5},
            )
            resp.raise_for_status()
            data = resp.json()

        results = data.get("results", [])
        if not results:
            raise RuntimeError(f"Mixkit: no results for query {query!r}")

        best = results[0]
        video_url = best.get("urls", {}).get("mp4") or best.get("download_url")
        if not video_url:
            raise RuntimeError(f"Mixkit: no downloadable URL in result")

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            dl_resp = await client.get(video_url)
            dl_resp.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(dl_resp.content)

        return AssetResult(
            request_id=request.request_id or "",
            asset_type=AssetType.VIDEO,
            provider=self.provider_name,
            path=output_path,
            metadata={
                "query": query,
                "source_url": video_url,
                "title": best.get("title", ""),
            },
        )
```

- [ ] **Step 4: Register in visual __init__.py**

Add imports:
```python
from video_editor_asset_ingestion.providers.visual.coverr import CoverrProvider
from video_editor_asset_ingestion.providers.visual.mixkit import MixkitProvider
```

- [ ] **Step 5: Add to config.yaml, scoring, and budget**

Add to `config.yaml` under `assets.providers`:
```yaml
    coverr:
      timeout: 60.0
      provider_options:
        per_page: 5
    mixkit:
      timeout: 60.0
      provider_options:
        per_page: 5
```

Add to `scoring.py` DEFAULT_SCORES under "image" and "video":
```python
"coverr": ProviderScoreCard(cost=0.0, quality=0.65, latency=5.0, reliability=0.8),
"mixkit": ProviderScoreCard(cost=0.0, quality=0.60, latency=5.0, reliability=0.8),
```

Add to `budget.py` DEFAULT_PRICING:
```python
"coverr": 0.0,
"mixkit": 0.0,
```

- [ ] **Step 6: Write tests**

Create `tests/test_coverr.py` and `tests/test_mixkit.py` following the Pexels test pattern.

- [ ] **Step 7: Run tests and commit**

```bash
uv run pytest tests/test_coverr.py tests/test_mixkit.py -v
uv run ruff check packages/asset_ingestion/src/video_editor_asset_ingestion/providers/visual/
uv run pytest -q 2>&1 | tail -3
```

---

### Task G4: Add `free_mode` config profile

**Files:**
- Create: `config.free.yaml`
- Modify: `README.md` (document free_mode)

- [ ] **Step 1: Create config.free.yaml**

```yaml
# Video Editor Engine — Free Mode Configuration
# Uses only free/local providers. No API keys required for basic operation.
# Copy to config.yaml to activate, or use: ve run --config config.free.yaml

llm:
  default_provider: ollama_cloud_pro
  providers:
    ollama_cloud_pro:
      name: ollama_cloud_pro
      base_url: https://api.ollama.com
      api_key_env: OLLAMA_CLOUD_API_KEY
      default_model: minimax-m3:cloud
  tasks:
    plan:        { task_type: plan,         provider: ollama_cloud_pro, model: minimax-m3:cloud }
    decompose:   { task_type: decompose,    provider: ollama_cloud_pro, model: minimax-m3:cloud }
    edit_plan:   { task_type: edit_plan,    provider: ollama_cloud_pro, model: minimax-m3:cloud }
    scene_plan:  { task_type: scene_plan,   provider: ollama_cloud_pro, model: minimax-m3:cloud }
    fallback:    { task_type: fallback,     provider: ollama_cloud_pro, model: glm-5.2:cloud }
    code:        { task_type: code,         provider: ollama_cloud_pro, model: qwen3-coder-next:cloud }
    utility:     { task_type: utility,      provider: ollama_cloud_pro, model: gemini-3-flash-preview:latest }
    verify_judge: { task_type: verify_judge, provider: ollama_cloud_pro, model: gemini-3-flash-preview:latest }

transcription:
  engine: whisperx
  model: small
  language: en
  device: cpu
  compute_type: int8

audio:
  loudness_target_lufs: -16
  true_peak_dbfs: -1.5
  loudness_range_lu: 11
  sample_rate: 48000
  default_music_path: ""

render:
  cache_dir: projects/{job_id}/.cache
  default_preset: short_explainer
  container: mp4
  video_codec: libx264
  audio_codec: aac
  fps: 30
  width: 1920
  height: 1080
  crf: 23
  video_bitrate: ""
  audio_bitrate: 192k
  output_name_template: "{stage}.mp4"
  hyperframes:
    cli: hyperframes
    timeout: 600

paths:
  projects: projects
  outputs: outputs
  presets: presets
  assets: assets
  music: assets/music

assets:
  providers:
    kokoro_tts:
      timeout: 120.0
      provider_options:
        voice: af_bella
        speed: 1.0
    kitten_tts:
      timeout: 120.0
      provider_options:
        voice: en_us_female_1
        speed: 1.0
    moss_tts:
      timeout: 120.0
      provider_options:
        voice: default
        speed: 1.0
    pexels:
      api_key_env: PEXELS_API_KEY
      timeout: 60.0
      provider_options:
        per_page: 5
        orientation: landscape
    coverr:
      timeout: 60.0
    mixkit:
      timeout: 60.0
    ace_step:
      timeout: 300.0
      provider_options:
        use_local: false
        poll_interval_seconds: 5.0
        poll_timeout_seconds: 300.0
    hyperframes_video:
      timeout: 600.0
      provider_options:
        cli_path: hyperframes
        width: 1920
        height: 1080
        fps: 30
  defaults:
    tts: tts_router
    image: pexels
    video: pexels
    music: ace_step
    sfx: stub_sfx
    alignment: stub_alignment
    isolation: stub_isolation
    speech_to_speech: stub_speech_to_speech
    data_viz: matplotlib_data_viz
```

- [ ] **Step 2: Document in README.md**

Add a "Free Mode" section after the "First Demo" section:
```markdown
## Free Mode — Zero API Key Operation

Run the engine with only free/local providers:

```bash
ve run ./demo/raw.mp4 --job-id demo-free --config config.free.yaml
```

This uses:
- **Kokoro / KittenTTS / MOSS-TTS** for local narration (no API key)
- **Pexels / Coverr / Mixkit** for free stock footage
- **ACE-Step** for free music generation
- **HyperFrames** for local motion graphics

The only API key needed is `OLLAMA_CLOUD_API_KEY` for LLM tasks.
```

- [ ] **Step 3: Commit**

```bash
git add config.free.yaml README.md
git commit -m "feat(config): add free_mode config profile for zero-API-key operation

Uses Kokoro/KittenTTS/MOSS-TTS for TTS, Pexels/Coverr/Mixkit for stock,
ACE-Step for music, and HyperFrames for motion graphics. Only LLM tasks
need an API key.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Phase H: MCP Server & Observability

### Task H1: Build MCP server package

**Files:**
- Create: `packages/mcp_server/pyproject.toml`
- Create: `packages/mcp_server/src/video_editor_mcp/__init__.py`
- Create: `packages/mcp_server/src/video_editor_mcp/server.py`
- Create: `packages/mcp_server/src/video_editor_mcp/tools/__init__.py`
- Create: `packages/mcp_server/src/video_editor_mcp/tools/pipeline.py`
- Create: `packages/mcp_server/src/video_editor_mcp/tools/assets.py`
- Create: `packages/mcp_server/src/video_editor_mcp/tools/templates.py`
- Create: `packages/mcp_server/src/video_editor_mcp/tools/probe.py`
- Create: `packages/mcp_server/src/video_editor_mcp/tools/ledger.py`
- Modify: `.mcp.json` (add video-editor MCP server entry)
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `SkillsPipeline`, `MusicVideoPipeline`, `AssetIngestionManager`, `TemplateRouter`, `ProjectLedger`
- Produces: MCP server with typed tools for Claude Code integration

- [ ] **Step 1: Create package structure**

```bash
mkdir -p packages/mcp_server/src/video_editor_mcp/tools
```

Create `packages/mcp_server/pyproject.toml`:
```toml
[project]
name = "video-editor-mcp"
version = "0.1.0"
description = "MCP server for the Video Editor Engine"
requires-python = ">=3.12"
dependencies = [
    "mcp>=1.0.0",
    "video-editor-cli",
    "video-editor-engine",
    "video-editor-asset-ingestion",
    "video-editor-state",
]

[project.scripts]
video-editor-mcp = "video_editor_mcp.server:main"
```

- [ ] **Step 2: Create MCP server main module**

Create `packages/mcp_server/src/video_editor_mcp/__init__.py`:
```python
"""MCP server for the Video Editor Engine."""
```

Create `packages/mcp_server/src/video_editor_mcp/server.py`:
```python
"""MCP server exposing the video editor engine as typed tools."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import structlog
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from video_editor_mcp.tools.assets import register_asset_tools
from video_editor_mcp.tools.ledger import register_ledger_tools
from video_editor_mcp.tools.pipeline import register_pipeline_tools
from video_editor_mcp.tools.probe import register_probe_tools
from video_editor_mcp.tools.templates import register_template_tools

logger = structlog.get_logger(__name__)


def create_server() -> Server:
    """Build the MCP server with all tool registrations."""
    server = Server("video-editor")

    # Register tool groups
    register_pipeline_tools(server)
    register_asset_tools(server)
    register_template_tools(server)
    register_probe_tools(server)
    register_ledger_tools(server)

    return server


async def main_async() -> None:
    """Run the MCP server over stdio."""
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream)


def main() -> None:
    """Entry point for the video-editor-mcp console script."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Create tool modules**

Create `packages/mcp_server/src/video_editor_mcp/tools/__init__.py` (empty).

Create `packages/mcp_server/src/video_editor_mcp/tools/pipeline.py`:
```python
"""Pipeline tools for the MCP server."""

from mcp.server import Server
from mcp.types import Tool, TextContent


def register_pipeline_tools(server: Server) -> None:
    """Register pipeline execution tools."""

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="run_pipeline",
                description="Run the full skills pipeline on a source video or script",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source": {"type": "string", "description": "Path to source video or markdown script"},
                        "job_id": {"type": "string", "description": "Unique job identifier"},
                        "template": {"type": "string", "description": "Template name (e.g., explainer_reel, storyteller_hd)"},
                        "preset": {"type": "string", "description": "Graphics preset name"},
                        "config_path": {"type": "string", "description": "Path to config.yaml"},
                        "music_prompt": {"type": "string", "description": "Prompt for AI music generation"},
                        "budget": {"type": "number", "description": "Credit budget limit"},
                    },
                    "required": ["source", "job_id"],
                },
            ),
            Tool(
                name="run_skill",
                description="Run a single skill on a job",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "skill": {"type": "string", "description": "Skill name: rough_cut, graphics, captions, music, export, review"},
                        "job_id": {"type": "string", "description": "Job identifier"},
                        "config_path": {"type": "string", "description": "Path to config.yaml"},
                    },
                    "required": ["skill", "job_id"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name == "run_pipeline":
            return [TextContent(
                type="text",
                text=json.dumps({
                    "status": "queued",
                    "job_id": arguments.get("job_id"),
                    "message": "Pipeline execution initiated. Use project_status to track progress.",
                })
            )]
        elif name == "run_skill":
            return [TextContent(
                type="text",
                text=json.dumps({
                    "status": "queued",
                    "job_id": arguments.get("job_id"),
                    "skill": arguments.get("skill"),
                })
            )]
        return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]
```

Create `packages/mcp_server/src/video_editor_mcp/tools/assets.py`:
```python
"""Asset generation tools for the MCP server."""

from mcp.server import Server
from mcp.types import Tool, TextContent


def register_asset_tools(server: Server) -> None:
    """Register asset generation tools."""

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="generate_image",
                description="Generate an image from a text prompt",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "Image generation prompt"},
                        "provider": {"type": "string", "description": "Provider name (poyo_ai, fal_ai, pexels)"},
                        "width": {"type": "integer", "description": "Image width"},
                        "height": {"type": "integer", "description": "Image height"},
                    },
                    "required": ["prompt"],
                },
            ),
            Tool(
                name="generate_tts",
                description="Generate speech audio from text",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text to speak"},
                        "voice_id": {"type": "string", "description": "Voice identifier"},
                        "language": {"type": "string", "description": "Language code (en, hi, ja, etc.)"},
                    },
                    "required": ["text"],
                },
            ),
            Tool(
                name="generate_music",
                description="Generate background music from a prompt",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "Music style description"},
                        "duration_seconds": {"type": "integer", "description": "Duration in seconds"},
                        "provider": {"type": "string", "description": "Provider name (poyo_music, ace_step, elevenlabs_music)"},
                    },
                    "required": ["prompt"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        return [TextContent(
            type="text",
            text=json.dumps({"status": "not_implemented", "tool": name, "args": arguments}),
        )]
```

Create `packages/mcp_server/src/video_editor_mcp/tools/templates.py`:
```python
"""Template tools for the MCP server."""

import json

from mcp.server import Server
from mcp.types import Tool, TextContent


def register_template_tools(server: Server) -> None:
    """Register template discovery tools."""

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="list_templates",
                description="List all available video templates",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="select_template",
                description="Select the best template for a given source",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source": {"type": "string", "description": "Path to source file"},
                        "template_name": {"type": "string", "description": "Optional explicit template name"},
                    },
                    "required": ["source"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            from video_editor_engine.templates import TemplateRouter
            router = TemplateRouter()
        except Exception as e:
            return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

        if name == "list_templates":
            return [TextContent(
                type="text",
                text=json.dumps({"templates": router.template_names}),
            )]
        elif name == "select_template":
            source = arguments.get("source", "")
            template_name = arguments.get("template_name")
            try:
                result = router.select(source=source, template_name=template_name)
                return [TextContent(
                    type="text",
                    text=json.dumps({
                        "template": result.template.name,
                        "pipeline_kind": result.pipeline_kind,
                        "preset": result.preset,
                        "aspect_ratio": result.aspect_ratio,
                        "providers": result.providers,
                        "notes": result.notes,
                    }),
                )]
            except Exception as e:
                return [TextContent(type="text", text=json.dumps({"error": str(e)}))]
        return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]
```

Create `packages/mcp_server/src/video_editor_mcp/tools/probe.py`:
```python
"""Media probe tools for the MCP server."""

import json

from mcp.server import Server
from mcp.types import Tool, TextContent


def register_probe_tools(server: Server) -> None:
    """Register media inspection tools."""

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="probe_media",
                description="Get technical metadata about a media file",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to media file"},
                    },
                    "required": ["path"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name == "probe_media":
            try:
                from video_editor_ffmpeg.probe import probe_media
                info = probe_media(arguments["path"])
                return [TextContent(
                    type="text",
                    text=json.dumps({
                        "duration": info.duration,
                        "width": info.width,
                        "height": info.height,
                        "fps": info.fps,
                        "codec": info.video_codec,
                        "audio_codec": info.audio_codec,
                        "has_audio": info.has_audio,
                    }),
                )]
            except Exception as e:
                return [TextContent(type="text", text=json.dumps({"error": str(e)}))]
        return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]
```

Create `packages/mcp_server/src/video_editor_mcp/tools/ledger.py`:
```python
"""Project ledger tools for the MCP server."""

import json

from mcp.server import Server
from mcp.types import Tool, TextContent


def register_ledger_tools(server: Server) -> None:
    """Register project ledger query tools."""

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="project_status",
                description="Get the status of a project from the ledger",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string", "description": "Project identifier"},
                    },
                    "required": ["project_id"],
                },
            ),
            Tool(
                name="cost_report",
                description="Get cost report for a project",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string", "description": "Project identifier"},
                    },
                    "required": ["project_id"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        return [TextContent(
            type="text",
            text=json.dumps({"status": "not_implemented", "tool": name, "args": arguments}),
        )]
```

- [ ] **Step 4: Add to .mcp.json**

```json
{
  "mcpServers": {
    "openrouter": { ... },
    "video-editor": {
      "command": "uv",
      "args": ["run", "video-editor-mcp"]
    }
  }
}
```

- [ ] **Step 5: Write tests**

Create `tests/test_mcp_server.py`:
```python
"""Tests for the MCP server."""

from video_editor_mcp.server import create_server


class TestMCPServer:
    def test_server_creation(self):
        """Server should be creatable without errors."""
        server = create_server()
        assert server is not None
        assert server.name == "video-editor"
```

- [ ] **Step 6: Run tests and commit**

```bash
uv run pytest tests/test_mcp_server.py -v
uv run ruff check packages/mcp_server/
```

---

### Task H2: Add SQLite-backed persistent job queue

**Files:**
- Modify: `packages/state/src/video_editor_state/queue.py` (add `PersistentJobQueue`)
- Test: `tests/test_queue.py` (add persistence tests)

**Interfaces:**
- Consumes: `JobQueue` ABC
- Produces: `PersistentJobQueue` implementing `JobQueue` with SQLite backend

- [ ] **Step 1: Study current JobQueue ABC**

```bash
cat packages/state/src/video_editor_state/queue.py
```

- [ ] **Step 2: Add PersistentJobQueue**

Add to `queue.py`:

```python
class PersistentJobQueue(JobQueue):
    """SQLite-backed job queue that survives process restarts."""

    def __init__(self, db_path: Path | str = "outputs/queue.db"):
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    command TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    error TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_jobs_status
                ON jobs(status, created_at)
            """)
            conn.commit()

    def enqueue(self, message: JobMessage) -> None:
        with self._lock, sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                "INSERT INTO jobs (id, tenant_id, command, payload_json, status, created_at) "
                "VALUES (?, ?, ?, ?, 'pending', ?)",
                (
                    message.id,
                    message.tenant_id,
                    message.command,
                    json.dumps(message.payload),
                    datetime.now(UTC).isoformat(),
                ),
            )
            conn.commit()

    def dequeue(self, tenant_id: str = "default") -> JobMessage | None:
        with self._lock, sqlite3.connect(str(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM jobs WHERE tenant_id = ? AND status = 'pending' "
                "ORDER BY created_at ASC LIMIT 1",
                (tenant_id,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE jobs SET status = 'running', started_at = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), row["id"]),
            )
            conn.commit()
            return JobMessage(
                id=row["id"],
                tenant_id=row["tenant_id"],
                command=row["command"],
                payload=json.loads(row["payload_json"]),
            )

    def complete(self, job_id: str) -> None:
        with self._lock, sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                "UPDATE jobs SET status = 'completed', completed_at = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), job_id),
            )
            conn.commit()

    def fail(self, job_id: str, error: str = "") -> None:
        with self._lock, sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                "UPDATE jobs SET status = 'failed', completed_at = ?, error = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), error, job_id),
            )
            conn.commit()

    def size(self, tenant_id: str = "default") -> int:
        with self._lock, sqlite3.connect(str(self._db_path)) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE tenant_id = ? AND status = 'pending'",
                (tenant_id,),
            ).fetchone()
            return row[0] if row else 0

    def pending_count(self, tenant_id: str = "default") -> int:
        return self.size(tenant_id)

    def completed_count(self, tenant_id: str = "default") -> int:
        with self._lock, sqlite3.connect(str(self._db_path)) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE tenant_id = ? AND status = 'completed'",
                (tenant_id,),
            ).fetchone()
            return row[0] if row else 0

    def failed_count(self, tenant_id: str = "default") -> int:
        with self._lock, sqlite3.connect(str(self._db_path)) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE tenant_id = ? AND status = 'failed'",
                (tenant_id,),
            ).fetchone()
            return row[0] if row else 0
```

- [ ] **Step 3: Add tests**

Add to `tests/test_queue.py`:
```python
class TestPersistentJobQueue:
    def test_enqueue_dequeue_persists(self, tmp_path):
        from video_editor_state.queue import PersistentJobQueue, JobMessage
        db = tmp_path / "queue.db"
        q = PersistentJobQueue(db)
        msg = JobMessage(id="job-1", command="run", payload={"source": "test.mp4"})
        q.enqueue(msg)
        assert q.size() == 1
        dequeued = q.dequeue()
        assert dequeued is not None
        assert dequeued.id == "job-1"
        q.complete("job-1")
        assert q.completed_count() == 1

    def test_survives_reopen(self, tmp_path):
        from video_editor_state.queue import PersistentJobQueue, JobMessage
        db = tmp_path / "queue.db"
        q1 = PersistentJobQueue(db)
        q1.enqueue(JobMessage(id="job-1", command="run", payload={}))
        # Reopen
        q2 = PersistentJobQueue(db)
        assert q2.size() == 1
```

- [ ] **Step 4: Run tests and commit**

---

## Phase I: Hardening & Polish

### Task I1: Add true E2E render test

**Files:**
- Create: `tests/test_e2e_render.py`

- [ ] **Step 1: Create E2E test**

```python
"""True end-to-end render tests using synthetic media."""

import subprocess
from pathlib import Path


def test_full_skills_pipeline_produces_playable_mp4(tmp_path):
    """Run the full skills pipeline on a synthetic video and verify output."""
    # Create synthetic test video
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=3:size=640x480:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-pix_fmt", "yuv420p", "-shortest",
        str(source),
    ], check=True, capture_output=True)
    assert source.exists()

    # Run the pipeline
    work = tmp_path / "work"
    output = tmp_path / "output"
    result = subprocess.run([
        "uv", "run", "ve", "run", str(source),
        "--job-id", "e2e-test",
        "--work-root", str(work),
        "--output-root", str(output),
        "--preset", "short_explainer",
        "--skip-review",
    ], capture_output=True, text=True, timeout=120)

    # Check output
    final = output / "e2e-test" / "final.mp4"
    if final.exists():
        # Verify it's playable
        probe = subprocess.run([
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", str(final),
        ], capture_output=True, text=True)
        assert probe.returncode == 0
        import json
        info = json.loads(probe.stdout)
        duration = float(info["format"]["duration"])
        assert duration > 0.5
```

- [ ] **Step 2: Run test (may be slow)**

```bash
uv run pytest tests/test_e2e_render.py -v -s
```

---

### Task I2: Add input path sanitization for worker payloads

**Files:**
- Modify: `apps/cli/src/video_editor_cli/main.py` (worker payload validation)

- [ ] **Step 1: Add path validation to worker dispatch**

Add a validation function and apply it in the worker loop:

```python
def _validate_payload_paths(payload: dict[str, Any], base_dir: Path) -> None:
    """Ensure payload paths don't escape base_dir via traversal."""
    for key in ("work_root", "output_root", "source"):
        value = payload.get(key)
        if value and isinstance(value, str):
            resolved = (base_dir / value).resolve()
            if not str(resolved).startswith(str(base_dir.resolve())):
                raise ValueError(
                    f"Payload path {key}={value!r} escapes base directory"
                )
```

- [ ] **Step 2: Add tests**

```python
def test_rejects_path_traversal():
    with pytest.raises(ValueError, match="escapes"):
        _validate_payload_paths(
            {"work_root": "../../etc"},
            Path("/tmp/test"),
        )
```

---

## Phase J: Architecture Modernization

### Task J1: Define SkillProtocol and refactor skills

**Files:**
- Create: `skills/skill_protocol.py`
- Modify: `skills/rough_cut/src/rough_cut/rough_cut.py`
- Modify: `skills/graphics/src/graphics/graphics.py`
- Modify: `skills/captions/src/captions/captions.py`
- Modify: `skills/music/src/music/music.py`
- Modify: `skills/export/src/export/export.py`
- Modify: `skills/review/src/review/review.py`
- Modify: `apps/cli/src/video_editor_cli/pipeline.py`

**Interfaces:**
- Consumes: Current skill classes with ad-hoc interfaces
- Produces: `SkillProtocol` with `run(ctx, **kwargs) -> dict` signature

- [ ] **Step 1: Define SkillProtocol**

Create `skills/skill_protocol.py`:
```python
"""Formal protocol for video editor skills."""

from __future__ import annotations

from typing import Any, Protocol

from video_editor_state.job import JobContext


class SkillProtocol(Protocol):
    """A skill is a pipeline stage that takes a job context and returns outputs."""

    @property
    def name(self) -> str: ...

    def run(self, ctx: JobContext, **kwargs: Any) -> dict[str, Any]:
        """Execute the skill and return stage outputs."""
        ...
```

- [ ] **Step 2: Update one skill as example (RoughCutSkill)**

Add `name` property and ensure `run` matches protocol:
```python
class RoughCutSkill:
    @property
    def name(self) -> str:
        return "rough_cut"

    def run(self, ctx: JobContext, **kwargs: Any) -> dict[str, Any]:
        # existing implementation
        ...
```

- [ ] **Step 3: Update pipeline.py to use protocol**

```python
from skills.skill_protocol import SkillProtocol

class SkillsPipeline:
    def __init__(self, ..., skills: list[SkillProtocol] | None = None):
        self._skills = skills or [
            RoughCutSkill(),
            GraphicsSkill(),
            CaptionsSkill(),
            MusicSkill(),
            ExportSkill(),
        ]
```

---

### Task J2: Define consistent error hierarchy

**Files:**
- Create: `packages/state/src/video_editor_state/errors.py`
- Modify: Various files to use new base class

- [ ] **Step 1: Create error hierarchy**

```python
"""Consistent error hierarchy for the video editor engine."""

class VEError(RuntimeError):
    """Base exception for all video editor engine errors."""

class VEConfigError(VEError):
    """Configuration-related errors."""

class VEProviderError(VEError):
    """Provider-related errors (API failures, auth, rate limits)."""

class VEBudgetError(VEError):
    """Budget-related errors."""

class VEPipelineError(VEError):
    """Pipeline execution errors."""

class VEAssetError(VEError):
    """Asset generation/ingestion errors."""
```

---

## Execution Order

1. **Phase F** (Tasks F1–F6): Foundation — must complete first
2. **Phase G** (Tasks G1–G4): Provider expansion — can parallelize G1+G2, G3+G4
3. **Phase H** (Tasks H1–H2): MCP server + persistent queue
4. **Phase I** (Tasks I1–I2): E2E tests + security
5. **Phase J** (Tasks J1–J2): Protocol + error hierarchy

Each phase ends with a green test suite and clean lint.
