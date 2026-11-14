"""Background plate and chant-title overlay for the Om Saravana Bhava video (PIL with raqm shaping for Tamil).

usage: python make_art.py OUT_DIR
writes bg.png (2304x1296, room for a slow drift crop) and title.png (1920x1080 RGBA)
"""

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(108)
NOTO = "/usr/share/fonts/truetype/noto/"

# ---------- background: warm temple darkness, golden glow, soft bokeh ----------
W, H = 2304, 1296
y, x = np.mgrid[0:H, 0:W].astype(np.float32)
r = np.sqrt(((x - W / 2) / (W * 0.55)) ** 2 + ((y - H * 0.46) / (H * 0.62)) ** 2)
deep = np.array([14, 6, 8], np.float32)  # near-black maroon
mid = np.array([92, 28, 18], np.float32)  # kumkum red-brown
gold = np.array([255, 178, 70], np.float32)
t = np.clip(1 - r, 0, 1)[..., None]
img = deep + (mid - deep) * t**1.6 + (gold - mid) * (t**5.0) * 0.55
# peacock-blue hint in the far corners
corner = np.clip(r - 0.95, 0, 1)[..., None]
img = img * (1 - 0.5 * corner) + np.array([10, 40, 52], np.float32) * 0.5 * corner
base = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))

bokeh = Image.new("RGB", (W, H), (0, 0, 0))
d = ImageDraw.Draw(bokeh)
for _ in range(90):
    cx, cy = rng.uniform(0, W), rng.uniform(0, H)
    rad = rng.uniform(12, 70)
    a = rng.uniform(0.15, 0.55) * (0.4 + 0.6 * np.exp(-(((cx - W / 2) / W) ** 2 + ((cy - H / 2) / H) ** 2) * 3))
    col = tuple(int(c * a) for c in (255, rng.uniform(150, 200), rng.uniform(60, 110)))
    d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=col)
bokeh = bokeh.filter(ImageFilter.GaussianBlur(14))
bg = Image.fromarray(np.clip(np.asarray(base, np.float32) + np.asarray(bokeh, np.float32) * 0.6, 0, 255).astype(np.uint8))
bg.save(OUT / "bg.png")

# ---------- overlays: Om sign (background section only) and chant caption (whole video) ----------
TW, TH = 1920, 1080
CAP_Y = int(TH * 0.75)  # user: captions centred at the 75% line, Tamil + English, a bit big


def text_center(draw, txt, font, cy, fill):
    box = draw.textbbox((0, 0), txt, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    draw.text(((TW - w) / 2 - box[0], cy - h / 2 - box[1]), txt, font=font, fill=fill)


om_font = ImageFont.truetype(NOTO + "NotoSerifTamil-Bold.ttf", 300)
ta_font = ImageFont.truetype(NOTO + "NotoSerifTamil-Bold.ttf", 104)
en_font = ImageFont.truetype(NOTO + "NotoSerif-Italic.ttf", 66)

# Om sign with a warm glow
om = Image.new("RGBA", (TW, TH), (0, 0, 0, 0))
glow = Image.new("RGBA", (TW, TH), (0, 0, 0, 0))
text_center(ImageDraw.Draw(glow), "ௐ", om_font, 360, (255, 170, 60, 255))
glow = glow.filter(ImageFilter.GaussianBlur(28))
om = Image.alpha_composite(Image.alpha_composite(om, glow), glow)
text_center(ImageDraw.Draw(om), "ௐ", om_font, 360, (255, 214, 130, 255))
om.save(OUT / "om.png")

# caption: Tamil line above, English below, block centred on CAP_Y; soft dark halo keeps it legible on images
TA_Y, EN_Y = CAP_Y - 42, CAP_Y + 62
cap = Image.new("RGBA", (TW, TH), (0, 0, 0, 0))
halo = Image.new("RGBA", (TW, TH), (0, 0, 0, 0))
hd = ImageDraw.Draw(halo)
text_center(hd, "ஓம் சரவண பவ", ta_font, TA_Y, (0, 0, 0, 230))
text_center(hd, "Om Saravana Bhava", en_font, EN_Y, (0, 0, 0, 230))
halo = halo.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(14))
cap = Image.alpha_composite(cap, halo)
cd = ImageDraw.Draw(cap)
text_center(cd, "ஓம் சரவண பவ", ta_font, TA_Y, (255, 222, 150, 255))
text_center(cd, "Om Saravana Bhava", en_font, EN_Y, (255, 236, 200, 245))
cap.save(OUT / "caption.png")

# soft bottom shade used only under the Muruga images, so the 75% caption stays legible on busy art
yy = np.linspace(0, 1, TH, dtype=np.float32)
alpha = (np.clip((yy - 0.52) / 0.48, 0, 1) ** 1.4 * 170).astype(np.uint8)
shade = np.zeros((TH, TW, 4), np.uint8)
shade[..., 3] = alpha[:, None]
Image.fromarray(shade, "RGBA").save(OUT / "shade.png")
print("wrote", OUT / "bg.png", OUT / "om.png", OUT / "caption.png", "caption centre y", CAP_Y)
