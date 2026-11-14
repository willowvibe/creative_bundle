"""Synthesize a stereo hall impulse response (2.4 s, dark tail) for afir reverb.

usage: python make_ir.py OUT.wav
"""

import sys

import numpy as np
import soundfile as sf
from scipy.signal import butter, lfilter

SR = 44100
rng = np.random.default_rng(7)
L = int(2.4 * SR)
t = np.arange(L) / SR
ir = rng.normal(0, 1, (L, 2)) * np.exp(-t / 0.55)[:, None]
b, a = butter(2, 6000 / (SR / 2))
ir = lfilter(b, a, ir, axis=0)
ramp = int(0.02 * SR)
ir[:ramp] *= np.linspace(0, 1, ramp)[:, None]  # soft 20 ms onset
sf.write(sys.argv[1], (ir / np.max(np.abs(ir)) * 0.5).astype(np.float32), SR)
print("wrote", sys.argv[1])
