from __future__ import annotations

import asyncio
import dataclasses
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args

import pytest
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.pricing import PricingRegistry
from video_editor_core.models.scene_plan import Scene
from video_editor_core.templates.base import PipelineKind
from video_editor_core.templates.router import TemplateRouter
from video_editor_core.templates.yaml_templates import load_yaml_templates
from video_editor_pipelines.explainer_pipeline import (
    MUSIC_SECONDS_PER_SCENE,
    build_cost,
    cost_check_values,
    estimate_explainer,
    explainer_job_requests,
    prepare_explainer,
    price_requests,
    reconcile_final_tail,
)
from video_editor_pipelines.explainer_plan import build_explainer_plan, explainer_requests
from video_editor_pipelines.explainer_qa import QaCheck
from video_editor_pipelines.explainer_script import load_explainer_script
from video_editor_pipelines.explainer_timing import align_words, derive_scene_timings
from video_editor_state.job import JobContext


def test_explainer_stills_is_a_pipeline_kind():
    assert "explainer_stills" in get_args(PipelineKind)


def test_the_manifest_routes_to_the_new_kind(tmp_path):
    script = tmp_path / "script.md"
    script.write_text(
        "---\ntitle: T\n---\n\n## One\nvisual: still\nimage: x\nnarration: y\n",
        encoding="utf-8",
    )
    selected = TemplateRouter().select(source=script, template_name="explainer_stills")
    assert selected.pipeline_kind == "explainer_stills"
    assert selected.aspect_ratio == "9:16"
    assert selected.preset == "short_explainer"
    assert selected.providers["image"] == "poyo_ai"


def test_every_scene_type_hint_is_a_real_scene_type():
    templates = {template.name: template for template in load_yaml_templates()}
    for name in ("explainer_stills", "explainer_motion"):
        for hint in templates[name].scene_type_hints:
            assert Scene(id="probe", type=hint, duration_seconds=1.0).type == hint


# ---------------------------------------------------------------------------
# Request building: the gate and the run must buy the same list
# ---------------------------------------------------------------------------


def _write_script(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "script.md"
    path.write_text(body, encoding="utf-8")
    return path


TWO_SCENE_SCRIPT = """---
title: Two scenes
voice: narrator
---

## One
visual: still
image: a red rack in a data centre
subject: rack
narration: This is the first scene about a rack.

## Two
visual: still
image: the same red rack, now empty
subject: rack
narration: This is the second scene about the same rack.

## Three
visual: typographic
layout: big_stat
headline: $32B
narration: Thirty two billion dollars.
"""


def test_a_typographic_scene_never_produces_an_image_request(tmp_path):
    script = load_explainer_script(_write_script(tmp_path, TWO_SCENE_SCRIPT))
    plan, reuse = build_explainer_plan(script, "job")
    requests = explainer_requests(
        plan, script, "job", tts_provider="tts_router", image_provider="poyo_ai"
    )
    tts = [request for request in requests if request.asset_type == AssetType.TTS]
    images = [request for request in requests if request.asset_type == AssetType.IMAGE]
    assert [request.asset_id for request in tts] == ["job_narration"]
    assert tts[0].scene_id == "job"
    # Two still scenes share a subject, so one render serves both.
    assert len(images) == 1
    assert reuse.saved == 1
    assert all(request.scene_id != "scene_3" for request in images)


def test_the_job_requests_carry_one_narration_and_one_real_duration_music(tmp_path):
    script = load_explainer_script(_write_script(tmp_path, TWO_SCENE_SCRIPT))
    plan, _reuse = build_explainer_plan(script, "job")
    requests = explainer_job_requests(
        plan,
        script,
        "job",
        tts_provider="tts_router",
        image_provider="poyo_ai",
        music_provider="poyo_music",
    )
    kinds = [request.asset_type for request in requests]
    assert kinds.count(AssetType.TTS) == 1
    music = [request for request in requests if request.asset_type == AssetType.MUSIC]
    assert len(music) == 1
    # The pipeline buys one track long enough for the whole reel; the plan's
    # 1 s placeholders are not the length it will actually request.
    assert music[0].params["duration_seconds"] == len(plan["scenes"]) * MUSIC_SECONDS_PER_SCENE
    assert music[0].asset_id == "job_music"


def test_a_supplied_music_track_is_not_bought(tmp_path):
    script = load_explainer_script(_write_script(tmp_path, TWO_SCENE_SCRIPT))
    plan, _reuse = build_explainer_plan(script, "job")
    requests = explainer_job_requests(
        plan,
        script,
        "job",
        music_provider="poyo_music",
        music_path=tmp_path / "track.mp3",
    )
    assert all(request.asset_type != AssetType.MUSIC for request in requests)


# ---------------------------------------------------------------------------
# #268: the last scene must reach the end of the narration
# ---------------------------------------------------------------------------


def _aligned_words(narrations: list[str]) -> list[dict[str, object]]:
    recognised = []
    cursor = 0.0
    for narration in narrations:
        for word in narration.split():
            recognised.append({"word": word, "start": cursor, "end": cursor + 0.4})
            cursor += 0.5
    return align_words(narrations, recognised)


def test_the_final_scene_is_extended_when_it_falls_short_of_the_audio():
    narrations = ["One two three.", "Four five six."]
    words = _aligned_words(narrations)
    timings = derive_scene_timings(narrations, words, ids=["scene_1", "scene_2"])
    reconciled = reconcile_final_tail(timings, audio_end=8.0)
    assert reconciled[-1].end == 8.0
    # Earlier scenes never move.
    assert reconciled[:-1] == timings[:-1]


def test_the_final_tail_is_kept_when_measurement_is_good():
    narrations = ["One two three.", "Four five six."]
    words = _aligned_words(narrations)
    timings = derive_scene_timings(narrations, words, ids=["scene_1", "scene_2"])
    measured_end = timings[-1].end
    reconciled = reconcile_final_tail(timings, audio_end=measured_end - 0.5)
    assert reconciled[-1].end == measured_end


# ---------------------------------------------------------------------------
# The plan and its reuse report are written before anything is bought
# ---------------------------------------------------------------------------


def test_prepare_writes_the_plan_and_the_reuse_report(tmp_path):
    script = load_explainer_script(_write_script(tmp_path, TWO_SCENE_SCRIPT))
    ctx = JobContext(job_id="job", work_root=tmp_path / "work", output_root=tmp_path / "out")
    plan, reuse = prepare_explainer(ctx, script)
    plan_path = ctx.work_dir / "plan" / "scene_plan.json"
    report_path = ctx.work_dir / "plan" / "reuse_report.json"
    assert json.loads(plan_path.read_text(encoding="utf-8"))["scenes"][0]["id"] == "scene_1"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["still_scenes"] == 2
    assert report["renders"] == 1
    assert report["saved"] == 1
    assert reuse.renders == 1
    assert plan["design"] == script.design


# ---------------------------------------------------------------------------
# A worker payload cannot point the mixer at an arbitrary host file
# ---------------------------------------------------------------------------


def test_the_worker_rejects_a_music_path_outside_the_job_roots(tmp_path, monkeypatch):
    import video_editor_pipelines.explainer_pipeline as pipeline
    from video_editor_pipelines.handlers import _handle_run

    monkeypatch.setattr(pipeline, "hyperframes_available", lambda *a, **k: True)

    ctx = JobContext(job_id="job", work_root=tmp_path / "work", output_root=tmp_path / "out")
    source = ctx.work_dir / "script.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(TWO_SCENE_SCRIPT, encoding="utf-8")
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"")

    with pytest.raises(ValueError, match="music_path"):
        asyncio.run(
            _handle_run(
                ctx,
                {
                    "source": str(source),
                    "template": "explainer_stills",
                    "music_path": str(outside),
                },
            )
        )


