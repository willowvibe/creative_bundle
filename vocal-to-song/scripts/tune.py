"""Drift-aware gentle pitch correction (WORLD vocoder, formant-preserving).

usage: python tune.py in.wav out.wav SA_HZ STRENGTH
"""

import sys

import noisereduce as nr
import numpy as np
import pyworld as pw
import soundfile as sf
from scipy.ndimage import median_filter, uniform_filter1d

src, dst, SA, STRENGTH = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
SCALE = np.array([0, 100, 500, 700, 1000, 1100])  # degrees seen in the stable-note histogram
GRID = np.concatenate([SCALE + 1200 * k for k in range(-3, 3)])

x, sr = sf.read(src)
x = x.astype(np.float64)
# light stationary denoise, noise profile from the quietest 10% of the file
x = nr.reduce_noise(y=x, sr=sr, stationary=True, prop_decrease=0.6)

FP = 5.0  # ms frame period
f0, t = pw.harvest(x, sr, f0_floor=120, f0_ceil=900, frame_period=FP)
f0 = pw.stonemask(x, f0, t, sr)
sp = pw.cheaptrick(x, f0, t, sr)
ap = pw.d4c(x, f0, t, sr)

v = f0 > 0
c = np.full_like(f0, np.nan)
c[v] = 1200 * np.log2(f0[v] / SA)
slope = np.abs(np.gradient(np.nan_to_num(c))) / FP * 1000  # cents/sec
stable = v & (slope < 600)
stable = median_filter(stable.astype(int), 15).astype(bool) & v


def nearest(vals):
    idx = np.abs(vals[:, None] - GRID[None, :]).argmin(1)
    return GRID[idx]


# 1) local drift of the singer's Sa: offset (-60..60c) that best fits the scale, per 6 s window
win = int(6000 / FP)
hopw = int(1000 / FP)
centres, offs = [], []
cands = np.arange(-60, 61, 5)
for s in range(0, len(c), hopw):
    seg = c[max(0, s - win // 2) : s + win // 2][stable[max(0, s - win // 2) : s + win // 2]]
    if len(seg) < 100:
        continue
    cost = [np.mean(np.minimum(np.abs(seg - o - nearest(seg - o)), 60)) for o in cands]
    centres.append(s)
    offs.append(cands[int(np.argmin(cost))])
drift = np.interp(np.arange(len(c)), centres, uniform_filter1d(np.array(offs, float), 5))

# 2) per frame correction on stable frames: intended note = nearest grid after removing drift
corr = np.full_like(f0, np.nan)
cs = c[stable]
target = nearest(cs - drift[stable])
note_part = target - (cs - drift[stable])  # residual intonation error
corr[stable] = -drift[stable] + STRENGTH * note_part
# 3) fill slides/gamakas with interpolated correction -> gesture shape kept, only moved
idx = np.arange(len(corr))
good = ~np.isnan(corr)
corr = np.interp(idx, idx[good], corr[good])
corr = uniform_filter1d(corr, int(60 / FP))  # ~60 ms retune glide, avoids steppy sound

f0n = np.where(v, f0 * 2 ** (corr / 1200), 0)
y = pw.synthesize(f0n, sp, ap, sr, FP)
y = y[: len(x)]
y = y / (np.max(np.abs(y)) + 1e-9) * 0.9
sf.write(dst, y, sr, subtype="PCM_24")
print(
    f"drift range {drift.min():.0f}..{drift.max():.0f}c, mean |note fix| {np.mean(np.abs(note_part)):.1f}c, "
    f"applied mean |corr| {np.mean(np.abs(corr[v])):.1f}c"
)
