# Audit Remediation — Implementation Plan (2026-10-28)

> **For agentic workers:** execute one task at a time, in order, and stop at a
> green tree after each. The task ids (`P1.1` … `P4.9`) are the handles used by
> `docs/ISSUES_BUGS.md`.

**Source findings:** `docs/archive/audits/AUDIT_2026-10-28.md` (#298–#310, A9–A13, §5.3).
**Backlog (state, not rationale):** `docs/ISSUES_BUGS.md`.
**Forward product plan:** `docs/archive/plans/ROADMAP_2026-10-28.md`.

**Goal:** close every HIGH and MEDIUM correctness finding in one short sprint,
make the guards structural so the next feature cannot reopen them, then start
the creative roadmap behind its own gates. The audit's prioritised list (§6) is
the input; this file turns it into ordered, testable tasks.

**Status 2026-10-28:** P1 complete; P2.1–P2.3 complete; P3 complete except the
data-dir move (A13, user decision); audit findings #298–#310 and A9–A11 closed.
P4 has started: look-ban gate (P4.1), director's brief (P4.2), measured
style extraction (P4.4), beat quantization (P4.6), brand capture (P4.5),
critique loop (P4.3), motion primitives (P4.7) and the sandboxed HTML layer
(P4.9) landed. P4.8 is blocked on provider image references.

---

## 0. Scope decisions

| Track | Contents | Ship rule |
|---|---|---|
| **P1 — Correctness sprint** | #298, #299, #300, #301, #302, #304 | Must land before any new feature work. ~2 days. |
| **P2 — Structural guardrails** | A9 rule, A10 trust levels, #303 + A11 parity | Land as one design, three commits. |
| **P3 — LOW findings** | #305–#310 | Independently shippable; order by blast radius. |
| **P4 — Creative roadmap** | §5.3 items 3, 7, 1, 2, 6, 4, 5, 8, 9 | Each item is its own feature with a demo pack and a cost cap. |

**Non-goals for this plan:** no provider expansion, no new renderer, no changes
to the QA pass/fail gates, no history rewrite (H1 decision), no change to the
accepted `.git` size.

**Global constraints**

- Python 3.12; `ruff`, `ruff format`, `mypy apps packages`, `lint-imports`, and
  the non-slow suite must stay green after every task.
- No new runtime dependency for P1–P3. P4 may add browser/vision tooling, but
  only behind an extra.
- Fixes and refactors don't share a commit. One finding → one commit.
- Any behaviour change to the queue, MCP surface, or spend path needs a test
  that fails on the old code.
- `find_usages` before changing `complete`/`fail`/`heartbeat`/`execute`.

**Gate for every task**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
uv run lint-imports
uv run pytest -q -n 8 -m "not integration and not slow"
```

---

## 1. Sequencing

```text
P1.1  off-loop dispatch ──► P1.2 lease token ──► P1.3 lock release ──► P1.2/P1.3 regression test
                                                      │
P1.4  registry/MCP guard ──────────────────────────────┤ (independent)
P1.5  SFX provider auth/licence ───────────────────────┤ (independent)
P1.6  short manifest save ─────────────────────────────┤ (independent)
P1.7  BLE001/S110 ─────────────────────────────────────┘
P2.1  A9 rule + run_sync      P2.2 A10 trust levels      P2.3 #303/A11 parity
P3.x  LOW items (any order)
P4.x  creative track (gated on P1)
```

Ordering rationale: the heartbeat only works once the loop is free (P1.1), and
the lease only protects a job once beats actually arrive (P1.2). Doing the lease
first would add rows and tests that the stall defeats anyway.

| Task | Finding | Size | Depends on |
|---|---|---|---|
| P1.1 | #298 (starved heartbeat), A9 | M | — |
| P1.2 | #298 (lost lease, double run) | M | P1.1 |
| P1.3 | #298 (`JobLockError` calls `fail`) | S | P1.2 |
| P1.4 | #299 | S | — |
| P1.5 | #300, #301 | S | — |
| P1.6 | #302 | XS | — |
| P1.7 | #304 | S | — |
| P2.1 | A9 | S | P1.1 |
| P2.2 | A10 | M | P1.4 |
| P2.3 | #303, A11 | M | P2.2 |
| P3.1–P3.6 | #305–#310 | S each | — |
| P4.x | §5.3 | S–L | P1 |

---

## 2. Phase 1 — correctness sprint

### P1.1 — Run handlers and skill stages off the event loop (#298, A9)

**Problem.** `_dispatch_message` calls sync handlers directly on the loop
(`apps/cli/src/video_editor_cli/main.py:527`), and `_heartbeat`
(`apps/cli/src/video_editor_cli/cli_support.py:1152`) is an asyncio task. Any
handler that blocks — most of them — starves the beat, so a job older than
`queue.stale_seconds` (900 s) is reclaimed while it runs.

**Files**

- Modify `apps/cli/src/video_editor_cli/main.py` (`_dispatch_message`)
- Modify `packages/pipelines/src/video_editor_pipelines/pipeline.py`
  (`run_async`, stages at ~456, 481, 493, 905, 981)
- Modify `packages/pipelines/src/video_editor_pipelines/handlers.py`
  (`_handle_render_fablecut`'s sync `render_fablecut_project` call)
- Test `tests/test_worker_heartbeat.py` (new)

- [x] **Step 1.** Write the failing test: a sync handler that `time.sleep`s for
      0.65 s, the real `_heartbeat` with `stale_seconds=0.3` (0.1 s interval).
      Assert at least three beats arrived; on the old code the task never runs.
- [x] **Step 2.** In `_dispatch_message`, branch on
      `inspect.iscoroutinefunction(handler)`; await coroutines, run sync ones
      with `await asyncio.to_thread(handler, ctx, payload)`. Keep the existing
      exception handling and status literals unchanged.
- [x] **Step 3.** The worker path now calls the sync entry points in a thread:
      `_handle_run` uses `asyncio.to_thread(skills.run, …)` and
      `asyncio.to_thread(pipeline.run, …)` for the music-video branch, so
      `run_async`'s inline stages run off the loop too. `run_async` stays
      callable for direct async callers; P2.1 decides whether to rename it.
- [x] **Step 4.** Wrapped the sync `render_fablecut_project` call in
      `_handle_render_fablecut` the same way.
- [x] **Step 5.** Re-ran the new test plus
      `tests/test_queue_multiprocess.py` and `tests/test_worker_payload_paths.py`.

**Acceptance:** heartbeat test green; no behavioural change to job statuses;
`asyncio.to_thread` contextvars still carry `job_id` into structlog (assert the
log record carries it in the test).

**Watch out:** `to_thread` runs in a worker thread — thread-local
`sqlite3` connections, `signal` handlers, and `asyncio.run` inside skills must
be checked. `JobContext`'s ledger connection is already created per process; if
a skill opens one lazily on the wrong thread, move the open into the call.

---

### P1.2 — Give the claim a lease token (#298)

**Problem.** `dequeue` sets `status='running'` but no owner identity
(`packages/state/src/video_editor_state/queue.py:606`). `_complete_sync`
(667), `_fail_sync` (681) and `_heartbeat_sync` (781) update `WHERE id = ?`
only, so a second worker that loses the race can still complete, fail, or
dead-letter a job it does not own.

**Design**

- `JobMessage` gains `lease_id: str | None = None`
  (`packages/state/src/video_editor_state/queue.py:48`). Not serialised to the
  manifest.
- Schema: `lease_id TEXT` in the fresh `CREATE TABLE`, plus a guarded
  `ALTER TABLE … ADD COLUMN lease_id TEXT` in `_init_db` (same retry pattern as
  `retry_after`).
- `_dequeue_sync` writes `lease_id = :lease` (uuid4 hex) in the same
  `UPDATE … RETURNING`; `_row_to_message` copies it out.
- `_heartbeat_sync`, `_complete_sync`, `_fail_sync` add
  `AND status='running' AND lease_id = :lease`. On zero rows, raise
  `LeaseLostError` (new, in `video_editor_state/errors.py`, exported from the
  package). `_fail_sync` must select and update inside one `BEGIN IMMEDIATE` so
  a reclaim between the SELECT and the UPDATE cannot slip through.
- `_requeue_stale_sync` clears `lease_id`; `InMemoryJobQueue` mirrors the same
  guard (set the field on dequeue, check on beat/complete/fail, clear on
  requeue).
- Worker: catch `LeaseLostError` separately — log
  `worker_lease_lost`, stop heartbeating, do **not** call `queue.fail`.

**Files:** `packages/state/src/video_editor_state/queue.py`,
`.../errors.py`, `packages/state/src/video_editor_state/__init__.py`,
`apps/cli/src/video_editor_cli/main.py`, `tests/test_queue.py`,
`tests/test_queue_multiprocess.py`.

- [x] **Step 1.** Failing test in `tests/test_queue_multiprocess.py`: one
      connection claims and heartbeats; a second sweeper keeps the claim while
      beats are fresh; after the timeout expires it reclaims and the old
      worker's `complete` raises `LeaseLostError`. The in-memory mirror lives in
      `tests/test_queue.py::TestLeaseOwnership`.
- [x] **Step 2.** Added the field, migration, and guarded updates. An old DB
      (schema without the column) opens and claims correctly.
- [x] **Step 3.** Updated both queue implementations and the worker; the
      in-memory queue returns a per-claim copy so identity, not a shared
      mutable field, is the ownership check.
- [x] **Step 4.** Re-ran the P1.1 test and the reclaim test.

**Acceptance:** fresh beats keep the claim (the sweeper returns `[]`); after
expiry only the new owner can heartbeat/complete/fail, and the old owner gets
`LeaseLostError`; a retry is charged only by the sweeper, never by a lost
owner.

**Watch out:** `JobMessage` is a mutable dataclass shared by tests; make
`lease_id` optional and default `None` so existing constructed messages still
work. Search every `queue.complete(`/`queue.fail(` call site first.

---

### P1.3 — Release the lock claim instead of failing the job (#298)

**Problem.** `JobLockError` falls into the loop-level `except` in
`ve worker` (`apps/cli/src/video_editor_cli/commands/queue.py`) and calls
`queue.fail()` on a job the worker does not own — consuming a retry per tick.

**Design**

- Add `release(message)` to `JobQueue`: `status='pending'`, `lease_id=NULL`,
  `started_at=NULL`, `retry_after = now + small jitter`, **no** retry increment,
  guarded by `lease_id` and `status='running'`.
- Worker: catch `JobLockError` before the generic handler. Release, sleep
  ~0.5 s, continue. Keep a consecutive-lock-error counter so a permanently held
  lock cannot hot-spin; after N tries, log and stop the worker cleanly.
- In-memory queue parity.

**Files:** `packages/state/src/video_editor_state/queue.py`,
`apps/cli/src/video_editor_cli/commands/queue.py`, `tests/test_queue.py`,
`tests/test_job_lock.py`.

- [x] **Step 1.** Failing test: worker B dequeues a job whose work dir is
      locked; assert the job returns to `pending` with the same `retries` and
      the same `error` field (not overwritten with `JobLockError`).
- [x] **Step 2.** Implemented `release` on both backends and the new worker
      branch (bounded to five contentions before the worker stops).
- [x] **Step 3.** Both `release` and the stale sweeper are conditional updates,
      so they cannot both move the row.

**Acceptance:** a locked job is never failed by a non-owner; `ve worker`
terminates after the bounded retry instead of spinning.

---

### P1.4 — Restrict MCP `run_command` and run sync registry handlers off-loop (#299)

**Problem.** `packages/mcp_server/src/video_editor_mcp/tools/commands.py:58`
forwards any registry command with client JSON. That reopens #276 (arbitrary
roots and `plan_out_name`) and #277 (uncapped `assets.resolve`), and
`CommandRegistry.execute` (`apps/engine/src/video_editor_engine/commands/registry.py:145`)
runs sync handlers inline, blocking stdio on `render.run`.

**Design**

- Add `mutates: bool = False` and `spends: bool = False` to `Command`
  (`registry.py`), include them in `to_dict()`, and set them on the four
  pipeline commands: `project.status` read-only; `qa.run` mutates;
  `assets.resolve` mutates + spends; `render.run` mutates.
- MCP `run_command` refuses any command with `mutates or spends`, naming the
  guarded tool to use instead (`assets.resolve` → `generate_image`/
  `run_skill`, `render.run` → `render`). Refuse a `config_path` key in
  `params_json` for every command.
- `CommandRegistry.execute` awaits coroutine handlers and
  `await asyncio.to_thread(...)` for sync ones.
- Leave `ve commands` / `ve control` permissive for now; P2.2 generalises the
  policy.

**Files:** `packages/engine`… (`commands/registry.py`, `commands/pipeline.py`),
`packages/mcp_server/src/video_editor_mcp/tools/commands.py`,
`tests/test_command_registry.py` (MCP and thread tests live in the existing
registry suite).

- [x] **Step 1.** Tests: `run_command assets.resolve` is refused; a
      `config_path` param is refused; a sync handler reports a different
      thread id than the loop thread.
- [x] **Step 2.** Added metadata + MCP refusal + thread offload.
- [x] **Step 3.** `list_commands` still advertises the refused commands
      (discoverability) and marks them `mutates`/`spends`.
- [x] **Step 4.** The control server's `execute` path still works (registry
      and control tests pass).

**Acceptance:** no MCP call can reach `assets.resolve` or `render.run`; a long
render command no longer blocks the stdio loop.

---

### P1.5 — Fix the SFX provider auth and licence checks (#300, #301)

**#300 Openverse.** The bearer token is set as a client default header and the
audio bytes are fetched from `chosen["url"]` (an arbitrary indexed host), so
httpx sends the token to third parties. Redirects are not followed, and any URL
scheme is accepted.

- Split the clients: `_get_client()` keeps `base_url=api.openverse.org` and the
  bearer; `_get_download_client()` has **no** auth headers,
  `follow_redirects=True`, and a plain timeout. Add an injectable
  `download_client` parameter next to `client` for tests.
- Add `assert_public_https(url)` in `providers/_media.py`: require `https`,
  reject loopback, private, link-local and reserved addresses (parse the host;
  for a hostname, resolve with `socket.getaddrinfo` and check every address).
  Document that DNS rebinding is out of scope.
- Download through the unauthenticated client.

**#301 Freesound.** `results[0]` is trusted entirely on the server-side filter;
`_LICENSE_LABELS` knows only CC BY 4.0; the key rides in the query string.

- Replace `chosen = results[0]` with `_first_acceptable(results, max_ms)` that
  accepts only an explicit URL set — CC0 1.0, PDM 1.0, CC BY 3.0 and 4.0, both
  schemes — and checks `duration` against the request cap; otherwise try the
  next result.
- Send `Authorization: Token <key>` and drop `token` from the params.
- Extend `_LICENSE_LABELS` with the 3.0 URLs.

**Files:** `.../providers/audio/openverse_sfx.py`,
`.../providers/audio/freesound_sfx.py`, `.../providers/_media.py` (URL helper),
`tests/test_openverse_sfx.py`, `tests/test_freesound_sfx.py`.

- [x] **Step 1.** Tests with `httpx.MockTransport`:
      - the audio request carries no `Authorization` header and no `token`;
      - an `http://` audio URL is refused; an `https://127.0.0.1` URL is
        refused;
      - a 302 to a CDN is followed and succeeds;
      - Freesound with an NC result first downloads the second, CC0 result;
      - a CC BY 3.0 result gets the readable label;
      - the search request has no `token` query parameter and has the header.
- [x] **Step 2.** Implemented the URL check + second client; Freesound now
      iterates an allowlist and checks duration.
- [x] **Step 3.** Credits/manifest fields are unchanged, so `credits.txt` and
      the SFX QA path do not move.

**Acceptance:** no token leaves the API host; an NC result can never ship; all
existing provider tests still pass.

---

### P1.6 — Save the short manifest after every stage (#302)

**Problem.** `run_short_job` calls `ctx.manifest().stage_done("layerize", …)`
(line 432), `"voice"` (511) and `"qa"` (874), but only `estimate_short` calls
`ctx.store.save()`. `qa.json` is current and `manifest.json` is stale; a copied
project reports the source project's stages.

**Design**

- Call `ctx.store.save()` immediately after each `stage_done` via a
  `_stage_done(ctx, name, payload)` helper in `short_pipeline.py`.
- Always record the `qa` stage, with `{"qa": ..., "passed": bool}` — including
  when QA fails.
- Wrap the QA/report section so a failure still saves what ran.

**Files:** `packages/pipelines/src/video_editor_pipelines/short_pipeline.py`,
`tests/test_short_pipeline.py`.

- [x] **Step 1.** Tests: run the short pipeline with fake stages; assert
      `manifest.json` on disk lists `layerize`, `voice` and `qa` after the run,
      and that a failing QA still persists the stage with `passed: false`.
- [x] **Step 2.** Added the helper and called it at each boundary.
- [x] **Step 3.** Added a copied-manifest test: a stale `manifest.json` from a
      source project is replaced by the local run's stage map.

**Acceptance:** MCP `job_status` reads the current stage map; one extra save per
stage (~ms).

---

### P1.7 — Enforce the A8 error policy in CI (#304)

**Problem.** 130 broad handlers, 47 without the policy comment. Most are fine;
nothing stops the next one from swallowing a spend or output failure.

**Design**

- Add `BLE001` and `S110` to `[tool.ruff.lint].select` in `pyproject.toml`.
- Rewrite every new hit as `except Exception:  # noqa: BLE001 - <side channel>`
  (or `S110` for `except …: pass`), or narrow the exception where the handler
  is really handling a specific failure.
- If test files legitimately use broad handlers, add `tests/**` to
  `per-file-ignores` — do not annotate hundreds of tests.
- Keep the audit's counting script under `scripts/audit_broad_except.py` so the
  next pass can reproduce "47 uncommented" without rewriting the grep.

**Files:** `pyproject.toml`, ~26 source files (`asset_ingestion/manager.py`,
`skills/export`, `skills/verify`, `mcp_server/tools/*`, `engine/commands/*`),
`scripts/audit_broad_except.py`, `docs/ARCHITECTURE.md` (policy pointer).

- [x] **Step 1.** Ran `ruff check . --select BLE001,S110`; annotated 50 sites
      with a named side channel and narrowed/fixed the two that hid #309 and
      #310 (see P3.2/P3.3, landed early).
- [x] **Step 2.** `scripts/audit_broad_except.py` reports 146 broad handlers,
      14 re-raise cleanup guards, 0 without a noqa.
- [x] **Step 3.** `docs/ARCHITECTURE.md` now says the rule is enforced by
      `BLE001`/`S110`, not advisory.

**Acceptance:** CI fails on a new unannotated broad handler. The two sites that
concealed #309/#310 are fixed rather than blessed with a comment.

---

## 3. Phase 2 — structural guardrails

### P2.1 — One async/sync rule (A9)

- [x] Wrote the rule in `docs/ARCHITECTURE.md` ("Concurrency rule"): the
      orchestration core is **sync**; async edges enter through
      `asyncio.to_thread`; provider I/O stays async inside
      `AssetIngestionManager`.
- [x] Kept `run_async` deliberately as a direct-call compatibility path; the
      worker and MCP use the sync entry points in a thread. Documented in the
      "Concurrency rule".
- [x] The watchdog coverage from P1.1 is in the default suite
      (`tests/test_worker_heartbeat.py`).

### P2.2 — Trust levels for every front-end (A10)

- [x] Spike the import graph: `video_editor_state` cannot import
      `asset_ingestion` (cycle), so containment/trust lives in
      `video_editor_state/trust.py` and the budget stays with the MCP factory.
- [x] Introduced `TrustLevel` (`CLI`, `CONTROL`, `MCP`). Untrusted callers get
      root containment, filename validation and `config_path` refusal; MCP is
      read-only and the control channel cannot spend.
- [x] `video_editor_mcp.context.make_job_ctx` delegates containment to the
      shared `contained_root`.
- [x] Registry commands declare `mutates`/`spends`; the policy is enforced in
      `CommandRegistry.execute`, so `run_command`, the control server and any
      future front-end inherit it.
- [x] Tests: `work_root=/tmp`, `plan_out_name="../x"`, `config_path=…`, a
      mutating command over MCP and a spending command over control are refused
      (`tests/test_command_registry.py`).

### P2.3 — Queue/MCP parity for the production paths (#303, A11)

- [x] Added `short` and `musical` to `HANDLERS`
      (`packages/pipelines/src/video_editor_pipelines/handlers.py`). `short`
      requires an approved `plan/cost_estimate.json` and `run_short_job`
      re-runs `check_approved_spend`; the queue never approves spend.
- [x] Added a guarded MCP tool `run_short(pack_path, approve_usd)` that prices
      with `price_short`, refuses above the approval, records the estimate and
      enqueues the job (same pattern as `run_pipeline`).
- [x] Added the capability matrix to `docs/CLI_USAGE.md`: capabilities × CLI,
      worker command, MCP tool.
- [x] Partially: the matrix names `ve explain` / `ve estimate` as `cli_only`;
      `tests/test_short_queue.py` asserts the two production paths have
      handlers, payload schemas and an MCP entry point. A generic
      every-CLI-command parity test is still open.

**Acceptance:** `saju`-style pack builds can be priced, approved and queued via
MCP and polled with `job_status`; the parity test catches a missing
handler/payload for the new paths.

---

## 4. Phase 3 — LOW findings

| Task | Finding | Change | Test |
|---|---|---|---|
| P3.1 | #307 | **done:** docstrings/warning fixed; both pipelines return `qa_text`; estimate UI moved to `estimate_ui.py`; `typer` banned in `packages/pipelines` | Import-linter contract + estimate tests |
| P3.2 | #309 | `VerifySkill.load_requirements` re-raises `VerificationError("requirements.json is invalid: …")`; `run()` no longer reports "none found" | Corrupt-JSON test |
| P3.3 | #310 | Probe failure returns `{"duration": null, "error": …}`; never substitute the plan duration | Broken-render test |
| P3.4 | #308 | **done:** the SFX chain gets a zero-limit `BudgetGovernor` with the price registry, so a priced provider fails closed | Zero-budget assertion in `test_short_pipeline.py` |
| P3.5 | #306 | **done:** layerize cache under `<data>/cache/layerize`, a `.done` marker written last, stale `.<key>.*` temp dirs swept | Cache-hit across jobs; partial entry rebuilds |
| P3.6 | #305 | **done:** index downloads stream to a temp file with a per-type cap then `os.replace`; buffered provider responses are capped and written atomically | `tests/test_media_limits.py` |

Do P3.1–P3.3 first (small, no new paths); P3.4 next (spend safety); P3.5/P3.6
last (they touch download and cache plumbing).

**Landed early (2026-10-28):** P3.2 (#309) and P3.3 (#310) were fixed with P1.7
so the two `except` sites did not get a noqa that blessed the bug.

---

## 5. Phase 4 — creative roadmap (separate track)

Every item needs: a demo pack under `videos/<date>/`, a spend cap, and a pass
through the existing QA gates. The pass/fail gates stay authoritative; new
scores are advisory.

| Task | Item (audit §5.3 / gap) | Deliverable | Gate |
|---|---|---|---|
| P4.1 | 3 — look-ban | **done:** `core/look_bans.py` checks motion monotony, one font weight, centred text on a full-bleed gradient, uniform scene lengths, missing depth; `gather_composition_qa` adds a `look_bans` check, failing only under `strict_look` (`ve short --strict-look`) | `tests/test_look_bans.py` |
| P4.2 | 7 — director's brief | **done:** optional `logline`, `references[]`, `characters[]`, `beat_sheet`, `on_screen_text`, `deliverables[]`, `direction`; `brief_warnings()` is logged by preflight and never blocks | `tests/test_content_pack.py` |
| P4.3 | 1 — critique loop (`ve critique`) | **done:** contact sheet → 7-axis vision rubric → structured patches → `qa/critique.json`; `--rebuild --pack --yes` patches and re-renders for up to `--max-rounds` until every score reaches `--target`; each rebuild reuses the approved estimate | `tests/test_critique.py`, `test_llm_client.py` |
| P4.4 | 2 — `ve style-from` | **done:** measures cuts, shot lengths, cut rate, fps/aspect and palette in code; writes `style.md` + a DesignTokens draft; `--vision` adds the prose reading (grammar, not content) via the `critique` vision task | `tests/test_style_from.py` |
| P4.5 | 6 — `ve brand-capture <url>` | **partial:** parses icon, OG image, palette and `font-family` from the page HTML, downloads assets, writes a `BrandProfile`; computed CSS/screenshots still need a browser | `tests/test_brand_capture.py` |
| P4.6 | 4 — beat grid + quantization | **done:** `--quantize beat\|downbeat` measures beats with the `beat_sync` skill, snaps cuts inside `--beat-tolerance`, keeps them ordered, and writes `plan/beat_grid.md` | `tests/test_beat_grid.py` + musical quantize test |
| P4.7 | 5 — UI primitives | **done:** `wipe`, `mask_reveal`, `camera`, `type_on`, `morph` (property morph) and `cursor` (offset-path markup); `draw`/`count` already existed. True FLIP is an authoring approximation | `tests/test_motion_primitives.py`, `test_compile_composition.py` |
| P4.8 | 8 — character bible | `characters[]` reference sheets; `AssetRequest.reference_images`; wire to image providers that accept references; CLIP/face-similarity QA | Consistency threshold; spend cap per character |
| P4.9 | 9 — HTML escape hatch | **done:** `PackScene.html`, sandboxed by validation (20k chars; no script/iframe/src/href/@import/event handlers), rendered as one html shot layer | `tests/test_content_pack.py`, `test_from_content_pack.py` |

**Recommended entry order:** P4.1 + P4.2 (S, and they give the critique loop its
rubric vocabulary) → P4.3 → P4.4/P4.5 → P4.6 → P4.7 → P4.8 → P4.9.

---

## 6. Dependency and migration checklists

**Queue schema migration**
- [ ] Fresh DB: `CREATE TABLE` includes `lease_id`.
- [ ] Old DB: `ALTER` runs before the indexes that reference new columns.
- [ ] N workers starting together: the existing retry loop still handles
      `duplicate column name`.
- [ ] `requeue_stale` clears the lease; a reclaimed run cannot complete.

**MCP surface**
- [ ] Every tool that writes or spends goes through `make_job_ctx` +
      `job_budget`.
- [ ] `run_command` exposes read-only ids only.
- [ ] `list_commands` still shows the refused ones, tagged.
- [ ] Sync registry handlers never run on the loop.

**Spend**
- [ ] `assets.resolve` unreachable from MCP.
- [ ] `_add_sfx` cannot spend.
- [ ] `short`/`musical` queue handlers require a prior approval.
- [ ] `check_approved_spend` still runs at the top of `run_short_job`.

---

## 7. Risks and rollbacks

| Risk | Mitigation |
|---|---|
| `to_thread` breaks a skill that calls `asyncio.run` internally or uses thread-local state | Audit `asyncio.run` sites (`grep -rn "asyncio.run" packages apps`) before P1.1; convert the pipeline entry points to sync-first in P2.1 |
| Lease guard breaks scripts/tests calling `complete()` directly | `find_usages("complete")`; add an explicit `force=True` only for tooling, never for the worker |
| SQLite `ALTER` race on a fresh DB opened by many workers | Reuse the existing `_init_db` retry; add a 4-process startup test |
| Ruff `BLE001` flags hundreds of test sites | `per-file-ignores` for `tests/**`; keep the rule for `apps`/`packages` |
| Openverse URL validation breaks the MockTransport fixtures | Inject `download_client`; validate at the call boundary, not in `_media` |
| Creative work destabilises the render path | Each P4 item ships behind a pack flag or advisory-by-default; frozen-parallax and seek-safety gates never relax |

---

## 8. Open decisions

1. **Phase 4 entry:** the audit ranks the critique loop first by value; P4.1
   and P4.2 are the cheap harness it needs. Recommendation: ship the two small
   items first, then the loop.
2. **#299 shape:** allowlist-only is fastest, but `mutates`/`spends` metadata is
   needed by A10 anyway. Recommendation: metadata now (P1.4), allowlist
   behaviour now, policy generalisation in P2.2.
3. **A13 data dir:** change the default to `<data>/projects` or only set
   `VIDEO_EDITOR_DATA_DIR` on this machine? Recommendation: set the env now,
   revisit the default after the next audit's `du -sh`.
4. **`run_async` API:** confirm whether anything outside the repo imports it
   before renaming (it is a package-level name).

---

## 9. Finding → task map

| Finding | Task | Exit criterion |
|---|---|---|
| #298 | P1.1, P1.2, P1.3 | two-worker test: one run, no retry, lease-lost raises |
| #299 | P1.4, P2.2 | `run_command` cannot mutate/spend; loop stays responsive |
| #300 | P1.5 | no auth header on audio host; https/private-IP check |
| #301 | P1.5 | NC-first result skipped; header auth; 3.0 label |
| #302 | P1.6 | manifest on disk matches `qa.json` after pass/fail |
| #303 | P2.3 | `short`/`musical` queue + MCP |
| #304 | P1.7 | ruff fails on an unannotated broad handler |
| #305 | P3.6 | oversize download aborts, no partial file |
| #306 | P3.5 | cross-job cache hit; temp sweep |
| #307 | P3.1 | docstring fixed; no `typer` in pipelines |
| #308 | P3.4 | priced provider in the SFX chain fails closed |
| #309 | P3.2 | corrupt requirements raise |
| #310 | P3.3 | failed probe returns null + error |
| A9 | P1.1, P2.1 | one documented rule; watchdog test |
| A10 | P2.2 | one guarded `JobContext` factory |
| A11 | P2.3 | generated matrix + parity test |
| A12 | P4.7, P4.9 | new primitives + escape hatch behind gates |
| A13 | Open decision 3 | data dir out of the checkout |
