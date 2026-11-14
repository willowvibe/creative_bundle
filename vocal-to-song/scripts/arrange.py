"""Stage-by-stage arrangement on the vocal's beat grid.

usage: python arrange.py STAGE OUTDIR      (STAGE: A B C D)
  A  v4 + vocal doubles on the refrain lines
  B  A + manjira + soft mridangam
  C  B + intro / interludes / outro with flute (vocal timeline opened up)
  D  C + arrangement build (sparse verse 1, layers enter later), bells, soft ending
Inputs in cwd: vocal_tuned.wav, beats_vocal.npy. Writes stems to OUTDIR.
Env: DRONE=tanpura|veena|sitar picks the beat instrument.

SONG-SPECIFIC CONSTANTS (set these per song; values below are Sruthi's Lingashtakam):
  SA, SCALE  - from find_sa.py
  REFRAINS   - beat index of each refrain line (structure analysis, see SKILL.md)
  CUTS       - {beat index of verse start: cut time in s inside the breath gap before it}
  pos_of()   - lines start at beat index = 1 (mod 8) in this song
"""

import os
import sys

import numpy as np
import pyworld as pw
import soundfile as sf
from scipy.ndimage import uniform_filter1d
from scipy.signal import butter, lfilter

STAGE, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
SR = 44100
SA = 289.0
SCALE = [0, 100, 500, 700, 1000, 1100]
rng = np.random.default_rng(5)
lvl = "ABCD".index(STAGE)

voc, vsr = sf.read("vocal_tuned.wav")
assert vsr == SR
vb = np.load("beats_vocal.npy")  # old (vocal-time) beats; line starts at idx = 1 mod 8
ibi_start, ibi_end = np.median(np.diff(vb[:8])), np.median(np.diff(vb[-8:]))
REFRAINS = [25 + 32 * k for k in range(8)]  # 4th line of each verse
CUTS = (
    {65: 39.30, 129: 77.95, 193: 117.70} if lvl >= 2 else {}
)  # interlude before these beats (cut in the breath gap)


def pos_of(idx):
    return (idx - 1) % 8


# ---------- timeline: old vocal time -> new song time ----------
if lvl >= 2:
    N_INTRO = 15
    base_off = N_INTRO * ibi_start  # intro beats start at old beat-0 time
else:
    N_INTRO = 0
    base_off = 3.0
seg_bounds = sorted(CUTS.values())
interlude_len = {k: 8 * np.median(np.diff(vb[k - 8 : k + 1])) for k in CUTS}
offsets = [base_off]
for k in sorted(CUTS):
    offsets.append(offsets[-1] + interlude_len[k])


def remap(t_old):
    t_old = np.asarray(t_old, float)
    seg = np.searchsorted(seg_bounds, t_old, side="right")
    return t_old + np.array(offsets)[seg]


beats, poss, tags = [], [], []  # new beat times, position in 8-beat cycle, section tag
if N_INTRO:
    for j in range(N_INTRO):
        beats.append(vb[0] + j * ibi_start)
        poss.append(pos_of(j - N_INTRO))
        tags.append("intro")
else:
    pre = remap(vb[0]) - ibi_start * np.arange(1, 12)[::-1]
    for j, tb in enumerate(pre[pre > 0]):
        beats.append(tb)
        poss.append(pos_of(-len(pre[pre > 0]) + j))
        tags.append("intro")
for i, tb in enumerate(vb):
    if i in CUTS:
        ib = interlude_len[i] / 8
        start = vb[i] + offsets[sorted(CUTS).index(i)]
        for j in range(8):
            beats.append(start + j * ib)
            poss.append(j)
            tags.append(f"inter{i}")
    beats.append(float(remap(tb)))
    poss.append(pos_of(i))
    tags.append(f"v{min(8, i // 32 + 1)}" if i >= 1 else "v1")
N_OUT = 12 if lvl >= 2 else 6
t = beats[-1]
for j in range(N_OUT):
    t += ibi_end * (1 + 0.35 * (j / N_OUT) ** 2)  # ritardando
    beats.append(t)
    poss.append(pos_of(len(vb) + j))
    tags.append("outro")
beats, poss = np.array(beats), np.array(poss)
TOTAL = beats[-1] + 7.0
N = int(TOTAL * SR)
tt = np.arange(N) / SR
print(
    f"stage {STAGE}: {len(beats)} beats, total {TOTAL:.1f}s, intro beats {N_INTRO}, interludes {list(CUTS)}"
)


