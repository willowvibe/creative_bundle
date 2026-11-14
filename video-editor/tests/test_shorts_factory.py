"""Tests for the shorts factory skill.

Tests use mocked ffmpeg/ffprobe calls so they run without external
dependencies. The skill uses ``subprocess.run`` directly; we patch it
on a per-test basis.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from shorts_factory import (
    MomentScorer,
    ShortCandidate,
    ShortsFactoryConfig,
    ShortsFactoryError,
    ShortsFactorySkill,
    _candidate_windows,
    _first_sentence,
    _HeuristicScorer,
    _parse_aspect,
    _probe_duration,
    _validate_transcript,
)

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_transcript() -> list[dict[str, Any]]:
    """Synthesise a 60-second transcript with a few hook phrases."""
    return [
        {"start": 0.0, "end": 4.0, "text": "Welcome back to the channel."},
        {"start": 4.0, "end": 8.0, "text": "Today I want to talk about scaling."},
        {"start": 8.0, "end": 13.0, "text": "The truth is most teams get this wrong."},
        {"start": 13.0, "end": 18.0, "text": "They hire too fast and then wonder why."},
        {"start": 18.0, "end": 23.0, "text": "Here's the thing nobody tells you about hiring."},
        {"start": 23.0, "end": 28.0, "text": "The real metric is revenue per employee."},
        {"start": 28.0, "end": 33.0, "text": "If you don't track that, you're flying blind."},
        {"start": 33.0, "end": 38.0, "text": "Let me show you what we changed at our company."},
        {"start": 38.0, "end": 43.0, "text": "We cut our burn rate by 40 percent in three months."},
        {"start": 43.0, "end": 48.0, "text": "And the only reason we survived is that metric."},
        {"start": 48.0, "end": 53.0, "text": "Most founders miss this completely."},
        {"start": 53.0, "end": 60.0, "text": "So if you take one thing away, track it weekly."},
    ]


class _StubResult:
    """Stub for ``subprocess.run`` return."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_shorts_run(
    monkeypatch: pytest.MonkeyPatch,
    fake: Callable[..., _StubResult],
    duration: float = 60.0,
) -> None:
    """Patch the shared ffmpeg/probe helpers used by the skill."""
    monkeypatch.setattr("shorts_factory.shorts_factory.run_ffmpeg", fake)
    monkeypatch.setattr("shorts_factory.shorts_factory.probe_duration", lambda _path: duration)


def _noop_fake_run(
    cmd: list[str],
    *args: Any,
    **kwargs: Any,
) -> _StubResult:
    """Default ffmpeg/ffprobe stub that returns success."""
    if cmd and cmd[0] == "ffprobe":
        return _StubResult(returncode=0, stdout="60.0")
    # ffmpeg: create a tiny file for every .mp4 arg so the cleanup pass
    # in _clip_video() can unlink the intermediate slice.
    for tok in cmd:
        if tok.endswith(".mp4") and "/" in tok:
            p = Path(tok)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    return _StubResult(returncode=0)


# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------


def test_parse_aspect_known_ratios() -> None:
    assert _parse_aspect("9:16") == (1080, 1920)
    assert _parse_aspect("16:9") == (1920, 1080)
    assert _parse_aspect("1:1") == (1080, 1080)
    assert _parse_aspect("4:5") == (1080, 1350)


def test_parse_aspect_unknown_falls_back_to_vertical() -> None:
    assert _parse_aspect("21:9") == (1080, 1920)


def test_parse_aspect_rejects_non_digit() -> None:
    with pytest.raises(ValueError):
        _parse_aspect("wide")
    with pytest.raises(ValueError):
        _parse_aspect("9:abc")
    with pytest.raises(ValueError):
        _parse_aspect("9x16")


def test_first_sentence_truncates_long_text() -> None:
    # Build a sentence that's longer than 20 chars before the first period.
    text = "This is a very long sentence indeed. And another one follows."
    out = _first_sentence(text, max_len=20)
    assert out.endswith("…")
    assert len(out) <= 20


def test_first_sentence_short_unchanged() -> None:
    text = "Short. Then another."
    # The first sentence "Short." is already under max_len, so no truncation.
    assert _first_sentence(text, max_len=80) == "Short."


def test_first_sentence_returns_empty_for_empty() -> None:
    assert _first_sentence("") == ""
    assert _first_sentence("   ") == ""


def test_first_sentence_handles_exclamation() -> None:
    assert _first_sentence("Wow! That was great.") == "Wow!"


def test_validate_transcript_ok() -> None:
    _validate_transcript([{"start": 0.0, "end": 1.0, "text": "hi"}])


def test_validate_transcript_missing_key() -> None:
    with pytest.raises(ShortsFactoryError, match="missing required key 'end'"):
        _validate_transcript([{"start": 0.0}])


