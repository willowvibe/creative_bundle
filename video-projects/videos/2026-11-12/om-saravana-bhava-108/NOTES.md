# Om Saravana Bhava ×108: devotional chant

Source: a WhatsApp voice note from the user, 2026-11-12 12:54 (8.6 s, mono MP3 128k), copied to
`audio/source_whatsapp.mp3` (untracked).

## Status

- **FINAL: video v5 (`video/om_saravana_bhava_108_v5.mp4`, images rotate through the whole chant), approved by the user 2026-11-13. Project done.** v4 (smaller petals, images only at the end) was approved before the user asked for this. Final audio is `audio/final/`.
- **Audio APPROVED: v4b (manjira "ding" after every chant), user 2026-11-13: "4b was good"; then the ding was raised 2 dB on request, which is v5 = final.** Final copy in `audio/final/`, and its timing (108 starts, 109 dings) is in `layout_final.json`. Video work may start.
- Cost so far: **$0**. Everything is local: faster-whisper, noisereduce, ffmpeg and numpy synths.

## The chant

- faster-whisper `medium` transcribed it as **"Om Saravana Bhava"** in Sanskrit, Tamil (ஓம் சரவண பவ),
  Hindi and English; every language agreed.
- The recording is exactly **one cycle**:
  - "Om" from 0.7 to 4.0 s, a long hum at about 205 Hz;
  - "Saravana" from 4.0 to 5.2 s;
  - "Bhava" from 5.2 s, held until 7.8 s and fading.