def place(dst, sig, t0, gain=1.0, pan=0.0):
    s = int(t0 * SR)
    if s >= len(dst) or s + len(sig) <= 0:
        return
    if s < 0:
        sig, s = sig[-s:], 0
    e = min(len(dst), s + len(sig))
    seg = sig[: e - s] * gain
    if dst.ndim == 2:
        dst[s:e, 0] += seg * np.sqrt((1 - pan) / 2)
        dst[s:e, 1] += seg * np.sqrt((1 + pan) / 2)
    else:
        dst[s:e] += seg


def remap_audio(x):
    """Cut the vocal-time signal at CUTS and lay it on the new timeline."""
    out = np.zeros((N,) + x.shape[1:])
    edges = [0.0] + seg_bounds + [len(x) / SR]
    for k in range(len(edges) - 1):
        a, b = int(edges[k] * SR), int(edges[k + 1] * SR)
        piece = x[a:b].copy()
        fl = int(0.01 * SR)  # 10 ms fades at the cut points
        if len(piece) > 2 * fl:
            ramp = np.linspace(0, 1, fl)
            piece[:fl] *= ramp if piece.ndim == 1 else ramp[:, None]
            piece[-fl:] *= ramp[::-1] if piece.ndim == 1 else ramp[::-1, None]
        s = int((edges[k] + offsets[k]) * SR)
        out[s : s + len(piece)] += piece[: max(0, N - s)]
    return out


# ---------- vocal main + doubles ----------
voc_new = remap_audio(voc)
sing = uniform_filter1d((np.abs(voc_new) > 0.02).astype(float), int(0.35 * SR))
sing = np.clip(sing * 3, 0, 1)

dbl_path = "dbl_vocaltime.npz"
if not os.path.exists(dbl_path):
    x = voc.astype(np.float64)
    f0, ft = pw.harvest(x, SR, f0_floor=120, f0_ceil=900, frame_period=5.0)
    sp, ap = pw.cheaptrick(x, f0, ft, SR), pw.d4c(x, f0, ft, SR)
    outs = []
    for seed, delay in [(1, 0.019), (2, 0.031)]:
        r = np.random.default_rng(seed)
        drift = uniform_filter1d(r.normal(0, 1, len(f0)), 200)
        drift = drift / (np.std(drift) + 1e-9) * 7  # +-7 cents slow wander, like a second singer
        y = pw.synthesize(np.where(f0 > 0, f0 * 2 ** (drift / 1200), 0), sp * 0.95, ap, SR, 5.0)[
            : len(x)
        ]
        y = np.concatenate([np.zeros(int(delay * SR)), y])[: len(x)]
        outs.append(y)
    np.savez(dbl_path, L=outs[0], R=outs[1])
d = np.load(dbl_path)
gate = np.zeros(len(voc))
for r in REFRAINS:
    a, b = vb[r] - 0.35, vb[min(r + 8, len(vb) - 1)] + 0.6
    gate[int(a * SR) : int(b * SR)] = 1.0
if lvl >= 3:  # whole last verse doubled, quieter
    gate[int((vb[225] - 0.35) * SR) :] = np.maximum(gate[int((vb[225] - 0.35) * SR) :], 0.6)
gate = uniform_filter1d(gate, int(0.15 * SR))
dbl = np.stack([d["L"] * gate, d["R"] * gate], 1)
dbl_new = remap_audio(dbl)
dbl_new[:, 0], dbl_new[:, 1] = (
    dbl_new[:, 0] * 0.8 + dbl_new[:, 1] * 0.2,
    dbl_new[:, 1] * 0.8 + dbl_new[:, 0] * 0.2,
)

# ---------- section gains (stage D builds the arrangement) ----------
sec_of_time = np.array(tags)[np.clip(np.searchsorted(beats, tt) - 1, 0, len(beats) - 1)]


def section_gain(rules):
    g = np.ones(N)
    if lvl < 3:
        return g
    for tag, val in rules.items():
        g[sec_of_time == tag] = val
    return uniform_filter1d(g, int(1.5 * SR))


# ---------- tanpura + pad (same voice as v4, new beats) ----------
def pluck(f, length=4.5):
    n = int(length * SR)
    x = np.arange(n) / SR
    out = np.zeros(n)
    for k in range(1, 36):
        fk = f * k * (1 + 0.0004 * k * k / 36)
        if fk > 9000:
            break
        out += (
            (1 / k**0.9)
            * (1 - np.exp(-x / (0.12 + 0.04 * k)))
            * np.exp(-x / max(2.2 - 0.04 * k, 0.5))
            * np.sin(2 * np.pi * fk * x + rng.uniform(0, 6.3))
        )
    return out * (1 - np.exp(-x / 0.003))


