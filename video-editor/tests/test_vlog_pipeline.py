"""End-to-end tests for the raw_to_video vlogger pipeline (Task 10).

The v2 ``SkillsPipeline`` gained five optional stages for the
``raw_to_video`` templates (``vlog_reel``, ``talking_head_polish``):

- ``audio_cleanup`` — pre-stage before the rough cut
- ``transitions`` — between rough cut and graphics
- ``reframe`` — between rough cut and graphics
- ``broll`` — between rough cut and graphics
- ``sfx_placement`` — between captions and music

Each is gated by a boolean flag on the pipeline. These tests lock the
orchestration contract end-to-end: stage order, artifact paths, chaining
(what feeds graphics / music), resume behavior, and the no-op default.
Provider-dependent stages (broll, sfx_placement) are mocked with copies so
the tests run offline without API keys.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
        job_id="vlog-pipeline-job",
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
            ("pause", 2.0, 2.5),
            ("Again", 3.0, 3.4),
        ]
    )
    path = tmp_path / "transcript.json"
    path.write_text(json.dumps(transcript), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Recording fakes for the five optional stages. Each copies its input to the
# expected output path so downstream stages (graphics/captions/music/export)
# still have a real, probeable MP4 to consume. They record invocation order so
# the orchestration contract is verifiable.
# ---------------------------------------------------------------------------


class _FakeAudioCleanupSkill:
    def __init__(self, ctx_arg: JobContext, **kwargs: object) -> None:
        self.ctx = ctx_arg
        self.kwargs = kwargs

    def run(
        self,
        input_path: Path | str,
        output_name: str = "cleaned.wav",
        return_video: bool = False,
    ) -> Path:
        record_stage("audio_cleanup")
        src = Path(input_path)
        out = self.ctx.work_dir / "audio_cleanup" / "cleaned.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(src.read_bytes())
        return out


class _FakeTransitionSkill:
    def __init__(self, ctx_arg: JobContext, **kwargs: object) -> None:
        self.ctx = ctx_arg
        self.kwargs = kwargs

    def run(
        self,
        segment_paths: list[Path | str],
        *,
        output_name: str = "with_transitions.mp4",
        edl: object | None = None,
        transitions: object | None = None,
    ) -> dict[str, Any]:
        record_stage("transitions")
        out = self.ctx.work_dir / "transitions" / output_name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(Path(segment_paths[0]).read_bytes())
        return {"output": str(out)}


class _FakeReframeSkill:
    def __init__(self, ctx_arg: JobContext, **kwargs: object) -> None:
        self.ctx = ctx_arg
        self.kwargs = kwargs

    def run(
        self,
        input_path: Path | str,
        output_name: str = "reframed.mp4",
        transcript_energy: list[float] | None = None,
    ) -> Path:
        record_stage("reframe")
        out = self.ctx.work_dir / "reframe" / output_name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(Path(input_path).read_bytes())
        return out


class _FakeBrollSkill:
    def __init__(self, ctx_arg: JobContext, **kwargs: object) -> None:
        self.ctx = ctx_arg
        self.kwargs = kwargs

    async def run(
        self,
        video_path: Path | str,
        transcript: dict[str, Any],
        *,
        output_name: str = "with_broll.mp4",
    ) -> dict[str, Any]:
        record_stage("broll")
        out = self.ctx.work_dir / "broll" / output_name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(Path(video_path).read_bytes())
        return {"output": str(out)}


class _FakeSfxPlacementSkill:
    def __init__(self, ctx_arg: JobContext, **kwargs: object) -> None:
        self.ctx = ctx_arg
        self.kwargs = kwargs

    async def run(
        self,
        video_path: Path | str,
        transcript: dict[str, Any],
        *,
        output_name: str = "with_sfx.mp4",
    ) -> dict[str, Any]:
        record_stage("sfx_placement")
        out = self.ctx.work_dir / "sfx_placement" / output_name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(Path(video_path).read_bytes())
        return {"output": str(out)}


_CALLED: list[str] = []


def record_stage(name: str) -> None:
    _CALLED.append(name)


@pytest.fixture(autouse=True)
def _reset_called() -> None:
    _CALLED.clear()


def _install_optional_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the pipeline's lazy skill imports at the recording fakes."""
    monkeypatch.setattr("audio_cleanup.audio_cleanup.AudioCleanupSkill", _FakeAudioCleanupSkill)
    monkeypatch.setattr("transitions.transitions.TransitionSkill", _FakeTransitionSkill)
    monkeypatch.setattr("reframe.reframe.ReframeSkill", _FakeReframeSkill)
    monkeypatch.setattr("broll.broll.BrollSkill", _FakeBrollSkill)
    monkeypatch.setattr("sfx_placement.sfx_placement.SfxPlacementSkill", _FakeSfxPlacementSkill)


