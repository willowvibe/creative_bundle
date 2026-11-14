"""Graphics skill orchestrator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
from video_editor_core.render_cache import SegmentCache
from video_editor_core.timeline import EDL, diff_edls
from video_editor_ffmpeg.cuts import apply_cuts, concat_segments
from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_ffmpeg.render_options import _add_audio_encode_args as _audio_encode
from video_editor_ffmpeg.render_options import _add_video_encode_args as _video_encode
from video_editor_ffmpeg.run import run_ffmpeg
from video_editor_ffmpeg.transitions import concat_with_transitions
from video_editor_state.job import JobContext

from graphics.models import Brand, CompositionPlan, Preset, SegmentComposition
from graphics.planner import plan_segments
from graphics.preset import load_brand, load_brand_for_job, load_preset
from graphics.renderer import render_all_segments

logger = structlog.get_logger(__name__)

# Repository root, used to anchor relative default paths so the skill works
# when the caller's current working directory is not the repo root.
REPO_ROOT = Path(__file__).resolve().parents[4]


class GraphicsSkill:
    """Orchestrate graphics composition and rendering for a job."""

    @property
    def name(self) -> str:
        return "graphics"

    def __init__(
        self,
        ctx: JobContext,
        presets_dir: Path | str | None = None,
        run_qa_per_segment: bool = False,
        render_options: RenderOptions | None = None,
    ):
        self.ctx = ctx
        self.presets_dir = Path(presets_dir) if presets_dir else REPO_ROOT / "presets"
        self.cache = SegmentCache(ctx.work_dir / ".cache" / "graphics")
        self._plan: CompositionPlan | None = None
        self.render_options = render_options or RenderOptions()
        # When True, the render loop runs the per-segment QA hook
        # (``qa.segment_check.check_segment_boundary``) at every cut.
        # Off by default because each check costs ~5% extra render time.
        self.run_qa_per_segment = run_qa_per_segment

    def load_preset(self, preset_name: str) -> Preset:
        brand = load_brand(self.presets_dir)
        return load_preset(preset_name, self.presets_dir, brand=brand)

    def load_brand_for(
        self,
        preset_name: str,
        tenant_id: str | None = None,
        job_id: str | None = None,
    ) -> Brand:
        """Resolve the brand for a specific job, using the per-tenant /
        per-project lookup chain. Falls back to the global default."""
        return load_brand_for_job(self.presets_dir, tenant_id=tenant_id, job_id=job_id)

    def plan(
        self,
        edl: EDL | None = None,
        preset_name: str = "short_explainer",
        source_video: Path | str | None = None,
        transcript_path: Path | str | None = None,
        brand: Brand | None = None,
    ) -> CompositionPlan:
        """Build a CompositionPlan from the EDL and preset.

        When ``brand`` is supplied, its logo is emitted as a logo overlay
        layer in every segment. When ``brand`` is None, no logo layer is
        added (preserves the pre-Session-N contract).
        """
        if edl is None:
            edl_path = self.ctx.artifact("plan", "edl.json")
            with open(edl_path, encoding="utf-8") as f:
                edl = EDL(**json.load(f))

        if source_video is None:
            source_video = self.ctx.work_dir / "rough_cut" / "locked.mp4"

        preset = self.load_preset(preset_name)

        transcript: dict[str, Any] = {}
        if transcript_path is None:
            transcript_path = self.ctx.artifact("transcript", "transcript.json")
        if Path(transcript_path).exists():
            with open(transcript_path, encoding="utf-8") as f:
                transcript = json.load(f)

        plan = plan_segments(edl, preset, transcript, source_video, brand=brand)
        self._plan = plan

        plan_path = self.ctx.artifact("graphics", "plan.json")
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan.model_dump(), f, indent=2)

        self.ctx.manifest().stage_done("graphics_plan", {"plan": str(plan_path)})
        self.ctx.store.save()
        logger.info("graphics_plan_complete", job_id=self.ctx.job_id, preset=preset_name)
        return plan

    def _preset_digest(self, plan: CompositionPlan) -> str:
        """Build a short stable digest of the preset + brand that produced ``plan``.

        This is mixed into segment cache keys so two runs on the same source
        segment with different presets (or a different brand) cannot collide.
        """
        import hashlib

        preset = self.load_preset(plan.preset_name)
        payload = {
            "preset_name": preset.name,
            "preset": preset.model_dump(),
            "brand": load_brand(self.presets_dir).model_dump(),
        }
        return hashlib.blake2b(
            json.dumps(payload, sort_keys=True).encode("utf-8"),
            digest_size=8,
        ).hexdigest()

    def render_composition(
        self,
        comp: SegmentComposition,
        seg_input: Path | str,
        output_dir: Path | str,
        plan: CompositionPlan,
    ) -> Path:
        """Render a single segment composition into the given output directory."""
        return render_all_segments(
            [comp],
            source_video=seg_input,
            output_dir=output_dir,
            cache=self.cache,
            preset_digest=self._preset_digest(plan),
            render_options=self.render_options,
        )[0]

    def _render_all_from_inputs(
        self,
        plan: CompositionPlan,
        segment_inputs: list[Path],
    ) -> list[Path]:
        """Render each composition against its pre-cut input and return output paths."""
        segment_dir = self.ctx.work_dir / "graphics" / "segments"
        segment_dir.mkdir(parents=True, exist_ok=True)
        final_segments: list[Path] = []
        prev_output: Path | None = None
        prev_segment_id: str | None = None
        for comp, seg_input in zip(plan.segments, segment_inputs, strict=False):
            rendered = self.render_composition(comp, seg_input, segment_dir, plan=plan)
            if self.run_qa_per_segment:
                # Imported lazily so the qa skill isn't a hard dep for
                # the v1 graphics flow that doesn't opt in.
                from qa.segment_check import check_segment_boundary

                check_segment_boundary(
                    prev_path=prev_output,
                    curr_path=rendered,
                    prev_segment_id=prev_segment_id,
                    curr_segment_id=comp.segment_id,
                    work_dir=self.ctx.work_dir,
                )
            prev_output = rendered
            prev_segment_id = comp.segment_id
            final_segments.append(rendered)
        return final_segments

    def render(self, plan: CompositionPlan | None = None) -> Path:
        """Render the composition plan and concatenate segments."""
        if plan is None:
            if self._plan is not None:
                plan = self._plan
            else:
                plan_path = self.ctx.artifact("graphics", "plan.json")
                with open(plan_path, encoding="utf-8") as f:
                    plan = CompositionPlan(**json.load(f))

        source_video = Path(plan.source_video)
        info = __import__("video_editor_ffmpeg.probe", fromlist=["probe_media"]).probe_media(
            source_video
        )
        segment_dir = self.ctx.work_dir / "graphics" / "segments"
        segment_dir.mkdir(parents=True, exist_ok=True)

        # Clamp source_end to the actual source duration to avoid empty trims.
        segment_inputs: list[Path] = []
        for comp in plan.segments:
            end = min(comp.source_end, info.duration)
            start = min(comp.source_start, end)
            seg_input = segment_dir / f"{comp.segment_id}_input.mp4"
            if end - start > 0.01:
                apply_cuts(
                    source_video, [(start, end)], seg_input, render_options=self.render_options
                )
            else:
                # Degenerate segment: produce a short black clip as placeholder.
                placeholder_cmd = [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c=black:s={comp.width}x{comp.height}:d=0.5",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=44100:cl=mono",
                    "-t",
                    "0.5",
                    "-pix_fmt",
                    "yuv420p",
                ]
                placeholder_cmd = _video_encode(placeholder_cmd, self.render_options)
                placeholder_cmd = _audio_encode(placeholder_cmd, self.render_options)
                placeholder_cmd.append(str(seg_input))
                run_ffmpeg(placeholder_cmd)
            segment_inputs.append(seg_input)

        final_segments = self._render_all_from_inputs(plan, segment_inputs)
        output = self.ctx.work_dir / "graphics" / "with_graphics.mp4"
        if self.render_options.transition != "none":
            concat_with_transitions(
                final_segments,
                output,
                transition=self.render_options.transition,  # type: ignore[arg-type]
                transition_duration=self.render_options.transition_duration,
                render_options=self.render_options,
            )
        else:
            concat_segments(final_segments, output)

        self.ctx.manifest().stage_done("graphics_render", {"video": str(output)})
        self.ctx.store.save()
        logger.info("graphics_render_complete", job_id=self.ctx.job_id, output=str(output))
        return output

    def run(
        self,
        preset_name: str = "short_explainer",
        edl: EDL | None = None,
        source_video: Path | str | None = None,
        transcript_path: Path | str | None = None,
        brand: Brand | None = None,
    ) -> Path:
        """Load preset, plan, and render graphics for the job.

        Pass ``brand=`` to enable the brand's logo overlay layer. When
        omitted, no logo is added (preserves the pre-Session-N contract).
        """
        self.ctx.ensure_dirs()
        plan = self.plan(
            edl=edl,
            preset_name=preset_name,
            source_video=source_video,
            transcript_path=transcript_path,
            brand=brand,
        )
        return self.render(plan)

    def update_and_render(self, new_edl: EDL) -> Path:
        """Re-plan and re-render the composition for an updated EDL."""
        old_edl_path = self.ctx.artifact("plan", "edl.json")
        with open(old_edl_path, encoding="utf-8") as f:
            old_edl = EDL(**json.load(f))

        diff = diff_edls(old_edl, new_edl)
        changed = set(diff.changed + diff.added)
        logger.info("partial_rerender", changed=len(changed), unchanged=len(diff.unchanged))

        with open(old_edl_path, "w", encoding="utf-8") as f:
            json.dump(new_edl.model_dump(), f, indent=2)

        plan = self.plan(edl=new_edl)
        return self.render(plan)
