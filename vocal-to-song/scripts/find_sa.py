"""Find the singer's own Sa (tonic) and which scale degrees they use.

usage: python find_sa.py raw.wav
Prints: Sa in Hz (median of the most common phrase-ending pitch), a 25-cent histogram of
stable notes relative to Sa (to choose SCALE in tune.py), and tempo.
"""

import sys
from collections import Counter

import librosa
import numpy as np

y, sr = librosa.load(sys.argv[1], sr=22050)
H = 256
f0, v, _ = librosa.pyin(y, fmin=80, fmax=1000, sr=sr, frame_length=2048, hop_length=H)
rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=H)[0][: len(v)]
vv = v & (rms > np.percentile(rms, 25))
dt = H / sr
print(f"duration {len(y) / sr:.1f}s, voiced {v.mean():.0%}, median f0 {np.nanmedian(f0[v]):.1f} Hz")

# phrase endings: last 0.4 s of a voiced run >0.5 s that is followed by a >0.35 s gap
ends, i, n = [], 0, len(vv)
while i < n:
    if not vv[i]:
        i += 1
        continue
    j = i
    while j < n and vv[j]:
        j += 1
    k = j
    while k < n and not vv[k]:
        k += 1
    if (k - j) * dt > 0.35 and (j - i) * dt > 0.5:
        ends.append(np.nanmedian(f0[max(i, j - int(0.4 / dt)) : j]))
    i = j
semis = np.round(12 * np.log2(np.array(ends) / 440)).astype(int)
best, cnt = Counter(semis).most_common(1)[0]
sa = float(np.median([e for e, s in zip(ends, semis, strict=True) if s == best]))
print(
    f"{len(ends)} phrase endings; most common bin hit {cnt}x -> Sa ~ {sa:.1f} Hz "
    f"({1200 * np.log2(sa / 440):+.0f} cents vs A4)"
)

# stable-note histogram relative to Sa
c = 1200 * np.log2(np.where(v, f0, np.nan) / sa)
st = v & (np.abs(np.gradient(np.nan_to_num(c))) < 3)
cs = (c[st] + 12.5) % 1200 - 12.5
h = np.histogram(cs, bins=48, range=(-12.5, 1187.5))[0]
print("stable-note histogram (cents above Sa) -> pick SCALE from the peaks:")
for b, x in enumerate(h):
    if x > h.max() * 0.08:
        print(f"  {b * 25:5d}c {'#' * int(50 * x / h.max())}")

oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=H)
print(
    f"tempo ~ {float(librosa.feature.tempo(onset_envelope=oenv, sr=sr, hop_length=H)[0]):.0f} BPM"
)
