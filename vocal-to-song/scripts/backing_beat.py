"""Tempo-following tanpura + breathing pad, aligned to the vocal's beats and phrase cycle.

usage: python backing_beat.py vocal.wav SA_HZ INTRO_S TOTAL_S
writes tanpura_beat.wav, pad_beat.wav
"""

import sys

import librosa
import numpy as np
import soundfile as sf
from scipy.ndimage import uniform_filter1d

VOC, SA, INTRO, TOTAL = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
SR = 44100
N = int(TOTAL * SR)
rng = np.random.default_rng(11)

# --- beats from the vocal (tracked, so it follows the slow-down) ---
y, sr = librosa.load(VOC, sr=22050)
oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=256)
_, bf = librosa.beat.beat_track(
    onset_envelope=oenv, sr=sr, hop_length=256, start_bpm=100, tightness=200
)
beats = librosa.frames_to_time(bf, sr=sr, hop_length=256) + INTRO
ibi0, ibi1 = np.median(np.diff(beats[:8])), np.median(np.diff(beats[-8:]))
pre = beats[0] - ibi0 * np.arange(1, 40)[::-1]
post = beats[-1] + ibi1 * np.cumsum(np.linspace(1.0, 1.25, 20))  # gentle ritardando into the ending
beats = np.concatenate([pre[pre > 0], beats, post[post < TOTAL]])

# --- downbeat phase: which beat (mod 8) phrase starts land on ---
rms = librosa.feature.rms(y=y, hop_length=256)[0]
quiet = rms < np.percentile(rms, 90) * 0.06
t = np.arange(len(quiet)) * 256 / sr
starts = [t[i] + INTRO for i in range(1, len(quiet)) if quiet[i - 1] and not quiet[i]]
votes = np.zeros(8)
for s in starts:
    k = int(np.argmin(np.abs(beats - s)))
    votes[k % 8] += 1
phase = int(np.argmax(votes))
print(
    f"{len(beats)} beats, ~{60 / np.median(np.diff(beats)):.0f} BPM, downbeat phase {phase}, votes {votes.astype(int)}"
)

# --- singing envelope (for ducking): tanpura dips under voice, blooms in gaps ---
env_v = np.interp(
    np.arange(N) / SR, t + INTRO, uniform_filter1d((~quiet).astype(float), int(0.4 * sr / 256))
)
duck = 1.0 - 0.35 * env_v  # ~ -3.7 dB while singing


def pluck(f, length):
    n = int(length * SR)
    tt = np.arange(n) / SR
    out = np.zeros(n)
    for k in range(1, 36):
        fk = f * k * (1 + 0.0004 * k * k / 36)
        if fk > 9000:
            break
        bloom = 0.12 + 0.04 * k
        dec = max(2.2 - 0.04 * k, 0.5)  # shorter than before so each beat is felt
        out += (
            (1 / k**0.9)
            * (1 - np.exp(-tt / bloom))
            * np.exp(-tt / dec)
            * np.sin(2 * np.pi * fk * tt + rng.uniform(0, 6.3))
        )
    return out * (1 - np.exp(-tt / 0.003))


# per beat in the 8-beat cycle: (freq, pan, velocity). Pa on 1, Sa Sa, low Sa on 4; repeat with lighter 5
cycle = [
    (SA * 3 / 4, -0.35, 1.0),
    (SA, 0.25, 0.62),
    (SA, 0.4, 0.68),
    (SA / 2, -0.1, 0.85),
    (SA * 3 / 4, -0.35, 0.8),
    (SA, 0.25, 0.6),
    (SA, 0.4, 0.66),
    (SA / 2, -0.1, 0.8),
]
tan = np.zeros((N + 6 * SR, 2))
for i, b in enumerate(beats):
    f, pan, vel = cycle[(i - phase) % 8]
    p = pluck(f * (1 + rng.normal(0, 0.0003)), 4.5)
    s = int(max(0, b + rng.normal(0, 0.008)) * SR)  # tiny human timing jitter
    vel *= 0.92 + 0.08 * rng.random()
    tan[s : s + len(p), 0] += p * vel * np.sqrt((1 - pan) / 2)
    tan[s : s + len(p), 1] += p * vel * np.sqrt((1 + pan) / 2)
tan = tan[:N] * duck[:, None]

# pad breathes once per 8-beat cycle (swells toward each downbeat)
tt = np.arange(N) / SR
down = beats[(np.arange(len(beats)) - phase) % 8 == 0]
breath = np.zeros(N)
for d0, d1 in zip(down[:-1], down[1:], strict=True):
    a, b = int(d0 * SR), int(d1 * SR)
    x = np.linspace(0, 1, b - a, endpoint=False)
    breath[a:b] = 0.75 + 0.25 * np.cos(2 * np.pi * x)
breath[breath == 0] = 0.75
pad = np.zeros((N, 2))
for f, amp in [(SA / 2, 1.0), (SA * 3 / 4, 0.45), (SA, 0.5), (SA * 1.5, 0.12)]:
    for det, ch in [(-0.6, 0), (0.6, 1)]:
        pad[:, ch] += amp * (
            np.sin(2 * np.pi * (f + det) * tt) + 0.25 * np.sin(4 * np.pi * (f + det) * tt)
        )
pad *= (breath * duck)[:, None]

fade = np.clip(tt / 3.0, 0, 1) * np.clip((TOTAL - tt) / 5.0, 0, 1)
for name, sig in [
    ("tanpura_beat.wav", tan * fade[:, None]),
    ("pad_beat.wav", pad * (fade**2)[:, None]),
]:
    sf.write(name, (sig / np.max(np.abs(sig)) * 0.8).astype(np.float32), SR)
