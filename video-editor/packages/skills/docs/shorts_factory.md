# Shorts Factory Skill

Turn one long-form video into 3–5 vertical Shorts/Reels.

## What it does

1. Reads a transcript (or auto-synthesises one from probe duration).
2. Slides candidate windows across the transcript using sentence-boundary
   snapping.
3. Scores each candidate:

   - **LLM scorer** (when an LLM client is provided) — picks the
     most "viral" / shareable moments.
   - **Heuristic scorer** (default fallback) — boosts windows with
     hook phrases ("the truth is", "here's the thing", "the real
     reason", etc.) and high word density.

4. Slices the top-N windows from the long-form video with
   `ffmpeg -ss -t -c copy` (no quality loss on the slice).
5. Reframes each slice to 9:16 (1080×1920) using a crop+zoom filter
   without re-encoding the audio.
6. Writes `plan.json` and `short_NNN.mp4` files in the output
   directory.

## Public API

```python
from shorts_factory import ShortsFactorySkill, ShortsFactoryConfig

skill = ShortsFactorySkill(
    config=ShortsFactoryConfig(count=3, min_duration=15.0, max_duration=45.0),
)
plan = skill.run(
    video_path=Path("long_video.mp4"),
    transcript=[{"start": 0.0, "end": 4.2, "text": "..."}, ...],
    output_dir=Path("videos/2026-08-09/shorts"),
)
```

## Outputs

```json
{
  "long_video": "long_video.mp4",
  "count": 4,
  "scoring": "heuristic",
  "aspect_ratio": "9:16",
  "shorts": [
    {
      "short_id": "short_001",
      "start": 12.5,
      "end": 38.0,
      "duration": 25.5,
      "score": 0.84,
      "title": "The truth is most founders get this wrong...",
      "transcript_excerpt": "...",
      "rationale": "hook_phrase x2, density=2.74 words/s",
      "output_path": "videos/2026-08-09/shorts/short_001.mp4"
    }
  ]
}
```

## Notes

- The skill does not depend on numpy — it uses ffmpeg for slicing and
  reframe so the runtime stays lightweight.
- The "viral" scorer is intentionally heuristic by default; high-end
  users can swap in an LLM by passing a `MomentScorer` to the skill.
- Captions are not auto-burned (the captions skill is the canonical
  source); the skill emits word-level ranges in `plan.json` for
  downstream captioning.
