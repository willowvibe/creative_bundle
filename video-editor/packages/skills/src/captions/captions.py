"""Captions skill orchestrator."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import structlog
from graphics.graphics import GraphicsSkill
from graphics.models import Brand, CompositionPlan, Layer
from video_editor_core.render_cache import SegmentCache
from video_editor_core.render_graph import Layer as GraphLayer
from video_editor_core.render_graph import Rect as GraphRect
from video_editor_core.render_graph import Timing as GraphTiming
from video_editor_core.safe_area import fit_box
from video_editor_core.timeline import EDL
from video_editor_llm import ClassifyClient, question_noul, run_sync
from video_editor_state.job import JobContext

from captions.models import CaptionLine, CaptionManifest, CaptionsError, CaptionStyle
from captions.timing import generate_caption_words, generate_segment_caption_lines

logger = structlog.get_logger(__name__)

# (flag name, Jev instruction, true label, false label, human-readable reason).
# The three checks are advisory only: they never change caption timing or the
# rendered output, they just annotate the manifest for a human to review.
_CAPTION_QUALITY_CHECKS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "too_long",
        "Is `line` too long or otherwise hard to read as a single caption?",
        "Too long",
        "Fine",
        "too long / hard to read",
    ),
    (
        "profanity",
        "Does `line` contain profanity or anything you would not caption?",
        "Contains profanity",
        "Clean",
        "profanity / unsuitable",
    ),
    (
        "emphasis",
        "Does `line` contain a term that deserves emphasis, such as a key word or a number?",
        "Deserves emphasis",
        "No emphasis",
        "deserves emphasis",
    ),
)


def _as_float(value: Any) -> float | None:
    """Best-effort float coercion; ``None`` when the value is not numeric."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _caption_review_items(lines: Iterable[Any]) -> list[dict[str, Any]]:
    """Normalize mixed caption inputs into ``{text, start, end}`` dicts.

    ``lines`` may mix ``CaptionWord``/``CaptionLine`` models, graphics
    ``Layer`` objects, plain dicts, or bare strings. Blank text is dropped.
    """
    items: list[dict[str, Any]] = []
    for line in lines:
        start: float | None = None
        end: float | None = None
        if isinstance(line, str):
            text = line.strip()
        elif isinstance(line, Mapping):
            text = str(line.get("text") or line.get("word") or "").strip()
            start = _as_float(line.get("start"))
            end = _as_float(line.get("end"))
        else:
            text = str(getattr(line, "text", "") or "").strip()
            start = _as_float(getattr(line, "start", None))
            end = _as_float(getattr(line, "end", None))
            if start is None and end is None:
                timing = getattr(line, "timing", None)
                if isinstance(timing, Mapping):
                    start = _as_float(timing.get("start"))
                    end = _as_float(timing.get("end"))
        if text:
            items.append({"text": text, "start": start, "end": end})
    return items


def review_caption_quality(
    lines: Iterable[Any],
    *,
    client: ClassifyClient | None = None,
    threshold: float = 0.5,
) -> list[dict[str, Any]]:
    """Flag caption lines worth a human's attention, via one batched Jev call.

    Every line is scored against three ``noul`` questions (too long, profanity,
    deserves emphasis) in a SINGLE ``classify`` request. Returns a list of
    advisory flag dicts::

        {"start": ..., "end": ..., "text": ..., "flags": [...], "reason": ...}

    only for lines that trip at least one check at ``threshold``. Returns
    ``[]`` when ``lines`` is empty, no classifier is available, or the call
    fails — so the render path is completely unchanged when Jev is
    unconfigured. The whole call (client build + classify + close) runs in one
    coroutine so the async HTTP client never crosses event loops.
    """
    items = _caption_review_items(lines)
    if not items:
        return []

    async def _classify() -> dict[str, Any]:
        active = client
        owns_client = active is None
        if active is None:
            try:
                active = ClassifyClient.from_config()
            except Exception:  # noqa: BLE001 - config probing must not raise
                active = ClassifyClient()
        try:
            if not active.available():
                return {}
            questions: dict[str, Any] = {}
            for i, item in enumerate(items):
                for name, instruction, true_label, false_label, _reason in _CAPTION_QUALITY_CHECKS:
                    questions[f"l{i}_{name}"] = question_noul(
                        instruction,
                        data={"line": item["text"]},
                        true_label=true_label,
                        false_label=false_label,
                    )
            return await active.classify({"state": "Caption quality review."}, questions)
        finally:
            if owns_client:
                await active.close()

    try:
        body = run_sync(_classify())
    except Exception as exc:  # noqa: BLE001 - advisory only, never block
        logger.warning("captions_jev_quality_failed", error=str(exc))
        return []

    answers = (body or {}).get("answers") or {}
    flags: list[dict[str, Any]] = []
    for i, item in enumerate(items):
        line_flags: list[str] = []
        reasons: list[str] = []
        for name, _instruction, _true_label, _false_label, reason in _CAPTION_QUALITY_CHECKS:
            prob = float((answers.get(f"l{i}_{name}") or {}).get("noul", 0.0) or 0.0)
            if prob >= threshold:
                line_flags.append(name)
                reasons.append(f"{reason} ({prob:.2f})")
        if line_flags:
            flags.append(
                {
                    "start": item["start"],
                    "end": item["end"],
                    "text": item["text"],
                    "flags": line_flags,
                    "reason": "; ".join(reasons),
                }
            )
    if flags:
        logger.info("captions_jev_quality", reviewed=len(items), flagged=len(flags))
    return flags