# ---------------------------------------------------------------------------
# The worker cannot spend against an estimate its payload was never priced on
# ---------------------------------------------------------------------------


def _worker_payload(ctx: JobContext) -> dict[str, Any]:
    source = ctx.work_dir / "script.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(TWO_SCENE_SCRIPT, encoding="utf-8")
    return {"source": str(source), "template": "explainer_stills"}


def _stored_approval(ctx: JobContext, total: float) -> None:
    path = ctx.work_dir / "plan" / "cost_estimate.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"total_cost": total}), encoding="utf-8")


def test_the_worker_refuses_a_build_that_costs_more_than_its_approval(tmp_path, monkeypatch):
    import video_editor_pipelines.explainer_pipeline as pipeline
    from video_editor_pipelines.handlers import _handle_run

    monkeypatch.setattr(pipeline, "hyperframes_available", lambda *a, **k: True)

    ctx = JobContext(job_id="job", work_root=tmp_path / "work", output_root=tmp_path / "out")
    payload = _worker_payload(ctx)
    # e.g. an approval written for a different provider/font/no-reuse choice,
    # or by ``ve estimate`` (per-scene TTS, a 1 s music bed).
    _stored_approval(ctx, 0.0001)

    with pytest.raises(ValueError, match="approved estimate is \\$0\\.0001") as excinfo:
        asyncio.run(_handle_run(ctx, payload))
    # The refusal states both numbers.
    assert "this build costs $" in str(excinfo.value)


def test_the_worker_runs_when_the_approval_covers_the_build(tmp_path, monkeypatch):
    import video_editor_pipelines.explainer_pipeline as pipeline
    from video_editor_pipelines.handlers import _handle_run

    ran: list[str] = []

    def fake_run(ctx, script, plan, **kwargs):  # noqa: ANN001, ARG001
        ran.append(ctx.job_id)
        return SimpleNamespace(
            final_path=ctx.output_dir / "final.mp4",
            passed=True,
            qa_path=ctx.work_dir / "qa.json",
        )

    monkeypatch.setattr(pipeline, "run_explainer_job", fake_run)
    monkeypatch.setattr(pipeline, "hyperframes_available", lambda *a, **k: True)

    ctx = JobContext(job_id="job", work_root=tmp_path / "work", output_root=tmp_path / "out")
    payload = _worker_payload(ctx)
    _stored_approval(ctx, 1000.0)

    asyncio.run(_handle_run(ctx, payload))

    assert ran == ["job"]


