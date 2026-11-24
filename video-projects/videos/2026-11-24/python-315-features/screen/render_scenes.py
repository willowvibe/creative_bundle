"""Draw the Python 3.15 video's screen track: real web pages (scroll + zoom) and an editor/terminal, timed to narration.

usage: uv run python render_scenes.py WORDS.json OUTPUTS.json OUT.mp4 [--from S] [--to S]

- Web scenes crop the full-page captures from capture_pages.py (python.org, docs.python.org, peps.python.org and the real
  Tachyon flame graph) and ease between keyframes.
- Editor scenes show the real demo files (../demos) typed in on cue words; terminal output is text recorded by
  capture_outputs.py, never typed by hand.
- Cue phrases are matched against Whisper word timings (audio/words.json), so actions land on the spoken word.
- The bottom-right corner (x > 1500, y > 700) carries no text in editor scenes: the avatar sits there.
Every frame is a pure function of time.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pygments.lexers import PythonLexer
from pygments.token import Comment, Keyword, Name, Number, Operator, String, Token

HERE = Path(__file__).resolve().parent
DEMOS = HERE.parent / "demos"
WEB = HERE / "web"
W, H, FPS = 1920, 1080, 30
MONO = "/usr/share/fonts/opentype/fira/FiraMono-Regular.otf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
F_CODE = ImageFont.truetype(MONO, 26)
F_TERM = ImageFont.truetype(MONO, 21)
F_UI = ImageFont.truetype(SANS, 19)
F_URL = ImageFont.truetype(SANS, 20)
# Fira Mono lacks Telugu: fall back per character
F_TELUGU = ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoSansTelugu-Regular.ttf", 20) \
    if Path("/usr/share/fonts/truetype/noto/NotoSansTelugu-Regular.ttf").exists() else F_TERM
F_SYM = ImageFont.truetype(SANS, 21)  # ☕ and other symbols missing from Fira Mono
C = {"bg": "#0d1117", "win": "#1e1f26", "bar": "#2b2d38", "term": "#111217", "text": "#e6e6e6", "dim": "#7c8193",
     "kw": "#c792ea", "str": "#c3e88d", "fn": "#82aaff", "num": "#f78c6c", "com": "#697098", "op": "#89ddff",
     "ok": "#7ee787", "err": "#ff7b72", "ln": "#4b5063", "hl": (255, 209, 102), "new": (126, 231, 135)}

WIN = (36, 26, 1884, 1054)  # window rectangle
TITLE_H = 42


# ---------------------------------------------------------------- cues
def norm(word: str) -> str:
    return re.sub(r"[^a-z0-9'.]", "", word.lower()).strip(".")


class Cues:
    def __init__(self, words: list) -> None:
        self.toks = [norm(w[0]) for w in words]
        self.times = [(w[1], w[2]) for w in words]
        self.pos = 0

    def at(self, phrase: str, offset: float = 0.0) -> float:
        """Start time of the next occurrence of `phrase` at or after the cursor."""
        want = [norm(p) for p in phrase.split()]
        for i in range(self.pos, len(self.toks) - len(want) + 1):
            if self.toks[i:i + len(want)] == want:
                self.pos = i  # the same phrase may be asked for again (start of one action, end of another)
                return self.times[i][0] + offset
        raise KeyError(f"cue not found after word {self.pos}: {phrase!r}")

    def end(self, phrase: str) -> float:
        """End time of the last word of the next occurrence of `phrase`."""
        want = [norm(p) for p in phrase.split()]
        for i in range(self.pos, len(self.toks) - len(want) + 1):
            if self.toks[i:i + len(want)] == want:
                self.pos = i + 1
                return self.times[i + len(want) - 1][1]
        raise KeyError(f"cue not found after word {self.pos}: {phrase!r}")


def ease(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


# ---------------------------------------------------------------- shared chrome
def window(d: ImageDraw.ImageDraw, title: str) -> None:
    x0, y0, x1, y1 = WIN
    d.rounded_rectangle((x0, y0, x1, y1), 14, fill=C["win"])
    d.rounded_rectangle((x0, y0, x1, y0 + TITLE_H), 14, fill=C["bar"])
    d.rectangle((x0, y0 + 24, x1, y0 + TITLE_H), fill=C["bar"])
    for i, col in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        d.ellipse((x0 + 18 + i * 24, y0 + 14, x0 + 32 + i * 24, y0 + 28), fill=col)
    tw = d.textlength(title, font=F_UI)
    d.text(((x0 + x1 - tw) / 2, y0 + 10), title, font=F_UI, fill="#b9bdc9")


# ---------------------------------------------------------------- web scenes
_IMG: dict[str, Image.Image] = {}


def page(name: str) -> Image.Image:
    if name not in _IMG:
        _IMG[name] = Image.open(WEB / f"{name}.png").convert("RGB")
    return _IMG[name]


@dataclass
class Web:
    """keys: (time, image, y_top, zoom, x_center). Moves ease over `move` seconds after each key time."""
    url: str
    keys: list
    move: float = 1.3
    tab: str = ""

    def view(self, t: float) -> tuple:
        prev = self.keys[0]
        for k in self.keys[1:]:
            if t < k[0]:
                break
            prev_k, prev = prev, k
            if t < k[0] + self.move:
                u = ease((t - k[0]) / self.move)
                if prev_k[1] != k[1]:  # different image: cut at the key time
                    return k[1:]
                return (k[1],) + tuple(a + (b - a) * u for a, b in zip(prev_k[2:], k[2:]))
        return prev[1:]

    def draw(self, t: float) -> Image.Image:
        im = Image.new("RGB", (W, H), C["bg"])
        d = ImageDraw.Draw(im)
        x0, y0, x1, y1 = WIN
        d.rounded_rectangle((x0, y0, x1, y1), 14, fill="#dfe1e5")
        # tab strip + address bar (browser-style chrome drawn here; the page pixels are the real capture)
        d.rounded_rectangle((x0, y0, x1, y0 + 44), 14, fill="#dfe1e5")
        for i, col in enumerate(("#ff5f57", "#febc2e", "#28c840")):
            d.ellipse((x0 + 18 + i * 24, y0 + 15, x0 + 32 + i * 24, y0 + 29), fill=col)
        d.rounded_rectangle((x0 + 110, y0 + 8, x0 + 470, y0 + 44), 10, fill="#ffffff")
        d.text((x0 + 128, y0 + 15), self.tab, font=F_UI, fill="#202124")
        d.rectangle((x0, y0 + 44, x1, y0 + 88), fill="#ffffff")
        d.rounded_rectangle((x0 + 120, y0 + 51, x1 - 120, y0 + 81), 15, fill="#f1f3f4")
        d.text((x0 + 145, y0 + 55), self.url, font=F_URL, fill="#202124")
        name, ytop, zoom, xc = self.view(t)
        src = page(name)
        cw, ch = (x1 - x0), (y1 - y0 - 88)
        sw, sh = cw / zoom, ch / zoom
        sx = min(max(0.0, xc - sw / 2), src.width - sw)
        sy = min(max(0.0, ytop), max(0.0, src.height - sh))
        crop = src.crop((int(sx), int(sy), int(sx + sw), int(sy + sh))).resize((cw, ch), Image.BILINEAR)
        mask = Image.new("L", (cw, ch), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, -20, cw - 1, ch - 1), 14, fill=255)
        im.paste(crop, (x0, y0 + 88), mask)
        return im


# ---------------------------------------------------------------- editor + terminal scenes
STYLE = [(Comment, "com"), (Keyword, "kw"), (String, "str"), (Number, "num"), (Name.Function, "fn"),
         (Name.Builtin, "fn"), (Operator, "op")]


def highlight(line: str) -> list[tuple[str, str]]:
    out = []
    for tok, text in PythonLexer().get_tokens(line):
        text = text.rstrip("\n")
        if not text:
            continue
        col = "text"
        for ttype, name in STYLE:
            if tok in ttype:
                col = name
                break
        if line.lstrip().startswith("lazy ") and text == "lazy":
            col = "kw"  # pygments does not know the 3.15 soft keyword yet
        if text in ("frozendict", "sentinel"):
            col = "fn"
        out.append((text, col))
    return out


def term_color(line: str) -> str:
    if line.startswith(("Traceback", "TypeError", "SyntaxError")) or line.strip().startswith(("^", "~")):
        return "err"
    return "text"


def _fallback(ch: str, size: int):
    if "\u0c00" <= ch <= "\u0c7f":
        return "telugu"
    if ord(ch) > 0x2000:
        return "sym"
    return None


_FB: dict = {}


def _fb_font(kind: str, size: int):
    key = (kind, size)
    if key not in _FB:
        path = "/usr/share/fonts/truetype/noto/NotoSansTelugu-Regular.ttf" if kind == "telugu" else SANS
        _FB[key] = ImageFont.truetype(path, size)
    return _FB[key]


def draw_text(d, x, y, text, col, font):
    """Draw text in runs per font (Telugu and symbols fall back), so Telugu conjuncts are shaped as a whole."""
    fill = C[col] if isinstance(col, str) else col
    size = font.size
    runs: list[tuple[object, str]] = []
    for ch in text:
        kind = _fallback(ch, size)
        if runs and runs[-1][0] == kind:
            runs[-1] = (kind, runs[-1][1] + ch)
        else:
            runs.append((kind, ch))
    for kind, part in runs:
        f = font if kind is None else _fb_font(kind, size)
        dy = -int(size * 0.12) if kind == "telugu" else 0
        d.text((x, y + dy), part, font=f, fill=fill)
        x += f.getlength(part)
    return x


@dataclass
class Code:
    """Editor + terminal. Events are (time, kind, payload):
    ("type", t, (line_no, text))  types `text` into line `line_no` (1-based), replacing it
    ("show", t, [lines])          the whole file appears at once
    ("hl", t, (a, b, until))      highlight lines a..b until `until`
    ("cmd", t, key)               types outputs[key]["cmd"] at the prompt, output follows `delay` later
    ("out", t, (key, delay, keep)) output of outputs[key] appears `delay` s after t; keep = list of line filters
    ("tab", t, name)              rename the editor tab
    """
    title: str
    events: list
    outputs: dict
    editor_h: int = 540
    tab0: str = ""
    term_lines: int = 12
    flash: list = field(default_factory=list)
    font_size: int = 26
    lh: int = 37
    first: int = 1  # first visible editor line
    term_size: int = 21

    def draw(self, t: float) -> Image.Image:
        im = Image.new("RGB", (W, H), C["bg"])
        d = ImageDraw.Draw(im, "RGBA")
        window(d, self.title)
        x0, y0, x1, y1 = WIN
        top = y0 + TITLE_H
        tab = self.tab0
        lines: dict[int, str] = {}
        typing: dict[int, tuple[str, float]] = {}
        highlights = []
        term: list[tuple[str, str]] = [("~/demo $ ", "ok")]
        term_rows: list[list[tuple[str, str]]] = [[("~/demo $ ", "ok")]]
        caret = None
        for ev in sorted(self.events, key=lambda e: e[1]):
            kind, et, pay = ev[0], ev[1], ev[2]
            if t < et:
                continue
            if kind == "tab":
                tab = pay
            elif kind == "show":
                lines = {i + 1: s for i, s in enumerate(pay)}
            elif kind == "type":
                no, text = pay
                dur = min(2.4, 0.5 + len(text) / 38)
                if t < et + dur:
                    typing[no] = (text, (t - et) / dur)
                lines[no] = text
            elif kind == "hl":
                a, b, until = pay
                if t < until:
                    highlights.append((a, b, min(1.0, (t - et) / 0.35)))
            elif kind == "cmd":
                cmd = self.outputs[pay]["cmd"]
                dur = min(2.0, 0.4 + len(cmd) / 30)
                n = len(cmd) if t >= et + dur else int((t - et) / dur * len(cmd))
                term_rows[-1] = [("~/demo $ ", "ok"), (cmd[:n], "text")]
                if n < len(cmd):
                    caret = ("term", n)
            elif kind == "out":
                key, delay, keep = pay
                if t < et + delay:
                    continue
                text = self.outputs[key]["out"].splitlines()
                if keep:
                    text = [s for s in text if keep(s)]
                rows_shown = int((t - et - delay) / 0.035) + 1  # fast scroll-in like a real terminal
                for s in text[:rows_shown]:
                    term_rows.append([(s, term_color(s))])
                if rows_shown >= len(text):
                    term_rows.append([("~/demo $ ", "ok")])
        font = ImageFont.truetype(MONO, self.font_size)
        if self.editor_h:  # tab
            d.rectangle((x0, top, x1, top + 40), fill="#181920")
            d.rectangle((x0 + 2, top, x0 + 26 + d.textlength(tab, font=F_UI) + 24, top + 40), fill=C["win"])
            d.text((x0 + 24, top + 9), tab, font=F_UI, fill=C["text"])
        # editor
        ey = top + 58
        lh = self.lh
        ex = x0 + 30
        for a, b, alpha in highlights:
            d.rectangle((x0 + 4, ey + (a - self.first) * lh - 4, x1 - 4, ey + (b - self.first + 1) * lh - 6),
                        fill=C["hl"] + (int(55 * alpha),))
        for no in sorted(lines):
            if no < self.first:
                continue
            y = ey + (no - self.first) * lh
            if y > top + self.editor_h - lh:
                break
            d.text((ex, y), f"{no:>2}", font=font, fill=C["ln"])
            text = lines[no]
            toks = highlight(text)
            limit = len(text)
            if no in typing:
                limit = int(len(typing[no][0]) * typing[no][1])
            x = ex + 66
            left = limit
            for s, col in toks:
                if left <= 0:
                    break
                part = s[:left]
                x = draw_text(d, x, y, part, col, font)
                left -= len(s)
            if no in typing:
                caret = ("ed", (x, y))
        # terminal
        ty = top + self.editor_h
        d.rectangle((x0, ty, x1, y1 - 14), fill=C["term"])
        d.rounded_rectangle((x0, ty, x1, y1), 14, fill=C["term"])
        d.line((x0, ty, x1, ty), fill="#333640", width=2)
        d.text((x0 + 24, ty + 8), "TERMINAL", font=F_UI, fill=C["dim"])
        rows = term_rows[-self.term_lines:]
        tfont = F_TERM if self.term_size == 21 else ImageFont.truetype(MONO, self.term_size)
        tl = int(self.term_size * 1.4)
        for j, row in enumerate(rows):
            x = x0 + 24
            y = ty + 40 + j * tl
            for s, col in row:
                x = draw_text(d, x, y, s, col, tfont)
            if j == len(rows) - 1:
                last_x = x
        blink = (t * 2) % 2 < 1.3
        if caret and caret[0] == "ed":
            cx, cy = caret[1]
            d.rectangle((cx + 2, cy + 3, cx + 5, cy + self.font_size + 6), fill=C["text"])
        elif blink:
            yb = ty + 40 + (len(rows) - 1) * tl
            d.rectangle((last_x + 2, yb + 2, last_x + self.term_size * 0.6, yb + self.term_size + 4), fill=C["text"])
        return im


def demo(name: str) -> list[str]:
    return (DEMOS / name).read_text().rstrip("\n").splitlines()


# ---------------------------------------------------------------- the timeline
def build(words: list, outputs: dict) -> list[tuple[float, object]]:
    q = Cues(words)
    scenes: list[tuple[float, object]] = []

    # S0 hook: python.org downloads
    s0 = 0.0
    dl = Web("python.org/downloads/", tab="Download Python | Python.org", keys=[
        (s0, "downloads", 0, 1.0, 960),
        (q.at("latest version"), "downloads", 150, 1.7, 640),
        (q.at("new keywords"), "downloads", 640, 1.15, 960),
        (q.at("here are 6"), "downloads", 1690, 1.3, 960),
    ])
    scenes.append((s0, dl))

    # S1 install with uv
    s1 = q.at("first let's install")
    cmd_t = q.at("one command")
    scenes.append((s1, Code("Terminal — uv", outputs=outputs, editor_h=0, term_lines=18, tab0="", term_size=32, events=[
        ("cmd", cmd_t, "install"),
        ("out", cmd_t, ("install", max(1.6, q.at("a few seconds later") - cmd_t), None)),
        ("cmd", q.at("no installer"), "version"),
        ("out", q.at("no installer"), ("version", 1.2, None)),
    ])))

    # S2 lazy imports
    s2 = q.at("feature one")
    wn = "docs.python.org/3.15/whatsnew/3.15.html"
    scenes.append((s2, Web(wn, tab="What's new in Python 3.15", keys=[
        (s2, "whatsnew__pep-810-explicit-lazy-imports", 60, 1.55, 940)])))
    p_t = q.at("here is the problem")
    eager = demo("cli_eager.py")
    lazy = demo("cli_lazy.py")
    ev = [("show", p_t, eager),
          ("hl", q.at("eight modules"), (1, 8, q.at("ask for the version"))),
          ("hl", q.at("ask for the version"), (10, 14, q.at("still loads all eight") + 2.0))]
    lt = q.at("put the new lazy")
    ev.append(("tab", lt, "cli_lazy.py"))
    for k, ln in enumerate(range(2, 9)):
        ev.append(("type", lt + 0.25 * k, (ln, lazy[ln - 1])))
    ev.append(("hl", lt, (2, 8, q.at("same code") + 2.5)))
    bt = q.at("i timed both")
    ev += [("cmd", bt, "bench_eager"), ("out", bt, ("bench_eager", max(1.8, q.at("normal imports took") - bt), None)),
           ("cmd", q.at("normal imports took", 2.4), "bench_lazy")]
    lz = q.at("lazy version took")
    ev.append(("out", lz - 1.0, ("bench_lazy", 0.9, None)))
    scenes.append((p_t, Code("cli_eager.py — Visual Studio Code", outputs=outputs, tab0="cli_eager.py", events=ev)))
    huge = q.at("this is huge")
    scenes.append((huge, Web(wn, tab="What's new in Python 3.15", keys=[
        (huge, "whatsnew__pep-810-explicit-lazy-imports", 900, 1.55, 940),
        (huge + 0.1, "whatsnew__pep-810-explicit-lazy-imports", 1150, 1.55, 940)], move=3.0)))

    # S3 frozendict
    s3 = q.at("feature two")
    scenes.append((s3, Web(wn, tab="What's new in Python 3.15", keys=[
        (s3, "whatsnew__pep-814-add-frozendict-built-in-type", 60, 1.55, 940),
        (s3 + 2.5, "whatsnew__pep-814-add-frozendict-built-in-type", 420, 1.55, 940)], move=4.0)))
    hs = q.at("because it is hashable")
    cfg = demo("config_cache.py")
    ev = [("type", hs, (1, cfg[0]))]
    ky = q.at("use a whole settings")
    ev += [("type", ky, (3, cfg[2])), ("type", ky + 1.6, (4, cfg[3]))]
    run_t = q.at("here i look up")
    ev += [("cmd", run_t, "config"),
           ("out", run_t, ("config", max(1.2, q.at("and it finds it") - run_t), lambda s: s == "connection #1"))]
    cp = q.at("modified copy")
    ev += [("type", cp, (6, cfg[5])), ("type", cp + 1.4, (7, cfg[6]))]
    ch = q.at("change it in place")
    ev += [("type", ch, (9, cfg[8])), ("cmd", ch + 1.8, "config"),
           ("out", ch + 1.8, ("config", max(1.0, q.at("python stops you") - ch - 1.8), None)),
           ("hl", q.at("python stops you"), (9, 9, q.at("perfect for configuration") + 3))]
    scenes.append((hs, Code("config_cache.py — Visual Studio Code", outputs=outputs, tab0="config_cache.py", events=ev)))

    # S4 sentinel
    s4 = q.at("feature three")
    scenes.append((s4, Web(wn, tab="What's new in Python 3.15", keys=[
        (s4, "whatsnew__pep-661-add-sentinel-built-in-type", 60, 1.55, 940),
        (s4 + 3, "whatsnew__pep-661-add-sentinel-built-in-type", 380, 1.55, 940)], move=5.0)))
    nb = q.at("now there's a built")
    sn = demo("sentinel_demo.py")
    ev = [("type", q.at("missing equals sentinel"), (1, sn[0]))]
    cg = q.at("call get setting")
    ev += [("type", cg - 1.6, (3, sn[2])), ("type", cg - 0.8, (4, sn[3])), ("type", cg - 0.2, (5, sn[4])),
           ("type", cg + 0.4, (6, sn[5])), ("type", cg + 1.2, (8, sn[7])),
           ("cmd", cg + 2.4, "sentinel")]
    pn = q.at("pass none")
    ev += [("type", pn - 0.6, (9, sn[8])), ("type", pn + 0.2, (10, sn[9])),
           ("out", pn + 0.2, ("sentinel", max(1.0, q.at("when you print it") - pn), None)),
           ("hl", q.at("when you print it"), (10, 10, q.at("feature four")))]
    scenes.append((nb, Code("sentinel_demo.py — Visual Studio Code", outputs=outputs, tab0="sentinel_demo.py",
                            events=ev)))

    # S5 unpacking in comprehensions
    s5 = q.at("feature four")
    scenes.append((s5, Web(wn, tab="What's new in Python 3.15", keys=[
        (s5, "whatsnew__pep-798-unpacking-in-comprehensions", 60, 1.55, 940)])))
    ll = q.at("list of lists")
    up = demo("unpack.py")
    sb = q.at("star batch")
    ev = [("type", ll, (1, up[0])), ("type", sb, (3, up[2])), ("type", sb + 1.6, (4, up[3]))]
    di = q.at("same idea works")
    ev += [("type", di, (6, up[5])), ("type", di + 0.8, (7, up[6])), ("type", di + 1.6, (8, up[7])),
           ("type", di + 2.6, (9, up[8]))]
    r14 = q.at("run the same file")
    ev += [("cmd", r14, "unpack314"), ("out", r14, ("unpack314", max(1.4, q.at("and you get a syntax") - r14), None))]
    r15 = q.at("you get the flat")
    ev += [("cmd", r15 - 1.6, "unpack315"), ("out", r15 - 1.6, ("unpack315", 1.5, None))]
    scenes.append((ll, Code("unpack.py — Visual Studio Code", outputs=outputs, tab0="unpack.py", events=ev)))

    # S6 UTF-8 by default
    s6 = q.at("feature five")
    pep = "peps.python.org/pep-0686/"
    win_t = q.at("on windows")
    scenes.append((s6, Web(pep, tab="PEP 686 – Make UTF-8 mode default", keys=[
        (s6, "pep686__abstract", 0, 1.5, 1150),
        (win_t, "pep686__abstract", 200, 1.5, 1150),
        (q.at("the default is"), "pep686__abstract", 560, 1.5, 1150)], move=1.6)))
    op = q.at("here i open a file")
    nt = demo("notes.py")
    ev = [("show", op, nt), ("cmd", op + 0.6, "notes"), ("out", op + 0.6, ("notes", 1.4, None))]
    en = q.at("the encoding is")
    ev += [("cmd", en - 1.8, "encoding"), ("out", en - 1.8, ("encoding", 1.7, None))]
    scenes.append((op, Code("notes.py — Visual Studio Code", outputs=outputs, tab0="notes.py", events=ev)))

    # S7 Tachyon
    s7 = q.at("feature six")
    scenes.append((s7, Web(wn, tab="What's new in Python 3.15", keys=[
        (s7, "whatsnew__tachyon-high-frequency-statistical-sampling-profiler", 40, 1.45, 940),
        (s7 + 3.5, "whatsnew__tachyon-high-frequency-statistical-sampling-profiler", 330, 1.45, 940)], move=3.5)))
    sc = q.at("here is a script")
    sr = demo("slow_report.py")
    pr = q.at("instead of guessing", 0.6)
    keep_stats = re.compile(r"^(Captured|Sample rate|Profile Stats|\s+nsamples|\s+\d+/\d+\s)")
    stats_rows = [s for s in outputs["profile"]["out"].splitlines() if keep_stats.match(s)][:10]
    ev = [("show", sc, sr), ("hl", sc + 0.3, (8, 12, pr)),
          ("cmd", pr, "profile"),
          ("out", pr, ("profile", max(2.0, q.at("it samples your program") - pr),
                       lambda s, rows=set(stats_rows): s in rows))]
    rs = q.at("the report shows")
    ev.append(("hl", q.at("the culprit is"), (11, 11, q.at("add dash dash"))))
    scenes.append((sc, Code("slow_report.py — Visual Studio Code", outputs=outputs, tab0="slow_report.py",
                            editor_h=560, term_lines=12, events=ev, flash=[rs], font_size=21, lh=29, first=4)))
    fg = q.at("add dash dash")
    scenes.append((fg, Web("~/demo/flame.html", tab="Tachyon flame graph", keys=[
        (fg, "flame", 0, 1.0, 960),
        (fg + 1.0, "flame", 120, 1.35, 900)], move=2.5)))
    fx = q.at("one line fix")
    fast = demo("fast_report.py")
    sw = q.at("the script went from")
    ev = [("show", fx, sr), ("tab", fx + 0.5, "fast_report.py"), ("type", fx + 0.5, (11, fast[10])),
          ("hl", fx + 0.5, (11, 11, sw)),
          ("cmd", sw - 0.4, "time_slow"), ("out", sw - 0.4, ("time_slow", 1.2, None)),
          ("cmd", sw + 2.2, "time_fast"), ("out", sw + 2.2, ("time_fast", 1.0, None))]
    scenes.append((fx, Code("fast_report.py — Visual Studio Code", outputs=outputs, tab0="slow_report.py",
                            editor_h=560, term_lines=12, events=ev, font_size=21, lh=29, first=4)))

    # S8 outro
    s8 = q.at("so that is python")
    scenes.append((s8, Web(wn, tab="What's new in Python 3.15", keys=[
        (s8, "whatsnew__summary-release-highlights", 0, 1.45, 940),
        (q.at("the full list"), "whatsnew__summary-release-highlights", 640, 1.45, 940)], move=2.5)))
    OVERLAY["sections"] = [
        (0.15, "PYTHON 3.15", "6 features you'll actually use", False),
        (s1, "SETUP", "Install Python 3.15 with uv", False),
        (s2, "FEATURE 1", "Lazy imports", False),
        (s3, "FEATURE 2", "frozendict", True),
        (s4, "FEATURE 3", "sentinel()", True),
        (s5, "FEATURE 4", "Unpacking in comprehensions", False),
        (s6, "FEATURE 5", "UTF-8 by default", False),
        (s7, "FEATURE 6", "Tachyon sampling profiler", False),
        (s8, "RECAP", "Which one will you use first?", False),
    ]
    OVERLAY["callouts"] = [
        (cmd_t + 0.3, "uv python install 3.15", 4.0, True),
        (p_t + 1.0, "Problem: all 8 modules load on every run", 4.2, False),
        (lt, "Fix: put  lazy  before the import", 4.0, False),
        (bt, "Timed: median of 30 starts", 3.5, False),
        (hs + 0.3, "Hashable → works as a dict key", 4.0, False),
        (cp, "Copy with  |  →  a new frozendict", 3.2, False),
        (ch, "Immutable → TypeError on change", 4.0, False),
        (nb + 0.2, "MISSING = sentinel(\"MISSING\")", 4.0, True),
        (r14, "Python 3.14 → SyntaxError", 3.2, False),
        (r15 - 1.0, "Python 3.15 → one flat list", 3.4, False),
        (op, "open() with no encoding= → UTF-8", 4.2, False),
        (pr, "python -m profiling.sampling run script.py", 4.5, True),
        (fg + 0.2, "--flamegraph → interactive HTML", 3.5, True),
        (fx, "One-line fix", 2.6, False),
    ]
    return scenes


# ---------------------------------------------------------------- section titles and callouts (drawn over everything)
F_LABEL = ImageFont.truetype("/usr/share/fonts/opentype/fira/FiraSans-Bold.otf", 30)
F_TITLE = ImageFont.truetype("/usr/share/fonts/opentype/fira/FiraSans-Bold.otf", 64)
F_TITLE_MONO = ImageFont.truetype("/usr/share/fonts/opentype/fira/FiraMono-Bold.otf", 60)
F_CHIP = ImageFont.truetype("/usr/share/fonts/opentype/fira/FiraSans-Bold.otf", 26)
F_CALL = ImageFont.truetype("/usr/share/fonts/opentype/fira/FiraSans-Bold.otf", 30)
F_CALL_MONO = ImageFont.truetype("/usr/share/fonts/opentype/fira/FiraMono-Medium.otf", 28)
YELLOW, BLUE, INK = (255, 212, 59), (75, 139, 190), (14, 18, 32)
OVERLAY: dict = {"sections": [], "callouts": []}


def ease_out_back(u: float) -> float:
    u = min(1.0, max(0.0, u))
    c = 1.4
    return 1 + (c + 1) * (u - 1) ** 3 + c * (u - 1) ** 2


def _lerp(a: float, b: float, u: float) -> float:
    return a + (b - a) * u


def draw_overlay(im: Image.Image, t: float) -> Image.Image:
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    secs = OVERLAY["sections"]
    for i, (start, label, title, mono) in enumerate(secs):
        end = secs[i + 1][0] if i + 1 < len(secs) else 10**9
        if not (start <= t < end):
            continue
        tf = F_TITLE_MONO if mono else F_TITLE
        lt = t - start
        card_in, card_hold, morph = 0.55, 3.0, 0.45
        tw = max(d.textlength(title, font=tf), d.textlength(label, font=F_LABEL)) + 90
        chip = f"{label}  ·  {title}" if label else title
        cw = d.textlength(chip, font=F_CHIP) + 44
        # big card (lower-left) -> chip (top-right)
        big = (70, H - 330, 70 + tw, H - 330 + 168)
        small = (WIN[2] - 24 - cw, 124, WIN[2] - 24, 124 + 48)
        def card(dx: float, a: int, reveal: float, bar: float) -> None:
            x0, y0, x1, y1 = big[0] + dx, big[1], big[2] + dx, big[3]
            d.rounded_rectangle((x0, y0, x1, y1), 22, fill=INK + (int(a * 0.88),))
            d.rounded_rectangle((x0, y0, x0 + 14, y0 + (y1 - y0) * bar), 7, fill=YELLOW + (a,))
            d.text((x0 + 46, y0 + 22), label, font=F_LABEL, fill=YELLOW + (a,))
            tl = Image.new("RGBA", (int(tw), 90), (0, 0, 0, 0))
            ImageDraw.Draw(tl).text((0, 0), title, font=tf, fill=(255, 255, 255, a))
            mask = Image.new("L", tl.size, 0)
            ImageDraw.Draw(mask).rectangle((0, 0, int(tl.width * reveal), tl.height), fill=255)
            layer.alpha_composite(Image.composite(tl, Image.new("RGBA", tl.size), mask), (int(x0 + 46), int(y0 + 62)))

        def chip_at(dx: float, dy: float, a: int) -> None:
            x0, y0, x1, y1 = small[0] + dx, small[1] + dy, small[2] + dx, small[3] + dy
            d.rounded_rectangle((x0, y0, x1, y1), 24, fill=INK + (int(a * 0.86),))
            d.rounded_rectangle((x0, y0, x0 + 8, y1), 4, fill=YELLOW + (a,))
            if label:
                lw = d.textlength(label + "  ·  ", font=F_CHIP)
                d.text((x0 + 26, y0 + 9), label + "  ·  ", font=F_CHIP, fill=YELLOW + (a,))
                d.text((x0 + 26 + lw, y0 + 9), title, font=F_CHIP, fill=(255, 255, 255, a))
            else:
                d.text((x0 + 26, y0 + 9), title, font=F_CHIP, fill=(255, 255, 255, a))

        if lt < card_in + card_hold:
            u = ease_out_back(lt / card_in)
            card(_lerp(-tw - 120, 0, u), int(255 * min(1.0, lt / 0.25)),
                 min(1.0, max(0.0, (lt - 0.2) / 0.45)), min(1.0, lt / 0.4))
        elif lt < card_in + card_hold + morph:  # card slides out left while the chip slides in from the right
            u = ease((lt - card_in - card_hold) / morph)
            card(-(tw + 120) * u, int(255 * (1 - u)), 1.0, 1.0)
            chip_at((cw + 80) * (1 - u), 0, int(255 * u))
        else:
            out_u = ease((0.35 - (end - t)) / 0.35) if end - t < 0.35 else 0.0  # leave upward before the next section
            chip_at(0, -70 * out_u, int(255 * (1 - out_u)))
    for start, text, hold, mono in OVERLAY["callouts"]:
        lt = t - start
        if not (0 <= lt < hold):
            continue
        f = F_CALL_MONO if mono else F_CALL
        w = d.textlength(text, font=f) + 56
        u_in = ease_out_back(lt / 0.45)
        u_out = ease((lt - (hold - 0.35)) / 0.35) if lt > hold - 0.35 else 0.0
        x1 = WIN[2] - 24 + _lerp(w + 60, 0, u_in) + 60 * u_out
        a = int(255 * (1 - u_out))
        y0 = 190
        d.rounded_rectangle((x1 - w, y0, x1, y0 + 56), 28, fill=(255, 255, 255, int(a * 0.94)))
        d.rounded_rectangle((x1 - w, y0, x1 - w + 10, y0 + 56), 5, fill=BLUE + (a,))
        d.text((x1 - w + 30, y0 + 11), text, font=f, fill=INK + (a,))
    out = im.convert("RGBA")
    out.alpha_composite(layer)
    return out.convert("RGB")


def render(scenes, duration: float, out: str, t_from: float = 0.0, t_to: float | None = None) -> None:
    t_to = duration if t_to is None else t_to
    n0, n1 = int(t_from * FPS), int(math.ceil(t_to * FPS))
    p = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                          "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                          "-pix_fmt", "yuv420p", out], stdin=subprocess.PIPE)
    starts = [s for s, _ in scenes]
    fade = 0.25
    for i in range(n0, n1):
        t = i / FPS
        k = max(j for j, s in enumerate(starts) if s <= t)
        im = scenes[k][1].draw(t)
        if k > 0 and t - starts[k] < fade:
            prev = scenes[k - 1][1].draw(t)
            im = Image.blend(prev, im, ease((t - starts[k]) / fade))
        im = draw_overlay(im, t)
        p.stdin.write(im.tobytes())
    p.stdin.close()
    p.wait()
    print("wrote", out, n1 - n0, "frames")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("words")
    ap.add_argument("outputs")
    ap.add_argument("out")
    ap.add_argument("--duration", type=float, required=True)
    ap.add_argument("--from", dest="t_from", type=float, default=0.0)
    ap.add_argument("--to", dest="t_to", type=float, default=None)
    ap.add_argument("--still", type=float, nargs="*", help="write PNG stills at these times instead of a video")
    a = ap.parse_args()
    scenes = build(json.load(open(a.words)), json.load(open(a.outputs)))
    for s, obj in scenes:
        print(f"{s:7.2f}  {type(obj).__name__:4}  {getattr(obj, 'title', getattr(obj, 'url', ''))}")
    if a.still:
        starts = [s for s, _ in scenes]
        for t in a.still:
            k = max(j for j, s in enumerate(starts) if s <= t)
            draw_overlay(scenes[k][1].draw(t), t).save(f"{a.out}_{t:.1f}.png")
        return
    render(scenes, a.duration, a.out, a.t_from, a.t_to)


if __name__ == "__main__":
    main()
