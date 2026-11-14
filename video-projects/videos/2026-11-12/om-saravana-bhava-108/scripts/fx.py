"""Seamless 8.3 s overlay loops (RGBA, qtrle .mov): falling flower petals and golden glitter.

Every loop is exactly one chant period (8.3 s = 249 frames at 30 fps). Laid from t=0 with -stream_loop,
each loop stays locked to the chant grid for all 108 chants.

usage: python fx.py OUT_DIR
writes petals_ding.mov  - a light wave of petals released just after each ding (ding phase = 12.25 mod 8.3)
       petals_shower.mov - a dense continuous pushpanjali shower, for the Muruga images
       glitter.mov       - soft golden sparkles twinkling, for the whole video
"""

import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
W, H, FPS = 1920, 1080, 30
P = 8.3
NF = int(round(P * FPS))  # 249
DING_PHASE = 12.25 % P  # 3.95 s into each loop
rng = np.random.default_rng(1008)


# ---------- petal sprites ----------
def petal_sprite(kind, size):
    s = size * 3
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if kind == "rose":  # single pink-red petal, teardrop
        col = [(214, 40, 70), (235, 80, 110), (196, 30, 60)][rng.integers(3)]
        d.ellipse([s * 0.2, s * 0.05, s * 0.8, s * 0.95], fill=col + (255,))
        d.ellipse([s * 0.32, s * 0.15, s * 0.6, s * 0.55], fill=tuple(min(255, c + 40) for c in col) + (150,))
    elif kind == "marigold":  # orange/yellow ruffled flower head
        col = [(255, 140, 0), (255, 170, 20), (250, 120, 10)][rng.integers(3)]
        for a in np.linspace(0, 2 * np.pi, 14, endpoint=False):
            r = s * 0.34
            cx, cy = s / 2 + r * np.cos(a) * 0.55, s / 2 + r * np.sin(a) * 0.55
            d.ellipse([cx - s * 0.2, cy - s * 0.2, cx + s * 0.2, cy + s * 0.2], fill=col + (255,))
        d.ellipse([s * 0.32, s * 0.32, s * 0.68, s * 0.68], fill=(255, 200, 60, 255))
    else:  # jasmine: small white five-petal flower
        for a in np.linspace(0, 2 * np.pi, 5, endpoint=False):
            cx, cy = s / 2 + s * 0.2 * np.cos(a), s / 2 + s * 0.2 * np.sin(a)
            d.ellipse([cx - s * 0.17, cy - s * 0.17, cx + s * 0.17, cy + s * 0.17], fill=(255, 252, 240, 255))
        d.ellipse([s * 0.42, s * 0.42, s * 0.58, s * 0.58], fill=(250, 220, 120, 255))
    im = im.filter(ImageFilter.GaussianBlur(1.2))
    return im.resize((size, size), Image.LANCZOS)


# v4: the user asked for smaller petals (was 72/54 px)
SPRITES = {k: [petal_sprite(k, 48 if k != "jasmine" else 36) for _ in range(4)] for k in ("rose", "marigold", "jasmine")}


def make_particles(n, spawn_fn, life_rng):
    ps = []
    for _ in range(n):
        kind = rng.choice(["rose", "rose", "marigold", "jasmine"])
        ps.append(dict(
            spr=SPRITES[kind][rng.integers(4)], spawn=spawn_fn(), life=rng.uniform(*life_rng),
            x0=rng.uniform(-60, W + 60), drift=rng.uniform(-160, 160), sway=rng.uniform(20, 70),
            swf=rng.uniform(0.25, 0.6), rot0=rng.uniform(0, 360), rotv=rng.uniform(-120, 120),
            flip=rng.uniform(0.6, 1.6), scale=rng.uniform(0.7, 1.25), ph=rng.uniform(0, 6.3),
        ))
    return ps


def draw_petals(frame, ps, t):
    for p in ps:
        age = (t - p["spawn"]) % P  # modular age keeps the loop seamless across its boundary
        if age > p["life"]:
            continue
        u = age / p["life"]
        y = -80 + (H + 160) * u
        x = p["x0"] + p["drift"] * u + p["sway"] * np.sin(2 * np.pi * p["swf"] * age + p["ph"])
        sx = max(0.15, abs(np.cos(2 * np.pi * p["flip"] * age * 0.5 + p["ph"])))  # tumbling flip
        spr = p["spr"]
        w0 = int(spr.width * p["scale"])
        spr = spr.resize((max(2, int(w0 * sx)), w0), Image.BILINEAR).rotate(
            p["rot0"] + p["rotv"] * age, expand=True, resample=Image.BILINEAR)
        a = min(1.0, age / 0.4) * min(1.0, (p["life"] - age) / 0.6)  # fade in at the top, out at the end
        if a < 1:
            r, g, b, al = spr.split()
            spr = Image.merge("RGBA", (r, g, b, al.point(lambda v: int(v * a))))
        frame.alpha_composite(spr, (int(x - spr.width / 2), int(y - spr.height / 2)))


def glitter_layer(n=80):
    gs = []
    for _ in range(n):
        gs.append(dict(x=rng.uniform(0, W), y=rng.uniform(0, H), r=rng.uniform(5, 13),
                       cyc=int(rng.integers(1, 4)), ph=rng.uniform(0, 6.3), dy=rng.uniform(8, 30)))
    return gs


def star(r):
    s = int(r * 6) | 1
    yy, xx = np.mgrid[0:s, 0:s] - s // 2
    core = np.exp(-(xx**2 + yy**2) / (2 * (r * 0.35) ** 2))
    rays = np.exp(-np.abs(yy) / (r * 0.08)) * np.exp(-np.abs(xx) / (r * 1.2)) + \
        np.exp(-np.abs(xx) / (r * 0.08)) * np.exp(-np.abs(yy) / (r * 1.2))
    a = np.clip(core + 0.6 * rays, 0, 1)
    im = np.zeros((s, s, 4), np.uint8)
    im[..., 0], im[..., 1], im[..., 2] = 255, 226, 150
    im[..., 3] = (a * 255).astype(np.uint8)
    return Image.fromarray(im, "RGBA")


def draw_glitter(frame, gs, t):
    for g in gs:
        tw = 0.5 + 0.5 * np.sin(2 * np.pi * g["cyc"] * t / P + g["ph"])  # integer cycles per loop: seamless
        tw = tw**3
        if tw < 0.05:
            continue
        r = g["r"] * (0.6 + 0.6 * tw)
        st = star(r)
        al = st.split()[3].point(lambda v: int(v * tw * 0.85))
        st.putalpha(al)
        y = g["y"] + g["dy"] * np.sin(2 * np.pi * t / P + g["ph"])
        frame.alpha_composite(st, (int(g["x"] - st.width / 2), int(y - st.height / 2)))


def write_loop(name, draw):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "qtrle", "-pix_fmt", "argb", str(OUT / name)]
    pr = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in range(NF):
        fr = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw(fr, f / FPS)
        pr.stdin.write(fr.tobytes())
    pr.stdin.close()
    assert pr.wait() == 0
    print("wrote", OUT / name, flush=True)


ding_ps = make_particles(16, lambda: DING_PHASE + rng.uniform(0.05, 1.3), (5.0, 6.8))
shower_ps = make_particles(46, lambda: rng.uniform(0, P), (5.5, 7.5))
gl = glitter_layer()
write_loop("petals_ding.mov", lambda fr, t: draw_petals(fr, ding_ps, t))
write_loop("petals_shower.mov", lambda fr, t: draw_petals(fr, shower_ps, t))
write_loop("glitter.mov", lambda fr, t: draw_glitter(fr, gl, t))
