# agy Image Provider with Ordered Fallback — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `agy` (Gemini CLI) as the primary image provider, with an ordered provider chain (`agy_image → poyo_ai → fal_ai → openrouter`) so a failed generation falls through instead of failing the job.

**Architecture:** `AGYImageProvider` implements the existing `GenerativeProvider` interface by shelling out to `agy --sandbox -p …`, then verifying the written file itself. The fallback order is policy, so it lives in `AssetProviderConfig.image_chain` and is executed by `AssetIngestionManager`, which keeps its existing per-provider semaphore, circuit breaker, and budget accounting. Cost is 0.0 (quota, not credits), bounded by `max_images_per_run`.

**Tech Stack:** Python 3.11, pydantic v2, asyncio subprocesses, pytest + pytest-asyncio, FFmpeg/ffprobe via `video_editor_ffmpeg`.

**Spec:** `docs/archive/specs/2026-10-23-agy-image-provider-design.md`

## Global Constraints

- `agy` is invoked with `--sandbox` on every call. Provider options cannot disable it (`sandbox: false` is ignored with a warning).
- The prompt is always passed as an argv element, never through a shell.
- `AGYImageError` is **not** retryable by `manager._is_retryable`; a timeout is re-wrapped so it is not mistaken for `TimeoutError`.
- `agy_image` writes PNG only; a non-PNG `output_ext` fails before agy is invoked.
- Chain failures must not poison the asset cache.
- No test may invoke the real `agy`; the real binary appears only under `@pytest.mark.integration`.
- Provider name string is exactly `"agy_image"`.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/visual/agy_image.py` | create | agy invocation, verification, `AssetResult` |
| `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/registry.py` | modify | register `agy_image` |
| `packages/asset_ingestion/src/video_editor_asset_ingestion/manager.py` | modify | chain walk, per-run cap, pass `provider_name`/`stub_fallback` to `_generate_one` |
| `packages/asset_ingestion/src/video_editor_asset_ingestion/config.py` | modify | `image_chain`, `max_images_per_run` |
| `packages/asset_ingestion/src/video_editor_asset_ingestion/budget.py` | modify | document `agy_image: 0.0` |
| `config.yaml`, `config.example.yaml` | modify | provider block + chain |
| `tests/test_agy_image.py` | create | provider tests (fake binary) |
| `tests/test_provider_config.py` | modify | config validation tests |
| `tests/test_asset_ingestion.py` | modify | chain + cap tests |
| `tests/test_provider_registry.py` | modify | registration test |
| `docs/CLI_USAGE.md`, `docs/POYO_ROUTING.md` | modify | usage + routing docs |

---

### Task 1: `AGYImageProvider` success path

**Files:**
- Create: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/visual/agy_image.py`
- Test: `tests/test_agy_image.py`

**Interfaces:**
- Produces: `AGYImageProvider(ProviderConfig | None = None)` with `provider_name = "agy_image"` and `async generate(request: AssetRequest, output_path: Path) -> AssetResult`.
- Produces: `AGYImageError(VEProviderError)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_agy_image.py`:

```python
"""AGYImageProvider tests. Every test uses a fake `agy` script; no quota is used."""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers.visual.agy_image import AGYImageProvider

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

FAKE_SOURCE = '''\
import pathlib, re, sys, time
here = pathlib.Path(__file__).parent
prompt = sys.argv[sys.argv.index("-p") + 1]
(here / "argv.txt").write_text(" ".join(sys.argv[1:]))
mode = (here / "mode.txt").read_text().strip()
match = re.search(r"Save the image to (.+?) and reply", prompt)
if mode == "sleep":
    time.sleep(10)
if mode == "exit1":
    sys.exit(3)
if mode != "nofile" and match:
    target = pathlib.Path(match.group(1))
    if mode == "garbage":
        target.write_bytes(b"<html>nope</html>")
    else:
        target.write_bytes((here / "fixture.png").read_bytes())
'''


def make_fake_agy(tmp_path: Path, mode: str = "ok") -> Path:
    """Write an executable stand-in for `agy` and return its path."""
    (tmp_path / "fixture.png").write_bytes(PNG_1X1)
    (tmp_path / "mode.txt").write_text(mode)
    script = tmp_path / "agy"
    script.write_text(f"#!{sys.executable}\n{FAKE_SOURCE}")
    script.chmod(0o755)
    return script


def make_request(**overrides: object) -> AssetRequest:
    payload = {
        "asset_id": "img-1",
        "job_id": "job-1",
        "scene_id": "scene-1",
        "asset_type": AssetType.IMAGE,
        "prompt": "a red circle on white",
        "output_ext": "png",
    }
    payload.update(overrides)
    return AssetRequest(**payload)


def make_provider(tmp_path: Path, mode: str = "ok", **options: object) -> AGYImageProvider:
    fake = make_fake_agy(tmp_path, mode)
    provider_options = {"agy_bin": str(fake)}
    provider_options.update(options)
    return AGYImageProvider(ProviderConfig(provider_options=provider_options))


@pytest.mark.asyncio
async def test_generate_writes_image(tmp_path: Path) -> None:
    provider = make_provider(tmp_path)
    output = tmp_path / "out.png"

    result = await provider.generate(make_request(), output)

    assert output.exists()
    assert output.stat().st_size > 0
    assert result.asset_type == AssetType.IMAGE
    assert result.metadata["provider"] == "agy_image"


@pytest.mark.asyncio
async def test_generate_uses_sandbox_and_aspect_line(tmp_path: Path) -> None:
    provider = make_provider(tmp_path)
    request = make_request(params={"width": 1080, "height": 1920})

    await provider.generate(request, tmp_path / "out.png")

    argv = (tmp_path / "argv.txt").read_text()
    assert "--sandbox" in argv
    assert "Aspect: 9:16 (1080x1920)" in argv
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agy_image.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'video_editor_asset_ingestion.providers.visual.agy_image'`