DRONE = os.environ.get("DRONE", "tanpura")


def veena_pluck(f, length=3.5):
    """Saraswati veena tala-string stroke: warm body, bright attack, little buzz, faster decay than tanpura."""
    n = int(length * SR)
    x = np.arange(n) / SR
    out = np.zeros(n)
    for k in range(1, 24):
        fk = f * k * (1 + 0.0003 * k * k / 24)
        if fk > 8000:
            break
        amp = (1 / k**1.1) * (
            1.6 if 2 <= k <= 4 else 1.0
        )  # big wooden resonator lifts low harmonics
        dec = max(1.9 / k**0.45, 0.18)
        bloom = (
            1 - 0.5 * np.exp(-x / 0.08) if k > 6 else 1
        )  # mild bridge buzz only on upper partials
        out += amp * bloom * np.exp(-x / dec) * np.sin(2 * np.pi * fk * x + rng.uniform(0, 6.3))
    out *= 1 + 0.004 * np.exp(-x / 0.25)  # tiny pitch sag on attack (felt as pluck 'give')
    click = rng.normal(0, 1, n) * np.exp(-x / 0.004) * 0.15
    return (out + click) * (1 - np.exp(-x / 0.0015))


TARAF = [SA * 2 ** (c / 1200) for c in (0, 100, 500, 700, 1000, 1200, 1700, 1900)]


def sitar_pluck(f, length=4.0, taraf=True):
    """Sitar: strong jawari buzz (upper partials swell after the attack) + sympathetic taraf shimmer."""
    n = int(length * SR)
    x = np.arange(n) / SR
    out = np.zeros(n)
    for k in range(1, 40):
        fk = f * k * (1 + 0.0005 * k * k / 40)
        if fk > 10000:
            break
        bloom = (1 - np.exp(-x / (0.02 + 0.012 * k))) * (1 + 1.5 * np.exp(-x / 0.6)) if k > 3 else 1
        out += (
            (1 / k**0.75)
            * bloom
            * np.exp(-x / max(2.6 - 0.05 * k, 0.35))
            * np.sin(2 * np.pi * fk * x + rng.uniform(0, 6.3))
        )
    if taraf:  # sympathetic strings ring softly after each stroke
        for ft in TARAF:
            if any(abs(1200 * np.log2(ft / (f * h))) < 30 for h in (1, 2, 3, 4)):
                out += (
                    0.18
                    * (1 - np.exp(-x / 0.25))
                    * np.exp(-x / 3.0)
                    * (np.sin(2 * np.pi * ft * x) + 0.4 * np.sin(4 * np.pi * ft * x))
                )
    return out * (1 - np.exp(-x / 0.001))


tan = np.zeros((N, 2))
if DRONE == "veena":
    # tala strings Sa-Pa-Sa' struck on every beat, accents on 1 and 5 (how a veena keeps tala)
    vcyc = [
        ((SA / 2, SA * 0.75), 1.0),
        ((SA,), 0.55),
        ((SA * 0.75,), 0.7),
        ((SA,), 0.55),
        ((SA / 2, SA * 0.75), 0.85),
        ((SA,), 0.55),
        ((SA * 0.75,), 0.7),
        ((SA,), 0.6),
    ]
    for b, p in zip(beats, poss, strict=True):
        fs, v = vcyc[p]
        for j, f in enumerate(fs):  # chord strokes roll ~12 ms like a finger sweep
            place(
                tan,
                veena_pluck(f * (1 + rng.normal(0, 0.0003))),
                max(0, b + 0.012 * j + rng.normal(0, 0.007)),
                v * (0.9 + 0.1 * rng.random()),
                -0.2 + 0.4 * j,
            )
