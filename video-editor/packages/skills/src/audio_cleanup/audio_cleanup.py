"""Audio cleanup skill.

Applies optional source separation (Demucs-style), neural/FFT denoising,
and loudness normalization to a speech audio track. The skill is designed to
run as an optional pre-stage before ``RoughCutSkill`` so downstream
transcription and captioning see cleaner audio.

Because ``demucs`` and ``pyrnnoise`` are heavy optional dependencies, the
skill falls back to pure-FFmpeg processing when they are not installed:

- ``demucs`` → FFmpeg ``afftdn`` + ``highpass`` fallback
- ``pyrnnoise`` → FFmpeg ``arnndn`` if available, otherwise ``afftdn``

The cleaned output is always normalized with EBU R128 loudnorm.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import structlog
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_ffmpeg.run import (
    PROBE_TIMEOUT_SECONDS,
    CommandError,
    run_ffmpeg,
    run_subprocess,
)
from video_editor_state.job import JobContext

from audio_cleanup.models import AudioCleanupConfig, AudioCleanupManifest

logger = structlog.get_logger(__name__)

DEFAULT_SAMPLE_RATE = 48000
DEFAULT_LOUDNESS_TARGET_LUFS = -16.0
DEFAULT_TRUE_PEAK_DBFS = -1.5
DEFAULT_LOUDNESS_RANGE_LU = 11.0


class AudioCleanupError(RuntimeError):
    """Raised when audio cleanup fails in a non-recoverable way."""


class AudioCleanupSkill:
    """Clean and normalize a speech audio track."""

    @property
    def name(self) -> str:
        return "audio_cleanup"

    def __init__(
        self,
        ctx: JobContext,
        config: AudioCleanupConfig | None = None,
        render_options: RenderOptions | None = None,
    ):
        self.ctx = ctx
        self.config = config or AudioCleanupConfig()
        self.render_options = render_options or RenderOptions()
        self.output_dir = ctx.work_dir / "audio_cleanup"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.output_dir / "audio_cleanup.json"

    def _has_ffmpeg_filter(self, filter_name: str) -> bool:
        """Return True when the installed FFmpeg exposes ``filter_name``."""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return False
        result = run_ffmpeg(
            [ffmpeg, "-filters"],
            check=False,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
        return f" {filter_name}\n" in result.stdout or f"\n {filter_name} " in result.stdout

    def _run_demucs(self, input_path: Path, output_path: Path) -> Path:
        """Isolate vocals with Demucs when available.

        Falls back to an FFmpeg vocal-emphasis chain when Demucs is not
        installed or fails. The fallback is not a true separator but
        suppresses obvious low-frequency rumble and hiss.
        """
        demucs = shutil.which("demucs")
        if demucs is None:
            logger.info("demucs_not_found", fallback="ffmpeg")
            return self._ffmpeg_vocal_emphasis(input_path, output_path)

        try:
            # Demucs writes stems under a directory named after the input file.
            run_subprocess(
                [
                    demucs,
                    "--two-stems=vocals",
                    "--out",
                    str(self.output_dir),
                    str(input_path),
                ],
                check=True,
                timeout=600,
            )
            stem_dir = self.output_dir / input_path.stem
            vocal_path = stem_dir / "vocals.wav"
            if not vocal_path.exists():
                # Newer Demucs versions use a slightly different layout.
                candidates = list(stem_dir.rglob("vocals.wav"))
                if not candidates:
                    raise AudioCleanupError(
                        f"Demucs finished but vocals.wav not found under {stem_dir}"
                    )
                vocal_path = candidates[0]
            return vocal_path
        except (CommandError, FileNotFoundError) as exc:
            logger.warning("demucs_failed", error=str(exc))
            return self._ffmpeg_vocal_emphasis(input_path, output_path)

    def _ffmpeg_vocal_emphasis(self, input_path: Path, output_path: Path) -> Path:
        """Lightweight vocal emphasis fallback using FFmpeg.

        Chain: highpass (80 Hz) + lowpass (8 kHz) + afftdn. This is not
        source separation but removes rumble, hiss, and broad-band noise so
        transcription quality stays acceptable.
        """
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-af",
            "highpass=f=80,lowpass=f=8000,afftdn=nr=18:nf=-60",
            "-ar",
            str(DEFAULT_SAMPLE_RATE),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
        run_ffmpeg(cmd)
        return output_path

    def _run_denoise(self, input_path: Path, output_path: Path) -> Path:
        """Apply neural or FFT denoising.

        Preference order:
        1. FFmpeg ``arnndn`` (built-in RNNoise wrapper)
        2. FFmpeg ``afftdn`` (FFT denoiser, always available)
        """
        if self._has_ffmpeg_filter("arnndn"):
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-af",
                "arnndn=m=0.85",
                "-ar",
                str(DEFAULT_SAMPLE_RATE),
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(output_path),
            ]
            run_ffmpeg(cmd)
            return output_path

        logger.info("arnndn_unavailable", fallback="afftdn")
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-af",
            "afftdn=nr=20:nf=-55",
            "-ar",
            str(DEFAULT_SAMPLE_RATE),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
        run_ffmpeg(cmd)
        return output_path

    def _run_normalize(self, input_path: Path, output_path: Path) -> Path:
        """Normalize with EBU R128 loudnorm and write to ``output_path``."""
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-af",
            (
                f"loudnorm=I={self.config.loudness_target_lufs}:"
                f"TP={self.config.true_peak_dbfs}:"
                f"LRA={self.config.loudness_range_lu}"
            ),
            "-ar",
            str(DEFAULT_SAMPLE_RATE),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
        run_ffmpeg(cmd)
        return output_path

    def _video_to_audio(self, video_path: Path, audio_path: Path) -> Path:
        """Extract a mono WAV from a video file."""
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ac",
            "1",
            "-ar",
            str(DEFAULT_SAMPLE_RATE),
            str(audio_path),
        ]
        run_ffmpeg(cmd)
        return audio_path

    def _replace_video_audio(self, video_path: Path, audio_path: Path, output_path: Path) -> Path:
        """Mux the cleaned audio back into the original video."""
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-c:v",
            "copy",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(output_path),
        ]
        run_ffmpeg(cmd)
        return output_path

    def run(
        self,
        input_path: Path | str,
        output_name: str = "cleaned.wav",
        return_video: bool = False,
    ) -> Path:
        """Clean ``input_path`` and write the cleaned audio or video.

        Args:
            input_path: Audio file or video file to clean.
            output_name: Filename for the cleaned output under
                ``audio_cleanup/``.
            return_video: If True and ``input_path`` is a video, mux the
                cleaned audio back into the original video.

        Returns:
            Path to the cleaned output.
        """
        self.ctx.ensure_dirs()
        input_path = Path(input_path)
        if not input_path.exists():
            raise FileNotFoundError(f"Audio cleanup input not found: {input_path}")

        info = probe_media(input_path)
        is_video = info.video_stream is not None
        audio_path = self.output_dir / "extracted.wav"

        if is_video:
            self._video_to_audio(input_path, audio_path)
        else:
            # Copy/resample input audio to the working file.
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-vn",  # harmless for audio-only files
                "-acodec",
                "pcm_s16le",
                "-ac",
                "1",
                "-ar",
                str(DEFAULT_SAMPLE_RATE),
                str(audio_path),
            ]
            run_ffmpeg(cmd)

        stages_applied: list[str] = []
        working = audio_path

        if self.config.isolate_voice:
            vocals_path = self.output_dir / "vocals.wav"
            self._run_demucs(working, vocals_path)
            working = vocals_path
            stages_applied.append("isolate_voice")

        if self.config.denoise:
            denoised_path = self.output_dir / "denoised.wav"
            self._run_denoise(working, denoised_path)
            working = denoised_path
            stages_applied.append("denoise")

        if self.config.normalize:
            normalized_path = self.output_dir / "normalized.wav"
            self._run_normalize(working, normalized_path)
            working = normalized_path
            stages_applied.append("normalize")

        cleaned_audio = self.output_dir / output_name
        shutil.copyfile(working, cleaned_audio)

        output: Path
        if return_video and is_video:
            output = self.output_dir / output_name.replace(".wav", ".mp4")
            self._replace_video_audio(input_path, cleaned_audio, output)
        else:
            output = cleaned_audio

        duration_ms = probe_media(output).duration_ms()
        manifest = AudioCleanupManifest(
            job_id=self.ctx.job_id,
            input_audio=str(input_path),
            cleaned_audio=str(output),
            config=self.config,
            stages_applied=stages_applied,
            metadata={"duration_ms": duration_ms},
        )
        self.manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

        self.ctx.manifest().stage_done(
            "audio_cleanup",
            {"cleaned_audio": str(output), "stages": stages_applied},
        )
        self.ctx.store.save()

        logger.info(
            "audio_cleanup_complete",
            job_id=self.ctx.job_id,
            output=str(output),
            stages=stages_applied,
        )
        return output
