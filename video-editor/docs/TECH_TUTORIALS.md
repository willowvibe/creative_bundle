# Tech tutorial videos (screen track + talking avatar)

Recipe behind Python Decoder (@pythondecoder101) EP1 "Python 3.15 Is Out" (2026-11-24), EP2 "uv" and EP3 "MCP server
in Python" (2026-11-25). Copy the newest project for the next video: `videos/2026-11-25/mcp-server-python/` (in
`../video-projects`) has every script below plus the explainer-diagram engine, a spliced use-case section and a
one-shot `render_all.sh` (screen → avatar → bumpers → SFX → `mix.sh`). Skill: `tech-tutorial-video`.

## Pipeline

| Step | Tool | Notes |
|---|---|---|
| 1. Verify every demo | the real interpreter/tool (`uvx uv@latest python install 3.15`) | Show only what runs on the release; `uvx uv@latest` when the installed uv is stale |
| 2. Script | `script.md` with `[S#]` scenes | Spell numbers for TTS in `audio/narration.txt` ("three fifteen"); quote the TTS character count before generating. After the basics, plan a **~3 min real use case** (owner, EP3) before the recap |
| 3. Narration | ElevenLabs `eleven_multilingual_v2`, voice "Harish – Professional", `mp3_44100_128` (192 kbps → 403 on pay-as-you-go) | Level with `acompressor=threshold=-24dB:ratio=3:attack=5:release=80,volume=12.8dB,alimiter=limit=0.54:level=0` → ≈ −16 LUFS, peak ≈ −3.5 dBFS (two-pass loudnorm undershoots). faster-whisper `small.en` with an `initial_prompt` of the jargon → `audio/words.json`; check doubtful words with `medium.en` (small heard "logs" for "locks") |
| 4. Real web pages | `screen/capture_pages.py` (Playwright, `channel="chrome"`) | Long pages: tall-viewport slices per anchor (full-page screenshots time out); local HTML (flame graph) too |
| 5. Real terminal output | `screen/capture_outputs.py` → `outputs.json` | Never type output by hand. Timings vary run to run: re-run until the narrated value appears and log the tries. Run in a scratch copy, `stderr=STDOUT` (keeps the order), `GIT_CONFIG_GLOBAL=/dev/null` (no real git author on screen); trim home paths and session ids from Claude Code logs |
| 6. Screen track | `screen/render_scenes.py` (Pillow, frame = f(time)) | Web scenes ease scroll/zoom over captures; editor/terminal scenes type the real demo files on cue words; animated section cards → top-right chip; key-moment callouts; Telugu/emoji via per-font text runs (raqm shaping). Explainer diagrams: `screen/diagram.py` (see below). `Cues.at()` is a forward cursor: ask for cues in spoken order (callouts use a second `Cues`) |
| 7. Avatar | `ve talking-avatar --script … --bubbles bubbles.yaml` | Hybrid lip sync by default (needs `.venv-lipsync`); bubbles ≈ 1/min on key or fun points |
| 8. Bumpers | `../video-projects/brand/python-decoder/make_bumpers.py OUT --episode … --next … --question …` | 4 s "decode" intro, 12 s end-screen outro, synthesized sound. Pass the episode's own `--question` (and keep it in `render_all.sh`), or the outro falls back to EP1's |
| 9. SFX | `screen/make_sfx.py` (timeline-driven, synthesized) | Swish on title cards, whoosh on page zooms/entries, random soft pop / UI blip on terminal output; `screen/sfx_options.py` makes a labelled sampler for picking sounds |
| 10. Music | **Reuse EP1's two PoYo Suno takes** (`videos/2026-11-24/python-315-features/audio/music/bed_1.mp3`, `bed_2.mp3`) for every channel episode (owner, 2026-11-25); no new music spend | EP3 `mix.sh`: crossfade, −11 dB, ducked under the voice by sidechain (≈ −34 LUFS while speaking), SFX gain fixed (+5.3 dB). The takes cover ~347 s; for longer cuts take 1 comes back after take 2 |
| 11. Join | concat filter: intro + main + outro | Shift captions/chapters by the intro length (+4 s) |
| 12. Thumbnail | Google Flow Nano Banana (0 credits) via Claude in Chrome, real photo as ingredient | User's style: **60% stylised** (semi-animated, still recognisable); PoYo Nano Banana Pro ($0.09/2K) as the paid fallback |
| 13. Upload | `social publish publish.yaml --yes` (browser lane), then Studio via Claude in Chrome | See "Publishing gotchas" |

