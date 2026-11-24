# Tech tutorial videos (screen track + talking avatar)

Recipe behind Python Decoder (@pythondecoder101) EP1, "Python 3.15 Is Out" (2026-11-24). The project folder
`videos/2026-11-24/python-315-features/` (in `../video-projects`) holds every script below; copy it for the next video.
Skill: `tech-tutorial-video`.

## Pipeline

| Step | Tool | Notes |
|---|---|---|
| 1. Verify every demo | the real interpreter/tool (`uvx uv@latest python install 3.15`) | Show only what runs on the release; `uvx uv@latest` when the installed uv is stale |
| 2. Script | `script.md` with `[S#]` scenes | Spell numbers for TTS in `audio/narration.txt` ("three fifteen"); quote the TTS character count before generating |
| 3. Narration | ElevenLabs `eleven_multilingual_v2`, voice "Harish – Professional" | Two-pass loudnorm to about −15.5 LUFS; faster-whisper `small.en` word times → `audio/words.json` |
| 4. Real web pages | `screen/capture_pages.py` (Playwright, `channel="chrome"`) | Long pages: tall-viewport slices per anchor (full-page screenshots time out); local HTML (flame graph) too |
| 5. Real terminal output | `screen/capture_outputs.py` → `outputs.json` | Never type output by hand. Timings vary run to run: re-run until the narrated value appears and log the tries |
| 6. Screen track | `screen/render_scenes.py` (Pillow, frame = f(time)) | Web scenes ease scroll/zoom over captures; editor/terminal scenes type the real demo files on cue words; animated section cards → top-right chip; key-moment callouts; Telugu/emoji via per-font text runs (raqm shaping) |
| 7. Avatar | `ve talking-avatar --script … --bubbles bubbles.yaml` | Hybrid lip sync by default (needs `.venv-lipsync`); bubbles ≈ 1/min on key or fun points |
| 8. Bumpers | `../video-projects/brand/python-decoder/make_bumpers.py OUT --episode … --next …` | 4 s "decode" intro, 12 s end-screen outro, synthesized sound |
| 9. SFX | `screen/make_sfx.py` (timeline-driven, synthesized) | Swish on title cards, whoosh on page zooms/entries, random soft pop / UI blip on terminal output; `screen/sfx_options.py` makes a labelled sampler for picking sounds |
| 10. Music | PoYo Suno `generate-music` (~$0.10, two takes) | `audio/music/mix_music.sh`: crossfade, ducked under the voice by sidechain (≈ −34 LUFS while speaking), SFX gain fixed (+5.3 dB) |
| 11. Join | concat filter: intro + main + outro | Shift captions/chapters by the intro length (+4 s) |
| 12. Thumbnail | Google Flow Nano Banana (0 credits) via Claude in Chrome, real photo as ingredient | User's style: **60% stylised** (semi-animated, still recognisable); PoYo Nano Banana Pro ($0.09/2K) as the paid fallback |
| 13. Upload | `social publish publish.yaml --yes` (browser lane), then Studio via Claude in Chrome | See "Publishing gotchas" |

## Levels that the user approved

- Voice −15.7 LUFS integrated, peaks ≤ −3.5 dBFS.
- Music bed ducked ≈ −34 LUFS under speech (18 dB below the voice), rising in pauses.
- SFX gain +5.3 dB on the synthesized kit (≈ 5 dB above the bed); whoosh 0.55 s, swish 0.28 s, pop/blip < 0.15 s.
- No burned-in subtitles: English SRT is uploaded to YouTube as a caption track only.

## Publishing gotchas (2026-11-24)

- YouTube Data API: the OAuth app is in Testing mode → "access denied" for the channel's account. Uploads use the
  browser lane; captions, tags, playlists and scheduling were done in Studio through Claude in Chrome.
- New channels show "Create post" on the dashboard, so the driver's `Create` locator is ambiguous: start Studio on
  the Content page (`/videos/upload`).
- Files under 50 MiB go through Playwright `setInputFiles` and time out over CDP; re-encode above 52.4 MB (CRF 12) or
  fix `browser/lib/files.mjs` in social-engine.
- The driver's visibility checks fail when its Chrome window is behind other windows (Chrome stops painting).
- Claude in Chrome can only upload files from the session's working folder and under 10 MB (captions OK, videos not).
- "Upload manual" subtitles land as a **draft** (Languages → language row → Manual subtitles ⋮ → Publish). "Edit
  subtitles" opens YouTube's own auto transcript — don't publish that one.
- AI disclosure for this channel: **No** (own voice clone + real recordings + cartoon avatar).
- Schedule from Visibility → Schedule (time zone defaults to GMT+05:30); until the channel has audience data, aim at
  the Indian evening and US morning (EP1: Sat 18:30 IST).

## Open items

- `#372`: the screen-track renderer, SFX and music mix are per-project scripts; port them into the engine as a
  `ve tutorial` command once a second video reuses them.
- `#373`: flow-mcp generation is broken by Flow renaming its "Agent" button to "Flow agent" (`~/flow-mcp/src/flow.ts`).
