"""Thumbnail: avatar + big '3.15' + 3-word hook + a real code chip. usage: python3 make_thumb.py OUT_DIR"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(sys.argv[1])
AVATAR = Path(__file__).resolve().parents[2] / "talking-avatar-pilot/avatar/sprites/base.png"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
MONO = "/usr/share/fonts/opentype/fira/FiraMono-Regular.otf"
W, H = 1280, 720


def make(hook: str, name: str) -> None:
    im = Image.new("RGB", (W, H), "#0f1630")
    d = ImageDraw.Draw(im)
    for y in range(H):  # blue -> deep navy gradient
        c = int(40 * (1 - y / H))
        d.line((0, y, W, y), fill=(15 + c // 2, 22 + c, 48 + c * 2))
    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).ellipse((700, 40, 1300, 760), fill=150)
    im.paste(Image.new("RGB", (W, H), "#ffd43b"), (0, 0), glow.filter(ImageFilter.GaussianBlur(120)).point(lambda v: v // 3))
    av = Image.open(AVATAR).convert("RGBA")
    bbox = av.getbbox()
    av = av.crop(bbox)
    scale = 700 / av.height
    av = av.resize((int(av.width * scale), 700), Image.LANCZOS)
    im.paste(av, (W - av.width + 40, H - 680), av)
    d = ImageDraw.Draw(im)
    d.text((54, 40), "PYTHON", font=ImageFont.truetype(BOLD, 92), fill="#ffffff", stroke_width=4, stroke_fill="#0b1020")
    d.text((44, 120), "3.15", font=ImageFont.truetype(BOLD, 250), fill="#ffd43b", stroke_width=8, stroke_fill="#0b1020")
    d.rounded_rectangle((52, 420, 640, 500), 16, fill="#1e1f26", outline="#4b8bbe", width=4)
    d.text((76, 436), "lazy import json", font=ImageFont.truetype(MONO, 44), fill="#c792ea")
    hf = ImageFont.truetype(BOLD, 74)
    d.rounded_rectangle((44, 540, 60 + d.textlength(hook, font=hf) + 30, 650), 18, fill="#e8384f")
    d.text((70, 550), hook, font=hf, fill="#ffffff")
    im.save(OUT / f"{name}.jpg", quality=92)
    im.resize((320, 180), Image.LANCZOS).save(OUT / f"{name}_320.png")


if __name__ == "__main__":
    make("6 NEW TOOLS", "thumb_a")
    make("IT'S OUT!", "thumb_b")
