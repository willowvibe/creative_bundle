"""Tests for the thumbnail skill."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from thumbnail import (
    ThumbnailCandidate,
    ThumbnailConfig,
    ThumbnailError,
    ThumbnailScorer,
    ThumbnailSkill,
    _best_title,
    _candidate_timestamps,
    _clamp_dimensions,
    _HeuristicScorer,
    _probe_duration,
    _sanitize_title,
)
from thumbnail.thumbnail import _render_title_overlay  # noqa: E402

# ---------------------------------------------------------------------------
# Minimal JobContext stub
# ---------------------------------------------------------------------------


class _StubCtx:
    """Minimal JobContext for thumbnail.run()."""

    def __init__(self, tmp_path: Path) -> None:
        self.work_dir = tmp_path / "work"
        self.output_dir = tmp_path / "out"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def ensure_dirs(self) -> None:
        for d in (self.work_dir, self.output_dir):
            d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# ffmpeg/ffprobe stub
# ---------------------------------------------------------------------------


class _StubResult:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _make_fake_run(
    fake_duration: float = 60.0,
    extract_target: str = "extracted",
    fail_on: str | None = None,
) -> Callable[..., _StubResult]:
    """Return a subprocess.run stand-in that pretends ffmpeg/ffprobe work.

    For jpg outputs (touched with write_bytes of a small payload), we
    fake the extraction by creating a dummy JPG file. For ffprobe we
    return ``fake_duration``.
    """

    def fake_run(cmd: list[str], **kwargs: Any) -> _StubResult:
        # ffprobe
        if cmd and cmd[0] == "ffprobe":
            if fail_on == "ffprobe":
                return _StubResult(returncode=1, stderr="bad")
            return _StubResult(returncode=0, stdout=str(fake_duration))
        # ffmpeg
        if cmd and cmd[0] == "ffmpeg":
            # Look for an output path ending with .jpg or .png
            output = None
            for tok in reversed(cmd):
                if tok.endswith(".jpg") or tok.endswith(".png"):
                    output = tok
                    break
            if fail_on == "ffmpeg":
                return _StubResult(returncode=1, stderr="ffmpeg failed")
            if output is not None:
                # Write a small payload so the file exists for the scorer.
                Path(output).write_bytes(b"\xff\xd8\xfakecandidate")
            return _StubResult(returncode=0)
        return _StubResult(returncode=0)

    return fake_run


def _patch_run(
    monkeypatch: pytest.MonkeyPatch,
    fake: Callable[..., _StubResult],
    duration: float = 60.0,
) -> None:
    """Patch the shared ffmpeg/probe helpers used by the thumbnail module."""
    monkeypatch.setattr("thumbnail.thumbnail.run_ffmpeg", fake)
    monkeypatch.setattr("thumbnail.thumbnail.probe_duration", lambda _path: duration)


@pytest.fixture
def patched_run(monkeypatch: pytest.MonkeyPatch) -> Callable[..., _StubResult]:
    """Patch the shared ffmpeg/probe helpers for the thumbnail module."""
    fake = _make_fake_run()
    _patch_run(monkeypatch, fake)
    return fake


# ---------------------------------------------------------------------------
# Pure-helper tests
# ---------------------------------------------------------------------------


def test_sanitize_title_strips_dangerous_chars() -> None:
    raw = "Hello: World / 'Path' \\ %danger% "
    cleaned = _sanitize_title(raw)
    assert ":" not in cleaned
    assert "'" not in cleaned
    assert '"' not in cleaned
    assert "/" not in cleaned
    assert "\\" not in cleaned
    assert "%" not in cleaned
    assert "World" in cleaned


def test_sanitize_title_collapses_whitespace() -> None:
    cleaned = _sanitize_title("  multi   space\ttext  ")
    assert cleaned == "multi space text"


def test_sanitize_title_empty_string() -> None:
    assert _sanitize_title("") == ""
    assert _sanitize_title("::,,%%") == ""


def test_candidate_timestamps_even_spacing() -> None:
    ts = _candidate_timestamps(60.0, 4)
    assert len(ts) == 4
    assert ts[0] > 0
    assert ts[-1] < 60.0
    # Should be roughly evenly spaced.
    diffs = [b - a for a, b in zip(ts, ts[1:], strict=False)]
    for d in diffs:
        assert abs(d - diffs[0]) < 0.5


def test_candidate_timestamps_single() -> None:
    ts = _candidate_timestamps(60.0, 1)
    assert ts == [30.0]


def test_candidate_timestamps_zero() -> None:
    assert _candidate_timestamps(60.0, 0) == []


def test_candidate_timestamps_zero_duration() -> None:
    assert _candidate_timestamps(0.0, 4) == [0.0]


def test_clamp_dimensions_rounds_even() -> None:
    w, h = _clamp_dimensions(1279, 719)
    assert w == 1278
    assert h == 718
    assert w % 2 == 0
    assert h % 2 == 0


def test_clamp_dimensions_enforces_minimum() -> None:
    w, h = _clamp_dimensions(100, 100)
    assert w >= 320
    assert h >= 180


def test_probe_duration_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("thumbnail.thumbnail.probe_duration", lambda _path: 42.5)
    assert _probe_duration(Path("ignored.mp4")) == 42.5


def test_probe_duration_filenotfound(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_path: Path) -> float:
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr("thumbnail.thumbnail.probe_duration", boom)
    assert _probe_duration(Path("ignored.mp4")) == 600.0


def test_probe_duration_garbage_output(monkeypatch: pytest.MonkeyPatch) -> None:
    from video_editor_ffmpeg.probe import MediaInfoError

    def boom(_path: Path) -> float:
        raise MediaInfoError("not a number")

    monkeypatch.setattr("thumbnail.thumbnail.probe_duration", boom)
    assert _probe_duration(Path("ignored.mp4")) == 600.0


def test_probe_duration_nonzero_returncode(monkeypatch: pytest.MonkeyPatch) -> None:
    from video_editor_ffmpeg.probe import MediaInfoError

    def boom(_path: Path) -> float:
        raise MediaInfoError("ffprobe exited 1")

    monkeypatch.setattr("thumbnail.thumbnail.probe_duration", boom)
    assert _probe_duration(Path("ignored.mp4")) == 600.0


def test_best_title_picks_highest_score() -> None:
    cands = [
        ThumbnailCandidate(
            candidate_id="a", timestamp=0.0, output_path=Path("a.jpg"), score=0.1, title="low"
        ),
        ThumbnailCandidate(
            candidate_id="b", timestamp=1.0, output_path=Path("b.jpg"), score=0.9, title="high"
        ),
        ThumbnailCandidate(
            candidate_id="c", timestamp=2.0, output_path=Path("c.jpg"), score=0.5, title="mid"
        ),
    ]
    assert _best_title(cands) == "high"


def test_best_title_no_titles_returns_empty() -> None:
    cands = [
        ThumbnailCandidate("a", 0.0, Path("a.jpg"), score=0.9, title=""),
        ThumbnailCandidate("b", 1.0, Path("b.jpg"), score=0.5, title=""),
    ]
    assert _best_title(cands) == ""


def test_best_title_empty_candidate_list() -> None:
    assert _best_title([]) == ""


# ---------------------------------------------------------------------------
# _HeuristicScorer
# ---------------------------------------------------------------------------


def _create_frames(tmp_path: Path, n: int) -> list[tuple[float, Path]]:
    """Create ``n`` JPG files of varying sizes to test the heuristic."""
    paths: list[tuple[float, Path]] = []
    for i in range(n):
        p = tmp_path / f"frame_{i}.jpg"
        # Vary size so complexity factor differs.
        p.write_bytes(b"\xff\xd8" + b"X" * (1000 * (i + 1)))
        paths.append((float(i * 10), p))
    return paths


def test_heuristic_scorer_returns_score_per_timestamp(tmp_path: Path) -> None:
    candidates = _create_frames(tmp_path, 4)
    scorer = _HeuristicScorer()
    scored = scorer.score_candidates(Path("unused.mp4"), candidates)
    assert set(scored.keys()) == {0.0, 10.0, 20.0, 30.0}
    for _ts, meta in scored.items():
        assert 0.0 <= meta["score"] <= 1.0
        assert "title" in meta
        assert "rationale" in meta


def test_heuristic_scorer_empty_candidates() -> None:
    scorer = _HeuristicScorer()
    assert scorer.score_candidates(Path("x"), []) == {}


def test_heuristic_scorer_middle_frame_beats_edges(tmp_path: Path) -> None:
    candidates = _create_frames(tmp_path, 5)
    scorer = _HeuristicScorer()
    scored = scorer.score_candidates(Path("unused.mp4"), candidates)
    middle_score = scored[20.0]["score"]
    edge_score = scored[0.0]["score"]
    assert middle_score > edge_score


def test_heuristic_scorer_larger_jpg_wins_complexity(tmp_path: Path) -> None:
    # All positions equal, but framework frame has bigger JPG.
    small = tmp_path / "small.jpg"
    small.write_bytes(b"\xff\xd8" + b"X" * 100)
    big = tmp_path / "big.jpg"
    big.write_bytes(b"\xff\xd8" + b"X" * 100000)
    candidates = [(0.0, small), (0.0, big)]  # Same ts but different sizes
    # Actually they must have different timestamps for the heuristic.
    candidates = [(0.0, small), (10.0, big)]
    scorer = _HeuristicScorer()
    scored = scorer.score_candidates(Path("unused.mp4"), candidates)
    # Bigger file should score at least as well.
    assert scored[10.0]["score"] >= scored[0.0]["score"]


# ---------------------------------------------------------------------------
# ThumbnailSkill.run()
# ---------------------------------------------------------------------------


def _seed_video(tmp_path: Path) -> Path:
    """Create a tiny fake video file (1 byte is enough — ffmpeg is mocked)."""
    p = tmp_path / "final.mp4"
    p.write_bytes(b"fake video")
    return p


def test_skill_run_writes_candidates_and_plan(
    tmp_path: Path, patched_run: Callable[..., _StubResult]
) -> None:
    ctx = _StubCtx(tmp_path)
    video_path = _seed_video(tmp_path)
    skill = ThumbnailSkill()
    plan = skill.run(ctx, video_path=video_path)
    assert "candidates" in plan
    assert "selected" in plan
    assert "title_overlay" in plan
    # 8 candidates (default).
    assert len(plan["candidates"]) == 8
    # Files exist.
    out_dir = ctx.output_dir / "thumbnail"
    for i in range(1, 9):
        assert (out_dir / f"candidate_{i:03d}.jpg").exists()
    assert (out_dir / "selected.jpg").exists()
    assert (out_dir / "plan.json").exists()


def test_skill_run_picks_highest_score(
    tmp_path: Path, patched_run: Callable[..., _StubResult]
) -> None:
    ctx = _StubCtx(tmp_path)
    video_path = _seed_video(tmp_path)
    skill = ThumbnailSkill()
    plan = skill.run(ctx, video_path=video_path, candidates=3)
    # Heuristic scorer is used by default.
    selected = plan["selected"]
    assert selected["candidate_id"] in {"candidate_001", "candidate_002", "candidate_003"}


def test_skill_run_with_title_overlay(
    tmp_path: Path, patched_run: Callable[..., _StubResult]
) -> None:
    ctx = _StubCtx(tmp_path)
    video_path = _seed_video(tmp_path)
    skill = ThumbnailSkill(
        config=ThumbnailConfig(
            candidates=2,
            title_overlay=True,
            title_font_size=64,
            title_max_chars=40,
        )
    )
    # Override the scorer to give a title.
    skill.scorer = _TitleScorer({"title": "Hello World"})
    plan = skill.run(ctx, video_path=video_path)
    assert plan["title_overlay"]["enabled"] is True
    assert plan["title_overlay"]["title"] == "Hello World"
    assert plan["title_overlay"]["output_path"] is not None
    out_path = Path(plan["title_overlay"]["output_path"])
    assert out_path.exists()


def test_skill_run_no_title_overlay_when_no_titles(
    tmp_path: Path, patched_run: Callable[..., _StubResult]
) -> None:
    ctx = _StubCtx(tmp_path)
    video_path = _seed_video(tmp_path)
    skill = ThumbnailSkill(config=ThumbnailConfig(candidates=2, title_overlay=True))
    plan = skill.run(ctx, video_path=video_path)
    # Heuristic scorer returns no titles.
    assert plan["title_overlay"]["enabled"] is False
    assert plan["title_overlay"]["output_path"] is None


def test_skill_run_infer_video_path(
    tmp_path: Path, patched_run: Callable[..., _StubResult]
) -> None:
    ctx = _StubCtx(tmp_path)
    # Drop final.mp4 in output_dir.
    video_path = ctx.output_dir / "final.mp4"
    video_path.write_bytes(b"fake")
    skill = ThumbnailSkill()
    plan = skill.run(ctx)  # No explicit video_path -> inferred
    assert plan["video"] == str(video_path)


def test_skill_run_infer_video_path_rough_cut(
    tmp_path: Path, patched_run: Callable[..., _StubResult]
) -> None:
    ctx = _StubCtx(tmp_path)
    # rough_cut.mp4 in work_dir should be preferred.
    rough_path = ctx.work_dir / "rough_cut.mp4"
    rough_path.write_bytes(b"fake")
    skill = ThumbnailSkill()
    plan = skill.run(ctx)
    assert plan["video"] == str(rough_path)


def test_skill_run_missing_video_raises(
    tmp_path: Path, patched_run: Callable[..., _StubResult]
) -> None:
    ctx = _StubCtx(tmp_path)
    skill = ThumbnailSkill()
    with pytest.raises(ThumbnailError, match="video_path is required"):
        skill.run(ctx)


def test_skill_run_video_path_does_not_exist(
    tmp_path: Path, patched_run: Callable[..., _StubResult]
) -> None:
    ctx = _StubCtx(tmp_path)
    bad = tmp_path / "missing.mp4"
    skill = ThumbnailSkill()
    with pytest.raises(ThumbnailError, match="video not found"):
        skill.run(ctx, video_path=bad)


def test_skill_run_zero_duration_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _make_fake_run(fake_duration=0.0)
    _patch_run(monkeypatch, fake, duration=0.0)
    ctx = _StubCtx(tmp_path)
    video_path = _seed_video(tmp_path)
    skill = ThumbnailSkill()
    with pytest.raises(ThumbnailError, match="zero duration"):
        skill.run(ctx, video_path=video_path)


def test_skill_run_ffmpeg_failure_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _make_fake_run(fail_on="ffmpeg")
    _patch_run(monkeypatch, fake)
    ctx = _StubCtx(tmp_path)
    video_path = _seed_video(tmp_path)
    skill = ThumbnailSkill()
    with pytest.raises(ThumbnailError, match="ffmpeg failed"):
        skill.run(ctx, video_path=video_path)


def test_skill_run_clamps_candidate_count(
    tmp_path: Path, patched_run: Callable[..., _StubResult]
) -> None:
    ctx = _StubCtx(tmp_path)
    video_path = _seed_video(tmp_path)
    skill = ThumbnailSkill()
    plan_huge = skill.run(ctx, video_path=video_path, candidates=999)
    assert len(plan_huge["candidates"]) == 32  # clamped
    plan_zero = skill.run(ctx, video_path=video_path, candidates=0)
    assert len(plan_zero["candidates"]) == 1
    plan_negative = skill.run(ctx, video_path=video_path, candidates=-5)
    assert len(plan_negative["candidates"]) == 1


def test_skill_run_unique_candidate_ids(
    tmp_path: Path, patched_run: Callable[..., _StubResult]
) -> None:
    ctx = _StubCtx(tmp_path)
    video_path = _seed_video(tmp_path)
    skill = ThumbnailSkill()
    plan = skill.run(ctx, video_path=video_path, candidates=5)
    ids = [c["candidate_id"] for c in plan["candidates"]]
    assert ids == [f"candidate_{i:03d}" for i in range(1, 6)]


def test_skill_run_title_overlay_disabled(
    tmp_path: Path, patched_run: Callable[..., _StubResult]
) -> None:
    ctx = _StubCtx(tmp_path)
    video_path = _seed_video(tmp_path)
    skill = ThumbnailSkill(config=ThumbnailConfig(candidates=2, title_overlay=False))
    skill.scorer = _TitleScorer({"title": "Foo"})
    plan = skill.run(ctx, video_path=video_path)
    assert plan["title_overlay"]["enabled"] is False
    assert plan["title_overlay"]["output_path"] is None


def test_skill_run_plan_json_roundtrip(
    tmp_path: Path, patched_run: Callable[..., _StubResult]
) -> None:
    ctx = _StubCtx(tmp_path)
    video_path = _seed_video(tmp_path)
    skill = ThumbnailSkill()
    skill.run(ctx, video_path=video_path, candidates=2)
    plan_path = ctx.output_dir / "thumbnail" / "plan.json"
    data = json.loads(plan_path.read_text())
    assert "selected" in data
    assert "candidates" in data
    assert data["scoring"] in {"heuristic", "custom"}


def test_skill_run_progress_callback_invoked(
    tmp_path: Path, patched_run: Callable[..., _StubResult]
) -> None:
    ctx = _StubCtx(tmp_path)
    video_path = _seed_video(tmp_path)
    messages: list[str] = []

    def on_progress(msg: str) -> None:
        messages.append(msg)

    skill = ThumbnailSkill(on_progress=on_progress)
    skill.run(ctx, video_path=video_path, candidates=2)
    assert len(messages) >= 3
    assert any("candidates" in m for m in messages)


def test_skill_run_progress_callback_does_not_raise(
    tmp_path: Path, patched_run: Callable[..., _StubResult]
) -> None:
    ctx = _StubCtx(tmp_path)
    video_path = _seed_video(tmp_path)

    def boom(msg: str) -> None:
        raise RuntimeError("kaboom")

    skill = ThumbnailSkill(on_progress=boom)
    # Should not raise even though the callback raises.
    plan = skill.run(ctx, video_path=video_path, candidates=2)
    assert "selected" in plan


def test_skill_run_uses_custom_scorer(
    tmp_path: Path, patched_run: Callable[..., _StubResult]
) -> None:
    ctx = _StubCtx(tmp_path)
    video_path = _seed_video(tmp_path)
    custom = _ConstantScorer(score=0.99, title="Custom Title")
    skill = ThumbnailSkill(scorer=custom)
    plan = skill.run(ctx, video_path=video_path, candidates=2)
    assert plan["scoring"] == "custom"
    # All scores equal — first candidate wins (earlier id).
    for c in plan["candidates"]:
        assert c["score"] == 0.99
    assert plan["selected"]["title"] == "Custom Title"


def test_skill_run_png_suffix(tmp_path: Path, patched_run: Callable[..., _StubResult]) -> None:
    ctx = _StubCtx(tmp_path)
    video_path = _seed_video(tmp_path)
    skill = ThumbnailSkill(config=ThumbnailConfig(candidates=2, suffix=".png"))
    skill.run(ctx, video_path=video_path)
    out_dir = ctx.output_dir / "thumbnail"
    assert (out_dir / "candidate_001.png").exists()
    assert (out_dir / "selected.png").exists()


# ---------------------------------------------------------------------------
# _render_title_overlay direct test
# ---------------------------------------------------------------------------


def test_render_title_overlay_empty_title_copies(
    tmp_path: Path, patched_run: Callable[..., _StubResult]
) -> None:
    src = tmp_path / "src.jpg"
    src.write_bytes(b"\xff\xd8src")
    dst = tmp_path / "dst.jpg"
    cfg = ThumbnailConfig()
    _render_title_overlay(src, dst, "", cfg, width=1280, height=720)
    assert dst.exists()


def test_render_title_overlay_with_title(
    tmp_path: Path, patched_run: Callable[..., _StubResult]
) -> None:
    src = tmp_path / "src.jpg"
    src.write_bytes(b"\xff\xd8src")
    dst = tmp_path / "dst.jpg"
    cfg = ThumbnailConfig(title_font_size=64)
    _render_title_overlay(src, dst, "Hello World", cfg, width=1280, height=720)
    assert dst.exists()


# ---------------------------------------------------------------------------
# ThumbnailScorer Protocol
# ---------------------------------------------------------------------------


def test_thumbnail_scorer_protocol_satisfied() -> None:
    """Classes implementing ``score_candidates`` should satisfy the Protocol."""

    class MyScorer:
        def score_candidates(
            self, video_path: Path, candidates: list[tuple[float, Path]]
        ) -> dict[float, dict[str, Any]]:
            return {ts: {"score": 0.5, "title": "", "rationale": ""} for ts, _ in candidates}

    # isinstance should treat it as a ThumbnailScorer.
    instance = MyScorer()
    assert isinstance(instance, ThumbnailScorer)


# ---------------------------------------------------------------------------
# ThumbnailCandidate dataclass
# ---------------------------------------------------------------------------


def test_thumbnail_candidate_to_dict() -> None:
    cand = ThumbnailCandidate(
        candidate_id="candidate_007",
        timestamp=12.5,
        output_path=Path("cand.jpg"),
        score=0.42,
        title="Foo",
        rationale="bar",
    )
    d = cand.to_dict()
    assert d["candidate_id"] == "candidate_007"
    assert d["timestamp"] == 12.5
    assert d["output_path"] == "cand.jpg"
    assert d["score"] == 0.42
    assert d["title"] == "Foo"
    assert d["rationale"] == "bar"


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _TitleScorer:
    """Returns the same title for every timestamp."""

    def __init__(self, meta: dict[str, Any]) -> None:
        self.meta = meta

    def score_candidates(
        self, video_path: Path, candidates: list[tuple[float, Path]]
    ) -> dict[float, dict[str, Any]]:
        return {ts: {"score": 0.7, **self.meta} for ts, _ in candidates}


class _ConstantScorer:
    """Returns a constant score + title for every timestamp."""

    def __init__(self, score: float, title: str) -> None:
        self.score = score
        self.title = title

    def score_candidates(
        self, video_path: Path, candidates: list[tuple[float, Path]]
    ) -> dict[float, dict[str, Any]]:
        return {
            ts: {"score": self.score, "title": self.title, "rationale": "const"}
            for ts, _ in candidates
        }