def test_the_worker_runs_the_real_pipeline_inside_its_event_loop(tmp_path, monkeypatch):
    """The dispatcher awaits ``_handle_run`` while its event loop is running.

    ``run_explainer_job`` is blocking and bridges to the manager with
    ``asyncio.run``. Calling it on the loop thread raises ``RuntimeError:
    asyncio.run() cannot be called from a running event loop`` before any paid
    request, so every explainer job driven by ``ve worker`` failed. This drives
    the real handler with the **real** ``run_explainer_job`` — only the
    provider/network and render boundaries are stubbed — and asserts the job
    completes.
    """
    import video_editor_pipelines.explainer_pipeline as pipeline
    from video_editor_pipelines.explainer_qa import QaCheck
    from video_editor_pipelines.handlers import _handle_run

    ctx = JobContext(job_id="job", work_root=tmp_path / "work", output_root=tmp_path / "out")
    script = load_explainer_script(_write_script(tmp_path, TWO_SCENE_SCRIPT))
    recorded = _patch_fake_pipeline(
        monkeypatch,
        ctx,
        raw_words=_raw_transcript_words([scene.narration for scene in script.scenes]),
    )
    # The QA gate is not what this test measures, and the fake measurements
    # (a 6 s file) cannot meet the format's 30 s floor.
    monkeypatch.setattr(
        pipeline,
        "evaluate_qa",
        lambda inputs, extra=None: [QaCheck(name="stub", ok=True, detail="")],
    )
    _stored_approval(ctx, 1000.0)

    asyncio.run(_handle_run(ctx, _worker_payload(ctx)))

    assert recorded["render_plan"].exists()
    assert (ctx.output_dir / "final.mp4").exists()


# ---------------------------------------------------------------------------
# The QA gate fails closed when the cost was not measured
# ---------------------------------------------------------------------------


def test_a_build_without_an_approved_estimate_fails_the_cost_check(tmp_path):
    cost, estimate = cost_check_values(tmp_path / "missing.json", spent=0.0)
    assert cost > estimate


def test_the_cost_check_compares_the_approved_estimate_with_the_build(tmp_path):
    estimate_path = tmp_path / "cost_estimate.json"
    estimate_path.write_text(json.dumps({"total_cost": 0.2}), encoding="utf-8")
    assert cost_check_values(estimate_path, spent=0.15) == (0.15, 0.2)
    cost, estimate = cost_check_values(estimate_path, spent=None)
    assert cost > estimate


# ---------------------------------------------------------------------------
# The QA cost is measured from what the provider results report, not a re-price
# ---------------------------------------------------------------------------


def _result(
    asset_id: str, asset_type: AssetType, *, duration_ms: int = 0, **metadata: Any
) -> AssetResult:
    return AssetResult(
        asset_id=asset_id,
        asset_type=asset_type,
        local_path=f"/tmp/{asset_id}",
        scene_id="job",
        duration_ms=duration_ms,
        metadata=dict(metadata),
    )


def _request(
    asset_id: str, asset_type: AssetType, prompt: str, provider: str = "tts_router"
) -> AssetRequest:
    return AssetRequest(
        asset_id=asset_id,
        job_id="job",
        scene_id="job",
        asset_type=asset_type,
        prompt=prompt,
        provider=provider,
    )


def test_the_build_cost_is_measured_from_the_quantities_the_results_report():
    results = [
        _result(
            "job_narration",
            AssetType.TTS,
            provider="elevenlabs_tts",
            model="eleven_flash_v2_5",
            text_length=2000,
        ),
        _result("still_1", AssetType.IMAGE, provider="poyo_ai", model="gpt-image-2"),
        _result(
            "job_music",
            AssetType.MUSIC,
            provider="poyo_music",
            model="minimax-music-2.6",
            duration_seconds=36.0,
        ),
    ]
    # 2000 billed characters on Flash = 2 blocks x $0.04; one PoYo still at its
    # base $0.01; minimax music bills $0.10 per generation. The requests are
    # deliberately absent: a re-price would have nothing to price.
    assert build_cost(results, []) == pytest.approx(0.19)


def test_tts_characters_come_from_the_text_the_provider_synthesised():
    # ElevenLabs and Sarvam do not report ``text_length``; the quantity billed
    # is the text actually sent, while the price row is the provider and model
    # the result says served it.
    narration = _request("job_narration", AssetType.TTS, "word " * 300)
    result = _result(
        "job_narration",
        AssetType.TTS,
        provider="elevenlabs_tts",
        model="eleven_flash_v2_5",
    )
    # 1,500 characters on Flash at $0.04/1k, billed by the character.
    assert build_cost([result], [narration]) == pytest.approx(0.06)


def test_music_cost_is_measured_from_the_generated_duration():
    # ElevenLabs Music bills per second and reports no model, so both the
    # generated length and the configured default model come from the result.
    music = _result("job_music", AssetType.MUSIC, provider="elevenlabs_music", duration_ms=12000)
    assert build_cost([music], []) == pytest.approx(0.03)


def test_the_qa_cost_uses_a_fully_reported_bill_when_every_result_has_one():
    results = [
        _result("job_narration", AssetType.TTS, cost=0.31),
        _result("job_music", AssetType.MUSIC, cost=0.10),
    ]
    assert build_cost(results, []) == pytest.approx(0.41)


def test_a_partly_reported_cost_is_completed_from_the_registry():
    results = [
        _result("job_narration", AssetType.TTS, cost=0.31),
        _result("still_1", AssetType.IMAGE, provider="poyo_ai", model="gpt-image-2"),
    ]
    # One provider reported its exact bill; the other reports no cost but does
    # report what it made. Pricing the gap keeps the total a measurement of the
    # whole build instead of a subset — and instead of discarding the bill.
    assert build_cost(results, []) == pytest.approx(0.32)


