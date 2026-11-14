"""Tests for the music-video pipeline orchestrator."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from video_editor_asset_ingestion.models import (
    AssetRequest,
    AssetResult,
    AssetType,
    BatchResult,
)
from video_editor_pipelines.pipeline import MusicVideoPipeline
from video_editor_state.job import JobContext


def _make_synthetic_video(tmp_path: Path, name: str = "rendered.mp4") -> Path:
    """Create a short test video with ffmpeg."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available on PATH")
    video = tmp_path / name
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=3:size=320x240:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        capture_output=True,
    )
    return video


def _make_music(tmp_path: Path, duration: int = 6) -> Path:
    """Create a synthetic MP3."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available on PATH")
    music = tmp_path / "song.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            "-q:a",
            "0",
            str(music),
        ],
        check=True,
        capture_output=True,
    )
    return music


def _make_media_dir(tmp_path: Path) -> Path:
    media_dir = tmp_path / "visuals"
    media_dir.mkdir()
    (media_dir / "img1.png").write_bytes(
        bytes(
            [
                0x89,
                0x50,
                0x4E,
                0x47,
                0x0D,
                0x0A,
                0x1A,
                0x0A,
                0x00,
                0x00,
                0x00,
                0x0D,
                0x49,
                0x48,
                0x44,
                0x52,
                0x00,
                0x00,
                0x00,
                0x01,
                0x00,
                0x00,
                0x00,
                0x01,
                0x08,
                0x02,
                0x00,
                0x00,
                0x00,
                0x90,
                0x77,
                0x53,
                0xDE,
                0x00,
                0x00,
                0x00,
                0x0C,
                0x49,
                0x44,
                0x41,
                0x54,
                0x08,
                0xD7,
                0x63,
                0xF8,
                0x0F,
                0x00,
                0x00,
                0x01,
                0x01,
                0x00,
                0x05,
                0xDD,
                0xDD,
                0xA4,
                0x0E,
                0x00,
                0x00,
                0x00,
                0x00,
                0x49,
                0x45,
                0x4E,
                0x44,
                0xAE,
                0x42,
                0x60,
                0x82,
            ]
        )
    )
    return media_dir


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(
        job_id="mv-test",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )


def _noop_render(ctx: JobContext, plan_path: Path, output_path: Path, **kwargs: Any) -> Path:
    """Mock render_scene_plan that copies a synthetic video to the output path."""
    # The caller already created the output dir; just ensure it exists.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Generate a fresh synthetic video for the output so downstream ffmpeg
    # commands have real frames/audio to work with.
    src = _make_synthetic_video(output_path.parent, "mock_rendered.mp4")
    shutil.copy2(src, output_path)
    return output_path


def test_music_video_pipeline_runs_without_lyrics(
    tmp_path: Path,
    ctx: JobContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pipeline builds a plan, ingests media, renders, and exports."""
    music = _make_music(tmp_path, duration=4)
    media_dir = _make_media_dir(tmp_path)

    monkeypatch.setattr(
        "video_editor_pipelines.pipeline.render_scene_plan",
        _noop_render,
    )

    pipeline = MusicVideoPipeline(
        ctx,
        music_path=music,
        media_dir=media_dir,
        segment_seconds=2.0,
    )
    result = pipeline.run(output_name="final.mp4")

    assert Path(result["plan"]).exists()
    assert Path(result["rendered"]).exists()
    assert Path(result["captions"]).exists()
    assert Path(result["final"]).exists()
    assert result["final"].endswith("final.mp4")
    assert result["transcript"] is None


