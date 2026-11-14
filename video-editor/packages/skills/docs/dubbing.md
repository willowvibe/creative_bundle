# Dubbing Skill

Multi-speaker dubbing pipeline that takes a transcript with speaker labels,
translates each speaker's segments to a target language, synthesizes them
with TTS, force-aligns the new audio to the original timing, and mixes the
resulting audio back into the source video.

The skill is designed to be **pluggable** — the four core subsystems
(diarizer, translator, TTS provider, aligner) are all injected so that
tests can run with deterministic mocks while production uses real
implementations.

## Files

- `dubbing.py` — `DubbingSkill` class, `SpeakerSegment` dataclass,
  `DubbingConfig` dataclass, `DubbingError` exception.
- `__init__.py` — public re-exports.

## Usage

```python
from dubbing import DubbingSkill, DubbingConfig

skill = DubbingSkill(
    ctx,
    config=DubbingConfig(target_language="es"),
    diarizer=my_diarizer,
    translator=my_translator,
    tts_provider=my_tts,
    aligner=my_aligner,
)
result = skill.run(transcript, segments, audio_path=source_wav)
```

## Subsystem protocols

Each subsystem is a `typing.Protocol` so production code can wire any
implementation; tests pass simple callables.

- `Diarizer.run(audio_path) -> list[SpeakerSegment]`
- `Translator.translate(text, target_language) -> str`
- `TTSProvider.synthesize(text, voice_id, language) -> AudioSegment`
- `Aligner.align(text, target_audio, target_start, target_end) -> AlignmentMap`
- `AudioMixer.mix(original, dubbed, output_path) -> None`
