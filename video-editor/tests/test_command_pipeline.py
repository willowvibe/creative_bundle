"""Tests for the real pipeline commands registered by ``commands.pipeline``.

All tests are network-free and hermetic: they use temporary work/output roots and
stub providers. Anything that shells out to FFmpeg is either gated behind
``_require_ffmpeg`` or has its heavy call monkeypatched.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from qa.models import QACheck, QAReport
from video_editor_asset_ingestion.models import AssetType
from video_editor_core.models.scene_plan import Scene, ScenePlan
from video_editor_engine.commands import CommandExecutionError, default_registry
from video_editor_state.job import JobContext
from video_editor_state.ledger import NullLedger

from tests.conftest import _require_ffmpeg

_LEDGER_ON = """
ledger:
  enabled: true
  path: projects.db
assets:
  strict: false  # these runs resolve offline through the stub fallbacks (#281)
"""

_LEDGER_OFF = """
ledger:
  enabled: false
assets:
  strict: false  # these runs resolve offline through the stub fallbacks (#281)
"""


def _run(coro: Any) -> Any:
    """Run a coroutine to completion in a plain (sync) test."""
    return asyncio.run(coro)


def _write_config(tmp_path: Path, *, ledger: bool) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_LEDGER_ON if ledger else _LEDGER_OFF, encoding="utf-8")
    return config_path


def _write_plan(work_root: Path, job_id: str, plan: ScenePlan, name: str) -> Path:
    plan_dir = work_root / job_id / "plan"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / name
    plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    return plan_path


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestPipelineCommandsRegistered:
    def test_commands_and_categories(self) -> None:
        commands = {command.id: command for command in default_registry.list_commands()}
        expected = {
            "project.status": "project",
            "qa.run": "qa",
            "assets.resolve": "assets",
            "render.run": "render",
        }
        for command_id, category in expected.items():
            assert command_id in commands, f"{command_id} not registered"
            command = commands[command_id]
            assert command.category == category
            assert command.summary
            assert command.params_model.model_json_schema()["type"] == "object"


# ---------------------------------------------------------------------------
# project.status
# ---------------------------------------------------------------------------


class TestProjectStatus:
    def test_reads_project_from_ledger(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path, ledger=True)
        ledger = JobContext._resolve_ledger(str(config_path))
        assert not isinstance(ledger, NullLedger)
        ledger.upsert_project("job-status", status="running", template="explainer_reel")
        ledger.record_job("job-status", "render")
        ledger.record_cost("job-status", provider="stub_tts", task_type="tts", actual_cost=0.0)
        ledger.close()

        result = _run(
            default_registry.execute(
                "project.status",
                {
                    "job_id": "job-status",
                    "work_root": str(tmp_path / "projects"),
                    "output_root": str(tmp_path / "outputs"),
                    "config_path": str(config_path),
                },
            )
        )

        assert result["found"] is True
        assert result["project_id"] == "job-status"
        assert result["project"]["status"] == "running"
        assert result["project"]["template"] == "explainer_reel"
        assert [job["stage"] for job in result["project"]["jobs"]] == ["render"]
        assert len(result["project"]["cost_lines"]) == 1
        json.dumps(result)  # must be JSON-serializable

    def test_missing_project_is_not_found(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path, ledger=True)
        result = _run(
            default_registry.execute(
                "project.status",
                {"job_id": "does-not-exist", "config_path": str(config_path)},
            )
        )
        assert result["found"] is False
        assert result["project"] is None

    def test_ledger_disabled_is_not_found(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path, ledger=False)
        result = _run(
            default_registry.execute(
                "project.status",
                {"job_id": "any", "config_path": str(config_path)},
            )
        )
        assert result["found"] is False


# ---------------------------------------------------------------------------
# qa.run
# ---------------------------------------------------------------------------


class TestQaRun:
    def test_builds_context_and_serializes_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ``qa.run`` has no config_path param; point the default config
        # resolution at a tmp config so no repo ledger is touched.
        monkeypatch.setenv("VIDEO_EDITOR_CONFIG", str(_write_config(tmp_path, ledger=False)))

        captured: dict[str, Any] = {}

        class _FakeQASkill:
            def __init__(self, ctx: JobContext, expected_aspect_ratio: str = "16:9") -> None:
                captured["ctx"] = ctx
                captured["aspect"] = expected_aspect_ratio

            def run(self, video_path: str | None = None) -> QAReport:
                captured["video_path"] = video_path
                ctx = captured["ctx"]
                return QAReport(
                    job_id=ctx.job_id,
                    video_path=str(video_path or ctx.output_dir / "auto.mp4"),
                    duration_seconds=2.0,
                    width=1920,
                    height=1080,
                    fps=30.0,
                    has_audio=True,
                    checks=[QACheck(name="file_present", passed=True)],
                    passed=True,
                )

        import qa.qa as qa_module

        monkeypatch.setattr(qa_module, "QASkill", _FakeQASkill)

        video = tmp_path / "render.mp4"
        video.write_bytes(b"FAKE")
        result = _run(
            default_registry.execute(
                "qa.run",
                {
                    "job_id": "job-qa",
                    "work_root": str(tmp_path / "projects"),
                    "output_root": str(tmp_path / "outputs"),
                    "expected_aspect_ratio": "4:3",
                    "video_path": str(video),
                },
            )
        )

        assert captured["aspect"] == "4:3"
        assert captured["video_path"] == str(video)
        assert captured["ctx"].job_id == "job-qa"
        assert result["passed"] is True
        assert result["job_id"] == "job-qa"
        assert result["checks"][0]["name"] == "file_present"
        json.dumps(result)


# ---------------------------------------------------------------------------
# assets.resolve
# ---------------------------------------------------------------------------


def _two_scene_plan(job_id: str) -> ScenePlan:
    return ScenePlan(
        job_id=job_id,
        script="A tiny script.",
        scenes=[
            Scene(
                id="scene_001",
                type="narration",
                duration_seconds=1.0,
                narration="Hello world.",
                visual_description="A soft gradient.",
            )
        ],
    )


def _image_only_plan(job_id: str) -> ScenePlan:
    return ScenePlan(
        job_id=job_id,
        script="A tiny script.",
        scenes=[
            Scene(
                id="scene_001",
                type="narration",
                duration_seconds=1.0,
                visual_description="A soft gradient.",
            )
        ],
    )


class TestAssetsResolve:
    def test_defaults_to_the_image_chain(self) -> None:
        """The engine pipeline must not pin a single provider by default."""
        from video_editor_engine.commands.pipeline import AssetsResolveParams

        assert AssetsResolveParams(job_id="job-defaults").image_provider == "auto"

    def test_forwards_an_explicit_image_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An explicitly named provider must reach the image requests unchanged."""
        from video_editor_asset_ingestion.manager import AssetIngestionManager
        from video_editor_asset_ingestion.models import BatchResult

        seen: dict[str, set[str]] = {}

        async def fake_resolve_partial(self, requests):  # type: ignore[no-untyped-def]
            seen["providers"] = {
                request.provider for request in requests if request.asset_type == AssetType.IMAGE
            }
            return BatchResult()

        monkeypatch.setattr(AssetIngestionManager, "resolve_partial", fake_resolve_partial)
        config_path = _write_config(tmp_path, ledger=False)
        work_root = tmp_path / "projects"
        _write_plan(work_root, "job-explicit", _image_only_plan("job-explicit"), "scene_plan.json")

        _run(
            default_registry.execute(
                "assets.resolve",
                {
                    "job_id": "job-explicit",
                    "work_root": str(work_root),
                    "output_root": str(tmp_path / "outputs"),
                    "config_path": str(config_path),
                    "image_provider": "poyo_ai",
                },
            )
        )

        assert seen["providers"] == {"poyo_ai"}

    def test_resolves_stub_tts_and_image(self, tmp_path: Path) -> None:
        # Stub TTS shells out to ffmpeg to synthesise a sine tone.
        _require_ffmpeg()
        config_path = _write_config(tmp_path, ledger=True)
        work_root = tmp_path / "projects"
        plan = _two_scene_plan("job-assets")
        _write_plan(work_root, "job-assets", plan, "scene_plan.json")

        result = _run(
            default_registry.execute(
                "assets.resolve",
                {
                    "job_id": "job-assets",
                    "work_root": str(work_root),
                    "output_root": str(tmp_path / "outputs"),
                    "config_path": str(config_path),
                },
            )
        )

        assert result["resolved"] == 2
        assert result["failed"] == []
        plan_out = Path(result["plan_out"])
        assert plan_out.exists()
        enriched = json.loads(plan_out.read_text(encoding="utf-8"))
        asset_types = {asset["asset_type"] for asset in enriched["scenes"][0]["assets"]}
        assert asset_types == {"tts", "image"}

    def test_image_only_resolution_is_offline(self, tmp_path: Path) -> None:
        """A visual-only scene uses the stub image provider (no ffmpeg needed)."""
        config_path = _write_config(tmp_path, ledger=False)
        work_root = tmp_path / "projects"
        _write_plan(work_root, "job-img", _image_only_plan("job-img"), "scene_plan.json")

        result = _run(
            default_registry.execute(
                "assets.resolve",
                {
                    "job_id": "job-img",
                    "work_root": str(work_root),
                    "output_root": str(tmp_path / "outputs"),
                    "config_path": str(config_path),
                },
            )
        )

        assert result["resolved"] == 1
        assert result["failed"] == []

    def test_missing_plan_raises(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path, ledger=False)
        with pytest.raises(CommandExecutionError):
            _run(
                default_registry.execute(
                    "assets.resolve",
                    {
                        "job_id": "no-plan",
                        "work_root": str(tmp_path / "projects"),
                        "output_root": str(tmp_path / "outputs"),
                        "config_path": str(config_path),
                    },
                )
            )


