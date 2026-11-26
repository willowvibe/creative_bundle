"""Coin sounds under a counting tally: the WAVs and the audio cues."""

from __future__ import annotations

import math
import struct
import wave

import pytest
from video_editor_core.adapters.from_scene_plan import COIN_GAIN, from_scene_plan
from video_editor_core.sfx_synth import coin_path, write_coin
from video_editor_core.tally_chip import COUNT_DELAY_SECONDS


def _read(path):
    with wave.open(str(path), "rb") as wav:
        frames = wav.readframes(wav.getnframes())
        info = (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getnframes())
    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
    return info, samples


@pytest.mark.parametrize("kind", ["in", "out"])
def test_bundled_coin_wavs_are_48k_mono_short_and_under_the_ceiling(kind):
    (rate, channels, width, count), samples = _read(coin_path(kind))
    assert (rate, channels, width) == (48_000, 1, 2)
    assert 0.3 <= count / rate <= 0.6
    peak_db = 20 * math.log10(max(abs(s) for s in samples) / 32768)
    assert -6.0 <= peak_db <= -1.0


def test_coin_out_is_lower_and_softer_than_coin_in():
    _, ins = _read(coin_path("in"))
    _, outs = ins, _read(coin_path("out"))[1]
    assert max(map(abs, outs)) < max(map(abs, ins))


def test_the_synth_is_deterministic(tmp_path):
    again = write_coin(tmp_path / "x.wav", "in")
    assert again.read_bytes() == coin_path("in").read_bytes()


def _plan(values, **extra):
    plan = {
        "job_id": "coin_job",
        "aspect_ratio": "16:9",
        "scenes": [
            {
                "id": f"s{i}",
                "type": "narration",
                "duration_seconds": 4.0,
                "props": {"tally": {"label": "cash", "value": value}},
            }
            for i, value in enumerate(values)
        ],
    }
    plan.update(extra)
    return plan


def _coins(composition):
    return [cue for cue in composition.audio if cue.id.endswith("_tally_coin")]


def test_an_increase_gets_one_coin_in_at_scene_start_plus_delay():
    coins = _coins(from_scene_plan(_plan([0, 12000])))
    assert len(coins) == 1  # the opening tally has nothing to count from
    cue = coins[0]
    assert cue.id == "scene_1_tally_coin"
    assert cue.source == str(coin_path("in"))
    assert cue.start == pytest.approx(4.0 + COUNT_DELAY_SECONDS)
    assert cue.volume == pytest.approx(COIN_GAIN)
    assert 0 < cue.duration <= 0.5


def test_a_decrease_gets_coin_out():
    (cue,) = _coins(from_scene_plan(_plan([5000, 3500])))
    assert cue.source == str(coin_path("out"))


def test_an_unchanged_tally_gets_no_cue():
    assert _coins(from_scene_plan(_plan([100, 100, 100]))) == []


def test_tally_sound_false_gets_none():
    assert _coins(from_scene_plan(_plan([0, 12000], tally_sound=False))) == []


def test_the_cue_follows_the_calc_delay_used_by_the_chip():
    plan = _plan([0, 12000])
    plan["scenes"][1]["duration_seconds"] = 10.0
    plan["scenes"][1]["props"].update(
        calc=[{"label": "Sales", "expr": "300 × ₹40", "amount": 12000}],
        word_times=[{"word": "₹12,000.", "start": 5.5, "end": 6.0}],
    )
    composition = from_scene_plan(plan)
    (cue,) = _coins(composition)
    chip = next(layer for layer in composition.scenes[1].layers if layer.preset == "tally")
    count_at = float(chip.content.text.split("linear ")[1].split("s")[0])
    assert cue.start == pytest.approx(4.0 + count_at)  # result 3.5 s into the clip + 1.0 s


def test_coin_cues_reach_the_page_audio(tmp_path):
    from video_editor_core.scene_plan_composition import compile_scene_plan_project

    plan = _plan([0, 12000])
    index = compile_scene_plan_project(plan, tmp_path / "proj")
    html_text = index.read_text(encoding="utf-8") if hasattr(index, "read_text") else ""
    assert 'id="scene_1_tally_coin"' in html_text
    assert (tmp_path / "proj" / "assets" / "coin_in.wav").exists()
