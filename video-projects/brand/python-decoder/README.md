# Python Decoder: channel intro and outro

Reusable bumpers for every @pythondecoder101 video. One script makes both, per episode:

```
uv run python brand/python-decoder/make_bumpers.py OUT_DIR --episode "EP 1 · Python 3.15" --next "Up next: uv from scratch" [--question "Which feature will you use first?"]
```
→ `OUT_DIR/intro.mp4` (4 s) and `OUT_DIR/outro.mp4` (12 s), 1920×1080, 30 fps, AAC 44.1 kHz stereo (matches
`ve talking-avatar` output, so they join with the stream-copy concat demuxer).

## Intro (4 s) — "decoding" sting

| Time | What happens |
|---|---|
| 0.0–0.5 | Dark navy screen, faint scrolling code glyphs, a blinking caret |
| 0.3–1.7 | **Decode effect:** "PYTHON DECODER" appears as random code characters that lock in left to right ("DECODER" in Python yellow); a soft tick per locked letter |
| 1.3–1.9 | The channel avatar (same cartoon sprite as in the videos) slides up bottom-right with a small overshoot; chime |
| 1.9–2.8 | Tagline types in: "Modern Python, decoded." |
| 2.2–3.4 | Episode chip slides in (e.g. "EP 1 · Python 3.15"); omit `--episode` for a generic intro |
| 3.4–4.0 | Gentle zoom and fade to black into the video |

Sound: synthesized (no licence questions): a filtered-noise whoosh, quiet ticks, a two-note chime (E5 → B5), at about
−18 LUFS so it sits under the −15 LUFS narration.

## Outro (12 s) — end-screen ready

| Area | Content |
|---|---|
| Left | Avatar (breathing motion), "Thanks for watching!", "Which feature will you use first? Tell me in the comments 👇" |
| Right top | 16:9 frame labelled with `--next` (e.g. "Up next: uv from scratch") — place YouTube's **video** end-screen element here |
| Right bottom | Circle labelled "Subscribe" — place YouTube's **subscribe** element here |
| Audio | Soft synthesized pad, fades out over the last 2 s |

YouTube end screens need the last 5–20 s of the video; 12 s gives room for both elements. In Studio → Editor → End
screen, choose the "1 video, 1 subscribe" layout and drag the elements onto the two frames.

## Rules

- Same palette as the section titles in the videos: navy `#0E1220`, Python yellow `#FFD43B`, Python blue `#4B8BBE`.
- Keep the intro ≤ 4 s (viewers skip long intros); the hook in the video comes right after it.
- Regenerate per episode only to change `--episode` / `--next`; everything else stays the same.

## Upload defaults for this channel

- **AI use / altered content: No** (`synthetic_media: false`). The narration is the owner's own voice clone (YouTube
  doesn't require disclosure for cloning your own voice), screens and outputs are real captures, the avatar is clearly
  animated, and music is a background bed. Re-check only if a video adds realistic AI footage of real people/places,
  someone else's voice, or AI music as the main focus.
- Category Education, language English (India) (channel defaults), not made for kids, playlist per series.
- Pass each episode's own `--question` (EP2: "Will you switch from pip to uv?", EP3: "What tool would you give your AI
  first?"); without it the outro shows the default EP1 question. Put it in the episode's `render_all.sh` too.