elif DRONE == "sitar":
    # main string on the beat (Sa, Pa, Sa, low Ni), chikari (high Sa) on the off-beats
    scyc = [
        (SA / 2, 1.0),
        (SA * 0.75 / 2 * 2, 0.6),
        (SA / 2, 0.75),
        (SA / 2 * 2 ** (-200 / 1200) * 2, 0.55),
        (SA / 2, 0.9),
        (SA * 0.75, 0.6),
        (SA / 2, 0.75),
        (SA * 2 ** (-100 / 1200), 0.55),
    ]
    for i, (b, p) in enumerate(zip(beats, poss, strict=True)):
        f, v = scyc[p]
        place(
            tan,
            sitar_pluck(f),
            max(0, b + rng.normal(0, 0.007)),
            v * (0.9 + 0.1 * rng.random()),
            -0.15,
        )
        nxt = beats[i + 1] if i + 1 < len(beats) else b + ibi_end
        off = b + (nxt - b) / 2 + rng.normal(0, 0.006)
        place(tan, sitar_pluck(SA * 2, 1.2, taraf=False), off, 0.28, 0.35)
        place(tan, sitar_pluck(SA * 4, 1.0, taraf=False), off + 0.008, 0.14, 0.45)
else:
    cyc = [
        (SA * 0.75, -0.35, 1.0),
        (SA, 0.25, 0.62),
        (SA, 0.4, 0.68),
        (SA / 2, -0.1, 0.85),
        (SA * 0.75, -0.35, 0.8),
        (SA, 0.25, 0.6),
        (SA, 0.4, 0.66),
        (SA / 2, -0.1, 0.8),
    ]
    for b, p in zip(beats, poss, strict=True):
        f, pan, v = cyc[p]
        place(
            tan,
            pluck(f * (1 + rng.normal(0, 0.0003))),
            max(0, b + rng.normal(0, 0.008)),
            v * (0.92 + 0.08 * rng.random()),
            pan,
        )
duck = 1 - 0.35 * sing
tan *= duck[:, None]
down = beats[poss == 0]
breath = np.full(N, 0.75)
for d0, d1 in zip(down[:-1], down[1:], strict=True):
    a, b = int(d0 * SR), int(d1 * SR)
    breath[a:b] = 0.75 + 0.25 * np.cos(2 * np.pi * np.linspace(0, 1, b - a, endpoint=False))
pad = np.zeros((N, 2))
for f, a in [(SA / 2, 1), (SA * 0.75, 0.45), (SA, 0.5), (SA * 1.5, 0.12)]:
    for det, ch in [(-0.6, 0), (0.6, 1)]:
        pad[:, ch] += a * (
            np.sin(2 * np.pi * (f + det) * tt) + 0.25 * np.sin(4 * np.pi * (f + det) * tt)
        )
pad *= (breath * duck)[:, None]


# ---------- percussion ----------
def manjira(open_=True):
    n = int((1.4 if open_ else 0.12) * SR)
    x = np.arange(n) / SR
    s = np.zeros(n)
    for f in [2380, 3410, 4720, 5960, 7350, 8810]:
        f *= 1 + rng.normal(0, 0.004)
        s += np.sin(2 * np.pi * f * x + rng.uniform(0, 6.3)) * np.exp(
            -x / ((0.55 if open_ else 0.03) * 3000 / f)
        )
    s += 0.6 * rng.normal(0, 1, n) * np.exp(-x / 0.012)
    b, a = butter(2, 1800 / (SR / 2), "high")
    return lfilter(b, a, s) * (1 - np.exp(-x / 0.0008))


def thom():
    n = int(0.6 * SR)
    x = np.arange(n) / SR
    f = 72 + 40 * np.exp(-x / 0.05)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-x / 0.28) * (1 - np.exp(-x / 0.002))


def nam(ring=0.45):  # right head tuned to Sa: harmonic (Raman) partials
    n = int(1.0 * SR)
    x = np.arange(n) / SR
    s = sum(
        (0.9**k)
        * np.sin(2 * np.pi * SA * k * x + rng.uniform(0, 6.3))
        * np.exp(-x / (ring / k**0.6))
        for k in range(1, 6)
    )
    return s * (1 - np.exp(-x / 0.0015)) + 0.25 * rng.normal(0, 1, n) * np.exp(-x / 0.004)


def ki():
    n = int(0.12 * SR)
    x = np.arange(n) / SR
    s = rng.normal(0, 1, n) * np.exp(-x / 0.012)
    b, a = butter(2, [900 / (SR / 2), 3200 / (SR / 2)], "band")
    return lfilter(b, a, s) * 2.5 + 0.4 * np.sin(2 * np.pi * SA * 2 * x) * np.exp(-x / 0.03)


