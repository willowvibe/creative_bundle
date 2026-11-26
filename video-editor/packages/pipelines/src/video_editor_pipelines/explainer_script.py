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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml
from video_editor_core.calc_card import ledger_total_error, parse_calc
from video_editor_core.hook_card import parse_hook
from video_editor_core.progress_strip import parse_progress_labels
from video_editor_core.tally_chip import money, parse_tally
from video_editor_core.typographic_scenes import (
    big_stat_align_error,
    donut_center_error,
    waterfall_end_error,
)

VALID_VISUALS = frozenset({"typographic", "still", "map", "composite", "footage"})
VALID_LAYOUTS = frozenset(
    {
        "big_stat",
        "bar_compare",
        "definition",
        "sequence",
        "array",
        "ledger",
        "waterfall",
        "line",
        "donut",
    }
)
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
    #: The ordered cost steps a scene's `progress:` label picks from; () = no strip.
    progress: tuple[str, ...] = ()
    #: False silences the coin clink under a counting tally chip.
    tally_sound: bool = True
    #: False silences only the photo-card pops.
    card_sound: bool = True
    #: Front matter ``captions: {mode: burned|sidecar}``; ``sidecar`` means no burn.
    captions_mode: str = "burned"

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


def parse_explainer_script(
    text: str, *, source: str = "<script>", base_dir: Path | None = None
) -> ExplainerScript:
    """Parse ``text`` into an :class:`ExplainerScript`, or raise ``ScriptError``.

    ``base_dir`` is what a footage shot's relative ``file:`` resolves against
    (the script's folder; the working directory when omitted).
    """
    meta, body = _split_front_matter(text, source)
    scenes = _parse_scenes(body, source, base_dir)
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

    progress = _progress_steps(meta.get("progress"), source)
    scenes = _resolve_progress(scenes, progress, source)
    tally_sound = meta.get("tally_sound", True)
    if not isinstance(tally_sound, bool):
        raise ScriptError(f"{source}: `tally_sound` must be true or false, got {tally_sound!r}")
    card_sound = meta.get("card_sound", True)
    if not isinstance(card_sound, bool):
        raise ScriptError(f"{source}: `card_sound` must be true or false, got {card_sound!r}")

    raw_captions = meta.get("captions") or {}
    captions_mode = (
        str(raw_captions.get("mode") or "burned").strip() if isinstance(raw_captions, dict) else ""
    )
    if captions_mode not in {"burned", "sidecar"}:
        raise ScriptError(f"{source}: `captions` must be `{{mode: burned|sidecar}}`")

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
        progress=progress,
        tally_sound=tally_sound,
        card_sound=card_sound,
        captions_mode=captions_mode,
    )


def _progress_steps(raw: object, source: str) -> tuple[str, ...]:
    """The front-matter ``progress`` list, or ``()`` when the script has none."""
    if raw is None:
        return ()
    try:
        return parse_progress_labels(raw)
    except ValueError as exc:
        raise ScriptError(f"{source}: front matter {exc}") from exc


def _resolve_progress(
    scenes: list[ScriptScene], steps: tuple[str, ...], source: str
) -> list[ScriptScene]:
    """Replace each scene's ``progress: <label>`` with ``{items, current}``.

    The renderer then needs no plan-level lookup. A label that is not one of
    the front matter's steps is an error.
    """
    resolved: list[ScriptScene] = []
    for scene in scenes:
        label = scene.props.get("progress")
        if label is None:
            resolved.append(scene)
            continue
        where = f"{source}: scene {scene.title!r}"
        if not isinstance(label, str) or not label.strip():
            raise ScriptError(f"{where}: `progress` must be one of the step labels, got {label!r}")
        label = label.strip()
        if not steps:
            raise ScriptError(
                f"{where}: `progress: {label}` needs a `progress:` list of steps in the front matter"
            )
        if label not in steps:
            raise ScriptError(
                f"{where}: `progress` {label!r} is not one of the front matter's steps {list(steps)}"
            )
        resolved.append(
            replace(
                scene, props={**scene.props, "progress": {"items": list(steps), "current": label}}
            )
        )
    return resolved