- [ ] **Step 3: Write minimal implementation**

Create `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/visual/agy_image.py`:

```python
"""Image generation through the local ``agy`` CLI (Gemini CLI agent).

``agy`` runs headless with ``--sandbox``: it may write the image but cannot
execute shell commands, so model-written script text cannot escalate into a
command. Verification is ours, not the model's: only the exact temp path we
asked for is accepted.
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from pathlib import Path

import structlog
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.base import GenerativeProvider
from video_editor_state.errors import VEProviderError

logger = structlog.get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 300.0


class AGYImageError(VEProviderError):
    """A generation attempt that should fall through to the next provider."""


def _aspect_line(request: AssetRequest) -> str:
    """Return a one-line aspect directive derived from width/height, or ""."""
    width = request.params.get("width")
    height = request.params.get("height")
    if (
        isinstance(width, int)
        and not isinstance(width, bool)
        and width > 0
        and isinstance(height, int)
        and not isinstance(height, bool)
        and height > 0
    ):
        divisor = math.gcd(width, height)
        return f"\n\nAspect: {width // divisor}:{height // divisor} ({width}x{height})."
    return ""


class AGYImageProvider(GenerativeProvider):
    """Generate one image per request through the ``agy`` CLI."""

    provider_name = "agy_image"

    def __init__(self, config: ProviderConfig | None = None) -> None:
        cfg = config or ProviderConfig()
        options = cfg.provider_options or {}
        self.timeout = cfg.timeout or DEFAULT_TIMEOUT_SECONDS
        self.model = str(options.get("model") or "")
        self.agy_bin = str(options.get("agy_bin") or "agy")

    def _argv(self, request: AssetRequest, tmp: Path) -> list[str]:
        prompt = (
            f"{request.prompt}{_aspect_line(request)}"
            f"\n\nSave the image to {tmp} and reply with the path."
        )
        argv = [self.agy_bin, "--sandbox"]
        if self.model:
            argv += ["--model", self.model]
        argv += ["-p", prompt, "--output-format", "text"]
        return argv

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        started = time.monotonic()
        tmp = output_path.with_name(f".{output_path.stem}.agy.png")
        tmp.unlink(missing_ok=True)

        proc = await asyncio.create_subprocess_exec(
            *self._argv(request, tmp),
            cwd=str(output_path.parent),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        await proc.communicate()

        os.replace(tmp, output_path)
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.IMAGE,
            local_path=str(output_path),
            scene_id=request.scene_id,
            metadata={
                "provider": self.provider_name,
                "model": self.model or "agy-default",
                "latency_ms": int((time.monotonic() - started) * 1000),
                "cost": 0.0,
            },
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_agy_image.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add packages/asset_ingestion/src/video_editor_asset_ingestion/providers/visual/agy_image.py tests/test_agy_image.py
git commit -m "feat(assets): add agy_image provider skeleton"
```

---

### Task 2: Verification, timeouts, and typed failures

**Files:**
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/visual/agy_image.py`
- Test: `tests/test_agy_image.py`

**Interfaces:**
- Consumes: `_validate_image_bytes(content: bytes) -> None` from `providers/visual/fal_ai.py`; `probe_media(path) -> MediaInfo` with `.width`/`.height` from `video_editor_ffmpeg.probe`.
- Produces: `generate()` raises `AGYImageError` for missing file, invalid bytes, non-zero exit, timeout, non-PNG extension, or missing binary.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agy_image.py`:

```python
@pytest.mark.asyncio
async def test_missing_file_raises(tmp_path: Path) -> None:
    provider = make_provider(tmp_path, mode="nofile")

    with pytest.raises(AGYImageError, match="wrote no image"):
        await provider.generate(make_request(), tmp_path / "out.png")


@pytest.mark.asyncio
async def test_invalid_bytes_raise(tmp_path: Path) -> None:
    provider = make_provider(tmp_path, mode="garbage")

    with pytest.raises(AGYImageError, match="not a valid image"):
        await provider.generate(make_request(), tmp_path / "out.png")


@pytest.mark.asyncio
async def test_nonzero_exit_raises(tmp_path: Path) -> None:
    provider = make_provider(tmp_path, mode="exit1")

    with pytest.raises(AGYImageError, match="exited 3"):
        await provider.generate(make_request(), tmp_path / "out.png")


@pytest.mark.asyncio
async def test_timeout_kills_process(tmp_path: Path) -> None:
    provider = make_provider(tmp_path, mode="sleep")
    provider.timeout = 0.5

    with pytest.raises(AGYImageError, match="timed out"):
        await provider.generate(make_request(), tmp_path / "out.png")


@pytest.mark.asyncio
async def test_non_png_extension_fails_before_running(tmp_path: Path) -> None:
    provider = make_provider(tmp_path)

    with pytest.raises(AGYImageError, match="PNG only"):
        await provider.generate(make_request(output_ext="jpg"), tmp_path / "out.jpg")


@pytest.mark.asyncio
async def test_missing_binary_raises(tmp_path: Path) -> None:
    provider = AGYImageProvider(
        ProviderConfig(provider_options={"agy_bin": str(tmp_path / "does-not-exist")})
    )

    with pytest.raises(AGYImageError, match="not found"):
        await provider.generate(make_request(), tmp_path / "out.png")


def test_timeout_is_not_retryable() -> None:
    from video_editor_asset_ingestion.manager import _is_retryable

    assert _is_retryable(AGYImageError("boom")) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agy_image.py -v`
