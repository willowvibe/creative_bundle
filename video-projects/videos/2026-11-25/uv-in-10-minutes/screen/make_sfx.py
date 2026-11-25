"""Sound-effects layer for the uv video (EP2; copied from EP1), timed from the same scene timeline as the screen track.

usage: uv run python make_sfx.py WORDS.json OUTPUTS.json OUT_DIR [--offset 4.0] [--duration 321.2]

Writes OUT_DIR/sfx_events.json (what plays when) and OUT_DIR/sfx.wav (stereo, 44.1 kHz, already at mix level).
Sounds are synthesized here (no downloads, no licence questions):
- whoosh_in : web page zooms in, or a page scene starts (band-limited noise, rising sweep, left->right pan)
- whoosh_out: web page zooms out or scrolls far (falling sweep, right->left)
- swish     : a section title card slides in (shorter, brighter)
- output    : terminal output appears: soft pop (sfx_options A) or two-tone UI blip (B), picked at random per
              event (seeded, never the same one three times in a row); the user chose A+B over the bell ting
Guidance followed (see README): voice stays on top; one sound per real change, nothing on every overlay.
`--offset` shifts everything by the intro length when mixing into the final cut.
"""

from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import random  # noqa: E402

import render_scenes as rs  # noqa: E402
import sfx_options as opt  # noqa: E402

SR = 44100
GAIN = {"whoosh_in": 0.16, "whoosh_out": 0.13, "swish": 0.12, "pop": 0.16, "blip": 0.10}  # mix level relative to full scale


def events(words: list, outputs: dict) -> list[tuple[float, str]]:
    scenes = rs.build(words, outputs)
    out: list[tuple[float, str]] = []
    for k, (start, obj) in enumerate(scenes):
        if isinstance(obj, rs.Web):
            if k > 0:
                out.append((start, "whoosh_in"))  # entering a page
            for prev, key in zip(obj.keys, obj.keys[1:]):
                if key[1] != prev[1]:
                    continue
                dz, dy = key[3] - prev[3], key[2] - prev[2]
                if dz > 0.05:
                    out.append((key[0], "whoosh_in"))
                elif dz < -0.05 or abs(dy) > 600:
                    out.append((key[0], "whoosh_out"))
        elif isinstance(obj, rs.Code):
            for ev in obj.events:
                if ev[0] == "out":
                    _key, delay, _keep = ev[2]
                    out.append((ev[1] + delay, "output"))
        elif isinstance(obj, rs.Race):  # a race result lands
            for _pane, _key, _tc, done, _label in obj.runs:
                out.append((done, "output"))
        elif isinstance(obj, rs.Palette):  # interpreter picked
            out.append((obj.t_pick, "output"))
    for start, *_ in rs.OVERLAY["sections"]:
        out.append((start, "swish"))
    out.sort()
    merged: list[tuple[float, str]] = []  # never stack two sounds within 0.35 s: keep the first
    for t, name in out:
        if merged and t - merged[-1][0] < 0.35:
            continue
        merged.append((t, name))
    rng = random.Random(315)
    final: list[tuple[float, str]] = []
    for t, name in merged:
        if name == "output":
            last = [n for _, n in final if n in ("pop", "blip")][-2:]
            pick = rng.choice(["pop", "blip"])
            if len(last) == 2 and last[0] == last[1] == pick:
                pick = "blip" if pick == "pop" else "pop"
            name = pick
        final.append((t, name))
    return final


def sweep_noise(dur: float, f0: float, f1: float, seed: int) -> np.ndarray:
    """Noise through a moving band-pass (cheap: a resonant one-pole pair whose cutoff glides f0 -> f1)."""
    n = int(SR * dur)
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n)
    f = np.geomspace(f0, f1, n)
    y = np.zeros(n)
    lp = bp = 0.0
    q = 0.35
    for i in range(n):  # state-variable filter
        g = 2 * np.sin(np.pi * f[i] / SR)
        hp = x[i] - lp - q * bp
        bp += g * hp
        lp += g * bp
        y[i] = bp
    env = np.sin(np.linspace(0, np.pi, n)) ** 1.6
    return y / (np.max(np.abs(y)) or 1) * env


def stereo_pan(mono: np.ndarray, left_to_right: bool) -> np.ndarray:
    p = np.linspace(0.2, 0.8, len(mono)) if left_to_right else np.linspace(0.8, 0.2, len(mono))
    return np.stack([mono * np.cos(p * np.pi / 2), mono * np.sin(p * np.pi / 2)], axis=1)


def bell(dur: float = 0.9) -> np.ndarray:
    t = np.arange(int(SR * dur)) / SR
    env = np.minimum(1, t / 0.003)
    s = (np.exp(-5.5 * t) * np.sin(2 * np.pi * 1760 * t)
         + 0.45 * np.exp(-8 * t) * np.sin(2 * np.pi * 1760 * 2.76 * t)
         + 0.25 * np.exp(-12 * t) * np.sin(2 * np.pi * 1760 * 5.40 * t))
    s = env * s / np.max(np.abs(s))
    return np.stack([s, s], axis=1)


def sounds() -> dict[str, np.ndarray]:
    return {
        "whoosh_in": stereo_pan(sweep_noise(0.55, 350, 4200, 1), True),
        "whoosh_out": stereo_pan(sweep_noise(0.5, 3800, 300, 2), False),
        "swish": stereo_pan(sweep_noise(0.28, 1200, 7000, 3), True),
        "pop": np.repeat(opt.pop()[:, None], 2, axis=1),
        "blip": np.repeat(opt.blip()[:, None], 2, axis=1),
    }


def write_wav(path: Path, stereo: np.ndarray) -> None:
    data = (np.clip(stereo, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("words")
    ap.add_argument("outputs")
    ap.add_argument("out")
    ap.add_argument("--offset", type=float, default=0.0)
    ap.add_argument("--duration", type=float, required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    evs = events(json.load(open(a.words)), json.load(open(a.outputs)))
    lib = sounds()
    track = np.zeros((int(SR * a.duration), 2))
    for t, name in evs:
        clip = lib[name] * GAIN[name]
        # whooshes peak at the middle of the clip: start them a little early so the peak lands on the move
        start = t + a.offset - (0.18 if name.startswith("whoosh") else 0.0)
        i = max(0, int(start * SR))
        n = min(len(clip), len(track) - i)
        track[i:i + n] += clip[:n]
    write_wav(out / "sfx.wav", track)
    (out / "sfx_events.json").write_text(json.dumps([{"t": round(t + a.offset, 3), "sound": n} for t, n in evs],
                                                    indent=1))
    counts: dict[str, int] = {}
    for _, n in evs:
        counts[n] = counts.get(n, 0) + 1
    print(len(evs), "events", counts)


if __name__ == "__main__":
    main()
