# Build Your First MCP Server in Python (FastMCP Tutorial)

Python Decoder (@pythondecoder101), video 3. Plan: `docs/superpowers/specs/2026-11-24-pythondecoder-channel-first-3-videos.md`.
Script: `script.md`. Template: EP2 (`videos/2026-11-25/uv-in-10-minutes/`).

## Verified (2026-11-25)

| Check | Real result |
|---|---|
| Versions | fastmcp 4.2.6 (released 2026-11-25), mcp SDK 2.3.0, uv 0.13.9, Python 3.15.2, Claude Code 2.1.325 |
| `uv add --script server.py fastmcp` | PEP 723 block `fastmcp>=4.2.6`; `uv run server.py` starts the stdio server on Python 3.15.2 |
| `fastmcp list demos/server.py` | 2 tools: `search_notes(query: str, limit: int = 5)`, `convert_units(value: float, unit: str)` |
| `fastmcp call … search_notes query=goa` | `trip-plan.md`: "# Trip plan: Hyderabad to Goa" |
| `fastmcp call … convert_units value=650 unit=km` | "650.0 km = 403.9 mi" |
| FastMCP client over `uv run server.py` | tools listed; `convert_units(2, kg)` → "2.0 kg = 4.4 lb" |
| `fastmcp install mcp-json server.py` | config JSON (`uv run --with fastmcp fastmcp run …/server.py`) |
| Claude Code, one-off (`claude -p … --mcp-config mcp.json --strict-mcp-config`, nothing written to the user's configs) | Calls: `search_notes("Goa")` → `search_notes("km")` → `convert_units(650, "km")`; answer "about 650 km … roughly 404 miles (403.9 mi)". Cost of that run: $0.53 of Claude usage |
| `read_note_unsafe.py` with `name=../.env.demo` | returns the fake key `OPENAI_API_KEY=sk-demo-NOT-A-REAL-KEY` |
| `read_note_safe.py` same call | `ToolError: Only .md files inside the notes folder can be read.`; `groceries.md` still works |
| `fastmcp inspect server.py --format mcp` | real `inputSchema` used in the "what the AI sees" diagram |

## Explainer diagrams (owner request, 2026-11-25)

Dark-screen animated flowcharts like ByteMonk's "The GPU Architecture Every AI Engineer Needs to Understand"
(youtu.be/ge5gfniFEnk): near-black purple background, neon outline cards with icons, connectors that draw on, glowing
packets along them, one idea per screen. `screen/diagram.py` (`Diagram`, `Node`, `Edge`, `Caption`); icons are Lucide
(ISC licence, `lucide-static` 1.79.0) rasterized by `screen/icons/render_icons.py`. Planned diagrams: S1 AI app → MCP →
server → tools; S3 function → schema the AI sees; S6 the three real tool calls; S7 hidden instruction → leak → path
check blocks it.

## Narration

`audio/narration.txt`: 3254 characters (not generated yet).

## Build (2026-11-25)

- Narration: 3,254 characters → ElevenLabs "Harish – Professional" (`mp3_44100_128`), 3:52; compressor + 12.8 dB +
  limiter 0.54 → −16.1 LUFS, peak −3.5 dBFS; `words.json` faster-whisper small.en (583 words).
- Pages: `screen/capture_pages.py screen/web` (modelcontextprotocol.io intro, gofastmcp.com welcome + tools).
- Outputs: `python3 screen/capture_outputs.py <uv 0.13.9 bin> screen/claude_run.jsonl` → `screen/outputs.json`.
  **Gotcha:** FastMCP 4 moved the `fastmcp` command into `fastmcp-slim`; `uv tool install fastmcp` fails, so the capture
  installs `fastmcp-slim==4.2.6 --with fastmcp==4.2.6`. The Claude step is formatted from the real stream-json log.
- Timeline: `screen/render_scenes.py` (EP2's renderer; `build()` is EP3's) with 5 `Diagram` explainers (D1 MCP
  connection, shown twice; D2 function → schema; D3 the real tool calls; D4 attack → path check; D5 security rules).
- Everything else: `bash render_all.sh` (screen → `ve talking-avatar` → bumpers → SFX → `mix.sh`), captions
  `python3 make_srt.py --offset 4.0` → `out/mcp.en.srt` (90 cues).
- Thumbnails: Google Flow via Claude in Chrome, photo attached from its tile menu (⋮ → Add to prompt; it no longer shows
  in the ingredient picker's Recent/Uploads). **Gotcha:** a prompt containing "@" (e.g. `@mcp.tool`) silently doesn't
  submit; reworded. Downloaded via flow-mcp's Chrome (CDP 9333) → `thumb/flow/mcp_0[1-6]*`.

## Use case: talk to your shop database (owner request, 2026-11-25)

Added after S7 ("another 3 min after the basics"); the main cut grew from 3:52 to 6:28.

| Check | Real result |
|---|---|
| `demos/shop/make_shop_db.py` (fixed seed, fictional) | `shop.db`: 8 products, 12 customers, 360 orders (Sep–Nov 2026); the `.db` is rebuilt, not committed |
| `demos/db_server.py` | 2 tools: `list_tables()`, `run_query(sql)` (≤ 50 rows); lock 1 = SELECT/WITH only, lock 2 = `mode=ro` connection |
| `run_query sql="DELETE …"` | `Only SELECT queries are allowed.` |
| `run_query sql="WITH x AS (SELECT 1) DELETE …"` | passes lock 1; `SQL error: attempt to write a readonly database`; still 360 orders |
| Claude Code one-off run (`screen/claude_db_run.jsonl`, trimmed) | `list_tables` + 5 queries it wrote itself; October top product Cold Brew Kit ₹19,000 / 20 units, best customer Arjun Patel (Pune) ₹18,899; Masala Chai most units (60), 5th on revenue (₹10,800); refused to delete without a go-ahead. ~$0.38 of Claude usage |

- Narration: `audio/usecase.txt`, 2,298 ElevenLabs characters, generated separately (same voice/settings, −15.9 LUFS)
  and spliced into `narration.wav` at 208.20 s with 0.5 s / 0.4 s pauses; the original is kept as
  `narration_main.wav` / `words_main.json` / `narration_main.txt`. Recap words shifted +156.33 s. Whisper small.en heard
  "two logs"; medium.en and a listen confirm "locks", fixed in `words.json`.
- Captures: `python3 screen/capture_outputs.py <uv bin> screen/claude_run.jsonl screen/claude_db_run.jsonl --db` merges
  the `db_*` steps and `claude_db` into `outputs.json` without re-running the other steps.
- Scenes U0–U5 in `render_scenes.py`: database intro diagram, `db_server.py` with the two locks highlighted, "two locks"
  diagram, question-flow diagram, the real Claude terminal (split per cue by `split_claude_db`), the real SQL card,
  the delete tests, and two pattern diagrams. New Lucide icons: database, table-2, receipt, ticket, scroll-text, user,
  wand-sparkles. Terminals got `term_top` so the section chip and callouts sit above the text.
- Bed: the two EP1 takes cover ~347 s, so `mix.sh` brings take 1 back after take 2 (no new music spend).
