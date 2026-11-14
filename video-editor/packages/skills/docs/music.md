# Music Skill

Mix a background music track onto the latest video (captions or graphics output)
with speech ducking via FFmpeg `sidechaincompress`.

## What it does

1. Reads the job manifest to find the latest video:
   - `captions/captions.json` output (if captions were run)
   - `graphics/with_graphics.mp4` (fallback)
   - `rough_cut/locked.mp4` (final fallback)
2. Loads a background music file from `--music-path` or `assets/music/default.mp3`.
3. Loops the music if it is shorter than the video.
4. Applies gain, optional fade in/out, and sidechain ducking against the speech
   audio.
5. Outputs `projects/{job_id}/music/with_music.mp4` and a manifest at
   `projects/{job_id}/music/music.json`.

## CLI usage

```bash
# Mix the default music asset onto the latest video
ve music demo-rc --work-root ./projects --output-root ./outputs

# Specify a custom music file
ve music demo-rc --music-path ./assets/music/lofi.mp3 \
   --work-root ./projects --output-root ./outputs

# Custom output name
ve music demo-rc --music-path ./assets/music/lofi.mp3 \
   --output-name final_with_music.mp4
```

## Programmatic usage

```python
from pathlib import Path
from music.music import MusicSkill
from music.models import MusicStyle
from video_editor_state.job import JobContext

ctx = JobContext(job_id="demo", work_root=Path("./projects"), output_root=Path("./outputs"))
style = MusicStyle(
    music_path="./assets/music/lofi.mp3",
    music_gain_db=-16.0,
    duck_ratio=20.0,
    fade_in=1.0,
    fade_out=2.0,
)
skill = MusicSkill(ctx, style=style)
output = skill.run()
```

## Music style

`MusicStyle` controls ducking strength, timing, and gain:

| Field | Default | Description |
|-------|---------|-------------|
| `music_path` | `""` | Path to background music file |
| `music_gain_db` | `-14.0` | Gain applied to music before ducking |
| `speech_gain_db` | `0.0` | Gain applied to speech audio |
| `duck_threshold` | `0.1` | Sidechain threshold (linear amplitude) |
| `duck_ratio` | `15.0` | Compression ratio when speech is present |
| `attack_ms` | `50` | Ducking attack time |
| `release_ms` | `300` | Ducking release time |
| `loop` | `True` | Loop music if shorter than video |
| `fade_in` | `0.0` | Music fade-in seconds |
| `fade_out` | `0.0` | Music fade-out seconds |

## Output

- `projects/{job_id}/music/with_music.mp4` (or requested output name)
- `projects/{job_id}/music/music.json`

## Cache behavior

The Music skill is a single final mix step that does not re-render the graphics
segments. It relies on the Graphics/Captions cache already being warm and simply
consumes the previously produced video. Re-running with the same inputs is
idempotent: the manifest is updated and the output is overwritten.