- Room noise is very low (−67 to −80 dBFS), and there is a small click at 8.4 s, after the voice ends.
- **Sa ≈ 210 Hz** (G#3, about +19 cents): the median f0 is 209.9 Hz and the held "Bhava" sits at 209–212 Hz.
  The voice was not pitch-corrected, to keep it natural for a chant.

## How v1 was built (`scripts/`)

1. **Clean** (`build.py`):
   - noisereduce, stationary, prop 0.7, profiled on the room tone at 0.10–0.58 s;
   - ffmpeg chain: `adeclick`, high-pass 75 Hz, low-pass 14 kHz, −1.5 dB at 220 Hz, +2 dB at 3.2 kHz,
     +1 dB at 9 kHz, de-esser 0.25, compression 2.5:1 at −24 dB.
2. **Cut** the longest voiced run, where dips under 0.4 s count as voiced: source 0.65 → 7.85 s
   (**7.20 s**), with a 15 ms fade-in and a 250 ms cos² fade-out. This leaves the 8.4 s click out.
3. **Repeat** the identical cycle 108 times on a fixed grid:
   - period **8.30 s** (the cycle plus a 1.1 s breath gap);
   - 5 s intro and 8 s outro.
4. **Backing**, synthesized and locked to the chant period:
   - tanpura: 8 plucks per period (Pa, Sa, Sa, low Sa), ducked about 3 dB under the voice so it opens up
     in the breath gaps;
   - shruti-style pad (low Sa, Pa, Sa) that breathes once per period;
   - temple bell (inharmonic partials at Sa×2) before cycle 1, before cycles 28, 55 and 82 (every 27,
     like a mala's quarter markers), and after cycle 108.
   - **No flute in v1**, so that it changes one thing per version. A flute is a candidate for v2.
5. **Mix and master** (`mix.sh`):
   - voice +6 dB, hall IR reverb wet 0.13;
   - tanpura −17 dB, pad −29 dB, bell −20 dB;
   - bus compression 1.8:1, then two-pass loudnorm to −14 LUFS with a −1.5 dBTP ceiling.

## v1 results (measured)

| | |
|---|---|
| Length | **15:08.3** (908.3 s) |
| Integrated loudness | **−14.0 LUFS** |
| True peak | **−4.8 dBTP** |
| LRA | 3.8 LU |
| Voice vs backing | −14.3 vs −31.3 LUFS: the voice is **17 dB in front** |
| Per-section loudness | every sampled cycle (1, 2, 27, 28, 54, 55, 81, 82, 107, 108) at −13.8 to −13.9 LUFS; gaps at −26 to −30 |
| **Repetition count** | **108**, checked two independent ways on the final master (`verify.py`) |

How the count was verified:

- **(a) Energy segmentation** of the 300–3400 Hz voice band:
  - 108 segments, each 6.72–6.74 s;
  - period 8.300 ± 0.000 s;
  - starts within 50 ms of the layout.
- **(b) Template matching**: normalized cross-correlation of the cycle's own envelope against the mix.
  - 108 matches with r > 0.8 (r from 0.861 to 0.915);
  - 0 ms offset from the layout.

## Files (renders are untracked)

- `audio/v1/om_saravana_bhava_108_v1.wav` (24-bit) and `.mp3` (320k): the full track.
- `audio/v1/AB_original_then_v1.mp3` (40 s): the raw voice note, then the first 30 s of v1.
- `audio/stems_v1/`: the cleaned cycle, the 108-repeat voice, tanpura, pad, bell and hall IR.
- `layout_v1.json`: Sa, cycle length, period, all 108 start times and the bell times. The video should
  sync to these.

To rebuild (about 1 min on CPU):

```bash
python -I scripts/build.py raw.wav v1 1.1 210
bash scripts/mix.sh v1 v1/om_saravana_bhava_108_v1
python -I scripts/verify.py v1/om_saravana_bhava_108_v1.wav v1/voice_cycle.wav v1/layout.json
```

`raw.wav` is the source converted to 44.1 kHz mono. `mix.sh` expects `v1/hall_ir.wav`, which the
vocal-to-song skill's `make_ir.py` creates.

## v2: a bell after every chant (user request, 2026-11-12)

- **Change:** the temple bell now rings 0.05 s after every chant ends, 108 times, at 0.75 level. The last
  one rings at full level. The opening bell at 2.0 s stays. This replaces v1's bells every 27 cycles.
  Nothing else changed: same voice, tanpura, pad, levels and grid.
- **Measured:**
  - 15:08.3, −14.0 LUFS, −4.7 dBTP, LRA 3.7;
  - voice −14.3 vs backing −30.5 LUFS;
  - every sampled cycle at −13.8 LUFS;
  - gaps −28.3 to −24.2 LUFS (v1: −31.5 to −25.3).
- **Count still 108:**
  - energy segments: 108, each 6.66–6.72 s, period 8.300 s;
  - template matches: 108, r 0.887–0.934, 0 ms offset.
- **Files:**
  - `audio/v2/om_saravana_bhava_108_v2.{wav,mp3}`;
  - `audio/v2/preview_first30s_v2.mp3`;
  - `audio/stems_v2/bell.wav`;
  - `layout_v2.json`, whose `bells_s` lists all 109 bell times for the video.
- `scripts/build.py` now builds v2. Recover v1's bell schedule from git history.

## v3: the Lingashtakam temple-bell sound (user request, 2026-11-12)

- **Request:** the user wanted the bell to sound like the temple bell from last week's Lingashtakam song.
- **Change:** the bell timbre only. Timing, gains, voice and backing are unchanged from v2.
  - `bell_hit()` is ported verbatim from vocal-to-song `arrange.py`, stage D (Lingashtakam v11).
  - Partials at ×0.5, 1, 1.183, 1.506, 2, 2.514 and 3.011 of Sa×2, with decays of 5 down to 0.8 s and a
    7 s ring. The ×0.5 partial is the deep hum.
  - Fresh random phases on every strike, panned −0.2, mixed at −20 dB with no high-pass. Lingashtakam had
    none either; v2's 200 Hz high-pass was removed so the hum comes through.
  - The bell is tuned to this chant's Sa (210 Hz, so it strikes at 420 Hz). Lingashtakam's was at
    578 Hz because Sruthi's Sa is 289 Hz, so this one sounds a little deeper.
- **Measured:**
  - 15:08.3, −14.0 LUFS, −4.8 dBTP, LRA 3.7;
  - voice −14.3 vs backing −30.7 LUFS;
  - cycles 1, 28, 55, 82 and 108 at −13.8 to −13.9 LUFS;
  - gaps −29.4 to −23.6 LUFS.
- **Count still 108:**
  - energy segments: 108, each 6.68–6.72 s, period 8.300 s;
  - template matches: 108, r 0.882–0.930, 0 ms offset.
- **Files:**
  - `audio/v3/om_saravana_bhava_108_v3.{wav,mp3}`;
  - `audio/v3/preview_first30s_v3.mp3`;
  - `audio/stems_v3/bell.wav`;
  - `layout_v3.json`, with the same bell times as v2.

## v4a / v4b: matching the "ding" in the user's reference (2026-11-12/13)

- **Reference:** the user sent `WhatsApp Audio 2026-11-05 at 11.13.59.mpeg` (3:18.09). A mel-spectrogram
  match against `~/Downloads/lingashtakam_out/` gave correlation 1.0000 with **`v7_veena.mp3`**, so the
  file is that render.
- **Two sounds in it could be the "ding":**
  - the temple bell, which strikes at 0.23 s and at the interludes and end. It rings at 578 Hz because
    that song's Sa is 289 Hz; v3 rang it at 420 Hz, which is deeper;
  - the open manjira cymbal stroke (partials 2.4–8.8 kHz, 1.4 s ring) on beats 1 and 5 of every cycle.
- **Both were built rather than guessed.** Only the bell changed from v3; timing and gains are the same.
  - **v4a:** the same temple bell at Lingashtakam's exact pitch, `BELL_KIND=temple BELL_F=578`.
  - **v4b:** the manjira open "ding", `BELL_KIND=manjira`, mixed at −22 dB (the user's preferred manjira
    level).
- **Both measured:** −14.0 LUFS, −4.7 / −4.8 dBTP, voice 16–17 dB above the backing, and **108** by both
  counts with 0 ms offset.
- **Comparison clip:** `audio/compare_dings_ref_v4a_v4b.mp3` (60 s). It plays:
  - v7 at 0–4 s (the opening temple bell);
  - v7 at 12–20 s (the verse, with the manjira);
  - v4a, first 22 s;
  - v4b, first 22 s;
  - with 1.5 s of silence between the parts.

## v5 = final: ding +2 dB (user request, 2026-11-13)

- **Change:** v4b's stems remixed with the manjira ding at −20 dB instead of −22. Nothing else changed:
  `bash scripts/mix.sh v4b v5/om_saravana_bhava_108_v5 0.13 -17 -29 -20`.
- **Measured:**
  - 15:08.3, −14.0 LUFS, −4.8 dBTP, LRA 3.8;
  - the ding peak in the 2–9 kHz band rose from −27.7 to −25.8 dBFS, +1.9 dB after loudness normalization;
  - the voice is unchanged, 16.9 dB above the backing;
  - **108** by both counts, 0 ms offset.
- `layout_final.json` is still valid, because the timing is the same as v4b.

## Muruga images (2026-11-13; free, $0)

- Made by `scripts/gen_images.py`, which tries only free providers: agy_image, then flow_image. It never
  reaches a paid one. The format is 16:9, because the user did not pick one.
- **agy problem (engine follow-up):** agy 1.5.7 drew all 5 images but saved them in its profile
  `brain/<session>/` folder, not the requested path. The provider therefore rejected every one. The files
  were recovered from there into `images/*__agy.jpg` (1376×768).
- **Flow:** made m1 and m2 (`*__flow_image.png`, with the sparkle mark at bottom right). It then timed
  out on a `locator.click` for m3–m5.
- **QA by eye:**
  - no garbled text in any image; the Vel in m1 carries a small Om symbol;
  - two arms on every figure, and the anatomy looks right;
  - **m3 (portrait) shows a teal sari-like drape**, which reads as a feminine costume. Flagged to the user
    and offered a regeneration in dhoti and angavastram.
- `images/contact_sheet.jpg`, row by row: m1 agy | m1 flow; m2 agy | m2 flow; m3 | m4; m5.

## Options for the review

- Gap length: 1.1 s now. A longer gap (1.5–2 s) feels more meditative; 108 × 0.4 s adds 43 s.
- Backing level: tanpura and pad up or down, or the bell every cycle instead of every 27.
- Add a soft flute: expressive and following the voice's contour, never snapped to notes.

## Video (2026-11-13)

Free, local and $0: PIL art, numpy/PIL effect loops, ffmpeg render.

- **Format:** 1920×1080 at 30 fps, H.264 CRF 18, AAC 256k. Length 908.3 s, matching the audio exactly.
  The user did not pick a format, so it defaulted to 16:9.
- **Chants 1–98 (0:00–13:37):**
  - `art/bg.png`: a warm temple-dark plate with gold bokeh, drifting slowly;
  - a glowing Tamil Om sign ௐ;
  - a brightness bloom on every ding;
  - `petals_ding.mov`: a light wave of marigold, rose and jasmine petals released just after each ding.
    The loop is one chant period (8.3 s), so it stays locked to the grid.
- **Chants 99–108 (13:37–15:08):** the five Muruga images, two chants each, starting on the ding.
  - Each image has its own eased zoompan: m1 pushes in, m2 zooms out, m3 pushes in, m4 tilts from the
    feet to the face, m5 pulls back.
  - Transitions: fadewhite (3 s), circleopen, hblur, fade, circleopen.
  - A dense `petals_shower.mov` (pushpanjali) falls over them.
- **Always on screen:**
  - `glitter.mov`, golden sparkles;
  - the Tamil + English chant caption, larger (Tamil 104 px, English 66 px) and **centred at the 75%
    line**, both at the user's request;
  - an "n / 108" japa counter at the bottom, which changes at each chant start;
  - a 3 s fade-in and 6 s fade-out.
- **Captions on images:** at 75% the caption crosses the lower part of each figure. A soft bottom shade
  (`art/shade.png`) on the images only keeps it legible.
- **Scripts:** `scripts/make_art.py` (bg, om, caption, shade), `scripts/fx.py` (the three loops) and
  `scripts/render.sh` (the full graph; it writes the `.ffgraph` file next to the mp4).
- **v2 QA** (a 16-frame contact sheet):
  - captions, counter, ding glow, petals, fadewhite and the zooms were all good;
  - the radial wipe cut a hard diagonal across Bala Murugan's face, and dissolve speckled the portrait's
    face with noise;
  - v3 replaces them with hblur and fade.
- **v3 = current deliverable:** `video/om_saravana_bhava_108_v3.mp4`.
  - 908.3 s, 27,249 frames, 231 MB; the whole file decodes.
  - QA frames at 850.6–851.8 s (hblur) and 867.2–868.4 s (fade) are smooth, with no hard edges or noise
    on faces.
  - `video/preview_v3_82s.mp4` (720p) holds the first 22 s plus 13:32–14:32 (the white reveal and the
    first three images).
- **v4 (user: "decrease the petal size"):** petal sprites went from 72/54 px to 48/36 px in `scripts/fx.py`, a third
  smaller; nothing else changed. 908.3 s, 27,249 frames. Side-by-side frames at 15.5 s and 826 s against v3
  confirm the petals are smaller with identical paths. Preview: `video/preview_v4_82s.mp4`.
- **v5 (user: "there is only one image in the background for a long time, what about the other images?")**,
  built by `scripts/render_rotate.py`:
  - Om intro for chants 1–2;
  - 4 rounds over chants 3–90, each m1–m5 at 4 chants apiece, then the Om plate for 2 chants;
  - a finale over chants 91–108 (m1–m5 at 4, 3, 4, 3, 4 chants) under the petal shower.
  - Every cut lands on a ding. Each return to the images uses fadewhite, and zooms reverse on alternate
    appearances.
  - Measured: 908.3 s, 27,249 frames, −14.0 LUFS. A 20-frame QA sheet shows the rotation, the interludes
    near chants 23, 46, 67 and 89, and the counter in sync.
  - Note: halfway through the m4 tilt the top of the head is above the frame until the move reaches the
    face.
  - **Memory:** one graph with 30 looped 2880-px image inputs buffered about 24 GB and was OOM-killed, which
    also took down the Claude session. The script now renders per-segment clips, then per-transition
    clips, joins them with the concat demuxer, and adds overlays and audio in one light pass (about 1.5 GB).
    It runs detached under `systemd-run --user --scope -p MemoryMax=14G`.
  - Preview: `video/preview_v5_90s.mp4` (0:00–1:00 and 3:02–3:32, the Om interlude and the return to the
    images).
- 2026-11-13: at the user's request ("delete all old version videos"), the v3 and v4 mp4s, their previews and
  their .ffgraph files were moved to the system Trash. Only v5 and its preview remain.
- Renders are in `video/` (untracked).

## Published (2026-11-13)

- Mantra Shakti, https://youtu.be/1e9l7MDYjcs, **scheduled public 2026-11-13 17:00 IST** (private until then). Metadata in `publish.yaml`.
- Uploaded through the browser lane, then tags (19), category Music and the schedule set on the Studio details page and checked after a reload.
- The first upload was interrupted when the channel's Chrome closed; it was resumed with Studio's "Resume upload" in a hidden Xvfb Chrome.
