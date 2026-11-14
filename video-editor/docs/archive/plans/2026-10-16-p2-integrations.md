# P2 Integrations — Implementation Plan (MCP SDK · OpenTimelineIO · FFmpeg 8.1)

> **For agentic workers:** steps use checkbox (`- [ ]`). Execute milestone by milestone; keep `uv run pytest -m "not integration and not slow"`, `uv run ruff check .`, and `uv run mypy apps packages` green after every milestone.

**Goal:** Land the three P2 items chosen from `docs/archive/audits/AUDIT_2026-10-15.md` §7 / the P1 plan's M8: modernize the MCP server onto the current SDK, add OpenTimelineIO interchange to the FableCut/preview bridge, and adopt FFmpeg 8.1.

**Companion:** `docs/archive/audits/AUDIT_2026-10-15.md`, `docs/superpowers/plans/2026-10-15-render-graph-rewrite.md`.

## Global Constraints

- No behavior regressions: the full non-slow suite stays green; `ruff` + `mypy apps packages` clean.
- Keep the existing MCP tool schemas/names and the `_on_list_tools` / `_on_call_tool` entry points (tests depend on them).
- Keep the FableCut bridge's existing JSON project format working; OTIO is an added interchange path.
- FFmpeg adoption must not silently change encoder defaults; verify with `ffprobe`/`ffmpeg -version`.

---

## M9 — MCP SDK update (mcp 2.0 `MCPServer`)

**Files:** modify `packages/mcp_server/src/video_editor_mcp/server.py`, `packages/mcp_server/pyproject.toml`; tests `tests/test_mcp_server.py`.

The repo's server already runs on mcp 2.0's **low-level** `Server`. Modernize onto the high-level `mcp.server.mcpserver.MCPServer` while preserving the existing tool metadata.

- [ ] **Step 1:** Factor the routing `if/elif` into `_dispatch_tool(name, arguments) -> CallToolResult`; keep `_on_list_tools` / `_on_call_tool` as thin compatibility wrappers.
- [ ] **Step 2:** `_signature_from_schema(tool)` → `inspect.Signature` mapping JSON types (string→str, number→float, integer→int, boolean→bool, array→list, untyped→Any; required→no default).
- [ ] **Step 3:** `create_server() -> MCPServer` registering one async wrapper per tool (dynamic `__signature__`), returning the tool's text; `main_async()` runs `server.run_stdio_async()`.
- [ ] **Step 4:** bump the `mcp` pin; tests: `create_server()` returns an `MCPServer` named `video-editor`; tool count/schema preserved.

## M10 — OpenTimelineIO interchange

**Files:** add `opentimelineio` dep; create `packages/fablecut_bridge/src/video_editor_fablecut_bridge/otio.py`; wire into the FableCut exporter/importer and/or `ve preview`; tests.

- [x] **Step 1:** `to_otio(project) -> opentimelineio.Timeline` and `from_otio(timeline) -> project model` mapping the bridge's project model (clips/tracks/markers) to OTIO `Timeline`/`Track`/`Clip` + back.
- [x] **Step 2:** `export_otio(project_dir)` / `import_otio(path)` helpers writing/reading `.otio` JSON.
- [x] **Step 3:** CLI/docs: `export_to_fablecut` now also writes `project.otio` next to `project.json`.
- [x] **Step 4:** tests: round-trip a small project (clips preserved; markers preserved); invalid file → clear error.

## M11 — FFmpeg 8.1 static build

**Files:** `scripts/install_ffmpeg.sh` (new), docs; validate argv assumptions.

- [x] **Step 1:** Script to fetch a static FFmpeg 8.1 build (`scripts/install_ffmpeg.sh` → `ffmpeg-n8.1-latest-linux64-gpl-8.1` into `tools/ffmpeg/`, symlinked into `~/.local/bin`); verified `ffmpeg 8.1.3`.
- [x] **Step 2:** Detect/route: installed onto PATH (no code change); `tools/ffmpeg/` gitignored; `FFMPEG_PREFIX`/`FFMPEG_BIN_DIR` overridable.
- [x] **Step 3:** Validated: FFmpeg-sensitive suite + a real single-pass render pass on 8.1; NVENC/AV1/Vulkan encoders present, `-hwaccel` still valid.
- [x] **Step 4:** docs: README requirements updated (8.1 recommended). Capability gains: `h264_nvenc`/`hevc_nvenc`/`av1_nvenc`/`av1_vulkan`. *(The `whisper` filter is not in this GPL build.)*

---

## Out of scope (deferred P2)

PySceneDetect, DeepFilterNet3, RIFE/ComfyUI, pyannote — not selected for this pass.
