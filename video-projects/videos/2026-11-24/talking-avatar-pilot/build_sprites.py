"""Turn the PoYo variants into a clean sprite set for the talking avatar.

usage: build_sprites.py [BASE_NAME]      (default base_0)

Only a feathered patch around the mouth (or the eyes, for the blink) is taken from each variant and laid over the
base, so hair, shirt and face stay pixel-identical across every frame. Green (#00B140) is keyed to alpha.

Output in avatar/sprites/ (all 2048x2048 RGBA, same canvas, stack with no offsets):
  base.png            keyed base, rest mouth (X), eyes open
  eyes_closed.png     eye patch only, transparent elsewhere
  mouth_<L>.png       mouth patch only, for L in A..H (X is the base itself)
  brows_up.png        eyebrows-raised patch (eyebrows + forehead), for emphasis
  eyes_left.png       eyes glancing toward the left (screen side), same region as eyes_closed
  sheet.jpg           preview of every mouth on a dark background

Batch 1 (2026-11-24): H came back as a sideways tongue lick and G looked like B, so they used C and B.
Batch 2 (2026-11-24): retakes mouth_G2 (teeth on lower lip) and mouth_H2 (tongue tip) replace those substitutes.
"""

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

P = Path(__file__).resolve().parent
RAW = P / "avatar" / "raw"
OUT = P / "avatar" / "sprites"

RETAKE = {"G": "mouth_G2"}   # batch 2 retake; mouth_H2 still read as a tongue poking out, so L keeps C
SUBST = {"H": "C"}

# Regions in 2048x2048 base coordinates, measured on base_0.
MOUTH = (1024, 1095, 215, 155)          # ellipse centre x, y, radius x, radius y
EYES = (760, 675, 1290, 830)            # rectangle
BROWS = (720, 470, 1330, 705)           # rectangle: eyebrows + forehead creases (stops above the eyelids)
FEATHER = 22


def mask_ellipse(size: int, cx: int, cy: int, rx: int, ry: int) -> np.ndarray:
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=255)
    return np.asarray(m.filter(ImageFilter.GaussianBlur(FEATHER))).astype(np.float32) / 255


def mask_rect(size: int, box: tuple[int, int, int, int]) -> np.ndarray:
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle(box, radius=60, fill=255)
    return np.asarray(m.filter(ImageFilter.GaussianBlur(FEATHER))).astype(np.float32) / 255


def key_alpha(rgb: np.ndarray) -> np.ndarray:
    """Alpha from green dominance, with despill so hair edges do not glow green."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    dom = g - np.maximum(r, b)                     # > ~40 on the backdrop, < 0 on skin, hair and shirt
    alpha = np.clip(1 - (dom - 12) / 40, 0, 1)
    return alpha


def despill(rgb: np.ndarray) -> np.ndarray:
    out = rgb.copy()
    cap = np.maximum(out[..., 0], out[..., 2])
    out[..., 1] = np.minimum(out[..., 1], cap)
    return out


def main() -> None:
    base_name = sys.argv[1] if len(sys.argv) > 1 else "base_0"
    OUT.mkdir(parents=True, exist_ok=True)
    base = np.asarray(Image.open(RAW / f"{base_name}.png").convert("RGB")).astype(np.float32)
    size = base.shape[0]
    alpha = key_alpha(base)
    clean = despill(base)
    rgba = np.dstack([clean, alpha * 255]).clip(0, 255).astype(np.uint8)
    Image.fromarray(rgba, "RGBA").save(OUT / "base.png")

    def patch(variant: str, mask: np.ndarray, name: str) -> None:
        v = np.asarray(Image.open(RAW / f"{variant}.png").convert("RGB")).astype(np.float32)
        a = mask * key_alpha(v)                      # never paint backdrop green into the patch
        out = np.dstack([despill(v), a * 255]).clip(0, 255).astype(np.uint8)
        Image.fromarray(out, "RGBA").save(OUT / f"{name}.png")

    patch("blink", mask_rect(size, EYES), "eyes_closed")
    mm = mask_ellipse(size, *MOUTH)
    for letter in "ABCDEFGH":
        source = RETAKE.get(letter) if (RAW / f"{RETAKE.get(letter, '')}.png").exists() else None
        patch(source or f"mouth_{SUBST.get(letter, letter)}", mm, f"mouth_{letter}")
    if (RAW / "brows.png").exists():
        patch("brows", mask_rect(size, BROWS), "brows_up")
    if (RAW / "glance.png").exists():
        patch("glance", mask_rect(size, EYES), "eyes_left")

    # preview sheet
    tiles = ["X", *"ABCDEFGH", "blink", "brows", "glance"]
    crop = (724, 640, 1324, 1240)
    sheet = Image.new("RGB", (6 * 300, 2 * 320), "#2b2f36")
    d = ImageDraw.Draw(sheet)
    b = Image.open(OUT / "base.png")
    for i, t in enumerate(tiles):
        frame = b.copy()
        if t == "blink":
            frame.alpha_composite(Image.open(OUT / "eyes_closed.png"))
        elif t == "brows":
            frame.alpha_composite(Image.open(OUT / "brows_up.png"))
        elif t == "glance":
            frame.alpha_composite(Image.open(OUT / "eyes_left.png"))
        elif t != "X":
            frame.alpha_composite(Image.open(OUT / f"mouth_{t}.png"))
        bg = Image.new("RGBA", frame.size, "#2b2f36")
        bg.alpha_composite(frame)
        x, y = (i % 6) * 300, (i // 6) * 320
        sheet.paste(bg.convert("RGB").crop(crop).resize((300, 300)), (x, y))
        d.text((x + 6, y + 304), t + (f" (= {SUBST[t]})" if t in SUBST else ""), fill="white")
    sheet.save(OUT / "sheet.jpg", quality=88)
    full = Image.new("RGBA", b.size, "#2b2f36")
    full.alpha_composite(b)
    full.convert("RGB").resize((512, 512)).save(OUT / "base_preview.jpg", quality=88)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