# ---------------------------------------------------------------------------
# render.run
# ---------------------------------------------------------------------------


class TestRenderRun:
    def test_invokes_renderer_and_reports_duration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_config(tmp_path, ledger=False)
        work_root = tmp_path / "projects"
        output_root = tmp_path / "outputs"
        plan_path = _write_plan(
            work_root, "job-render", _two_scene_plan("job-render"), "scene_plan_with_assets.json"
        )

        captured: dict[str, Any] = {}

        import video_editor_core.scene_plan_composition as composition

        def _fake_render(
            plan_path: Path | str, output_path: Path | str, project_dir: Path | str, **_kw: Any
        ) -> Path:
            captured["plan_path"] = Path(plan_path)
            captured["output_path"] = Path(output_path)
            captured["project_dir"] = Path(project_dir)
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"NOT-A-REAL-VIDEO")
            return Path(output_path)

        monkeypatch.setattr(composition, "render_scene_plan", _fake_render)

        result = _run(
            default_registry.execute(
                "render.run",
                {
                    "job_id": "job-render",
                    "work_root": str(work_root),
                    "output_root": str(output_root),
                    "config_path": str(config_path),
                },
            )
        )

        assert captured["plan_path"] == plan_path
        assert captured["output_path"] == output_root / "job-render" / "final.mp4"
        assert captured["project_dir"] == work_root / "job-render" / "hyperframes"
        assert result["output"].endswith("final.mp4")
        # Probing the fake output fails (#310): report null plus the error,
        # never substitute the plan's scene total for a measured duration.
        assert result["duration_seconds"] is None
        assert "duration_error" in result
        assert result["render_engine"] == "hyperframes"
        json.dumps(result)

    def test_missing_plan_raises(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path, ledger=False)
        with pytest.raises(CommandExecutionError):
            _run(
                default_registry.execute(
                    "render.run",
                    {
                        "job_id": "no-plan",
                        "work_root": str(tmp_path / "projects"),
                        "output_root": str(tmp_path / "outputs"),
                        "config_path": str(config_path),
                    },
                )
            )
