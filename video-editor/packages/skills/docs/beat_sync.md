# Beat Sync Skill

Detect musical beats and onsets in a video or audio file, and emit
beat-synced cut points for montage-style edits.

## What it does

1. Extracts audio to mono 16 kHz PCM (ffmpeg).
2. Loads samples into a Python list of floats.
3. Runs a pluggable `BeatDetector` (default: librosa if available,
   otherwise an stdlib energy-threshold fallback).
4. Optionally emits beat-synced cut points every N beats (respecting
   a minimum interval).
5. Writes `beats.json`, `cuts.json`, and `plan.json` under
   `beat_sync/`.

## Public API

```python
from pathlib import Path
from beat_sync import BeatSyncSkill, BeatSyncConfig

skill = BeatSyncSkill(
    config=BeatSyncConfig(
        sample_rate=16000,
        generate_cuts=True,
        cut_every_n_beats=4,
        cut_min_interval=0.5,
    )
)
plan = skill.run(ctx, video_path=Path("final.mp4"))
```

## Outputs

```json
{
  "duration_seconds": 184.5,
  "sampled": true,
  "detector": "_LibrosaDetector",
  "beat_count": 312,
  "avg_bpm": 101.4,
  "config": {
    "sample_rate": 16000,
    "hop_length": 512,
    "onset_delta": 0.5,
    "min_bpm": null,
    "max_bpm": null,
    "generate_cuts": true,
    "cut_every_n_beats": 4,
    "cut_min_interval": 0.5
  },
  "beats_path": "beat_sync/beats.json",
  "cuts_path": "beat_sync/cuts.json",
  "cut_count": 78
}
```

## `beats.json`

```json
[
  {"timestamp": 0.50, "strength": 0.82, "is_onset": true},
  {"timestamp": 1.07, "strength": 0.45, "is_onset": false},
  ...
]
```

## `cuts.json`

```json
[
  {"index": 0, "timestamp": 2.0, "beat_index": 8, "strength": 0.71},
  {"index": 1, "timestamp": 4.0, "beat_index": 16, "strength": 0.55},
  ...
]
```

## Notes

- The skill defaults to `librosa` if it is importable; otherwise it
  falls back to a pure-stdlib energy-threshold detector. The fallback
  is good enough for short upbeat clips and unit tests.
- Pass a custom `BeatDetector` to plug in something else (e.g.
  essentia, madmom, or a Triton-served model).
- The audio is extracted to a transient WAV under `beat_sync/_audio.wav`
  and removed at the end of the run.
- Beats longer than `_MAX_BEATS_OUTPUT` (default 5,000) are truncated.
- Optional BPM filtering (`min_bpm` / `max_bpm`) is applied to the
  final list based on average inter-beat interval.
