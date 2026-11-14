from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from video_editor_asset_ingestion.models import AssetType
from video_editor_core.content_pack import ContentPack, PackLine
from video_editor_core.designs import DesignTokens
from video_editor_pipelines.short_pipeline import (
    ShortPreflightError,
    estimate_short,
    preflight_short,
    short_job_requests,
)
from video_editor_state.job import JobContext

PACK = {
    "job_id": "demo",
    "design": "saju_series",
    "voice": {
        "provider": "sarvam_tts",
        "voice_id": "sarvam:sarang_en_narration",
        "model": "bulbul:v4-flash",
        "language": "en-IN",
        "gap_seconds": 0.28,
    },
    "image": {
        "provider": "poyo_ai",
        "model": "gpt-image-2.5-fixed",
        "resolution": "2K",
        "style_suffix": "ink wash, no text",
    },
    "scenes": [
        {
            "id": "hook",
            "still": {"key": "rat", "prompt": "a rat on a branch"},
            "lines": [{"say": "You think you're a Rat?", "show": "Not just a Rat"}],
        },
        {
            "id": "branch",
            "still": {"reuse": "rat"},
            "lines": [{"say": "Your animal is the year branch."}],
        },
    ],
    "end_card": {"say": "Find yours free at cosmicsaju.com.", "text": "Free chart snapshot"},
}


def make_pack() -> ContentPack:
    return ContentPack.model_validate(PACK)


def make_ctx(tmp_path: Path) -> JobContext:
    ctx = JobContext(job_id="demo", work_root=tmp_path / "work", output_root=tmp_path / "out")
    ctx.ensure_dirs()
    return ctx


def test_requests_are_one_image_per_still_and_one_voice_line_each() -> None:
    requests = short_job_requests(make_pack())
    images = [r for r in requests if r.asset_type == AssetType.IMAGE]
    tts = [r for r in requests if r.asset_type == AssetType.TTS]
    assert [r.asset_id for r in images] == ["still_rat"]
    assert [r.asset_id for r in tts] == [
        "line_hook_0",
        "line_branch_0",
        "line_end_card_0",
    ]
    assert images[0].params == {
        "model": "gpt-image-2.5-fixed",
        "model_kwargs": {"resolution": "2K"},
    }
    assert tts[0].params == {
        "voice_id": "sarvam:sarang_en_narration",
        "model": "bulbul:v4-flash",
        "language": "en-IN",
    }


def test_the_style_suffix_is_appended_to_every_still_prompt() -> None:
    image = short_job_requests(make_pack())[0]
    assert image.prompt == "a rat on a branch. ink wash, no text"


def test_auto_image_requests_carry_the_canvas_for_agy() -> None:
    """agy (and a poyo fallback) derive the aspect directive from these."""
    pack = make_pack()
    pack.image.provider = "auto"
    image = short_job_requests(pack)[0]
    assert image.params["width"] == 1080
    assert image.params["height"] == 1920


def test_pinned_poyo_requests_keep_their_old_cache_keys() -> None:
    """Only ``auto``/agy gain the dimensions, so shipped poyo caches hold."""
    image = short_job_requests(make_pack())[0]
    assert "width" not in image.params
    assert "height" not in image.params


def test_preflight_returns_the_design_it_validated() -> None:
    tokens = preflight_short(make_pack())
    assert tokens.name == "saju_series"


def test_preflight_rejects_an_unknown_design() -> None:
    pack = make_pack()
    pack.design = "no_such_design"
    with pytest.raises(ShortPreflightError, match="no_such_design"):
        preflight_short(pack)


def test_estimate_writes_an_approved_total(tmp_path: Path) -> None:
    path = estimate_short(make_ctx(tmp_path), make_pack(), auto_approve=True)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["job_id"] == "demo"
    assert data["line_items"]
    assert data["total_cost"] > 0


def test_estimate_refuses_when_the_prompt_is_declined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Never read the real terminal: under pytest stdin is captured, so an
    # un-patched ``confirm`` raises OSError/EOFError instead of returning.
    from video_editor_pipelines import short_pipeline
    from video_editor_pipelines.estimate_ui import EstimateAbortedError

    monkeypatch.setattr(short_pipeline, "confirm", lambda: "n")
    with pytest.raises(EstimateAbortedError):
        estimate_short(make_ctx(tmp_path), make_pack(), auto_approve=False)
    assert not (tmp_path / "work" / "demo" / "plan" / "cost_estimate.json").exists()


