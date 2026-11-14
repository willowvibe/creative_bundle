"""Export skill orchestrator."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from video_editor_asset_ingestion.budget import BudgetGovernor
from video_editor_ffmpeg.probe import probe_media
from video_editor_state.job import JobContext

from export.models import ExportManifest, ExportStyle

logger = structlog.get_logger(__name__)

# Bug #73: prefer broll-mixed video so Music/Export/QA agree on the final
# candidate. Order is newest skill stage → oldest fallback.
DEFAULT_INPUT_SEARCH = [
    # The render-graph pipeline (graphics + captions + music in one pass)
    # writes here; it is the polished deliverable.
    "graph/composed.mp4",
    "music/with_music.mp4",
    "captions/with_captions.mp4",
    "graphics/with_graphics.mp4",
    # B-roll is applied before graphics, so it ranks below both.
    "broll/with_broll.mp4",
    "rough_cut/locked.mp4",
]


class ExportSkill:
    """Export the final video, report, and decision log for a job."""

    @property
    def name(self) -> str:
        return "export"

    def __init__(
        self,
        ctx: JobContext,
        style: ExportStyle | None = None,
    ):
        self.ctx = ctx
        self.style = style or ExportStyle()
        self.manifest_path = ctx.work_dir / "export" / "export.json"

    def _find_input_video(self, explicit: Path | None = None) -> Path:
        """Locate the video to export.

        An explicit path from the caller wins. Otherwise fall back to the
        known skill output locations, most-polished first.
        """
        if explicit is not None:
            return Path(explicit)
        for relative in DEFAULT_INPUT_SEARCH:
            candidate = self.ctx.work_dir / relative
            if candidate.exists():
                return candidate
        raise FileNotFoundError(
            f"No input video found for job {self.ctx.job_id}. "
            "Run rough-cut, graphics, captions, or music first."
        )

    def _load_decisions(self) -> list[dict[str, Any]]:
        """Collect operation logs as decisions.

        If the pipeline did not explicitly record operations, fall back to
        the completed stage summaries in the manifest so the decision log
        is never empty for a real run.
        """
        operations = self.ctx.manifest().operations
        if operations:
            return [
                {
                    "timestamp": op.timestamp,
                    "operation": op.operation,
                    "inputs": op.inputs,
                    "outputs": op.outputs,
                }
                for op in operations
            ]

        decisions: list[dict[str, Any]] = []
        for stage, meta in self.ctx.manifest().stages.items():
            decisions.append(
                {
                    "timestamp": meta.get("completed_at", ""),
                    "operation": stage,
                    "inputs": {},
                    "outputs": meta.get("outputs", {}),
                }
            )
        return decisions

    def _build_report(
        self,
        input_video: Path,
        final_video: Path,
        duration: float,
    ) -> str:
        """Render a Markdown report summarizing the job."""
        manifest = self.ctx.manifest()
        lines: list[str] = []
        lines.append(f"# Export Report — {self.ctx.job_id}")
        lines.append("")
        lines.append(f"- **Generated**: {datetime.now(UTC).isoformat()}")
        lines.append(f"- **Final video**: `{final_video}`")
        lines.append(f"- **Source video**: `{input_video}`")
        lines.append(f"- **Duration**: {duration:.3f}s")
        lines.append("")
        # Project brief — only when a brief is present, either supplied
        # at JobContext construction (ctx.brief) or auto-discovered from
        # ``<work_dir>/project.md`` (load_brief_from_workdir). The
        # auto-discovery happens lazily on first read so skills that
        # don't need a brief pay nothing for the lookup.
        brief = self.ctx.brief
        if brief is None:
            brief = self.ctx.load_brief_from_workdir()
        if brief is not None:
            lines.append("## Project Brief")
            lines.append("")
            if brief.title:
                lines.append(f"- **Title**: {brief.title}")
            if brief.audience:
                lines.append(f"- **Audience**: {brief.audience}")
            if brief.tone:
                lines.append(f"- **Tone**: {brief.tone}")
            if brief.target_duration_s is not None:
                lines.append(f"- **Target duration**: {brief.target_duration_s}s")
            if brief.key_segments:
                lines.append("- **Key segments**: " + ", ".join(brief.key_segments))
            if brief.brand:
                lines.append(f"- **Brand**: {brief.brand}")
            if brief.notes:
                lines.append("")
                lines.append(brief.notes)
            lines.append("")
        lines.append("## Completed Stages")
        lines.append("")
        if manifest.stages:
            for stage, meta in manifest.stages.items():
                completed = meta.get("completed_at", "unknown")
                outputs = meta.get("outputs", {})
                lines.append(f"- **{stage}** — {completed}")
                for key, value in outputs.items():
                    lines.append(f"  - {key}: `{value}`")
        else:
            lines.append("_No stages recorded._")
        lines.append("")
        lines.append("## Decisions")
        lines.append("")
        decisions = self._load_decisions()
        if decisions:
            for decision in decisions:
                lines.append(
                    f"- `{decision['timestamp']}` — {decision['operation']}: "
                    f"{decision.get('outputs', {})}"
                )
        else:
            lines.append("_No decisions recorded._")
        lines.append("")

        # Budget / spend ledger — appended when the governor wrote budget.json.
        lines.extend(self._build_budget_section())

        # Verification gate — appended when the gate has already run.
        verify_report_path = self.ctx.work_dir / "verify" / "verification_report.json"
        if verify_report_path.exists():
            lines.append("## Requirements Verification")
            lines.append("")
            try:
                data = json.loads(verify_report_path.read_text(encoding="utf-8"))
                lines.append(f"- **Overall**: {data.get('overall', 'unknown')}")
                lines.append("")
                for check in data.get("checks", []):
                    result = check.get("result", "UNKNOWN")
                    reason = check.get("reason", "")
                    req = check.get("requirement", "unknown")
                    lines.append(f"- **{req}**: {result} — {reason}")
            except Exception as exc:  # noqa: BLE001 - optional artifact enrichment
                lines.append(f"_Could not read verification report: {exc}_")
            lines.append("")

        return "\n".join(lines)

    def _build_budget_section(self) -> list[str]:
        """Render a Markdown budget/spend section from ``budget/budget.json``."""
        budget_path = self.ctx.work_dir / "budget" / "budget.json"
        lines: list[str] = []
        if not budget_path.exists():
            return lines

        lines.append("## Budget / Spend")
        lines.append("")
        try:
            governor = BudgetGovernor.load(budget_path)
            snap = governor.snapshot()
            limit_text = f"{snap.limit:.2f}" if snap.limit is not None else "unlimited"
            remaining_text = (
                f"{snap.estimated_remaining:.2f}"
                if snap.estimated_remaining != float("inf")
                else "unlimited"
            )
            lines.append(f"- **Budget limit**: {limit_text} credits")
            lines.append(f"- **Total spent**: {snap.spent:.2f} credits")
            lines.append(f"- **Estimated remaining**: {remaining_text} credits")
            lines.append(f"- **Line items**: {snap.estimate_count}")
            lines.append("")

            # Group by provider/task for a concise breakdown.
            grouped: dict[tuple[str, str], dict[str, float]] = {}
            for estimate in governor.ledger():
                key = (estimate.provider, estimate.task_type)
                bucket = grouped.setdefault(key, {"estimated": 0.0, "actual": 0.0, "count": 0})
                bucket["estimated"] += estimate.estimated_cost
                bucket["actual"] += estimate.actual_cost or estimate.estimated_cost
                bucket["count"] += 1

            if grouped:
                lines.append("### Provider breakdown")
                lines.append("")
                lines.append("| Provider | Task | Calls | Estimated | Actual |")
                lines.append("|---|---|---:|---:|---:|")
                for (provider, task_type), bucket in sorted(grouped.items()):
                    lines.append(
                        f"| {provider} | {task_type} | {bucket['count']} | "
                        f"{bucket['estimated']:.2f} | {bucket['actual']:.2f} |"
                    )
                lines.append("")
        except Exception as exc:  # noqa: BLE001 - optional artifact enrichment
            lines.append(f"_Could not read budget ledger: {exc}_")
            lines.append("")
        return lines

    def append_post_render_review(self) -> None:
        """Append the post-render review section to the export Markdown report.

        The review gate runs after export, so the export report is written
        before review_report.json exists. Calling this after the review
        stage completes updates the deliverable report without regenerating
        the whole document.
        """
        review_report_path = self.ctx.work_dir / "review" / "review_report.json"
        report_path = self.ctx.output_dir / self.style.report_name
        decision_log_path = self.ctx.output_dir / self.style.decision_log_name
        if not review_report_path.exists() or not report_path.exists():
            return

        lines: list[str] = []
        lines.append("## Post-Render Review")
        lines.append("")
        try:
            data = json.loads(review_report_path.read_text(encoding="utf-8"))
            lines.append(f"- **Overall**: {data.get('overall', 'unknown')}")
            lines.append(f"- **Passed**: {data.get('passed', False)}")
            lines.append("")
            for check in data.get("checks", []):
                result = check.get("result", "UNKNOWN")
                category = check.get("category", "quality")
                reason = check.get("reason", "")
                name = check.get("name", "unknown")
                lines.append(f"- **[{category}] {name}**: {result} — {reason}")
        except Exception as exc:  # noqa: BLE001 - optional artifact enrichment
            lines.append(f"_Could not read post-render review report: {exc}_")
        lines.append("")

        report_path.write_text(
            report_path.read_text(encoding="utf-8") + "\n" + "\n".join(lines),
            encoding="utf-8",
        )

        # Append a decision log entry so the review verdict is auditable.
        decision_log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(review_report_path.read_text(encoding="utf-8"))
            entry = {
                "timestamp": datetime.now(UTC).isoformat(),
                "operation": "post_render_review",
                "inputs": {"video_path": data.get("video_path")},
                "outputs": {
                    "overall": data.get("overall"),
                    "passed": data.get("passed"),
                    "checks": data.get("checks", []),
                },
            }
            with open(decision_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False))
                f.write("\n")
        except Exception as exc:  # noqa: BLE001 - optional artifact enrichment
            logger.warning("append_review_decision_log_failed", error=str(exc))

    def _write_decision_log(self, path: Path) -> None:
        """Write the decisions as newline-delimited JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for decision in self._load_decisions():
                f.write(json.dumps(decision, ensure_ascii=False))
                f.write("\n")

    def run(self, output_name: str | None = None, input_video: Path | None = None) -> Path:
        """Export final video, report, and decision log.

        ``input_video`` should be the pipeline's actual deliverable (e.g. the
        render graph's ``graph/composed.mp4``); when omitted the skill falls
        back to :data:`DEFAULT_INPUT_SEARCH`.
        """
        self.ctx.ensure_dirs()

        input_video = self._find_input_video(input_video)
        output_name = output_name or self.style.output_name
        final_video = self.ctx.output_dir / output_name
        report_path = self.ctx.output_dir / self.style.report_name
        decision_log_path = self.ctx.output_dir / self.style.decision_log_name

        final_video.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(input_video, final_video)
        info = probe_media(final_video)
        if info.duration is None:
            raise RuntimeError(f"Could not determine duration for {final_video}")

        report = self._build_report(input_video, final_video, info.duration)
        report_path.write_text(report, encoding="utf-8")
        self._write_decision_log(decision_log_path)

        self.ctx.manifest().stage_done(
            "export",
            {
                "final_video": str(final_video),
                "report": str(report_path),
                "decision_log": str(decision_log_path),
                "duration": info.duration,
            },
        )
        self.ctx.store.save()
        self._save_manifest(
            input_video=str(input_video),
            final_video=str(final_video),
            report=str(report_path),
            decision_log=str(decision_log_path),
            duration=info.duration,
        )
        logger.info(
            "export_complete",
            job_id=self.ctx.job_id,
            final_video=str(final_video),
        )
        return final_video

    def _save_manifest(
        self,
        input_video: str,
        final_video: str,
        report: str,
        decision_log: str,
        duration: float,
    ) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = ExportManifest(
            job_id=self.ctx.job_id,
            style=self.style,
            input_video=input_video,
            final_video=final_video,
            report=report,
            decision_log=decision_log,
            duration=duration,
        )
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest.model_dump(), f, indent=2)