def test_music_video_pipeline_burns_lyrics(
    tmp_path: Path,
    ctx: JobContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lyrics are aligned and burned as drawtext captions."""
    music = _make_music(tmp_path, duration=3)
    media_dir = _make_media_dir(tmp_path)
    lyrics = tmp_path / "lyrics.txt"
    lyrics.write_text("hello world test", encoding="utf-8")

    monkeypatch.setattr(
        "video_editor_pipelines.pipeline.render_scene_plan",
        _noop_render,
    )

    async def fake_align(self: MusicVideoPipeline) -> dict[str, Any]:
        return {
            "source_audio": str(music),
            "language": "en",
            "language_probability": 1.0,
            "word_timestamps": True,
            "segments": [],
            "words": [
                {"word": "hello", "start": 0.0, "end": 0.5, "probability": 1.0},
                {"word": "world", "start": 0.6, "end": 1.1, "probability": 1.0},
                {"word": "test", "start": 1.2, "end": 1.7, "probability": 1.0},
            ],
        }

    monkeypatch.setattr(MusicVideoPipeline, "_align_lyrics", fake_align)

    pipeline = MusicVideoPipeline(
        ctx,
        music_path=music,
        media_dir=media_dir,
        segment_seconds=2.0,
        lyrics_path=lyrics,
    )
    result = pipeline.run(output_name="final.mp4")

    assert Path(result["final"]).exists()
    assert result["transcript"] is not None
    assert result["transcript"].endswith("transcript_en.json")


def test_music_video_pipeline_rejects_missing_media_dir(
    tmp_path: Path,
    ctx: JobContext,
) -> None:
    """A non-existent media directory is rejected at plan time."""
    music = _make_music(tmp_path, duration=2)
    pipeline = MusicVideoPipeline(
        ctx,
        music_path=music,
        media_dir=tmp_path / "missing",
    )
    with pytest.raises(NotADirectoryError):
        pipeline.run()


@pytest.mark.parametrize(
    "bad_name",
    [
        "../../evil.mp4",
        "sub/dir.mp4",
        "sub\\dir.mp4",
        "..",
        "",
    ],
)
def test_music_video_pipeline_rejects_traversal_output_name(
    ctx: JobContext,
    bad_name: str,
) -> None:
    """Bug #191: a traversal ``output_name`` cannot escape ``output_dir``.

    ``MusicVideoPipeline.run_async`` joins ``output_name`` with
    ``ctx.output_dir`` to build both the rendered and final paths. Without
    sanitization, a value like ``../../evil.mp4`` writes outside
    ``output_dir``. The pipeline must reject such names before any render
    work begins (so the test needs no ffmpeg / real media).
    """
    pipeline = MusicVideoPipeline(
        ctx,
        music_path=ctx.work_dir / "song.mp3",
        media_dir=ctx.work_dir / "media",
        segment_seconds=2.0,
    )
    with pytest.raises(ValueError, match="plain filename"):
        pipeline.run(output_name=bad_name)


@pytest.mark.parametrize(
    "bad_lang",
    [
        "../../tmp/evil",
        "sub/dir",
        "sub\\dir",
        "..",
        ".",
        "",
    ],
)
def test_music_video_pipeline_rejects_traversal_language(
    ctx: JobContext,
    bad_lang: str,
) -> None:
    """Bug #192: a traversal ``language`` cannot escape the transcript dir.

    ``MusicVideoPipeline`` interpolates ``language`` into
    ``transcript_{language}.json`` inside ``ctx.work_dir / "transcript"``.
    Without sanitization, a value like ``../../tmp/evil`` resolves the
    transcript path outside ``work_dir``. The pipeline must reject such
    language codes at construction time (so the test needs no ffmpeg /
    real media).
    """
    with pytest.raises(ValueError, match="language"):
        MusicVideoPipeline(
            ctx,
            music_path=ctx.work_dir / "song.mp3",
            media_dir=ctx.work_dir / "media",
            segment_seconds=2.0,
            language=bad_lang,
        )


# ---------------------------------------------------------------------------
# Bug #195 — the uncaptioned render must not be overwritten by the captioned
# final when output_name is supplied.
# ---------------------------------------------------------------------------


def test_music_video_pipeline_keeps_rendered_distinct_from_final(
    tmp_path: Path,
    ctx: JobContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#195: with ``output_name="final.mp4"`` the render and the final
    deliverable used to resolve to the SAME path, so burning captions
    overwrote the uncaptioned render. The render must now live at a distinct
    ``rendered.mp4`` and survive the caption burn."""
    music = _make_music(tmp_path, duration=4)
    media_dir = _make_media_dir(tmp_path)
    monkeypatch.setattr("video_editor_pipelines.pipeline.render_scene_plan", _noop_render)

    pipeline = MusicVideoPipeline(
        ctx,
        music_path=music,
        media_dir=media_dir,
        segment_seconds=2.0,
    )
    result = pipeline.run(output_name="final.mp4")

    assert result["rendered"] != result["final"]
    rendered = Path(result["rendered"])
    final = Path(result["final"])
    assert rendered.exists(), "uncaptioned render must be preserved"
    assert final.exists(), "captioned final must exist"
    assert rendered.name == "rendered.mp4"
    assert final.name == "final.mp4"


# ---------------------------------------------------------------------------
# Bug #196 — forced-alignment failure degrades gracefully to no captions.
# ---------------------------------------------------------------------------


def _fake_alignment_manager(monkeypatch: pytest.MonkeyPatch, *, fail: bool) -> None:
    """Patch ``AssetIngestionManager`` in the pipeline module so its
    ``resolve_partial`` either raises (alignment failure) or returns a single
    dummy result whose ``local_path`` is a small JSON file."""
    from video_editor_asset_ingestion.models import BatchResult

    class _FakeManager:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def resolve_partial(self, requests: list[AssetRequest]) -> BatchResult:
            request = requests[0]
            # Media ingest (IMAGE/VIDEO) must always succeed so the plan/ingest
            # /render stages run before alignment is ever attempted — the
            # ``local_path`` is the real source file on disk (the request's
            # ``prompt`` carries the filesystem path from the planner).
            if request.asset_type in (AssetType.IMAGE, AssetType.VIDEO):
                return BatchResult(
                    results=[
                        AssetResult(
                            asset_id=request.asset_id,
                            asset_type=request.asset_type,
                            scene_id=request.scene_id,
                            local_path=request.prompt,
                        )
                    ],
                )
            # Alignment request: fail or return a dummy alignment JSON whose
            # ``local_path`` is a small JSON file (the converter is mocked).
            if fail:
                raise RuntimeError("alignment endpoint down")
            import tempfile

            tmp = Path(tempfile.mktemp(suffix=".json"))
            tmp.write_text('{"words": []}', encoding="utf-8")
            return BatchResult(
                results=[
                    AssetResult(
                        asset_id=request.asset_id,
                        asset_type=AssetType.ALIGNMENT,
                        scene_id="",
                        local_path=str(tmp),
                    )
                ],
            )

    monkeypatch.setattr("video_editor_pipelines.pipeline.AssetIngestionManager", _FakeManager)


def test_music_video_pipeline_degrades_when_alignment_fails(
    tmp_path: Path,
    ctx: JobContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#196: if forced alignment raises (missing key, 401, timeout) the
    pipeline must log and continue without captions instead of crashing
    after the plan/ingest/render already succeeded."""
    music = _make_music(tmp_path, duration=3)
    media_dir = _make_media_dir(tmp_path)
    lyrics = tmp_path / "lyrics.txt"
    lyrics.write_text("hello world", encoding="utf-8")

    monkeypatch.setattr("video_editor_pipelines.pipeline.render_scene_plan", _noop_render)
    _fake_alignment_manager(monkeypatch, fail=True)

    pipeline = MusicVideoPipeline(
        ctx,
        music_path=music,
        media_dir=media_dir,
        segment_seconds=2.0,
        lyrics_path=lyrics,
    )
    result = pipeline.run(output_name="final.mp4")
    assert Path(result["final"]).exists()
    assert result["transcript"] is None  # captions skipped, build succeeded


# ---------------------------------------------------------------------------
# Bug #202 / #217 — missing or directory lyrics path is handled gracefully.
# ---------------------------------------------------------------------------


def test_align_lyrics_returns_none_for_missing_path(
    tmp_path: Path,
    ctx: JobContext,
) -> None:
    """#202: a user-supplied but non-existent lyrics path returns None
    (no captions) rather than silently producing no captions with no signal."""
    pipeline = MusicVideoPipeline(
        ctx,
        music_path=tmp_path / "song.mp3",
        media_dir=tmp_path / "media",
        segment_seconds=2.0,
        lyrics_path=tmp_path / "typo_lyrics.txt",  # does not exist
    )
    import asyncio

    assert asyncio.run(pipeline._align_lyrics()) is None


def test_align_lyrics_returns_none_for_directory_path(
    tmp_path: Path,
    ctx: JobContext,
) -> None:
    """#217: a directory passed as ``lyrics_path`` must not raise
    ``IsADirectoryError``; it returns None with a warning."""
    lyrics_dir = tmp_path / "lyrics_dir"
    lyrics_dir.mkdir()
    pipeline = MusicVideoPipeline(
        ctx,
        music_path=tmp_path / "song.mp3",
        media_dir=tmp_path / "media",
        segment_seconds=2.0,
        lyrics_path=lyrics_dir,
    )
    import asyncio

    assert asyncio.run(pipeline._align_lyrics()) is None


# ---------------------------------------------------------------------------
# Bug #197 — one corrupt media file does not fail the whole batch.
# ---------------------------------------------------------------------------


def test_resolve_local_media_tolerates_partial_failure(
    tmp_path: Path,
    ctx: JobContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#197: ``_resolve_local_media`` uses ``resolve_partial``; a single
    failed asset is skipped (with a warning) while the successful ones are
    attached, instead of the whole batch raising."""
    from video_editor_asset_ingestion.models import BatchResult
    from video_editor_pipelines.music_video_planner import build_music_video_plan

    music = _make_music(tmp_path, duration=4)
    media_dir = _make_media_dir(tmp_path)
    plan = build_music_video_plan(
        job_id=ctx.job_id,
        music_path=music,
        media_dir=media_dir,
        segment_seconds=2.0,
    )

    class _FakeManager:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def resolve_partial(self, requests: list[AssetRequest]) -> BatchResult:
            # Fail the first request, succeed the rest.
            results = [
                AssetResult(
                    asset_id=r.asset_id,
                    asset_type=AssetType.IMAGE,
                    scene_id=r.scene_id,
                    local_path=str(tmp_path / "ok.bin"),
                )
                for r in requests[1:]
            ]
            failures = [(requests[0].asset_id, "MediaInfoError: corrupt file")]
            return BatchResult(results=results, failures=failures)

    monkeypatch.setattr("video_editor_pipelines.pipeline.AssetIngestionManager", _FakeManager)
    pipeline = MusicVideoPipeline(
        ctx,
        music_path=music,
        media_dir=media_dir,
        segment_seconds=2.0,
    )
    enriched = asyncio_run(pipeline._resolve_local_media(plan))
    # At least one scene got its asset attached (the failed one was skipped).
    attached = [s for s in enriched.scenes if s.assets]
    assert len(attached) >= 1


def test_resolve_local_media_raises_when_all_fail(
    tmp_path: Path,
    ctx: JobContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#197: if every media request fails, the pipeline still raises —
    partial success only degrades when something actually succeeded."""
    from video_editor_asset_ingestion.models import BatchResult
    from video_editor_pipelines.music_video_planner import build_music_video_plan

    music = _make_music(tmp_path, duration=4)
    media_dir = _make_media_dir(tmp_path)
    plan = build_music_video_plan(
        job_id=ctx.job_id,
        music_path=music,
        media_dir=media_dir,
        segment_seconds=2.0,
    )

    class _FakeManager:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def resolve_partial(self, requests: list[AssetRequest]) -> BatchResult:
            return BatchResult(
                results=[],
                failures=[(r.asset_id, "MediaInfoError") for r in requests],
            )

    monkeypatch.setattr("video_editor_pipelines.pipeline.AssetIngestionManager", _FakeManager)
    pipeline = MusicVideoPipeline(
        ctx,
        music_path=music,
        media_dir=media_dir,
        segment_seconds=2.0,
    )
    with pytest.raises(RuntimeError, match="local media request"):
        asyncio_run(pipeline._resolve_local_media(plan))


def test_resolve_local_media_partial_failure_preserves_timeline(
    tmp_path: Path,
    ctx: JobContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#197 (end-to-end invariant): when one scene's media fails, the failed
    scene must remain in the plan with its original ``duration_seconds`` so
    the HyperFrames composition renders a placeholder at the scheduled
    length and music sync (``totalFrames`` = sum of ``sceneFrames``) is
    preserved. Dropping the failed scene, or zeroing its duration, would make
    the rendered video shorter than the music with no error."""
    from video_editor_asset_ingestion.models import BatchResult
    from video_editor_pipelines.music_video_planner import build_music_video_plan

    # 6 s of music at 2 s/segment → 3 scenes of ~2 s each.
    music = _make_music(tmp_path, duration=6)
    media_dir = _make_media_dir(tmp_path)
    plan = build_music_video_plan(
        job_id=ctx.job_id,
        music_path=music,
        media_dir=media_dir,
        segment_seconds=2.0,
    )
    assert len(plan.scenes) >= 2, "test needs >=2 scenes to fail a middle one"
    scene_ids = [s.id for s in plan.scenes]
    original_durations = {s.id: s.duration_seconds for s in plan.scenes}
    original_total = sum(s.duration_seconds for s in plan.scenes)
    # Fail the middle scene; let the rest succeed.
    failed_id = scene_ids[len(scene_ids) // 2]

    class _FakeManager:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def resolve_partial(self, requests: list[AssetRequest]) -> BatchResult:
            results = [
                AssetResult(
                    asset_id=r.asset_id,
                    asset_type=AssetType.IMAGE,
                    scene_id=r.scene_id,
                    local_path=str(tmp_path / "ok.bin"),
                )
                for r in requests
                if r.scene_id != failed_id
            ]
            failures = [
                (r.asset_id, "MediaInfoError: corrupt file")
                for r in requests
                if r.scene_id == failed_id
            ]
            return BatchResult(results=results, failures=failures)

    monkeypatch.setattr("video_editor_pipelines.pipeline.AssetIngestionManager", _FakeManager)
    pipeline = MusicVideoPipeline(
        ctx,
        music_path=music,
        media_dir=media_dir,
        segment_seconds=2.0,
    )
    enriched = asyncio_run(pipeline._resolve_local_media(plan))

    # No scene was dropped: the failed scene stays so its placeholder keeps
    # the timeline aligned with the music.
    assert [s.id for s in enriched.scenes] == scene_ids
    failed = next(s for s in enriched.scenes if s.id == failed_id)
    assert failed.assets == [], "the failed scene must have no attached asset"
    # Duration untouched: this is the invariant the composition relies on.
    assert failed.duration_seconds == pytest.approx(original_durations[failed_id])
    assert sum(s.duration_seconds for s in enriched.scenes) == pytest.approx(original_total)


# ---------------------------------------------------------------------------
# Bug #216 — canonical transcript.json is first-write-wins across languages.
# ---------------------------------------------------------------------------


def test_canonical_transcript_first_write_wins(
    tmp_path: Path,
    ctx: JobContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#216: re-running with a different language must not overwrite the
    canonical ``transcript.json``; the first language's canonical stays."""
    from video_editor_asset_ingestion.models import BatchResult

    lyrics = tmp_path / "lyrics.txt"
    lyrics.write_text("hello", encoding="utf-8")

    def make_manager(language_marker: str) -> type:
        class _FakeManager:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

            async def resolve_partial(self, requests: list[AssetRequest]) -> BatchResult:
                import tempfile

                tmp = Path(tempfile.mktemp(suffix=".json"))
                tmp.write_text('{"words": []}', encoding="utf-8")
                return BatchResult(
                    results=[
                        AssetResult(
                            asset_id=requests[0].asset_id,
                            asset_type=AssetType.ALIGNMENT,
                            scene_id="",
                            local_path=str(tmp),
                        )
                    ],
                )

        return _FakeManager

    def fake_converter(
        alignment_data: Any,
        source_audio: Any,
        language: str,
    ) -> dict[str, Any]:
        return {"language": language, "marker": language, "words": []}

    monkeypatch.setattr(
        "video_editor_pipelines.pipeline.forced_alignment_response_to_transcript",
        fake_converter,
    )

    async def run_once(language: str) -> None:
        monkeypatch.setattr(
            "video_editor_pipelines.pipeline.AssetIngestionManager",
            make_manager(language),
        )
        pipeline = MusicVideoPipeline(
            ctx,
            music_path=tmp_path / "song.mp3",
            media_dir=tmp_path / "media",
            segment_seconds=2.0,
            lyrics_path=lyrics,
            language=language,
            alignment_provider="stub_alignment",
        )
        await pipeline._align_lyrics()

    import asyncio

    asyncio.run(run_once("en"))
    asyncio.run(run_once("es"))

    transcript_dir = ctx.work_dir / "transcript"
    canonical = json.loads((transcript_dir / "transcript.json").read_text(encoding="utf-8"))
    # First-write-wins: canonical stays English even after the Spanish run.
    assert canonical["marker"] == "en"
    # Both language-specific files exist and are distinct.
    assert (transcript_dir / "transcript_en.json").exists()
    assert (transcript_dir / "transcript_es.json").exists()


# ---------------------------------------------------------------------------
# Bug #211 — resume: a completed export is not re-rendered.
# ---------------------------------------------------------------------------


def test_music_video_pipeline_resumes_completed_export(
    tmp_path: Path,
    ctx: JobContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#211: if the manifest shows the export stage complete and the final
    deliverable exists, ``run_async`` returns it without re-rendering."""
    music = _make_music(tmp_path, duration=3)
    media_dir = _make_media_dir(tmp_path)

    def _fail_if_called(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("render_scene_plan must not run on a resumed export")

    monkeypatch.setattr("video_editor_pipelines.pipeline.render_scene_plan", _fail_if_called)

    ctx.ensure_dirs()
    final_path = ctx.output_dir / "final.mp4"
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(b"already-rendered")
    ctx.manifest().stage_done("export", {"final": str(final_path)})
    ctx.store.save()

    pipeline = MusicVideoPipeline(
        ctx,
        music_path=music,
        media_dir=media_dir,
        segment_seconds=2.0,
    )
    result = pipeline.run(output_name="final.mp4")
    assert result["final"] == str(final_path)
    assert final_path.read_bytes() == b"already-rendered"  # not re-rendered


# ---------------------------------------------------------------------------
# Helper: run an awaitable without the per-test overhead of a fresh loop.
# ---------------------------------------------------------------------------


def asyncio_run(coro: Any) -> Any:
    return asyncio.run(coro)