def _tts_total(items: list[dict[str, Any]]) -> float:
    return round(
        sum(float(item["total_cost"]) for item in items if item["task_type"] == "tts"),
        4,
    )


def test_both_gates_price_each_voice_line_as_its_own_call() -> None:
    """Sarvam bills per call, and this build makes one call per line (#263).

    The pack's three lines each cost one block of 1 000 characters ($0.023),
    so voice costs $0.069. The generic ``price_requests`` path now honours the
    provider's ``billing_unit: per_call`` as well as the short-path helper.
    """
    from video_editor_asset_ingestion.pricing import PricingRegistry
    from video_editor_pipelines.explainer_pipeline import price_requests
    from video_editor_pipelines.short_pipeline import price_short_job

    registry = PricingRegistry.load()
    requests = short_job_requests(make_pack())
    assert _tts_total(price_requests(requests, registry)) == pytest.approx(0.069)
    items = price_short_job(requests, registry)
    voice = next(item for item in items if item["task_type"] == "tts")
    assert voice["count"] == 3
    assert voice["total_cost"] == pytest.approx(0.069)


def test_the_estimate_file_charges_voice_per_line(tmp_path: Path) -> None:
    path = estimate_short(make_ctx(tmp_path), make_pack(), auto_approve=True)
    data = json.loads(path.read_text(encoding="utf-8"))
    voice = next(item for item in data["line_items"] if item["task_type"] == "tts")
    assert voice["count"] == 3
    assert voice["total_cost"] == pytest.approx(0.069)
    assert data["total_cost"] == pytest.approx(
        round(sum(float(item["total_cost"]) for item in data["line_items"]), 4)
    )


def test_estimate_refuses_an_unpriced_provider(tmp_path: Path) -> None:
    pack = make_pack()
    pack.voice.provider = "no_such_provider"
    from video_editor_pipelines.estimate_ui import EstimateError

    with pytest.raises(EstimateError, match="no price"):
        estimate_short(make_ctx(tmp_path), pack, auto_approve=True)


def _broken_design(monkeypatch: pytest.MonkeyPatch, mutate: Callable[[DesignTokens], None]) -> None:
    """Point ``preflight_short`` at a mutated copy of the real design."""
    from video_editor_core.designs import load_design
    from video_editor_pipelines import short_pipeline

    tokens = load_design("saju_series").model_copy(deep=True)
    mutate(tokens)
    monkeypatch.setattr(short_pipeline, "load_design", lambda name: tokens)


def test_preflight_wraps_a_bad_caption_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bad preset raises a raw ``ValueError`` from the captions skill at
    compile time — after every paid stage. Preflight must catch it."""
    _broken_design(
        monkeypatch,
        lambda tokens: setattr(tokens.layers, "caption_preset", "no_such_preset"),
    )
    with pytest.raises(ShortPreflightError, match="caption"):
        preflight_short(make_pack())


def test_preflight_checks_every_pill_the_pack_can_show(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scene with two ``show`` lines stacks two bands; only the first was
    checked, so a gap that pushes the second off-frame passed preflight."""
    _broken_design(monkeypatch, lambda tokens: tokens.layers.pill.update({"gap": 4000}))
    pack = make_pack()
    pack.scenes[0].lines.append(PackLine(say="Second.", show="Second pill"))
    with pytest.raises(ShortPreflightError, match="pill 1"):
        preflight_short(pack)


def test_preflight_refuses_a_blank_end_card_text() -> None:
    pack = make_pack()
    pack.end_card.text = "  "
    with pytest.raises(ShortPreflightError, match="end_card.text"):
        preflight_short(pack)


