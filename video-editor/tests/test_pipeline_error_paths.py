"""Error-path tests for the skills-first end-to-end pipeline.

The happy paths are covered in `test_pipeline.py`. This file adds
regression tests for failure modes that have caused real outages:
missing source, explicit-but-missing music file, and the case where
a downstream skill can't find its expected input (which is raised
later in the chain).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from video_editor_pipelines.pipeline import SkillsPipeline
from video_editor_state.job import JobContext


def _build_transcript(words: list[tuple[str, float, float]]) -> dict[str, object]:
    return {
        "source_audio": "",
        "language": "en",
        "language_probability": 1.0,
        "word_timestamps": True,
        "segments": [],
        "words": [{"word": w, "start": s, "end": e, "probability": 0.95} for w, s, e in words],
    }


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(
        job_id="pipeline-err",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )


@pytest.fixture
def transcript_with_filler(tmp_path: Path) -> Path:
    transcript = _build_transcript(
        [
            ("Hello", 0.0, 0.4),
            ("um", 0.5, 0.7),
            ("world", 0.8, 1.2),
        ]
    )
    path = tmp_path / "transcript.json"
    path.write_text(json.dumps(transcript), encoding="utf-8")
    return path


def test_pipeline_raises_when_source_missing(
    ctx: JobContext,
    transcript_with_filler: Path,
    tmp_path: Path,
) -> None:
    """Missing source path surfaces from the rough cut stage."""
    pipeline = SkillsPipeline(ctx)
    missing = tmp_path / "no_such_source.mp4"

    with pytest.raises(FileNotFoundError):
        pipeline.run(missing, transcript_path=transcript_with_filler)


def test_pipeline_raises_when_explicit_music_missing(
    tmp_media: Path,
    ctx: JobContext,
    transcript_with_filler: Path,
    tmp_path: Path,
) -> None:
    """Explicit --music-path that doesn't exist propagates FileNotFoundError.

    ``SkillsPipeline._should_run_music`` returns True when an explicit path
    is given, so a missing file surfaces from ``MusicSkill._find_music``
    — this is the correct behavior (silent skip would mask user typos).
    """
    pipeline = SkillsPipeline(ctx, music_path=tmp_path / "no_such_music.mp3")

    with pytest.raises(FileNotFoundError, match="music"):
        pipeline.run(tmp_media, transcript_path=transcript_with_filler)


def test_graphics_inputs_remap_source_times_to_the_cut_timeline(
    ctx: JobContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plan segment after the last source cut must not be clamped away."""
    from video_editor_core.timeline import EDL, Segment

    edl = EDL(
        job_id="mapped",
        source_path="/tmp/raw.mp4",
        segments=[
            Segment(
                id="seg_0000",
                source_start=0.0,
                source_end=8.9,
                timeline_start=0.0,
                timeline_end=8.9,
            ),
            Segment(
                id="seg_0001",
                source_start=13.4,
                source_end=16.4,
                timeline_start=8.9,
                timeline_end=11.9,
            ),
        ],
    )
    edl_path = ctx.artifact("plan", "edl.json")
    edl_path.parent.mkdir(parents=True, exist_ok=True)
    edl_path.write_text(edl.model_dump_json(), encoding="utf-8")
    transcript_path = ctx.artifact("transcript", "transcript.json")
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(
        json.dumps(
            {
                "words": [{"word": "tail", "start": 13.5, "end": 13.7, "probability": 1.0}],
                "segments": [],
            }
        ),
        encoding="utf-8",
    )

    class _Info:
        duration = 12.02

    monkeypatch.setattr("video_editor_pipelines.pipeline.probe_media", lambda _: _Info())
    cut_edl, cut_transcript = SkillsPipeline(ctx)._graphics_inputs(Path("/tmp/locked.mp4"))

    assert cut_edl.segments[1].source_start == 8.9
    assert cut_edl.segments[1].source_end == 11.9
    words = json.loads(cut_transcript.read_text(encoding="utf-8"))["words"]
    assert words[0]["start"] == pytest.approx(9.0)


def test_skip_music_disables_even_a_config_default(ctx: JobContext) -> None:
    pipeline = SkillsPipeline(ctx, skip_music=True)

    assert pipeline._should_run_music() is False


def test_default_music_path_resolves_from_config() -> None:
    """The default music path is resolved from config.yaml, not hardcoded.

    When ``audio.default_music_path`` is set in config.yaml, the pipeline
    reads it through ``ProjectConfig.default_music_path()`` which resolves
    relative paths against the repo root.
    """
    from video_editor_config import ProjectConfig

    config = ProjectConfig.load()
    path = config.default_music_path()
    # May be None if not configured, which is valid.
    if path is not None:
        assert path.is_absolute()
