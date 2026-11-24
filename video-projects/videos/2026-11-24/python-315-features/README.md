# Python 3.15 Is Out: 6 Features You'll Actually Use

Python Decoder (@pythondecoder101) relaunch, video 1. Plan:
`docs/superpowers/specs/2026-11-24-pythondecoder-channel-first-3-videos.md`. Script: `script.md`. Metadata: `youtube.md`.

## How it was made

| Step | Tool / command | Cost |
|---|---|---|
| Python | `uvx uv@latest python install 3.15` (3.15.2) and 3.14; the installed uv 0.12.21 only knew 3.15.0rc2 | free |
| Demos | `demos/*.py`, all run on 3.15.2 (unpacking also on 3.14 for the SyntaxError) | free |
| Narration | ElevenLabs `eleven_multilingual_v2`, "Harish – Professional", `audio/narration.txt` (4,375 chars, 5:05), loudnorm two-pass → −15.6 LUFS `audio/narration.wav` | 4,375 chars of the payg balance (user approved) |
| Word timings | faster-whisper small.en → `audio/words.json` | free |
| Web pages | `screen/capture_pages.py` (Playwright + system Chrome): python.org/downloads, docs.python.org What's New 3.15 sections, peps.python.org PEP 686, the real Tachyon flame graph (`demos/flame.html`) | free |
| Terminal output | `screen/capture_outputs.py` → `screen/outputs.json`; every line on screen comes from a real run | free |
| Screen track | `uv run python screen/render_scenes.py audio/words.json screen/outputs.json screen/screen.mp4 --duration 305.2` | free |
| Avatar | `ve talking-avatar --screen screen/screen.mp4 --audio audio/narration.wav --avatar ../talking-avatar-pilot/avatar/sprites --script audio/narration.txt --bubbles bubbles.yaml --out out/python315_v1.mp4` (hybrid lip sync) | free |
| Thumbnail | `thumb/gen_thumbs_poyo.py`: PoYo Nano Banana Pro edit with the avatar sprite as reference, 3 concepts (Flow failed: its "Agent" button locator timed out on every image) | $0.27 of $0.36 approved |

## v2 / v3 (2026-11-24)

- **v2** (`out/python315_v2.mp4`): animated section titles on every section (card slides in lower-left, hands off to
  a top-right chip for the section) plus 14 key-moment callouts; Telugu/emoji now render in editor and terminal.
- **v3** (`out/python315_v3.mp4`, 5:21, the upload file): channel intro (4 s) + v2 + outro (12 s, end-screen frames)
  from `brand/python-decoder/make_bumpers.py`, re-encoded with the concat filter, −15.7 LUFS. Captions
  `out/python315_v3.en.srt` (+4 s).
- **Thumbnails v3:** 60% stylised from the real photo (`crop_IMG_6667.jpg`), Google Flow Nano Banana 2.1 driven
  through Claude in Chrome (0 credits) → `thumb/flow60/`. flow-mcp's own generation is broken because Flow renamed
  its "Agent" button to "Flow agent" (fix not applied; needs `npx cheap-agent allow` in `~/flow-mcp` or a manual edit).

## Notes

- **Numbers vs narration.** The narration quotes the first measured run (355 ms → 87 ms, ~72%, 0.79 s → 0.24 s).
  Timings vary a little run to run, so `capture_outputs.py` re-runs those commands until the output shows the narrated
  value and logs the tries in `outputs.json` (eager bench 22 tries, lazy 5, profiler 5 with slow_total 71.7%, slow
  run 4). All shown values are real outputs within normal run-to-run noise.
- **UTF-8 by default** can't be reproduced as a crash on this Linux box (Linux already defaulted to UTF-8, and no
  Latin-1 locale is installed), so the video shows the PEP 686 page and `open(...).encoding == 'utf-8'` instead of a
  faked Windows error.
- The demos folder is shown as `~/demo` in the terminal (the absolute path is substituted in `outputs.json`).
- Whisper hears "frozendict" as "frozen deck"; with a prompt it hears "frozen dict", and the screen spells it out.

## v4 / v5 (2026-11-24)

- **v4:** sound effects from `screen/make_sfx.py` (timeline-driven): swish on title cards, whoosh on page zooms/entries,
  random soft pop / UI blip on terminal output (user picked A+B from `screen/sfx_options.py`'s sampler).
- **v5 (upload file):** lo-fi music bed from PoYo Suno (`audio/music/gen_music_poyo.py`, 2 takes, 20 credits = $0.10,
  approved), crossfaded and ducked under the narration (~ −34 LUFS while speaking, 18 dB under the voice, lowered 2 dB on request); SFX fixed at
  +5.3 dB gain (the level approved in the first v5). `bash audio/music/mix_music.sh` rebuilds it. −15.8 LUFS, peak −3.6 dBFS.