def test_preflight_skips_the_pill_check_when_no_line_shows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pack with no ``show`` line draws no pill, so its design need not
    carry a ``pill`` block; ``range(max(pills, 1))`` validated row 0 anyway."""
    from video_editor_pipelines import short_pipeline

    def _unexpected(*args: object, **kwargs: object) -> object:
        raise AssertionError("pill checked for a pack with no show lines")

    monkeypatch.setattr(short_pipeline, "pill", _unexpected)
    pack = make_pack()
    for scene in pack.scenes:
        for line in scene.lines:
            line.show = None
    preflight_short(pack)


def test_a_prompt_keeps_its_question_or_ellipsis_before_the_suffix() -> None:
    pack = make_pack()
    pack.scenes[0].still.prompt = "Who is the Rat?"
    assert short_job_requests(pack)[0].prompt == "Who is the Rat? ink wash, no text"
    pack.scenes[0].still.prompt = "Who is the Rat…"
    assert short_job_requests(pack)[0].prompt == "Who is the Rat… ink wash, no text"


def test_a_failed_narration_join_is_a_short_pipeline_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``main.short`` catches only ``ShortPipelineError``; a failed join after
    every paid TTS call must not escape as an ``FFmpegError`` traceback."""
    from video_editor_ffmpeg.run import FFmpegError
    from video_editor_pipelines import short_pipeline

    def _boom(command: list[str], **kwargs: object) -> None:
        raise FFmpegError("join failed", command)

    monkeypatch.setattr(short_pipeline, "run_ffmpeg", _boom)
    with pytest.raises(short_pipeline.ShortPipelineError, match="join"):
        short_pipeline._concat_narration([tmp_path / "one.mp3"], 0.0, tmp_path / "narration.wav")


def test_a_prompt_keeps_its_period_when_no_suffix_is_set() -> None:
    pack = make_pack()
    pack.image.style_suffix = ""
    pack.scenes[0].still.prompt = "a rat on a branch."
    image = short_job_requests(pack)[0]
    assert image.prompt == "a rat on a branch."


def stub_pack() -> ContentPack:
    data = json.loads(json.dumps(PACK))
    data["image"]["provider"] = "stub_image"
    data["voice"]["provider"] = "stub_tts"
    data["voice"]["voice_id"] = "en_US-lessac-medium"
    return ContentPack.model_validate(data)


def test_stills_are_generated_once_per_key(tmp_path: Path) -> None:
    from video_editor_pipelines.short_pipeline import generate_stills

    assets = generate_stills(make_ctx(tmp_path), stub_pack())
    assert set(assets.paths) == {"rat"}
    assert assets.paths["rat"].is_file()
    assert [result.asset_id for result in assets.results] == ["still_rat"]


def test_a_second_build_reuses_the_cached_still(tmp_path: Path) -> None:
    from video_editor_pipelines.short_pipeline import generate_stills

    ctx = make_ctx(tmp_path)
    first = generate_stills(ctx, stub_pack())
    second = generate_stills(ctx, stub_pack())
    assert second.paths["rat"] == first.paths["rat"]
    assert second.results[0].cached is True


TRANSCRIPT = {
    "words": [
        {"word": "You", "start": 0.10, "end": 0.30},
        {"word": "think", "start": 0.30, "end": 0.50},
        {"word": "you're", "start": 0.50, "end": 0.70},
        {"word": "a", "start": 0.70, "end": 0.80},
        {"word": "Rat", "start": 1.10, "end": 1.40},
        {"word": "Your", "start": 2.10, "end": 2.30},
        {"word": "animal", "start": 2.30, "end": 2.60},
        {"word": "is", "start": 2.60, "end": 2.70},
        {"word": "the", "start": 2.70, "end": 2.80},
        {"word": "year", "start": 2.80, "end": 3.00},
        {"word": "branch", "start": 3.00, "end": 3.40},
        {"word": "Find", "start": 4.10, "end": 4.30},
        {"word": "yours", "start": 4.30, "end": 4.60},
        {"word": "free", "start": 4.60, "end": 4.80},
        {"word": "at", "start": 4.80, "end": 4.90},
        {"word": "cosmicsaju", "start": 4.90, "end": 5.60},
    ]
}


def test_align_scenes_windows_start_where_each_scene_is_spoken() -> None:
    from video_editor_pipelines.short_pipeline import align_scenes

    alignment = align_scenes(make_pack(), audio_duration=6.0, transcript=TRANSCRIPT)
    assert [scene.id for scene in alignment.scenes] == ["hook", "branch", "end_card"]
    assert alignment.duration == pytest.approx(6.0)
    assert alignment.scenes[0].start == pytest.approx(0.0)
    assert alignment.scenes[1].start == pytest.approx(2.10)
    assert alignment.scenes[-1].end == pytest.approx(6.0)
    assert alignment.scenes[0].words[0].text == "You"


