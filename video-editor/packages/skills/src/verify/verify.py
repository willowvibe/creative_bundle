"""Requirements verification gate skill."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, cast

import structlog
from video_editor_ffmpeg.probe import probe_media
from video_editor_llm import ClassifyClient, question_choice
from video_editor_llm.router import ModelRouter
from video_editor_state.job import JobContext

from verify.deterministic import (
    check_aspect_ratio,
    check_duration,
    check_filler_words,
    check_forbidden_element,
    check_must_keep_segments,
    check_must_mention,
    check_required_element,
)
from verify.models import RequirementsSpec, VerificationCheck, VerificationReport

logger = structlog.get_logger(__name__)


def _as_verdict(result: str) -> Literal["PASS", "FAIL", "UNSURE"]:
    """Narrow a deterministic result string to the allowed literal set."""
    return cast(Literal["PASS", "FAIL", "UNSURE"], result)


_VERIFICATION_STAGE = "verify"


class VerificationError(RuntimeError):
    """Raised when a requirement check fails and the job should be blocked."""


class VerifySkill:
    """Run deterministic + LLM-as-judge checks against a rendered video."""

    def __init__(
        self,
        ctx: JobContext,
        requirements: RequirementsSpec | None = None,
        router: ModelRouter | None = None,
        classify_client: ClassifyClient | None = None,
    ):
        self.ctx = ctx
        self.requirements = requirements
        self.router = router
        self.classify_client = classify_client

    @property
    def name(self) -> str:
        return "verify"

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------
    def _requirements_path(self) -> Path:
        return self.ctx.work_dir / "verify" / "requirements.json"

    def _report_path(self) -> Path:
        return self.ctx.work_dir / "verify" / "verification_report.json"

    def _final_video_path(self) -> Path:
        """Locate the exported final video."""
        export = self.ctx.manifest().stages.get("export", {})
        final = export.get("outputs", {}).get("final_video")
        if final:
            return Path(final)
        # Fallback: look for the copied output in the conventional output dir.
        for candidate in (
            self.ctx.output_dir / "final.mp4",
            self.ctx.work_dir / "export" / "final.mp4",
        ):
            if candidate.exists():
                return candidate
        raise FileNotFoundError(f"No exported final video found for {self.ctx.job_id}")

    # ------------------------------------------------------------------
    # Requirements persistence
    # ------------------------------------------------------------------
    def load_requirements(self) -> RequirementsSpec | None:
        """Read requirements captured earlier from the work dir."""
        path = self._requirements_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return RequirementsSpec.model_validate(data)
        except Exception as exc:  # noqa: BLE001 - re-raised as a typed gate error
            logger.warning("requirements_load_failed", path=str(path), error=str(exc))
            raise VerificationError(f"requirements.json is invalid: {exc}") from exc

    def save_requirements(self, requirements: RequirementsSpec) -> None:
        """Persist requirements so they are available after intake/export."""
        path = self._requirements_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            requirements.model_dump_json(indent=2),
            encoding="utf-8",
        )
        self.ctx.manifest().log_operation(
            operation="requirements_captured",
            inputs=requirements.model_dump(exclude_none=True),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _load_transcript(self) -> list[dict[str, Any]] | None:
        """Load a WhisperX-style transcript if one exists."""
        path = self.ctx.work_dir / "transcript" / "transcript.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "words" in data:
                return cast(list[dict[str, Any]], data["words"])
            return None
        except Exception:  # noqa: BLE001 - optional transcript/plan metadata
            return None

    def _load_plan_or_edl(self) -> Any:
        """Load a scene plan or rough-cut EDL for segment-keeping checks."""
        for path in (
            self.ctx.work_dir / "plan" / "scene_plan.json",
            self.ctx.work_dir / "rough_cut" / "edl.json",
        ):
            if path.exists():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001 - optional transcript/plan metadata
                    return None
        return None

    # ------------------------------------------------------------------
    # Deterministic checks
    # ------------------------------------------------------------------
    def _run_deterministic_checks(self, video_path: Path) -> list[VerificationCheck]:
        """Run every deterministic check in the spec."""
        requirements = self.requirements
        if requirements is None:
            return []

        info = probe_media(video_path)
        work_dir = self.ctx.work_dir
        transcript = self._load_transcript()
        plan_or_edl = self._load_plan_or_edl()

        checks: list[VerificationCheck] = []

        if requirements.target_duration_range is not None:
            result, reason, actual = check_duration(
                info,
                requirements.target_duration_range.min,
                requirements.target_duration_range.max,
            )
            checks.append(
                VerificationCheck(
                    requirement="duration_within_range",
                    result=_as_verdict(result),
                    actual=actual,
                    reason=reason,
                )
            )

        if requirements.aspect_ratio:
            result, reason, actual = check_aspect_ratio(info, requirements.aspect_ratio)
            checks.append(
                VerificationCheck(
                    requirement=f"aspect_ratio_{requirements.aspect_ratio}",
                    result=_as_verdict(result),
                    actual=actual,
                    reason=reason,
                )
            )

        for element in requirements.required_elements:
            result, reason, actual = check_required_element(element, work_dir, info)
            checks.append(
                VerificationCheck(
                    requirement=f"required_element:{element}",
                    result=_as_verdict(result),
                    actual=actual,
                    reason=reason,
                )
            )

        for element in requirements.forbidden_elements:
            result, reason, actual = check_forbidden_element(element, work_dir, info)
            checks.append(
                VerificationCheck(
                    requirement=f"forbidden_element:{element}",
                    result=_as_verdict(result),
                    actual=actual,
                    reason=reason,
                )
            )

        cc = requirements.content_constraints
        if cc.must_mention:
            result, reason, actual = check_must_mention(cc.must_mention, transcript)
            checks.append(
                VerificationCheck(
                    requirement="must_mention",
                    result=_as_verdict(result),
                    actual=actual,
                    reason=reason,
                )
            )

        if cc.remove_filler_words:
            result, reason, actual = check_filler_words(transcript)
            checks.append(
                VerificationCheck(
                    requirement="remove_filler_words",
                    result=_as_verdict(result),
                    actual=actual,
                    reason=reason,
                )
            )

        if cc.must_keep_segments:
            result, reason, actual = check_must_keep_segments(cc.must_keep_segments, plan_or_edl)
            checks.append(
                VerificationCheck(
                    requirement="must_keep_segments",
                    result=_as_verdict(result),
                    actual=actual,
                    reason=reason,
                )
            )

        if requirements.style_preset:
            # Style presets are verified at intake; runtime only checks that
            # the chosen style was recorded in the manifest.
            chosen = (
                self.ctx.manifest()
                .stages.get("template", {})
                .get("outputs", {})
                .get("style_preset")
            )
            ok = chosen == requirements.style_preset
            checks.append(
                VerificationCheck(
                    requirement=f"style_preset:{requirements.style_preset}",
                    result="PASS" if ok else "FAIL",
                    actual=chosen,
                    reason="style preset recorded"
                    if ok
                    else f"expected {requirements.style_preset!r}, got {chosen!r}",
                )
            )

        return checks

    # ------------------------------------------------------------------
    # LLM judge
    # ------------------------------------------------------------------
    def _build_judge_prompt(
        self,
        video_path: Path,
        requirements: RequirementsSpec,
        deterministic_checks: list[VerificationCheck],
    ) -> str:
        """Compose the LLM-as-judge prompt."""
        checks_text = (
            "\n".join(f"- {c.requirement}: {c.result} — {c.reason}" for c in deterministic_checks)
            or "_No deterministic checks available._"
        )
        checklist_text = (
            "\n".join(f"- {item}" for item in requirements.judge_checklist)
            or "_No judge checklist provided._"
        )

        return (
            "You are reviewing a rendered video against a requirements spec.\n\n"
            f"Video path: {video_path}\n"
            f"Job ID: {requirements.job_id or self.ctx.job_id}\n\n"
            "Deterministic checks already run:\n"
            f"{checks_text}\n\n"
            "Judge checklist (subjective / content checks):\n"
            f"{checklist_text}\n\n"
            "For each checklist item, return one of PASS/FAIL/UNSURE and a brief reason.\n"
            "Then provide an overall verdict (PASS, FAIL, or NEEDS_REVIEW) and a short summary.\n"
            "Respond ONLY with valid JSON in this exact shape:\n"
            "{\n"
            '  "item_checks": [\n'
            '    {"item": "string", "result": "PASS|FAIL|UNSURE", "reason": "string"}\n'
            "  ],\n"
            '  "overall": "PASS|FAIL|NEEDS_REVIEW",\n'
            '  "summary": "string"\n'
            "}\n"
        )

    async def _run_jev_judge(
        self,
        video_path: Path,
        requirements: RequirementsSpec,
        deterministic_checks: list[VerificationCheck],
    ) -> VerificationCheck | None:
        """Batched Jev judge: one classify call scores every checklist item
        (PASS/FAIL/UNSURE) plus an overall verdict. Returns None when no
        classifier is available so the caller falls back to the LLM judge."""
        client = self.classify_client
        owns_client = False
        if client is None:
            try:
                client = ClassifyClient.from_config()
            except Exception:  # noqa: BLE001 - config probing must not raise
                client = ClassifyClient()
            owns_client = True
        if not client.available():
            if owns_client:
                await client.close()
            return None

        checks_text = (
            "\n".join(f"- {c.requirement}: {c.result} — {c.reason}" for c in deterministic_checks)
            or "_No deterministic checks available._"
        )
        context = (
            f"Job {requirements.job_id or self.ctx.job_id}. "
            f"Rendered video: {video_path.name}. "
            f"Deterministic checks already run:\n{checks_text}"
        )

        questions: dict[str, Any] = {}
        for i, item in enumerate(requirements.judge_checklist):
            questions[f"item{i}"] = question_choice(
                "Given the context, does `item` PASS, FAIL, or is it UNSURE?",
                {
                    "PASS": "The item is clearly satisfied.",
                    "FAIL": "The item is clearly not satisfied.",
                    "UNSURE": "Cannot be decided from the available context.",
                },
                data={"context": context[:4000], "item": item},
            )
        questions["overall"] = question_choice(
            "Overall, does the render meet the requirements? Choose PASS only if no item failed.",
            {
                "PASS": "All checklist items pass.",
                "FAIL": "At least one item clearly fails.",
                "UNSURE": "The outcome cannot be decided.",
            },
            data={"context": context[:4000]},
        )

        try:
            body = await client.classify({"state": "Verify rendered video."}, questions)
        except Exception as exc:  # noqa: BLE001 - any classify error → fall back
            logger.warning("jev_judge_failed", error=str(exc))
            return None
        finally:
            if owns_client:
                await client.close()

        answers = body.get("answers") or {}
        item_checks = [
            {
                "item": item,
                "result": _as_verdict(str((answers.get(f"item{i}") or {}).get("choice", "UNSURE"))),
                "reason": "jev classifier",
            }
            for i, item in enumerate(requirements.judge_checklist)
        ]
        overall = _as_verdict(str((answers.get("overall") or {}).get("choice", "UNSURE")))
        if any(c["result"] == "FAIL" for c in item_checks):
            overall = "FAIL"
        summary = f"Jev judge: {sum(1 for c in item_checks if c['result'] == 'PASS')}/{len(item_checks)} items pass"
        return VerificationCheck(
            requirement="llm_judge",
            result=overall,
            actual={"item_checks": item_checks, "summary": summary, "judge": "jev"},
            reason=summary,
        )

    async def _run_llm_judge(
        self,
        video_path: Path,
        requirements: RequirementsSpec,
        deterministic_checks: list[VerificationCheck],
    ) -> VerificationCheck:
        """Run the LLM-as-judge and return a single aggregated check."""
        if not requirements.judge_checklist:
            return VerificationCheck(
                requirement="llm_judge",
                result="PASS",
                actual=None,
                reason="no judge checklist configured",
            )

        # Prefer the cheap, calibrated Jev judge when available.
        jev_check = await self._run_jev_judge(video_path, requirements, deterministic_checks)
        if jev_check is not None:
            return jev_check

        if self.router is None:
            return VerificationCheck(
                requirement="llm_judge",
                result="UNSURE",
                actual=None,
                reason="LLM router not provided",
            )

        prompt = self._build_judge_prompt(video_path, requirements, deterministic_checks)
        try:
            response_text = await self.router.generate(
                task_type="verify_judge",
                prompt=prompt,
                system=(
                    "You are a strict video requirements judge. "
                    "Return only valid JSON. Do not add markdown formatting or commentary outside the JSON."
                ),
            )
            parsed = json.loads(response_text.strip())
            overall = parsed.get("overall", "UNSURE")
            item_checks = parsed.get("item_checks", [])
            summary = parsed.get("summary", "no summary")
            return VerificationCheck(
                requirement="llm_judge",
                result=_as_verdict(overall if overall in {"PASS", "FAIL", "UNSURE"} else "UNSURE"),
                actual={"item_checks": item_checks, "summary": summary},
                reason=summary,
            )
        except json.JSONDecodeError as exc:
            logger.warning(
                "llm_judge_json_parse_failed", error=str(exc), response=response_text[:200]
            )
            return VerificationCheck(
                requirement="llm_judge",
                result="UNSURE",
                actual=None,
                reason=f"judge returned non-JSON: {exc}",
            )
        except Exception as exc:  # noqa: BLE001 - optional transcript/plan metadata
            logger.warning("llm_judge_failed", error=str(exc))
            return VerificationCheck(
                requirement="llm_judge",
                result="UNSURE",
                actual=None,
                reason=f"judge failed: {exc}",
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def run(
        self,
        requirements: RequirementsSpec | None = None,
        raise_on_fail: bool = True,
    ) -> VerificationReport:
        """Run the verification gate against the exported video.

        Args:
            requirements: If provided, persist and use these requirements.
                Otherwise load any previously saved requirements.
            raise_on_fail: When True (default), raise ``VerificationError``
                if the overall verdict is FAIL.

        Returns:
            The structured ``VerificationReport``.
        """
        if requirements is not None:
            self.save_requirements(requirements)
            self.requirements = requirements
        else:
            self.requirements = self.requirements or self.load_requirements()

        if self.requirements is None:
            raise VerificationError("No requirements provided and none found in work dir")

        video_path = self._final_video_path()
        if not video_path.exists():
            raise VerificationError(f"Final video not found: {video_path}")

        self.ctx.ensure_dirs()

        deterministic_checks = self._run_deterministic_checks(video_path)
        judge_check = await self._run_llm_judge(video_path, self.requirements, deterministic_checks)
        all_checks = [*deterministic_checks, judge_check]

        report = VerificationReport(
            job_id=self.ctx.job_id,
            overall="PASS",
            checks=all_checks,
        )

        self._report_path().parent.mkdir(parents=True, exist_ok=True)
        self._report_path().write_text(
            report.model_dump_json(indent=2),
            encoding="utf-8",
        )

        self.ctx.manifest().stage_done(
            _VERIFICATION_STAGE,
            {
                "verification_report": str(self._report_path()),
                "overall": report.overall,
                "checks_count": len(report.checks),
                "fail_count": sum(1 for c in report.checks if c.result == "FAIL"),
            },
        )
        self.ctx.store.save()

        logger.info(
            "verification_complete",
            job_id=self.ctx.job_id,
            overall=report.overall,
            checks=len(report.checks),
        )

        if raise_on_fail and report.overall == "FAIL":
            raise VerificationError(
                f"Verification failed for {self.ctx.job_id}: "
                f"{[c.requirement for c in report.checks if c.result == 'FAIL']}"
            )

        return report