## Explainer diagrams (ByteMonk style, owner request for EP3)

`screen/diagram.py` in EP3: near-black purple screens, neon outline cards with Lucide icons (ISC, `lucide-static`
1.79.0 SVGs in `screen/icons/svg/`, rasterized by `render_icons.py`), connectors that draw on, glowing packets with
labels, state changes (`flash`, colour, `blocked` cross), code cards with highlighted lines, captions. One idea per
screen, between real screens. Layout rules learned:

- The avatar covers the bottom-right corner (≈ x > 1560, y > 780 at 1080p): no cards or numbers there.
- The section chip (y 124–172) and callouts (y 190–246) sit top-right: give full-width terminals `term_top=150` so
  their rows start below them.
- Render stills at each scene (`render_scenes.py … --still T …`) and look before the full render; check final frames
  again after the avatar pass.

## Adding a section to a finished video (EP3 use case)

Generate only the new narration (same voice and leveling), splice it into `narration.wav` at a pause with ffmpeg
`atrim`/`concat` (0.4–0.5 s pauses either side), keep the original as `narration_main.*`, transcribe the new part,
offset its words and shift every later word by the inserted length. Shift later `bubbles.yaml` times too; captures can
be merged into `outputs.json` without re-running the old steps (`capture_outputs.py --db`).

## Levels that the user approved

- Voice −15.7 to −16.2 LUFS integrated, peaks ≤ −3.5 dBFS.
- Music bed ducked ≈ −34 LUFS under speech (18 dB below the voice), rising in pauses.
- SFX gain +5.3 dB on the synthesized kit (≈ 5 dB above the bed); whoosh 0.55 s, swish 0.28 s, pop/blip < 0.15 s.
- No burned-in subtitles: English SRT is uploaded to YouTube as a caption track only.

## Publishing gotchas (2026-11-24)

- YouTube Data API: the OAuth app is in Testing mode → "access denied" for the channel's account. Uploads use the
  browser lane; captions, tags, playlists and scheduling were done in Studio through Claude in Chrome.
- New channels show "Create post" on the dashboard, so the driver's `Create` locator is ambiguous: start Studio on
  the Content page (`/videos/upload`).
- Files under 50 MiB go through Playwright `setInputFiles` and time out over CDP; re-encode above 52.4 MB or fix
  `browser/lib/files.mjs` in social-engine. Screen recordings compress very small: CRF 12 was enough for EP1, EP2/EP3
  needed CRF 3 (`*_upload.mp4`).
- The driver's visibility checks fail when its Chrome window is behind other windows (Chrome stops painting).
- Claude in Chrome can only upload files from the session's working folder and under 10 MB (captions OK, videos not).
- "Upload manual" subtitles land as a **draft** (Languages → language row → Manual subtitles ⋮ → Publish). "Edit
  subtitles" opens YouTube's own auto transcript — don't publish that one.
- AI disclosure for this channel: **No** (own voice clone + real recordings + cartoon avatar).
- Schedule from Visibility → Schedule (time zone defaults to GMT+05:30); until the channel has audience data, aim at
  the Indian evening and US morning (EP1: Sat 18:30 IST).

## Open items

- `#372`: the screen-track renderer, diagram engine, SFX and music mix are still per-project scripts (copied EP1 → EP2
  → EP3); three videos now reuse them, so port them into the engine as a `ve tutorial` command.
- Thumbnails in Flow through Claude in Chrome: a prompt containing "@" (e.g. `@mcp.tool`) silently doesn't submit;
  the uploaded photo may be missing from the ingredient picker, so add it from its tile menu (⋮ → Add to prompt).
- `#373`: flow-mcp generation is broken by Flow renaming its "Agent" button to "Flow agent" (`~/flow-mcp/src/flow.ts`).
