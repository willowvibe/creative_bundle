# Devotional chant videos (japa ×108)

A long, calm video for a repeated chant. Captions stay on screen, a counter steps from 1 to 108, petals
and glitter fall, and deity images rotate on the dings.

- **Built:** 2026-11-13 for *Om Saravana Bhava* ×108. Approved as v5, 15:08 at 1080p30, $0.
- **Production:** `../video-projects/videos/2026-11-12/om-saravana-bhava-108/`. `NOTES.md` holds every
  version and measurement, and `scripts/` holds all the code.
- **Audio method** (clean one cycle, repeat it N times, ding after each chant, verify the count): the
  vocal-to-song skill, section 8, with scripts in `scripts/chant/`.

This runs outside the `ve` engine: plain PIL, numpy and ffmpeg scripts kept with the project. Keep them
there, or port them into the engine with tests if a second chant video needs them.

## Timeline: everything hangs on the chant grid

The audio step writes `layout.json`. With a 7.2 s cycle and a 1.1 s gap:

- chant k starts at `5 + 8.3·(k−1)`;
- its ding sounds at `12.25 + 8.3·(k−1)`.

Every visual event is placed on these times:

- **Counter:** a drawtext expression `clip(floor((t−5)/8.3)+1, 1, 108)`, which changes as each chant starts.
- **Ding glow:** a brightness bloom from `eq=eval=frame`, `0.045·exp(−mod(t−12.25, 8.3)/0.9)`.
- **Effect loops:** each is exactly one chant period long (8.3 s = 249 frames). Laid from t=0 with
  `-stream_loop -1`, they stay locked to the grid for all 108 chants.
- **Image cuts and transitions:** all start on a ding.

## Layers (`scripts/make_art.py`, `scripts/fx.py`)

| Layer | What | Notes |
|---|---|---|
| `bg.png` 2304×1296 | Warm temple-dark plate with gold bokeh | A slow Lissajous crop drifts it |
| `om.png` | Tamil Om sign ௐ (U+0BD0) with a glow | Noto Serif Tamil. Pillow needs raqm for Tamil shaping; check `features.check('raqm')` |
| `caption.png` | "ஓம் சரவண பவ" (104 px) over "Om Saravana Bhava" (66 px) | **Centred at 75% height**, on screen the whole time, with a dark halo (user request) |
| `shade.png` | Bottom gradient | Images only; keeps the 75% caption legible on busy art |
| `petals_ding.mov` | A light wave of rose, marigold and jasmine petals after each ding | qtrle with alpha. Petals 48 px (36 px jasmine); the user found 72 px too big |
| `petals_shower.mov` | A dense pushpanjali shower | The finale |
| `glitter.mov` | Twinkling golden four-point stars | An integer number of twinkle cycles per loop keeps it seamless |

Petals fall on modular time: `age = (t − spawn) mod P`. A petal released near the loop end continues
across the loop boundary, so the loop has no seam.

## Images: rotate through the whole video

The user found images only at the end too static ("only one image in the background for a long time").
The approved layout (`scripts/render_rotate.py`):

- the Om plate for chants 1–2;
- 4 rounds, each showing m1–m5 for 4 chants apiece followed by the Om plate for 2 chants;
- a finale of m1–m5 at 4, 3, 4, 3 and 4 chants under the shower, with the last image held through the fade.

How the images move:

- Each image has an eased zoompan (smoothstep) on a pre-upscaled 2880×1620 copy, so the motion has no
  pixel stepping.
- The moves are: push toward the face, zoom out to reveal, tilt from the feet up to the face, and pull
  back to the whole shrine. The move reverses on alternate appearances.

Transitions:

- **Good:** `fadewhite` (a "divine light" reveal when the images return), `circleopen`, `hblur`, `fade`.
- **Avoid on faces:** `radial`, which sweeps a hard diagonal edge across a face, and `dissolve`, which is
  pixel noise and speckles a face.

## Render in pieces, never as one graph

One `filter_complex` with about 30 looped image inputs and a chain of 29 xfades used **about 24 GB in
45 s** and was OOM-killed, taking the Claude session down with it. ffmpeg reads every input at once and
queues frames for segments that are not yet needed. `render_rotate.py` instead:

1. renders each segment to its own clip, including its outgoing 2 s overlap;
2. renders each transition from the tail of one clip and the head of the next (`trim` + `xfade` with
   `offset=0`);
3. renders each body, which is the clip minus its overlaps;
4. joins body, transition, body and so on with the concat demuxer;
5. adds glow, glitter, petals, caption, counter, fades and audio in one light pass (about 1.5 GB).

Boundaries are whole frames, `round(seconds·30)`, so the pieces add up exactly to the audio's 27,249 frames.

Run long renders **detached and capped**. A `run_in_background` job dies when the Claude session ends.

```bash
setsid nohup systemd-run --user --scope -q -p MemoryMax=14G \
  python3 scripts/render_rotate.py video/out.mp4 > video/render.log 2>&1 < /dev/null & disown
```

## Deity images

- Generate 4–5 images with the free chain: `agy_image`, then `flow_image`.
- Prompts ask for a traditional South Indian devotional oil-painting style, exactly two arms, and no text,
  letters, watermark or frame.
- Look at every image before using it: anatomy, garbled text, attire.
- agy 1.5.7 saves into its profile's `brain/<session>/` folder, so the provider rejects the file (#370).
  Copy the images out of there by hand until that is fixed. Flow stills carry a sparkle mark at bottom
  right, so prefer the agy copies.

## QA checklist

- `ffprobe`: the video and audio durations are equal, and the frame count equals `round(TOTAL·30)`.
- Loudness is −14 LUFS and true peak at or below −1.5 dBTP.
- Contact sheets of 16–20 frames spread over the timeline: the counter matches each frame's chant, the
  interludes fall where planned, and the caption is at 75%.
- Frame-step every transition, about 0.4 s apart, looking at faces.
- For any change, compare a few frames side by side with the previous version.