def load_explainer_script(path: Path | str) -> ExplainerScript:
    """Read and parse ``path``."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScriptError(f"{path}: cannot read script: {exc}") from exc
    return parse_explainer_script(text, source=str(path), base_dir=path.absolute().parent)


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


def _parse_scenes(body: str, source: str, base_dir: Path | None = None) -> list[ScriptScene]:
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
        scenes.append(_scene_from(position + 1, title, dict(data), source, base_dir))
    _check_tally_moves(scenes, source)
    return scenes


#: ``file:`` shot extensions: stills Chrome can draw, and clips ffmpeg can read.
FILE_SHOT_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
FILE_SHOT_VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".webm"})


def _resolve_shot_file(raw: str, label: str, position: int, base_dir: Path | None) -> str:
    """The absolute path of a ``file:`` shot, or a ``ScriptError`` naming the problem."""
    where = f"{label}: shot {position} `file`"
    given = Path(raw).expanduser()
    candidate = given if given.is_absolute() else (base_dir or Path.cwd()) / given
    if candidate.is_symlink():
        raise ScriptError(f"{where} is a symlink ({candidate}); point at the real file")
    if not candidate.exists():
        raise ScriptError(f"{where} does not exist: {candidate}")
    if not candidate.is_file():
        raise ScriptError(f"{where} is not a regular file: {candidate}")
    suffix = candidate.suffix.lower()
    if suffix not in FILE_SHOT_IMAGE_EXTENSIONS | FILE_SHOT_VIDEO_EXTENSIONS:
        known = sorted(
            e.lstrip(".") for e in FILE_SHOT_IMAGE_EXTENSIONS | FILE_SHOT_VIDEO_EXTENSIONS
        )
        raise ScriptError(f"{where} has unsupported extension {suffix or '(none)'!r}; use {known}")
    return str(candidate.resolve())


def _parse_shots(raw: object, label: str, base_dir: Path | None = None) -> list[dict[str, Any]]:
    """Validate a footage scene's ``shots`` list and return it normalized.

    A shot is one of: a ``query`` (stock clip), a ``photo`` (stock still) or a
    ``file`` (your own image or clip, path relative to the script; ``ai: true``
    marks a generated image so it carries the AI label). Exactly one of the
    three. ``image`` is the stock shot's own AI fallback prompt.
    """
    one_of = "exactly one of `query`, `photo` or `file`"
    if not isinstance(raw, list) or not raw:
        raise ScriptError(f"{label}: `shots` must be a non-empty list of maps, each with {one_of}")
    shots: list[dict[str, Any]] = []
    for position, item in enumerate(raw, start=1):
        if not isinstance(item, Mapping):
            raise ScriptError(f"{label}: shot {position} must be a map with {one_of}")
        query = str(item.get("query") or "").strip()
        photo = str(item.get("photo") or "").strip()
        file = str(item.get("file") or "").strip()
        if sum(bool(value) for value in (query, photo, file)) != 1:
            raise ScriptError(f"{label}: shot {position} must have {one_of}, got {dict(item)!r}")
        unknown = sorted(set(item) - {"query", "photo", "image", "file", "ai"})
        if unknown:
            raise ScriptError(
                f"{label}: shot {position} has unknown key(s) {unknown}; "
                "use `query`, `photo`, `file` and optionally `image` (a comma ends a value "
                "inside `{...}`, so keep prompts comma-free or quote them)"
            )
        ai = item.get("ai", False)
        if not isinstance(ai, bool):
            raise ScriptError(f"{label}: shot {position} `ai` must be true or false, got {ai!r}")
        if ai and not file:
            raise ScriptError(f"{label}: shot {position} `ai` is only allowed with `file`")
        if file and item.get("image"):
            raise ScriptError(
                f"{label}: shot {position} is a `file` shot; it has no AI fallback, drop `image`"
            )
        shots.append(
            {
                "query": query,
                "photo": photo,
                "file": _resolve_shot_file(file, label, position, base_dir) if file else "",
                "ai": ai,
                "image": str(item.get("image") or "").strip(),
            }
        )
    return shots


def _scene_from(
    number: int, title: str, data: dict[str, Any], source: str, base_dir: Path | None = None
) -> ScriptScene:
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
        raw_shots = data.get("shots")
        if raw_shots is not None:
            if str(data.get("query") or "").strip():
                raise ScriptError(
                    f"{label}: a footage scene takes either `query` or `shots`, not both; "
                    "move the query into a shot"
                )
            data["shots"] = _parse_shots(raw_shots, label, base_dir)
            bare = [
                n
                for n, shot in enumerate(data["shots"], 1)
                if not (shot["file"] or shot["image"] or image)
            ]
            if bare:
                raise ScriptError(
                    f"{label}: shot {bare[0]} has no `image` fallback prompt and the scene has "
                    "no scene-level `image` to fall back on"
                )
        else:
            # Stock search is English-only on every library; the still is what
            # the scene shows when no clip passes the relevance check.
            if not str(data.get("query") or "").strip():
                raise ScriptError(
                    f"{label}: a footage scene needs a `query` (English stock search) or `shots`"
                )
            if not image:
                raise ScriptError(f"{label}: a footage scene needs a `image` fallback prompt")
    if visual == "still" and not image:
        raise ScriptError(f"{label}: a still scene needs an `image` prompt")
    if visual == "map" and not str(data.get("country") or "").strip():
        raise ScriptError(f"{label}: a map scene needs a `country`")

    try:
        parse_tally(data.get("tally"))
        parse_hook(data.get("hook"))
        if data.get("calc"):
            parse_calc(data["calc"])
        if visual == "typographic" and layout == "ledger":
            total = data.get("total")
            if not data.get("rows") or not isinstance(total, dict) or "amount" not in total:
                raise ValueError("a ledger needs `rows` and a `total` with an `amount`")
            error = ledger_total_error(parse_calc(data["rows"]), float(total["amount"]))
            if error:
                raise ValueError(error)
        if visual == "typographic" and layout == "waterfall":
            error = waterfall_end_error(data)
            if error:
                raise ValueError(error)
        if visual == "typographic" and layout == "big_stat":
            error = big_stat_align_error(data)
            if error:
                raise ValueError(error)
        if visual == "typographic" and layout == "donut":
            error = donut_center_error(data)
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
