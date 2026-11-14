"""Parse an explainer ``script.md`` into a structured spec.

The file is YAML front matter between ``---`` fences, then one ``## `` heading
per scene whose body is itself YAML:

    ---
    title: Why Google Paid $32 Billion For Wiz
    ---

    ## Scene 1 — The number
    visual: typographic
    layout: big_stat
    headline: $32B
    narration: |
      Thirty-two billion dollars.

Durations are never authored here: the pipeline derives them from the spoken
narration (``explainer_timing``), because a written duration always drifts
from the voice.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from video_editor_core.calc_card import ledger_total_error, parse_calc
from video_editor_core.tally_chip import money, parse_tally

VALID_VISUALS = frozenset({"typographic", "still", "map", "composite", "footage"})
VALID_LAYOUTS = frozenset({"big_stat", "bar_compare", "definition", "sequence", "array", "ledger"})
VALID_COMPOSITE_LAYOUTS = frozenset({"split", "over_media", "compare", "quote", "section"})

#: Composite layouts that need a still, and how many.
COMPOSITE_IMAGES = {
    "split": 1,
    "over_media": 1,
    "compare": 2,
    "quote": 0,
    "section": 0,
}

DEFAULT_ASPECT_RATIO = "9:16"
DEFAULT_CAPTION_PRESET = "karaoke"
DEFAULT_DESIGN = "cream_editorial"
DEFAULT_REUSE_THRESHOLD = 0.30
#: ``single``: the whole script is one TTS call, timed by Whisper (continuous
#: delivery). ``per_scene``: one call per scene, timed by each clip's measured
#: length; exact for languages Whisper times badly (Telugu, chai-stall-pilot-1).
NARRATION_MODES = frozenset({"single", "per_scene"})

#: Keys with a meaning to the pipeline. Everything else a scene declares is a
#: layout prop and is copied verbatim into ``props``.
_RESERVED_KEYS = frozenset({"visual", "narration", "image", "image_b", "layout", "subject"})


#: The display copy that makes a still a designed plate.
DESIGNED_STILL_KEYS = ("eyebrow", "headline", "body", "subtitle")


def is_designed_still(props: Mapping[str, Any]) -> bool:
    """True when a still scene authors a title block over its image.

    A ``visual: still`` scene is a bare full-bleed image: fine for atmosphere,
    but it leaves the frame with no design. Display copy turns it into the
    ``over_media`` composite — the full-bleed still under a scrim with a title
    block — so one renderer draws both and the plan stays truthful about what
    will render. ``badge`` alone is not enough: ``over_media`` needs a
    headline, body or subtitle to hold.
    """
    return any(str(props.get(key) or "").strip() for key in DESIGNED_STILL_KEYS)


_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(?P<body>.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)
_SCENE_HEADING_RE = re.compile(r"^##[ \t]+(?P<title>[^\n]+)$", re.MULTILINE)

#: YAML 1.1 resolves ``9:16`` as a sexagesimal integer (556), but a script
#: means the literal string. The int resolver below drops that production.
_INT_RE = re.compile(
    r"""^(?:[-+]?0b[0-1_]+
        |[-+]?0[0-7_]+
        |[-+]?(?:0|[1-9][0-9_]*)
        |[-+]?0x[0-9a-fA-F_]+)$""",
    re.X,
)


class _ScriptLoader(yaml.SafeLoader):
    """``SafeLoader`` that keeps ``9:16`` and friends as strings.

    It only re-registers implicit resolvers; it adds no constructors, so it
    cannot build arbitrary Python objects.
    """


def _install_script_loader() -> None:
    """Give ``_ScriptLoader`` its own resolvers, minus sexagesimal ints."""
    _ScriptLoader.yaml_implicit_resolvers = {
        first: [
            (tag, _INT_RE if tag == "tag:yaml.org,2002:int" else regexp) for tag, regexp in entries
        ]
        for first, entries in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }


_install_script_loader()


class ScriptError(ValueError):
    """A script that cannot be turned into a plan."""


@dataclass(frozen=True)
class ScriptScene:
    """One ``## `` block of the script."""

    id: str
    title: str
    visual: str
    narration: str
    layout: str = ""
    image: str = ""
    image_b: str = ""
    subject: str = ""
    props: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExplainerScript:
    """The parsed script: front matter plus scenes."""

    title: str
    scenes: list[ScriptScene]
    aspect_ratio: str = DEFAULT_ASPECT_RATIO
    voice: str = ""
    voice_model: str = ""
    voice_language: str = ""
    #: Speaking speed for providers that take one (Sarvam: 0.5-2.0); None keeps the default.
    voice_pace: float | None = None
    caption_preset: str = DEFAULT_CAPTION_PRESET
    design: str = DEFAULT_DESIGN
    reuse_threshold: float = DEFAULT_REUSE_THRESHOLD
    target_seconds: float | None = None
    music_mood: str = ""
    #: Display word -> spoken form for the voice only (#346); captions and cues
    #: keep the display text. Matched as whole words, case-sensitive.
    pronunciations: dict[str, str] = field(default_factory=dict)
    narration_mode: str = "single"
    #: Drawn over the Gemini sparkle on Flow/agy stills; "" leaves stills as made.
    ai_label: str = "AI image"

    @property
    def narration(self) -> str:
        """The whole script as one display string, scene order preserved."""
        return "\n\n".join(scene.narration for scene in self.scenes)

    @property
    def spoken_narration(self) -> str:
        """What the voice reads: the narration with ``pronunciations`` applied."""
        return self.spoken(self.narration)

    def spoken(self, text: str) -> str:
        """``text`` with ``pronunciations`` applied, for the voice only."""
        if not self.pronunciations:
            return text
        pattern = re.compile(
            r"(?<![\w-])("
            + "|".join(
                re.escape(word) for word in sorted(self.pronunciations, key=len, reverse=True)
            )
            + r")(?![\w-])"
        )
        return pattern.sub(lambda match: self.pronunciations[match.group(1)], text)