def test_a_phonetic_line_captions_the_display_spelling() -> None:
    """Cues keep the spoken word; captions show the pack's spelling."""
    from video_editor_pipelines.short_pipeline import align_scenes

    pack = make_pack()
    pack.scenes[1].lines[0].say = "In Saju, you are the year branch."
    pack.scenes[1].lines[0].speak_as = "In Sah-joo, you are the year branch."
    transcript = {
        "words": [
            {"word": "You", "start": 0.10, "end": 0.30},
            {"word": "think", "start": 0.30, "end": 0.50},
            {"word": "you're", "start": 0.50, "end": 0.70},
            {"word": "a", "start": 0.70, "end": 0.80},
            {"word": "Rat", "start": 1.10, "end": 1.40},
            {"word": "In", "start": 2.10, "end": 2.30},
            {"word": "Sah-joo", "start": 2.30, "end": 2.70},
            {"word": "you", "start": 2.70, "end": 2.90},
            {"word": "are", "start": 2.90, "end": 3.10},
            {"word": "the", "start": 3.10, "end": 3.25},
            {"word": "year", "start": 3.25, "end": 3.50},
            {"word": "branch", "start": 3.50, "end": 3.90},
            {"word": "Find", "start": 4.10, "end": 4.30},
            {"word": "yours", "start": 4.30, "end": 4.60},
            {"word": "free", "start": 4.60, "end": 4.80},
            {"word": "at", "start": 4.80, "end": 4.90},
            {"word": "cosmicsaju", "start": 4.90, "end": 5.60},
        ]
    }

    alignment = align_scenes(pack, audio_duration=6.0, transcript=transcript)

    branch = next(scene for scene in alignment.scenes if scene.id == "branch")
    assert [word.text for word in branch.words] == [
        "In",
        "Sah-joo,",
        "you",
        "are",
        "the",
        "year",
        "branch.",
    ]
    assert branch.words[0].display == ""
    assert branch.words[1].display == "Saju,"


def test_a_scene_with_no_timed_words_fails_the_build() -> None:
    from video_editor_pipelines.short_pipeline import ShortPipelineError, align_scenes

    # The guard walks the scenes in order, so with a transcript that heard
    # nothing at all it names the first one; the trailing case (only the end
    # card unheard) is covered by the same check, one iteration later.
    with pytest.raises(ShortPipelineError, match="hook"):
        align_scenes(make_pack(), audio_duration=6.0, transcript={"words": []})


def test_group_words_by_narration_splits_on_the_narration_text() -> None:
    from video_editor_pipelines.explainer_timing import (
        align_words,
        group_words_by_narration,
    )

    narrations = ["You think you're a Rat?", "Your animal is the year branch."]
    words = align_words(narrations, TRANSCRIPT["words"])
    groups = group_words_by_narration(narrations, words)
    assert [" ".join(word["word"] for word in group) for group in groups] == narrations


def test_the_voice_track_holds_the_gap_between_lines(tmp_path: Path) -> None:
    from video_editor_pipelines.short_pipeline import generate_voice

    track = generate_voice(make_ctx(tmp_path), stub_pack())
    # Three lines of the stub's 1.0 s tone, two 0.28 s gaps, ±40 ms of codec.
    assert track.duration == pytest.approx(3.56, abs=0.04)
    assert track.path.is_file()
    assert len(track.results) == 3


def test_a_line_the_voice_dropped_fails_the_build() -> None:
    """An unheard interior line is not an empty group — it is a pinned one.

    ``_offset_to_time`` maps an unmatched region onto the next matched block,
    so dropping the branch line leaves its words sharing the end card's first
    start. The empty-group guard alone would let a one-frame flash scene ship.
    """
    from video_editor_pipelines.short_pipeline import ShortPipelineError, align_scenes

    dropped = [
        word for word in TRANSCRIPT["words"] if word["start"] < 1.28 or word["start"] >= 4.10
    ]
    with pytest.raises(ShortPipelineError, match="branch"):
        align_scenes(make_pack(), audio_duration=6.0, transcript={"words": dropped})


def test_run_short_job_runs_each_stage_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from video_editor_pipelines import short_pipeline

    order: list[str] = []

    def record(name: str):
        def step(*args, **kwargs):
            order.append(name)
            raise AssertionError(f"stop after {name}")

        return step

    # The approval gate is the first thing the job does and it reads a file
    # this test never writes, so stub it: an unstubbed gate raises
    # ``ShortPipelineError`` before ``generate_stills`` is ever called and the
    # test would pass for the wrong reason.
    monkeypatch.setattr(short_pipeline, "check_approved_spend", lambda *a, **k: 0.0)
    monkeypatch.setattr(short_pipeline, "generate_stills", record("stills"))
    with pytest.raises(AssertionError, match="stop after stills"):
        short_pipeline.run_short_job(make_ctx(tmp_path), make_pack(), preflight_short(make_pack()))
    assert order == ["stills"]


