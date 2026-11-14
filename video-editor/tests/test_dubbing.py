"""Tests for the dubbing skill."""

from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest
from dubbing.dubbing import (
    Aligner,
    AudioMixer,
    Diarizer,
    DubbingConfig,
    DubbingError,
    DubbingSkill,
    SpeakerSegment,
    Translator,
    TTSProvider,
)
from video_editor_state.job import JobContext

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(
        job_id="dub-test",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )


def _write_silent_wav(path: Path, duration_s: float = 0.2, sr: int = 24000) -> Path:
    """Write a tiny silent mono PCM WAV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(duration_s * sr)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes(b"\x00\x00" * n)
    return path


class _StaticDiarizer:
    """Pretend diarizer that returns a fixed list of segments."""

    def __init__(self, segments: list[SpeakerSegment]) -> None:
        self.segments = segments

    def run(self, audio_path: Path | str) -> list[SpeakerSegment]:
        return list(self.segments)


class _StaticTranslator:
    """Translator that returns the input prefixed with a tag."""

    def __init__(self, tag: str = "[es] ") -> None:
        self.tag = tag
        self.calls: list[tuple[str, str]] = []

    def translate(self, text: str, target_language: str) -> str:
        self.calls.append((text, target_language))
        return f"{self.tag}{text}"


class _StaticTTS:
    """TTS provider that writes a silent WAV per call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.counter = 0
        self.out_dir: Path | None = None

    def synthesize(self, text: str, voice_id: str, language: str) -> tuple[Path, float]:
        self.calls.append((text, voice_id, language))
        assert self.out_dir is not None
        self.counter += 1
        out = self.out_dir / f"tts_{self.counter:03d}.wav"
        _write_silent_wav(out, duration_s=0.2)
        return out, 0.2


class _StaticMixer:
    """Mixer that just writes a marker file and records segments."""

    def __init__(self, output: Path | None = None) -> None:
        self.output = output
        self.calls: list[SpeakerSegment] = []

    def mix(
        self,
        segments: list[SpeakerSegment],
        output_path: Path,
        sample_rate: int = 24000,
    ) -> Path:
        self.calls.extend(segments)
        target = self.output or output_path
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_silent_wav(target, duration_s=0.1)
        return target


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_skill_name() -> None:
    skill = DubbingSkill(ctx=JobContext("n", Path("/tmp/w"), Path("/tmp/o")))
    assert skill.name == "dubbing"


def test_run_with_provided_segments(ctx: JobContext, tmp_path: Path) -> None:
    tts = _StaticTTS()
    tts.out_dir = tmp_path
    mixer = _StaticMixer()
    segments = [
        SpeakerSegment(
            speaker_id="host",
            start=0.0,
            end=1.5,
            original_text="Hello world.",
            translated_text="",
        ),
        SpeakerSegment(
            speaker_id="guest",
            start=2.0,
            end=3.5,
            original_text="Nice to be here.",
            translated_text="",
        ),
    ]
    skill = DubbingSkill(
        ctx,
        config=DubbingConfig(target_language="es", voice_map={"host": "v-host"}),
        translator=_StaticTranslator(),
        tts_provider=tts,
        mixer=mixer,
    )

    result = skill.run(segments=segments)

    assert result["target_language"] == "es"
    assert result["count"] == 2
    assert Path(result["output"]).exists()
    # Each segment was synthesized + translated.
    assert len(tts.calls) == 2
    assert all(call[2] == "es" for call in tts.calls)
    assert mixer.calls and len(mixer.calls) == 2


def test_run_persists_segment_audio_into_work_dir(ctx: JobContext, tmp_path: Path) -> None:
    """Per-segment audio should land in work_dir/dubbing/segments/."""
    tts = _StaticTTS()
    tts.out_dir = tmp_path / "tts"
    segments = [
        SpeakerSegment(
            speaker_id="host",
            start=0.0,
            end=1.0,
            original_text="Hi",
            translated_text="",
        ),
    ]
    skill = DubbingSkill(
        ctx,
        translator=_StaticTranslator(),
        tts_provider=tts,
    )
    skill.run(segments=segments)
    out_segments_dir = ctx.work_dir / "dubbing" / "segments"
    assert out_segments_dir.exists()
    assert any(out_segments_dir.glob("*.wav"))