Expected: FAIL — the new tests raise `FileNotFoundError`/`IsADirectoryError` or pass unexpectedly; `test_missing_file_raises` fails because `os.replace` raises `FileNotFoundError`.

- [ ] **Step 3: Implement verification and process control**

Replace the body of `generate` and add helpers in `agy_image.py`:

```python
    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        if request.asset_type is not AssetType.IMAGE:
            raise AGYImageError(f"agy_image handles images only, got {request.asset_type}")
        ext = request.output_ext.lstrip(".").lower()
        if ext and ext != "png":
            raise AGYImageError(f"agy_image writes PNG only; request asked for {ext!r}")

        binary = shutil.which(self.agy_bin)
        if binary is None:
            raise AGYImageError(f"agy binary not found: {self.agy_bin!r}")

        started = time.monotonic()
        tmp = output_path.with_name(f".{output_path.stem}.agy.png")
        tmp.unlink(missing_ok=True)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        await self._run(self._argv(request, tmp, binary), cwd=output_path.parent)
        width, height = self._verify(tmp)
        os.replace(tmp, output_path)
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.IMAGE,
            local_path=str(output_path),
            scene_id=request.scene_id,
            metadata={
                "provider": self.provider_name,
                "model": self.model or "agy-default",
                "width": width,
                "height": height,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "cost": 0.0,
            },
        )

    async def _run(self, argv: list[str], *, cwd: Path) -> None:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except TimeoutError:
            self._kill(proc)
            raise AGYImageError(f"agy timed out after {self.timeout:.0f}s") from None
        if proc.returncode != 0:
            tail = (stderr or b"")[-500:].decode("utf-8", "replace")
            raise AGYImageError(f"agy exited {proc.returncode}: {tail}")

    @staticmethod
    def _kill(proc: asyncio.subprocess.Process) -> None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()

    def _verify(self, path: Path) -> tuple[int, int]:
        if not path.exists() or path.stat().st_size == 0:
            raise AGYImageError("agy reported success but wrote no image")
        try:
            _validate_image_bytes(path.read_bytes())
        except MediaContentError as exc:
            raise AGYImageError(f"agy wrote bytes that are not a valid image: {exc}") from exc
        try:
            info = probe_media(path)
        except MediaInfoError as exc:
            raise AGYImageError(f"agy wrote an unreadable image: {exc}") from exc
        if info.width <= 0 or info.height <= 0:
            raise AGYImageError("agy wrote an image with no readable dimensions")
        return info.width, info.height
```

Add to the imports:

```python
import shutil
import signal

from video_editor_asset_ingestion.providers._media import MediaContentError
from video_editor_asset_ingestion.providers.visual.fal_ai import _validate_image_bytes
from video_editor_ffmpeg.probe import MediaInfoError, probe_media
```

Change `_argv` to accept the resolved binary:

```python
    def _argv(self, request: AssetRequest, tmp: Path, binary: str) -> list[str]:
        prompt = (
            f"{request.prompt}{_aspect_line(request)}"
            f"\n\nSave the image to {tmp} and reply with the path."
        )
        argv = [binary, "--sandbox"]
        if self.model:
            argv += ["--model", self.model]
        argv += ["-p", prompt, "--output-format", "text"]
        return argv
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agy_image.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add packages/asset_ingestion/src/video_editor_asset_ingestion/providers/visual/agy_image.py tests/test_agy_image.py
git commit -m "feat(assets): verify agy output and fail fast on bad generations"
```

---

### Task 3: Registry and manager wiring

**Files:**
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/registry.py`
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/manager.py` (the `ProviderConfig`-injected provider set in `_resolve_provider`)
- Test: `tests/test_provider_registry.py`, `tests/test_asset_ingestion.py`

**Interfaces:**
- Consumes: `AGYImageProvider`, `AGYImageError` from Task 1/2.
- Produces: `get_provider("agy_image")` returns `AGYImageProvider`; the manager constructs it with its `ProviderConfig`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_provider_registry.py`:

```python
def test_agy_image_is_registered() -> None:
    from video_editor_asset_ingestion.providers.registry import get_provider
    from video_editor_asset_ingestion.providers.visual.agy_image import AGYImageProvider

    assert get_provider("agy_image") is AGYImageProvider
