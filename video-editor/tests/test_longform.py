"""Long-form chapter packs: load, join and orchestration (#315)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from video_editor_ffmpeg.probe import probe_media
from video_editor_pipelines.longform import (
    LongformPlan,
    join_chapters,
    load_longform,
    run_longform,
)
from video_editor_state.job import JobContext

from tests.conftest import _require_ffmpeg

PACK = """\
job_id: chapter
design: saju_series
aspect: "9:16"
voice: {voice_id: "sarvam:test"}
end_card: {say: "bye", text: "bye"}
scenes:
  - id: hook
    still: {key: a, prompt: "a still"}
    lines: [{say: "one"}]
"""


def _video(path: Path, seconds: float = 1.0) -> Path:
    _require_ffmpeg()
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s=320x180:d={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def test_load_longform_resolves_chapter_packs(tmp_path: Path) -> None:
    for name in ("c0.yaml", "c1.yaml"):
        (tmp_path / name).write_text(PACK, encoding="utf-8")
    plan_path = tmp_path / "longform.yaml"
    plan_path.write_text(
        "job_id: long\nchapters:\n  - pack: c0.yaml\n  - pack: c1.yaml\n",
        encoding="utf-8",
    )
    plan = load_longform(plan_path)
    assert [Path(chapter.pack).name for chapter in plan.chapters] == ["c0.yaml", "c1.yaml"]
    assert all(Path(chapter.pack).is_absolute() for chapter in plan.chapters)


def test_a_missing_chapter_pack_is_refused(tmp_path: Path) -> None:
    plan_path = tmp_path / "longform.yaml"
    plan_path.write_text("job_id: long\nchapters:\n  - pack: nope.yaml\n", encoding="utf-8")
    with pytest.raises(Exception, match="not found"):
        load_longform(plan_path)


def test_cut_join_concatenates_the_chapters(tmp_path: Path) -> None:
    first = _video(tmp_path / "a.mp4", 1.0)
    second = _video(tmp_path / "b.mp4", 1.0)
    output = join_chapters([first, second], tmp_path / "joined.mp4", transition="cut")
    assert output.is_file()
    assert (probe_media(str(output)).duration or 0.0) == pytest.approx(2.0, abs=0.2)


def test_crossfade_join_overlaps_the_chapters(tmp_path: Path) -> None:
    first = _video(tmp_path / "a.mp4", 1.5)
    second = _video(tmp_path / "b.mp4", 1.5)
    output = join_chapters(
        [first, second], tmp_path / "joined.mp4", transition="crossfade", transition_seconds=0.5
    )
    assert (probe_media(str(output)).duration or 0.0) == pytest.approx(2.5, abs=0.3)


def test_run_longform_renders_chapters_and_writes_a_report(tmp_path: Path) -> None:
    packs = []
    for index in range(2):
        pack = tmp_path / f"c{index}.yaml"
        pack.write_text(PACK, encoding="utf-8")
        packs.append(pack)
    plan = LongformPlan(
        job_id="long",
        chapters=[{"pack": str(pack)} for pack in packs],
    )
    ctx = JobContext(job_id="long", work_root=tmp_path / "work", output_root=tmp_path / "out")
    built: list[str] = []

    def render(chapter_ctx: JobContext, pack_path: Path, final: bool) -> Path:
        built.append(chapter_ctx.job_id)
        target = chapter_ctx.output_dir / "final.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"video")
        return target

    def join(paths, output, **_kwargs):
        Path(output).write_bytes(b"joined")
        return Path(output)

    result = run_longform(ctx, plan, render_chapter=render, join=join, run_qa=False)

    assert built == ["long-ch00", "long-ch01"]
    assert result.final_path.read_bytes() == b"joined"
    report = json.loads((ctx.work_dir / "longform.json").read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert len(report["chapters"]) == 2