def parse_explainer_script(text: str, *, source: str = "<script>") -> ExplainerScript:
    """Parse ``text`` into an :class:`ExplainerScript`, or raise ``ScriptError``."""
    meta, body = _split_front_matter(text, source)
    scenes = _parse_scenes(body, source)
    title = str(meta.get("title") or "").strip()
    if not title:
        raise ScriptError(f"{source}: front matter needs a non-empty `title`")

    target_seconds: float | None = None
    raw_target = meta.get("target_seconds")
    if raw_target is not None and raw_target != "":
        try:
            target_seconds = float(raw_target)
        except (TypeError, ValueError) as exc:
            raise ScriptError(f"{source}: `target_seconds` must be a number") from exc
        if target_seconds <= 0:
            raise ScriptError(f"{source}: `target_seconds` must be > 0")

    raw_threshold = meta.get("reuse_threshold", DEFAULT_REUSE_THRESHOLD)
    try:
        threshold = float(raw_threshold)
    except (TypeError, ValueError) as exc:
        raise ScriptError(f"{source}: `reuse_threshold` must be a number") from exc
    if not 0.0 < threshold <= 1.0:
        raise ScriptError(f"{source}: `reuse_threshold` must be in (0, 1], got {threshold}")

    voice_pace = _voice_pace(meta.get("voice_pace"), source)

    raw_pronunciations = meta.get("pronunciations") or {}
    if not isinstance(raw_pronunciations, dict) or not all(
        isinstance(key, str) and key.strip() and isinstance(value, str) and value.strip()
        for key, value in raw_pronunciations.items()
    ):
        raise ScriptError(
            f"{source}: `pronunciations` must map each display word to how it is spoken, "
            "e.g. `Kutch: Kuchh`"
        )
    pronunciations = {key.strip(): value.strip() for key, value in raw_pronunciations.items()}

    narration_mode = str(meta.get("narration_mode") or "single").strip()
    if narration_mode not in NARRATION_MODES:
        raise ScriptError(
            f"{source}: `narration_mode` must be one of {sorted(NARRATION_MODES)}, "
            f"got {narration_mode!r}"
        )

    return ExplainerScript(
        title=title,
        scenes=scenes,
        aspect_ratio=str(meta.get("aspect_ratio") or DEFAULT_ASPECT_RATIO).strip(),
        voice=str(meta.get("voice") or "").strip(),
        voice_model=str(meta.get("voice_model") or "").strip(),
        voice_language=str(meta.get("voice_language") or "").strip(),
        voice_pace=voice_pace,
        caption_preset=str(meta.get("caption_preset") or DEFAULT_CAPTION_PRESET).strip(),
        design=str(meta.get("design") or DEFAULT_DESIGN).strip(),
        reuse_threshold=threshold,
        target_seconds=target_seconds,
        music_mood=str(meta.get("music_mood") or "").strip(),
        pronunciations=pronunciations,
        narration_mode=narration_mode,
        ai_label=str(meta["ai_label"]).strip() if "ai_label" in meta else "AI image",
    )


