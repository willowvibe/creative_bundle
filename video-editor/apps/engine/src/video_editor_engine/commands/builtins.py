"""Built-in command registrations.

A deliberately small, safe, side-effect-light slice of existing engine
capabilities, exposed as typed commands. These are demonstrations of the
registry contract (and useful agent introspection helpers) rather than heavy
pipeline runs — no renders, no provider API calls, no network.

Registering here is import-time; the underlying modules are imported lazily
inside each handler so importing :mod:`video_editor_engine.commands` stays cheap
and free of optional-dependency surprises.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from video_editor_engine.commands.registry import register_command

__all__ = [
    "PricingCheapestParams",
    "ProbeMediaParams",
    "TicksInfoParams",
]


class ProbeMediaParams(BaseModel):
    """Inputs for ``probe.media``."""

    path: str = Field(description="Path to the media file to probe with ffprobe.")


@register_command(
    id="probe.media",
    summary="Probe a media file for duration, dimensions, fps and codecs.",
    category="media",
    params_model=ProbeMediaParams,
)
def _probe_media(params: ProbeMediaParams) -> dict[str, Any]:
    from video_editor_ffmpeg.probe import probe_media

    info = probe_media(params.path)
    return {
        "path": str(info.path),
        "duration": info.duration,
        "width": info.width,
        "height": info.height,
        "fps": info.fps,
        "has_audio": info.has_audio,
        "video_codec": (info.video_stream or {}).get("codec_name", ""),
        "audio_codec": (info.audio_stream or {}).get("codec_name", ""),
    }


class EmptyParams(BaseModel):
    """No parameters."""


@register_command(
    id="templates.list",
    summary="List every supported video template and its required inputs.",
    category="templates",
    params_model=EmptyParams,
)
def _templates_list(params: EmptyParams) -> dict[str, Any]:
    from video_editor_core.templates import TemplateRouter

    router = TemplateRouter()
    return {"templates": router.list_templates(), "count": len(router.template_names)}


class PricingCheapestParams(BaseModel):
    """Inputs for ``pricing.cheapest``."""

    model: str = Field(description="Canonical model id, e.g. 'gpt-image-2'.")
    asset_type: str = Field(
        description=(
            "Asset type to compare on: image, data_viz, video, music, sfx, "
            "tts, alignment, isolation, or speech_to_speech."
        )
    )


@register_command(
    id="pricing.cheapest",
    summary="Return the cheapest provider for a model + asset type.",
    category="pricing",
    params_model=PricingCheapestParams,
)
def _pricing_cheapest(params: PricingCheapestParams) -> dict[str, Any]:
    from video_editor_asset_ingestion.pricing import PricingRegistry

    registry = PricingRegistry.load()
    best = registry.cheapest_provider(params.model, params.asset_type)
    if best is None:
        return {
            "found": False,
            "model": params.model,
            "asset_type": params.asset_type,
        }
    provider, provider_model, unit_cost = best
    return {
        "found": True,
        "model": params.model,
        "asset_type": params.asset_type,
        "provider": provider,
        "provider_model": provider_model,
        "unit_cost": unit_cost,
        "currency": "USD",
    }


class TicksInfoParams(BaseModel):
    """Inputs for ``ticks.info``."""

    seconds: float = Field(description="Timeline position in seconds.", ge=0.0)
    fps: float = Field(default=30.0, description="Frame rate to convert against.", gt=0.0)


@register_command(
    id="ticks.info",
    summary="Convert seconds to integer ticks and frames on the exact tick base.",
    category="timeline",
    params_model=TicksInfoParams,
)
def _ticks_info(params: TicksInfoParams) -> dict[str, Any]:
    from video_editor_core.ticks import (
        TICKS_PER_SECOND,
        frames_for_ticks,
        seconds_to_ticks,
        snap_to_frame,
        ticks_for_frames,
    )

    ticks = seconds_to_ticks(params.seconds)
    frames = frames_for_ticks(ticks, params.fps)
    return {
        "seconds": params.seconds,
        "fps": params.fps,
        "ticks_per_second": TICKS_PER_SECOND,
        "ticks": ticks,
        "frames": frames,
        "ticks_per_frame": ticks_for_frames(1, params.fps),
        "snapped_seconds": snap_to_frame(params.seconds, params.fps),
    }
