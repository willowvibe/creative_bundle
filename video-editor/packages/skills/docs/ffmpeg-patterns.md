---
type: code
description: FFmpeg patterns used across the video editor engine.
---

# FFmpeg Patterns Skill

Use this skill when editing `packages/ffmpeg` or any code that calls FFmpeg.

## Core principles

- All FFmpeg calls go through `packages/ffmpeg` so they are logged, cached, and idempotent.
- Prefer `subprocess.run` with explicit args list to avoid shell injection.
- Always read duration/dimensions from `ffprobe` first.
- Use `filter_complex` for multi-segment cuts to avoid re-encoding where possible.

## Common commands

### Probe

```python
["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", input_path]
```

### Make proxy

```python
["ffmpeg", "-i", input, "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "fast", "-crf", "28", "-c:a", "aac", "-b:a", "128k", proxy]
```

### Concat segments (same codec)

```python
# write concat list file, then:
["ffmpeg", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", output]
```

### Normalize audio

```python
["ffmpeg", "-i", input, "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:v", "copy", output]
```

### Burn captions

```python
["ffmpeg", "-i", video, "-vf", "subtitles=captions.srt", "-c:a", "copy", output]
```

### Smooth transitions between segments (`xfade`)

```text
[seg0_t][seg1_t]xfade=transition=fade:duration=1.5:offset=8.0[out1];
[out1][seg2_t]xfade=transition=wipeleft:duration=1.5:offset=16.0[out2]
```

- Internal segments need source material of `segment_duration + transition_duration`
  so the overlap has enough frames.
- Offsets are `(i + 1) * segment_duration`; the last segment supplies whatever
  remains of the music.
- Valid styles include `fade`, `wipeleft`, `wiperight`, `slideleft`, `radial`,
  `pixelize`.

### Ken Burns on a still image (`zoompan`)

```text
[0:v]zoompan=z='1.0+0.04*on/285':x='(-0.01+0.02*on/285)*iw':y='(ih-oh)/2':
          d=285:s=1920x1080:fps=30,trim=0:9.5,setpts=PTS-STARTPTS[seg]
```

- Keep the zoom delta small (e.g., `1.0 → 1.04`) and pan delta tiny
  (`±0.01`) for calm, slow motion.
- `d` is the number of output frames; `s` and `fps` set the output resolution
  and frame rate.
- Always follow with `trim` + `setpts=PTS-STARTPTS` so the segment starts at 0.

### Slow a video clip to 50% (`trim` + `setpts`)

```text
[0:v]trim=0:4.5,setpts=2*(PTS-STARTPTS),scale=1920:1080:force_original_aspect_ratio=increase,
      setsar=1,crop=1920:1080:(iw-1920)/2:(ih-1080)/2,fps=30[seg]
```

- Trim half the raw clip duration, then stretch timestamps by 2× so the same
  segment slot plays at half speed.
- Use `force_original_aspect_ratio=increase` + `crop` to cover the target frame
  without distortion.

### Mute source clip audio and mux a hero music track

```python
# Add each visual source as a separate -i (drop their audio).
# Add the music as the last input and map only its audio stream.
cmd = [
    "ffmpeg", "-y",
    "-i", str(clip1), "-i", str(clip2), "-i", str(music),
    "-filter_complex", filter_complex,
    "-map", "[final_v]",
    "-map", f"{music_input_index}:a",
    "-c:v", "libx264", "-c:a", "aac", "-b:a", "192k",
    "-shortest", output,
]
```

- Mapping only `{music_input_index}:a` guarantees the output uses the hero music
  and drops original clip audio.
- `-shortest` keeps the output length matched to the music duration.

## Idempotency helper

```python
if artifact.exists() and not force:
    logger.info("skipping_ffmpeg_operation", output=str(artifact))
    return artifact
```