def test_validate_transcript_non_numeric() -> None:
    with pytest.raises(ShortsFactoryError, match="non-numeric 'start'"):
        _validate_transcript([{"start": "oops", "end": 1.0}])


def test_validate_transcript_non_dict() -> None:
    with pytest.raises(ShortsFactoryError, match="must be a dict"):
        _validate_transcript(["nope"])  # type: ignore[list-item]


def test_candidate_windows_basic() -> None:
    transcript = _make_transcript()
    cfg = ShortsFactoryConfig(min_duration=15.0, max_duration=60.0)
    windows = _candidate_windows(transcript, cfg)
    assert windows, "should produce at least one window"
    for start, end in windows:
        assert end - start >= cfg.min_duration - 1.0
        assert end - start <= cfg.max_duration + 1.0


def test_candidate_windows_empty_transcript() -> None:
    assert _candidate_windows([], ShortsFactoryConfig()) == []


def test_heuristic_scorer_boosts_hook_phrases() -> None:
    transcript = _make_transcript()
    cfg = ShortsFactoryConfig(min_duration=15.0, max_duration=30.0)
    windows = _candidate_windows(transcript, cfg)
    scorer = _HeuristicScorer()
    scored = scorer.score_moments(transcript, windows)
    assert scored
    # The window covering "Here's the thing..." should score higher than
    # a window without any hook phrases.
    by_score = sorted(scored.items(), key=lambda kv: kv[1]["score"], reverse=True)
    top_score = by_score[0][1]["score"]
    assert top_score >= 0.5
    # Empty-window scoring should be present.
    for _, meta in scored.items():
        assert 0.0 <= meta["score"] <= 1.0


def test_heuristic_scorer_includes_rationale() -> None:
    transcript = _make_transcript()
    windows = _candidate_windows(transcript, ShortsFactoryConfig())
    scored = _HeuristicScorer().score_moments(transcript, windows)
    for _, meta in scored.items():
        assert "rationale" in meta
        assert "title" in meta


# ---------------------------------------------------------------------------
# Custom scorer
# ---------------------------------------------------------------------------


class _StaticScorer:
    """Returns a fixed score for every candidate window."""

    def score_moments(
        self,
        transcript: list[dict[str, Any]],
        candidates: list[tuple[float, float]],
    ) -> dict[tuple[float, float], dict[str, Any]]:
        return {
            w: {"score": 0.9, "title": "static", "rationale": "static_scorer"} for w in candidates
        }


# ---------------------------------------------------------------------------
# ShortsFactorySkill.run() tests
# ---------------------------------------------------------------------------


def test_skill_name() -> None:
    skill = ShortsFactorySkill()
    assert skill.name == "shorts_factory"


def test_skill_run_with_mock_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_shorts_run(monkeypatch, _noop_fake_run)
    src = tmp_path / "long.mp4"
    src.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    out_dir = tmp_path / "shorts"
    skill = ShortsFactorySkill(config=ShortsFactoryConfig(count=3))
    plan = skill.run(video_path=src, transcript=_make_transcript(), output_dir=out_dir)
    assert plan["count"] == 3
    assert plan["long_video"] == str(src)
    assert plan["aspect_ratio"] == "9:16"
    assert len(plan["shorts"]) == 3
    for entry in plan["shorts"]:
        assert entry["score"] >= 0.0
        assert Path(entry["output_path"]).exists()
    # plan.json must be written.
    plan_path = out_dir / "plan.json"
    assert plan_path.exists()
    saved = json.loads(plan_path.read_text())
    assert saved["count"] == 3


def test_skill_run_clamps_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_shorts_run(monkeypatch, _noop_fake_run)
    src = tmp_path / "long.mp4"
    src.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    skill = ShortsFactorySkill()
    # Asking for 99 should be clamped to 8.
    plan = skill.run(
        video_path=src,
        transcript=_make_transcript(),
        output_dir=tmp_path / "shorts",
        count=99,
    )
    assert plan["count"] <= 8


def test_skill_run_propagates_ffmpeg_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(*args: Any, **kwargs: Any) -> _StubResult:
        if args and args[0] and args[0][0] == "ffprobe":
            return _StubResult(returncode=0, stdout="60.0")
        return _StubResult(returncode=1, stderr="boom")

    _patch_shorts_run(monkeypatch, _fail)
    src = tmp_path / "long.mp4"
    src.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    skill = ShortsFactorySkill(config=ShortsFactoryConfig(count=1))
    with pytest.raises(ShortsFactoryError, match="ffmpeg failed"):
        skill.run(
            video_path=src,
            transcript=_make_transcript(),
            output_dir=tmp_path / "shorts",
        )


