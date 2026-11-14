"""Tests for the Jev-backed caption-quality review — no network.

The helper makes a single batched ``classify`` call over every caption line
and returns advisory flag dicts. These tests drive it with a fake client so
they are fast and offline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from captions.captions import CaptionsSkill, review_caption_quality
from captions.models import CaptionLine, CaptionManifest, CaptionWord
from graphics.models import CompositionPlan
from video_editor_state.job import JobContext


class _FakeClassifyClient:
    """Minimal stand-in returning per-line ``noul`` probabilities.

    ``scores`` maps caption text -> ``{flag: probability}``; the flag is read
    from the question key suffix (``too_long`` / ``profanity`` / ``emphasis``).
    """

    def __init__(
        self,
        scores: dict[str, dict[str, float]] | None = None,
        *,
        available: bool = True,
        error: bool = False,
    ) -> None:
        self._scores = scores or {}
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
            text = str(instructions.get("line", ""))
            flag = key.split("_", 1)[-1]
            answers[key] = {"noul": self._scores.get(text, {}).get(flag, 0.0)}
        return {"answers": answers}

    async def close(self) -> None:  # pragma: no cover
        pass


def _line(text: str, start: float, end: float) -> CaptionLine:
    return CaptionLine(words=[CaptionWord(text=text, start=start, end=end)])


def test_flags_only_lines_that_trip_a_check() -> None:
    fake = _FakeClassifyClient(
        {
            "a really long caption line": {
                "too_long": 0.9,
                "profanity": 0.1,
                "emphasis": 0.2,
            },
            "a punchy number 42": {
                "too_long": 0.1,
                "profanity": 0.05,
                "emphasis": 0.8,
            },
            "clean and short": {
                "too_long": 0.1,
                "profanity": 0.05,
                "emphasis": 0.1,
            },
        }
    )
    lines = [
        _line("a really long caption line", 0.0, 3.0),
        _line("a punchy number 42", 3.0, 4.0),
        _line("clean and short", 4.0, 5.0),
    ]

    flags = review_caption_quality(lines, client=fake)

    assert len(flags) == 2
    assert flags[0]["flags"] == ["too_long"]
    assert flags[0]["start"] == 0.0
    assert flags[0]["end"] == 3.0
    assert flags[0]["text"] == "a really long caption line"
    assert "too long" in flags[0]["reason"]
    assert flags[1]["flags"] == ["emphasis"]


def test_review_makes_one_batched_call_for_all_lines() -> None:
    fake = _FakeClassifyClient()
    lines = [_line("one", 0.0, 1.0), _line("two", 1.0, 2.0), _line("three", 2.0, 3.0)]

    review_caption_quality(lines, client=fake)

    assert fake.calls == 1


def test_unavailable_client_returns_empty_without_calling() -> None:
    fake = _FakeClassifyClient(available=False)
    assert review_caption_quality([_line("hello", 0.0, 1.0)], client=fake) == []
    assert fake.calls == 0


def test_error_falls_back_to_empty() -> None:
    fake = _FakeClassifyClient(error=True)
    assert review_caption_quality([_line("hello", 0.0, 1.0)], client=fake) == []


def test_empty_input_makes_no_call() -> None:
    fake = _FakeClassifyClient()
    assert review_caption_quality([], client=fake) == []
    assert fake.calls == 0


def test_accepts_dicts_words_and_strings() -> None:
    fake = _FakeClassifyClient({"hello": {"profanity": 0.99}})
    lines: list[Any] = [
        {"text": "hello", "start": 1.0, "end": 2.0},
        "hello",
        CaptionWord(text="hello", start=3.0, end=4.0),
    ]

    flags = review_caption_quality(lines, client=fake)

    assert len(flags) == 3
    assert [f["flags"] for f in flags] == [["profanity"]] * 3
    assert flags[0]["start"] == 1.0
    assert flags[1]["start"] is None  # bare string has no timing
    assert flags[2]["start"] == 3.0


def test_threshold_filters_low_probabilities() -> None:
    fake = _FakeClassifyClient({"borderline": {"too_long": 0.45}})
    flags = review_caption_quality([_line("borderline", 0.0, 1.0)], client=fake)
    assert flags == []


def test_skill_method_delegates_to_injected_client(tmp_path: Path) -> None:
    fake = _FakeClassifyClient({"hi": {"emphasis": 0.9}})
    ctx = JobContext(
        job_id="captions-jev", work_root=tmp_path / "work", output_root=tmp_path / "out"
    )
    skill = CaptionsSkill(ctx, graphics_skill=object(), classify_client=fake)  # type: ignore[arg-type]

    flags = skill.review_caption_quality([_line("hi", 0.0, 1.0)])

    assert flags[0]["flags"] == ["emphasis"]
    assert fake.calls == 1


def _skill(tmp_path: Path) -> CaptionsSkill:
    ctx = JobContext(
        job_id="captions-jev", work_root=tmp_path / "work", output_root=tmp_path / "out"
    )
    plan = CompositionPlan(
        job_id=ctx.job_id,
        preset_name="short_explainer",
        width=1080,
        height=1920,
        fps=30,
        source_video="x.mp4",
        segments=[],
    )
    ctx.artifact("graphics", "plan.json").write_text(
        json.dumps(plan.model_dump()), encoding="utf-8"
    )
    return CaptionsSkill(ctx, graphics_skill=object())  # type: ignore[arg-type]


def test_manifest_persists_quality_flags(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    notes = [
        {
            "start": 0.0,
            "end": 2.0,
            "text": "a long line",
            "flags": ["too_long"],
            "reason": "too long / hard to read (0.91)",
        }
    ]
    skill._quality_flags = notes

    skill._save_manifest(output="with_captions.mp4")

    saved = json.loads(skill.manifest_path.read_text(encoding="utf-8"))
    assert saved["quality_flags"] == notes
    assert saved["output_video"] == "with_captions.mp4"


def test_manifest_defaults_to_empty_quality_flags(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    skill._save_manifest(output="with_captions.mp4")

    saved = json.loads(skill.manifest_path.read_text(encoding="utf-8"))
    assert saved["quality_flags"] == []
    assert CaptionManifest(job_id="j").quality_flags == []
