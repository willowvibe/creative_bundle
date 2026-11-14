"""Tests for the skills pipeline render-graph path (M3)."""

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
        job_id="graph-job",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )


@pytest.fixture
def transcript_path(tmp_path: Path) -> Path:
    transcript = _build_transcript(
        [
            ("Hello", 0.0, 0.4),
            ("um", 0.5, 0.7),
            ("world", 0.8, 1.2),
            ("Again", 2.0, 2.4),
        ]
    )
    path = tmp_path / "transcript.json"
    path.write_text(json.dumps(transcript), encoding="utf-8")
    return path


def test_render_graph_path_produces_one_composition(
    tmp_media: Path,
    ctx: JobContext,
    transcript_path: Path,
) -> None:
    """Graphics + captions flatten into one composed render."""
    pipeline = SkillsPipeline(ctx)
    result = pipeline.run(tmp_media, transcript_path=transcript_path)

    assert Path(result["locked"]).exists()
    # graphics and captions both point at the single composed render
    assert result["graphics"] == result["captions"]
    composed = Path(result["graphics"])
    assert composed.exists()
    assert composed.name == "composed.mp4"
    assert Path(result["final"]).exists()

    # the flattened graph is persisted for inspection / partial re-render
    graph_path = ctx.work_dir / "plan" / "render_graph.json"
    assert graph_path.exists()
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    assert len(graph["cuts"]) >= 1
    assert graph["overlays"], "expected caption/overlay layers in the graph"


def test_export_ships_the_composed_render_not_the_rough_cut(
    tmp_media: Path,
    ctx: JobContext,
    transcript_path: Path,
) -> None:
    """``final.mp4`` must be the graphics+captions composition.

    Export's fallback search also matches ``rough_cut/locked.mp4`` (always
    present), so without an explicit input the pipeline silently shipped the
    unpolished cut.
    """
    result = SkillsPipeline(ctx).run(tmp_media, transcript_path=transcript_path)

    composed = Path(result["graphics"])
    final = Path(result["final"])
    assert composed != ctx.work_dir / "rough_cut" / "locked.mp4"
    assert final.read_bytes() == composed.read_bytes()


def test_music_is_folded_into_the_graph_render(
    tmp_media: Path,
    ctx: JobContext,
    transcript_path: Path,
    tmp_path: Path,
) -> None:
    """With the graph path, music folds into the single render (no MusicSkill)."""
    import subprocess

    music = tmp_path / "music.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=1", str(music)],
        check=True,
        capture_output=True,
    )

    pipeline = SkillsPipeline(ctx, music_path=str(music))
    result = pipeline.run(tmp_media, transcript_path=transcript_path)

    # music mixed into the composed render, not a separate with_music.mp4
    assert result["music"] == result["graphics"]
    assert not (ctx.work_dir / "music" / "with_music.mp4").exists()

    graph = json.loads((ctx.work_dir / "plan" / "render_graph.json").read_text())
    assert graph["audio"], "expected the music bus in the graph"
    assert graph["audio"][0]["kind"] == "music"
    assert graph["audio"][0]["duck"] is True
