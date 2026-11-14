"""Count chant repetitions in a finished mix two independent ways.
usage: python verify.py MIX.wav CYCLE.wav LAYOUT.json [VOICE_STEM BACKING_STEM]"""
import json, sys
import numpy as np, soundfile as sf, librosa, pyloudnorm as pyln
from scipy.signal import butter, sosfiltfilt, find_peaks, fftconvolve
mix, sr = sf.read(sys.argv[1]); mix = mix.mean(1) if mix.ndim > 1 else mix
cyc, _ = sf.read(sys.argv[2]); L = json.load(open(sys.argv[3]))
FS = 1000  # 1 kHz analysis rate
# (a) energy segmentation in the voice band (300-3400 Hz), hop 10 ms
sos = butter(4, [300, 3400], btype="band", fs=sr, output="sos")
v = sosfiltfilt(sos, mix)
hop = sr // 100
e = 20*np.log10(np.sqrt(np.mean(v[: len(v)//hop*hop].reshape(-1, hop)**2, 1)) + 1e-9)
thr = np.percentile(e, 10) + 0.5*(np.percentile(e, 90) - np.percentile(e, 10))
act = e > thr
# merge dips < 0.5 s, drop blips < 2 s
segs=[]; i=0
while i < len(act):
    if act[i]:
        j=i
        while j < len(act) and (act[j] or act[j:j+50].any()): j+=1
        if (j-i)/100 >= 2.0: segs.append((i/100, j/100))
        i=j
    else: i+=1
dur = np.array([b-a for a,b in segs]); st = np.array([a for a,_ in segs])
print(f"(a) energy segments: {len(segs)}  dur {dur.min():.2f}-{dur.max():.2f}s  period {np.diff(st).mean():.3f}±{np.diff(st).std():.3f}s  thr {thr:.1f} dB")
# (b) template match: normalised xcorr of the voice-band envelope against the cycle's own envelope
vc = sosfiltfilt(sos, cyc)
ec = 20*np.log10(np.sqrt(np.mean(vc[: len(vc)//hop*hop].reshape(-1, hop)**2, 1)) + 1e-9)
x = np.clip(e, thr - 15, None); tpl = np.clip(ec, ec.max() - 40, None)
tpl = tpl - tpl.mean()
num = np.correlate(x - x.mean(), tpl, "valid")
den = np.sqrt(np.convolve((x - x.mean())**2, np.ones(len(tpl)), "valid") * np.sum(tpl**2)) + 1e-9
c = num/den
pk, _ = find_peaks(c, height=0.8, distance=int(0.6*L["period_s"]*100))
t_pk = pk/100
off = np.array([t - np.array(L["starts_s"])[np.argmin(np.abs(np.array(L["starts_s"]) - t))] for t in t_pk])
print(f"(b) envelope template matches (r>0.8): {len(pk)}  offset vs layout {off.min()*1000:.0f}..{off.max()*1000:.0f} ms  r min/max {c[pk].min():.3f}/{c[pk].max():.3f}")
ea = np.array([np.min(np.abs(np.array(L["starts_s"]) - s)) for s in st])
print(f"    energy-segment starts vs layout: max offset {ea.max()*1000:.0f} ms")
if len(sys.argv) > 5:
    m = pyln.Meter(sr)
    vo, _ = sf.read(sys.argv[4]); bk, _ = sf.read(sys.argv[5])
    vo = vo * 10**(6/20)
    print(f"voice {m.integrated_loudness(vo):.1f} LUFS vs backing {m.integrated_loudness(bk):.1f} LUFS (same gain staging)")
ok = len(segs) == len(pk) == L["reps"]
print("PASS" if ok else "FAIL", "count =", len(segs), len(pk))
