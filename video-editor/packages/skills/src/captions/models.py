"""Data models for the Captions skill."""

from __future__ import annotations

from typing import Any, Literal

from graphics.models import Brand
from pydantic import BaseModel, Field


class CaptionsError(RuntimeError):
    """Raised when the Captions skill cannot produce a captioned output.

    Used to fail loudly on a degenerate transcript (0 words) instead of
    silently emitting the graphics-only output, which would otherwise look
    like a successful captions stage while being byte-identical to the
    upstream ``with_graphics.mp4`` (Issue C).
    """


class CaptionWord(BaseModel):
    """A single word with its timing relative to the cut timeline."""

    text: str
    start: float
    end: float
    confidence: float = 1.0


class CaptionLine(BaseModel):
    """A line of captions composed of one or more words."""

    words: list[CaptionWord] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def start(self) -> float:
        return self.words[0].start if self.words else 0.0

    @property
    def end(self) -> float:
        return self.words[-1].end if self.words else 0.0


class CaptionStyle(BaseModel):
    """Styling and animation settings for captions."""

    # Defaults are the karaoke look (Montserrat ExtraBold, soft shadow, short
    # lines, per-word highlight with dimmed read-ahead). The pre-2026-10-22
    # Coolvetica pop-in look is ``CaptionStyle.preset("pop_in")``.
    font: str = "Montserrat"
    font_size: int = 58
    font_color: str = "#FFFFFF"
    border_width: int = 4
    border_color: str = "#000000"
    animation: str = "karaoke"
    max_chars: int = 34
    max_duration: float = 2.6
    max_gap: float = 0.35
    # When > 0, captions are pulled down so that the band sits
    # `bottom_padding` pixels above the bottom edge of the canvas.
    # The preset's `layout.captions` rect provides the band height;
    # this field only overrides the y-coordinate. Default 0 preserves
    # the preset-defined position (backwards compatible).
    bottom_padding: int = 0
    # Move the caption band inside the platform safe area
    # (``video_editor_core.safe_area``) unless ``bottom_padding`` places it.
    respect_safe_area: bool = True
    font_weight: int = 800
    # "stroke" outlines glyphs (legacy look); "shadow" uses a soft drop shadow
    # that keeps thin letters intact; "halo" is a soft light glow for dark text
    # on light backdrops (``captions.theme``). Honoured by the HyperFrames renderer.
    text_effect: Literal["stroke", "shadow", "halo"] = "shadow"
    # With ``animation="karaoke"`` each word takes this colour while spoken.
    highlight_color: str = "#FDB813"
    # Seconds a line stays up after its last word (never past the next line).
    linger: float = 0.15
    # Karaoke only: opacity of words not yet spoken (1.0 = no dimming). Words
    # step to full opacity as they are reached, so the eye tracks the voice.
    upcoming_opacity: float = Field(default=0.6, ge=0.0, le=1.0)

    @classmethod
    def preset(cls, name: str, **overrides: Any) -> CaptionStyle:
        """Return a named style preset, optionally overriding fields."""
        if name not in CAPTION_PRESETS:
            raise ValueError(f"unknown caption preset {name!r}; valid: {sorted(CAPTION_PRESETS)}")
        return cls(**{**CAPTION_PRESETS[name], **overrides})

    @classmethod
    def from_brand(cls, brand: Brand) -> CaptionStyle:
        """Build a CaptionStyle from a Brand profile.

        Reads from the brand's ``fonts.captions``, ``colors.text``,
        and ``defaults.caption_animation`` paths. Falls back to the
        class defaults if the brand is missing a key.
        """
        animation = brand.defaults.get("caption_animation", cls.model_fields["animation"].default)
        base = CAPTION_PRESETS.get(animation, {})
        return cls(
            **{
                **base,
                "font": brand.fonts.get(
                    "captions", base.get("font", cls.model_fields["font"].default)
                ),
                "font_color": brand.colors.get("text", cls.model_fields["font_color"].default),
                "animation": animation,
            }
        )


# Named presets; a brand whose ``caption_animation`` matches a preset name
# starts from that preset (brand font/colour still win).
CAPTION_PRESETS: dict[str, dict[str, Any]] = {
    "karaoke": {
        "font": "Montserrat",
        "font_size": 58,
        "font_weight": 800,
        "text_effect": "shadow",
        "animation": "karaoke",
        "highlight_color": "#FDB813",
        "max_chars": 34,
        "max_duration": 2.6,
        "max_gap": 0.35,
        "linger": 0.15,
        "upcoming_opacity": 0.6,
    },
    # The original default: Coolvetica outlined lines that pop in whole.
    "pop_in": {
        "font": "Coolvetica",
        "font_size": 48,
        "font_weight": 700,
        "text_effect": "stroke",
        "animation": "pop_in",
        "max_chars": 36,
        "max_duration": 4.0,
        "max_gap": 0.3,
        "linger": 0.0,
        "upcoming_opacity": 1.0,
    },
}


class CaptionManifest(BaseModel):
    """Record of captions applied to a job."""

    job_id: str
    style: CaptionStyle = Field(default_factory=CaptionStyle)
    segment_ids: list[str] = Field(default_factory=list)
    output_video: str = ""
    # Advisory, non-blocking caption-quality flags produced by the optional
    # Jev classifier (long lines, profanity, emphasis candidates). Empty when
    # no classifier is available; never affects caption timing or rendering.
    quality_flags: list[dict[str, Any]] = Field(default_factory=list)
