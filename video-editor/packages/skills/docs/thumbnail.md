# Thumbnail Skill

Extract candidate thumbnails from a final video, pick the best one,
and optionally render a title overlay.

## What it does

1. Probes the video duration with `ffprobe`.
2. Picks N evenly-spaced timestamps.
3. Extracts one JPG per timestamp via `ffmpeg -vf scale+pad`.
4. Scores each candidate:
   - **LLM scorer** (when provided) — vision LLM picks the most
     "clickable" frame and suggests a title.
   - **Heuristic scorer** (default fallback) — boosts middle frames
     and frames with high visual complexity (larger JPG size proxy).
5. Copies the best candidate to `selected.jpg`.
6. Optionally renders `with_title.jpg` with a drawtext overlay
   (bottom-center, white on black border, safe title sanitisation).

## Public API

```python
from thumbnail import ThumbnailSkill, ThumbnailConfig

skill = ThumbnailSkill(
    config=ThumbnailConfig(
        candidates=8,
        width=1280,
        height=720,
        title_overlay=True,
    ),
)
plan = skill.run(ctx, video_path=Path("final.mp4"))
```

## Outputs

```json
{
  "video": "final.mp4",
  "duration_seconds": 184.5,
  "candidates": [
    {
      "candidate_id": "candidate_001",
      "timestamp": 23.0,
      "output_path": "thumbnail/candidate_001.jpg",
      "score": 0.78,
      "title": "",
      "rationale": "heuristic: middle=0.92, complexity=0.85"
    },
    ...
  ],
  "selected": {
    "candidate_id": "candidate_004",
    "timestamp": 92.0,
    "score": 0.91,
    "rationale": "llm_vision",
    "output_path": "thumbnail/selected.jpg",
    "title": "How to scale your team without breaking"
  },
  "title_overlay": {
    "enabled": true,
    "title": "How to scale your team without breaking",
    "output_path": "thumbnail/with_title.jpg"
  },
  "scoring": "heuristic"
}
```

## Notes

- The skill defaults to 8 candidates at 1280×720 (YouTube thumbnail size).
- The `title_overlay` flag uses ffmpeg's `drawtext` filter; titles are
  sanitised before being passed to the filter (colons, quotes, backslashes
  and percent signs are stripped).
- The "best" frame is the highest-scoring candidate; ties are broken by
  candidate ID (earlier frames win).
- The skill is deterministic when no LLM scorer is provided — the same
  video always produces the same thumbnail.
