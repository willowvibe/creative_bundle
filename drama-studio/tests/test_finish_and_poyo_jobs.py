"""finish_upscale plans and prices before spending; poyo_jobs never resubmits a paid task."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import finish_upscale, poyo_jobs


def test_upscale_price_scales_with_output_pixels():
    assert finish_upscale.usd_for(10.0, 2.0) == pytest.approx(0.55)
    assert finish_upscale.usd_for(10.0, 1.5) == pytest.approx(0.55 * 0.5625)


def test_plan_skips_done_clips_and_honours_only(tmp_path: Path):
    clips, out = tmp_path / "clips", tmp_path / "out"
    clips.mkdir()
    out.mkdir()
    for name in ("v01", "v02", "v03"):
        (clips / f"{name}.mp4").write_bytes(b"x")
    (out / "v02.mp4").write_bytes(b"done")
    assert [c.stem for c in finish_upscale.plan(clips, out, None)] == ["v01", "v03"]
    assert [c.stem for c in finish_upscale.plan(clips, out, {"v03"})] == ["v03"]


def test_without_yes_nothing_is_spent(tmp_path: Path, monkeypatch, capsys):
    clips = tmp_path / "clips"
    clips.mkdir()
    (clips / "v01.mp4").write_bytes(b"x")
    monkeypatch.setattr(finish_upscale, "seconds", lambda clip: 6.0)
    spent: list[Path] = []
    monkeypatch.setattr(finish_upscale, "upscale", lambda *a: spent.append(a[0]))
    assert finish_upscale.main([str(clips), str(tmp_path / "out")]) == 0
    assert not spent and "dry run" in capsys.readouterr().out
    assert (
        finish_upscale.main([str(clips), str(tmp_path / "out"), "--yes", "--max-usd", "0.01"]) == 2
    )
    assert not spent


class FakePoyo:
    def __init__(self) -> None:
        self.submits = 0
        self.finished = False

    def __call__(self, method: str, path: str, body: dict | None = None) -> dict:
        if path.endswith("/submit"):
            self.submits += 1
            return {"task_id": f"t{self.submits}"}
        if self.finished:
            return {
                "status": "finished",
                "files": [{"file_url": "https://cdn/v07.mp4"}],
                "credits": 18,
            }
        return {"status": "running"}


def test_a_submitted_key_is_not_paid_for_again_after_a_crash(tmp_path: Path):
    fake = FakePoyo()
    path = tmp_path / "_poyo_runs.json"
    poyo_jobs.Ledger(path, http=fake).submit("v07", "veo3.1-lite-official", {"prompt": "p"})
    assert json.loads(path.read_text())["v07"]["status"] == "submitted"  # saved before polling
    # a new process (after a crash) reads the ledger and does not resubmit
    again = poyo_jobs.Ledger(path, http=fake)
    assert again.submit("v07", "veo3.1-lite-official", {"prompt": "p"}) == "t1"
    assert fake.submits == 1
    fake.finished = True
    assert again.collect(timeout=1, interval=0) == []
    assert (
        again.rows["v07"]["urls"] == ["https://cdn/v07.mp4"] and again.rows["v07"]["credits"] == 18
    )


def test_a_failed_key_can_be_resubmitted(tmp_path: Path):
    fake = FakePoyo()
    jobs = poyo_jobs.Ledger(tmp_path / "l.json", http=fake)
    jobs.submit("v08", "m", {})
    jobs.rows["v08"]["status"] = "failed"
    assert jobs.submit("v08", "m", {}) == "t2"