def test_a_free_result_does_not_make_the_rest_of_the_build_unmeasured():
    # agy_image reports ``cost: 0.0``; Sarvam reports no bill but does expose
    # the characters it synthesized. Before this, the single 0.0 made the build
    # "partly reported" and the QA cost check failed with an infinite cost.
    results = [
        _result("job_narration", AssetType.TTS, provider="sarvam_tts", text_length=1000),
        _result("still_1", AssetType.IMAGE, provider="agy_image", cost=0.0),
    ]
    narration = _request("job_narration", AssetType.TTS, "x" * 1000)

    cost = build_cost(results, [narration])

    assert cost is not None
    assert math.isfinite(cost)
    assert cost > 0.0


def test_no_results_at_all_fails_the_cost_check(tmp_path):
    estimate_path = tmp_path / "cost_estimate.json"
    estimate_path.write_text(json.dumps({"total_cost": 0.2}), encoding="utf-8")
    cost, estimate = cost_check_values(estimate_path, build_cost([], []))
    assert cost > estimate


def test_an_unpriceable_result_reports_no_cost():
    # The result names a provider the registry has no row for and reports no
    # bill: there is no billable quantity to price.
    result = _result("job_narration", AssetType.TTS, provider="mystery_tts")
    assert build_cost([result], [_request("job_narration", AssetType.TTS, "words")]) is None


# ---------------------------------------------------------------------------
# tts_router is priced as the provider it will deterministically route to
# ---------------------------------------------------------------------------


def _voice_script(tmp_path: Path, voice: str, language: str = "") -> Path:
    language_line = f"voice_language: {language}\n" if language else ""
    body = (
        "---\n"
        "title: Voice\n"
        f"voice: {voice}\n"
        f"{language_line}"
        "---\n\n"
        "## One\n"
        "visual: typographic\n"
        "layout: big_stat\n"
        "headline: 42\n"
        "narration: One short spoken line.\n"
    )
    return _write_script(tmp_path, body)


@pytest.mark.parametrize(
    ("voice", "language", "expected_provider", "expect_cost"),
    [
        ("elevenlabs:Brian", "", "elevenlabs_tts", True),
        ("sarvam:meera", "hi-IN", "sarvam_tts", True),
        ("narrator", "", "elevenlabs_tts", True),  # unprefixed voices go to ElevenLabs (2026-10-30)
    ],
)
def test_tts_router_narration_prices_its_routed_provider(
    tmp_path: Path,
    voice: str,
    language: str,
    expected_provider: str,
    expect_cost: bool,
):
    script = load_explainer_script(_voice_script(tmp_path, voice, language))
    plan, _reuse = build_explainer_plan(script, "job")
    requests = explainer_job_requests(
        plan, script, "job", tts_provider="tts_router", music_provider=""
    )
    items = price_requests(requests, PricingRegistry.load())
    assert len(items) == 1
    assert items[0]["task_type"] == "tts"
    assert items[0]["provider"] == expected_provider
    if expect_cost:
        assert items[0]["total_cost"] > 0.0
    else:
        # Kokoro is local and genuinely free, so $0.00 here is a real price.
        assert items[0]["total_cost"] == 0.0


def test_auto_image_provider_prices_the_first_paid_fallback(tmp_path):
    """``--image-provider auto`` walks the chain; the estimate must still price it."""
    script = load_explainer_script(_write_script(tmp_path, TWO_SCENE_SCRIPT))
    plan, _reuse = build_explainer_plan(script, "job")
    requests = explainer_job_requests(
        plan,
        script,
        "job",
        tts_provider="tts_router",
        image_provider="auto",
        music_provider="",
    )

    items = price_requests(requests, PricingRegistry.load())

    images = [item for item in items if item["task_type"] == "image"]
    assert len(images) == 1
    assert images[0]["provider"] == "poyo_ai"
    assert images[0]["total_cost"] > 0.0


def test_auto_image_provider_prices_the_first_paid_chain_member(tmp_path):
    """A configured chain must be priced at its first *paid* member."""
    script = load_explainer_script(_write_script(tmp_path, TWO_SCENE_SCRIPT))
    plan, _reuse = build_explainer_plan(script, "job")
    requests = explainer_job_requests(
        plan,
        script,
        "job",
        tts_provider="tts_router",
        image_provider="auto",
        music_provider="",
    )

    items = price_requests(
        requests,
        PricingRegistry.load(),
        image_chain=["agy_image", "fal_ai", "poyo_ai"],
    )

    images = [item for item in items if item["task_type"] == "image"]
    assert len(images) == 1
    # agy_image is quota-based and unpriced, so fal_ai is the first billed one.
    assert images[0]["provider"] == "fal_ai"
    assert images[0]["total_cost"] > 0.0


def test_auto_image_provider_keeps_the_default_when_the_chain_is_unpriced(tmp_path):
    """A chain with no paid member keeps the historical ``poyo_ai`` price."""
    script = load_explainer_script(_write_script(tmp_path, TWO_SCENE_SCRIPT))
    plan, _reuse = build_explainer_plan(script, "job")
    requests = explainer_job_requests(
        plan,
        script,
        "job",
        tts_provider="tts_router",
        image_provider="auto",
        music_provider="",
    )

    items = price_requests(requests, PricingRegistry.load(), image_chain=["agy_image"])

    images = [item for item in items if item["task_type"] == "image"]
    assert len(images) == 1
    assert images[0]["provider"] == "poyo_ai"