def test_run_short_job_gates_spend_before_any_paid_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from video_editor_pipelines import short_pipeline
    from video_editor_pipelines.short_pipeline import ShortPipelineError

    reached: list[str] = []

    def no_approval(*args: object, **kwargs: object) -> float:
        raise ValueError("no approval on file")

    def record_stills(*args: object, **kwargs: object) -> object:
        reached.append("stills")
        raise AssertionError("generate_stills ran before the spend gate")

    monkeypatch.setattr(short_pipeline, "check_approved_spend", no_approval)
    monkeypatch.setattr(short_pipeline, "generate_stills", record_stills)
    with pytest.raises(ShortPipelineError, match="no approval on file"):
        short_pipeline.run_short_job(make_ctx(tmp_path), make_pack(), preflight_short(make_pack()))
    assert reached == []


def test_resolve_sfx_skips_a_sound_no_provider_can_fill(tmp_path: Path) -> None:
    import asyncio

    from sfx_placement.bank import LicenceRecord, ResolvedSfx, SfxBankError
    from video_editor_core.sfx_cues import SfxCue
    from video_editor_pipelines import short_pipeline

    clip = tmp_path / "hit.mp3"
    clip.write_bytes(b"mp3")

    class Bank:
        async def resolve(self, sound: str) -> ResolvedSfx:
            if sound == "hit":
                return ResolvedSfx(
                    path=clip,
                    provider="local",
                    licence=LicenceRecord(title="hit", license="local"),
                )
            raise SfxBankError("missing")

    cues = [
        SfxCue(id="hit_0001000", sound="hit", start=1.0),
        SfxCue(id="whoosh_0002000", sound="whoosh", start=2.0),
    ]
    resolved, timeline = asyncio.run(short_pipeline._resolve_sfx(Bank(), cues))
    assert [item.path for item in resolved] == [clip]
    assert timeline == [(clip, 1.0)]