man = np.zeros((N, 2))
mri = np.zeros((N, 2))
# 16 half-beat slots per cycle: (slot, stroke, velocity)
PATTERN = [
    (0, "dhin", 1.0),
    (2, "ki", 0.45),
    (3, "ki", 0.35),
    (4, "nam", 0.7),
    (6, "thom", 0.6),
    (7, "ki", 0.35),
    (8, "dhin", 0.85),
    (10, "ki", 0.45),
    (11, "ki", 0.3),
    (12, "nam", 0.65),
    (13, "ki", 0.3),
    (14, "thom", 0.55),
    (15, "ki", 0.4),
]
if lvl >= 1:
    for i, (b, p) in enumerate(zip(beats, poss, strict=True)):
        nxt = beats[i + 1] if i + 1 < len(beats) else b + ibi_end
        tag = tags[i]
        if p in (0, 4):
            place(man, manjira(True), b + rng.normal(0, 0.004), 0.9 if p == 0 else 0.65, 0.3)
        if p in (2, 6):
            place(man, manjira(False), b + rng.normal(0, 0.004), 0.55, 0.3)
        if lvl >= 3 and (tag in ("intro", "v1", "v2") or tag == "outro"):
            continue  # stage D: mridangam enters from verse 3 (after first interlude)
        for slot, stroke, vel in PATTERN:
            if slot // 2 != p:
                continue
            tb = b + (nxt - b) * (slot % 2) / 2 + rng.normal(0, 0.006)
            vel *= 0.88 + 0.12 * rng.random()
            if stroke == "dhin":
                place(mri, thom(), tb, vel * 0.9, -0.15)
                place(mri, nam(0.6), tb, vel * 0.7, 0.15)
            elif stroke == "thom":
                place(mri, thom(), tb, vel, -0.15)
            elif stroke == "nam":
                place(mri, nam(), tb, vel * 0.8, 0.15)
            else:
                place(mri, ki(), tb, vel, 0.1)
man *= section_gain({"intro": 0.0, "v1": 0.0, "outro": 0.6})[:, None]
mri *= section_gain({"v8": 0.75})[:, None]