def load_explainer_script(path: Path | str) -> ExplainerScript:
    """Read and parse ``path``."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScriptError(f"{path}: cannot read script: {exc}") from exc
    return parse_explainer_script(text, source=str(path))


def _split_front_matter(text: str, source: str) -> tuple[dict[str, Any], str]:
    """Return (front matter mapping, rest of the file)."""
    match = _FRONT_MATTER_RE.match(text)
    if match is None:
        raise ScriptError(f"{source}: missing front matter; the file must start with a `---` block")
    try:
        meta = yaml.load(match.group("body"), Loader=_ScriptLoader)
    except yaml.YAMLError as exc:
        raise ScriptError(f"{source}: front matter is not valid YAML: {exc}") from exc
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise ScriptError(f"{source}: front matter must be a mapping")
    return dict(meta), text[match.end() :]


def _check_tally_moves(scenes: list[ScriptScene], source: str) -> None:
    """Each tally change must equal the cash rows its scene shows.

    A scene that moves the cash box and shows a ``calc`` card must show the
    whole move: the chip may not jump by more or less than the rows add up to.
    """
    previous = 0
    for scene in scenes:
        tally = parse_tally(scene.props.get("tally"))
        if tally is None:
            continue
        rows = parse_calc(scene.props["calc"]) if scene.props.get("calc") else []
        cash = [row for row in rows if row.in_tally]
        move = tally.value - previous
        if cash:
            added = sum(row.amount for row in cash)
            if abs(added - move) > 0.5:
                shown = money(round(added))
                raise ScriptError(
                    f"{source}: scene {scene.title!r}: tally moves by "
                    f"{money(move)} but its cash rows add to {shown}"
                )
        previous = tally.value


def _parse_scenes(body: str, source: str) -> list[ScriptScene]:
    """Split the body on ``## `` headings and parse each block as YAML."""
    headings = list(_SCENE_HEADING_RE.finditer(body))
    if not headings:
        raise ScriptError(f"{source}: no `## ` scene heading found")
    scenes: list[ScriptScene] = []
    for position, heading in enumerate(headings):
        start = heading.end()
        end = headings[position + 1].start() if position + 1 < len(headings) else len(body)
        title = heading.group("title").strip()
        try:
            data = yaml.load(body[start:end], Loader=_ScriptLoader)
        except yaml.YAMLError as exc:
            raise ScriptError(f"{source}: scene {title!r} has invalid YAML: {exc}") from exc
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ScriptError(f"{source}: scene {title!r} must be a YAML mapping")
        scenes.append(_scene_from(position + 1, title, dict(data), source))
    _check_tally_moves(scenes, source)
    return scenes


