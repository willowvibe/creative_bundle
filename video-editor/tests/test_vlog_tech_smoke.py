"""CLI wiring smoke for the vlogger + tech templates (Task 21 follow-up).

Mocks ONLY the pipeline entry point (``SkillsPipeline.run_async``) so
``ve run`` dispatch, template selection, and preset resolution run for real.
Proves the CLI connects end-to-end:

    ve run ./clips/ --template vlog_reel             -> SkillsPipeline on the first clip
    ve run ./talk.mp4 --template talking_head_polish -> SkillsPipeline w/ vlogger_youtube
    ve run ./screen.mp4 --template tech_tutorial     -> SkillsPipeline w/ tech_tutorial
    ve worker 'run' payload (screen_to_video)        -> SkillsPipeline w/ tech_tutorial

This is a wiring smoke, NOT a re-test of pipeline internals (covered by
``test_vlog_pipeline.py``, ``test_vlog_templates.py``,
``test_tech_templates.py``). Source files are dummies — every stage that
touches media runs inside the mocked pipeline entry point.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner
from video_editor_state.job import JobContext


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


def _make_dummy_video(path: Path) -> None:
    path.write_bytes(b"\x00" * 16)


class TestCLIRunWiring:
    """``ve run`` dispatch + preset resolution for the new templates."""

    def test_vlog_reel_directory_runs_skills_pipeline_on_first_clip(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Directory source + --template vlog_reel runs the skills pipeline on
        the first clip (the v1 raw-footage pipeline was retired)."""
        from video_editor_cli.main import app

        clips = tmp_path / "clips"
        clips.mkdir()
        _make_dummy_video(clips / "a.mp4")
        _make_dummy_video(clips / "b.mp4")

        captured: dict[str, Any] = {}

        async def _fake_run_async(
            self: Any,
            source_path: Path,
            **kwargs: Any,
        ) -> dict[str, Any]:
            captured["source"] = source_path
            captured["preset"] = self.preset
            return {"final": str(self.ctx.output_root / "final.mp4")}

        monkeypatch.setattr(
            "video_editor_pipelines.pipeline.SkillsPipeline.run_async",
            _fake_run_async,
        )

        result = cli_runner.invoke(
            app,
            [
                "run",
                str(clips),
                "--template",
                "vlog_reel",
                "--job-id",
                "smoke-vlog",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Template: vlog_reel (raw_to_video, preset=vlogger_youtube" in result.output
        assert "Pipeline complete. Final output:" in result.output
        # the directory resolved to the first supported clip (sorted)
        assert Path(str(captured["source"])).name == "a.mp4"

    def test_talking_head_polish_single_file_applies_template_preset(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """raw_to_video sentinel: template preset wins over CLI default."""
        from video_editor_cli.main import app

        source = tmp_path / "talk.mp4"
        _make_dummy_video(source)

        captured: dict[str, Any] = {}

        async def _fake_run_async(
            self: Any,
            source_path: Path,
            **kwargs: Any,
        ) -> dict[str, Any]:
            captured["preset"] = self.preset
            return {"final": str(self.ctx.output_root / "final.mp4")}

        monkeypatch.setattr(
            "video_editor_pipelines.pipeline.SkillsPipeline.run_async",
            _fake_run_async,
        )

        result = cli_runner.invoke(
            app,
            [
                "run",
                str(source),
                "--template",
                "talking_head_polish",
                "--skip-music",
                "--job-id",
                "smoke-talk",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Template: talking_head_polish (raw_to_video, preset=vlogger_youtube" in (
            result.output
        )
        assert captured["preset"] == "vlogger_youtube"

    def test_tech_tutorial_single_file_applies_tech_preset(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """screen_to_video sentinel: tech_tutorial preset must not be dropped.

        Regression lock: the preset sentinel previously matched only
        ``raw_to_video``, so ``tech_tutorial`` renders silently fell back
        to the CLI default ``short_explainer`` preset.
        """
        from video_editor_cli.main import app

        source = tmp_path / "screen.mp4"
        _make_dummy_video(source)

        captured: dict[str, Any] = {}

        async def _fake_run_async(
            self: Any,
            source_path: Path,
            **kwargs: Any,
        ) -> dict[str, Any]:
            captured["preset"] = self.preset
            return {"final": str(self.ctx.output_root / "final.mp4")}

        monkeypatch.setattr(
            "video_editor_pipelines.pipeline.SkillsPipeline.run_async",
            _fake_run_async,
        )

        result = cli_runner.invoke(
            app,
            [
                "run",
                str(source),
                "--template",
                "tech_tutorial",
                "--skip-music",
                "--job-id",
                "smoke-tutorial",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Template: tech_tutorial (screen_to_video, preset=tech_tutorial" in (result.output)
        assert captured["preset"] == "tech_tutorial"


class TestWorkerRunWiring:
    """The ``ve worker`` ``run`` handler mirrors the CLI preset sentinel."""

    def test_worker_run_tech_tutorial_applies_tech_preset(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from video_editor_cli.main import _handle_run

        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        ctx = JobContext(
            job_id="worker-tutorial",
            work_root=work_root,
            output_root=output_root,
        )

        source_video = ctx.work_dir / "screen.mp4"
        ctx.ensure_dirs()
        _make_dummy_video(source_video)

        captured: dict[str, Any] = {}

        async def _fake_run_async(
            self: Any,
            source_path: Path,
            **kwargs: Any,
        ) -> dict[str, Any]:
            captured["preset"] = self.preset
            return {"final": str(output_root / "worker-tutorial" / "final.mp4")}

        monkeypatch.setattr(
            "video_editor_pipelines.pipeline.SkillsPipeline.run_async",
            _fake_run_async,
        )

        asyncio.run(
            _handle_run(
                ctx,
                {
                    "source": str(source_video),
                    "template": "tech_tutorial",
                    "work_root": str(work_root),
                    "output_root": str(output_root),
                },
            )
        )

        assert captured["preset"] == "tech_tutorial"