def _install_raising_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install fakes that raise if invoked (for resume assertions)."""

    def _raise(*args: object, **kwargs: object) -> object:
        raise AssertionError("optional stage should have been skipped on resume")

    async def _raise_async(*args: object, **kwargs: object) -> object:
        raise AssertionError("optional stage should have been skipped on resume")

    class _Audio:
        def __init__(self, ctx_arg: JobContext, **kwargs: object) -> None:
            pass

        def run(self, *args: object, **kwargs: object) -> Path:
            raise AssertionError("audio_cleanup should have been skipped on resume")

    class _Transitions:
        def __init__(self, ctx_arg: JobContext, **kwargs: object) -> None:
            pass

        def run(self, *args: object, **kwargs: object) -> dict[str, Any]:
            raise AssertionError("transitions should have been skipped on resume")

    class _Reframe:
        def __init__(self, ctx_arg: JobContext, **kwargs: object) -> None:
            pass

        def run(self, *args: object, **kwargs: object) -> Path:
            raise AssertionError("reframe should have been skipped on resume")

    class _Broll:
        def __init__(self, ctx_arg: JobContext, **kwargs: object) -> None:
            pass

        async def run(self, *args: object, **kwargs: object) -> dict[str, Any]:
            raise AssertionError("broll should have been skipped on resume")

    class _SfxPlacement:
        def __init__(self, ctx_arg: JobContext, **kwargs: object) -> None:
            pass

        async def run(self, *args: object, **kwargs: object) -> dict[str, Any]:
            raise AssertionError("sfx_placement should have been skipped on resume")

    monkeypatch.setattr("audio_cleanup.audio_cleanup.AudioCleanupSkill", _Audio)
    monkeypatch.setattr("transitions.transitions.TransitionSkill", _Transitions)
    monkeypatch.setattr("reframe.reframe.ReframeSkill", _Reframe)
    monkeypatch.setattr("broll.broll.BrollSkill", _Broll)
    monkeypatch.setattr("sfx_placement.sfx_placement.SfxPlacementSkill", _SfxPlacement)


class TestOptionalStagesDefaultOff:
    """With all flags at their defaults, the optional raw_to_video stages
    are strict no-ops — no artifacts, no result keys."""

    def test_all_flags_default_to_false(self) -> None:
        pipeline = SkillsPipeline(JobContext(job_id="x"))
        assert pipeline.run_audio_cleanup is False
        assert pipeline.run_transitions is False
        assert pipeline.run_reframe is False
        assert pipeline.run_broll is False
        assert pipeline.run_sfx_placement is False

    def test_no_optional_artifacts_when_disabled(
        self,
        tmp_path: Path,
        ctx: JobContext,
        transcript_with_filler: Path,
        tmp_media: Path,
    ) -> None:
        pipeline = SkillsPipeline(ctx)
        result = pipeline.run(tmp_media, transcript_path=transcript_with_filler)

        for key in ("audio_cleanup", "transitions", "reframe", "broll", "sfx_placement"):
            assert key not in result, f"unexpected result key {key!r}"

        assert not (ctx.work_dir / "audio_cleanup").exists()
        assert not (ctx.work_dir / "transitions").exists()
        assert not (ctx.work_dir / "reframe").exists()
        assert not (ctx.work_dir / "sfx_placement").exists()
        # The pre-existing broll dir is only written when the broll stage runs.
        assert not (ctx.work_dir / "broll" / "with_broll.mp4").exists()


class TestOptionalStagesChain:
    """When enabled, the optional stages run in the documented order and
    each one's output feeds the next consumer."""

    def test_optional_stages_run_in_order(
        self,
        tmp_path: Path,
        ctx: JobContext,
        transcript_with_filler: Path,
        tmp_media: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install_optional_fakes(monkeypatch)

        pipeline = SkillsPipeline(
            ctx,
            run_audio_cleanup=True,
            run_transitions=True,
            run_reframe=True,
            run_broll=True,
            run_sfx_placement=True,
            reframe_aspect="9:16",
        )
        result = pipeline.run(tmp_media, transcript_path=transcript_with_filler)

        # Every optional stage ran, and in the documented order.
        assert _CALLED == [
            "audio_cleanup",
            "transitions",
            "reframe",
            "broll",
            "sfx_placement",
        ], f"unexpected stage order: {_CALLED!r}"

        # Every optional artifact exists on disk.
        assert (ctx.work_dir / "audio_cleanup" / "cleaned.mp4").exists()
        assert (ctx.work_dir / "transitions" / "with_transitions.mp4").exists()
        assert (ctx.work_dir / "reframe" / "reframed.mp4").exists()
        assert (ctx.work_dir / "broll" / "with_broll.mp4").exists()
        assert (ctx.work_dir / "sfx_placement" / "with_sfx.mp4").exists()

        # The result dict reports every optional stage path.
        assert result["audio_cleanup"].endswith("audio_cleanup/cleaned.mp4")
        assert result["transitions"].endswith("transitions/with_transitions.mp4")
        assert result["reframe"].endswith("reframe/reframed.mp4")
        assert result["broll"].endswith("broll/with_broll.mp4")
        assert result["sfx_placement"].endswith("sfx_placement/with_sfx.mp4")

        # The final deliverable still exists (downstream stages consumed
        # the chained base video without error).
        assert Path(result["final"]).exists()

    def test_sfx_placement_output_feeds_music(
        self,
        tmp_path: Path,
        ctx: JobContext,
        transcript_with_filler: Path,
        tmp_media: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When sfx_placement is on, the music mix input (sfx_input) is the
        sfx_placement output, not the captions video."""
        _install_optional_fakes(monkeypatch)

        pipeline = SkillsPipeline(ctx, run_sfx_placement=True)
        result = pipeline.run(tmp_media, transcript_path=transcript_with_filler)

        expected = ctx.work_dir / "sfx_placement" / "with_sfx.mp4"
        assert Path(result["sfx_input"]) == expected, (
            f"music should receive the sfx_placement output; got {result['sfx_input']!r}"
        )
        assert expected.exists()

    def test_transitions_and_reframe_rewrite_edl_to_single_segment(
        self,
        tmp_path: Path,
        ctx: JobContext,
        transcript_with_filler: Path,
        tmp_media: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Transitions and reframe change the base video timeline, so the
        EDL must be rewritten to a single full-duration segment over the
        composed video (so graphics/captions stay in sync)."""
        _install_optional_fakes(monkeypatch)

        pipeline = SkillsPipeline(
            ctx,
            run_transitions=True,
            run_reframe=True,
        )
        pipeline.run(tmp_media, transcript_path=transcript_with_filler)

        from video_editor_core.timeline import EDL

        edl_path = ctx.artifact("plan", "edl.json")
        assert edl_path.exists()
        edl = EDL(**json.loads(edl_path.read_text(encoding="utf-8")))
        assert len(edl.segments) == 1, (
            f"expected a single rewritten segment; got {len(edl.segments)}"
        )
        # The rewritten EDL points at the reframed video, the final base.
        assert edl.source_path.endswith("reframe/reframed.mp4")


class TestTemplateAwarePresetDefaulting:
    """raw_to_video templates (vlog_reel, talking_head_polish) carry a
    default preset (vlogger_youtube) that must win unless the user
    explicitly passes --preset. The CLI default is "short_explainer",
    which is treated as a "not set" sentinel for this pipeline kind."""

    def test_run_selects_template_preset_when_flag_omitted(
        self,
        tmp_path: Path,
        tmp_media: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``ve run <file> --template talking_head_polish`` (no --preset)
        constructs SkillsPipeline with the template's vlogger_youtube preset."""
        from typer.testing import CliRunner
        from video_editor_cli.main import app

        captured: dict[str, Any] = {}

        class _FakeSkillsPipeline:
            def __init__(self, ctx_arg: object, **kwargs: Any) -> None:
                captured.update(kwargs)
                self.render_options = type(
                    "RenderOptions", (), {"transition": "none", "transition_duration": 0.3}
                )()

            def run(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
                return {"final": str(tmp_path / "out.mp4")}

        monkeypatch.setattr("video_editor_cli.main.SkillsPipeline", _FakeSkillsPipeline)

        source = tmp_path / "talk.mp4"
        source.write_bytes(tmp_media.read_bytes())

        result = CliRunner().invoke(
            app,
            [
                "run",
                str(source),
                "-j",
                "vlog-preset-job",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
                "--template",
                "talking_head_polish",
            ],
        )
        assert result.exit_code == 0, result.output
        assert captured["preset"] == "vlogger_youtube", (
            f"template preset should win; got {captured.get('preset')!r}"
        )

    def test_run_explicit_preset_overrides_template(
        self,
        tmp_path: Path,
        tmp_media: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An explicit --preset beats the template default."""
        from typer.testing import CliRunner
        from video_editor_cli.main import app

        captured: dict[str, Any] = {}

        class _FakeSkillsPipeline:
            def __init__(self, ctx_arg: object, **kwargs: Any) -> None:
                captured.update(kwargs)
                self.render_options = type(
                    "RenderOptions", (), {"transition": "none", "transition_duration": 0.3}
                )()

            def run(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
                return {"final": str(tmp_path / "out.mp4")}

        monkeypatch.setattr("video_editor_cli.main.SkillsPipeline", _FakeSkillsPipeline)

        source = tmp_path / "talk.mp4"
        source.write_bytes(tmp_media.read_bytes())

        result = CliRunner().invoke(
            app,
            [
                "run",
                str(source),
                "-j",
                "vlog-preset-job",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
                "--template",
                "talking_head_polish",
                "--preset",
                "vlogger_shorts",
            ],
        )
        assert result.exit_code == 0, result.output
        assert captured["preset"] == "vlogger_shorts"

    def test_handler_selects_template_preset_for_raw_to_video(
        self,
        tmp_path: Path,
        tmp_media: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The worker handler applies the same template-aware default."""
        from video_editor_pipelines.handlers import _handle_run
        from video_editor_state.job import JobContext

        captured: dict[str, Any] = {}

        class _FakeSkillsPipeline:
            def __init__(self, ctx_arg: object, **kwargs: Any) -> None:
                captured.update(kwargs)
                self.render_options = type(
                    "RenderOptions", (), {"transition": "none", "transition_duration": 0.3}
                )()

            def run(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
                return {"final": str(tmp_path / "out.mp4")}

        monkeypatch.setattr("video_editor_pipelines.handlers.SkillsPipeline", _FakeSkillsPipeline)

        ctx = JobContext(
            job_id="handler-preset-job",
            work_root=tmp_path / "work",
            output_root=tmp_path / "output",
        )
        # C4 guard: the source must live inside the job's work_dir.
        source = ctx.work_dir / "talk.mp4"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(tmp_media.read_bytes())

        import asyncio

        asyncio.run(
            _handle_run(
                ctx,
                {
                    "source": str(source),
                    "template": "talking_head_polish",
                },
            )
        )
        assert captured["preset"] == "vlogger_youtube", (
            f"handler should use template preset; got {captured.get('preset')!r}"
        )


class TestResume:
    """Bug #78 regression: completed optional stages are skipped on re-run."""

    def test_optional_stages_resume_completed(
        self,
        tmp_path: Path,
        ctx: JobContext,
        transcript_with_filler: Path,
        tmp_media: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install_optional_fakes(monkeypatch)
        pipeline = SkillsPipeline(
            ctx,
            run_audio_cleanup=True,
            run_transitions=True,
            run_reframe=True,
            run_broll=True,
            run_sfx_placement=True,
        )
        first = pipeline.run(tmp_media, transcript_path=transcript_with_filler)
        assert Path(first["final"]).exists()

        # Second run: the optional stages are marked complete, so their
        # skills must NOT be invoked again.
        _CALLED.clear()
        _install_raising_fakes(monkeypatch)
        second = SkillsPipeline(
            ctx,
            run_audio_cleanup=True,
            run_transitions=True,
            run_reframe=True,
            run_broll=True,
            run_sfx_placement=True,
        ).run(tmp_media, transcript_path=transcript_with_filler)

        assert _CALLED == [], f"optional stages re-ran on resume: {_CALLED!r}"
        assert Path(second["final"]).exists()
