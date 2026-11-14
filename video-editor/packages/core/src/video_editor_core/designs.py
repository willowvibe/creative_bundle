"""Design tokens for typographic scenes.

A design is one JSON file under ``designs/`` at the repo root::

    {"name": "cream_editorial",
     "palette": {"bg": "#EFEAE3", "ink": "#141210",
                 "accent": "#C8102E", "muted": "#8A8378"},
     "type": {"display": "Montserrat", "body": "Inter", "display_weight": 700},
     "theme": "light",
     "motion": {"tempo": 1.0, "entry": "rise", "drift": 1.06}}

The data lives next to ``presets/``; this loader resolves it from the package
location, exactly as ``video_editor_core.fonts`` resolves its font files, so
core never has to import the config package.

Fonts are restricted to :data:`video_editor_core.fonts.BUNDLED_FONTS`. A design
naming "Playfair Display" would render with whatever Chrome finds installed —
different on every machine — so an unbundled family is a validation error, not
a silent substitution.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from video_editor_core.fonts import BUNDLED_FONTS

DEFAULT_DESIGN = "cream_editorial"

#: ``packages/core/src/video_editor_core/designs.py`` -> repo root.
DESIGNS_DIR = Path(__file__).resolve().parents[4] / "designs"

_NAME_RE = re.compile(r"^[a-z0-9_]+$")


class DesignError(ValueError):
    """A design that cannot be loaded or is not a valid token set."""


class Palette(BaseModel):
    bg: str
    ink: str
    accent: str
    muted: str


class TypeScale(BaseModel):
    display: str = "Montserrat"
    body: str = "Inter"
    display_weight: int = Field(default=700, ge=100, le=900)

    @model_validator(mode="after")
    def _fonts_are_bundled(self) -> TypeScale:
        for family in (self.display, self.body):
            if family not in BUNDLED_FONTS:
                raise ValueError(
                    f"font {family!r} is not bundled; choose from {sorted(BUNDLED_FONTS)}"
                )
        return self


class Motion(BaseModel):
    tempo: float = Field(default=1.0, gt=0.2, le=3.0)
    entry: str = "rise"
    drift: float = Field(default=1.06, gt=1.0, le=1.30)


class Layers(BaseModel):
    """Layer styling for the Composition compiler; every field is optional.

    Series identity lives here rather than in per-video Python: a design names
    the safe-area profile, the scrim/pill/end-card colours and the caption
    preset, and the content pack supplies only words and assets.

    Unknown keys are allowed so a design can carry a new preset before the
    compiler learns about it.
    """

    model_config = ConfigDict(extra="allow")

    safe_area: str = ""
    scrim: dict[str, Any] = Field(default_factory=dict)
    chip: dict[str, Any] = Field(default_factory=dict)
    pill: dict[str, Any] = Field(default_factory=dict)
    #: The karaoke caption band: font, size, highlight colour and box. Named
    #: rather than left to ``extra`` so the compiler reads a typed contract.
    caption_line: dict[str, Any] = Field(default_factory=dict)
    end_card: dict[str, Any] = Field(default_factory=dict)
    caption_preset: str = "karaoke"


#: The presets the installed HyperFrames runtime knows. Kept here rather than
#: shelled out at compile time so a design is validated offline.
GRADE_PRESETS: frozenset[str] = frozenset(
    {
        "neutral",
        "warm-daylight",
        "clean-studio",
        "skin-soft",
        "food-pop",
        "night-lift",
        "muted-editorial",
        "vintage-wash",
        "mono-clean",
        "mono-fade",
        "soft-boost",
        "bright-pop",
        "deep-contrast",
        "creator-camcorder",
        "vhs-playback",
        "home-movie-8mm",
        "editorial-halftone",
        "two-ink-print",
    }
)

#: The tonal controls ``data-color-grading`` reads. ``effects`` is intentionally
#: open: the runtime owns a large optical/glitch catalogue and ignores keys it
#: does not know, so pinning them here would block a newer runtime for no gain.
_GRADE_ADJUST_KEYS: frozenset[str] = frozenset(
    {
        "exposure",
        "contrast",
        "highlights",
        "shadows",
        "whites",
        "blacks",
        "temperature",
        "tint",
        "vibrance",
        "saturation",
    }
)
_GRADE_DETAIL_KEYS: frozenset[str] = frozenset(
    {
        "vignette",
        "vignetteMidpoint",
        "vignetteRoundness",
        "vignetteFeather",
        "grain",
        "grainSize",
        "grainRoughness",
    }
)


class LightLeak(BaseModel):
    """A seek-safe radial light leak placed at a scene cut.

    ``placement`` is the video-level policy: one leak at the first cut, one at
    the last content cut, or both. The overlay is a full-canvas ``html`` layer
    with a CSS keyframe on opacity and transform, so a seek reproduces it.
    """

    model_config = ConfigDict(extra="forbid")

    placement: Literal["off", "first_cut", "last_cut", "both"] = "off"
    intensity: float = Field(default=0.5, ge=0.0, le=1.0)
    duration: float = Field(default=0.9, gt=0.0, le=3.0)
    color: str = "#ffd7a1"

    @field_validator("color")
    @classmethod
    def _color_is_css_safe(cls, value: str) -> str:
        if not re.fullmatch(r"[#a-zA-Z0-9(),.% ]+", value):
            raise ValueError(f"light_leak.color is not a CSS colour: {value!r}")
        return value


class Grade(BaseModel):
    """A film-look grade emitted as ``data-color-grading`` on media layers.

    The sections stay sparse: the runtime merges them over its defaults, so a
    design states only what it changes. ``lut`` is a ``.cube`` file the compiler
    copies into the project's ``assets/`` and points the runtime at.
    """

    model_config = ConfigDict(extra="forbid")

    preset: str = ""
    intensity: float = Field(default=1.0, ge=0.0, le=1.0)
    adjust: dict[str, float] = Field(default_factory=dict)
    details: dict[str, float] = Field(default_factory=dict)
    effects: dict[str, float] = Field(default_factory=dict)
    lut: str = ""
    lut_intensity: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _controls_are_known_and_finite(self) -> Grade:
        if self.preset and self.preset not in GRADE_PRESETS:
            raise ValueError(
                f"unknown grade preset {self.preset!r}; choose from "
                f"{', '.join(sorted(GRADE_PRESETS))}"
            )
        for name, allowed in (
            ("adjust", _GRADE_ADJUST_KEYS),
            ("details", _GRADE_DETAIL_KEYS),
        ):
            section = getattr(self, name)
            unknown = sorted(set(section) - allowed)
            if unknown:
                raise ValueError(
                    f"grade {name} has unknown key(s): {', '.join(unknown)}; "
                    f"known: {', '.join(sorted(allowed))}"
                )
        for name in ("adjust", "details", "effects"):
            for key, value in getattr(self, name).items():
                if not math.isfinite(value):
                    raise ValueError(f"grade {name}.{key} must be finite, got {value!r}")
        return self


class DesignTokens(BaseModel):
    """One design system: palette, type, theme, motion, layers and grade."""

    name: str
    palette: Palette
    type: TypeScale = Field(default_factory=TypeScale)
    theme: str = "light"
    motion: Motion = Field(default_factory=Motion)
    layers: Layers = Field(default_factory=Layers)
    grade: Grade | None = None
    light_leak: LightLeak = Field(default_factory=LightLeak)


def designs_dir() -> Path:
    """The directory holding the design JSON files."""
    return DESIGNS_DIR


def load_design(name: str = "", directory: Path | str | None = None) -> DesignTokens:
    """Load ``<directory>/<name>.json`` and validate it."""
    resolved = (name or DEFAULT_DESIGN).strip()
    if not _NAME_RE.match(resolved):
        raise DesignError(
            f"invalid design name {resolved!r}: use lowercase letters, digits and underscores"
        )
    root = Path(directory) if directory is not None else DESIGNS_DIR
    path = root / f"{resolved}.json"
    if not path.is_file():
        raise DesignError(f"unknown design {resolved!r}: {path} is missing")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DesignError(f"design {resolved!r} cannot be read: {exc}") from exc
    if not isinstance(data, dict):
        raise DesignError(f"design {resolved!r} must be a JSON object")
    data.setdefault("name", resolved)
    try:
        return DesignTokens.model_validate(data)
    except ValidationError as exc:
        raise DesignError(f"design {resolved!r} is invalid: {exc}") from exc