# ---------- flute ----------
fl_f0 = np.zeros(N)
fl_amp = np.zeros(N)
if lvl >= 2:
    z = np.load("f0_tuned.npz")
    vf0, vt = z["f0"], z["t"]
    venv = uniform_filter1d(np.abs(voc), int(0.03 * SR))[:: int(0.01 * SR)][: len(vt)]

    def copy_line(src_beat, dst_beats, rate=1.0):
        """Flute plays the melody of vocal line starting at old beat src_beat, warped onto dst_beats (9 times)."""
        src = vb[src_beat : src_beat + 9]
        a, b = int(dst_beats[0] * SR), int(dst_beats[-1] * SR)
        tn = np.arange(a, b) / SR
        told = np.interp(tn, dst_beats, src)  # piecewise-linear beat-to-beat warp
        f = np.interp(told, vt, vf0)
        env = np.interp(told, vt, venv)
        voiced = f > 0
        cents = 1200 * np.log2(np.where(voiced, f, SA) / SA)
        grid = np.concatenate([np.array(SCALE) + 1200 * k for k in (-1, 0, 1)])
        snapped = grid[np.abs(cents[:, None] - grid[None]).argmin(1)]
        cents = 0.5 * cents + 0.5 * snapped  # cleaner than the voice, still with its slides
        fl_f0[a:b] = np.where(
            voiced, SA * 2 * 2 ** (uniform_filter1d(cents, int(0.03 * SR)) / 1200), 0
        )
        fl_amp[a:b] = uniform_filter1d(
            np.where(voiced, env / (np.percentile(venv, 95) + 1e-9), 0), int(0.04 * SR)
        )

    def notes(seq, t0, unit):
        """seq: (cents, beats) -> flute phrase with 70 ms glides."""
        tcur = t0
        for c, nb in seq:
            a, b = int(tcur * SR), int((tcur + nb * unit) * SR)
            fl_f0[a:b] = SA * 2 * 2 ** (c / 1200)
            env = np.ones(b - a)
            r = min(int(0.06 * SR), (b - a) // 3)
            env[:r] = np.linspace(0.3, 1, r)
            fl_amp[a:b] = np.maximum(fl_amp[a:b], env * 0.8)
            tcur += nb * unit
        fl_f0[:] = np.where(
            fl_f0 > 0, np.exp(uniform_filter1d(np.log(np.maximum(fl_f0, 1)), int(0.07 * SR))), 0
        )

    ib = beats[tags.index("intro") + 1] - beats[tags.index("intro")]
    notes(
        [
            (0, 2),
            (500, 1.5),
            (700, 1.5),
            (1000, 1),
            (1200, 3),
            (1000, 1),
            (700, 1),
            (500, 1.5),
            (0, 2.5),
        ],
        beats[0] + 0.05,
        ib,
    )
    for k in CUTS:
        ib_idx = tags.index(f"inter{k}")
        dst = np.append(beats[ib_idx : ib_idx + 8], beats[ib_idx + 8])
        copy_line(k - 8, dst)  # replay the refrain the singer just finished
    oi = tags.index("outro")
    start = oi + int((-poss[oi]) % 8)  # next downbeat in the outro
    if start + 8 < len(beats):
        copy_line(249, beats[start : start + 9])
        tail = int(beats[start + 8] * SR)
        fl_f0[tail : tail + int(3 * SR)] = SA * 2
        fl_amp[tail : tail + int(3 * SR)] = 0.7 * np.exp(-np.arange(int(3 * SR)) / SR / 1.2)
        fl_f0[tail + int(3 * SR) :] = 0

    vib = 1 + (2 ** (11 / 1200) - 1) * np.sin(2 * np.pi * 5.3 * tt) * np.clip(
        uniform_filter1d(fl_amp, int(0.4 * SR)) * 1.5, 0, 1
    )
    ph = 2 * np.pi * np.cumsum(fl_f0 * vib) / SR
    tone = np.sin(ph) + 0.3 * np.sin(2 * ph) + 0.1 * np.sin(3 * ph) + 0.04 * np.sin(4 * ph)
    b, a = butter(2, [1200 / (SR / 2), 7000 / (SR / 2)], "band")
    air = lfilter(b, a, rng.normal(0, 1, N)) * 0.12
    flute = (tone + air) * fl_amp * (fl_f0 > 0)
    flute = lfilter(*butter(1, 6000 / (SR / 2)), flute)
    # control curves for neural (DDSP) flute rendering: same pitch incl. vibrato, same loudness shape
    np.savez_compressed(
        f"{OUT}/flute_ctrl.npz", f0=(fl_f0 * vib)[::21], amp=fl_amp[::21], sr=SR / 21
    )
else:
    flute = np.zeros(N)
flute_st = np.stack([flute * 0.85, flute], 1)

# ---------- temple bell (stage D) ----------
bell = np.zeros((N, 2))
if lvl >= 3:

    def bell_hit():
        n = int(7 * SR)
        x = np.arange(n) / SR
        s = sum(
            a * np.sin(2 * np.pi * SA * 2 * r * x + rng.uniform(0, 6.3)) * np.exp(-x / dcy)
            for r, a, dcy in [
                (0.5, 0.5, 5),
                (1, 1, 3.5),
                (1.183, 0.45, 2.5),
                (1.506, 0.4, 2),
                (2, 0.3, 1.6),
                (2.514, 0.25, 1.1),
                (3.011, 0.15, 0.8),
            ]
        )
        return s * (1 - np.exp(-x / 0.001))

    place(bell, bell_hit(), 0.25, 1.0, -0.2)
    place(bell, bell_hit(), beats[-1] + 0.2, 1.0, -0.2)
    for k in CUTS:  # soft bell at each interlude downbeat
        place(bell, bell_hit(), beats[tags.index(f"inter{k}")], 0.45, 0.2)

# stage D: tanpura/pad slightly lower in verse 1 so later verses feel like they grow
tg = section_gain({"v1": 0.85, "intro": 1.1})
tan *= tg[:, None]
pad *= tg[:, None]

fade = np.clip(tt / 2.5, 0, 1) * np.clip((TOTAL - tt) / 5, 0, 1)
for name, sig in [
    ("voc_main", voc_new),
    ("voc_dbl", dbl_new),
    ("tanpura", tan),
    ("pad", pad),
    ("manjira", man),
    ("mridangam", mri),
    ("flute", flute_st),
    ("bell", bell),
]:
    if sig.ndim == 2 and name not in ("voc_dbl",):
        sig = sig * fade[:, None]
    peak = np.max(np.abs(sig))
    # keep relative scale: normalise each stem to a fixed reference, mix levels are set in mix_stage.sh
    sf.write(f"{OUT}/{name}.wav", (sig / peak * 0.8 if peak > 0 else sig).astype(np.float32), SR)
with open(f"{OUT}/total.txt", "w") as fh:
    fh.write(f"{TOTAL:.3f}")
np.save(f"{OUT}/beats.npy", beats)
