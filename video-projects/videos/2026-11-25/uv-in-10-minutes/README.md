# Stop Using pip: uv in 10 Minutes (2026 Setup)

Python Decoder (@pythondecoder101), video 2. Plan:
`docs/superpowers/specs/2026-11-24-pythondecoder-channel-first-3-videos.md`. Script: `script.md`. Template: EP1
(`videos/2026-11-24/python-315-features/`).

## Verified demos (2026-11-25, uv 0.13.9 in a scratch venv, Python 3.15.2)

| Demo | Real result |
|---|---|
| Race: `python3 -m venv` + `pip install --no-cache-dir -r demos/requirements.txt` (pip 24.0, Python 3.12) | 20.3 s, 17.5 s, 19.1 s |
| Same with `uv venv` + `uv pip install -r` and an empty cache | 4.9 s, 5.8 s, 5.4 s (≈ 3.8× faster) |
| uv again with its cache warm (fresh venv) | 0.076 s, 0.050 s, 0.051 s |
| pip with its cache warm (fair check, not in the narration) | 16.4 s, 13.4 s |
| `uv python install 3.13` into an empty install dir | 3.13.18 downloaded and installed in 2.9 s |
| `uv init weather` | **src layout + build system** (`src/weather/__init__.py`, `[project.scripts]`, `uv_build`); default since uv 0.12 per docs.astral.sh "Creating projects". `--no-package` gives the old `main.py` |
| `uv add requests rich` | Created `.venv` with CPython 3.15.2, resolved 10 packages in 468 ms, `uv.lock` |
| `uv run weather` (`demos/weather_init.py` as the package) | Rich table with live open-meteo temperatures (Hyderabad 23.8, London 5.3, New York 9.7) |
| `rm -rf .venv && uv sync` | 10 packages installed in 12 ms from the lock |
| `uv add --script hello_rich.py rich` + `uv run` | PEP 723 block written; runs with no project venv |
| `uv tool install ruff` | ruff 0.17.6 |
| `uvx ruff check demos/messy.py` | F401 `os` imported but unused, 1 fixable |
| `uv add -r requirements.txt` | 6 deps moved into `pyproject.toml` |

## Capture gotchas

- `uv init` fills `authors` from git config (it showed the owner's email). Capture with `--author-from none`.
- Use scratch `UV_TOOL_DIR`, `UV_TOOL_BIN_DIR`, `UV_PYTHON_INSTALL_DIR` and `UV_CACHE_DIR` so on-screen installs are
  real downloads and nothing on the machine changes; show paths as `~/demo`.
- The race must time uv as a fixed binary, not `uvx uv@latest` (the first try counted uv downloading itself: 19.8 s).

## Narration

`audio/narration.txt`: 4,050 characters → ElevenLabs `eleven_multilingual_v2`, "Harish – Professional" (`mp3_44100_128`; 192 kbps is refused on payg), 4:54. Two-pass loudnorm could not reach −15.5 under the −3.5 dBFS peak cap, so `narration.wav` = compressor (−24 dB, 3:1) + 12.8 dB + limiter (level=0) → −15.9 LUFS, peak −3.4 dBFS. `words.json` from faster-whisper small.en (718 words).

## Screen track (2026-11-25)

| Step | Command | Notes |
|---|---|---|
| Web pages | `uv run --with playwright python screen/capture_pages.py screen/web` | docs.astral.sh/uv (home, installation incl. the Windows tab, projects/init), peps.python.org PEP 723 |
| Terminal output | `python3 screen/capture_outputs.py <dir with uv 0.13.9>` → `screen/outputs.json` | Scratch work dir shown as `~/demo`; stdout+stderr captured as one stream (terminal order). Race: pip 19.16 s (try 8), uv cold 4.69 s (try 6), uv warm 0.049 s (try 3); Python 3.13.18 in 3.01 s |
| Screen track | `.venv/bin/python screen/render_scenes.py audio/words.json screen/outputs.json screen/screen.mp4 --duration 292.7` | EP1's renderer plus a side-by-side `Race` scene, a drawn VS Code `Palette` (the interpreters listed are the real ones on this machine) and a `Recap` cheat sheet; `--still T ...` for checks |

**`uv sync` timing.** The narration first said "twelve milliseconds" (a real run in testing), but 200 capture runs all
showed 8 ms. Instead of paying to regenerate, "in twelve milliseconds" was cut from `audio/narration.wav` (175.22 →
176.62 s, checked with Whisper; uncut copy `audio/narration_uncut.wav`) and `words.json` shifted by −1.40 s. The
screen shows the real 8 ms. Narration is now 4:52.7.

## Final cut (2026-11-25)

- Avatar: `ve talking-avatar … --bubbles bubbles.yaml --out out/uv_v1.mp4` (hybrid lip sync, 7 bubbles, ~4 min render).
- Bumpers: `brand/python-decoder/make_bumpers.py out --episode "EP 2 · uv" --next "Up next: MCP server in Python"`.
- SFX: `screen/make_sfx.py audio/words.json screen/outputs.json out/sfx --offset 4.0 --duration 308.73` (35 events;
  race results and the interpreter pick also get a pop/blip).
- `bash mix.sh`: intro + main + outro → `out/uv_v2.mp4`, then EP1's lo-fi bed (reused, no spend) at −11 dB ducked under
  the voice and SFX at +5.3 dB → `out/uv_v3.mp4` (5:08.7, −16.1 LUFS, peak −4.7 dBFS). Upload copy at CRF 3 (CRF 12 gave only 33.7 MB, under the 50 MiB handover limit):
  `out/uv_v3_upload.mp4`.
- Captions: `python3 make_srt.py --offset 4.0` → `out/uv.en.srt` (109 cues, Whisper mishearings fixed).
- Thumbnails: Google Flow (Nano Banana 2.1, 0 credits) via Claude in Chrome with `harish_photo_ref.jpg` as the
  ingredient, downloaded through flow-mcp's Chrome (CDP 9333) → `thumb/flow/uv_0[1-6]*`. Typing via Claude in Chrome
  needs the prompt editor's ref (`textbox "What do you want to create?"`): an earlier ref pointed at the project-title
  input and the prompt landed in the title (restored before saving).
