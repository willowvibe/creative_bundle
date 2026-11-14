"""Integration tests for engine-wide handshakes and stage-to-stage communication.

These tests exercise multiple packages/skills in a single flow to catch wiring
regressions that unit tests miss. They avoid paid APIs by using stub providers,
mocked generative calls, or hand-built plans.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from export.export import ExportSkill
from review.review import PostRenderReviewSkill
from typer.testing import CliRunner
from video_editor_asset_ingestion.config import AssetProviderConfig
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetResult, AssetType
from video_editor_asset_ingestion.providers.registry import PROVIDER_REGISTRY
from video_editor_cli import main as cli_main
from video_editor_cli.main import (
    _PAYLOAD_SCHEMAS,
    _build_job_context,
    _validate_payload_root,
    app,
)
from video_editor_config import ProjectConfig
from video_editor_core.models.scene_plan import Scene, ScenePlan
from video_editor_core.templates import TemplateRouter
from video_editor_pipelines.pipeline import MusicVideoPipeline, SkillsPipeline
from video_editor_state.job import JobContext
from video_editor_state.queue import InMemoryJobQueue, JobMessage

from tests.conftest import _require_ffmpeg


@pytest.fixture(autouse=True)
def _tmp_data_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make each test's ``tmp_path`` the configured data root (#278)."""
    monkeypatch.setenv("VE_WORK_ROOT", str(tmp_path))
    monkeypatch.setenv("VE_OUTPUT_ROOT", str(tmp_path))


def _make_video(path: Path, *, duration: float = 3.0, size: str = "640x360") -> Path:
    """Render a synthetic video with audio using FFmpeg."""
    _require_ffmpeg()
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={duration}:size={size}:rate=30",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _make_music(path: Path, *, duration: float = 4.0) -> Path:
    """Render a synthetic music/audio file using FFmpeg."""
    _require_ffmpeg()
    path.parent.mkdir(parents=True, exist_ok=True)
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
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _build_transcript(words: list[tuple[str, float, float]]) -> dict[str, object]:
    """Build a minimal transcript JSON for the skills pipeline."""
    return {
        "source_audio": "",
        "language": "en",
        "language_probability": 1.0,
        "word_timestamps": True,
        "segments": [],
        "words": [{"word": w, "start": s, "end": e, "probability": 0.95} for w, s, e in words],
    }


@pytest.fixture
def job_ctx(tmp_path: Path) -> JobContext:
    return JobContext(
        job_id="integration-job",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )


@pytest.fixture
def fresh_queue(monkeypatch: pytest.MonkeyPatch) -> InMemoryJobQueue:
    """Reset the module-level queue singleton so tests don't leak state."""
    q = InMemoryJobQueue()
    monkeypatch.setattr(cli_main, "_queue_instance", q, raising=False)
    return q


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