def test_an_unpriced_paid_request_refuses_an_estimate(tmp_path):
    script = load_explainer_script(_write_script(tmp_path, TWO_SCENE_SCRIPT))
    ctx = JobContext(job_id="job", work_root=tmp_path / "work", output_root=tmp_path / "out")
    plan, _reuse = build_explainer_plan(script, "job")
    from video_editor_pipelines.estimate_ui import EstimateError

    with pytest.raises(EstimateError, match="mystery_ai"):
        estimate_explainer(
            ctx,
            script,
            plan,
            image_provider="mystery_ai",
            music_provider="",
            auto_approve=True,
        )


# ---------------------------------------------------------------------------
# The render never sees the music bed; the ducking mix owns it
# ---------------------------------------------------------------------------


def _raw_transcript_words(
    narrations: list[str], *, gap: float = 1.6, gap_after: int = 1
) -> list[dict[str, Any]]:
    """Whisper-style words that keep real inter-word silence.

    Unlike ``align_words`` output, these do not force every word's end onto
    the next word's start, so the gap below is a real observation.
    """
    words: list[dict[str, Any]] = []
    cursor = 0.0
    for narration in narrations:
        for raw in narration.split():
            start, end = cursor, cursor + 0.4
            words.append({"word": raw, "start": round(start, 3), "end": round(end, 3)})
            cursor = end + (gap if len(words) - 1 == gap_after else 0.1)
    return words


def _patch_fake_pipeline(
    monkeypatch,
    ctx: JobContext,
    *,
    raw_words: list[dict[str, Any]],
    composition: bool = True,
) -> dict[str, Any]:
    """Stub the provider/network and render boundaries of the real pipeline.

    ``run_explainer_job`` itself stays real: this patches only the manager
    (provider/network), the renderer, the mixer, the captioner, the normaliser,
    the probe and the transcriber. The fake render writes no composition
    project, so by default the composition directory and its gate are stubbed
    too; pass ``composition=False`` to exercise the fail-closed branch.
    """
    import video_editor_pipelines.explainer_pipeline as pipeline
    from video_editor_transcribe import whisper_local

    narration_file = ctx.work_dir / "fake_narration.mp3"
    narration_file.parent.mkdir(parents=True, exist_ok=True)
    narration_file.write_bytes(b"")
    still_file = ctx.work_dir / "fake_still.png"
    still_file.write_bytes(b"")
    recorded: dict[str, Any] = {}
    if composition:
        (ctx.work_dir / "composition").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        pipeline,
        "gather_composition_qa",
        lambda **kwargs: [QaCheck("composition_gates", True, "stubbed")],
    )

    class FakeManager:
        def __init__(self, job_ctx, config=None, final=False):
            self.job_ctx = job_ctx

        async def resolve(self, requests):
            results = [
                AssetResult(
                    asset_id="job_narration",
                    asset_type=AssetType.TTS,
                    local_path=str(narration_file),
                    scene_id="job",
                )
            ]
            results.extend(
                AssetResult(
                    asset_id=request.asset_id,
                    asset_type=request.asset_type,
                    local_path=str(still_file),
                    scene_id=request.scene_id,
                )
                for request in requests
                if request.asset_type == AssetType.IMAGE
            )
            return results

    class FakeConfig:
        @classmethod
        def load(cls, path=None):
            return cls()

    class FakeSettings:
        @classmethod
        def load(cls, path=None):
            return SimpleNamespace(config=SimpleNamespace(assets=FakeConfig()))

    def fake_render(plan_path, output_path, project_dir, **kwargs):
        recorded["render_plan"] = Path(plan_path)
        Path(output_path).write_bytes(b"")
        return Path(output_path)

    def fake_mix(video, music, output, **kwargs):
        recorded["mixed_music"] = str(music)
        Path(output).write_bytes(b"")
        return Path(output)

    def fake_captions(video, words, output, **kwargs):
        Path(output).write_bytes(b"")
        return Path(output)

    def fake_normalize(video, output):
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_bytes(b"")
        return Path(output)

    def fake_probe(path):
        return SimpleNamespace(
            duration_ms=lambda: 6000, fps=30.0, duration=6.0, width=1080, height=1920
        )

    def fake_transcribe(audio_path, output_path, **kwargs):
        recorded["initial_prompt"] = kwargs.get("initial_prompt", "<absent>")
        return {"words": raw_words}

    def fake_settings(config_path=None):
        return SimpleNamespace(model="tiny", language="en", device="cpu", compute_type="int8")

    monkeypatch.setattr(pipeline, "AssetIngestionManager", FakeManager)
    monkeypatch.setattr(pipeline, "Settings", FakeSettings)
    monkeypatch.setattr(pipeline, "hyperframes_available", lambda *a, **k: True)
    monkeypatch.setattr(pipeline, "render_scene_plan", fake_render)
    monkeypatch.setattr(pipeline, "mix_background_music", fake_mix)
    monkeypatch.setattr(pipeline, "burn_captions", fake_captions)
    monkeypatch.setattr(pipeline, "normalize_loudness", fake_normalize)
    monkeypatch.setattr(pipeline, "probe_media", fake_probe)
    monkeypatch.setattr(pipeline, "resolve_transcription_settings", fake_settings)
    monkeypatch.setattr(pipeline, "measure_loudness", lambda path: (-14.0, -1.0))
    monkeypatch.setattr(whisper_local, "transcribe_audio", fake_transcribe)
    return recorded


