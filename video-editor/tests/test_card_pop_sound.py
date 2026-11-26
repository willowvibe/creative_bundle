"""Photo-card pops: pop.wav itself and the cues scheduled from map cards."""

from __future__ import annotations

import math
import re
import struct
import wave
from pathlib import Path

import pytest
from PIL import Image
from video_editor_core.adapters.from_scene_plan import POP_GAIN, from_scene_plan
from video_editor_core.sfx_synth import pop_path, write_pop


def _plan(tmp_path: Path, **flags: bool) -> dict:
    photo = tmp_path / "dish.jpg"
    Image.new("RGB", (800, 600), (200, 120, 40)).save(photo)
    cards = [
        {"image": str(photo), "title": "One", "cue": "alpha"},
        {"image": str(photo), "title": "Two", "cue": "beta"},
    ]
    words = [
        {"word": "alpha", "start": 1.0, "end": 1.4},
        {"word": "beta", "start": 3.0, "end": 3.4},
    ]
    return {
        "job_id": "pops",
        "aspect_ratio": "9:16",
        "design": "night_map",
        **flags,
        "scenes": [
            {"id": "a", "type": "narration", "duration_seconds": 2.0, "props": {}},
            {
                "id": "m",
                "type": "map",
                "duration_seconds": 6.0,
                "props": {
                    "country": "India",
                    "frame": "full",
                    "cards": cards,
                    "word_times": [
                        {**w, "start": w["start"] + 2.0, "end": w["end"] + 2.0} for w in words
                    ],
                },
            },
        ],
    }


def _pops(plan: dict):
    return [c for c in from_scene_plan(plan).audio if "card_pop" in c.id]


def _card_starts(plan: dict) -> list[float]:
    comp = from_scene_plan(plan)
    markup = "".join(layer.content.text or "" for scene in comp.scenes for layer in scene.layers)
    return [float(m) for m in re.findall(r'data-card-start="([\d.]+)"', markup)]


def test_one_pop_per_card_at_scene_start_plus_entry(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    starts = _card_starts(plan)
    assert len(starts) == 2 and starts[0] < starts[1]
    pops = _pops(plan)
    assert [round(p.start, 3) for p in pops] == [round(2.0 + s, 3) for s in starts]
    assert all(abs(p.volume - POP_GAIN) < 1e-9 and p.source == str(pop_path()) for p in pops)
    assert len({p.track for p in pops}) == 2 and all(p.duration <= 0.12 + 1e-9 for p in pops)


def test_a_map_without_cards_gets_no_pops(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    del plan["scenes"][1]["props"]["cards"]
    assert _pops(plan) == []


@pytest.mark.parametrize("flags", [{"tally_sound": False}, {"card_sound": False}])
def test_flags_disable_pops(tmp_path: Path, flags: dict) -> None:
    assert _pops(_plan(tmp_path, **flags)) == []


def test_card_sound_false_keeps_other_ui_sounds(tmp_path: Path) -> None:
    plan = _plan(tmp_path, card_sound=False)
    plan["scenes"].append(
        {
            "id": "t",
            "type": "typographic",
            "duration_seconds": 3.0,
            "props": {"layout": "big_stat", "headline": "152", "sub": "plates"},
        }
    )
    ids = {c.id for c in from_scene_plan(plan).audio}
    assert any(i.endswith("_swoosh") for i in ids) and any(i.endswith("_hit") for i in ids)


def test_pop_wav_is_short_and_never_clips(tmp_path: Path) -> None:
    path = write_pop(tmp_path / "pop.wav")
    for wav_path in (path, pop_path()):
        with wave.open(str(wav_path), "rb") as wav:
            rate, count = wav.getframerate(), wav.getnframes()
            samples = struct.unpack(f"<{count}h", wav.readframes(count))
        assert abs(count / rate - 0.12) < 0.005
        assert 20 * math.log10(max(abs(s) for s in samples) / 32768) < 0.0
