"""The whole short path, offline: stubs in, a gated MP4 out."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from video_editor_core.adapters.from_content_pack import pack_to_composition
from video_editor_core.composition import Canvas
from video_editor_core.content_pack import ContentPack, load_content_pack
from video_editor_pipelines import short_pipeline
from video_editor_pipelines.short_captions import caption_layers
from video_editor_pipelines.short_pipeline import (
    align_scenes,
    estimate_short,
    preflight_short,
    run_short_job,
)
from video_editor_state.job import JobContext

from tests.conftest import _require_ffmpeg

PACK_DIR = Path(__file__).parent / "fixtures" / "shorts" / "e2e-pack"


@pytest.fixture
def pack() -> ContentPack:
    return load_content_pack(PACK_DIR / "short.yaml")


@pytest.fixture
def transcript() -> dict[str, Any]:
    return json.loads((PACK_DIR / "transcript.json").read_text(encoding="utf-8"))


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    context = JobContext(
        job_id="short-e2e",
        work_root=tmp_path / "work",
        output_root=tmp_path / "out",
    )
    context.ensure_dirs()
    return context


@pytest.mark.slow
def test_the_pipeline_builds_a_gated_short(
    ctx: JobContext,
    pack: ContentPack,
    transcript: dict[str, Any],
    hyperframes_binary: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_ffmpeg()
    monkeypatch.setattr(
        short_pipeline,
        "_transcribe",
        lambda audio, job, config=None: transcript,
    )
    tokens = preflight_short(pack)
    estimate_short(ctx, pack, auto_approve=True)
    result = run_short_job(ctx, pack, tokens, cli_path=hyperframes_binary)

    assert result.final_path.is_file()
    report = json.loads(result.qa_path.read_text(encoding="utf-8"))
    checks = {check["name"]: check for check in report["checks"]}
    failures = {name: check["detail"] for name, check in checks.items() if not check["ok"]}
    # This fixture is deliberately offline and names stub providers, so the
    # #281 gate must catch it. Every measured check still passes; only the
    # placeholder-provider gate fails.
    assert not report["passed"]
    assert set(failures) == {"no_stub_assets"}, failures
    assert {
        "canvas",
        "loudness",
        "true_peak",
        "duration",
        "scene_starts",
        "captions",
        "stills",
        "silent_gaps",
        "tail",
        "cost",
        "cues",
        "no_stub_assets",
    } <= set(checks)
    assert report["inputs"]["duration"] == pytest.approx(4.77, abs=0.15)
    assert result.passed is False


def test_the_composition_keeps_captions_above_the_safe_edge(
    pack: ContentPack, transcript: dict[str, Any]
) -> None:
    tokens = preflight_short(pack)
    alignment = align_scenes(pack, audio_duration=3.56, transcript=transcript)
    canvas = Canvas(width=1080, height=1920, fps=30)
    captions = caption_layers(tokens, alignment, canvas=canvas)
    composition = pack_to_composition(
        pack, tokens, alignment, stills={"rat": Path("rat.png")}, captions=captions
    )

    assert composition.duration == pytest.approx(4.7667, abs=1e-3)
    assert [scene.id for scene in composition.scenes] == ["hook", "branch", "end_card"]
    layers = {layer.id: layer for scene in composition.scenes for layer in scene.layers}
    assert {"end_card_text", "end_card_url"} <= set(layers)
    # The branch scene starts where "Your" is spoken (1.35 s). ``quantise``
    # rounds to the 30 fps grid, so 1.35 s is frame 40 = 1.3333 s, not 1.3667.
    assert layers["still_hook"].time.end == pytest.approx(1.3333, abs=1e-3)
    caption_layers_only = [
        layer for layer_id, layer in layers.items() if layer_id.startswith("caption_")
    ]
    assert caption_layers_only, "the composition carries no caption layers"
    for layer in caption_layers_only:
        assert layer.rect.y + layer.rect.height <= 1536
        assert layer.preset == "caption_line"
