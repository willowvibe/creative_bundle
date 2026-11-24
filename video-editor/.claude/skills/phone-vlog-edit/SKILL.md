---
name: phone-vlog-edit
description: >
  Use when editing the user's own phone-recorded talking-head footage — a vlog,
  interview answers, a self-recorded pitch: stitching clips, turning vertical
  9:16 into horizontal 16:9, cropping, cleaning and levelling the voice,
  trimming record/stop fumbles and silences, or cutting repeats to hit a time
  limit from a transcript. Not for generated explainers/shorts (video-editor)
  or for graphic overlays on a finished clip (hyperframes:talking-head-recut).
---

# Phone vlog edit

Plain FFmpeg + one build script per video in
`video-projects/videos/<date>/<slug>/build.sh` (renders in `out/`, untracked).
Worked example: `videos/2026-11-22/interview-vlog/` (`build.sh`, `NOTES.md`).

## 1. Probe first

- `ffprobe` each clip: codec, size, fps, **rotation** (phones often store
  `-180`/`90` as a display matrix; FFmpeg's autorotate applies it on decode, so
  crop coordinates refer to the upright picture).
- Clips the user excludes stay out by name — note them in `NOTES.md`.
- Grab a frame grid (two frames per clip) and look before choosing a crop.

## 2. Vertical → horizontal

Keep the top part of the 1080x1920 frame (face in the upper third, desk and
lower body dropped), scale it to height 1080, centre it on a blurred,
slightly darkened copy of the same frame:

```
crop=1080:H:0:0,split[a][b];
[a]scale=1920:-2,crop=1920:1080,boxblur=30:2,eq=brightness=-0.08:saturation=0.8[bg];
[b]scale=W:1080[fg];[bg][fg]overlay=(W-w)/2:0
```

H=1260 gave W=926. Render one preview frame and check it before the full run.

## 3. Voice: clean, tuned, constant

Measure before filtering: phones already apply AGC and noise suppression
(here LRA ~4 LU, pauses at −50 to −70 dB), so go gentle.

Chain: per-clip gain to a common LUFS → `highpass=80` → `afftdn=nr=18:nf=-45:tn=1`
→ EQ −2 dB @250 Hz, +2 dB @3.5 kHz → `acompressor` 3:1 @−24 dB →
`dynaudnorm=...:m=5:t=0.003` → `agate=threshold=0.025:range=0.03` → per-segment
trim (re-measure each segment, fix any >0.5 dB off) → gain to −14 LUFS +
`alimiter=limit=0.7`.

- **Levelling lifts room hiss.** Without `t=` and the gate, pauses rose from
  −69 to −55 dB. Measure the real gaps (silencedetect on the raw audio, then
  RMS of only those spans) — a 10th-percentile RMS mostly catches breaths and
  will not move when you change the gate.
- Two-pass `loudnorm linear=true` backs off when peaks lack headroom (landed at
  −15.3); plain gain + limiter is predictable.
- A clip recorded softer stays softer after the chain: re-measure per clip /
  segment after processing, not only before.
- `ebur128` on a stereo source reads ~3 dB hotter than the mono mix — compare
  like with like.

## 4. Clip edges: record/stop fumbles

Each phone clip opens with the hand leaving the record button plus 1–2 s of
silence, and ends with silence and the hand reaching to stop.

- Speech edges: `silencedetect=n=-40dB:d=0.8` per clip. Keep ~0.25 s before
  the first word, ~0.1 s after the last.
- **Check frames, not just audio:** tile the first 2 s and last 3 s of every
  clip *in the cropped view* — the reach can enter the frame before the last
  word's tail ends. Trim to the last clean frame.
- A short sound after the last word may be rustle, not speech: compare its
  level with the voice (−40 dB vs −20 dB here).

## 5. Cutting to a time limit (transcript-driven)

1. Transcribe the processed audio. Default: Sarvam (`SARVAM_API` in
   `video-editor/.env`; handles Indian English and Telugu) with
   `scripts/sarvam_stt.py phrases <wav> <tsv>`. Sync endpoint limit is 30 s
   and it returns one timestamp per call, so chunk at pauses. ElevenLabs STT
   is the fallback. A local Whisper model is not downloaded here — ask before
   pulling one.
2. Read the transcript and list cuts with a reason each: repeated points (say
   which telling is kept), restarts ("sorry, let me explain again"),
   side-tracks, stutters, answers that repeat an earlier claim.
3. For a cut inside a phrase, `sarvam_stt.py fine <wav> <txt> A-B ...` to find
   pause-level boundaries; probe a few start offsets if a phrase has no pause.
4. Target ~30 s under the limit. Map timeline cuts back to **source** times
   and rebuild from the originals (no re-encode of a render).
5. Re-transcribe the finished audio and read every join.
6. Flag content problems you can't cut away (e.g. years of experience that
   disagree between answers) — the user decides.

## 6. Render: frame-exact segments

- Snap every in/out to 1/30 s; print with `printf %.6f` (bc's `.866` is
  rejected by `ffmpeg -ss` and, under `set -e` inside `$(...)`, the script
  dies silently).
- Encode each segment on its own with `tpad=stop_mode=clone:stop_duration=0.2`
  and `-frames:v round(d*30)` — input seeks can drop a boundary frame (2 lost
  frames = 66 ms drift). Then concat with `-c copy` and mux the audio.
- One decoder per encode keeps memory low; long runs go detached:
  `setsid systemd-run --user --scope -p MemoryMax=8G bash build.sh > out/build.log 2>&1 &`.
- A wait loop must not `pgrep -f` a string that appears in its own command
  line; watch the log/output files instead.

## 7. Verify before reporting

- Video and audio stream durations equal (to a frame).
- Integrated LUFS, true peak, per-clip/segment LUFS spread, gap RMS.
- Frames either side of every join in the cropped view: no hands, no glitches.
- Re-read the new transcript at each cut.
