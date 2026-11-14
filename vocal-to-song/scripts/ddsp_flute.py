"""Render the flute part with Magenta's pretrained DDSP solo-flute model.

usage: python ddsp_flute.py CKPT_DIR STEM_DIR
Reads STEM_DIR/flute_ctrl.npz (pitch incl. vibrato + loudness shape from arrange.py),
writes STEM_DIR/flute.wav (44.1 kHz stereo, peak 0.8) replacing the sine-synth flute.
"""

import glob
import os
import pickle
import sys

import ddsp.training
import ddsp.training.postprocessing as pp
import gin
import numpy as np
import soundfile as sf
from scipy.ndimage import uniform_filter1d
from scipy.signal import resample_poly

CKPT, STEMS = sys.argv[1], sys.argv[2]
FR, SR_M, SR_OUT = 250, 16000, 44100

c = np.load(f"{STEMS}/flute_ctrl.npz")
f0_in, amp_in, sr_c = c["f0"], c["amp"], float(c["sr"])
dur = len(f0_in) / sr_c
T = int(dur * FR)
tf_ = np.arange(T) / FR
tc = np.arange(len(f0_in)) / sr_c
f0 = np.interp(tf_, tc, f0_in)
amp = np.interp(tf_, tc, amp_in)
on = (f0 > 50) & (amp > 0.03)

# DDSP wants a continuous f0: hold the nearest voiced pitch through silences
idx = np.where(on)[0]
f0 = np.interp(np.arange(T), idx, f0[idx])

# phrases = runs of notes with gaps < 0.6 s. Normalise loudness per phrase so a soft interlude is not
# pushed to the bottom of the model's range by the loud intro (v10 bug: interludes went ~silent).
gapfill = np.convolve(on.astype(float), np.ones(int(0.6 * FR)), "same") > 0
phrases, i = [], 0
while i < T:
    if gapfill[i]:
        j = i
        while j < T and gapfill[j]:
            j += 1
        if on[i:j].any():
            phrases.append((i, j))
        i = j
    else:
        i += 1
amp_n = amp.copy()
for a, b in phrases:
    m = on[a:b]
    amp_n[a:b] = amp[a:b] / (np.percentile(amp[a:b][m], 90) + 1e-6)
loud = (
    20 * np.log10(np.maximum(amp_n, 1e-4)) - 20.0
)  # rough dB scale, refined by quantile matching below


class _U(pickle.Unpickler):
    def find_class(self, m, n):
        return pp.QuantileTransformer if n == "QuantileTransformer" else super().find_class(m, n)


# The pickle ships with Google Magenta's official checkpoint (storage.googleapis.com/ddsp), a trusted
# source; the unpickler above only remaps the one class that moved between ddsp versions.
with open(os.path.join(CKPT, "dataset_statistics.pkl"), "rb") as fh:
    stats = _U(fh).load()
# Map into the model's "real note" range instead of a full quantile transform: the dataset's note
# loudness has median -55 dB and p90 -44 dB; below ~-58 dB the model fades to breath, which made soft
# interlude notes vanish. Phrase peak -> -44 dB, with dynamics compressed (0.6x) and floored at -56 dB.
qt = stats["quantile_transform"]
loud = np.where(
    on, np.clip(-44.0 + 0.6 * 20 * np.log10(np.maximum(amp_n, 1e-4)), -56.0, -40.0), -100.0
)
loud = uniform_filter1d(loud, 5)  # 20 ms smoothing so notes don't click on/off

gin.parse_config_file(os.path.join(CKPT, "operative_config-0.gin"), skip_unknown=True)
with gin.unlock_config():
    gin.bind_parameter("F0LoudnessPreprocessor.time_steps", T)
    gin.bind_parameter(
        "F0LoudnessPreprocessor.compute_loudness", False
    )  # we supply loudness, no input audio
    gin.bind_parameter("Harmonic.n_samples", T * SR_M // FR)
    gin.bind_parameter("FilteredNoise.n_samples", T * SR_M // FR)
    gin.bind_parameter(
        "oscillator_bank.use_angular_cumsum", True
    )  # avoids phase drift on long renders
model = ddsp.training.models.Autoencoder()
ckpt = sorted(glob.glob(os.path.join(CKPT, "ckpt-*.index")))[-1][:-6]
model.restore(ckpt)

feats = {
    "f0_hz": f0[None, :, None].astype(np.float32),
    "loudness_db": loud[None, :, None].astype(np.float32),
    "f0_confidence": np.ones((1, T, 1), np.float32),
}
out = model(feats, training=False)
audio = np.asarray(model.get_audio_from_outputs(out))[0]

y = resample_poly(audio, 441, 160)
gate = np.interp(np.arange(len(y)) / SR_OUT, tf_, uniform_filter1d(on.astype(float), 10))
y *= np.clip(gate * 1.5, 0, 1)  # silence the model's breath noise between phrases

# keep the approved v7 balance between phrases: match each phrase's RMS to the sine-flute stem
sine_path = f"{STEMS}/flute_sine.wav"
if not os.path.exists(sine_path):
    os.replace(f"{STEMS}/flute.wav", sine_path)
ref = sf.read(sine_path)[0][:, 1]
for a, b in phrases:
    s, e = int(a / FR * SR_OUT), min(len(y), int(b / FR * SR_OUT))
    r = np.sqrt(np.mean(ref[s:e] ** 2)) / (np.sqrt(np.mean(y[s:e] ** 2)) + 1e-9)
    y[s:e] *= r
st = np.stack([y * 0.85, y], 1)
st = st / (np.max(np.abs(st)) + 1e-9) * 0.8
sf.write(f"{STEMS}/flute.wav", st.astype(np.float32), SR_OUT)
print(
    f"DDSP flute: {dur:.1f}s, {on.mean() * dur:.1f}s of notes, mean f0 on notes {np.mean(f0[on]):.0f} Hz"
)