def _run_fake_job(
    monkeypatch,
    ctx: JobContext,
    script,
    plan,
    *,
    raw_words: list[dict[str, Any]],
    music_path: Path | None,
    composition: bool = True,
) -> dict[str, Any]:
    """Run the pipeline with every render/mix/measure step replaced.

    Only the orchestration is under test: the plan the renderer receives and
    the arguments the mixer receives.
    """
    import video_editor_pipelines.explainer_pipeline as pipeline

    recorded = _patch_fake_pipeline(monkeypatch, ctx, raw_words=raw_words, composition=composition)

    pipeline.run_explainer_job(
        ctx,
        script,
        plan,
        music_provider="",
        music_path=music_path,
        caption_preset="karaoke",
    )
    return recorded


def test_the_render_plan_carries_no_music_so_the_bed_is_mixed_once(tmp_path: Path, monkeypatch):
    script = load_explainer_script(_write_script(tmp_path, TWO_SCENE_SCRIPT))
    ctx = JobContext(job_id="job", work_root=tmp_path / "work", output_root=tmp_path / "out")
    plan, _reuse = prepare_explainer(ctx, script)
    track = tmp_path / "bed.mp3"
    track.write_bytes(b"not really audio")

    recorded = _run_fake_job(
        monkeypatch,
        ctx,
        script,
        plan,
        raw_words=_raw_transcript_words([scene.narration for scene in script.scenes]),
        music_path=track,
    )

    render_plan = json.loads(recorded["render_plan"].read_text(encoding="utf-8"))
    assert not render_plan.get("music_path")
    # The path stays in the plan on disk as metadata ...
    on_disk = json.loads((ctx.work_dir / "plan" / "scene_plan.json").read_text(encoding="utf-8"))
    assert on_disk["music_path"] == str(track)
    # ... and the ducking mix is the only consumer of the supplied track.
    assert recorded["mixed_music"] == str(track)


def test_the_qa_canvas_uses_the_probed_dimensions(tmp_path: Path, monkeypatch):
    script = load_explainer_script(_write_script(tmp_path, TWO_SCENE_SCRIPT))
    ctx = JobContext(job_id="job", work_root=tmp_path / "work", output_root=tmp_path / "out")
    plan, _reuse = prepare_explainer(ctx, script)
    track = tmp_path / "bed.mp3"
    track.write_bytes(b"")

    _run_fake_job(
        monkeypatch,
        ctx,
        script,
        plan,
        raw_words=_raw_transcript_words([scene.narration for scene in script.scenes]),
        music_path=track,
    )

    qa = json.loads((ctx.work_dir / "qa.json").read_text(encoding="utf-8"))
    assert (qa["inputs"]["width"], qa["inputs"]["height"]) == (1080, 1920)
    checks = {check["name"]: check for check in qa["checks"]}
    assert checks["canvas"]["ok"]


def test_a_missing_composition_project_fails_the_qa_gate(tmp_path: Path, monkeypatch):
    script = load_explainer_script(_write_script(tmp_path, TWO_SCENE_SCRIPT))
    ctx = JobContext(job_id="job", work_root=tmp_path / "work", output_root=tmp_path / "out")
    plan, _reuse = prepare_explainer(ctx, script)

    _run_fake_job(
        monkeypatch,
        ctx,
        script,
        plan,
        raw_words=_raw_transcript_words([scene.narration for scene in script.scenes]),
        music_path=None,
        composition=False,
    )

    qa = json.loads((ctx.work_dir / "qa.json").read_text(encoding="utf-8"))
    checks = {check["name"]: check for check in qa["checks"]}
    assert checks["composition_gates"]["ok"] is False
    assert checks["composition_gates"]["detail"] == "no composition project to gate"
    assert qa["passed"] is False


def test_the_whisper_prompt_is_the_script_title_not_the_narration_opening(
    tmp_path: Path, monkeypatch
):
    """A leading transcript makes faster-whisper skip the opening seconds.

    ``initial_prompt`` must therefore be the script title, never the narration
    it is about to transcribe (the opening of the audio).
    """
    script = load_explainer_script(_write_script(tmp_path, TWO_SCENE_SCRIPT))
    ctx = JobContext(job_id="job", work_root=tmp_path / "work", output_root=tmp_path / "out")
    plan, _reuse = prepare_explainer(ctx, script)

    recorded = _run_fake_job(
        monkeypatch,
        ctx,
        script,
        plan,
        raw_words=_raw_transcript_words([scene.narration for scene in script.scenes]),
        music_path=None,
    )

    assert recorded["initial_prompt"] == "Two scenes"
    assert recorded["initial_prompt"] != script.narration[:200]


def test_an_empty_title_leaves_the_whisper_prompt_unset(tmp_path: Path, monkeypatch):
    script = dataclasses.replace(
        load_explainer_script(_write_script(tmp_path, TWO_SCENE_SCRIPT)), title=""
    )
    ctx = JobContext(job_id="job", work_root=tmp_path / "work", output_root=tmp_path / "out")
    plan, _reuse = prepare_explainer(ctx, script)

    recorded = _run_fake_job(
        monkeypatch,
        ctx,
        script,
        plan,
        raw_words=_raw_transcript_words([scene.narration for scene in script.scenes]),
        music_path=None,
    )

    assert recorded["initial_prompt"] is None


