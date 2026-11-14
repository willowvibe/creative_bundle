"""A committed content pack: one video's words and asset prompts.

A pack is the authoring surface for a short. It carries narration, on-screen
text, image prompts and the voice choice — never a colour, a font or a rect.
Styling lives in the series design (``designs/<name>.json``) and reaches the
compiler through :mod:`video_editor_core.layer_presets`.

The alignment models live here too because the adapter consumes both: an
:class:`Alignment` is the measured timeline the pack's words were spoken on.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from video_editor_core.motion import PRIMITIVES

#: Reserved scene id for the end card; a pack scene may not take it.
END_CARD_ID = "end_card"

#: The aspects ``canvas_for_aspect`` knows. Closed here because that helper
#: falls back to 16:9 for anything unrecognised: a pack typo would otherwise
#: render a vertical short as a 1920×1080 landscape file and pass every gate.
Aspect = Literal["9:16", "16:9", "1:1", "4:5", "4:3"]

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class ContentPackError(ValueError):
    """A pack that cannot be read or breaks the pack contract."""


class PackVoice(BaseModel):
    """Which voice reads the narration, and the beat between scene lines."""

    model_config = ConfigDict(extra="forbid")

    #: ElevenLabs is the default voice-over (2026-10-30); a pack can still name ``sarvam_tts`` explicitly.
    provider: str = "elevenlabs_tts"
    #: Provider-specific voice id: an ElevenLabs voice id (or ``elevenlabs:<stock name>``); Sarvam takes
    #: ``sarvam:<speaker>``.
    voice_id: str = Field(min_length=1)
    model: str = ""
    language: str = "en-IN"
    gap_seconds: float = Field(default=0.28, ge=0.0, le=2.0)


class PackMusic(BaseModel):
    """A local music bed mixed under the narration (#316)."""

    model_config = ConfigDict(extra="forbid")

    path: str = ""
    gain_db: float = -22.0
    #: ``[at_seconds, gain_db, duration_seconds]`` gain points.
    swells: list[list[float]] = Field(default_factory=list)


class PackCaptions(BaseModel):
    """How captions ship: burned into the render, or as a sidecar ``.srt``."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["burned", "sidecar"] = "burned"


class PackImage(BaseModel):
    """The still provider and the style suffix appended to every prompt."""

    model_config = ConfigDict(extra="forbid")

    provider: str = "poyo_ai"
    model: str = ""
    resolution: str = ""
    style_suffix: str = ""
    #: A style frame sent as a reference image with every generated still (#318). A path is
    #: relative to the pack file; a URL is used as is.
    style_reference: str = ""


class PackStill(BaseModel):
    """Either a still to generate (``key`` + ``prompt``) or an alias to one."""

    model_config = ConfigDict(extra="forbid")

    key: str = ""
    prompt: str = ""
    reuse: str = ""
    #: Characters in this still (names from the pack's ``characters``); their reference sheets
    #: go with the request so faces and wardrobe stay consistent (P4.8).
    characters: list[str] = Field(default_factory=list)
    #: Generate this still as an edit of another still's output (its ``key``), e.g. the same
    #: gate now open (#318). The prompt describes the change.
    edit_of: str = ""

    @model_validator(mode="after")
    def _generate_xor_reuse(self) -> PackStill:
        if self.reuse and (self.characters or self.edit_of):
            raise ValueError(f"still alias {self.reuse!r} cannot carry characters or edit_of")
        if bool(self.key) == bool(self.reuse):
            raise ValueError("a still needs exactly one of `key:` (generate) or `reuse:` (alias)")
        if self.key:
            if not _KEY_RE.match(self.key):
                raise ValueError(
                    f"still key {self.key!r} must be lowercase letters, digits and underscores"
                )
            if not self.prompt.strip():
                raise ValueError(f"still {self.key!r} has no prompt")
        elif self.prompt.strip():
            raise ValueError(f"still alias {self.reuse!r} cannot carry a prompt; reuse is free")
        return self


class PackLine(BaseModel):
    """One spoken line and, optionally, the words it puts on screen."""

    model_config = ConfigDict(extra="forbid")

    say: str = Field(min_length=1)
    show: str = ""
    cue: str = ""
    speak_as: str = ""
    #: Silence after this line, in seconds; 0 uses ``voice.gap_seconds`` (#312).
    pause_after: float = Field(default=0.0, ge=0.0, le=10.0)

    @model_validator(mode="after")
    def _spoken_is_not_blank(self) -> PackLine:
        if not self.spoken:
            raise ValueError("a line needs spoken text: `say:` or a non-blank `speak_as:`")
        return self

    @property
    def spoken(self) -> str:
        """What the voice reads: ``speak_as`` when set, else ``say``."""
        return (self.speak_as or self.say).strip()


class PackGraphic(BaseModel):
    """A chart/component rendered over the scene as static HTML (#311)."""

    model_config = ConfigDict(extra="forbid")

    component: str = Field(min_length=1)
    data: dict[str, Any] = Field(default_factory=dict)
    #: Non-empty blurs the still behind the graphic (e.g. ``i05_blur``).
    over: str = ""


_HTML_MAX_CHARS = 20_000
_HTML_FORBIDDEN = (
    "<script",
    "<iframe",
    "<object",
    "<embed",
    "<link",
    "javascript:",
    "src=",
    "href=",
    "@import",
)
_HTML_EVENT = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)


def _check_html(markup: str, scene_id: str) -> None:
    """Reject anything that could execute or fetch (static HTML/CSS only)."""
    if len(markup) > _HTML_MAX_CHARS:
        raise ValueError(
            f"scene {scene_id!r} html is {len(markup)} chars; the limit is {_HTML_MAX_CHARS}"
        )
    lowered = markup.lower()
    for token in _HTML_FORBIDDEN:
        if token in lowered:
            raise ValueError(f"scene {scene_id!r} html contains forbidden {token!r}")
    if _HTML_EVENT.search(markup):
        raise ValueError(f"scene {scene_id!r} html contains an inline event handler")


class PackGenerated(BaseModel):
    """Three prompts for a generated BG/MG/FG stack (#317)."""

    model_config = ConfigDict(extra="forbid")

    bg: str = Field(min_length=1)
    mg: str = Field(min_length=1)
    fg: str = Field(min_length=1)


class PackShot(BaseModel):
    """One visual inside a scene, starting on a narration line (#314)."""

    model_config = ConfigDict(extra="forbid")

    still: PackStill | None = None
    video: str = ""
    #: Index into ``scene.lines`` where this shot takes over; 0 is the scene start.
    at_line: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _one_visual(self) -> PackShot:
        if (self.still is None) == (not self.video):
            raise ValueError("a shot needs exactly one of `still:` or `video:`")
        return self


class PackScene(BaseModel):
    """One still, its lines, and the ambient motion the still drifts with."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    #: Exactly one of ``still``, ``video`` or ``html`` (#314, P4.9).
    still: PackStill | None = None
    video: str = ""
    #: A sandboxed static HTML/CSS shot (LLM-authored). No scripts or network.
    html: str = ""
    chip: str = ""
    motion: str = "drift"
    #: ``auto`` renders the layerized pair ``<still>__bg`` + ``<still>__fg``
    #: with a parallax pan (the depth map is required too); ``generated``
    #: renders three generated images (BG/MG/FG, #317); ``off`` renders the
    #: single still.
    layers: Literal["off", "auto", "generated"] = "off"
    generated: PackGenerated | None = None
    lines: list[PackLine] = Field(min_length=1)
    #: Optional chart/component over the still; rendered as one html layer.
    graphic: PackGraphic | None = None
    #: Visuals that change on a line boundary (#314). When set, no scene-level
    #: still/video/html is allowed.
    shots: list[PackShot] = Field(default_factory=list)
    #: Transition into the next scene (#313). Empty keeps a hard cut.
    transition_out: str = ""
    transition_seconds: float = Field(default=0.5, gt=0.0, le=2.0)

    @model_validator(mode="after")
    def _one_visual(self) -> PackScene:
        choices = [self.still is not None, bool(self.video), bool(self.html)]
        if self.shots:
            if any(choices):
                raise ValueError(
                    f"scene {self.id!r} cannot mix `shots:` with a scene-level still/video/html"
                )
            if self.shots[0].at_line != 0:
                raise ValueError(f"scene {self.id!r}: the first shot must be at_line 0")
            if any(
                later.at_line < earlier.at_line
                for earlier, later in zip(self.shots, self.shots[1:], strict=False)
            ):
                raise ValueError(f"scene {self.id!r}: shots must be ordered by at_line")
            return self
        if self.layers == "generated" and not self.shots:
            if self.video or self.html:
                raise ValueError(
                    f"scene {self.id!r}: layers: generated cannot mix `video:` or `html:`"
                )
            return self
        if sum(choices) != 1:
            raise ValueError(
                f"scene {self.id!r} needs exactly one of `still:` (generate/reuse), "
                "`video:` or `html:` (or a `shots:` list)"
            )
        if self.html:
            _check_html(self.html, self.id)
        return self

    @model_validator(mode="after")
    def _generated_matches_layers(self) -> PackScene:
        if self.layers == "generated" and self.generated is None:
            raise ValueError(f"scene {self.id!r}: layers: generated needs a `generated:` block")
        if self.layers != "generated" and self.generated is not None:
            raise ValueError(f"scene {self.id!r}: `generated:` requires layers: generated")
        return self

    @model_validator(mode="after")
    def _motion_is_known(self) -> PackScene:
        if self.motion not in PRIMITIVES:
            raise ValueError(
                f"scene {self.id!r}: unknown motion {self.motion!r}; known: "
                f"{', '.join(sorted(PRIMITIVES))}"
            )
        return self


class PackEndCard(BaseModel):
    """The closing card: one spoken line, the on-screen text, and the tail."""

    model_config = ConfigDict(extra="forbid")

    say: str = ""
    #: The phonetic spelling the voice reads, when it differs from ``say``.
    speak_as: str = ""
    text: str = ""
    url: str = "cosmicsaju.com"
    #: Silence held after the last spoken word. The QA tail gate accepts
    #: 0.8–2.0 s, so the window is closed here rather than discovered later.
    tail_seconds: float = Field(default=1.2, ge=0.8, le=2.0)
    #: Chapter packs in a longform set turn the closing card off (#315).
    enabled: bool = True

    @property
    def spoken(self) -> str:
        """What the voice reads: ``speak_as`` when set, else ``say``."""
        return (self.speak_as or self.say).strip()


class PackCharacter(BaseModel):
    """A character in the film, with the reference sheet that keeps it consistent."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = ""
    reference_images: list[str] = Field(default_factory=list)


class PackBeat(BaseModel):
    """One row of the beat sheet the operator approves before render."""

    model_config = ConfigDict(extra="forbid")

    at_seconds: float = Field(ge=0)
    label: str = ""


class PackDeliverable(BaseModel):
    """One render target: the aspect and, optionally, the length."""

    model_config = ConfigDict(extra="forbid")

    aspect: Aspect
    seconds: float | None = Field(default=None, gt=0)


#: The optional director's-brief fields (P4.2), in the order the audit names
#: them. Missing fields are advisory, never a build failure.
BRIEF_FIELDS = (
    "logline",
    "references",
    "characters",
    "beat_sheet",
    "on_screen_text",
    "deliverables",
    "direction",
)


class ContentPack(BaseModel):
    """One video's full authoring surface."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    design: str = "saju_series"
    aspect: Aspect = "9:16"
    #: ``auto`` places the cue table's sounds from local files, then the
    #: free Freesound/Openverse chain. ``off`` renders silently.
    sfx: Literal["off", "auto"] = "off"
    voice: PackVoice
    image: PackImage = Field(default_factory=PackImage)
    captions: PackCaptions = Field(default_factory=PackCaptions)
    music: PackMusic = Field(default_factory=PackMusic)
    #: Keep the right half clear in the last 20 s for YouTube's end screen.
    youtube_end_screen: bool = False
    end_card: PackEndCard = Field(default_factory=PackEndCard)
    scenes: list[PackScene] = Field(min_length=1)
    #: Optional director's brief (P4.2). None of these blocks a build; the
    #: preflight reports them as advisory gaps.
    logline: str = ""
    references: list[str] = Field(default_factory=list)
    characters: list[PackCharacter] = Field(default_factory=list)
    beat_sheet: list[PackBeat] = Field(default_factory=list)
    on_screen_text: list[str] = Field(default_factory=list)
    deliverables: list[PackDeliverable] = Field(default_factory=list)
    direction: str = ""

    @model_validator(mode="after")
    def _scene_ids_are_unique(self) -> ContentPack:
        seen: set[str] = set()
        for scene in self.scenes:
            if scene.id == END_CARD_ID:
                raise ValueError(f"scene id {END_CARD_ID!r} is reserved for the end card")
            if scene.id in seen:
                raise ValueError(f"duplicate scene id {scene.id!r}")
            seen.add(scene.id)
        return self

    @model_validator(mode="after")
    def _references_resolve(self) -> ContentPack:
        _check_references(self)
        return self

    @model_validator(mode="after")
    def _reuse_follows_its_still(self) -> ContentPack:
        generated: set[str] = set()
        for scene in self.scenes:
            still = scene.still
            if still is None:
                continue
            if still.key:
                generated.add(still.key)
            elif still.reuse not in generated:
                raise ValueError(
                    f"scene {scene.id!r} reuses still {still.reuse!r} before any scene generates it"
                )
        return self


#: Image providers that send reference images (PoYo by URL, Flow as local files, at most 3).
REFERENCE_PROVIDERS = frozenset({"poyo_ai", "flow_image"})


def _check_references(pack: ContentPack) -> None:
    """Characters, style frames and edits must resolve, and need a provider that sends references."""
    stills = still_specs(pack)
    keys = {still.key for still in stills}
    sheets = {character.name: character.reference_images for character in pack.characters}
    uses_references = bool(pack.image.style_reference)
    for still in stills:
        for name in still.characters:
            if name not in sheets:
                raise ValueError(
                    f"still {still.key!r} names character {name!r}, which `characters:` does not define"
                )
            if not sheets[name]:
                raise ValueError(f"character {name!r} has no reference_images to send")
        if still.edit_of and (still.edit_of == still.key or still.edit_of not in keys):
            raise ValueError(
                f"still {still.key!r}: edit_of must name another generated still, got {still.edit_of!r}"
            )
        uses_references = uses_references or bool(still.characters or still.edit_of)
    edits = {still.key: still.edit_of for still in stills if still.edit_of}
    for start in edits:
        seen, key = {start}, edits[start]
        while key in edits:
            if key in seen:
                raise ValueError(f"still {start!r}: edit_of chain loops back to {key!r}")
            seen.add(key)
            key = edits[key]
    if uses_references and pack.image.provider not in REFERENCE_PROVIDERS:
        raise ValueError(
            "reference images (style_reference, characters, edit_of) are sent by "
            f"{', '.join(sorted(REFERENCE_PROVIDERS))} only; image.provider is {pack.image.provider!r}"
        )


def still_references(pack: ContentPack, still: PackStill) -> list[str]:
    """Reference images for one still: the style frame, then each named character's sheet (deduplicated)."""
    refs: list[str] = []
    if pack.image.style_reference:
        refs.append(pack.image.style_reference)
    sheets = {character.name: character.reference_images for character in pack.characters}
    for name in still.characters:
        refs.extend(sheets.get(name, []))
    return list(dict.fromkeys(refs))


def _absolutize(reference: str, root: Path) -> str:
    if not reference or reference.startswith(("http://", "https://")):
        return reference
    path = Path(reference).expanduser()
    return str(path if path.is_absolute() else (root / path).resolve())


def brief_warnings(pack: ContentPack) -> list[str]:
    """Advisory gaps in the director's brief (P4.2). Never blocks a build."""
    missing = [name for name in BRIEF_FIELDS if not getattr(pack, name)]
    if not missing:
        return []
    return ["director's brief is missing: " + ", ".join(missing)]


class TimedWord(BaseModel):
    """One spoken word on the absolute timeline.

    ``text`` is what the voice said (the transcript's spelling, which may be
    phonetic); ``display`` is the pack's spelling when the line carries a
    ``speak_as``. Cues match ``text``; captions draw ``display``.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    display: str = ""
    start: float = Field(ge=0)
    end: float = Field(ge=0)


class TimedScene(BaseModel):
    """A scene's window and the words spoken inside it."""

    model_config = ConfigDict(extra="forbid")

    id: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    words: list[TimedWord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _window(self) -> TimedScene:
        if self.end <= self.start:
            raise ValueError(
                f"scene {self.id!r} ends at {self.end:.3f} but starts at {self.start:.3f}"
            )
        return self


class Alignment(BaseModel):
    """The measured timeline: contiguous scene windows tiling the duration."""

    model_config = ConfigDict(extra="forbid")

    duration: float = Field(gt=0)
    scenes: list[TimedScene] = Field(min_length=1)

    @model_validator(mode="after")
    def _scenes_tile_the_duration(self) -> Alignment:
        # Same rule as ``Composition._scenes_are_contiguous``: the adapter maps
        # these windows straight onto composition scenes, so a gap here would
        # only fail later, in the compiler.
        cursor = 0.0
        for scene in self.scenes:
            if abs(scene.start - cursor) > 1e-6:
                raise ValueError(
                    f"scene {scene.id!r} starts at {scene.start:.3f}s, expected {cursor:.3f}s"
                )
            cursor = scene.end
        if abs(cursor - self.duration) > 1e-6:
            raise ValueError(f"scenes cover {cursor:.3f}s but the duration is {self.duration:.3f}s")
        return self

    def word_dicts(self) -> list[dict[str, Any]]:
        """The flat word stream ``cues.find_cue`` expects."""
        return [
            {"word": word.text, "start": word.start, "end": word.end}
            for scene in self.scenes
            for word in scene.words
        ]


def load_content_pack(path: Path | str) -> ContentPack:
    """Read ``short.yaml`` and validate it; every failure names the file."""
    source = Path(path)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ContentPackError(f"pack {source} cannot be read: {exc}") from exc
    if not isinstance(raw, dict):
        raise ContentPackError(f"pack {source} must be a YAML mapping")
    try:
        pack = ContentPack.model_validate(raw)
    except ValidationError as exc:
        raise ContentPackError(f"pack {source} is invalid:\n{exc}") from exc
    # Reference images are written relative to the pack file; requests carry absolute paths.
    root = source.resolve().parent
    pack.image.style_reference = _absolutize(pack.image.style_reference, root)
    for character in pack.characters:
        character.reference_images = [_absolutize(ref, root) for ref in character.reference_images]
    return pack


def still_specs(pack: ContentPack) -> list[PackStill]:
    """The stills to generate, in first-appearance order, one per key."""
    specs: list[PackStill] = []
    seen: set[str] = set()
    for scene in pack.scenes:
        stills = [scene.still] if scene.still is not None else []
        stills.extend(shot.still for shot in scene.shots if shot.still is not None)
        for still in stills:
            if still.key and still.key not in seen:
                specs.append(still)
                seen.add(still.key)
    return specs


def still_for(pack: ContentPack, scene_id: str) -> str:
    """The generated still key a scene renders with (aliases resolved)."""
    generated = {
        scene.still.key for scene in pack.scenes if scene.still is not None and scene.still.key
    }
    for scene in pack.scenes:
        if scene.id != scene_id:
            continue
        if scene.still is None:
            raise ContentPackError(f"scene {scene_id!r} uses a video, not a still")
        key = scene.still.key or scene.still.reuse
        if key not in generated:
            raise ContentPackError(f"still {key!r} does not resolve to a generated still")
        return key
    raise ContentPackError(f"unknown scene {scene_id!r}")


def cue_phrases(pack: ContentPack) -> dict[str, str]:
    """``{"<scene_id>:<line_index>": phrase>`` for every declared cue."""
    return {
        f"{scene.id}:{index}": line.cue
        for scene in pack.scenes
        for index, line in enumerate(scene.lines)
        if line.cue
    }


def spoken_lines(pack: ContentPack) -> list[tuple[str, str]]:
    """Every narrated line in order, with the end card last when enabled."""
    lines = [(scene.id, line.spoken) for scene in pack.scenes for line in scene.lines]
    if pack.end_card.enabled and pack.end_card.spoken:
        lines.append((END_CARD_ID, pack.end_card.spoken))
    return lines


def display_lines(pack: ContentPack) -> list[tuple[str, str]]:
    """What each narrated line shows on screen, in the same order as spoken_lines."""
    lines = [(scene.id, line.say.strip()) for scene in pack.scenes for line in scene.lines]
    if pack.end_card.spoken:
        lines.append((END_CARD_ID, pack.end_card.say.strip()))
    return lines
