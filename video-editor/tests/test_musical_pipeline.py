"""Tests for the `ve musical` pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from video_editor_core.adapters.from_musical import from_musical
from video_editor_core.composition import Canvas
from video_editor_core.musical import MusicalPlanError, MusicalSource, plan_segments
from video_editor_core.scene_transitions import apply_scene_transitions
from video_editor_pipelines import musical_pipeline
from video_editor_pipelines.musical_pipeline import (
    MusicalPipelineError,
    run_musical_job,
)
from video_editor_state.job import JobContext


def _write(path: Path | str, data: bytes = b"x") -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def _ctx(tmp_path: Path) -> JobContext:
    ctx = JobContext(
        job_id="musical-demo",
        work_root=tmp_path / "work",
        output_root=tmp_path / "out",
    )
    ctx.ensure_dirs()
    return ctx


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    music = tmp_path / "music.mp3"
    music.write_bytes(b"mp3")
    media = tmp_path / "media"
    media.mkdir()
    (media / "a.png").write_bytes(b"png")
    (media / "b.mp4").write_bytes(b"mp4")
    lyrics = tmp_path / "lyrics.txt"
    lyrics.write_text("one\ntwo\n", encoding="utf-8")
    return music, media, lyrics


def _probe(path: Path | str) -> SimpleNamespace:
    name = Path(path).name
    if name == "music.mp3":
        return SimpleNamespace(duration=16.0)
    if name == "b.mp4":
        return SimpleNamespace(duration=30.0)
    if name == "final.mp4":
        return SimpleNamespace(duration=16.0)
    raise AssertionError(f"unexpected probe: {path}")


def test_run_musical_job_builds_compiles_and_muxes_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    music, media, lyrics = _inputs(tmp_path)
    output = tmp_path / "out" / "final.mp4"
    calls: list[str] = []

    def fake_render(project_dir: Path, path: Path, **kwargs: Any) -> Path:
        calls.append("render")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"rendered")
        return Path(path)

    def fake_mux(rendered: Path, track: Path, path: Path, **kwargs: Any) -> Path:
        calls.append("mux")
        Path(path).write_bytes(b"muxed")
        assert Path(track).name == "music.mp3"
        assert kwargs["shortest"] is True
        return Path(path)

    def fake_normalize(path: Path, path_out: Path) -> Path:
        calls.append("normalize")
        Path(path_out).parent.mkdir(parents=True, exist_ok=True)
        Path(path_out).write_bytes(b"final")
        return Path(path_out)

    monkeypatch.setattr(musical_pipeline, "probe_media", _probe)
    monkeypatch.setattr(musical_pipeline, "render_project", fake_render)
    monkeypatch.setattr(musical_pipeline, "mux_voiceover", fake_mux)
    monkeypatch.setattr(musical_pipeline, "normalize_loudness", fake_normalize)

    result = run_musical_job(
        _ctx(tmp_path),
        music_path=music,
        media_dir=media,
        lyrics_path=lyrics,
        output_path=output,
        run_qa=False,
    )

    assert calls == ["render", "mux", "normalize"]
    assert result.final_path == output
    assert output.read_bytes() == b"final"
    assert result.qa_path is None
    assert result.checks == ()
    assert result.passed is True

    plan = json.loads(result.composition_path.read_text(encoding="utf-8"))
    assert len(plan["scenes"]) == 2
    assert plan["scenes"][1]["transition_in"]["name"] == "crossfade"
    assert plan["audio_mode"] == "external"
    captions = [
        layer["id"]
        for scene in plan["scenes"]
        for layer in scene["layers"]
        if layer["id"].startswith("caption_")
    ]
    assert captions == ["caption_0", "caption_1"]
    # The video source was probed and recorded in the plan's layer style.
    video = next(
        layer
        for scene in plan["scenes"]
        for layer in scene["layers"]
        if layer["media_kind"] == "video"
    )
    assert video["style"]["playback_rate"] == 0.5


def test_run_musical_job_records_the_composition_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    music, media, lyrics = _inputs(tmp_path)
    output = tmp_path / "out" / "final.mp4"
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"png")

    monkeypatch.setattr(musical_pipeline, "probe_media", _probe)
    monkeypatch.setattr(
        musical_pipeline,
        "render_project",
        lambda project, path, **kwargs: _write(path, b"rendered"),
    )
    monkeypatch.setattr(
        musical_pipeline,
        "mux_voiceover",
        lambda rendered, track, path, **kwargs: _write(path, b"muxed"),
    )
    monkeypatch.setattr(
        musical_pipeline,
        "normalize_loudness",
        lambda path, path_out: _write(path_out, b"final"),
    )
    monkeypatch.setattr(musical_pipeline, "check_project", lambda project, **kwargs: "check passed")
    monkeypatch.setattr(
        musical_pipeline,
        "snapshot_project",
        lambda project, frame_dir, **kwargs: [frame],
    )
    monkeypatch.setattr(musical_pipeline, "frame_stddev", lambda path: 20.0)
    monkeypatch.setattr(musical_pipeline, "measure_loudness", lambda path: (-14.2, -1.5))

    result = run_musical_job(
        _ctx(tmp_path),
        music_path=music,
        media_dir=media,
        lyrics_path=lyrics,
        output_path=output,
    )

    assert [check.name for check in result.checks] == [
        "composition",
        "visual",
        "duration",
        "loudness",
    ]
    assert all(check.ok for check in result.checks)
    assert result.passed is True
    assert result.qa_path is not None
    report = json.loads(result.qa_path.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert len(report["checks"]) == 4


def test_a_failed_gate_fails_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    music, media, lyrics = _inputs(tmp_path)
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"png")

    monkeypatch.setattr(musical_pipeline, "probe_media", _probe)
    monkeypatch.setattr(
        musical_pipeline,
        "render_project",
        lambda project, path, **kwargs: _write(path, b"rendered"),
    )
    monkeypatch.setattr(
        musical_pipeline,
        "mux_voiceover",
        lambda rendered, track, path, **kwargs: _write(path, b"muxed"),
    )
    monkeypatch.setattr(
        musical_pipeline,
        "normalize_loudness",
        lambda path, path_out: _write(path_out, b"final"),
    )
    monkeypatch.setattr(musical_pipeline, "check_project", lambda project, **kwargs: "check passed")
    monkeypatch.setattr(
        musical_pipeline, "snapshot_project", lambda project, frame_dir, **kwargs: [frame]
    )
    monkeypatch.setattr(musical_pipeline, "frame_stddev", lambda path: 1.0)  # blank frame
    monkeypatch.setattr(musical_pipeline, "measure_loudness", lambda path: (-14.0, -1.5))

    result = run_musical_job(
        _ctx(tmp_path),
        music_path=music,
        media_dir=media,
        lyrics_path=lyrics,
        output_path=tmp_path / "out" / "final.mp4",
    )

    assert result.passed is False
    visual = next(check for check in result.checks if check.name == "visual")
    assert visual.ok is False


def test_a_missing_music_file_names_the_input(tmp_path: Path) -> None:
    _, media, lyrics = _inputs(tmp_path)
    with pytest.raises(MusicalPipelineError, match="music file not found"):
        run_musical_job(
            _ctx(tmp_path),
            music_path=tmp_path / "missing.mp3",
            media_dir=media,
            lyrics_path=lyrics,
            output_path=tmp_path / "out" / "final.mp4",
            run_qa=False,
        )


def test_empty_lyrics_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    music, media, lyrics = _inputs(tmp_path)
    lyrics.write_text("\n\n", encoding="utf-8")
    monkeypatch.setattr(musical_pipeline, "probe_media", _probe)
    with pytest.raises(MusicalPipelineError, match="no captions parsed"):
        run_musical_job(
            _ctx(tmp_path),
            music_path=music,
            media_dir=media,
            lyrics_path=lyrics,
            output_path=tmp_path / "out" / "final.mp4",
            run_qa=False,
        )


def test_an_invalid_transition_is_refused_before_any_probe(tmp_path: Path) -> None:
    music, media, lyrics = _inputs(tmp_path)
    with pytest.raises(MusicalPipelineError, match="must be less than"):
        run_musical_job(
            _ctx(tmp_path),
            music_path=music,
            media_dir=media,
            lyrics_path=lyrics,
            output_path=tmp_path / "out" / "final.mp4",
            segment_seconds=2.0,
            transition_seconds=2.0,
            run_qa=False,
        )


def test_a_media_folder_without_supported_files_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    music = tmp_path / "music.mp3"
    music.write_bytes(b"mp3")
    media = tmp_path / "media"
    media.mkdir()
    (media / "notes.txt").write_text("ignore me")
    lyrics = tmp_path / "lyrics.txt"
    lyrics.write_text("one\n", encoding="utf-8")
    monkeypatch.setattr(musical_pipeline, "probe_media", _probe)
    with pytest.raises(MusicalPlanError, match="no supported media"):
        run_musical_job(
            _ctx(tmp_path),
            music_path=music,
            media_dir=media,
            lyrics_path=lyrics,
            output_path=tmp_path / "out" / "final.mp4",
            run_qa=False,
        )


def test_beat_quantization_writes_the_grid_and_moves_the_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    music, media, lyrics = _inputs(tmp_path)
    output = tmp_path / "out" / "final.mp4"
    beats_path = tmp_path / "beats.json"
    beats_path.write_text(json.dumps([{"timestamp": 7.9}]), encoding="utf-8")

    class FakeBeatSync:
        def __init__(self, config: Any = None) -> None:
            self.config = config

        def run(self, ctx: JobContext, audio_path: Path) -> dict[str, Any]:
            return {"beats_path": str(beats_path), "avg_bpm": 120.0}

    monkeypatch.setattr(musical_pipeline, "BeatSyncSkill", FakeBeatSync)
    monkeypatch.setattr(musical_pipeline, "probe_media", _probe)
    monkeypatch.setattr(
        musical_pipeline,
        "render_project",
        lambda project_dir, path, **kwargs: _write(path, b"rendered"),
    )
    monkeypatch.setattr(
        musical_pipeline,
        "mux_voiceover",
        lambda rendered, track, path, **kwargs: _write(path, b"muxed"),
    )
    monkeypatch.setattr(
        musical_pipeline,
        "normalize_loudness",
        lambda path, path_out: _write(path_out, b"final"),
    )

    result = run_musical_job(
        _ctx(tmp_path),
        music_path=music,
        media_dir=media,
        lyrics_path=lyrics,
        output_path=output,
        run_qa=False,
        quantize="beat",
        beat_tolerance=0.2,
    )

    grid = (tmp_path / "work" / "musical-demo" / "plan" / "beat_grid.md").read_text()
    assert "BPM: 120.00" in grid
    assert "| 1 | 8.000 | 7.900 | -0.100 |" in grid
    plan = json.loads(result.composition_path.read_text(encoding="utf-8"))
    assert len(plan["scenes"]) == 2


def test_a_sub_frame_tail_does_not_leave_a_caption_past_the_composition() -> None:
    # The 8 s parity fixture's MP3 probes a few tens of milliseconds over 8 s.
    # With 4 s segments that tail rounds to a single frame, so it would become
    # a one-frame scene; the last caption (which runs to the composition end)
    # would then belong to the scene before it and the crossfade out of that
    # scene would push the caption past the composition.
    segments = plan_segments(
        [MusicalSource(path=Path("still.png"), kind="image")],
        8.046,
        segment_seconds=4.0,
        transition_seconds=1.0,
    )
    composition = from_musical(
        segments,
        ["one", "two", "three"],
        job_id="job",
        canvas=Canvas(width=1280, height=720, fps=30),
    )

    out = apply_scene_transitions(composition)

    assert [scene.id for scene in out.scenes] == ["segment_0", "segment_1"]
    assert out.duration == pytest.approx(241 / 30)
    last_caption = next(layer for layer in out.scenes[-1].layers if layer.id == "caption_2")
    assert last_caption.time.end == pytest.approx(out.duration)
    for scene in out.scenes:
        for layer in scene.layers:
            assert layer.time.end is not None
            assert layer.time.end <= out.duration + 1e-9


def test_swell_filter_encodes_gain_points() -> None:
    from video_editor_pipelines.musical_pipeline import swell_filter

    expression = swell_filter([(10.0, 3.0, 2.0), (20.0, -2.0, 1.5)])
    assert "between(t,10,12)" in expression
    assert "between(t,20,21.5)" in expression
    assert "1.41254" in expression
    assert "0.794328" in expression
