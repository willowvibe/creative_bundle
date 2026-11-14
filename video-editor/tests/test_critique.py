"""Vision critique: contact sheet, rubric parsing and motion patches (P4.3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from critique.critique import (
    RUBRIC,
    CritiqueError,
    Patch,
    apply_patches,
    build_contact_sheet,
    critique_frames,
    frames_to_images,
)
from PIL import Image
from video_editor_core.content_pack import ContentPack, PackLine, PackScene, PackStill, PackVoice


def _frames(tmp_path: Path, count: int = 4) -> list[Path]:
    paths: list[Path] = []
    for index in range(count):
        path = tmp_path / f"frame_{index:03d}.png"
        Image.new("RGB", (320, 180), (index * 40, 80, 120)).save(path)
        paths.append(path)
    return paths


class _Router:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.images: list[bytes] = []
        self.task = ""

    async def generate(self, task_type: str, prompt: str, **kwargs: object) -> str:
        self.task = task_type
        self.images = list(kwargs.get("images") or [])
        return json.dumps(self.payload)


def _report_payload(**overrides: object) -> dict[str, object]:
    scores = [{"axis": axis, "score": 8, "note": "fine"} for axis in RUBRIC]
    payload: dict[str, object] = {"scores": scores, "summary": "solid"}
    payload.update(overrides)
    return payload


def test_the_contact_sheet_tiles_the_frames(tmp_path: Path) -> None:
    sheet = build_contact_sheet(_frames(tmp_path, 5), tmp_path / "sheet.png", cell=96, columns=3)
    with Image.open(sheet) as image:
        assert image.width == 288
        assert image.height == 192


def test_frames_are_downscaled_and_capped(tmp_path: Path) -> None:
    images = frames_to_images(_frames(tmp_path, 10), max_frames=3, cell=64)
    assert len(images) == 3
    assert images[0].startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_critique_sends_the_contact_sheet_to_the_vision_task(tmp_path: Path) -> None:
    router = _Router(_report_payload())
    report = await critique_frames(_frames(tmp_path, 2), router)
    assert router.task == "critique"
    assert len(router.images) == 2
    assert report.overall == 8.0
    assert report.failing_axes() == []


@pytest.mark.asyncio
async def test_a_low_axis_is_reported(tmp_path: Path) -> None:
    payload = _report_payload()
    payload["scores"][0]["score"] = 5  # type: ignore[index]
    report = await critique_frames(_frames(tmp_path, 2), _Router(payload))
    assert report.failing_axes() == ["hierarchy"]


@pytest.mark.asyncio
async def test_missing_axes_are_refused(tmp_path: Path) -> None:
    payload = _report_payload()
    payload["scores"] = payload["scores"][:-1]  # type: ignore[index]
    with pytest.raises(CritiqueError, match="omitted axes"):
        await critique_frames(_frames(tmp_path, 1), _Router(payload))


@pytest.mark.asyncio
async def test_fenced_json_is_accepted(tmp_path: Path) -> None:
    class Fenced(_Router):
        async def generate(self, task_type: str, prompt: str, **kwargs: object) -> str:
            return "```json\n" + json.dumps(self.payload) + "\n```"

    report = await critique_frames(_frames(tmp_path, 1), Fenced(_report_payload()))
    assert report.overall == 8.0


def _pack() -> ContentPack:
    return ContentPack(
        job_id="demo",
        voice=PackVoice(voice_id="v"),
        scenes=[
            PackScene(
                id="hook",
                still=PackStill(key="a", prompt="a"),
                lines=[PackLine(say="one")],
                motion="drift",
            )
        ],
    )


def test_motion_patches_are_applied_and_others_reported() -> None:
    pack = _pack()
    updated, applied, skipped = apply_patches(
        pack,
        [
            Patch(action="change_motion", target="hook", value="stamp"),
            Patch(action="recolour", target="hook", value="accent"),
        ],
    )
    assert updated.scenes[0].motion == "stamp"
    assert applied == ["hook: motion=stamp"]
    assert skipped == ["recolour hook accent"]
    # The original pack is untouched.
    assert pack.scenes[0].motion == "drift"


@pytest.mark.asyncio
async def test_the_loop_patches_then_stops_on_a_clean_round(tmp_path: Path) -> None:
    low = _report_payload(patches=[{"action": "change_motion", "target": "hook", "value": "stamp"}])
    low["scores"][0]["score"] = 5  # type: ignore[index]
    high = _report_payload()

    class Switching:
        def __init__(self) -> None:
            self.round = 0

        async def generate(self, task_type: str, prompt: str, **kwargs: object) -> str:
            self.round += 1
            return json.dumps(low if self.round == 1 else high)

    builds: list[list[object]] = []

    def build(patches: list[object]) -> tuple[bool, list[str], list[str]]:
        builds.append(patches)
        return True, ["hook: motion=stamp"], []

    from critique.critique import critique_loop

    rounds = await critique_loop(lambda: _frames(tmp_path, 1), Switching(), build, max_rounds=3)

    assert len(rounds) == 2
    assert rounds[0].applied == ["hook: motion=stamp"]
    assert builds and builds[0][0].action == "change_motion"
    assert all(score.score >= 8 for score in rounds[-1].scores)


@pytest.mark.asyncio
async def test_the_loop_stops_when_nothing_can_be_applied(tmp_path: Path) -> None:
    low = _report_payload()
    low["scores"][0]["score"] = 5  # type: ignore[index]

    from critique.critique import critique_loop

    def build(patches: list[object]) -> tuple[bool, list[str], list[str]]:
        return False, [], ["recolour hook accent"]

    rounds = await critique_loop(lambda: _frames(tmp_path, 1), _Router(low), build, max_rounds=3)
    assert len(rounds) == 1
    assert rounds[0].skipped == ["recolour hook accent"]