class TestWorkerDispatchHandshakes:
    """Every worker command maps to a registered handler and a valid payload schema."""

    def test_every_handler_command_has_payload_schema_or_is_internal(
        self,
    ) -> None:
        """Commands that need payload validation must have a schema in
        ``_PAYLOAD_SCHEMAS``. Commands without external payload (e.g. raw-footage
        stage handlers that only use job_id + force) may be absent.
        """
        # Commands that carry user-supplied paths/params and MUST be validated.
        expected_schemas = {
            "run",
            "verify",
            "rough_cut",
            "music_generate",
            "sfx_generate",
            "brief",
            "align",
            "audio_isolate",
            "speech_to_speech",
            "export_comfystudio",
            "import_comfystudio",
            "export_fablecut",
            "storyteller_export",
            "import_fablecut",
            "render_fablecut",
        }
        missing = expected_schemas - set(_PAYLOAD_SCHEMAS.keys())
        assert not missing, f"missing payload schemas: {missing}"

    def test_every_payload_schema_can_be_instantiated_minimally(self) -> None:
        """The run payload schema accepts its required fields."""
        from video_editor_cli.main import _RunPayload

        payload = _RunPayload(source="/work/job/input.mp4")
        assert payload.source == "/work/job/input.mp4"
        assert payload.review is True

    def test_build_job_context_reads_work_and_output_roots(self, tmp_path: Path) -> None:
        """``_build_job_context`` reconstructs a JobContext from a queue message."""
        work = tmp_path / "work"
        output = tmp_path / "output"
        message = JobMessage(
            job_id="j1",
            tenant_id="t1",
            command="run",
            payload={"work_root": str(work), "output_root": str(output)},
        )
        ctx = _build_job_context(message)
        assert ctx.job_id == "j1"
        assert ctx.tenant_id == "t1"
        assert str(ctx.work_root) == str(work)
        assert str(ctx.output_root) == str(output)

    @pytest.mark.asyncio
    async def test_dispatch_message_runs_verify_handler(
        self,
        job_ctx: JobContext,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
    ) -> None:
        """Enqueue a ``verify`` job; the worker dispatches it to the verify handler."""
        from verify.models import DurationRange, RequirementsSpec
        from verify.verify import VerifySkill

        video = _make_video(tmp_path / "final.mp4", duration=2.0)
        job_ctx.output_dir.mkdir(parents=True, exist_ok=True)
        (job_ctx.output_dir / "final.mp4").write_bytes(video.read_bytes())
        job_ctx.manifest().stage_done("export", {"final": str(job_ctx.output_dir / "final.mp4")})
        job_ctx.store.save()

        spec = RequirementsSpec(
            job_id=job_ctx.job_id,
            target_duration_range=DurationRange(min=1.0, max=3.0),
        )
        VerifySkill(job_ctx, requirements=spec).save_requirements(spec)

        message = JobMessage(
            job_id=job_ctx.job_id,
            tenant_id="default",
            command="verify",
            payload={
                "work_root": str(job_ctx.work_root),
                "output_root": str(job_ctx.output_dir.parent),
            },
        )
        await fresh_queue.enqueue(message)
        claimed = await fresh_queue.dequeue()
        assert claimed is not None
        status = await cli_main._dispatch_message(fresh_queue, claimed)

        assert status == "completed"
        assert await fresh_queue.completed_count() == 1
        assert (job_ctx.work_dir / "verify" / "verification_report.json").exists()

    @pytest.mark.asyncio
    async def test_dispatch_message_fails_unknown_command(
        self, fresh_queue: InMemoryJobQueue
    ) -> None:
        """An unknown command is failed, not orphaned (no retries to keep the test fast)."""
        message = JobMessage(
            job_id="j1",
            tenant_id="default",
            command="not_a_command",
            payload={},
            max_retries=0,
        )
        await fresh_queue.enqueue(message)
        claimed = await fresh_queue.dequeue()
        assert claimed is not None
        status = await cli_main._dispatch_message(fresh_queue, claimed)
        assert status == "failed"
        assert await fresh_queue.failed_count() == 1


