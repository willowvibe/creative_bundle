"""Long-form videos as chapter packs (#315).

Each chapter is an ordinary content pack rendered as its own job, so a failed
chapter can be re-run without paying for the others. The chapter files are then
joined (hard cut or crossfade) and the joined file gets one measured QA pass.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field
from video_editor_core.content_pack import load_content_pack
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, run_ffmpeg
from video_editor_state.job import JobContext

from video_editor_pipelines.short_pipeline import (
    estimate_short,
    preflight_short,
    run_short_job,
)

logger = structlog.get_logger(__name__)


class LongformError(RuntimeError):
    """A chapter pack or the join cannot proceed."""


class LongformChapter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack: str
    title: str = ""


class LongformPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    chapters: list[LongformChapter] = Field(min_length=1)
    transition: Literal["cut", "crossfade"] = "cut"
    transition_seconds: float = Field(default=0.5, gt=0.0, le=2.0)
    output_name: str = "final.mp4"


@dataclass(frozen=True)
class LongformResult:
    final_path: Path
    chapter_paths: tuple[Path, ...]
    qa_path: Path | None
    passed: bool


def load_longform(path: Path | str) -> LongformPlan:
    """Read ``longform.yaml`` and resolve chapter packs next to it."""
    source = Path(path)
    try:
        data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise LongformError(f"cannot read {source}: {exc}") from exc
    plan = LongformPlan.model_validate(data)
    for chapter in plan.chapters:
        resolved = (source.parent / chapter.pack).resolve()
        if not resolved.is_file():
            raise LongformError(f"chapter pack not found: {resolved}")
        chapter.pack = str(resolved)
    return plan


def _concat_list(paths: Sequence[Path], target: Path) -> None:
    lines = [f"file '{path.as_posix()}'" for path in paths]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def join_chapters(
    paths: Sequence[Path],
    output: Path,
    *,
    transition: str = "cut",
    transition_seconds: float = 0.5,
) -> Path:
    """Join chapter renders. ``cut`` is a stream copy; ``crossfade`` uses xfade."""
    if not paths:
        raise LongformError("no chapters to join")
    output.parent.mkdir(parents=True, exist_ok=True)
    if transition == "cut" or len(paths) == 1:
        listing = output.with_suffix(".concat.txt")
        _concat_list(paths, listing)
        completed = run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-c",
                "copy",
                str(output),
            ],
            timeout=RENDER_TIMEOUT_SECONDS,
        )
        listing.unlink(missing_ok=True)
        if completed.returncode != 0:
            raise LongformError(f"chapter join failed: {completed.stderr[-400:]}")
        return output

    if transition != "crossfade":
        raise LongformError(f"unknown chapter transition {transition!r}")

    durations = [float(probe_media(str(path)).duration or 0.0) for path in paths]
    if any(duration <= transition_seconds for duration in durations):
        raise LongformError(
            f"every chapter must be longer than the {transition_seconds}s transition"
        )
    inputs: list[str] = []
    for path in paths:
        inputs.extend(["-i", str(path)])

    video = "[0:v]"
    audio = "[0:a]"
    offset = 0.0
    filters: list[str] = []
    for index in range(1, len(paths)):
        offset += durations[index - 1] - transition_seconds
        next_video = f"[v{index}]"
        next_audio = f"[a{index}]"
        filters.append(
            f"{video}[{index}:v]xfade=transition=fade:duration={transition_seconds:.3f}:"
            f"offset={offset:.3f}{next_video}"
        )
        filters.append(
            f"{audio}[{index}:a]acrossfade=d={transition_seconds:.3f}:c1=tri:c2=tri{next_audio}"
        )
        video, audio = next_video, next_audio

    last = len(paths) - 1
    completed = run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex",
            ";".join(filters),
            "-map",
            video,
            "-map",
            audio,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(output),
        ],
        timeout=RENDER_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise LongformError(f"chapter crossfade failed: {completed.stderr[-400:]}")
    logger.info("longform_crossfade", chapters=len(paths), seconds=last * transition_seconds)
    return output


def run_longform(
    ctx: JobContext,
    plan: LongformPlan,
    *,
    config_path: Path | None = None,
    auto_approve: bool = False,
    final: bool = False,
    run_qa: bool = True,
    render_chapter: Callable[[JobContext, Path, bool], Path] | None = None,
    join: Callable[..., Path] = join_chapters,
) -> LongformResult:
    """Render every chapter as its own job, then join and gate the result."""
    ctx.ensure_dirs()
    chapter_paths: list[Path] = []
    for index, chapter in enumerate(plan.chapters):
        pack_path = Path(chapter.pack)
        try:
            pack = load_content_pack(pack_path)
            tokens = preflight_short(pack)
        except Exception as exc:  # noqa: BLE001 - report the pack and stop
            raise LongformError(f"chapter {index} ({pack_path}): {exc}") from exc
        chapter_ctx = JobContext(
            job_id=f"{plan.job_id}-ch{index:02d}",
            work_root=ctx.work_root,
            output_root=ctx.output_root,
            tenant_id=ctx.tenant_id,
        )
        chapter_ctx.ensure_dirs()
        if render_chapter is not None:
            rendered = render_chapter(chapter_ctx, pack_path, final)
        else:
            if (
                not auto_approve
                and not (chapter_ctx.work_dir / "plan" / "cost_estimate.json").exists()
            ):
                raise LongformError(
                    f"chapter {index} has no approved estimate; re-run with --auto-approve "
                    "or price it with `ve short` first"
                )
            estimate_short(
                chapter_ctx, pack, config_path=config_path, auto_approve=auto_approve, final=final
            )
            result = run_short_job(chapter_ctx, pack, tokens, config_path=config_path, final=final)
            if not result.passed:
                raise LongformError(f"chapter {index} failed QA: {result.qa_path}")
            rendered = result.final_path
        if not Path(rendered).is_file():
            raise LongformError(f"chapter {index} produced no file: {rendered}")
        chapter_paths.append(Path(rendered))
        logger.info("longform_chapter_complete", index=index, path=str(rendered))

    final_path = ctx.output_dir / plan.output_name
    join(
        chapter_paths,
        final_path,
        transition=plan.transition,
        transition_seconds=plan.transition_seconds,
    )
    qa_path: Path | None = None
    passed = True
    if run_qa:
        from qa.qa import QASkill

        report = QASkill(ctx).run(final_path)
        qa_path = ctx.work_dir / "qa" / "longform_qa.json"
        qa_path.parent.mkdir(parents=True, exist_ok=True)
        qa_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        passed = report.passed
    report_path = ctx.work_dir / "longform.json"
    report_path.write_text(
        json.dumps(
            {
                "job_id": plan.job_id,
                "chapters": [str(path) for path in chapter_paths],
                "final": str(final_path),
                "transition": plan.transition,
                "passed": passed,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return LongformResult(
        final_path=final_path,
        chapter_paths=tuple(chapter_paths),
        qa_path=qa_path,
        passed=passed,
    )
