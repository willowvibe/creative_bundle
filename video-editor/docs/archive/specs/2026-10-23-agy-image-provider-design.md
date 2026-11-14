# agy Image Provider with Ordered Fallback — Design

**Date:** 2026-10-23
**Status:** Approved for planning
**Deciders:** user (scope: every scene still; cost model: free + per-run cap)
**Builds on:** `agy` v1.4.6 at `~/.local/bin/agy` (Gemini CLI, internals named "jetski")

## 1. Problem

`agy` produces noticeably good images — a test run generated a 1376×768 Red Fort
Independence Day scene that matched the prompt closely (verified 2026-10-23,
`outputs/agy-delegation-2026-10-23/independence-day-1947.png`,
2,032,548 bytes, sha256 `d346b3a4…`). It uses the user's existing Google OAuth
quota, so it costs no API credits.

The pipeline cannot use it. Image generation today resolves to exactly one
provider per request (`AssetIngestionManager._resolve_one`), and on failure it
falls back only to `StubImageProvider`, never to another real provider. There is
no way to say "try agy, then PoYo, then FAL".

This spec adds `agy` as a first-class image provider and adds an ordered
provider chain to the manager.

## 2. Goals

- `agy_image` is a normal `GenerativeProvider` in the registry.
- Images default to the chain `agy_image → poyo_ai → fal_ai → openrouter`.
- A failed agy generation falls through to PoYo without failing the job.
- The chain is policy in `config.yaml`; providers stay unaware of it.
- No new cost plumbing: agy images price at 0.0.
- A per-run cap bounds unmetered quota consumption.
- CI never calls the real `agy`.

## 3. Non-goals

Video generation through agy (it has no video tool), changing TTS/music
fallback, changing `select_provider` scoring, caching changes, and any agy
feature beyond `generate_image`.

## 4. Findings that constrain the design

Verified before designing (2026-10-23):

| Finding | Evidence |
|---|---|
| Headless `agy -p` generates and writes an image to an absolute path | `independence-day-1947.png`, 1376×768 |
| The image tool needs no permission grant | ran with no prompt |
| `--sandbox` blocks shell execution but still writes images | `sandbox-test.png`, 400×400, exit 0 |
| Output dimensions follow the prompt loosely | 1376×768 landscape for a landscape prompt; 400×400 for "minimal" |
| Multi-line command grants are rejected, and an agent session can execute shell commands | `permission_grant_store.go:482 ignoring invalid allow entry` |
| Latency is an agent round-trip (tens of seconds) | accepted by the user: "delay ok in our case of agy cli" |

The sandbox finding is the security boundary: the provider feeds
model-written script text into an agent whose user settings allow `command(*)`.
Without `--sandbox`, a prompt-injection in a script could execute shell
commands.

## 5. Components

| Unit | Responsibility | Depends on |
|---|---|---|
| `AGYImageProvider` (new, `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/visual/agy_image.py`) | one image request → one verified file; no fallback knowledge | `agy` binary, `ProviderConfig` |
| chain walk in `AssetIngestionManager` | ordering, fallback, cap, breaker, budget | registry, config |
| `AssetProviderConfig` (edit, `config.py`) | `image_chain`, `max_images_per_run`, validation | — |
| `config.yaml`, `config.example.yaml` (edit) | the policy | — |
| registry (edit, `providers/registry.py`, `manager._resolve_provider`) | construct with `ProviderConfig` | — |

### 5.1 `AGYImageProvider`

```python
class AGYImageProvider(GenerativeProvider):
    provider_name = "agy_image"

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult: ...
```

Steps:

1. **Prompt.** `request.prompt`, plus one line derived from `params["width"]` /
   `params["height"]` when both are positive ints, e.g.
   `Aspect: vertical 9:16 (1080x1920).` Omitted when absent.
2. **Arguments.** A list, never a shell string:
   `[agy_bin, "--sandbox", "-p", f"{prompt}\n\nSave the image to {tmp}.png and reply with the path.", "--output-format", "text"]`.
   `--model` is appended only when `provider_options.model` is non-empty.
3. **Process.** `asyncio.create_subprocess_exec(..., cwd=<job scratch dir>, start_new_session=True)`,
   stdout/stderr captured, wrapped in `asyncio.wait_for(timeout)`.
   On timeout: `os.killpg` the process group, then raise `AGYImageError`.
4. **Verify.** `tmp` exists, size > 0, `_validate_image_bytes` (reused from
   `providers/visual/fal_ai.py`), dimensions via `probe_media`. A dimension
   mismatch against the requested aspect is logged as
   `agy_image_aspect_mismatch` and is **not** a failure.
5. **Commit.** `os.replace(tmp, output_path)`.
6. **Result.** `AssetResult(asset_id, AssetType.IMAGE, local_path, scene_id,
   metadata={"provider": "agy_image", "model": <configured or "agy-default">,
   "width": …, "height": …, "latency_ms": …, "cost": 0.0})`.

**Errors.** Any of {non-zero exit, missing file, invalid bytes, timeout}
raises `AGYImageError(VEProviderError)` with the last 500 bytes of stderr.
`AGYImageError` is deliberately not retryable: `manager._is_retryable` returns
False for it, and the provider re-wraps `asyncio.TimeoutError` so it is not
mistaken for a transient transport failure and retried three times.

**Format.** `agy_image` writes PNG only. Every current image request sets
`output_ext="png"` (`scene_assets.py:163,178`). When `request.output_ext` is
non-empty and not `png`, the provider raises `AGYImageError` *before* invoking
agy, so the chain falls through to a provider that can honour the requested
format rather than writing PNG bytes under a `.jpg` name.

### 5.2 Chain walk

