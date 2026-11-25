"""Python Decoder channel intro (4 s) and outro (12 s), with synthesized sound. See README.md for the plan.

usage: uv run python make_bumpers.py OUT_DIR [--episode "EP 1 · Python 3.15"] [--next "Up next: uv from scratch"]
"""

from __future__ import annotations

import argparse
import math
import random
import subprocess
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
AVATAR = HERE.parents[1] / "videos/2026-11-24/talking-avatar-pilot/avatar/sprites/base.png"
W, H, FPS, SR = 1920, 1080, 30, 44100
NAVY, YELLOW, BLUE, WHITE = (14, 18, 32), (255, 212, 59), (75, 139, 190), (255, 255, 255)
FIRA = "/usr/share/fonts/opentype/fira/"
F_BIG = ImageFont.truetype(FIRA + "FiraSans-Bold.otf", 150)
F_TAG = ImageFont.truetype(FIRA + "FiraMono-Medium.otf", 46)
F_CHIP = ImageFont.truetype(FIRA + "FiraSans-Bold.otf", 40)
F_H1 = ImageFont.truetype(FIRA + "FiraSans-Bold.otf", 80)
F_BODY = ImageFont.truetype(FIRA + "FiraSans-Book.otf", 44)
F_LABEL = ImageFont.truetype(FIRA + "FiraSans-Bold.otf", 38)
F_GLYPH = ImageFont.truetype(FIRA + "FiraMono-Regular.otf", 22)
F_EMOJI = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 44)
GLYPHS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789{}[]<>/=+*#$%&"


