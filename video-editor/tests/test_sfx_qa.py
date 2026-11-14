"""Tests for the advisory SFX cue measurement."""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_core.sfx_cues import SfxCue
from video_editor_pipelines import sfx_qa


def _payload(
    values: list[float],
    onsets: list[tuple[float, float]],
    *,
    lufs: float | None = None,
) -> dict:
    item = {
        "envelope": {"startsAt": 0.0, "hopMs": 5.0, "values": values},
        "onsets": {"events": [{"t": t, "strength": strength} for t, strength in onsets]},
    }
    if lufs is not None:
        item["loudness"] = {"lufsIntegrated": lufs}
    return {"items": [item]}


def _envelope(bed_db: float = -40.0, cue_db: float | None = -20.0) -> list[float]:
    # 2 s at 5 ms per value; the cue occupies [1.0, 1.2).
    values: list[float] = []
    for index in range(400):
        t = index * 0.005
        values.append(cue_db if (cue_db is not None and 1.0 <= t < 1.2) else bed_db)
    return values


def test_a_loud_cue_on_a_quiet_bed_is_reported_ok(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        sfx_qa, "analyze_audio", lambda *args, **kwargs: _payload(_envelope(), [(1.01, 0.6)])
    )
    report = sfx_qa.analyze_sfx(
        tmp_path / "final.mp4",
        [SfxCue(id="hit_0001000", sound="hit", start=1.0)],
    )
    measurement = report.measurements[0]
    assert measurement.level_db is not None
    assert measurement.bed_db is not None
    assert measurement.lift_db == pytest.approx(20.0)
    assert measurement.onset == pytest.approx(1.01)
    assert measurement.sync_error == pytest.approx(0.01)
    assert measurement.ok is True
    assert report.ok is True
    assert report.as_dict()["cues"][0]["lift_db"] == pytest.approx(20.0)


def test_a_cue_with_no_level_lift_is_still_reported(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        sfx_qa,
        "analyze_audio",
        lambda *args, **kwargs: _payload(_envelope(cue_db=None), []),
    )
    report = sfx_qa.analyze_sfx(
        tmp_path / "final.mp4",
        [SfxCue(id="hit_0001000", sound="hit", start=1.0)],
    )
    measurement = report.measurements[0]
    assert measurement.lift_db == pytest.approx(0.0)
    assert measurement.ok is True  # sync is unmeasured, not wrong


def test_a_late_onset_is_reported(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        sfx_qa,
        "analyze_audio",
        lambda *args, **kwargs: _payload(_envelope(), [(1.12, 0.6)]),
    )
    report = sfx_qa.analyze_sfx(
        tmp_path / "final.mp4",
        [SfxCue(id="hit_0001000", sound="hit", start=1.0)],
    )
    measurement = report.measurements[0]
    assert measurement.sync_error == pytest.approx(0.12)
    assert measurement.ok is False


def test_a_missing_onset_is_advised_but_not_a_sync_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        sfx_qa,
        "analyze_audio",
        lambda *args, **kwargs: _payload(_envelope(), []),
    )
    report = sfx_qa.analyze_sfx(
        tmp_path / "final.mp4",
        [SfxCue(id="hit_0001000", sound="hit", start=1.0)],
    )
    measurement = report.measurements[0]
    assert measurement.onset is None
    assert measurement.sync_error is None
    assert measurement.ok is True


def test_no_cues_is_an_empty_report(tmp_path: Path) -> None:
    report = sfx_qa.analyze_sfx(tmp_path / "final.mp4", [])
    assert report.measurements == ()
    assert report.ok is False


def test_the_reference_file_isolates_the_sfx_contribution(tmp_path: Path, monkeypatch) -> None:
    def fake_analyze(path, **kwargs):
        if str(path).endswith("voiced.mp4"):
            return _payload(_envelope(cue_db=-40.0), [])
        return _payload(_envelope(cue_db=-20.0), [(1.01, 0.6)])

    monkeypatch.setattr(sfx_qa, "analyze_audio", fake_analyze)
    report = sfx_qa.analyze_sfx(
        tmp_path / "mixed.mp4",
        [SfxCue(id="hit_0001000", sound="hit", start=1.0)],
        reference=tmp_path / "voiced.mp4",
    )
    measurement = report.measurements[0]
    assert measurement.reference_db == pytest.approx(-40.0)
    assert measurement.added_db == pytest.approx(20.0)
    assert measurement.ok is True
    assert report.as_dict()["cues"][0]["added_db"] == pytest.approx(20.0)


def test_a_reference_with_no_sfx_contribution_is_not_ok(tmp_path: Path, monkeypatch) -> None:
    def fake_analyze(path, **kwargs):
        return _payload(_envelope(cue_db=-40.0), [])

    monkeypatch.setattr(sfx_qa, "analyze_audio", fake_analyze)
    report = sfx_qa.analyze_sfx(
        tmp_path / "mixed.mp4",
        [SfxCue(id="hit_0001000", sound="hit", start=1.0)],
        reference=tmp_path / "voiced.mp4",
    )
    measurement = report.measurements[0]
    # No contribution: the column is zero for review. `ok` is the sync verdict,
    # and sync is unmeasured here.
    assert measurement.added_db == pytest.approx(0.0)
    assert measurement.ok is True


def test_the_global_sfx_level_is_gated(tmp_path: Path, monkeypatch) -> None:
    def fake_analyze(path, **kwargs):
        if str(path).endswith("voiced.mp4"):
            return _payload(_envelope(), [], lufs=-14.0)
        return _payload(_envelope(), [(1.0, 0.6)], lufs=-10.0)

    monkeypatch.setattr(sfx_qa, "analyze_audio", fake_analyze)
    report = sfx_qa.analyze_sfx(
        tmp_path / "mixed.mp4",
        [SfxCue(id="hit_0001000", sound="hit", start=1.0)],
        reference=tmp_path / "voiced.mp4",
    )
    assert report.added_lufs == pytest.approx(4.0)
    assert report.ok is False
    assert report.as_dict()["added_lufs"] == pytest.approx(4.0)


def test_a_subtle_sfx_bus_passes_the_global_gate(tmp_path: Path, monkeypatch) -> None:
    def fake_analyze(path, **kwargs):
        if str(path).endswith("voiced.mp4"):
            return _payload(_envelope(), [], lufs=-14.0)
        return _payload(_envelope(), [(1.0, 0.6)], lufs=-13.7)

    monkeypatch.setattr(sfx_qa, "analyze_audio", fake_analyze)
    report = sfx_qa.analyze_sfx(
        tmp_path / "mixed.mp4",
        [SfxCue(id="hit_0001000", sound="hit", start=1.0)],
        reference=tmp_path / "voiced.mp4",
    )
    assert report.added_lufs == pytest.approx(0.3)
    assert report.ok is True
