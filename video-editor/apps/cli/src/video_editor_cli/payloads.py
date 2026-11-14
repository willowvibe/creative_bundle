"""Typed Pydantic payload models for worker enqueue commands.

Every worker command has a corresponding payload schema so that
``ve enqueue`` validates inputs at enqueue time rather than at
dequeue time (Bug #79).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from video_editor_cli.paths import default_output_root, default_work_root

_DEFAULT_WORK_ROOT = default_work_root()
_DEFAULT_OUTPUT_ROOT = default_output_root()
_DEFAULT_PLAN_NAME = "scene_plan.json"
_DEFAULT_PLAN_OUT_NAME = "scene_plan_with_assets.json"


class RunPayload(BaseModel):
    """Payload schema for the ``run`` worker command."""

    source: str
    transcript_path: str | None = None
    config_path: str | None = None
    preset: str = "short_explainer"
    presets_dir: str | None = None
    music_path: str | None = None
    music_prompt: str = ""
    music_provider: str = "poyo_music"
    music_duration_seconds: int = 30
    # explainer_stills flags (Task 9). The worker branch reads these; the
    # defaults mirror the ones it assumes when a key is absent.
    auto_approve: bool = False
    no_reuse: bool = False
    tts_provider: str = "tts_router"
    image_provider: str = "auto"
    caption_preset: str = ""
    sfx_provider: str = ""
    sfx_duration_seconds: float = 5.0
    sfx_gain_db: float = -6.0
    output_name: str | None = None
    qa: bool = False
    loudness_target_lufs: float | None = None
    expected_aspect_ratio: str = "16:9"
    verify: bool = False
    template: str | None = None
    media_dir: str | None = None
    segment_seconds: float = 5.0
    lyrics: str | None = None
    language: str = "en"
    skip_hyperframes_lint: bool = False
    transition: str = "none"
    transition_duration: float = 0.3
    review: bool = True
    final: bool = False
    budget: float | None = None
    work_root: str = _DEFAULT_WORK_ROOT
    output_root: str = _DEFAULT_OUTPUT_ROOT
    # raw_to_video optional-stage flags (Task 10).
    audio_cleanup: bool = False
    transitions: bool = False
    reframe: bool = False
    broll: bool = False
    sfx: bool = False
    reframe_aspect: str = "9:16"

    @field_validator("segment_seconds")
    @classmethod
    def _validate_segment_seconds(cls, value: float) -> float:
        import math

        if not math.isfinite(value):
            raise ValueError("segment_seconds must be a finite number (not NaN or inf)")
        if value <= 0:
            raise ValueError("segment_seconds must be positive")
        return value

    @model_validator(mode="after")
    def _require_media_dir_for_musical_video(self) -> RunPayload:
        if self.template == "musical_video" and not self.media_dir:
            raise ValueError("'media_dir' is required for the musical_video template")
        return self

    @model_validator(mode="after")
    def _explainer_music_provider_default(self) -> RunPayload:
        """The explainer branch defaults music to PoYo, not ElevenLabs.

        ``ve enqueue`` validates without rewriting the payload, so the worker
        applies its own default. This keeps the schema's default honest for
        callers that construct a ``RunPayload`` directly, and leaves every
        other template's existing default untouched.
        """
        if "music_provider" not in self.model_fields_set and self.template == "explainer_stills":
            self.music_provider = "poyo_music"
        return self


class VerifyPayload(BaseModel):
    """Payload schema for the ``verify`` worker command."""

    work_root: str = _DEFAULT_WORK_ROOT
    output_root: str = _DEFAULT_OUTPUT_ROOT


class RoughCutPayload(BaseModel):
    source: str
    transcript_path: str | None = None
    work_root: str = _DEFAULT_WORK_ROOT
    output_root: str = _DEFAULT_OUTPUT_ROOT


class MusicGeneratePayload(BaseModel):
    prompt: str
    provider: str = "poyo_music"
    duration_seconds: int = 30
    config_path: str | None = None
    work_root: str = _DEFAULT_WORK_ROOT
    output_root: str = _DEFAULT_OUTPUT_ROOT


class SfxGeneratePayload(BaseModel):
    provider: str
    plan_name: str = _DEFAULT_PLAN_NAME
    duration_seconds: float = 5.0
    config_path: str | None = None
    work_root: str = _DEFAULT_WORK_ROOT
    output_root: str = _DEFAULT_OUTPUT_ROOT


class BriefPayload(BaseModel):
    from_md: str
    work_root: str = _DEFAULT_WORK_ROOT
    output_root: str = _DEFAULT_OUTPUT_ROOT


class AlignPayload(BaseModel):
    audio: str
    text: str
    provider: str = ""
    output_name: str = "alignment.json"
    config_path: str | None = None
    work_root: str = _DEFAULT_WORK_ROOT
    output_root: str = _DEFAULT_OUTPUT_ROOT


class AudioIsolatePayload(BaseModel):
    audio: str
    provider: str = ""
    output_name: str = "isolated.mp3"
    file_format: str = "other"
    config_path: str | None = None
    work_root: str = _DEFAULT_WORK_ROOT
    output_root: str = _DEFAULT_OUTPUT_ROOT


class SpeechToSpeechPayload(BaseModel):
    audio: str
    voice_id: str
    provider: str = ""
    model_id: str = "eleven_english_sts_v2"
    output_name: str = "converted.mp3"
    output_format: str = ""
    output_ext: str = ""
    remove_background_noise: bool = False
    seed: int = 0
    stability: float = 0.0
    similarity_boost: float = 0.0
    config_path: str | None = None
    work_root: str = _DEFAULT_WORK_ROOT
    output_root: str = _DEFAULT_OUTPUT_ROOT


class ExportComfyStudioPayload(BaseModel):
    """Payload schema for the ``export-comfystudio`` worker command."""

    plan_name: str = _DEFAULT_PLAN_NAME
    project_dir: str
    fps: int = 30
    width: int = 1920
    height: int = 1080
    music_path: str | None = None
    work_root: str = _DEFAULT_WORK_ROOT
    output_root: str = _DEFAULT_OUTPUT_ROOT


class ImportComfyStudioPayload(BaseModel):
    """Payload schema for the ``import-comfystudio`` worker command."""

    project_dir: str
    plan_name: str = _DEFAULT_PLAN_NAME
    plan_out_name: str = _DEFAULT_PLAN_OUT_NAME
    work_root: str = _DEFAULT_WORK_ROOT
    output_root: str = _DEFAULT_OUTPUT_ROOT


class ExportFableCutPayload(BaseModel):
    """Payload schema for the ``export-fablecut`` worker command."""

    plan_name: str = _DEFAULT_PLAN_NAME
    project_dir: str
    fps: int = 30
    width: int = 1920
    height: int = 1080
    music_path: str | None = None
    lyrics_path: str | None = None
    work_root: str = _DEFAULT_WORK_ROOT
    output_root: str = _DEFAULT_OUTPUT_ROOT


class StorytellerExportPayload(BaseModel):
    """Payload schema for the ``storyteller-export`` worker command."""

    job_id: str
    project_dir: str
    plan_name: str = "scene_plan.json"
    aspect_ratio: str = "16:9"


class ImportFableCutPayload(BaseModel):
    """Payload schema for the ``import-fablecut`` worker command."""

    project_dir: str
    plan_name: str = _DEFAULT_PLAN_NAME
    plan_out_name: str = _DEFAULT_PLAN_OUT_NAME
    work_root: str = _DEFAULT_WORK_ROOT
    output_root: str = _DEFAULT_OUTPUT_ROOT


class RenderFableCutPayload(BaseModel):
    """Payload schema for the ``render-fablecut`` worker command."""

    project_dir: str
    output_name: str = "final.mp4"
    font: str | None = None
    fps: int | None = None
    width: int | None = None
    height: int | None = None
    config_path: str | None = None
    work_root: str = _DEFAULT_WORK_ROOT
    output_root: str = _DEFAULT_OUTPUT_ROOT


class ShortPayload(BaseModel):
    """Payload schema for the ``short`` worker command (#303).

    The job directory must already hold an approved ``plan/cost_estimate.json``
    for this pack; the worker verifies it and never approves spend itself.
    """

    pack: str
    final: bool = False
    config_path: str | None = None
    work_root: str = _DEFAULT_WORK_ROOT
    output_root: str = _DEFAULT_OUTPUT_ROOT


class MusicalPayload(BaseModel):
    """Payload schema for the ``musical`` worker command (#303)."""

    music: str
    media_dir: str
    lyrics: str
    output_name: str = "final.mp4"
    width: int = 1920
    height: int = 1080
    fps: int = 30
    segment_seconds: float = 8.0
    transition_seconds: float = 1.5
    qa: bool = True
    quantize: str = "off"
    beat_tolerance: float = 0.12
    swells: list[list[float]] = Field(default_factory=list)
    config_path: str | None = None
    work_root: str = _DEFAULT_WORK_ROOT
    output_root: str = _DEFAULT_OUTPUT_ROOT


# Registry mapping command names to their payload schemas.
PAYLOAD_SCHEMAS: dict[str, type[BaseModel]] = {
    "run": RunPayload,
    "short": ShortPayload,
    "musical": MusicalPayload,
    "verify": VerifyPayload,
    "rough_cut": RoughCutPayload,
    "music_generate": MusicGeneratePayload,
    "sfx_generate": SfxGeneratePayload,
    "brief": BriefPayload,
    "align": AlignPayload,
    "audio_isolate": AudioIsolatePayload,
    "speech_to_speech": SpeechToSpeechPayload,
    "export_comfystudio": ExportComfyStudioPayload,
    "import_comfystudio": ImportComfyStudioPayload,
    "export_fablecut": ExportFableCutPayload,
    "storyteller_export": StorytellerExportPayload,
    "import_fablecut": ImportFableCutPayload,
    "render_fablecut": RenderFableCutPayload,
}