def caption_graph_layers(
    lines: Iterable[CaptionLine],
    rect: GraphRect,
    style: CaptionStyle,
    *,
    offset: float = 0.0,
    end_limit: float | None = None,
    overrides: Sequence[Mapping[str, object]] | None = None,
) -> list[GraphLayer]:
    """Turn caption lines into timeline-absolute render-graph text layers.

    ``offset`` shifts line times (which are relative to their segment) onto
    the timeline. Each line stays up ``style.linger`` seconds after its last
    word, clamped so it never overlaps the next line or passes ``end_limit``.
    With ``animation="karaoke"`` every layer also carries its words' timings
    (relative to the line start) so the renderer can highlight them in turn.
    ``overrides`` holds per-line style changes, one mapping per non-empty line
    (``captions.theme`` uses it to switch colours over light backdrops).
    """
    line_list = [line for line in lines if line.words]
    base_style: dict[str, object] = {
        "font": style.font,
        "font_size": style.font_size,
        "font_weight": style.font_weight,
        "font_color": style.font_color,
        "border_width": style.border_width,
        "border_color": style.border_color,
        "animation": style.animation,
        "text_effect": style.text_effect,
    }
    karaoke = style.animation == "karaoke"
    layers: list[GraphLayer] = []
    for index, line in enumerate(line_list):
        start = offset + line.start
        end = offset + line.end + style.linger
        if index + 1 < len(line_list):
            end = min(end, offset + line_list[index + 1].start)
        if end_limit is not None:
            end = min(end, end_limit)
        end = max(end, offset + line.end, start + 0.001)
        layer_style = dict(base_style)
        if karaoke:
            layer_style["highlight_color"] = style.highlight_color
            layer_style["upcoming_opacity"] = style.upcoming_opacity
            layer_style["requires_motion"] = True
            layer_style["words"] = [
                {"text": w.text, "start": w.start - line.start, "end": w.end - line.start}
                for w in line.words
            ]
        if overrides is not None and index < len(overrides):
            layer_style.update(overrides[index])
        layers.append(
            GraphLayer(
                kind="text",
                name="caption_line",
                rect=rect,
                text=line.text,
                style=layer_style,
                timing=GraphTiming(start=start, end=end),
            )
        )
    return layers