def test_silent_gaps_are_measured_on_the_raw_transcript(tmp_path: Path, monkeypatch):
    script = load_explainer_script(_write_script(tmp_path, TWO_SCENE_SCRIPT))
    ctx = JobContext(job_id="job", work_root=tmp_path / "work", output_root=tmp_path / "out")
    plan, _reuse = prepare_explainer(ctx, script)
    track = tmp_path / "bed.mp3"
    track.write_bytes(b"")
    raw_words = _raw_transcript_words([scene.narration for scene in script.scenes], gap=1.6)

    _run_fake_job(
        monkeypatch,
        ctx,
        script,
        plan,
        raw_words=raw_words,
        music_path=track,
    )

    qa = json.loads((ctx.work_dir / "qa.json").read_text(encoding="utf-8"))
    assert qa["inputs"]["max_silent_gap"] == pytest.approx(1.6)
    checks = {check["name"]: check for check in qa["checks"]}
    assert not checks["silent_gaps"]["ok"]


# ---------------------------------------------------------------------------
# The run payload schema declares every explainer flag the branch reads
# ---------------------------------------------------------------------------


def test_the_run_payload_declares_the_explainer_flags():
    from video_editor_cli.payloads import RunPayload

    payload = RunPayload.model_validate(
        {
            "source": "script.md",
            "template": "explainer_stills",
            "no_reuse": True,
            "auto_approve": True,
            "tts_provider": "sarvam_tts",
            "image_provider": "poyo_ai",
            "caption_preset": "karaoke",
        }
    )
    assert payload.no_reuse is True
    assert payload.auto_approve is True
    assert payload.tts_provider == "sarvam_tts"
    assert payload.image_provider == "poyo_ai"
    assert payload.caption_preset == "karaoke"


def test_the_run_payload_defaults_match_the_explainer_branch():
    from video_editor_cli.payloads import RunPayload

    payload = RunPayload.model_validate({"source": "script.md", "template": "explainer_stills"})
    assert payload.auto_approve is False
    assert payload.no_reuse is False
    assert payload.tts_provider == "tts_router"
    assert payload.image_provider == "auto"
    assert payload.caption_preset == ""
    assert payload.music_provider == "poyo_music"


def test_run_payloads_never_default_to_elevenlabs_music():
    """ElevenLabs is voice-over only (2026-10-30), so no payload defaults to its music."""
    from video_editor_cli.payloads import RunPayload

    payload = RunPayload.model_validate({"source": "clip.mp4"})
    assert payload.music_provider == "poyo_music"


# ---------------------------------------------------------------------------
# `ve run` refuses explainer_stills; `ve explain` is the entry point
# ---------------------------------------------------------------------------


