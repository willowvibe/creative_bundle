# video-editor-skill-audio-cleanup

Audio cleanup skill for the video editor engine. Isolates vocals, reduces
noise, and normalizes speech as an optional pre-stage before rough cutting.

## Usage

```python
from video_editor_state.job import JobContext
from audio_cleanup.audio_cleanup import AudioCleanupSkill

ctx = JobContext(job_id="demo", work_root="./work", output_root="./out")
skill = AudioCleanupSkill(ctx, isolate_voice=True, denoise=True)
result = skill.run("noisy.mp4")
print(result["output"])  # ./work/audio_cleanup/cleaned.wav
```

## Protocol

Implements the project `SkillProtocol` with `name` property and `run()` method.
