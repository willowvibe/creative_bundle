"""Animated explainer diagrams on a dark screen (EP3, after the ByteMonk-style look the owner asked for).

Nodes (neon outline cards with a Lucide icon), connectors that draw on, glowing packets travelling along them,
captions, and per-node state changes (flash, colour change, "blocked" cross). Every frame is a pure function of time,
like render_scenes.py, so a Diagram is just another scene: `Diagram(...).draw(t) -> PIL.Image`.

Icons: Lucide (ISC licence) rasterized by icons/render_icons.py into icons/png/<name>.png (white on transparent).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

W, H = 1920, 1080
ICONS = Path(__file__).resolve().parent / "icons" / "png"
FB = "/usr/share/fonts/opentype/fira/FiraSans-Bold.otf"
FR = "/usr/share/fonts/opentype/fira/FiraSans-Regular.otf"
FM = "/usr/share/fonts/opentype/fira/FiraMono-Medium.otf"
COL = {"cyan": (64, 224, 255), "pink": (255, 80, 170), "green": (80, 240, 140), "yellow": (255, 212, 59),
       "purple": (176, 120, 255), "red": (255, 76, 76), "white": (235, 238, 245), "dim": (120, 118, 150)}

_FONTS: dict = {}
_ICON: dict = {}
_BG: list = []


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    if (path, size) not in _FONTS:
        _FONTS[(path, size)] = ImageFont.truetype(path, size)
    return _FONTS[(path, size)]


def ease(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def ease_back(u: float) -> float:
    u = min(1.0, max(0.0, u))
    c = 1.5
    return 1 + (c + 1) * (u - 1) ** 3 + c * (u - 1) ** 2


def icon(name: str, size: int, color: tuple) -> Image.Image:
    key = (name, size, color)
    if key not in _ICON:
        a = Image.open(ICONS / f"{name}.png").convert("RGBA").getchannel("A").resize((size, size), Image.LANCZOS)
        im = Image.new("RGBA", (size, size), color + (0,))
        im.putalpha(a)
        _ICON[key] = im
    return _ICON[key]


def background() -> Image.Image:
    """Near-black purple with a soft centre glow and a faint dot grid (cached)."""
    if not _BG:
        bg = Image.new("RGB", (W, H), (8, 6, 16))
        glow = Image.new("L", (W, H), 0)
        ImageDraw.Draw(glow).ellipse((W * 0.15, H * 0.05, W * 0.85, H * 1.05), fill=255)
        glow = glow.filter(ImageFilter.GaussianBlur(220))
        bg.paste(Image.new("RGB", (W, H), (34, 20, 64)), (0, 0), glow)
        d = ImageDraw.Draw(bg)
        for x in range(40, W, 48):
            for y in range(40, H, 48):
                d.point((x, y), fill=(40, 34, 66))
        _BG.append(bg)
    return _BG[0].copy()


@dataclass
class Node:
    x: float  # centre
    y: float
    label: str
    t: float  # appears at
    icon: str = ""
    sub: str = ""
    color: str = "cyan"
    w: int = 250
    h: int = 210
    states: list = field(default_factory=list)  # (time, "flash"|"red"|"green"|"blocked"|"dim")
    lines: list = field(default_factory=list)  # code/JSON card: mono lines instead of icon + label
    hl: list = field(default_factory=list)  # (time_from, time_to, line_index) highlighted code line
    code_size: int = 26

    def line_xy(self, i: int, side: str = "r") -> tuple:
        """Anchor at the left/right edge of code line i (for connectors pointing at a line)."""
        x0, y0, x1, y1 = self.rect()
        lh = int(self.code_size * 1.45)
        y = y0 + 64 + i * lh + lh / 2
        return (x1 if side == "r" else x0, y)

    def rect(self) -> tuple:
        return (self.x - self.w / 2, self.y - self.h / 2, self.x + self.w / 2, self.y + self.h / 2)


@dataclass
class Edge:
    a: str
    b: str
    t: float  # starts drawing at
    color: str = "cyan"
    label: str = ""
    packets: list = field(default_factory=list)  # (time, text, reverse?) a glowing dot a->b (or b->a)
    via: list = field(default_factory=list)  # extra corner points for elbow routes
    dur: float = 0.6
    until: float = 1e9  # fades out over 0.4 s before this time


@dataclass
class Caption:
    t: float
    x: float
    y: float
    text: str
    size: int = 34
    color: str = "white"
    mono: bool = False
    until: float = 1e9
    anchor: str = "mm"


@dataclass
class Diagram:
    nodes: dict
    edges: list = field(default_factory=list)
    captions: list = field(default_factory=list)
    title: tuple = ()  # (time, text)

    # ---------------------------------------------------------------- geometry
    def _anchor(self, n: Node, toward: tuple) -> tuple:
        """Point on the card's border facing `toward`."""
        x0, y0, x1, y1 = n.rect()
        dx, dy = toward[0] - n.x, toward[1] - n.y
        if abs(dx) * n.h >= abs(dy) * n.w:
            return (x1 if dx > 0 else x0, n.y)
        return (n.x, y1 if dy > 0 else y0)

    def _path(self, e: Edge) -> list:
        """Endpoints are node names or fixed (x, y) points (e.g. Node.line_xy for a code line)."""
        def centre(k):
            return k if isinstance(k, tuple) else (self.nodes[k].x, self.nodes[k].y)
        first = e.via[0] if e.via else centre(e.b)
        last = e.via[-1] if e.via else centre(e.a)
        pa = e.a if isinstance(e.a, tuple) else self._anchor(self.nodes[e.a], first)
        pb = e.b if isinstance(e.b, tuple) else self._anchor(self.nodes[e.b], last)
        return [pa, *e.via, pb]

    @staticmethod
    def _along(pts: list, u: float) -> tuple[list, tuple]:
        """Polyline cut at fraction u of its length, and the point there."""
        segs = [math.dist(p, q) for p, q in zip(pts, pts[1:])]
        total = sum(segs) or 1.0
        left = total * min(1.0, max(0.0, u))
        out = [pts[0]]
        for (p, q), s in zip(zip(pts, pts[1:]), segs):
            if left <= s:
                f = left / s if s else 0
                pt = (p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f)
                out.append(pt)
                return out, pt
            out.append(q)
            left -= s
        return out, pts[-1]

    # ---------------------------------------------------------------- drawing
    def _node_color(self, n: Node, t: float) -> tuple[tuple, float, bool]:
        col, pulse, blocked = COL[n.color], 0.0, False
        for st, kind in n.states:
            if t < st:
                continue
            if kind in ("red", "green", "yellow", "pink", "purple", "cyan"):
                col = COL[kind]
            elif kind == "dim":
                col = COL["dim"]
            elif kind == "blocked":
                blocked = True
            if kind == "flash" or kind in ("red", "green"):
                pulse = max(pulse, max(0.0, 1 - (t - st) / 0.7))
        return col, pulse, blocked

    def draw(self, t: float) -> Image.Image:
        im = background()
        sharp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ds, dg = ImageDraw.Draw(sharp), ImageDraw.Draw(glow)

        if self.title and t >= self.title[0]:
            a = int(255 * ease((t - self.title[0]) / 0.5))
            ds.text((W / 2, 96), self.title[1], font=font(FB, 54), fill=COL["white"] + (a,), anchor="mm")

        labels: list = []  # packet labels are drawn last, over the cards
        for e in self.edges:  # connectors under the cards
            if t < e.t or t >= e.until:
                continue
            col = COL[e.color]
            if e.until - t < 0.4:  # fading out: dim the colour toward the background
                k = (e.until - t) / 0.4
                col = tuple(int(c * k + 12 * (1 - k)) for c in col)
            pts = self._path(e)
            drawn, tip = self._along(pts, (t - e.t) / e.dur)
            if len(drawn) > 1:
                dg.line(drawn, fill=col + (255,), width=10, joint="curve")
                ds.line(drawn, fill=col + (230,), width=4, joint="curve")
            if e.label and t >= e.t + e.dur:
                mid = self._along(pts, 0.5)[1]
                a = int(255 * ease((t - e.t - e.dur) / 0.4))
                f = font(FM, 24)
                tw = ds.textlength(e.label, font=f)
                ds.rounded_rectangle((mid[0] - tw / 2 - 14, mid[1] - 40, mid[0] + tw / 2 + 14, mid[1] - 6), 8,
                                     fill=(14, 10, 28, int(a * 0.9)), outline=col + (a,), width=2)
                ds.text((mid[0], mid[1] - 23), e.label, font=f, fill=col + (a,), anchor="mm")
            for pt_t, text, rev in e.packets:
                u = (t - pt_t) / 0.9
                if not (0 <= u <= 1):
                    continue
                p = self._along(pts[::-1] if rev else pts, ease(u))[1]
                dg.ellipse((p[0] - 18, p[1] - 18, p[0] + 18, p[1] + 18), fill=col + (255,))
                ds.ellipse((p[0] - 8, p[1] - 8, p[0] + 8, p[1] + 8), fill=(255, 255, 255, 255))
                if text:
                    labels.append((p, text, col))

        for n in self.nodes.values():
            if t < n.t:
                continue
            u = ease_back((t - n.t) / 0.5)
            a = int(255 * ease((t - n.t) / 0.3))
            col, pulse, blocked = self._node_color(n, t)
            s = 0.6 + 0.4 * u
            x0, y0, x1, y1 = n.rect()
            cx, cy = n.x, n.y
            r = (cx + (x0 - cx) * s, cy + (y0 - cy) * s, cx + (x1 - cx) * s, cy + (y1 - cy) * s)
            gw = 8 + int(10 * pulse)
            dg.rounded_rectangle(r, 22, outline=col + (int(a * (0.8 + 0.2 * pulse)),), width=gw)
            ds.rounded_rectangle(r, 22, fill=(14, 10, 28, int(a * 0.92)), outline=col + (a,), width=3)
            if n.lines:  # code / JSON card
                if n.label:
                    ds.text((r[0] + 26, r[1] + 16), n.label, font=font(FB, int(26 * s)), fill=col + (a,))
                f = font(FM, max(8, int(n.code_size * s)))
                lh = int(n.code_size * 1.45 * s)
                for i, line in enumerate(n.lines):
                    y = r[1] + 64 * s + i * lh
                    for h0, h1, li in n.hl:
                        if li == i and h0 <= t < h1:
                            k = ease((t - h0) / 0.3) * (1 - ease((t - (h1 - 0.3)) / 0.3))
                            ds.rectangle((r[0] + 8, y - 4, r[2] - 8, y + lh - 4), fill=col + (int(60 * k),))
                    ds.text((r[0] + 26, y), line, font=f, fill=COL["white"] + (a,))
                continue
            if n.icon:
                size = int(min(96, n.h * 0.42) * s)
                ic = icon(n.icon, size, col)
                if a < 255:
                    ic = ic.copy()
                    ic.putalpha(ic.getchannel("A").point(lambda v: v * a // 255))
                ly = r[3] - (64 if n.sub else 40) * s
                top = r[1] + ((ly - 24 * s) - r[1] - size) / 2
                sharp.alpha_composite(ic, (int(cx - size / 2), int(top)))
                glow.alpha_composite(ic, (int(cx - size / 2), int(top)))
            else:
                ly = cy - (14 if n.sub else 0)
            ds.text((cx, ly), n.label, font=font(FB, int(34 * s)), fill=COL["white"] + (a,), anchor="mm")
            if n.sub:
                ds.text((cx, ly + 36 * s), n.sub, font=font(FM, int(22 * s)), fill=COL["dim"] + (a,), anchor="mm")
            if blocked:
                d = 70
                for p, q in (((cx - d, cy - d), (cx + d, cy + d)), ((cx - d, cy + d), (cx + d, cy - d))):
                    dg.line((p, q), fill=COL["red"] + (255,), width=26)
                    ds.line((p, q), fill=COL["red"] + (255,), width=12)

        for p, text, col in labels:
            f = font(FM, 26)
            tw = ds.textlength(text, font=f)
            ds.rounded_rectangle((p[0] - tw / 2 - 14, p[1] - 66, p[0] + tw / 2 + 14, p[1] - 28), 10,
                                 fill=(14, 10, 28, 240), outline=col + (255,), width=2)
            ds.text((p[0], p[1] - 47), text, font=f, fill=COL["white"] + (255,), anchor="mm")

        for c in self.captions:
            if not (c.t <= t < c.until):
                continue
            a = int(255 * ease((t - c.t) / 0.45) * (1 - ease((t - (c.until - 0.3)) / 0.3)))
            dy = 18 * (1 - ease((t - c.t) / 0.45))
            ds.text((c.x, c.y + dy), c.text, font=font(FM if c.mono else FB, c.size), fill=COL[c.color] + (a,),
                    anchor=c.anchor)

        small = glow.resize((W // 2, H // 2), Image.BILINEAR)
        wide = small.filter(ImageFilter.GaussianBlur(14))
        near = small.filter(ImageFilter.GaussianBlur(4))
        base = im.convert("RGB")
        lit = base
        for layer, k in ((wide, 2.2), (near, 1.3)):
            g = layer.resize((W, H), Image.BILINEAR)
            m = g.getchannel("A").point(lambda v, k=k: min(255, int(v * k)))
            lit = ImageChops.screen(lit, Image.composite(g.convert("RGB"), Image.new("RGB", (W, H)), m))
        out = lit.convert("RGBA")
        out.alpha_composite(sharp)
        return out.convert("RGB")