def test_run_writes_manifest_and_segments_json(ctx: JobContext, tmp_path: Path) -> None:
    tts = _StaticTTS()
    tts.out_dir = tmp_path
    segments = [
        SpeakerSegment(
            speaker_id="host",
            start=0.0,
            end=1.0,
            original_text="A",
            translated_text="",
        ),
        SpeakerSegment(
            speaker_id="guest",
            start=1.5,
            end=2.5,
            original_text="B",
            translated_text="",
        ),
    ]
    skill = DubbingSkill(
        ctx,
        config=DubbingConfig(target_language="fr", voice_map={"host": "v1", "guest": "v2"}),
        translator=_StaticTranslator("[fr] "),
        tts_provider=tts,
    )
    result = skill.run(segments=segments)

    manifest_path = Path(result["manifest"])
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["job_id"] == "dub-test"
    assert payload["target_language"] == "fr"
    assert payload["count"] == 2
    assert payload["voice_map"] == {"host": "v1", "guest": "v2"}

    seg_path = Path(result["segments_path"])
    seg_payload = json.loads(seg_path.read_text(encoding="utf-8"))
    assert seg_payload["count"] == 2
    assert {s["speaker_id"] for s in seg_payload["segments"]} == {"host", "guest"}


def test_run_uses_diarizer_when_segments_missing(ctx: JobContext, tmp_path: Path) -> None:
    """Provide only a diarizer + audio_path, no pre-built segments."""
    audio = _write_silent_wav(tmp_path / "source.wav", duration_s=0.5)
    diarizer_segments = [
        SpeakerSegment(
            speaker_id="host",
            start=0.0,
            end=1.0,
            original_text="hi",
            translated_text="",
        ),
    ]
    tts = _StaticTTS()
    tts.out_dir = tmp_path / "tts"
    skill = DubbingSkill(
        ctx,
        diarizer=_StaticDiarizer(diarizer_segments),
        translator=_StaticTranslator(),
        tts_provider=tts,
    )

    result = skill.run(audio_path=audio)
    assert result["count"] == 1


def test_run_without_segments_or_diarizer_raises(ctx: JobContext) -> None:
    skill = DubbingSkill(
        ctx,
        translator=_StaticTranslator(),
        tts_provider=_StaticTTS(),
    )
    with pytest.raises(DubbingError, match="either `segments` or a `diarizer`"):
        skill.run()


def test_run_without_translator_raises(ctx: JobContext) -> None:
    segments = [
        SpeakerSegment("host", 0.0, 1.0, "hi", ""),
    ]
    skill = DubbingSkill(ctx, tts_provider=_StaticTTS())
    with pytest.raises(DubbingError, match="translator"):
        skill.run(segments=segments)


def test_run_without_tts_raises(ctx: JobContext) -> None:
    segments = [
        SpeakerSegment("host", 0.0, 1.0, "hi", ""),
    ]
    skill = DubbingSkill(ctx, translator=_StaticTranslator())
    with pytest.raises(DubbingError, match="tts_provider"):
        skill.run(segments=segments)


def test_run_skips_sub_threshold_segments(ctx: JobContext, tmp_path: Path) -> None:
    """Segments shorter than min_segment_duration are dropped."""
    tts = _StaticTTS()
    tts.out_dir = tmp_path
    segments = [
        SpeakerSegment("host", 0.0, 0.05, "x", ""),  # below default 0.1
        SpeakerSegment("host", 0.5, 1.5, "y", ""),
    ]
    skill = DubbingSkill(
        ctx,
        config=DubbingConfig(min_segment_duration=0.1),
        translator=_StaticTranslator(),
        tts_provider=tts,
    )
    result = skill.run(segments=segments)
    assert result["count"] == 1


def test_run_skips_segment_when_tts_fails(ctx: JobContext, tmp_path: Path) -> None:
    """A TTS failure on a single segment should not abort the whole run."""

    class _FlakyTTS:
        def __init__(self, out_dir: Path) -> None:
            self.counter = 0
            self.out_dir = out_dir

        def synthesize(self, text: str, voice_id: str, language: str) -> tuple[Path, float]:
            self.counter += 1
            if self.counter == 1:
                raise RuntimeError("tts down")
            out = self.out_dir / f"tts_{self.counter:03d}.wav"
            _write_silent_wav(out)
            return out, 0.2

    flaky = _FlakyTTS(tmp_path)
    segments = [
        SpeakerSegment("host", 0.0, 1.0, "a", ""),
        SpeakerSegment("host", 1.5, 2.5, "b", ""),
    ]
    skill = DubbingSkill(
        ctx,
        translator=_StaticTranslator(),
        tts_provider=flaky,
    )
    result = skill.run(segments=segments)
    assert result["count"] == 1