def ease(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def ease_out_back(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return 1 + 2.4 * (u - 1) ** 3 + 1.4 * (u - 1) ** 2


# ---------------------------------------------------------------- shared background
_rng = random.Random(7)
CODE_COLS = [(x, _rng.uniform(0, H), _rng.uniform(25, 60), "".join(_rng.choice(GLYPHS) for _ in range(40)))
             for x in range(20, W, 46)]


def background(t: float) -> Image.Image:
    im = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(im)
    for y in range(0, H, 4):  # subtle vertical gradient
        c = int(18 * y / H)
        d.rectangle((0, y, W, y + 4), fill=(NAVY[0] + c // 3, NAVY[1] + c // 2, NAVY[2] + c))
    for x, y0, speed, chars in CODE_COLS:  # faint falling code
        y = (y0 + t * speed) % (H + 600) - 600
        for i, ch in enumerate(chars[:22]):
            d.text((x, y + i * 28), ch, font=F_GLYPH, fill=(40, 60, 95))
    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).ellipse((W * 0.45, -200, W * 1.15, H * 0.9), fill=110)
    im.paste(Image.new("RGB", (W, H), BLUE), (0, 0), glow.filter(ImageFilter.GaussianBlur(160)).point(lambda v: v // 3))
    return im


_AV: Image.Image | None = None


def avatar(height: int) -> Image.Image:
    global _AV
    if _AV is None:
        a = Image.open(AVATAR).convert("RGBA")
        _AV = a.crop(a.getbbox())
    a = _AV
    return a.resize((int(a.width * height / a.height), height), Image.LANCZOS)


# ---------------------------------------------------------------- intro
def intro_frame(t: float, episode: str | None, av: Image.Image) -> Image.Image:
    im = background(t)
    d = ImageDraw.Draw(im)
    word1, word2 = "PYTHON ", "DECODER"
    text = word1 + word2
    x0, y0 = 150, 400
    lock_start, lock_step = 0.45, 0.085
    rng = random.Random(int(t * FPS))
    x = x0
    if t >= 0.3:
        for i, ch in enumerate(text):
            col = YELLOW if i >= len(word1) else WHITE
            locked = t >= lock_start + i * lock_step
            shown = ch if (locked or ch == " ") else rng.choice(GLYPHS)
            a = 1.0 if locked else 0.55
            d.text((x, y0), shown, font=F_BIG,
                   fill=tuple(int(c * a + NAVY[k] * (1 - a)) for k, c in enumerate(col)))
            x += F_BIG.getlength(ch)
    elif int(t * 4) % 2 == 0:  # caret before the decode starts
        d.rectangle((x0, y0 + 20, x0 + 18, y0 + 160), fill=WHITE)
    # tagline types in
    tag = "Modern Python, decoded."
    if t >= 1.9:
        n = min(len(tag), int((t - 1.9) / 0.03))
        d.text((x0 + 6, y0 + 200), tag[:n], font=F_TAG, fill=(190, 200, 220))
        if n < len(tag) or int(t * 4) % 2 == 0:
            cx = x0 + 6 + F_TAG.getlength(tag[:n])
            d.rectangle((cx + 4, y0 + 206, cx + 26, y0 + 252), fill=YELLOW)
    # episode chip
    if episode and t >= 2.2:
        u = ease_out_back((t - 2.2) / 0.5)
        cw = F_CHIP.getlength(episode) + 60
        cx = x0 - (cw + 200) * (1 - u)
        d.rounded_rectangle((cx, y0 - 110, cx + cw, y0 - 40), 35, fill=YELLOW)
        d.text((cx + 30, y0 - 101), episode, font=F_CHIP, fill=NAVY)
    # avatar slides up with overshoot
    if t >= 1.3:
        u = ease_out_back((t - 1.3) / 0.6)
        ay = H - av.height + int((1 - u) * (av.height + 60))
        im.paste(av, (W - av.width - 90, ay), av)
    # zoom + fade out
    if t >= 3.4:
        u = ease((t - 3.4) / 0.6)
        z = 1 + 0.06 * u
        im = im.resize((int(W * z), int(H * z)), Image.BILINEAR).crop(
            (int((W * z - W) / 2), int((H * z - H) / 2), int((W * z - W) / 2) + W, int((H * z - H) / 2) + H))
        im = Image.blend(im, Image.new("RGB", (W, H), (0, 0, 0)), u)
    elif t < 0.25:
        im = Image.blend(Image.new("RGB", (W, H), (0, 0, 0)), im, t / 0.25)
    return im


# ---------------------------------------------------------------- outro
VIDEO_BOX = (1000, 150, 1000 + 800, 150 + 450)    # YouTube "video" end-screen element goes here
SUB_CIRCLE = (1290, 680, 1290 + 230, 680 + 230)   # YouTube "subscribe" element goes here


def outro_frame(t: float, nxt: str, av: Image.Image, dur: float, question: str = "Which feature will you use first?") -> Image.Image:
    im = background(t + 4)
    d = ImageDraw.Draw(im, "RGBA")
    a = ease(t / 0.5)
    # left: title and prompt
    d.text((110, 150), "Thanks for watching!", font=F_H1, fill=WHITE + (int(255 * a),))
    body = question
    d.text((116, 275), body, font=F_BODY, fill=(200, 210, 230, int(255 * ease((t - 0.4) / 0.5))))
    line2 = "Tell me in the comments "
    d.text((116, 335), line2, font=F_BODY, fill=(200, 210, 230, int(255 * ease((t - 0.7) / 0.5))))
    d.text((116 + F_BODY.getlength(line2), 335), "↓", font=F_EMOJI,
           fill=YELLOW + (int(255 * ease((t - 0.7) / 0.5)),))
    # right: end-screen frames
    u = ease_out_back((t - 0.5) / 0.6)
    off = int((1 - u) * 700)
    x0, y0, x1, y1 = VIDEO_BOX
    d.rounded_rectangle((x0 + off, y0, x1 + off, y1), 26, outline=YELLOW, width=6, fill=(255, 255, 255, 18))
    d.text((x0 + off + 30, y1 + 18), nxt, font=F_LABEL, fill=YELLOW)
    u2 = ease_out_back((t - 0.8) / 0.6)
    off2 = int((1 - u2) * 700)
    cx0, cy0, cx1, cy1 = SUB_CIRCLE
    pulse = 1 + 0.03 * math.sin(t * 4)
    r = (cx1 - cx0) / 2 * pulse
    cx, cy = (cx0 + cx1) / 2 + off2, (cy0 + cy1) / 2
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=WHITE, width=6, fill=(255, 255, 255, 18))
    d.text((cx1 + 40 + off2, cy - 26), "Subscribe", font=F_LABEL, fill=WHITE)
    # avatar, bottom-left, gentle breathing
    s = 1 + 0.008 * math.sin(t * 2 * math.pi / 3.2)
    a2 = av.resize((int(av.width * s), int(av.height * s)), Image.BILINEAR)
    ay = H - a2.height + int((1 - ease_out_back(t / 0.7)) * 400)
    im.paste(a2, (140, ay), a2)
    if t > dur - 1.0:
        im = Image.blend(im, Image.new("RGB", (W, H), (0, 0, 0)), ease((t - (dur - 1.0)) / 1.0))
    return im


# ---------------------------------------------------------------- sound
def tone(freq: float, dur: float, decay: float = 4.0, vol: float = 0.25) -> np.ndarray:
    t = np.arange(int(SR * dur)) / SR
    env = np.exp(-decay * t) * np.minimum(1, t / 0.005)
    return vol * env * (np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(4 * np.pi * freq * t))


def place(buf: np.ndarray, clip: np.ndarray, at: float) -> None:
    i = int(at * SR)
    n = min(len(clip), len(buf) - i)
    if n > 0:
        buf[i:i + n] += clip[:n]


def intro_audio(dur: float) -> np.ndarray:
    buf = np.zeros(int(SR * dur))
    rng = np.random.default_rng(3)
    n = int(SR * 1.2)  # whoosh: band-limited noise with a swell
    noise = np.convolve(rng.standard_normal(n), np.ones(40) / 40, mode="same")
    env = np.sin(np.linspace(0, np.pi, n)) ** 2
    place(buf, 0.35 * noise * env, 0.2)
    for i in range(14):  # ticks as letters lock
        place(buf, tone(2400, 0.04, decay=80, vol=0.08), 0.45 + i * 0.085)
    place(buf, tone(659.25, 1.6, decay=2.5, vol=0.28), 1.35)   # E5
    place(buf, tone(987.77, 1.8, decay=2.2, vol=0.24), 1.55)   # B5
    fade = np.ones_like(buf)
    fade[-int(SR * 0.6):] = np.linspace(1, 0, int(SR * 0.6))
    return buf * fade


def outro_audio(dur: float) -> np.ndarray:
    t = np.arange(int(SR * dur)) / SR
    chords = [(261.63, 329.63, 392.0), (220.0, 261.63, 329.63), (174.61, 220.0, 261.63), (196.0, 246.94, 293.66)]
    buf = np.zeros_like(t)
    seg = dur / len(chords)
    for k, ch in enumerate(chords):
        m = (t >= k * seg) & (t < (k + 1) * seg + 0.5)
        tt = t[m] - k * seg
        env = np.minimum(1, tt / 0.6) * np.exp(-0.25 * tt)
        for f in ch:
            buf[m] += 0.07 * env * (np.sin(2 * np.pi * f * tt) + 0.2 * np.sin(2 * np.pi * 2 * f * tt))
    fade = np.minimum(1, t / 0.4) * np.minimum(1, (dur - t) / 2.0)
    return buf * fade


def write_wav(path: Path, mono: np.ndarray) -> None:
    peak = np.max(np.abs(mono)) or 1.0
    data = (mono / peak * 0.7 * 32767).astype(np.int16)
    stereo = np.repeat(data[:, None], 2, axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(stereo.tobytes())


def render(path: Path, frames, n: int, wav: Path, lufs: float) -> None:
    p = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                          "-r", str(FPS), "-i", "-", "-i", str(wav),
                          "-af", f"loudnorm=I={lufs}:TP=-2:LRA=11", "-ar", str(SR),
                          "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                          "-c:a", "aac", "-b:a", "192k", "-shortest", str(path)], stdin=subprocess.PIPE)
    for i in range(n):
        p.stdin.write(frames(i / FPS).tobytes())
    p.stdin.close()
    p.wait()
    print("wrote", path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--episode", default=None)
    ap.add_argument("--next", dest="nxt", default="Up next")
    ap.add_argument("--question", default="Which feature will you use first?", help="outro prompt above 'Tell me in the comments'")
    ap.add_argument("--still", type=float, nargs="*", help="write intro/outro PNG stills at these times instead")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    av_intro, av_outro = avatar(560), avatar(640)
    if a.still is not None:
        for t in a.still:
            intro_frame(min(t, 3.99), a.episode, av_intro).save(out / f"intro_{t:.1f}.png")
            outro_frame(t, a.nxt, av_outro, 12.0, a.question).save(out / f"outro_{t:.1f}.png")
        return
    di, do = 4.0, 12.0
    write_wav(out / "_intro.wav", intro_audio(di))
    write_wav(out / "_outro.wav", outro_audio(do))
    render(out / "intro.mp4", lambda t: intro_frame(t, a.episode, av_intro), int(di * FPS), out / "_intro.wav", -18)
    render(out / "outro.mp4", lambda t: outro_frame(t, a.nxt, av_outro, do, a.question), int(do * FPS), out / "_outro.wav", -20)
    (out / "_intro.wav").unlink()
    (out / "_outro.wav").unlink()


if __name__ == "__main__":
    main()
