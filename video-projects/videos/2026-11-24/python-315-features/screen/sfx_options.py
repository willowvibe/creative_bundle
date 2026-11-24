"""Candidate "output appeared" sounds and a labelled sampler clip to pick one.

usage: python3 sfx_options.py VIDEO.mp4 EVENT_TIME OUT_DIR
Writes OUT_DIR/<id>.wav for each candidate and OUT_DIR/sampler.mp4: the same 3 s of the video (terminal output
appearing at EVENT_TIME) repeated once per candidate, with the option's letter and name on screen.
"""
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

SR = 44100
FONT = "/usr/share/fonts/opentype/fira/FiraSans-Bold.otf"


def t_axis(dur):
    return np.arange(int(SR * dur)) / SR


def norm(x, peak=1.0):
    return x / (np.max(np.abs(x)) or 1) * peak


def pop():  # bubble pop: fast pitch drop
    t = t_axis(0.09)
    f = 950 * np.exp(-t * 28) + 250
    return norm(np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 45) * np.minimum(1, t / 0.002))


def blip():  # two-tone UI confirm (soft square-ish)
    out = []
    for f, d in ((880, 0.055), (1320, 0.09)):
        t = t_axis(d)
        s = np.tanh(2.2 * np.sin(2 * np.pi * f * t)) * np.exp(-t * 30) * np.minimum(1, t / 0.003)
        out.append(s)
    return norm(np.concatenate(out))


def enter_key():  # keyboard Enter: click + low thock
    t = t_axis(0.08)
    rng = np.random.default_rng(5)
    click = rng.standard_normal(len(t)) * np.exp(-t * 300)
    click = np.convolve(click, [1, -0.9], mode="same")
    thock = np.sin(2 * np.pi * 180 * t) * np.exp(-t * 60)
    return norm(0.6 * click + thock)


def marimba():  # wooden mallet note (G5)
    t = t_axis(0.5)
    f = 784
    s = (np.sin(2 * np.pi * f * t) * np.exp(-t * 9) + 0.35 * np.sin(2 * np.pi * 4 * f * t) * np.exp(-t * 40)
         + 0.15 * np.sin(2 * np.pi * 10 * f * t) * np.exp(-t * 80))
    return norm(s * np.minimum(1, t / 0.002))


def chime():  # soft success: two sine notes C6 -> E6
    out = np.zeros(int(SR * 0.6))
    for f, at in ((1046.5, 0.0), (1318.5, 0.09)):
        t = t_axis(0.5)
        s = np.sin(2 * np.pi * f * t) * np.exp(-t * 7) * np.minimum(1, t / 0.004)
        i = int(at * SR)
        out[i:i + len(s)] += s[: len(out) - i]
    return norm(out)


def tick():  # wood-block tick
    t = t_axis(0.06)
    s = np.sin(2 * np.pi * 1900 * t) * np.exp(-t * 110) + 0.5 * np.sin(2 * np.pi * 3100 * t) * np.exp(-t * 160)
    return norm(s * np.minimum(1, t / 0.001))


def bloop():  # water-drop bloop: pitch rises
    t = t_axis(0.12)
    f = 320 + 700 * (t / t[-1]) ** 0.6
    return norm(np.sin(2 * np.pi * np.cumsum(f) / SR) * np.sin(np.pi * t / t[-1]) ** 0.7)


def typewriter():  # quick typing burst: three soft key clicks
    out = np.zeros(int(SR * 0.22))
    rng = np.random.default_rng(9)
    for k, at in enumerate((0.0, 0.07, 0.15)):
        t = t_axis(0.03)
        c = rng.standard_normal(len(t)) * np.exp(-t * 400) + 0.6 * np.sin(2 * np.pi * (2400 + 200 * k) * t) * np.exp(-t * 250)
        i = int(at * SR)
        out[i:i + len(c)] += c
    return norm(out)


OPTIONS = [  # (id, label, generator, mix level)
    ("A_pop", "A · Soft pop", pop, 0.16),
    ("B_blip", "B · UI blip (two-tone)", blip, 0.10),
    ("C_enter", "C · Keyboard Enter click", enter_key, 0.20),
    ("D_marimba", "D · Marimba pluck", marimba, 0.13),
    ("E_chime", "E · Soft success chime", chime, 0.10),
    ("F_tick", "F · Wood tick", tick, 0.14),
    ("G_bloop", "G · Water-drop bloop", bloop, 0.15),
    ("H_typing", "H · Quick typing burst", typewriter, 0.14),
    ("X_current", "Current: bell ting", None, 0.11),
]


def bell():
    t = t_axis(0.9)
    s = (np.exp(-5.5 * t) * np.sin(2 * np.pi * 1760 * t) + 0.45 * np.exp(-8 * t) * np.sin(2 * np.pi * 1760 * 2.76 * t)
         + 0.25 * np.exp(-12 * t) * np.sin(2 * np.pi * 1760 * 5.40 * t))
    return norm(s * np.minimum(1, t / 0.003))


def write(path, mono, level):
    data = (np.clip(mono * level, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.repeat(data[:, None], 2, axis=1).tobytes())


def main():
    video, t_ev, out = sys.argv[1], float(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    pre, length = 1.2, 3.0
    parts = []
    for oid, label, gen, level in OPTIONS:
        wav = out / f"{oid}.wav"
        write(wav, (gen or bell)(), level)
        clip = out / f"_{oid}.mp4"
        delay = int(pre * 1000)
        draw = (f"drawbox=x=60:y=60:w=760:h=110:color=0x0E1220@0.9:t=fill,"
                f"drawtext=fontfile={FONT}:text='{label}':x=90:y=88:fontsize=54:fontcolor=0xFFD43B")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t_ev - pre), "-t", str(length), "-i", video,
                        "-i", str(wav), "-filter_complex",
                        f"[1:a]adelay={delay}|{delay}[s];[0:a][s]amix=inputs=2:duration=first:normalize=0[a];[0:v]{draw}[v]",
                        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                        "-c:a", "aac", "-b:a", "160k", "-r", "30", str(clip)], check=True)
        parts.append(clip)
    lst = out / "_list.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                    str(out / "sampler.mp4")], check=True, cwd=out)
    for p in parts:
        p.unlink()
    lst.unlink()
    print("wrote", out / "sampler.mp4")


if __name__ == "__main__":
    main()
