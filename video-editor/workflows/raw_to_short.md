# Raw to Short Workflow

Turn raw footage into a short-form explainer clip.

## Fast path (one command)

If you just want the final video, run the full skills-first pipeline in one command:

```bash
ve run ./demo/raw.mp4 \
  --job-id my-short \
  --work-root ./projects \
  --output-root ./outputs \
  --preset short_explainer \
  --transcript ./demo/transcript.json \
  --music-path ./demo/music.m4a
```

See `workflows/raw_to_short_one_command.md` for details.

## Manual path (per-skill commands)

Use these when you want to inspect or tweak each stage:

1. **Intake** — copy footage to `projects/{job_id}/intake/`.
2. **Rough Cut** — run `ve rough-cut` to lock the edit.
3. **Graphics** — load `short_explainer` preset, render overlays.
4. **Second Pass** — refine segments one at a time via render cache.
5. **Captions** — animate word-level captions.
6. **Music** — mix background track.
7. **Export** — final MP4 to `outputs/{job_id}/`.