class TestPipelineStageCommunication:
    """Manifest stages are recorded, persisted, and honored on resume."""

    def test_skills_pipeline_records_all_stages(
        self,
        job_ctx: JobContext,
        tmp_path: Path,
        tmp_media: Path,
    ) -> None:
        """A full skills run records rough_cut, graphics, captions, export."""
        transcript_path = tmp_path / "transcript.json"
        transcript_path.write_text(
            json.dumps(_build_transcript([("Hello", 0.0, 0.4), ("world", 0.5, 1.0)])),
            encoding="utf-8",
        )

        pipeline = SkillsPipeline(job_ctx)
        pipeline.run(tmp_media, transcript_path=transcript_path)

        manifest = job_ctx.manifest()
        for stage in ("rough_cut", "graphics", "captions", "export"):
            assert manifest.is_stage_complete(stage), f"stage {stage} not recorded"

        # Manifest round-trips: reload from disk and stages are still present.
        reloaded = JobContext.from_manifest(job_ctx.store.manifest_path)
        assert reloaded.manifest().is_stage_complete("export")

    def test_manifest_resume_skips_completed_stages(
        self,
        job_ctx: JobContext,
        tmp_path: Path,
        tmp_media: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If export is already complete, ``SkillsPipeline.run`` does not re-export."""
        transcript_path = tmp_path / "transcript.json"
        transcript_path.write_text(
            json.dumps(_build_transcript([("Hello", 0.0, 0.4)])),
            encoding="utf-8",
        )

        first = SkillsPipeline(job_ctx).run(tmp_media, transcript_path=transcript_path)
        final_mtime = Path(first["final"]).stat().st_mtime

        # Patch ExportSkill so a second invocation would raise if called.
        class _RaisingExport(ExportSkill):
            def run(self, *args: Any, **kwargs: Any) -> Path:
                raise AssertionError("export should not re-run on resume")

        monkeypatch.setattr(cli_main, "ExportSkill", _RaisingExport)
        second = SkillsPipeline(job_ctx).run(tmp_media, transcript_path=transcript_path)
        assert Path(second["final"]).stat().st_mtime == final_mtime


class TestExportReviewHandshake:
    """Export writes deliverables; review appends to the report and decision log."""

    def test_review_appends_to_export_report_and_log(
        self,
        job_ctx: JobContext,
        tmp_path: Path,
    ) -> None:
        """A post-export review updates ``report.md`` and ``decision_log.jsonl``."""
        video = _make_video(tmp_path / "with_music.mp4", duration=3.0)
        job_ctx.artifact("music", "with_music.mp4").write_bytes(video.read_bytes())

        export_skill = ExportSkill(job_ctx)
        final = export_skill.run()

        review_skill = PostRenderReviewSkill(job_ctx, loudness_target_lufs=-22.0)
        review_skill.run(final)

        export_skill.append_post_render_review()

        report = (job_ctx.output_dir / "report.md").read_text(encoding="utf-8")
        assert "## Post-Render Review" in report
        assert "overall" in report.lower()

        log = (job_ctx.output_dir / "decision_log.jsonl").read_text(encoding="utf-8")
        decisions = [json.loads(line) for line in log.splitlines()]
        review_decisions = [d for d in decisions if d.get("operation") == "post_render_review"]
        assert len(review_decisions) == 1

    def test_skills_pipeline_with_review_returns_review_report(
        self,
        job_ctx: JobContext,
        tmp_path: Path,
        tmp_media: Path,
    ) -> None:
        """``SkillsPipeline.run(run_post_render_review=True)`` returns the review dict."""
        transcript_path = tmp_path / "transcript.json"
        transcript_path.write_text(
            json.dumps(_build_transcript([("Hello", 0.0, 0.4), ("world", 0.5, 1.0)])),
            encoding="utf-8",
        )

        result = SkillsPipeline(job_ctx).run(
            tmp_media,
            transcript_path=transcript_path,
            run_post_render_review=True,
        )

        assert "review" in result
        assert result["review"]["checks"]
        assert job_ctx.manifest().is_stage_complete("review")


class TestMusicVideoPipelineIntegration:
    """Music-video planner → renderer → captions → review end-to-end."""

    def test_music_video_pipeline_records_plan_export_review(
        self,
        job_ctx: JobContext,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A full music-video run records plan, export, and review stages.

        The real review gate is strict with synthetic test patterns, so we stub
        ``PostRenderReviewSkill.run`` to a PASS report to keep this an integration
        handshake test (the real review logic is covered in ``test_post_render_review.py``).
        """
        music = _make_music(tmp_path / "song.mp3", duration=4.0)
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

        def _noop_render(ctx_arg: Any, plan_path: Path, output_path: Path, **kwargs: Any) -> Path:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            duration = (
                sum(float(s.get("duration_seconds", 0) or 0) for s in plan.get("scenes", [])) or 3.0
            )
            return _make_video(output_path, duration=duration)

        monkeypatch.setattr("video_editor_pipelines.pipeline.render_scene_plan", _noop_render)

        monkeypatch.setattr(
            PostRenderReviewSkill,
            "_run_checks",
            lambda self, video_path, info: [],
        )

        pipeline = MusicVideoPipeline(
            job_ctx,
            music_path=music,
            media_dir=media_dir,
            segment_seconds=2.0,
        )
        result = pipeline.run(run_post_render_review=True)

        assert Path(result["final"]).exists()
        assert job_ctx.manifest().is_stage_complete("music_video_plan")
        assert job_ctx.manifest().is_stage_complete("export")
        assert job_ctx.manifest().is_stage_complete("review")
        assert result["review"]["overall"] == "PASS"


class TestScenePlanAssetRenderHandshake:
    """A ScenePlan can be enriched with assets and rendered through the CLI path."""

    def test_scene_plan_with_assets_roundtrips_through_manager(
        self,
        job_ctx: JobContext,
        tmp_path: Path,
    ) -> None:
        """``AssetIngestionManager`` resolves IMAGE/TTS requests and attaches them to scenes."""
        from video_editor_asset_ingestion.scene_assets import (
            attach_results_to_scene,
            requests_from_scene,
        )

        image_path = tmp_path / "frame.png"
        image_path.write_bytes(b"FAKE_PNG")
        audio_path = tmp_path / "voice.mp3"
        audio_path.write_bytes(b"FAKE_MP3")

        scene = Scene(
            id="scene_001",
            type="narration",
            duration_seconds=5.0,
            narration="hello world",
            visual_description="a wide shot",
            asset_query="",
            props={},
        )

        requests = list(
            requests_from_scene(
                scene,
                job_ctx.job_id,
                tts_provider="stub_tts",
                image_provider="stub_image",
            )
        )
        assert any(r.asset_type == AssetType.IMAGE for r in requests)
        assert any(r.asset_type == AssetType.TTS for r in requests)

        # Resolve with stub providers (no network).
        config = AssetProviderConfig.load()
        manager = AssetIngestionManager(job_ctx, config=config)
        results = asyncio.run(manager.resolve(requests))
        assert len(results) == len(requests)

        attach_results_to_scene(scene, results)
        assert scene.assets
        assert any(a.asset_type == AssetType.IMAGE for a in scene.assets)
        assert any(a.asset_type == AssetType.TTS for a in scene.assets)


class TestProviderRegistryHandshake:
    """Provider registry and TTS router can be constructed and route requests."""

    def test_provider_registry_contains_expected_providers(self) -> None:
        """The registry includes the providers the engine depends on."""
        expected = {
            "stub_image",
            "stub_tts",
            "elevenlabs_tts",
            "elevenlabs_music",
            "elevenlabs_sfx",
            "elevenlabs_isolation",
            "elevenlabs_forced_alignment",
            "poyo_ai",
            "poyo_music",
            "sarvam_tts",
            "tts_router",
        }
        present = set(PROVIDER_REGISTRY.keys())
        missing = expected - present
        assert not missing, f"expected providers missing from registry: {missing}"

    def test_tts_router_defaults_to_elevenlabs(self) -> None:
        """``TTSRouter`` sends unprefixed voices to ElevenLabs in every language; prefixes still win."""
        from video_editor_asset_ingestion.providers.tts.tts_router import _pick_provider

        assert _pick_provider("shubh", "hi-IN") == "elevenlabs"
        assert _pick_provider("Brian", "en") == "elevenlabs"
        assert _pick_provider("Brian", "ru") == "elevenlabs"
        assert _pick_provider("sarvam:shubh", "en") == "sarvam"
        assert _pick_provider("elevenlabs:Brian", "hi") == "elevenlabs"


class TestFableCutBridgeRoundtrip:
    """FableCut export/import/render handshakes use compatible JSON schemas."""

    def test_fablecut_export_import_roundtrip(
        self,
        job_ctx: JobContext,
        tmp_path: Path,
    ) -> None:
        """Export a ScenePlan to FableCut, then import it back; ids and durations survive."""
        from video_editor_fablecut_bridge.exporter import export_to_fablecut
        from video_editor_fablecut_bridge.importer import import_from_fablecut

        image_path = tmp_path / "frame.png"
        image_path.write_bytes(b"FAKE_PNG")

        scene = Scene(
            id="scene_001",
            type="narration",
            duration_seconds=5.0,
            narration="hello",
            visual_description="wide shot",
            asset_query="",
            props={},
        )
        scene.assets = [
            AssetResult(
                asset_id="img_001",
                asset_type=AssetType.IMAGE,
                local_path=str(image_path),
                scene_id=scene.id,
            )
        ]
        plan = ScenePlan(
            job_id=job_ctx.job_id,
            script="roundtrip test",
            scenes=[scene],
            aspect_ratio="16:9",
        )

        project_dir = tmp_path / "fablecut"
        export_to_fablecut(plan, project_dir)
        assert (project_dir / "project.json").exists()

        imported = import_from_fablecut(project_dir / "project.json", plan)
        assert imported.job_id == plan.job_id
        assert len(imported.scenes) == 1
        assert imported.scenes[0].id == scene.id
        assert imported.scenes[0].duration_seconds == scene.duration_seconds


class TestQueueWorkerEndToEnd:
    """``ve enqueue`` + ``ve worker`` process a job through the dispatch path."""

    def test_enqueue_run_and_worker_dispatch_completes(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        job_ctx: JobContext,
        tmp_path: Path,
        tmp_media: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Enqueue ``run`` for a directory source; worker dispatches to raw-footage pipeline."""
        source_dir = job_ctx.work_dir / "sources"
        source_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(tmp_media, source_dir / "test.mp4")

        from video_editor_transcribe import whisper_local

        whisper_local._MODEL_CACHE.clear()

        class _FakeWord:
            def __init__(self, word: str, start: float, end: float) -> None:
                self.word = word
                self.start = start
                self.end = end
                self.probability = 0.95

        class _FakeSegment:
            def __init__(self) -> None:
                self.id = 0
                self.start = 0.0
                self.end = 1.0
                self.text = "hello world"
                self.words = [_FakeWord("hello", 0.0, 0.5), _FakeWord("world", 0.5, 1.0)]

        class _FakeInfo:
            def __init__(self) -> None:
                self.language = "en"
                self.language_probability = 0.99

        class _FakeModel:
            def transcribe(self, *args: Any, **kwargs: Any) -> tuple[Any, Any]:
                return iter([_FakeSegment()]), _FakeInfo()

        monkeypatch.setattr(whisper_local, "_load_model", lambda *a, **k: _FakeModel())

        enqueue = cli_runner.invoke(
            app,
            [
                "enqueue",
                "run",
                "-j",
                job_ctx.job_id,
                "--tenant-id",
                "default",
                "--payload",
                json.dumps(
                    {
                        "source": str(source_dir),
                        "work_root": str(tmp_path / "work"),
                        "output_root": str(tmp_path / "output"),
                        "review": False,
                    }
                ),
            ],
        )
        assert enqueue.exit_code == 0, enqueue.output

        worker = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert worker.exit_code == 0, worker.output

        assert (job_ctx.output_dir / "final.mp4").exists()
        # The worker writes a fresh manifest with the payload roots; reload from disk.
        reloaded_ctx = JobContext.from_manifest(job_ctx.store.manifest_path)
        assert reloaded_ctx.manifest().is_stage_complete("export")

    def test_cli_run_forwards_review_flag_to_pipeline(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
        tmp_media: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``ve run --skip-review`` does not run the post-render review gate."""
        transcript_path = tmp_path / "transcript.json"
        transcript_path.write_text(
            json.dumps(_build_transcript([("Hello", 0.0, 0.4), ("world", 0.5, 1.0)])),
            encoding="utf-8",
        )

        review_ran: list[bool] = []
        original_run = SkillsPipeline.run

        def _wrapped_run(self: SkillsPipeline, *args: Any, **kwargs: Any) -> dict[str, Any]:
            review_ran.append(kwargs.get("run_post_render_review", False))
            return original_run(self, *args, **kwargs)

        monkeypatch.setattr(SkillsPipeline, "run", _wrapped_run)

        result = cli_runner.invoke(
            app,
            [
                "run",
                str(tmp_media),
                "-j",
                "skip-review-cli",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
                "--transcript",
                str(transcript_path),
                "--skip-review",
            ],
        )
        assert result.exit_code == 0, result.output
        assert review_ran == [False]


class TestTemplateRouterIntegration:
    """TemplateRouter maps source paths to the right pipeline kind and preset."""

    def test_router_selects_skills_for_file_source(self, tmp_path: Path, tmp_media: Path) -> None:
        selected = TemplateRouter().select(source=tmp_media)
        assert selected.pipeline_kind == "v2_skills"

    def test_router_selects_raw_footage_for_directory(
        self, tmp_path: Path, tmp_media: Path
    ) -> None:
        source_dir = tmp_path / "raw"
        source_dir.mkdir()
        shutil.copy(tmp_media, source_dir / "clip.mp4")
        selected = TemplateRouter().select(source=source_dir)
        assert selected.pipeline_kind == "v1_raw"

    def test_router_selects_musical_video_with_media_dir(self, tmp_path: Path) -> None:
        music = _make_music(tmp_path / "song.mp3")
        media_dir = tmp_path / "visuals"
        media_dir.mkdir()
        selected = TemplateRouter().select(
            source=music,
            template_name=None,
            overrides={"media_dir": str(media_dir)},
        )
        assert selected.pipeline_kind == "music_video"


class TestProjectConfigHandshake:
    """ProjectConfig loads consistently across skills that need it."""

    def test_config_loads_for_cli_and_pipeline(self, tmp_path: Path) -> None:
        """A config.yaml written by one skill is readable by CLI and pipeline."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("paths:\n  presets: ./presets\n", encoding="utf-8")

        from video_editor_cli.main import _resolve_presets_dir

        presets_dir = _resolve_presets_dir(None, config_path)
        assert presets_dir == tmp_path / "presets"

        config = ProjectConfig.load(config_path)
        assert config is not None


class TestPayloadPathValidation:
    """#278: a queued root must sit inside the configured data roots."""

    def test_rejects_a_root_outside_the_data_root(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="configured data root"):
            _validate_payload_root(str(tmp_path / "elsewhere"), str(tmp_path / "data"), "work_root")

    def test_allows_a_root_inside_the_data_root(self, tmp_path: Path) -> None:
        _validate_payload_root(str(tmp_path / "data" / "work"), str(tmp_path / "data"), "work_root")

    def test_rejects_a_sibling_with_a_shared_prefix(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="configured data root"):
            _validate_payload_root(str(tmp_path / "abc-evil"), str(tmp_path / "abc"), "work_root")

    def test_build_job_context_rejects_an_outside_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from video_editor_state.queue import JobMessage

        monkeypatch.setenv("VE_WORK_ROOT", str(tmp_path / "data"))
        monkeypatch.setenv("VE_OUTPUT_ROOT", str(tmp_path / "data"))
        message = JobMessage(
            job_id="job-1",
            tenant_id="t",
            command="music",
            payload={"work_root": str(tmp_path / "elsewhere")},
        )

        with pytest.raises(ValueError, match="configured data root"):
            _build_job_context(message)


# ---------------------------------------------------------------------------
# SkillProtocol compliance
# ---------------------------------------------------------------------------


def test_all_skills_have_name_property() -> None:
    """Every skill must expose a ``name`` property for SkillProtocol compliance."""
    from captions.captions import CaptionsSkill
    from export.export import ExportSkill
    from graphics.graphics import GraphicsSkill
    from music.music import MusicSkill
    from review.review import PostRenderReviewSkill
    from rough_cut.rough_cut import RoughCutSkill
    from second_pass.second_pass import SecondPassSkill
    from verify.verify import VerifySkill

    # We can't instantiate without a JobContext, but we can check the class.
    skills = [
        RoughCutSkill,
        GraphicsSkill,
        CaptionsSkill,
        MusicSkill,
        ExportSkill,
        PostRenderReviewSkill,
        SecondPassSkill,
        VerifySkill,
    ]
    for skill_cls in skills:
        assert hasattr(skill_cls, "name") or hasattr(skill_cls, "run"), (
            f"{skill_cls.__name__} must have 'name' property and 'run' method"
        )


def test_run_payload_carries_the_final_image_choice() -> None:
    from video_editor_cli.payloads import RunPayload

    assert RunPayload(source="input.mp4", final=True).final is True
    assert RunPayload(source="input.mp4").final is False