def test_run_refuses_explainer_stills_and_points_at_ve_explain(tmp_path, monkeypatch):
    import video_editor_cli.main as main
    from typer.testing import CliRunner

    def _skills_pipeline_must_not_run(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        raise AssertionError("ve run must not route explainer_stills into SkillsPipeline")

    monkeypatch.setattr(main, "SkillsPipeline", _skills_pipeline_must_not_run)
    source = _write_script(tmp_path, TWO_SCENE_SCRIPT)

    result = CliRunner().invoke(
        main.app,
        [
            "run",
            str(source),
            "--template",
            "explainer_stills",
            "--job-id",
            "job",
            "--work-root",
            str(tmp_path / "work"),
            "--output-root",
            str(tmp_path / "out"),
        ],
    )

    output = result.output + result.stderr
    assert result.exit_code == 2
    assert "ve explain" in output
    assert "script.md" in output


# ---------------------------------------------------------------------------
# Free prerequisites fail before pricing or spending
# ---------------------------------------------------------------------------

BAD_DESIGN_SCRIPT = """---
title: Two scenes
voice: narrator
design: no_such_design
---

## One
visual: still
image: a red rack in a data centre
subject: rack
narration: This is the first scene about a rack.

## Two
visual: typographic
layout: big_stat
headline: $32B
narration: Thirty two billion dollars.
"""


def _preflight_worker(tmp_path):
    return JobContext(job_id="job", work_root=tmp_path / "work", output_root=tmp_path / "out")


def _write_worker_script(ctx: JobContext, body: str) -> Path:
    source = ctx.work_dir / "script.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(body, encoding="utf-8")
    return source


def test_preflight_rejects_a_bad_design_before_the_manager_runs(tmp_path, monkeypatch):
    import video_editor_pipelines.explainer_pipeline as pipeline
    from video_editor_pipelines.handlers import _handle_run

    constructed: list[str] = []

    class FakeManager:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003, ARG002
            constructed.append("manager")

    monkeypatch.setattr(pipeline, "AssetIngestionManager", FakeManager)
    monkeypatch.setattr(pipeline, "hyperframes_available", lambda *a, **k: True)
    ctx = _preflight_worker(tmp_path)
    source = _write_worker_script(ctx, BAD_DESIGN_SCRIPT)

    with pytest.raises(pipeline.ExplainerPreflightError, match="design check failed"):
        asyncio.run(_handle_run(ctx, {"source": str(source), "template": "explainer_stills"}))

    assert constructed == []


def test_preflight_rejects_an_unknown_caption_preset_before_the_manager_runs(tmp_path, monkeypatch):
    import video_editor_pipelines.explainer_pipeline as pipeline
    from video_editor_pipelines.handlers import _handle_run

    constructed: list[str] = []

    class FakeManager:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003, ARG002
            constructed.append("manager")

    monkeypatch.setattr(pipeline, "AssetIngestionManager", FakeManager)
    monkeypatch.setattr(pipeline, "hyperframes_available", lambda *a, **k: True)
    ctx = _preflight_worker(tmp_path)
    source = _write_worker_script(ctx, TWO_SCENE_SCRIPT)

    with pytest.raises(pipeline.ExplainerPreflightError, match="caption preset check failed"):
        asyncio.run(
            _handle_run(
                ctx,
                {
                    "source": str(source),
                    "template": "explainer_stills",
                    "caption_preset": "not_a_preset",
                },
            )
        )

    assert constructed == []


def test_a_valid_script_passes_preflight_and_the_worker_proceeds(tmp_path, monkeypatch):
    import video_editor_pipelines.explainer_pipeline as pipeline
    from video_editor_pipelines.handlers import _handle_run

    ran: list[str] = []

    def fake_run(ctx, script, plan, **kwargs):  # noqa: ANN001, ANN003, ARG001
        ran.append(ctx.job_id)
        return SimpleNamespace(
            final_path=ctx.output_dir / "final.mp4",
            passed=True,
            qa_path=ctx.work_dir / "qa.json",
        )

    monkeypatch.setattr(pipeline, "run_explainer_job", fake_run)
    monkeypatch.setattr(pipeline, "hyperframes_available", lambda *a, **k: True)
    ctx = _preflight_worker(tmp_path)
    source = _write_worker_script(ctx, TWO_SCENE_SCRIPT)

    # A direct preflight of a valid script and preset is a no-op.
    script = load_explainer_script(source)
    pipeline.preflight_explainer(script)
    pipeline.preflight_explainer(script, caption_preset="karaoke")

    _stored_approval(ctx, 1000.0)
    asyncio.run(_handle_run(ctx, {"source": str(source), "template": "explainer_stills"}))

    assert ran == ["job"]


def test_preflight_requires_hyperframes_before_the_manager_runs(tmp_path, monkeypatch):
    import video_editor_pipelines.explainer_pipeline as pipeline
    from video_editor_pipelines.handlers import _handle_run

    constructed: list[str] = []

    class FakeManager:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003, ARG002
            constructed.append("manager")

    monkeypatch.setattr(pipeline, "AssetIngestionManager", FakeManager)
    monkeypatch.setattr(pipeline, "hyperframes_available", lambda *a, **k: False)
    ctx = _preflight_worker(tmp_path)
    source = _write_worker_script(ctx, TWO_SCENE_SCRIPT)

    with pytest.raises(pipeline.ExplainerPreflightError, match="HyperFrames check failed"):
        asyncio.run(_handle_run(ctx, {"source": str(source), "template": "explainer_stills"}))

    assert constructed == []


def test_the_cli_reports_a_preflight_failure_before_pricing(tmp_path, monkeypatch):
    import video_editor_cli.main as main
    import video_editor_pipelines.explainer_pipeline as pipeline
    from typer.testing import CliRunner

    priced: list[str] = []

    def _estimate_must_not_run(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        priced.append("estimate")

    monkeypatch.setattr(pipeline, "estimate_explainer", _estimate_must_not_run)
    source = _write_script(tmp_path, BAD_DESIGN_SCRIPT)

    result = CliRunner().invoke(
        main.app,
        [
            "explain",
            str(source),
            "--job-id",
            "job",
            "--work-root",
            str(tmp_path / "work"),
            "--output-root",
            str(tmp_path / "out"),
        ],
    )

    output = result.output + result.stderr
    assert result.exit_code == 1
    assert "Explainer preflight failed" in output
    assert "design check failed" in output
    assert priced == []


def test_sfx_cues_are_priced_with_the_job_and_one_lands_per_scene(tmp_path):
    """The SFX clips are part of the priced list, not a surprise after it.

    Freesound prices at zero, but the gate still has to see the requests: a
    later stage that fetches clips nobody priced is exactly what the
    fail-closed cost gate exists to prevent.
    """
    script = load_explainer_script(_write_script(tmp_path, TWO_SCENE_SCRIPT))
    plan, _reuse = build_explainer_plan(script, "job")

    plain = explainer_job_requests(plan, script, "job", music_provider="")
    cued = explainer_job_requests(
        plan, script, "job", music_provider="", sfx_provider="freesound_sfx"
    )

    added = [request for request in cued if request.asset_type == AssetType.SFX]
    assert len(added) == len(plan["scenes"])
    assert len(cued) == len(plain) + len(plan["scenes"])
    # The two stills share a rack prompt; their cues still differ by type.
    assert added[0].prompt
    assert added[-1].prompt == "soft impact"  # the typographic card


def test_each_sfx_cue_lands_on_its_scene_cut(tmp_path):
    from video_editor_pipelines.explainer_pipeline import scene_sfx_timeline

    plan = {
        "scenes": [
            {"id": "scene_1", "duration_seconds": 4.0},
            {"id": "scene_2", "duration_seconds": 3.5},
            {"id": "scene_3", "duration_seconds": 5.25},
        ]
    }
    clip = tmp_path / "cue.mp3"
    clip.write_bytes(b"")

    timeline = scene_sfx_timeline(plan, {"scene_1": clip, "scene_3": clip})

    # scene_2 has no clip but still advances the cursor: 4.0 + 3.5.
    assert timeline == [(clip, 0.0), (clip, 7.5)]
