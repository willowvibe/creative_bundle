# qa Skill

## Flash frames

`no_flash_frames` decodes the video at 160 px wide through `signalstats` and
reads mean luma per frame. A flash is a large luma jump undone by an opposite
jump within two frames, with the frames either side agreeing; a hard cut makes
one jump and never returns, so it does not match. Both 1-frame and 2-frame
flashes are reported as one event.

It is advisory by default (`max_flashes=None`), because strobes and camera
flashes are real footage. Explainers and drama pass `max_flashes=0`.

Do not build this on FFmpeg's `scene_score`: it scores the transition *into* a
single-frame flash at 1.0 and the transition *out* at ~0.01, so a "two spikes"
test misses every single-frame flash.

## Audio dropouts

`no_audio_dropouts` reuses `silencedetect` at -80 dBFS (0.02 s minimum) and
keeps only gaps that are bracketed by sound on both sides and shorter than 1 s.
That separates a broken join from leading/trailing padding and from a
deliberate pause, which `audio_not_silent` already covers as long silence.
Advisory by default; `max_audio_dropouts=0` fails on any gap.

## Freeze detection

`no_frozen_frames` runs FFmpeg `freezedetect` (n=0.003, d=1.5 s) and reports
the longest held frame. It is advisory by default, because screen recordings
legitimately hold. Pass `freeze_max_seconds=1.5` to make it fail explainers
or motion graphics that stall. See `docs/VIDEO_MOTION_QUALITY.md`.

## Safe-area check

When the job has `plan/render_graph.json`, QA adds `text_in_safe_area`. The
check counts text layers outside the platform safe area: the bottom 20 % and
8 % margins on vertical video, 5 % title-safe on landscape (see
`video_editor_core.safe_area`). It is advisory unless
`QASkill(..., safe_area_strict=True)`.