Provider precedence in `_resolve_one`, explicit and ordered:

1. `request.provider` set and not `"auto"` → single provider, no chain.
2. `config.image_chain` non-empty → walk it in order.
3. Otherwise → current behavior (`assets.defaults`, then scored selection).

Per attempt:

- Skip a provider whose circuit breaker is open (`ProviderCircuitBreaker.check`
  is caught and logged as `provider_chain_skipped`), where today it aborts the
  asset.
- `BudgetGovernor.allocate()` per attempt; on failure
  `record_actual(estimate, 0.0)` so the reservation is released.
- Each failed attempt records a failure on **that** provider's breaker; the
  provider that succeeds records a success.
- On success, return with `_fallback_from` = first failed provider and
  `chain_attempted` = the names tried. `_fallback_from` keeps Bug #13's meaning
  (a successful-looking `AssetResult` whose primary failed), so cost lines and
  logs still attribute the failure correctly.
- All providers fail → existing terminal path: uncached stub placeholder plus
  the `asset_provider_fallback` warning.

Cache behavior is unchanged: the successful provider's result is cached under
the request's normal key; chain failures do not poison it.

### 5.3 Per-run cap

`max_images_per_run` (default 25) counts `AssetType.IMAGE` requests that reach
a provider, per manager instance (one manager per job). Exceeding it raises
`ImageQuotaError(VEProviderError)` before any provider is invoked. Cache hits do
not count.

## 6. Configuration

```yaml
assets:
  image_chain: [agy_image, poyo_ai, fal_ai, openrouter]
  max_images_per_run: 25
  providers:
    agy_image:
      timeout: 300.0
      provider_options:
        agy_bin: agy
        sandbox: true      # enforced True; a false value logs a warning and is ignored
        model: ""          # empty = agy's own choice
```

`AssetProviderConfig` gains `image_chain: list[str] = []` and
`max_images_per_run: int = 25`. A validator rejects chain entries that are not
registered providers, so a typo fails at load instead of mid-job.

`DEFAULT_PRICING["image"]["agy_image"] = 0.0` documents intent; the code
already yields 0.0 for unpriced providers through both the registry and legacy
paths.

## 7. Safety

What the provider enforces:

- **Argv-only invocation.** The CLI is started with an argv list, never a
  shell string, so prompt text cannot become shell syntax.
- **`--sandbox` is mandatory** and restricts the agent's *terminal* commands.
  Provider options cannot disable it.
- **A fixed output path we verify.** The output path is resolved to an absolute
  path, chosen by us, and only that exact temp file is accepted.
- **A per-job cwd.** The child runs in the job's cache directory
  (`<work_dir>/.cache/assets`), not the repository root.
- **Timeout/cancellation kill the whole process group.**
- **`agy_bin` is resolved through `shutil.which`**; a missing binary fails the
  provider with a clear error, which the chain then falls past.

What it does **not** enforce:

- `--sandbox` does not confine the agent's **file tools** to `cwd`. A
  prompt-injected prompt could make the agent write outside the job directory;
  the per-job cwd limits accidental strays, not a determined agent.
- Prompts are therefore **assumed to be our own content** (plan-derived visual
  descriptions), not arbitrary untrusted text. Feeding third-party text into
  the prompt would need a real filesystem sandbox first.
- The sandbox restricts terminal commands; it is not a container or VM.

## 8. Testing

| Test | Type |
|---|---|
| fake `agy` script that writes a valid PNG → `generate()` succeeds, dimensions recorded | unit, no quota |
| fake that exits 0 without writing → `AGYImageError`, no retry | unit |
| fake that writes text bytes → `AGYImageError` | unit |
| fake that sleeps past a 1 s timeout → killed, `AGYImageError`, no retry | unit |
| prompt contains width/height → aspect line present in argv | unit |
| chain: agy fails → poyo succeeds → `_fallback_from="agy_image"` | unit |
| chain: open breaker skipped, next provider used | unit |
| chain: all fail → stub + warning | unit |
| cap: 26th image raises `ImageQuotaError` | unit |
| explicit `provider="poyo_ai"` bypasses the chain | unit |
| real `agy` produces a readable image | `@pytest.mark.integration`, skipped by default |

Fake binaries are generated in `tmp_path` and injected via
`provider_options.agy_bin`, so no test depends on PATH state.

## 9. Docs

- `docs/CLI_USAGE.md`: `--image-provider`, chain override, cap behavior.
- `docs/POYO_ROUTING.md`: where agy sits, cost = 0, latency expectation.
- `config.example.yaml`: the block above.

## 10. Acceptance criteria

1. With `image_chain` configured, `ve run` on a multi-scene job produces every
   still through agy when it succeeds.
2. Killing agy (bad `agy_bin`) makes the same job complete through PoYo with
   `_fallback_from="agy_image"` in the asset metadata and a warning in the log.
3. A job requesting 26 images stops at the cap with `ImageQuotaError` and a
   clear message.
4. Full test suite passes; one integration run produces a real image.

## 11. Risks

| Risk | Mitigation |
|---|---|
| Latency: a 20-scene video now waits on agent round-trips, 2 at a time | accepted by the user; chain makes it recoverable; `--image-provider poyo_ai` restores the old path |
| Google quota exhausted mid-job | `max_images_per_run`; breaker opens after 3 failures and the chain falls through |
| agy changes its CLI or artifact behavior | provider is one file behind the `GenerativeProvider` interface; fake-binary tests pin the contract |
| Prompt injection via script text | `--sandbox`, argv (no shell), scratch cwd, fixed output path |
| Unpriced provider breaks budget math | priced 0.0 explicitly; `--budget` stays meaningful for the paid fallbacks |
