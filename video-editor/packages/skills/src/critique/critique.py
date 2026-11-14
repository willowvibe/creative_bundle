"""Score a render against a design rubric with a vision model (P4.3).

The pass is advisory: technical QA gates stay authoritative. It sends a contact
sheet of downscaled keyframes to the `critique` task (a vision model), parses a
fixed 1–10 rubric, and returns structured patches. Only motion patches are
applied automatically today; the rest are reported for a human or a later pass.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from PIL import Image
from pydantic import BaseModel, Field, ValidationError
from video_editor_core.motion import PRIMITIVES

RUBRIC: tuple[str, ...] = (
    "hierarchy",
    "motion_variety",
    "depth",
    "brand_fit",
    "sync",
    "legibility",
    "pacing",
)
DEFAULT_CELL = 384
DEFAULT_MAX_FRAMES = 12

_SYSTEM = (
    "You are a demanding motion-graphics director. Judge design quality, not "
    "technical validity. Score each axis 1-10; a score below 8 needs a concrete "
    "fix. Return JSON only."
)


class CritiqueError(RuntimeError):
    """The critique pass could not produce a usable report."""


class AxisScore(BaseModel):
    axis: str
    score: int = Field(ge=1, le=10)
    note: str = ""


class CritiqueRound(BaseModel):
    scores: list[AxisScore]
    summary: str = ""
    applied: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)


class Patch(BaseModel):
    action: str
    target: str = ""
    value: str = ""


class CritiqueReport(BaseModel):
    scores: list[AxisScore]
    summary: str = ""
    patches: list[Patch] = Field(default_factory=list)

    @property
    def overall(self) -> float:
        if not self.scores:
            return 0.0
        return round(sum(item.score for item in self.scores) / len(self.scores), 2)

    def failing_axes(self, threshold: int = 8) -> list[str]:
        return [item.axis for item in self.scores if item.score < threshold]


class VisionRouter(Protocol):
    async def generate(
        self,
        task_type: str,
        prompt: str,
        *,
        system: str | None = None,
        images: list[bytes] | None = None,
    ) -> str: ...


def _load_frames(frames: list[Path], max_frames: int) -> list[Path]:
    ordered = sorted(path for path in frames if path.suffix.lower() in {".png", ".jpg", ".jpeg"})
    if not ordered:
        raise CritiqueError("no QA frames to critique; build with QA enabled first")
    if len(ordered) <= max_frames:
        return ordered
    step = len(ordered) / max_frames
    return [ordered[int(index * step)] for index in range(max_frames)]


def frames_to_images(
    frames: list[Path], *, max_frames: int = DEFAULT_MAX_FRAMES, cell: int = DEFAULT_CELL
) -> list[bytes]:
    """Downscale each keyframe to ``cell`` px and return PNG bytes for a vision call."""
    images: list[bytes] = []
    for path in _load_frames(frames, max_frames):
        with Image.open(path) as image:
            frame = image.convert("RGB")
            frame.thumbnail((cell, cell))
            payload = io.BytesIO()
            frame.save(payload, format="PNG")
            images.append(payload.getvalue())
    return images


def build_contact_sheet(
    frames: list[Path],
    output: Path | str,
    *,
    max_frames: int = DEFAULT_MAX_FRAMES,
    cell: int = DEFAULT_CELL,
    columns: int = 4,
) -> Path:
    """Tile the keyframes into one image (one vision call instead of twelve)."""
    selected = _load_frames(frames, max_frames)
    columns = max(1, min(columns, len(selected)))
    rows = (len(selected) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell, rows * cell), (0, 0, 0))
    for index, path in enumerate(selected):
        with Image.open(path) as image:
            frame = image.convert("RGB")
            frame.thumbnail((cell, cell))
            left = (index % columns) * cell + (cell - frame.width) // 2
            top = (index // columns) * cell + (cell - frame.height) // 2
            sheet.paste(frame, (left, top))
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, format="PNG")
    return target


def _prompt() -> str:
    axes = ", ".join(RUBRIC)
    return (
        f"Score this render on each axis: {axes}.\n"
        'Return JSON: {"scores":[{"axis":"...","score":1-10,"note":"..."}],'
        '"summary":"...","patches":[{"action":"change_motion",'
        '"target":"scene_id","value":"rise|pop|drift|..."}]}\n'
        "Use at most five patches. A patch must be a structured change, never code."
    )


def _parse(text: str) -> CritiqueReport:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    try:
        data = json.loads(cleaned)
    except ValueError as exc:
        raise CritiqueError(f"critique returned non-JSON: {text[:200]!r}") from exc
    try:
        report = CritiqueReport.model_validate(data)
    except ValidationError as exc:
        raise CritiqueError(f"critique returned an invalid report: {exc}") from exc
    known = {item.axis for item in report.scores}
    missing = [axis for axis in RUBRIC if axis not in known]
    if missing:
        raise CritiqueError(f"critique omitted axes: {', '.join(missing)}")
    return report


async def critique_frames(
    frames: list[Path],
    router: VisionRouter,
    *,
    max_frames: int = DEFAULT_MAX_FRAMES,
    cell: int = DEFAULT_CELL,
) -> CritiqueReport:
    """Score the frames (one contact sheet) and return the parsed report."""
    images = frames_to_images(frames, max_frames=max_frames, cell=cell)
    if not images:
        raise CritiqueError("no frames survived downscaling")
    text = await router.generate(
        "critique",
        _prompt(),
        system=_SYSTEM,
        images=images,
    )
    return _parse(text)


def apply_patches(pack: Any, patches: list[Patch]) -> tuple[Any, list[str], list[str]]:
    """Apply the motion patches to a pack copy; report applied and skipped ones."""
    updated = pack.model_copy(deep=True)
    scenes = {scene.id: scene for scene in updated.scenes}
    applied: list[str] = []
    skipped: list[str] = []
    for patch in patches:
        if patch.action == "change_motion" and patch.target in scenes and patch.value in PRIMITIVES:
            scenes[patch.target].motion = patch.value
            applied.append(f"{patch.target}: motion={patch.value}")
            continue
        skipped.append(f"{patch.action} {patch.target} {patch.value}".strip())
    return updated, applied, skipped


async def critique_loop(
    frames: Callable[[], list[Path]],
    router: VisionRouter,
    build: Callable[[list[Patch]], tuple[bool, list[str], list[str]]],
    *,
    max_rounds: int = 2,
    target: int = 8,
) -> list[CritiqueRound]:
    """Score, patch, rebuild and re-score until every axis reaches ``target``.

    ``build`` applies the accepted patches and rebuilds; it returns
    ``(changed, applied, skipped)``. The loop stops when a round is clean, when
    no patch was applied, or when ``max_rounds`` is exhausted. The technical
    gates still decide whether the build ships.
    """
    rounds: list[CritiqueRound] = []
    for _ in range(max(1, max_rounds)):
        report = await critique_frames(frames(), router)
        if not report.failing_axes(target):
            rounds.append(CritiqueRound(scores=report.scores, summary=report.summary))
            break
        changed, applied, skipped = build(report.patches)
        rounds.append(
            CritiqueRound(
                scores=report.scores,
                summary=report.summary,
                applied=list(applied),
                skipped=list(skipped),
            )
        )
        if not changed:
            break
    return rounds