def _scene_from(number: int, title: str, data: dict[str, Any], source: str) -> ScriptScene:
    """Validate one scene mapping; the heading is always named in errors."""
    label = f"{source}: scene {title!r}"
    visual = str(data.get("visual") or "").strip()
    if visual not in VALID_VISUALS:
        raise ScriptError(
            f"{label}: `visual` must be one of {sorted(VALID_VISUALS)}, got {visual!r}"
        )
    narration = str(data.get("narration") or "").strip()
    if not narration:
        raise ScriptError(f"{label}: `narration` must be non-empty")

    layout = str(data.get("layout") or "").strip()
    image = str(data.get("image") or "").strip()
    image_b = str(data.get("image_b") or "").strip()
    if visual == "typographic":
        if not layout:
            raise ScriptError(f"{label}: a typographic scene needs `layout`")
        if layout not in VALID_LAYOUTS:
            raise ScriptError(
                f"{label}: unknown layout {layout!r}; choose one of {sorted(VALID_LAYOUTS)}"
            )
    if visual == "composite":
        if not layout:
            raise ScriptError(f"{label}: a composite scene needs `layout`")
        if layout not in VALID_COMPOSITE_LAYOUTS:
            raise ScriptError(
                f"{label}: unknown composite layout {layout!r}; "
                f"choose one of {sorted(VALID_COMPOSITE_LAYOUTS)}"
            )
        needed = COMPOSITE_IMAGES[layout]
        if needed >= 1 and not image:
            raise ScriptError(f"{label}: composite layout {layout!r} needs an `image` prompt")
        if needed >= 2 and not image_b:
            raise ScriptError(
                f"{label}: composite layout {layout!r} needs a second `image_b` prompt"
            )
    if visual == "footage":
        # Stock search is English-only on every library; the still is what the
        # scene shows when no clip passes the relevance check.
        if not str(data.get("query") or "").strip():
            raise ScriptError(f"{label}: a footage scene needs a `query` (English stock search)")
        if not image:
            raise ScriptError(f"{label}: a footage scene needs a `image` fallback prompt")
    if visual == "still" and not image:
        raise ScriptError(f"{label}: a still scene needs an `image` prompt")
    if visual == "map" and not str(data.get("country") or "").strip():
        raise ScriptError(f"{label}: a map scene needs a `country`")

    try:
        parse_tally(data.get("tally"))
        if data.get("calc"):
            parse_calc(data["calc"])
        if visual == "typographic" and layout == "ledger":
            total = data.get("total")
            if not data.get("rows") or not isinstance(total, dict) or "amount" not in total:
                raise ValueError("a ledger needs `rows` and a `total` with an `amount`")
            error = ledger_total_error(parse_calc(data["rows"]), float(total["amount"]))
            if error:
                raise ValueError(error)
    except ValueError as exc:
        raise ScriptError(f"{label}: {exc}") from exc

    props = {key: value for key, value in data.items() if key not in _RESERVED_KEYS}
    return ScriptScene(
        id=f"scene_{number}",
        title=title,
        visual=visual,
        narration=narration,
        layout=layout,
        image=image,
        image_b=image_b,
        subject=str(data.get("subject") or "").strip(),
        props=props,
    )


MIN_VOICE_PACE = 0.5
MAX_VOICE_PACE = 2.0


def _voice_pace(raw: object, source: str) -> float | None:
    """Parse ``voice_pace``: a speaking-speed multiplier within the provider's range."""
    if raw is None or raw == "":
        return None
    try:
        pace = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ScriptError(f"{source}: `voice_pace` must be a number, e.g. 1.15") from exc
    if not MIN_VOICE_PACE <= pace <= MAX_VOICE_PACE:
        raise ScriptError(
            f"{source}: `voice_pace` must be between {MIN_VOICE_PACE} and {MAX_VOICE_PACE}, "
            f"got {pace}"
        )
    return pace
