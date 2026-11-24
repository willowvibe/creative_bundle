---
name: tech-tutorial-video
description: >
  Use when making a tech/coding tutorial video for the Python Decoder channel (or any screen-recording explainer
  with the user's talking cartoon avatar): verifying demos, real web-page captures, editor/terminal screen track,
  `ve talking-avatar`, section titles, SFX, music bed, channel intro/outro, thumbnail, and YouTube upload.
---

# Tech tutorial video

Full recipe and levels: `docs/TECH_TUTORIALS.md`. Template project to copy:
`videos/2026-11-24/python-315-features/` (scripts in `screen/`, `audio/music/`, `thumb/`; README explains each run).
Channel bumpers: `../video-projects/brand/python-decoder/make_bumpers.py`.

## Rules the user set

- **Real over generated.** Every demo runs on the real release before scripting; terminal output comes only from
  `capture_outputs.py`; web scenes are real page captures. Numbers on screen must match the narration
  (re-run noisy timings until they show the narrated value; log the tries).
- **English only** for the avatar (hybrid lip sync); no head bounce.
- **Text on every section:** animated section card ("FEATURE n · name") that hands off to a top-right chip, plus
  callouts on key moments. Bubbles about once a minute.
- **No burned-in subtitles.** Upload the English SRT to YouTube as a caption track.
- **Sound:** swish on title cards, whoosh on zooms/page entries, random soft pop / UI blip on terminal output (not a
  bell). Lo-fi bed ducked about 18 dB under the voice; keep SFX gain fixed when the bed changes.
- **Thumbnail:** the real photo (`/mnt/data1/Personal Docs/Harish-photo/crop_IMG_6667.jpg`) at about **60%
  stylised**; Google Flow (0 credits) through Claude in Chrome first, PoYo only with a quoted price.
- **YouTube:** AI disclosure **No** for this channel; upload private first, then schedule; category Education.
- Quote any paid step (ElevenLabs characters, PoYo credits) and get a yes before spending.

## Checklist

1. Plan topic in `docs/superpowers/specs/` (video-projects); verify demos; write `script.md` + `narration.txt`.
2. Quote and generate narration; loudnorm; `words.json`.
3. `capture_pages.py`, `capture_outputs.py`; adapt `render_scenes.py` scenes/cues; render stills at each section
   and look at them before the full render.
4. `ve talking-avatar` (detached render ≈ 10–30 min for 5 min); check frames per section.
5. `make_bumpers.py`, `make_sfx.py`, `mix_music.sh`; concat intro + main + outro; captions/chapters +intro length.
6. Thumbnail concepts (3), user picks; `publish.yaml` (`synthetic_media: false`), `youtube.md` checklist.
7. `social publish` private → Studio (Claude in Chrome on the owner's account): tags, playlist, captions (publish
   the manual draft), schedule. Record the video id in `youtube.md`.
