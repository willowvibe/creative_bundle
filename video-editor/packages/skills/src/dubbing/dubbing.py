"""Dubbing skill: multi-speaker translation + TTS + audio mix.

The skill chains four subsystems:

1. **Diarization** — split the original transcript into per-speaker
   segments with start/end timestamps.
2. **Translation** — translate each segment's text to the target language.
3. **TTS** — synthesize the translated text into a target-language audio
   segment. Each speaker keeps their own voice ID.
4. **Forced alignment** — align the new audio to the original segment
   timing so the dubbed track fits the original video without drift.
5. **Mix** — combine the synthesized segments into a single audio track
   and (optionally) mix it back into the source video.

Every subsystem is injectable via a :class:`typing.Protocol` so tests can
pass deterministic mocks. Production code should wire real
implementations (e.g. ``pyannote.audio`` for diarization, an LLM router
for translation, ElevenLabs for TTS, the existing alignment provider
for forced alignment).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import structlog
from video_editor_state.job import JobContext

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SpeakerSegment:
    """A single diarized utterance with translated text + synthesized audio."""

    speaker_id: str
    start: float
    end: float
    original_text: str
    translated_text: str
    audio_path: Path | None = None
    voice_id: str | None = None


@dataclass
class DubbingConfig:
    """User-facing configuration for the dubbing run."""

    target_language: str = "es"
    source_language: str = "en"
    voice_map: dict[str, str] = field(default_factory=dict)
    output_format: str = "wav"
    mix_to_video: bool = False
    min_segment_duration: float = 0.1


class DubbingError(RuntimeError):
    """Raised when dubbing cannot proceed."""


# ---------------------------------------------------------------------------
# Subsystem protocols
# ---------------------------------------------------------------------------


class Diarizer(Protocol):
    """Splits an audio stream into per-speaker segments."""

    def run(self, audio_path: Path | str) -> list[SpeakerSegment]: ...


class Translator(Protocol):
    """Translates text into a target language."""

    def translate(self, text: str, target_language: str) -> str: ...


class TTSProvider(Protocol):
    """Synthesizes text into an audio segment."""

    def synthesize(self, text: str, voice_id: str, language: str) -> tuple[Path, float]:
        """Return (audio_path, duration_seconds)."""
        ...


class Aligner(Protocol):
    """Forces synthesized audio to match the original segment timing."""

    def align(
        self,
        text: str,
        audio_path: Path,
        target_start: float,
        target_end: float,
    ) -> dict[str, Any]: ...


class AudioMixer(Protocol):
    """Combines per-segment audio into a single track."""

    def mix(
        self,
        segments: list[SpeakerSegment],
        output_path: Path,
        sample_rate: int = 24000,
    ) -> Path: ...


# ---------------------------------------------------------------------------
# Default no-op mixer (so the skill works without an audio engine)
# ---------------------------------------------------------------------------


class _NullMixer:
    """Concatenate per-segment audio files into a single WAV/PCM track.

    Stdlib-only implementation (no numpy) that reads mono PCM WAV
    files produced by the TTS provider, time-aligns them by their
    ``start`` timestamp, and writes a single mono PCM WAV. It's
    intended for offline runs and tests; production code should wire
    a real mixer (e.g. one backed by ffmpeg's ``amix``).
    """

    SAMPLE_RATE = 24_000

    def mix(
        self,
        segments: list[SpeakerSegment],
        output_path: Path,
        sample_rate: int = 24_000,
        silence_padding: float = 0.05,
    ) -> Path:
        import array
        import math
        import wave

        output_path.parent.mkdir(parents=True, exist_ok=True)
        sr = sample_rate
        timeline: list[tuple[float, array.array[int]]] = []

        for seg in segments:
            if seg.audio_path is None or not Path(seg.audio_path).exists():
                continue
            try:
                with wave.open(str(seg.audio_path), "rb") as handle:
                    nchannels = handle.getnchannels()
                    sampwidth = handle.getsampwidth()
                    wav_sr = handle.getframerate()
                    raw = handle.readframes(handle.getnframes())
            except (wave.Error, EOFError, FileNotFoundError):
                continue
            if sampwidth != 2:
                # Null mixer only handles 16-bit PCM; skip non-conforming inputs.
                continue
            samples: array.array[int] = array.array("h")
            samples.frombytes(raw)
            if nchannels > 1:
                # Downmix to mono by averaging channels.
                mono = array.array(
                    "h",
                    [
                        int(sum(samples[i : i + nchannels]) / nchannels)
                        for i in range(0, len(samples), nchannels)
                    ],
                )
                samples = mono
            if wav_sr != sr:
                # Naive linear resample — good enough for null-mix tests.
                duration = len(samples) / wav_sr
                new_len = max(1, int(duration * sr))
                resampled: array.array[int] = array.array("h", [0] * new_len)
                for i in range(new_len):
                    src_idx = int((i / max(new_len - 1, 1)) * (len(samples) - 1))
                    resampled[i] = samples[src_idx]
                samples = resampled
            timeline.append((seg.start, samples))

        if not timeline:
            with wave.open(str(output_path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(sr)
                handle.writeframes(b"\x00\x00")
            return output_path

        end_time = max(seg.end for seg in segments) + silence_padding
        total_samples = int(math.ceil(end_time * sr))
        mix_samples = [0] * total_samples
        for start, samples in timeline:
            offset = int(round(start * sr))
            for i, value in enumerate(samples):
                idx = offset + i
                if idx >= total_samples:
                    break
                # Clip-safe summation — values stay within int16 range.
                mix_samples[idx] = max(-32_768, min(32_767, mix_samples[idx] + value))

        pcm = array.array("h", mix_samples).tobytes()
        with wave.open(str(output_path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sr)
            handle.writeframes(pcm)
        return output_path


class _NullAligner:
    """No-op aligner that returns passthrough timing."""

    def align(
        self,
        text: str,
        audio_path: Path,
        target_start: float,
        target_end: float,
    ) -> dict[str, Any]:
        return {
            "text": text,
            "audio_path": str(audio_path),
            "start": target_start,
            "end": target_end,
        }


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------


class DubbingSkill:
    """Translate, synthesize, and mix multi-speaker audio.

    All four subsystems are injectable. The bundled
    :class:`_NullMixer` + :class:`_NullAligner` provide a working
    end-to-end path for offline runs and tests; production callers
    should pass real implementations.
    """

    @property
    def name(self) -> str:
        return "dubbing"

    def __init__(
        self,
        ctx: JobContext,
        *,
        config: DubbingConfig | None = None,
        diarizer: Diarizer | None = None,
        translator: Translator | None = None,
        tts_provider: TTSProvider | None = None,
        aligner: Aligner | None = None,
        mixer: AudioMixer | None = None,
    ):
        self.ctx = ctx
        self.config = config or DubbingConfig()
        self.diarizer = diarizer
        self.translator = translator
        self.tts_provider = tts_provider
        self.aligner = aligner or _NullAligner()
        self.mixer = mixer or _NullMixer()
        self.output_dir = ctx.work_dir / "dubbing"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _voice_for(self, speaker_id: str) -> str:
        """Resolve the voice ID for a given speaker.

        Falls back to a deterministic placeholder if the config does not
        declare a mapping. Real callers should always supply a complete
        voice_map for production-quality output.
        """
        return self.config.voice_map.get(speaker_id, f"voice-{speaker_id}")

    def _persist_segment_audio(self, idx: int, speaker_id: str, audio_path: Path) -> Path:
        """Ensure per-segment audio is in the dubbing output directory."""
        target = self.output_dir / "segments" / f"{idx:03d}_{speaker_id}.wav"
        target.parent.mkdir(parents=True, exist_ok=True)
        if audio_path != target:
            data = Path(audio_path).read_bytes()
            target.write_bytes(data)
        return target

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(
        self,
        transcript: dict[str, Any] | None = None,
        segments: list[SpeakerSegment] | None = None,
        *,
        audio_path: Path | str | None = None,
    ) -> dict[str, Any]:
        """Translate, synthesize, and mix multi-speaker audio.

        Args:
            transcript: Optional dict with ``segments`` list of dicts
                containing ``text``, ``start``, ``end``, ``speaker``.
            segments: Optional pre-diarized segments (skip diarization).
            audio_path: Optional path to the source audio file. Required
                for full diarization; ignored if segments are passed.

        Returns:
            Dict with ``output_path``, ``segments`` (list of
            :class:`SpeakerSegment`), ``target_language``,
            ``count``, and ``manifest_path``.
        """
        self.ctx.ensure_dirs()

        # 1. Diarize or accept pre-built segments.
        if segments is None:
            if self.diarizer is None:
                raise DubbingError("either `segments` or a `diarizer` must be provided")
            if audio_path is None:
                raise DubbingError("audio_path is required for diarization")
            segments = self.diarizer.run(audio_path)
            logger.info("dubbing_diarized", count=len(segments))

        if not segments:
            raise DubbingError("no segments to dub")

        if self.translator is None:
            raise DubbingError("dubbing requires a translator")
        if self.tts_provider is None:
            raise DubbingError("dubbing requires a tts_provider")

        # 2. Translate + synthesize each segment.
        out_segments: list[SpeakerSegment] = []
        for idx, seg in enumerate(segments):
            if seg.end - seg.start < self.config.min_segment_duration:
                # Skip sub-threshold segments (typically breath/silence).
                continue
            voice_id = self._voice_for(seg.speaker_id)
            try:
                translated = self.translator.translate(
                    seg.original_text, self.config.target_language
                )
            except Exception as exc:  # noqa: BLE001 - best-effort cleanup
                logger.warning(
                    "dubbing_translate_failed",
                    speaker=seg.speaker_id,
                    error=str(exc),
                )
                translated = seg.original_text  # fallback

            try:
                audio_path, _ = self.tts_provider.synthesize(
                    translated, voice_id, self.config.target_language
                )
            except Exception as exc:  # noqa: BLE001 - best-effort cleanup
                logger.warning(
                    "dubbing_tts_failed",
                    speaker=seg.speaker_id,
                    error=str(exc),
                )
                continue

            persisted = self._persist_segment_audio(idx, seg.speaker_id, audio_path)
            aligned = self.aligner.align(translated, persisted, seg.start, seg.end)
            out_segments.append(
                SpeakerSegment(
                    speaker_id=seg.speaker_id,
                    start=seg.start,
                    end=seg.end,
                    original_text=seg.original_text,
                    translated_text=translated,
                    audio_path=persisted,
                    voice_id=voice_id,
                )
            )
            logger.info(
                "dubbing_segment_done",
                speaker=seg.speaker_id,
                start=seg.start,
                end=seg.end,
                aligned_start=aligned.get("start"),
                aligned_end=aligned.get("end"),
            )

        # 3. Mix into a single track.
        output_path = self.output_dir / "output.wav"
        try:
            self.mixer.mix(out_segments, output_path)
        except Exception as exc:
            raise DubbingError(f"audio mix failed: {exc!r}") from exc

        # 4. Persist manifest + per-segment JSON.
        segments_payload = [
            {
                "speaker_id": s.speaker_id,
                "start": s.start,
                "end": s.end,
                "original_text": s.original_text,
                "translated_text": s.translated_text,
                "voice_id": s.voice_id,
                "audio_path": str(s.audio_path) if s.audio_path else None,
            }
            for s in out_segments
        ]
        segments_path = self.output_dir / "speaker_segments.json"
        segments_path.write_text(
            json.dumps(
                {
                    "job_id": self.ctx.job_id,
                    "target_language": self.config.target_language,
                    "source_language": self.config.source_language,
                    "count": len(segments_payload),
                    "segments": segments_payload,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        manifest = {
            "job_id": self.ctx.job_id,
            "output_path": str(output_path),
            "segments_path": str(segments_path),
            "target_language": self.config.target_language,
            "source_language": self.config.source_language,
            "count": len(out_segments),
            "voice_map": dict(self.config.voice_map),
        }
        manifest_path = self.output_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        return {
            "output": str(output_path),
            "segments_path": str(segments_path),
            "manifest": str(manifest_path),
            "target_language": self.config.target_language,
            "count": len(out_segments),
            "segments": out_segments,
        }