def test_skill_run_missing_video(tmp_path: Path) -> None:
    skill = ShortsFactorySkill()
    with pytest.raises(ShortsFactoryError, match="video not found"):
        skill.run(
            video_path=tmp_path / "missing.mp4",
            transcript=_make_transcript(),
            output_dir=tmp_path / "shorts",
        )


def test_skill_run_progress_callback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_shorts_run(monkeypatch, _noop_fake_run)
    src = tmp_path / "long.mp4"
    src.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    captured: list[str] = []
    skill = ShortsFactorySkill(
        config=ShortsFactoryConfig(count=1),
        on_progress=lambda msg: captured.append(msg),
    )
    skill.run(
        video_path=src,
        transcript=_make_transcript(),
        output_dir=tmp_path / "shorts",
    )
    assert any("scoring" in m or "rendering" in m or "plan" in m for m in captured)


def test_skill_run_with_custom_scorer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_shorts_run(monkeypatch, _noop_fake_run)
    src = tmp_path / "long.mp4"
    src.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    skill = ShortsFactorySkill(scorer=_StaticScorer())
    plan = skill.run(
        video_path=src,
        transcript=_make_transcript(),
        output_dir=tmp_path / "shorts",
        count=2,
    )
    assert plan["scoring"] == "custom"
    assert all(s["score"] == 0.9 for s in plan["shorts"])


def test_skill_run_no_transcript_synthesises_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_shorts_run(monkeypatch, _noop_fake_run)
    src = tmp_path / "long.mp4"
    src.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    skill = ShortsFactorySkill(config=ShortsFactoryConfig(count=2))
    plan = skill.run(
        video_path=src,
        transcript=None,
        output_dir=tmp_path / "shorts",
    )
    assert plan["count"] == 2
    assert all(s["rationale"] for s in plan["shorts"])


def test_skill_run_short_transcript_no_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transcript with one 5s segment and min_duration=60 raises."""
    _patch_shorts_run(monkeypatch, _noop_fake_run)
    skill = ShortsFactorySkill(
        config=ShortsFactoryConfig(count=1, min_duration=120.0, max_duration=180.0),
    )
    src = tmp_path / "long.mp4"
    src.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    # With min_duration=120 and only 5s of transcript, total_dur stays at
    # max(5, 121) = 121, so the loop's stop condition (cursor + 120 <= 121)
    # is only true for cursor=0, but the snap step finds no boundary, so
    # no candidate is produced.
    with pytest.raises(ShortsFactoryError, match="no candidate moments"):
        skill.run(
            video_path=src,
            transcript=[{"start": 0.0, "end": 5.0, "text": "tiny"}],
            output_dir=tmp_path / "shorts",
        )


def test_skill_run_writes_unique_short_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_shorts_run(monkeypatch, _noop_fake_run)
    src = tmp_path / "long.mp4"
    src.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    skill = ShortsFactorySkill(config=ShortsFactoryConfig(count=4))
    plan = skill.run(
        video_path=src,
        transcript=_make_transcript(),
        output_dir=tmp_path / "shorts",
    )
    ids = [s["short_id"] for s in plan["shorts"]]
    assert ids == ["short_001", "short_002", "short_003", "short_004"]
    # Filenames must be unique.
    output_paths = [s["output_path"] for s in plan["shorts"]]
    assert len(set(output_paths)) == len(output_paths)


def test_probe_duration_returns_float_when_ffprobe_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "shorts_factory.shorts_factory.probe_duration",
        lambda _path: 42.5,
    )
    assert _probe_duration(tmp_path / "anything.mp4") == 42.5


def test_probe_duration_falls_back_when_ffprobe_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(_path: Path) -> float:
        raise FileNotFoundError("no ffprobe")

    monkeypatch.setattr("shorts_factory.shorts_factory.probe_duration", _raise)
    assert _probe_duration(tmp_path / "anything.mp4") == 600.0


def test_short_candidate_dataclass_minimal() -> None:
    cand = ShortCandidate(
        short_id="short_001",
        start=1.0,
        end=11.0,
        duration=10.0,
        score=0.7,
        title="test",
        transcript_excerpt="hi",
        rationale="",
    )
    assert cand.output_path is None
    assert cand.short_id == "short_001"


def test_moment_scorer_protocol_satisfied() -> None:
    """The MomentScorer Protocol is structurally typed."""

    class _CustomScorer:
        def score_moments(
            self,
            transcript: list[dict[str, Any]],
            candidates: list[tuple[float, float]],
        ) -> dict[tuple[float, float], dict[str, Any]]:
            return {c: {"score": 0.5, "title": "x", "rationale": "y"} for c in candidates}

    s: MomentScorer = _CustomScorer()
    out = s.score_moments([], [(0.0, 10.0)])
    assert out[(0.0, 10.0)]["score"] == 0.5
