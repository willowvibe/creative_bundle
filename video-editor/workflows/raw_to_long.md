# Raw to Long Workflow

Turn raw footage into a long-form video with lower thirds and full graphics overlays.

## Fast path (one command)

```bash
ve run ./demo/raw.mp4 \
  --job-id my-long \
  --work-root ./projects \
  --output-root ./outputs \
  --preset long_form \
  --transcript ./demo/transcript.json \
  --music-path ./demo/music.m4a
```

See `workflows/raw_to_short_one_command.md` for the full one-command guide.

## Manual path (per-skill commands)

1. **Intake** — copy footage to `projects/{job_id}/intake/`.
2. **Rough Cut** — run `ve rough-cut` to lock the edit.
3. **Graphics** — load `long_form` preset, render overlays.
4. **Second Pass** — refine segments one at a time via render cache.
5. **Captions** — animate word-level captions.
6. **Music** — mix background track.
7. **Export** — final MP4 to `outputs/{job_id}/`.
