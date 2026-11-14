"""Chant x108: clean one cycle, repeat it on a fixed period, synth tanpura/pad/bell locked to that period.

usage: python chant_build.py raw.wav OUTDIR [GAP_S=1.1] [SA_HZ=210]
writes OUTDIR/voice_cycle.wav, voice_108.wav, tanpura.wav, pad.wav, bell.wav, layout.json
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import noisereduce as nr
import numpy as np
import soundfile as sf
from scipy.ndimage import uniform_filter1d
from scipy.signal import butter, lfilter

RAW, OUT = sys.argv[1], Path(sys.argv[2])
GAP = float(sys.argv[3]) if len(sys.argv) > 3 else 1.1
SA = float(sys.argv[4]) if len(sys.argv) > 4 else 210.0
SR = 44100
REPS = 108
INTRO, OUTRO = 5.0, 8.0
OUT.mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(108)

# ---------- 1. clean ----------
y, sr = sf.read(RAW)
assert sr == SR and y.ndim == 1
noise = y[int(0.10 * SR) : int(0.58 * SR)]  # room tone before the first "Om"
y = nr.reduce_noise(y=y, sr=SR, y_noise=noise, stationary=True, prop_decrease=0.7)
sf.write(OUT / "_nr.wav", y.astype(np.float32), SR)
chain = (
    "adeclick=w=55:o=75,highpass=f=75,lowpass=f=14000,"
    "equalizer=f=220:t=q:w=1:g=-1.5,equalizer=f=3200:t=q:w=1.2:g=2,equalizer=f=9000:t=q:w=0.8:g=1,"
    "deesser=i=0.25,acompressor=threshold=-24dB:ratio=2.5:attack=15:release=200:makeup=2"
)
subprocess.run(
    ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(OUT / "_nr.wav"), "-af", chain,
     "-c:a", "pcm_f32le", str(OUT / "_clean.wav")],
    check=True,
)
y, _ = sf.read(OUT / "_clean.wav")

# ---------- 2. cut one cycle at the voice edges ----------
hop = int(0.01 * SR)
rms = np.sqrt(np.convolve(y**2, np.ones(hop) / hop, mode="same"))[::hop]
db = 20 * np.log10(rms + 1e-9)
act = db > db.max() - 40
runs, i = [], 0  # voiced runs, merging dips shorter than 0.4 s (the Om->Saravana breath)
while i < len(act):
    if act[i]:
        j = i
        while j < len(act) and (act[j] or act[j : j + 40].any()):
            j += 1
        runs.append((i, j))
        i = j
    else:
        i += 1
r0, r1 = max(runs, key=lambda r: r[1] - r[0])
a, b = r0 * hop - int(0.04 * SR), r1 * hop + int(0.12 * SR)
cyc = y[max(a, 0) : b].copy()
fi, fo = int(0.015 * SR), int(0.25 * SR)
cyc[:fi] *= np.linspace(0, 1, fi)
cyc[-fo:] *= np.cos(np.linspace(0, np.pi / 2, fo)) ** 2
CL = len(cyc) / SR
P = CL + GAP
sf.write(OUT / "voice_cycle.wav", cyc.astype(np.float32), SR)

# ---------- 3. exact repeats on a fixed grid ----------
TOTAL = INTRO + REPS * P - GAP + OUTRO
N = int(TOTAL * SR)
starts = [INTRO + k * P for k in range(REPS)]
voice = np.zeros(N)
for s in starts:
    i = int(round(s * SR))
    voice[i : i + len(cyc)] += cyc
sf.write(OUT / "voice_108.wav", voice.astype(np.float32), SR)

# singing envelope (1 while voice, 0 in gaps) smoothed, for ducking
tt = np.arange(N) / SR
sing = np.zeros(N)
for s in starts:
    sing[int(s * SR) : int((s + CL) * SR)] = 1
k = int(0.5 * SR)
sing = uniform_filter1d(sing, k)
duck = 1.0 - 0.30 * sing  # ~-3 dB under the voice, blooms in the breath gap


# ---------- 4. tanpura: 8 plucks per chant period, cycle Pa Sa Sa low-Sa ----------
def pluck(f, length):
    n = int(length * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    for h in range(1, 36):
        fk = f * h * (1 + 0.0004 * h * h / 36)
        if fk > 9000:
            break
        out += (1 / h**0.9) * (1 - np.exp(-t / (0.12 + 0.04 * h))) * np.exp(
            -t / max(3.2 - 0.05 * h, 0.7)
        ) * np.sin(2 * np.pi * fk * t + rng.uniform(0, 6.3))
    return out * (1 - np.exp(-t / 0.004))


pat = [(SA * 3 / 4, -0.35, 1.0), (SA, 0.25, 0.62), (SA, 0.4, 0.68), (SA / 2, -0.1, 0.85)]
step = P / 8
PLK = {(n, v): pluck(pat[n][0] * (1 + rng.normal(0, 0.0003)), 5.5) for n in range(4) for v in range(5)}
tan = np.zeros((N + 8 * SR, 2))
t0 = INTRO - 4 * step  # four plucks of lead-in before the first Om
k = 0
while t0 + k * step < TOTAL - 4.0:
    f, pan, vel = pat[k % 4]
    vel *= 0.92 + 0.08 * rng.random()
    p = PLK[(k % 4, int(rng.integers(5)))]
    s = int(max(0.0, t0 + k * step + rng.normal(0, 0.006)) * SR)
    tan[s : s + len(p), 0] += p * vel * np.sqrt((1 - pan) / 2)
    tan[s : s + len(p), 1] += p * vel * np.sqrt((1 + pan) / 2)
    k += 1
tan = tan[:N] * duck[:, None]

# ---------- 5. shruti-like pad: low Sa + Pa, breathes once per period ----------
breath = 0.8 + 0.2 * np.cos(2 * np.pi * ((tt - INTRO) / P - CL / P / 2 - 0.5))
pad = np.zeros((N, 2))
for f, amp in [(SA / 2, 1.0), (SA * 3 / 4, 0.45), (SA, 0.4)]:
    for det, ch in [(-0.5, 0), (0.5, 1)]:
        ph = 2 * np.pi * (f + det) * tt
        pad[:, ch] += amp * (np.sin(ph) + 0.3 * np.sin(2 * ph) + 0.1 * np.sin(3 * ph))
pad *= (breath * duck)[:, None]


# ---------- 6. temple bell (Lingashtakam v11 timbre): before the first chant and after every chant ----------
BELL_KIND = os.environ.get("BELL_KIND", "manjira")  # manjira (user pick, 2026-11-13) | temple
BELL_F = float(os.environ.get("BELL_F", SA * 2))  # temple strike pitch; Lingashtakam used 578 (its Sa 289 x2)


def manjira_ding():
    """Open manjira stroke from the Lingashtakam arrangement (vocal-to-song arrange.py), verbatim."""
    n = int(1.4 * SR)
    x = np.arange(n) / SR
    s = np.zeros(n)
    for f in [2380, 3410, 4720, 5960, 7350, 8810]:
        f *= 1 + rng.normal(0, 0.004)
        s += np.sin(2 * np.pi * f * x + rng.uniform(0, 6.3)) * np.exp(-x / (0.55 * 3000 / f))
    s += 0.6 * rng.normal(0, 1, n) * np.exp(-x / 0.012)
    b, a = butter(2, 1800 / (SR / 2), "high")
    return lfilter(b, a, s) * (1 - np.exp(-x / 0.0008))


def bell_hit():
    """Temple bell from the Lingashtakam v11 arrangement (vocal-to-song arrange.py, stage D), verbatim:
    hum partial an octave under Sa*2, inharmonic upper partials, fresh random phases per strike."""
    n = int(7 * SR)
    x = np.arange(n) / SR
    s = sum(
        a * np.sin(2 * np.pi * BELL_F * r * x + rng.uniform(0, 6.3)) * np.exp(-x / dcy)
        for r, a, dcy in [(0.5, 0.5, 5), (1, 1, 3.5), (1.183, 0.45, 2.5), (1.506, 0.4, 2),
                          (2, 0.3, 1.6), (2.514, 0.25, 1.1), (3.011, 0.15, 0.8)]
    )
    return s * (1 - np.exp(-x / 0.001))


bel = np.zeros((N + 8 * SR, 2))
# v2: one bell right after every chant ends (in the breath gap), plus the opening bell
bell_at = [INTRO - 3.0] + [s + CL + 0.05 for s in starts]
PAN = -0.2
for j, s in enumerate(bell_at):
    p = (manjira_ding() if BELL_KIND == "manjira" else bell_hit()) * (1.0 if j in (0, len(bell_at) - 1) else 0.75)
    i = int(s * SR)
    bel[i : i + len(p), 0] += p * np.sqrt((1 - PAN) / 2)
    bel[i : i + len(p), 1] += p * np.sqrt((1 + PAN) / 2)
bel = bel[:N]

fade_in = np.clip(tt / 3.0, 0, 1)
fade_out = np.clip((TOTAL - tt) / 6.0, 0, 1)
for name, sig in [("tanpura.wav", tan), ("pad.wav", pad * fade_in[:, None] ** 2), ("bell.wav", bel)]:
    sig = sig * (fade_in * fade_out)[:, None] if name != "bell.wav" else sig * fade_out[:, None]
    sf.write(OUT / name, (sig / np.max(np.abs(sig)) * 0.8).astype(np.float32), SR)

layout = dict(sa_hz=SA, bell_kind=BELL_KIND, bell_f=BELL_F if BELL_KIND == "temple" else None, cycle_s=round(CL, 3), gap_s=GAP, period_s=round(P, 3), reps=REPS, intro_s=INTRO,
              outro_s=OUTRO, total_s=round(TOTAL, 3), cut_src_s=[round(a / SR, 3), round(b / SR, 3)],
              starts_s=[round(s, 3) for s in starts], bells_s=[round(s, 3) for s in bell_at])
(OUT / "layout.json").write_text(json.dumps(layout, indent=1))
print({k: v for k, v in layout.items() if k not in ("starts_s",)})
