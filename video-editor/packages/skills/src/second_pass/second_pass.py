"""Second Pass skill orchestrator."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import structlog
from graphics.graphics import GraphicsSkill
from graphics.models import Brand, CompositionPlan
from video_editor_core.render_cache import SegmentCache
from video_editor_core.timeline import EDL
from video_editor_llm import (
    ClassifyClient,
    question_choice,
    question_noul,
    run_sync,
)
from video_editor_state.job import JobContext

from second_pass.editor import apply_segment_edit
from second_pass.models import SecondPassManifest, SegmentEdit
from second_pass.renderer import rebuild_after_edit

logger = structlog.get_logger(__name__)


def _describe_edit(edit: SegmentEdit) -> str:
    """Render a ``SegmentEdit`` as a short, human-readable action string."""
    parts: list[str] = []
    if edit.new_source_start is not None:
        parts.append(f"trim start to {edit.new_source_start:.2f}s")
    if edit.new_source_end is not None:
        parts.append(f"trim end to {edit.new_source_end:.2f}s")
    if edit.new_label is not None:
        parts.append(f"relabel to {edit.new_label!r}")
    return "; ".join(parts) if parts else "no change"


# Rubrics for the single-choice "what is the issue" Jev question. Keys are the
# canonical issue labels the advisory notes use.
JEV_ISSUE_OPTIONS: dict[str, str] = {
    "none": "The edit is clean and improves the video's pacing or clarity.",
    "too-abrupt": "The cut is jarring, or the new bounds are too tight to breathe.",
    "redundant": "The edit removes or repeats content that was already fine.",
    "confusing-order": "The edit makes the narrative order harder to follow.",
    "weak-hook": "The edit weakens the opening hook or first impression.",
    "pacing-slow": "The edit leaves dead air or makes the pacing drag.",
}


class SecondPassSkill:
    """Orchestrate user refinements and partial re-renders after graphics."""

    def __init__(
        self,
        ctx: JobContext,
        graphics_skill: GraphicsSkill,
        classify_client: ClassifyClient | None = None,
    ):
        self.ctx = ctx
        self.graphics_skill = graphics_skill
        self.classify_client = classify_client
        self.cache = SegmentCache(ctx.work_dir / ".cache" / "graphics")
        self.manifest_path = ctx.work_dir / "second_pass" / "second_pass.json"
        self._edl: EDL | None = None
        self._edits: list[SegmentEdit] = []
        self._new_plan: CompositionPlan | None = None

    @property
    def name(self) -> str:
        return "second_pass"

    def load_edl(self) -> EDL:
        """Load the current EDL from the project."""
        edl_path = self.ctx.artifact("plan", "edl.json")
        with open(edl_path, encoding="utf-8") as f:
            self._edl = EDL(**json.load(f))
        return self._edl

    def load_plan(self) -> CompositionPlan:
        """Load the current graphics composition plan."""
        plan_path = self.ctx.artifact("graphics", "plan.json")
        with open(plan_path, encoding="utf-8") as f:
            return CompositionPlan(**json.load(f))

    def _critique_edits_with_jev(
        self,
        edits: list[SegmentEdit],
        new_edl: EDL,
    ) -> list[dict[str, Any]]:
        """Judge every applied edit with one batched Jev classify call.

        Each edit gets a ``noul`` score ("does this improve pacing and
        clarity?") and a single-choice ``issue`` label. The result is an
        advisory list of notes attached to the manifest; it never blocks the
        render. Returns ``[]`` when no classifier is available or the call
        fails, so behaviour is unchanged when Jev is unconfigured.

        The whole call (client build + classify + close) runs in one coroutine
        so the async HTTP client never crosses event loops.
        """
        if not edits:
            return []

        seg_map = new_edl.segment_map()
        context: list[dict[str, Any]] = []
        for edit in edits:
            seg = seg_map.get(edit.segment_id)
            start = seg.source_start if seg is not None else 0.0
            end = seg.source_end if seg is not None else 0.0
            context.append(
                {
                    "edit": edit.segment_id,
                    "action": _describe_edit(edit),
                    "time": f"{start:.2f}s-{end:.2f}s",
                    "reason": edit.reason or "no reason given",
                }
            )

        client = self.classify_client

        async def _classify() -> dict[str, Any]:
            active = client
            owns_client = active is None
            if active is None:
                try:
                    active = ClassifyClient.from_config()
                except Exception:  # noqa: BLE001 - config probing must not raise
                    active = ClassifyClient()
            if not active.available():
                if owns_client:
                    await active.close()
                return {}
            questions: dict[str, Any] = {}
            for i, item in enumerate(context):
                questions[f"improves{i}"] = question_noul(
                    "Does edit `edit` (action `action` at `time`) improve the "
                    "video's pacing and clarity?",
                    data=item,
                    true_label="Improves",
                    false_label="Hurts",
                )
                questions[f"issue{i}"] = question_choice(
                    "What is the main issue, if any, with edit `edit` "
                    "(action `action` at `time`, reason `reason`)?",
                    JEV_ISSUE_OPTIONS,
                    data=item,
                )
            try:
                return await active.classify(
                    {"state": "Per-edit critique of a second-pass edit decision."},
                    questions,
                )
            finally:
                if owns_client:
                    await active.close()

        try:
            body = run_sync(_classify())
        except Exception as exc:  # noqa: BLE001 - advisory only, never block
            logger.warning("second_pass_jev_critique_failed", error=str(exc))
            return []

        answers = body.get("answers") or {}
        if not answers:
            # Unavailable classifier or an empty response: stay silent.
            return []
        notes: list[dict[str, Any]] = []
        for i, item in enumerate(context):
            improves = float((answers.get(f"improves{i}") or {}).get("noul", 0.5) or 0.0)
            raw_issue = str((answers.get(f"issue{i}") or {}).get("choice", "none") or "none")
            issue = raw_issue if raw_issue in JEV_ISSUE_OPTIONS else "none"
            notes.append(
                {
                    "segment_id": item["edit"],
                    "action": item["action"],
                    "time": item["time"],
                    "improves_pacing": round(improves, 4),
                    "issue": issue,
                    "advisory": issue != "none" or improves < 0.5,
                }
            )
        logger.info("second_pass_jev_critique", edits=len(notes))
        return notes

    def apply_edit(self, edit: SegmentEdit) -> EDL:
        """Apply a single segment edit to the current EDL and plan.

        The composition plan is written immediately so it can be inspected by
        callers, but the canonical EDL is only persisted by ``render()`` after
        the re-render succeeds.
        """
        if self._edl is None:
            self.load_edl()
        edl = self._edl
        assert edl is not None

        plan = self.load_plan()
        source_video = Path(plan.source_video)
        info = __import__("video_editor_ffmpeg.probe", fromlist=["probe_media"]).probe_media(
            source_video
        )
        if info.duration is None:
            raise RuntimeError(f"Could not determine duration for {source_video}")

        new_edl = apply_segment_edit(edl, edit, info.duration)
        self._edl = new_edl
        self._edits.append(edit)

        # Preserve the original EDL before the first second-pass render so
        # the renderer can diff against it.
        old_edl_path = self.ctx.work_dir / "plan" / "edl_backup.json"
        canonical_edl_path = self.ctx.artifact("plan", "edl.json")
        if not old_edl_path.exists():
            old_edl_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(canonical_edl_path, old_edl_path)

        # Update the composition plan for the new EDL.
        preset = self.graphics_skill.load_preset(plan.preset_name)
        transcript: dict[str, Any] = {}
        transcript_path = self.ctx.artifact("transcript", "transcript.json")
        if transcript_path.exists():
            with open(transcript_path, encoding="utf-8") as f:
                transcript = json.load(f)

        from graphics.planner import plan_segments

        # Bug #57: only re-apply the brand when the original plan already
        # carried logo layers. Otherwise a global brand with a logo path
        # would silently add overlays that the first graphics pass did not
        # have, breaking tests and altering renders that were produced
        # without a brand.
        has_logo = any(
            layer.type == "logo" for segment in plan.segments for layer in segment.layers
        )
        brand: Brand | None = None
        if has_logo:
            brand = self.graphics_skill.load_brand_for(
                plan.preset_name,
                tenant_id=self.ctx.tenant_id,
                job_id=self.ctx.job_id,
            )
        self._new_plan = plan_segments(new_edl, preset, transcript, source_video, brand=brand)
        self.graphics_skill._plan = self._new_plan

        plan_path = self.ctx.artifact("graphics", "plan.json")
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(self._new_plan.model_dump(), f, indent=2)

        self._save_manifest()
        logger.info(
            "second_pass_edit_applied",
            segment_id=edit.segment_id,
            new_duration=new_edl.duration(),
        )
        return new_edl

    def render(self) -> Path:
        """Re-render only changed segments and rebuild the final output.

        The new EDL and composition plan are persisted only after the render
        succeeds so a failure does not leave the project diffing against an
        unrendered plan.
        """
        if self._edl is None:
            self.load_edl()
        new_edl = self._edl
        assert new_edl is not None

        if self._new_plan is None:
            self._new_plan = self.load_plan()

        # Load the old EDL from the most recent second pass manifest if available,
        # otherwise from the original plan backup.
        old_edl_path = self.ctx.work_dir / "plan" / "edl_backup.json"
        if not old_edl_path.exists():
            # Before first second pass, backup the original EDL.
            original = self.ctx.artifact("plan", "edl.json")
            old_edl_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, old_edl_path)

        with open(old_edl_path, encoding="utf-8") as f:
            old_edl = EDL(**json.load(f))

        plan = self._new_plan
        output = rebuild_after_edit(
            old_edl, new_edl, plan, self.graphics_skill, self.cache, edits=self._edits
        )

        # Commit the new EDL and plan only after a successful render.
        edl_path = self.ctx.artifact("plan", "edl.json")
        edl_path.parent.mkdir(parents=True, exist_ok=True)
        with open(edl_path, "w", encoding="utf-8") as f:
            json.dump(new_edl.model_dump(), f, indent=2)

        plan_path = self.ctx.artifact("graphics", "plan.json")
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan.model_dump(), f, indent=2)

        # After rendering, update backup to the current state so the next edit
        # diffs against it.
        with open(old_edl_path, "w", encoding="utf-8") as f:
            json.dump(new_edl.model_dump(), f, indent=2)

        # Advisory per-edit critique (Jev). Additive: empty when the
        # classifier is unavailable or errors, in which case behaviour is
        # exactly as before.
        critique = self._critique_edits_with_jev(self._edits, new_edl)

        self.ctx.manifest().stage_done("second_pass", {"video": str(output), "critique": critique})
        self.ctx.store.save()
        self._save_manifest(output=str(output), critique=critique)
        logger.info("second_pass_complete", job_id=self.ctx.job_id, output=str(output))
        return output

    def run(self, edit: SegmentEdit) -> Path:
        """Apply an edit and re-render the final output."""
        self.ctx.ensure_dirs()
        self.apply_edit(edit)
        return self.render()

    def _save_manifest(
        self,
        output: str = "",
        critique: list[dict[str, Any]] | None = None,
    ) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = SecondPassManifest(
            job_id=self.ctx.job_id,
            edits=list(self._edits),
            output_video=output,
            critique=critique or [],
        )
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest.model_dump(), f, indent=2)