def test_add_sfx_mixes_the_cues_and_writes_credits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sfx_placement.bank import LicenceRecord, ResolvedSfx
    from video_editor_core.sfx_cues import SfxCue
    from video_editor_pipelines import short_pipeline

    ctx = make_ctx(tmp_path)
    video = tmp_path / "voiced.mp4"
    video.write_bytes(b"voice")
    clip = tmp_path / "hit.mp3"
    clip.write_bytes(b"mp3")
    cue = SfxCue(id="hit_0001000", sound="hit", start=1.0)

    monkeypatch.setattr(
        short_pipeline, "cues_from_composition", lambda composition, **kwargs: [cue]
    )

    class FakeBank:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def resolve(self, sound: str) -> ResolvedSfx:
            return ResolvedSfx(
                path=clip,
                provider="freesound",
                licence=LicenceRecord(title="Hit", license="CC0 1.0"),
            )

        @staticmethod
        def write_credits(path: Path, resolved: list[ResolvedSfx]) -> Path:
            Path(path).write_text("credits", encoding="utf-8")
            return Path(path)

    captured: dict[str, object] = {}

    class FakeManager:
        def __init__(self, ctx: object, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(short_pipeline, "AssetIngestionManager", FakeManager)
    monkeypatch.setattr(short_pipeline, "SfxBank", FakeBank)
    monkeypatch.setattr(short_pipeline, "manager_resolver", lambda *args, **kwargs: object())

    calls: list[tuple[Path, list[tuple[Path, float]]]] = []

    def fake_mix(base: Path, timeline: list[tuple[Path, float]], output: Path, **kwargs):
        calls.append((Path(base), timeline))
        Path(output).write_bytes(b"mixed")
        return Path(output)

    monkeypatch.setattr(short_pipeline, "mix_sfx_into_video", fake_mix)

    output = short_pipeline._add_sfx(ctx, object(), video)

    assert output == ctx.work_dir / "with_sfx.mp4"
    assert output.read_bytes() == b"mixed"
    assert calls == [(video, [(clip, 1.0)])]
    assert (ctx.output_dir / "credits.txt").read_text(encoding="utf-8") == "credits"
    # #308: the SFX chain gets a zero budget, so a priced provider fails closed.
    budget = captured["budget"]
    assert getattr(budget, "limit", None) == 0.0


def test_add_sfx_with_no_cues_is_a_no_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from video_editor_pipelines import short_pipeline

    ctx = make_ctx(tmp_path)
    video = tmp_path / "voiced.mp4"
    video.write_bytes(b"voice")
    monkeypatch.setattr(short_pipeline, "cues_from_composition", lambda composition, **kwargs: [])
    assert short_pipeline._add_sfx(ctx, object(), video) == video


# ---------------------------------------------------------------------------
# #302 — the manifest must be saved after every stage
# ---------------------------------------------------------------------------


class _FakeScene:
    def __init__(self, start: float, end: float) -> None:
        self.start = start
        self.words = [type("Word", (), {"text": "hi", "display": "", "start": start, "end": end})()]


class _FakeAlignment:
    def __init__(self) -> None:
        self.scenes = [_FakeScene(0.0, 2.0)]

    def word_dicts(self) -> list[dict[str, object]]:
        return [{"word": "hi", "start": 0.0, "end": 2.0}]

    def model_dump(self, mode: str = "json") -> dict[str, object]:
        return {"scenes": []}


class _FakeComposition:
    duration = 2.0

    def model_dump(self, mode: str = "json") -> dict[str, object]:
        return {"scenes": []}


class _FakeProbe:
    width = 1080
    height = 1920
    fps = 30.0

    def duration_ms(self) -> int:
        return 2000


def _stub_short_stages(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """Replace every stage around the manifest bookkeeping."""
    from types import SimpleNamespace

    from video_editor_pipelines import short_pipeline

    (tmp_path / "work" / "demo" / "plan").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(short_pipeline, "check_approved_spend", lambda *a, **k: 0.0)
    monkeypatch.setattr(
        short_pipeline,
        "generate_stills",
        lambda *a, **k: short_pipeline.StillAssets(paths={"rat": tmp_path / "rat.png"}, results=()),
    )
    monkeypatch.setattr(
        short_pipeline,
        "layerize_still",
        lambda *a, **k: SimpleNamespace(
            depth=tmp_path / "depth.png",
            fg=tmp_path / "fg.png",
            bg=tmp_path / "bg.png",
            near=None,
            cached=True,
        ),
    )

    def _fake_resolve(ctx: object, requests: list[Any], **kwargs: object) -> list[Any]:
        return [
            SimpleNamespace(
                asset_id=request.asset_id,
                metadata={"cost": 0.0},
                resolved_path=(lambda p=tmp_path / f"{request.asset_id}.wav": p),
            )
            for request in requests
        ]

    monkeypatch.setattr(short_pipeline, "_resolve", _fake_resolve)
    monkeypatch.setattr(
        short_pipeline, "_concat_narration", lambda *a, **k: tmp_path / "narration.wav"
    )
    monkeypatch.setattr(short_pipeline, "_transcribe", lambda *a, **k: {"words": []})
    monkeypatch.setattr(short_pipeline, "align_scenes", lambda *a, **k: _FakeAlignment())
    monkeypatch.setattr(short_pipeline, "caption_layers", lambda *a, **k: [])
    monkeypatch.setattr(short_pipeline, "pack_to_composition", lambda *a, **k: _FakeComposition())
    monkeypatch.setattr(short_pipeline, "compile_composition", lambda *a, **k: None)
    monkeypatch.setattr(short_pipeline, "render_project", lambda *a, **k: tmp_path / "rendered.mp4")
    monkeypatch.setattr(short_pipeline, "mux_voiceover", lambda *a, **k: tmp_path / "voiced.mp4")
    monkeypatch.setattr(
        short_pipeline, "normalize_loudness", lambda *a, **k: tmp_path / "final.mp4"
    )
    monkeypatch.setattr(short_pipeline, "probe_media", lambda *a, **k: _FakeProbe())
    monkeypatch.setattr(short_pipeline, "measure_loudness", lambda *a, **k: (-16.0, -1.0))
    monkeypatch.setattr(short_pipeline, "cost_check_values", lambda *a, **k: (0.0, 0.0))
    monkeypatch.setattr(short_pipeline, "resolve_cues", lambda *a, **k: {})
    monkeypatch.setattr(short_pipeline, "_caption_coverage", lambda *a, **k: 1.0)
    monkeypatch.setattr(short_pipeline, "format_qa", lambda *a, **k: "QA TABLE")
    return SimpleNamespace(short_pipeline=short_pipeline)


def test_run_short_job_saves_each_stage_to_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stubs = _stub_short_stages(monkeypatch, tmp_path)
    module = stubs.short_pipeline
    monkeypatch.setattr(module, "evaluate_qa", lambda *a, **k: [])
    monkeypatch.setattr(module, "qa_report", lambda *a, **k: {"passed": True})

    pack = make_pack()
    pack.scenes[0].layers = "auto"
    ctx = make_ctx(tmp_path)
    result = module.run_short_job(ctx, pack, preflight_short(pack))

    on_disk = json.loads((ctx.work_dir / "manifest.json").read_text())
    assert {"layerize", "voice", "qa"} <= set(on_disk["stages"])
    assert on_disk["stages"]["qa"]["outputs"]["passed"] is True
    assert result.passed is True
    assert result.qa_text == "QA TABLE"


def test_a_failed_qa_still_persists_the_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from video_editor_pipelines.explainer_qa import QaCheck

    stubs = _stub_short_stages(monkeypatch, tmp_path)
    module = stubs.short_pipeline
    monkeypatch.setattr(module, "evaluate_qa", lambda *a, **k: [QaCheck("canvas", False, "boom")])
    monkeypatch.setattr(module, "qa_report", lambda *a, **k: {"passed": False})

    ctx = make_ctx(tmp_path)
    result = module.run_short_job(ctx, make_pack(), preflight_short(make_pack()))

    on_disk = json.loads((ctx.work_dir / "manifest.json").read_text())
    assert on_disk["stages"]["qa"]["outputs"]["passed"] is False
    assert result.passed is False


def test_a_copied_manifest_is_replaced_by_the_local_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A copied project must not keep reporting the source project's stages."""
    stubs = _stub_short_stages(monkeypatch, tmp_path)
    module = stubs.short_pipeline
    monkeypatch.setattr(module, "evaluate_qa", lambda *a, **k: [])
    monkeypatch.setattr(module, "qa_report", lambda *a, **k: {"passed": True})

    ctx = make_ctx(tmp_path)
    (ctx.work_dir / "manifest.json").write_text(
        json.dumps(
            {
                "job_id": "source-project",
                "stages": {"qa": {"completed_at": "2026-10-01", "outputs": {"qa": "old"}}},
            }
        ),
        encoding="utf-8",
    )

    module.run_short_job(ctx, make_pack(), preflight_short(make_pack()))

    on_disk = json.loads((ctx.work_dir / "manifest.json").read_text())
    assert on_disk["job_id"] == "demo"
    assert on_disk["stages"]["qa"]["outputs"]["passed"] is True


def test_sidecar_captions_write_an_srt_and_skip_the_burn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stubs = _stub_short_stages(monkeypatch, tmp_path)
    module = stubs.short_pipeline
    monkeypatch.setattr(module, "evaluate_qa", lambda *a, **k: [])
    monkeypatch.setattr(module, "qa_report", lambda *a, **k: {"passed": True})

    pack = make_pack()
    pack.captions.mode = "sidecar"
    ctx = make_ctx(tmp_path)
    result = module.run_short_job(ctx, pack, preflight_short(pack))

    assert result.srt_path == ctx.output_dir / "final.srt"
    assert "hi" in result.srt_path.read_text(encoding="utf-8")


def test_short_music_mixes_the_bed_with_ducking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from video_editor_pipelines import short_pipeline

    ctx = make_ctx(tmp_path)
    video = tmp_path / "voiced.mp4"
    video.write_bytes(b"v")
    music = tmp_path / "bed.mp3"
    music.write_bytes(b"m")
    pack = make_pack()
    pack.music.path = str(music)
    seen: dict[str, object] = {}

    class FakeSkill:
        def __init__(self, ctx, music_path=None, style=None):
            seen["music_path"] = music_path
            seen["gain"] = style.music_gain_db

        def run(self, **kwargs):
            seen["input"] = kwargs.get("input_video")
            out = ctx.work_dir / "mixed.mp4"
            out.write_bytes(b"mixed")
            return out

    monkeypatch.setattr("music.music.MusicSkill", FakeSkill)
    result = short_pipeline.mix_short_music(ctx, pack, video)

    assert result.read_bytes() == b"mixed"
    assert seen["music_path"] == music
    assert seen["gain"] == -22.0
    assert seen["input"] == video