class CaptionsSkill:
    """Burn word-level animated captions onto the graphics composition."""

    @property
    def name(self) -> str:
        return "captions"

    def __init__(
        self,
        ctx: JobContext,
        graphics_skill: GraphicsSkill,
        style: CaptionStyle | None = None,
        brand: Brand | None = None,
        classify_client: ClassifyClient | None = None,
    ):
        self.ctx = ctx
        self.graphics_skill = graphics_skill
        # Optional TypeSafe/Jev classifier for advisory caption-quality review.
        # When None the review helper falls back to config/env; unconfigured it
        # is a no-op and the render output is byte-for-byte unchanged.
        self.classify_client = classify_client
        self._quality_flags: list[dict[str, Any]] = []
        # Priority: explicit style > brand-derived style > hard-coded default.
        # If a brand is supplied without a style, build the style from the
        # brand's fonts/colors/defaults (see ``CaptionStyle.from_brand``).
        if style is not None:
            self.style = style
        elif brand is not None:
            self.style = CaptionStyle.from_brand(brand)
        else:
            self.style = CaptionStyle()
        self.cache = SegmentCache(ctx.work_dir / ".cache" / "graphics")
        self.manifest_path = ctx.work_dir / "captions" / "captions.json"
        self._plan: CompositionPlan | None = None

    def load_edl(self) -> EDL:
        """Load the current EDL from the project."""
        edl_path = self.ctx.artifact("plan", "edl.json")
        with open(edl_path, encoding="utf-8") as f:
            return EDL(**json.load(f))

    def load_plan(self) -> CompositionPlan:
        """Load the current graphics composition plan."""
        plan_path = self.ctx.artifact("graphics", "plan.json")
        with open(plan_path, encoding="utf-8") as f:
            self._plan = CompositionPlan(**json.load(f))
        return self._plan

    def load_transcript(self) -> dict[str, Any]:
        """Load the transcript if it exists."""
        transcript_path = self.ctx.artifact("transcript", "transcript.json")
        if transcript_path.exists():
            with open(transcript_path, encoding="utf-8") as f:
                data: dict[str, Any] = json.load(f)
                return data
        return {}

    def _caption_rect(self, comp: Any) -> Any:
        """Resolve the caption band rect for a segment composition."""
        from graphics.models import Rect

        # Prefer the rect from an existing captions layer in the composition plan.
        rect = None
        for layer in comp.layers:
            if layer.name == "captions" or layer.name.startswith("caption_line"):
                rect = layer.rect
                break
        if rect is None:
            # Default to lower-center area if no preset captions layout.
            rect = Rect(
                x=0,
                y=int(comp.height * 0.75),
                width=comp.width,
                height=int(comp.height * 0.2),
            )

        # Apply bottom_padding override: pull the band down so the
        # bottom edge of the band sits `bottom_padding` pixels above
        # the canvas bottom. The band height is unchanged; only y moves.
        if self.style.bottom_padding > 0:
            rect = rect.model_copy(
                update={"y": comp.height - self.style.bottom_padding - rect.height}
            )
        elif self.style.respect_safe_area:
            # Keep the band clear of platform UI (e.g. the bottom 20% of a
            # 9:16 frame on TikTok/Shorts). An explicit bottom_padding wins.
            x, y, w, h = fit_box((rect.x, rect.y, rect.width, rect.height), comp.width, comp.height)
            rect = rect.model_copy(update={"x": x, "y": y, "width": w, "height": h})
        return rect

    def _caption_style_dict(self) -> dict[str, Any]:
        return {
            "font": self.style.font,
            "font_size": self.style.font_size,
            "font_color": self.style.font_color,
            "border_width": self.style.border_width,
            "border_color": self.style.border_color,
            "animation": self.style.animation,
        }

    def plan_graph_layers(self, plan: CompositionPlan) -> list[GraphLayer]:
        """Return timeline-absolute caption layers for the render graph.

        Unlike :meth:`_inject_captions_into_plan`, which produces per-segment
        layers for the old per-segment renderer, this returns one flat list of
        graph layers for a single whole-timeline render.
        """
        edl = self.load_edl()
        transcript = self.load_transcript()
        all_words = generate_caption_words(transcript, edl)
        layers: list[GraphLayer] = []
        for comp in plan.segments:
            rect = self._caption_rect(comp)
            lines = generate_segment_caption_lines(
                all_words, comp.timeline_start, comp.timeline_end, self.style
            )
            layers.extend(
                caption_graph_layers(
                    lines,
                    GraphRect(
                        x=int(rect.x),
                        y=int(rect.y),
                        width=int(rect.width),
                        height=int(rect.height),
                    ),
                    self.style,
                    offset=comp.timeline_start,
                    end_limit=comp.timeline_end,
                )
            )
        return layers

    def _build_caption_layers(
        self,
        comp: Any,
        lines: list[Any],
    ) -> list[Layer]:
        """Convert caption lines into timed text layers."""
        if not lines:
            return []

        rect = self._caption_rect(comp)
        style = self._caption_style_dict()

        layers: list[Layer] = []
        for index, line in enumerate(lines):
            layers.append(
                Layer(
                    type="text",
                    name=f"caption_line_{index}",
                    rect=rect,
                    text=line.text,
                    style=style,
                    timing={"start": line.start, "end": line.end},
                )
            )
        return layers

    def _inject_captions_into_plan(self, plan: CompositionPlan) -> CompositionPlan:
        """Return a new plan with per-segment timed caption layers."""
        edl = self.load_edl()
        transcript = self.load_transcript()
        all_words = generate_caption_words(transcript, edl)

        new_segments: list[Any] = []
        for comp in plan.segments:
            lines = generate_segment_caption_lines(
                all_words,
                comp.timeline_start,
                comp.timeline_end,
                self.style,
            )

            # Preserve non-caption layers and replace any existing caption layers.
            base_layers = [
                layer
                for layer in comp.layers
                if layer.name != "captions" and not layer.name.startswith("caption_line")
            ]
            caption_layers = self._build_caption_layers(comp, lines)
            new_layers = base_layers + caption_layers

            new_segments.append(comp.model_copy(update={"layers": new_layers}))

        new_plan = plan.model_copy(update={"segments": new_segments})
        return new_plan

    def plan(self) -> CompositionPlan:
        """Update the graphics composition plan with timed caption layers."""
        plan = self.load_plan()
        new_plan = self._inject_captions_into_plan(plan)

        plan_path = self.ctx.artifact("graphics", "plan.json")
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(new_plan.model_dump(), f, indent=2)

        self.graphics_skill._plan = new_plan
        self._plan = new_plan
        logger.info(
            "captions_plan_complete", job_id=self.ctx.job_id, segments=len(new_plan.segments)
        )
        return new_plan

    def render(self, plan: CompositionPlan | None = None) -> Path:
        """Render the captioned composition plan and concatenate segments."""
        if plan is None:
            plan = self._plan if self._plan is not None else self.load_plan()
        return self.graphics_skill.render(plan)

    def review_caption_quality(self, lines: Iterable[Any]) -> list[dict[str, Any]]:
        """Advisory caption-quality flags for ``lines`` (see module helper).

        Delegates to :func:`review_caption_quality`, passing this skill's
        optional classifier. Returns ``[]`` when no classifier is available or
        the call fails; the result never affects caption timing or rendering.
        """
        return review_caption_quality(lines, client=self.classify_client)

    def run(
        self,
        output_name: str = "with_captions.mp4",
    ) -> Path:
        """Add captions to the current graphics output."""
        self.ctx.ensure_dirs()
        plan = self.plan()
        # Issue C: fail loudly on a 0-word transcript. Without this guard the
        # plan is unchanged (no caption layers are injected) and the rendered
        # output is byte-identical to the upstream graphics video — the
        # pipeline would ship a caption-less deliverable with no signal that
        # the captions stage was a no-op.
        caption_layers = [
            layer
            for seg in plan.segments
            for layer in seg.layers
            if layer.name.startswith("caption_line")
        ]
        if not caption_layers:
            transcript_path = self.ctx.artifact("transcript", "transcript.json")
            raise CaptionsError(
                f"Captions skill produced 0 caption layers: the transcript at "
                f"{transcript_path} has no words remapped to the cut timeline. "
                f"This is usually because the source audio was silent when "
                f"WhisperX ran (run `ve align` first, or supply a transcript "
                f"with --transcript). Refusing to emit a caption-less output."
            )
        # Optional advisory review (Jev): annotate the manifest with caption
        # lines a human may want to fix. Purely additive — the flags are never
        # fed back into timing/rendering, and this is a no-op when Jev is
        # unconfigured, keeping the rendered output byte-for-byte unchanged.
        self._quality_flags = self.review_caption_quality(caption_layers)

        rendered = self.render(plan)

        # Move the rendered output into the captions stage directory so
        # downstream skills (music, export) can find it in the expected location.
        output_dir = self.ctx.work_dir / "captions"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / output_name
        shutil.copyfile(rendered, output)

        self.ctx.manifest().stage_done("captions", {"video": str(output)})
        self.ctx.store.save()
        self._save_manifest(output=str(output))
        logger.info("captions_complete", job_id=self.ctx.job_id, output=str(output))
        return output

    def _save_manifest(self, output: str = "") -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        plan = self._plan or self.load_plan()
        manifest = CaptionManifest(
            job_id=self.ctx.job_id,
            style=self.style,
            segment_ids=[seg.segment_id for seg in plan.segments],
            output_video=output,
            quality_flags=self._quality_flags,
        )
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest.model_dump(), f, indent=2)
