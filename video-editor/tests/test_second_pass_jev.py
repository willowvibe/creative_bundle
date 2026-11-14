"""Tests for the Jev-backed per-edit critique in second_pass — no network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from second_pass.models import SegmentEdit
from second_pass.second_pass import SecondPassSkill
from video_editor_core.timeline import EDL, compute_timeline_segments
from video_editor_state.job import JobContext


class _FakeClassifyClient:
    """Minimal stand-in for ``ClassifyClient`` returning per-edit answers."""

    def __init__(
        self,
        improves: dict[str, float] | None = None,
        issues: dict[str, str] | None = None,
        available: bool = True,
        error: bool = False,
    ) -> None:
        self._improves = improves or {}
        self._issues = issues or {}
        self._available = available
        self._error = error
        self.calls = 0

    def available(self) -> bool:
        return self._available

    async def classify(
        self, state: Any, questions: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        self.calls += 1
        if self._error:
            raise RuntimeError("classify exploded")
        answers: dict[str, Any] = {}
        for key, question in questions.items():
            instructions = question.get("instructions") or {}
            edit = str(instructions.get("edit", ""))
            if key.startswith("improves"):
                answers[key] = {"noul": self._improves.get(edit, 0.5)}
            else:
                answers[key] = {"choice": self._issues.get(edit, "none")}
        return {"answers": answers}

    async def close(self) -> None:  # pragma: no cover
        pass


def _skill(tmp_path: Path, client: Any = None) -> SecondPassSkill:
    ctx = JobContext(
        job_id="second-pass-job",
        work_root=tmp_path / "work",
        output_root=tmp_path / "out",
    )
    # The critique path never touches graphics_skill, so a stub is enough.
    return SecondPassSkill(ctx, graphics_skill=object(), classify_client=client)  # type: ignore[arg-type]


def _edl() -> EDL:
    segments = compute_timeline_segments([(0.0, 3.0, "intro"), (4.0, 8.0, "outro")])
    return EDL(job_id="second-pass-job", source_path="locked.mp4", segments=segments)


def _edits() -> list[SegmentEdit]:
    return [
        SegmentEdit(segment_id="seg_0000", new_source_end=0.8, reason="tighten intro"),
        SegmentEdit(segment_id="seg_0001", new_source_start=5.0, reason="drop dead air"),
    ]


def test_critique_returns_one_note_per_edit(tmp_path: Path) -> None:
    fake = _FakeClassifyClient(
        improves={"seg_0000": 0.9, "seg_0001": 0.2},
        issues={"seg_0000": "none", "seg_0001": "pacing-slow"},
    )
    skill = _skill(tmp_path, fake)
    notes = skill._critique_edits_with_jev(_edits(), _edl())

    assert len(notes) == 2
    assert notes[0]["segment_id"] == "seg_0000"
    assert notes[0]["issue"] == "none"
    assert notes[0]["improves_pacing"] == 0.9
    assert notes[0]["advisory"] is False
    assert notes[1]["issue"] == "pacing-slow"
    assert notes[1]["advisory"] is True


def test_critique_uses_one_batched_call(tmp_path: Path) -> None:
    fake = _FakeClassifyClient()
    skill = _skill(tmp_path, fake)
    skill._critique_edits_with_jev(_edits(), _edl())

    assert fake.calls == 1


def test_critique_empty_edits_makes_no_call(tmp_path: Path) -> None:
    fake = _FakeClassifyClient()
    skill = _skill(tmp_path, fake)

    assert skill._critique_edits_with_jev([], _edl()) == []
    assert fake.calls == 0


def test_critique_unavailable_returns_empty(tmp_path: Path) -> None:
    fake = _FakeClassifyClient(available=False)
    skill = _skill(tmp_path, fake)

    assert skill._critique_edits_with_jev(_edits(), _edl()) == []
    assert fake.calls == 0


def test_critique_error_falls_back_to_empty(tmp_path: Path) -> None:
    fake = _FakeClassifyClient(error=True)
    skill = _skill(tmp_path, fake)

    assert skill._critique_edits_with_jev(_edits(), _edl()) == []


def test_critique_unknown_issue_normalizes_to_none(tmp_path: Path) -> None:
    fake = _FakeClassifyClient(
        improves={"seg_0000": 0.7},
        issues={"seg_0000": "banana"},
    )
    skill = _skill(tmp_path, fake)
    notes = skill._critique_edits_with_jev([_edits()[0]], _edl())

    assert notes[0]["issue"] == "none"
    assert notes[0]["advisory"] is False


def test_manifest_persists_critique(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    skill._edits = _edits()
    notes = [{"segment_id": "seg_0000", "issue": "none", "advisory": False}]

    skill._save_manifest(output="final.mp4", critique=notes)

    saved = json.loads(skill.manifest_path.read_text(encoding="utf-8"))
    assert saved["critique"] == notes
    assert saved["output_video"] == "final.mp4"


def test_manifest_defaults_to_empty_critique(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    skill._save_manifest(output="final.mp4")

    saved = json.loads(skill.manifest_path.read_text(encoding="utf-8"))
    assert saved["critique"] == []