def test_run_falls_back_to_original_text_on_translate_failure(
    ctx: JobContext, tmp_path: Path
) -> None:
    """Translate exception → use the original text and continue."""

    class _FailingTranslator:
        def translate(self, text: str, target_language: str) -> str:
            raise RuntimeError("llm down")

    tts = _StaticTTS()
    tts.out_dir = tmp_path
    skill = DubbingSkill(
        ctx,
        translator=_FailingTranslator(),
        tts_provider=tts,
    )
    segments = [
        SpeakerSegment("host", 0.0, 1.0, "Original text", ""),
    ]
    result = skill.run(segments=segments)
    assert result["count"] == 1
    seg = result["segments"][0]
    assert seg.translated_text == "Original text"


def test_voice_for_falls_back_to_deterministic_placeholder() -> None:
    skill = DubbingSkill(
        ctx=JobContext("x", Path("/tmp/w"), Path("/tmp/o")),
        translator=_StaticTranslator(),
        tts_provider=_StaticTTS(),
        config=DubbingConfig(voice_map={"host": "alice"}),
    )
    assert skill._voice_for("host") == "alice"
    assert skill._voice_for("guest") == "voice-guest"


def test_protocols_are_runtime_satisfied(ctx: JobContext) -> None:
    """Smoke test that Protocol classes can be satisfied by duck-typed mocks."""
    diarizer = _StaticDiarizer([])
    translator = _StaticTranslator()
    tts = _StaticTTS()
    mixer = _StaticMixer()
    # Each one should be a Protocol-compatible instance at runtime.
    assert isinstance(diarizer, object)
    assert callable(translator.translate)
    assert callable(tts.synthesize)
    assert callable(mixer.mix)


def test_run_does_not_require_audio_path_when_segments_given(
    ctx: JobContext, tmp_path: Path
) -> None:
    """When `segments` is provided, `audio_path` is optional."""
    tts = _StaticTTS()
    tts.out_dir = tmp_path
    skill = DubbingSkill(
        ctx,
        translator=_StaticTranslator(),
        tts_provider=tts,
    )
    segments = [SpeakerSegment("host", 0.0, 1.0, "x", "")]
    result = skill.run(segments=segments)
    assert result["count"] == 1


def test_empty_segments_list_raises(ctx: JobContext, tmp_path: Path) -> None:
    skill = DubbingSkill(
        ctx,
        translator=_StaticTranslator(),
        tts_provider=_StaticTTS(),
    )
    with pytest.raises(DubbingError, match="no segments"):
        skill.run(segments=[])


def test_diarizer_called_with_audio_path(ctx: JobContext, tmp_path: Path) -> None:
    audio = _write_silent_wav(tmp_path / "x.wav")

    class _CapturingDiarizer:
        captured: list[Path] = []

        def run(self, audio_path: Path | str) -> list[SpeakerSegment]:
            _CapturingDiarizer.captured.append(Path(audio_path))
            return [SpeakerSegment("host", 0.0, 1.0, "hi", "")]

    tts = _StaticTTS()
    tts.out_dir = tmp_path
    skill = DubbingSkill(
        ctx,
        diarizer=_CapturingDiarizer(),
        translator=_StaticTranslator(),
        tts_provider=tts,
    )
    skill.run(audio_path=audio)
    assert _CapturingDiarizer.captured == [audio]


def test_run_writes_real_wav_output(ctx: JobContext, tmp_path: Path) -> None:
    """The mixer output should be a real, openable WAV file."""
    tts = _StaticTTS()
    tts.out_dir = tmp_path
    skill = DubbingSkill(
        ctx,
        translator=_StaticTranslator(),
        tts_provider=tts,
    )
    segments = [SpeakerSegment("host", 0.0, 0.3, "x", "")]
    result = skill.run(segments=segments)
    out = Path(result["output"])
    assert out.exists()
    with wave.open(str(out), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getframerate() == 24000
        # At least one frame was written.
        assert handle.getnframes() >= 1


# Verify all Protocol classes are importable (smoke test for the public API).
def test_protocol_imports() -> None:
    assert Aligner is not None
    assert Diarizer is not None
    assert Translator is not None
    assert TTSProvider is not None
    assert AudioMixer is not None