```

Append to `tests/test_asset_ingestion.py` (inside `TestManagerPhase4`):

```python
    @pytest.mark.asyncio
    async def test_manager_constructs_agy_image_with_provider_config(
        self, tmp_path: Path
    ) -> None:
        from video_editor_asset_ingestion.config import AssetProviderConfig, ProviderConfig
        from video_editor_asset_ingestion.models import AssetRequest

        ctx = JobContext(
            job_id="agy-job",
            work_root=tmp_path / "work",
            output_root=tmp_path / "output",
        )
        config = AssetProviderConfig(
            providers={"agy_image": ProviderConfig(provider_options={"agy_bin": "/bin/true"})}
        )
        manager = AssetIngestionManager(ctx, config=config)
        request = AssetRequest(
            asset_id="img-1",
            job_id="agy-job",
            scene_id="s1",
            asset_type=AssetType.IMAGE,
            prompt="x",
            provider="agy_image",
            output_ext="png",
        )

        provider = manager._resolve_provider(request)

        assert provider.provider_name == "agy_image"
        assert provider.agy_bin == "/bin/true"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_provider_registry.py::test_agy_image_is_registered tests/test_asset_ingestion.py::TestManagerPhase4::test_manager_constructs_agy_image_with_provider_config -v`
Expected: FAIL — `ProviderNotFoundError` / `RuntimeError: Unknown provider 'agy_image'`

- [ ] **Step 3: Register and inject the config**

In `providers/registry.py`, add the import and registry entry:

```python
from video_editor_asset_ingestion.providers.visual.agy_image import AGYImageProvider
```

```python
    AGYImageProvider.provider_name: AGYImageProvider,
```

In `manager._resolve_provider`, add `"agy_image"` to the set of names that receive a `ProviderConfig`:

```python
            "poyo_ai",
            "agy_image",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_provider_registry.py tests/test_asset_ingestion.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/asset_ingestion/src/video_editor_asset_ingestion/providers/registry.py packages/asset_ingestion/src/video_editor_asset_ingestion/manager.py tests/test_provider_registry.py tests/test_asset_ingestion.py
git commit -m "feat(assets): register agy_image provider"
```

---

### Task 4: `image_chain` and `max_images_per_run` config

**Files:**
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/config.py:93-98`
- Test: `tests/test_provider_config.py`

**Interfaces:**
- Produces: `AssetProviderConfig.image_chain: list[str]` (default `[]`) and `AssetProviderConfig.max_images_per_run: int` (default `25`). Validation rejects unknown provider names.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_provider_config.py`:

```python
def test_image_chain_defaults_to_empty() -> None:
    from video_editor_asset_ingestion.config import AssetProviderConfig

    config = AssetProviderConfig()

    assert config.image_chain == []
    assert config.max_images_per_run == 25


def test_image_chain_rejects_unknown_provider() -> None:
    import pytest

    from video_editor_asset_ingestion.config import AssetProviderConfig

    with pytest.raises(ValueError, match="unknown provider"):
        AssetProviderConfig(image_chain=["agy_image", "not_a_provider"])


def test_image_chain_accepts_registered_providers() -> None:
    from video_editor_asset_ingestion.config import AssetProviderConfig

    config = AssetProviderConfig(image_chain=["agy_image", "poyo_ai", "fal_ai"])

    assert config.image_chain == ["agy_image", "poyo_ai", "fal_ai"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_provider_config.py -v -k image_chain`
Expected: FAIL — `ValidationError: Extra inputs are not permitted` / attribute errors. (Note: a model with no `model_config` ignores extras; if the tests pass silently, they still fail on missing fields.)

- [ ] **Step 3: Add the fields and validator**

In `config.py`, inside `AssetProviderConfig`:

```python
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    defaults: dict[str, str] = Field(default_factory=dict)
    #: Ordered image providers. Empty means "use ``defaults``/scored selection".
    image_chain: list[str] = Field(default_factory=list)
    #: Hard cap on image generations per manager (one manager per job).
    max_images_per_run: int = 25

    @field_validator("image_chain")
    @classmethod
    def _validate_chain(cls, value: list[str]) -> list[str]:
        """Reject typos at load time instead of mid-job."""
        from video_editor_asset_ingestion.providers.registry import PROVIDER_REGISTRY

        unknown = [name for name in value if name not in PROVIDER_REGISTRY]
        if unknown:
            raise ValueError(f"image_chain names unknown provider(s): {unknown}")
        return value
```

Add `field_validator` to the pydantic import at the top of `config.py`:

```python
from pydantic import BaseModel, Field, field_validator, model_validator
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_provider_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/asset_ingestion/src/video_editor_asset_ingestion/config.py tests/test_provider_config.py
git commit -m "feat(assets): add image_chain and max_images_per_run config"
```

---

### Task 5: Chain walk in the manager

**Files:**
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/manager.py` (`_resolve_one`, `_generate_one`, `_resolve_provider`, new helpers)
- Test: `tests/test_asset_ingestion.py`

**Interfaces:**
- Consumes: `AssetProviderConfig.image_chain` (Task 4).
- Produces: `_provider_chain(request) -> list[str]`, `_attempt_provider(request, provider_name, media_path, meta_path, *, stub_fallback) -> AssetResult`, `_resolve_provider(request, provider_name: str | None = None)`, `_generate_one(..., *, stub_fallback: bool = True)`.
- Produces metadata keys `_fallback_from` and `chain_attempted`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_asset_ingestion.py` (new class at end of file):

```python
class TestImageChain:
    """Fallback chain: agy_image -> poyo_ai."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> AssetIngestionManager:
        from video_editor_asset_ingestion.config import AssetProviderConfig

        ctx = JobContext(
            job_id="chain-job",
            work_root=tmp_path / "work",
            output_root=tmp_path / "output",
        )
        config = AssetProviderConfig(image_chain=["agy_image", "poyo_ai"])
        return AssetIngestionManager(ctx, config=config)

    @staticmethod
    def _install(monkeypatch: pytest.MonkeyPatch, manager: AssetIngestionManager, providers: dict) -> None:
        def fake_resolve(request: AssetRequest, provider_name: str | None = None):
            name = provider_name or request.provider
            return providers[name]

        monkeypatch.setattr(manager, "_resolve_provider", fake_resolve)

    @pytest.mark.asyncio
    async def test_first_provider_success(
        self, tmp_path: Path, manager: AssetIngestionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from video_editor_asset_ingestion.providers.visual.agy_image import AGYImageError

        class Ok:
            provider_name = "agy_image"

            async def generate(self, request: AssetRequest, output_path: Path):
                output_path.write_bytes(PNG_1X1)
                return AssetResult(
                    asset_id=request.asset_id,
                    asset_type=AssetType.IMAGE,
                    local_path=str(output_path),
                    scene_id=request.scene_id,
                    metadata={"provider": "agy_image"},
                )

        class Never:
            provider_name = "poyo_ai"

            async def generate(self, request: AssetRequest, output_path: Path):
                raise AGYImageError("should not run")

        self._install(monkeypatch, manager, {"agy_image": Ok(), "poyo_ai": Never()})
        request = AssetRequest(
            asset_id="img-1", job_id="chain-job", scene_id="s1",
            asset_type=AssetType.IMAGE, prompt="x", output_ext="png",
        )

        result = await manager.resolve([request])

        assert result[0].metadata["provider"] == "agy_image"
        assert "_fallback_from" not in result[0].metadata

    @pytest.mark.asyncio
    async def test_first_failure_falls_through(
        self, tmp_path: Path, manager: AssetIngestionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from video_editor_asset_ingestion.providers.visual.agy_image import AGYImageError

        class Broken:
            provider_name = "agy_image"

            async def generate(self, request: AssetRequest, output_path: Path):
                raise AGYImageError("quota exhausted")

        class Ok:
            provider_name = "poyo_ai"

            async def generate(self, request: AssetRequest, output_path: Path):
                output_path.write_bytes(PNG_1X1)
                return AssetResult(
                    asset_id=request.asset_id,
                    asset_type=AssetType.IMAGE,
                    local_path=str(output_path),
                    scene_id=request.scene_id,
                    metadata={"provider": "poyo_ai"},
                )

        self._install(monkeypatch, manager, {"agy_image": Broken(), "poyo_ai": Ok()})
        request = AssetRequest(
            asset_id="img-2", job_id="chain-job", scene_id="s1",
            asset_type=AssetType.IMAGE, prompt="x", output_ext="png",
        )

        result = await manager.resolve([request])

        assert result[0].metadata["provider"] == "poyo_ai"
        assert result[0].metadata["_fallback_from"] == "agy_image"
        assert result[0].metadata["chain_attempted"] == ["agy_image", "poyo_ai"]

    @pytest.mark.asyncio
    async def test_open_breaker_is_skipped(
        self, tmp_path: Path, manager: AssetIngestionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Never:
            provider_name = "agy_image"

            async def generate(self, request: AssetRequest, output_path: Path):
                raise AssertionError("breaker should have skipped this provider")

        class Ok:
            provider_name = "poyo_ai"

            async def generate(self, request: AssetRequest, output_path: Path):
                output_path.write_bytes(PNG_1X1)
                return AssetResult(
                    asset_id=request.asset_id,
                    asset_type=AssetType.IMAGE,
                    local_path=str(output_path),
                    scene_id=request.scene_id,
                    metadata={"provider": "poyo_ai"},
                )

        self._install(monkeypatch, manager, {"agy_image": Never(), "poyo_ai": Ok()})
        from video_editor_asset_ingestion.manager import ProviderCircuitBreaker

        breaker = ProviderCircuitBreaker("agy_image", failure_threshold=1, recovery_seconds=3600)
        breaker.record_failure()
        monkeypatch.setattr(
            manager, "_circuit_breaker", lambda name: breaker if name == "agy_image" else ProviderCircuitBreaker(name)
        )
        request = AssetRequest(
            asset_id="img-3", job_id="chain-job", scene_id="s1",
            asset_type=AssetType.IMAGE, prompt="x", output_ext="png",
        )

        result = await manager.resolve([request])

        assert result[0].metadata["_fallback_from"] == "agy_image"

    @pytest.mark.asyncio
    async def test_explicit_provider_bypasses_the_chain(
        self, tmp_path: Path, manager: AssetIngestionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pinned provider means exactly that provider — no agy first."""
        from video_editor_asset_ingestion.providers.visual.agy_image import AGYImageError

        class Never:
            provider_name = "agy_image"

            async def generate(self, request: AssetRequest, output_path: Path):
                raise AGYImageError("chain should have been bypassed")

        class Ok:
            provider_name = "poyo_ai"

            async def generate(self, request: AssetRequest, output_path: Path):
                output_path.write_bytes(PNG_1X1)
                return AssetResult(
                    asset_id=request.asset_id,
                    asset_type=AssetType.IMAGE,
                    local_path=str(output_path),
                    scene_id=request.scene_id,
                    metadata={"provider": "poyo_ai"},
                )

        self._install(monkeypatch, manager, {"agy_image": Never(), "poyo_ai": Ok()})
        request = AssetRequest(
            asset_id="img-4", job_id="chain-job", scene_id="s1",
            asset_type=AssetType.IMAGE, prompt="x", provider="poyo_ai", output_ext="png",
        )

        result = await manager.resolve([request])

        assert result[0].metadata["provider"] == "poyo_ai"
        assert "_fallback_from" not in result[0].metadata

    @pytest.mark.asyncio
    async def test_all_fail_reaches_the_placeholder(
        self, tmp_path: Path, manager: AssetIngestionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The final attempt keeps the existing FAL->stub terminal path."""
        from video_editor_asset_ingestion.config import AssetProviderConfig
        from video_editor_asset_ingestion.providers.visual.agy_image import AGYImageError

        manager.config = AssetProviderConfig(image_chain=["agy_image", "fal_ai"])

        class Broken:
            def __init__(self, name: str) -> None:
                self.provider_name = name

            async def generate(self, request: AssetRequest, output_path: Path):
                raise AGYImageError(f"{self.provider_name} is down")

        self._install(
            monkeypatch,
            manager,
            {"agy_image": Broken("agy_image"), "fal_ai": Broken("fal_ai")},
        )
        request = AssetRequest(
            asset_id="img-5", job_id="chain-job", scene_id="s1",
            asset_type=AssetType.IMAGE, prompt="x", output_ext="png",
        )

        result = await manager.resolve([request])

        assert result[0].metadata["provider"] == "stub_image"
        assert result[0].metadata["_fallback_from"] == "agy_image"
        assert result[0].metadata["chain_attempted"] == ["agy_image", "fal_ai"]
```

Note: `PNG_1X1`, `AssetRequest`, `AssetResult`, `AssetType`, `JobContext` must be imported at the top of `tests/test_asset_ingestion.py`; add the PNG constant and confirm imports:

```python
import base64

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_asset_ingestion.py::TestImageChain -v`
Expected: FAIL — the manager runs a single provider, so `test_first_failure_falls_through` raises `AGYImageError` instead of falling through.

- [ ] **Step 3: Implement the chain**

Three notes before editing:

1. The block to replace is everything in `_resolve_one` from `media_path, meta_path = self._cache_paths(request)` down to the end of the `async with self._provider_semaphore(provider_name):` block. The current `provider_name = ...` / `_resolve_auto_provider` lines become part of `_provider_chain`.
2. `_count_image()` is called **once per resolved request**, before the walk — not per attempt. A request that burns three providers still costs one image against the cap.
3. The terminal placeholder is still FAL's: `_generate_one` only substitutes `StubImageProvider` when the *last* attempt is `fal_ai`. For any other terminal provider the exception propagates, exactly as today. `stub_fallback=True` means "this attempt may stub", not "stub every provider".

Replace that block with:

```python
            media_path, meta_path = self._cache_paths(request)
            chain = self._provider_chain(request)
            if not chain:
                raise RuntimeError(
                    f"No provider configured for asset type {request.asset_type.value}"
                )
            if request.asset_type is AssetType.IMAGE:
                self._count_image()

            failures: list[tuple[str, str]] = []
            for index, provider_name in enumerate(chain):
                last = index == len(chain) - 1
                try:
                    result = await self._attempt_provider(
                        request,
                        provider_name,
                        media_path,
                        meta_path,
                        stub_fallback=last,
                    )
                except Exception as exc:  # noqa: BLE001
                    failures.append((provider_name, str(exc)))
                    event = (
                        "provider_chain_skipped"
                        if isinstance(exc, CircuitBreakerOpenError)
                        else "provider_chain_failed"
                    )
                    logger.warning(
                        event,
                        job_id=request.job_id,
                        asset_id=request.asset_id,
                        provider=provider_name,
                        error=str(exc),
                        remaining=chain[index + 1 :],
                    )
                    if not last:
                        continue
                    raise
                if failures:
                    result.metadata["_fallback_from"] = failures[0][0]
                    result.metadata["chain_attempted"] = [n for n, _ in failures] + [provider_name]
                return result

            raise RuntimeError("empty provider chain")  # defensive; loop always returns or raises
```

Add the helpers:

```python
    def _provider_chain(self, request: AssetRequest) -> list[str]:
        """Return the providers to try, in order, for this request.

        An explicit provider means exactly that provider; otherwise the
        configured image chain applies; otherwise the existing default/scored
        selection is used unchanged.
        """
        explicit = request.provider
        if explicit and explicit != "auto":
            return [explicit]
        if request.asset_type is AssetType.IMAGE and self.config.image_chain:
            return list(self.config.image_chain)
        name = self.config.defaults.get(request.asset_type.value, "")
        if name == "auto":
            name = self._resolve_auto_provider(request)
            request.provider = name
        return [name] if name else []

    async def _attempt_provider(
        self,
        request: AssetRequest,
        provider_name: str,
        media_path: Path,
        meta_path: Path,
        *,
        stub_fallback: bool,
    ) -> AssetResult:
        """Run one provider with its own budget reservation, semaphore, and breaker."""
        breaker = self._circuit_breaker(provider_name)
        # Check before reserving: an open breaker costs nothing, so there is
        # no reservation to unwind. The error propagates to the chain loop.
        breaker.check()

        estimate: BudgetEstimate | None = None
        if self.budget is not None:
            estimate = self.budget.allocate(
                provider_name,
                request.asset_type.value,
                metadata={"asset_id": request.asset_id},
                **self._budget_context(request, provider_name),
            )
            self._persist_budget_snapshot()

        async with self._provider_semaphore(provider_name):
            try:
                result = await self._generate_one(
                    request,
                    media_path,
                    meta_path,
                    estimate=estimate,
                    provider_name=provider_name,
                    stub_fallback=stub_fallback,
                )
            except Exception:
                breaker.record_failure()
                self._release_estimate(estimate)
                self._persist_budget_snapshot()
                raise
            if result.metadata.get("_fallback_from") == provider_name:
                # A placeholder came back: the provider failed, nothing was
                # purchased, so the reservation must not stay outstanding.
                breaker.record_failure()
                self._release_estimate(estimate)
            else:
                breaker.record_success()
            return result

    def _release_estimate(self, estimate: BudgetEstimate | None) -> None:
        """Undo a budget reservation for an attempt that produced nothing."""
        if estimate is not None and self.budget is not None:
            self.budget.record_actual(estimate, 0.0)
```

Change `_generate_one` to accept the provider name and the stub flag:

```python
    async def _generate_one(
        self,
        request: AssetRequest,
        media_path: Path,
        meta_path: Path,
        estimate: BudgetEstimate | None = None,
        *,
        provider_name: str | None = None,
        stub_fallback: bool = True,
    ) -> AssetResult:
```

Inside it, replace `provider = self._resolve_provider(request)` with:

```python
        provider = self._resolve_provider(request, provider_name)
```

and change the FAL fallback condition to:

```python
            if stub_fallback and provider.provider_name == "fal_ai" and request.asset_type in {
```

Change `_resolve_provider`'s signature and first line:

```python
    def _resolve_provider(
        self, request: AssetRequest, provider_name: str | None = None
    ) -> GenerativeProvider:
        """Instantiate (or reuse) the right provider for a request."""
        provider_name = provider_name or request.provider or self.config.defaults.get(
            request.asset_type.value, ""
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_asset_ingestion.py -v`
Expected: PASS (existing bugs #10/#13/#83 tests included)

- [ ] **Step 5: Commit**

```bash
git add packages/asset_ingestion/src/video_editor_asset_ingestion/manager.py tests/test_asset_ingestion.py
git commit -m "feat(assets): walk the image provider chain with per-provider fallback"
```

---

### Task 6: Per-run image cap

**Files:**
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/manager.py`
- Test: `tests/test_asset_ingestion.py`

**Interfaces:**
- Produces: `ImageQuotaError(VEProviderError)` and `_count_image()`; `max_images_per_run` enforced per manager.

- [ ] **Step 1: Write the failing test**

Append to `TestImageChain`:

```python
    @pytest.mark.asyncio
    async def test_image_cap_stops_the_run(
        self, tmp_path: Path, manager: AssetIngestionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from video_editor_asset_ingestion.manager import ImageQuotaError

        manager.config.max_images_per_run = 2

        class Ok:
            provider_name = "agy_image"

            async def generate(self, request: AssetRequest, output_path: Path):
                output_path.write_bytes(PNG_1X1)
                return AssetResult(
                    asset_id=request.asset_id,
                    asset_type=AssetType.IMAGE,
                    local_path=str(output_path),
                    scene_id=request.scene_id,
                    metadata={"provider": "agy_image"},
                )

        self._install(monkeypatch, manager, {"agy_image": Ok(), "poyo_ai": Ok()})
        requests = [
            AssetRequest(
                asset_id=f"img-{i}", job_id="chain-job", scene_id=f"s{i}",
                asset_type=AssetType.IMAGE, prompt=f"p{i}", output_ext="png",
            )
            for i in range(3)
        ]

        with pytest.raises(ImageQuotaError, match="cap reached"):
            await manager.resolve(requests)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_asset_ingestion.py::TestImageChain::test_image_cap_stops_the_run -v`
Expected: FAIL — `ImportError: cannot import name 'ImageQuotaError'`

- [ ] **Step 3: Implement the cap**

Add next to `CircuitBreakerOpenError`:

```python
class ImageQuotaError(VEProviderError):
    """Raised when a run asks for more images than ``max_images_per_run``."""
```

In `AssetIngestionManager.__init__`, add:

```python
        self._images_started = 0
```

Add the helper:

```python
    def _count_image(self) -> None:
        """Count one image generation against the per-run cap."""
        limit = self.config.max_images_per_run
        self._images_started += 1
        if limit and self._images_started > limit:
            raise ImageQuotaError(
                f"Image cap reached: {limit} images per run (max_images_per_run); "
                f"refusing image {self._images_started}"
            )
```

`_resolve_one` already calls `self._count_image()` after a cache miss (Task 5).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_asset_ingestion.py::TestImageChain -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/asset_ingestion/src/video_editor_asset_ingestion/manager.py tests/test_asset_ingestion.py
git commit -m "feat(assets): cap image generations per run"
```

---

### Task 7: Configuration, pricing note, and docs

**Files:**
- Modify: `config.yaml` (`assets:` section)
- Modify: `config.example.yaml`
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/budget.py:60-70`
- Modify: `docs/CLI_USAGE.md`, `docs/POYO_ROUTING.md`

**Interfaces:**
- Consumes: config fields from Task 4, provider name from Task 3.

- [ ] **Step 1: Add the YAML block**

In `config.yaml`, inside `assets:` beside `providers:`:

```yaml
  # Ordered image providers. Explicit --image-provider/request.provider wins
  # over this list; an empty list restores defaults/scored selection.
  image_chain: [agy_image, poyo_ai, fal_ai, openrouter]
  # Unmetered agy generations consume Google quota, not credits.
  max_images_per_run: 25
```

In `config.yaml` under `assets.providers:`, add:

```yaml
    agy_image:
      # Local Gemini CLI agent. Cost is 0.0 (quota-based).
      timeout: 300.0
      provider_options:
        agy_bin: agy
        sandbox: true   # enforced; false is ignored with a warning
        model: ""       # empty = let agy choose
```

Apply the same block to `config.example.yaml`.

- [ ] **Step 2: Document the zero price**

In `budget.py`, in `DEFAULT_PRICING["image"]`, add:

```python
        # Quota-based, not credit-based. Also 0.0 in the pricing registry
        # because agy_image has no model-level price entry.
        "agy_image": 0.0,
```

- [ ] **Step 3: Update docs**

Append to `docs/POYO_ROUTING.md`:

```markdown
## agy (Gemini CLI) as the primary image provider

`assets.image_chain` defaults to `[agy_image, poyo_ai, fal_ai, openrouter]`.
`agy_image` runs `agy --sandbox -p …` locally and verifies the written PNG
before returning it. It costs 0 credits (it consumes Google OAuth quota), so
`--budget` only limits the paid fallbacks. `max_images_per_run` (default 25)
bounds quota use. An explicit `--image-provider poyo_ai` bypasses the chain.
Expect seconds-to-minutes per image: this is an agent round-trip, not an API.
```

Append to `docs/CLI_USAGE.md`:

```markdown
### Image provider chain

Images try `agy_image` first, then `poyo_ai`, `fal_ai`, `openrouter`
(`assets.image_chain`). Override for one run with `--image-provider poyo_ai`.
A run stops after `assets.max_images_per_run` generations (default 25) with
`ImageQuotaError`.
```

- [ ] **Step 4: Verify config loads**

Run: `python -c "from video_editor_asset_ingestion.config import AssetProviderConfig as C; c=C.load('config.yaml'); print(c.image_chain, c.max_images_per_run, 'agy_image' in c.providers)"`
Expected: `['agy_image', 'poyo_ai', 'fal_ai', 'openrouter'] 25 True`

- [ ] **Step 5: Commit**

```bash
git add config.yaml config.example.yaml packages/asset_ingestion/src/video_editor_asset_ingestion/budget.py docs/CLI_USAGE.md docs/POYO_ROUTING.md
git commit -m "feat(assets): configure agy_image chain and document routing"
```

---

### Task 8: Integration test and full verification

**Files:**
- Create: `tests/test_agy_image_integration.py`

**Interfaces:**
- Consumes: `AGYImageProvider`, real `agy` binary.

- [ ] **Step 1: Write the integration test**

Create `tests/test_agy_image_integration.py`:

```python
"""Real-agy smoke test. Skipped unless explicitly selected.

Run with: pytest tests/test_agy_image_integration.py -m integration -v
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers.visual.agy_image import AGYImageProvider


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_agy_generates_a_readable_png(tmp_path: Path) -> None:
    if shutil.which("agy") is None:
        pytest.skip("agy not installed")
    provider = AGYImageProvider(ProviderConfig(timeout=300.0, provider_options={"agy_bin": "agy"}))
    request = AssetRequest(
        asset_id="img-real",
        job_id="job-real",
        scene_id="s1",
        asset_type=AssetType.IMAGE,
        prompt="a plain red circle centred on a white background",
        output_ext="png",
        params={"width": 512, "height": 512},
    )
    output = tmp_path / "real.png"

    result = await provider.generate(request, output)

    assert output.exists() and output.stat().st_size > 1000
    assert result.metadata["width"] > 0
    assert result.metadata["height"] > 0
```

- [ ] **Step 2: Register the marker**

Confirm `pyproject.toml` already declares `integration` in `markers`; if not, add:

```toml
markers = [
    "integration: tests that call real external binaries or services",
]
```

Run: `pytest tests/test_agy_image_integration.py -m integration -v`
Expected: PASS against the real binary (or SKIP if not installed).

- [ ] **Step 3: Run the whole suite**

Run: `pytest tests/ -q`
Expected: all tests pass, same count as before plus the new agy tests.

- [ ] **Step 4: Run one real image through the chain**

Run: `pytest tests/test_agy_image_integration.py -m integration -v`
Expected: PASS, and `tmp_path/real.png` is a valid PNG.

- [ ] **Step 5: Commit**

```bash
git add tests/test_agy_image_integration.py pyproject.toml
git commit -m "test(assets): add opt-in real-agy integration test"
```

---

## Self-Review

**Spec coverage**

| Spec section | Task |
|---|---|
| `AGYImageProvider` contract, prompt/aspect, argv, sandbox | 1 |
| Verification, timeouts, non-retryable errors, PNG-only | 2 |
| Registry + `ProviderConfig` injection | 3 |
| `image_chain`, `max_images_per_run`, typo validation | 4 |
| Chain precedence, breaker skip, budget release, `_fallback_from`, `chain_attempted` | 5 |
| Per-run cap / `ImageQuotaError` | 6 |
| `config.yaml`, `config.example.yaml`, zero price, docs | 7 |
| Fake-binary tests, integration test | 1–3, 8 |

**Type consistency checked:** `AGYImageError`, `ImageQuotaError`, `_attempt_provider(..., stub_fallback=)`, `_resolve_provider(request, provider_name=None)`, `_generate_one(..., provider_name=..., stub_fallback=)`, `_provider_chain`, `_count_image`, `_release_estimate` are each defined once and used with the same signature everywhere.

**Known ordering dependency:** Task 3 imports `AGYImageProvider`; Tasks 5–6 assume Task 4's config fields exist. Execute in order.

**Placeholder scan:** no TBD/TODO; every code step shows complete code.
