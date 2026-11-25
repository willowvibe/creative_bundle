"""Draw the uv video's screen track (EP2): real web pages (scroll + zoom), an editor/terminal, the pip-vs-uv race,
a VS Code interpreter picker and a recap card, timed to narration. Copied from EP1's renderer and re-timed.

usage: uv run python render_scenes.py WORDS.json OUTPUTS.json OUT.mp4 [--from S] [--to S]

- Web scenes crop the full-page captures from capture_pages.py (docs.astral.sh/uv and peps.python.org) and ease between keyframes.
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
from pygments.lexers import PythonLexer, TOMLLexer
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


def highlight(line: str, lang: str = "python") -> list[tuple[str, str]]:
    out = []
    lexer = TOMLLexer() if lang == "toml" else PythonLexer()
    for tok, text in lexer.get_tokens(line):
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
    lang: str = "python"
    prompt: str = "~/demo $ "

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
        term: list[tuple[str, str]] = [(self.prompt, "ok")]
        term_rows: list[list[tuple[str, str]]] = [[(self.prompt, "ok")]]
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
                term_rows[-1] = [(self.prompt, "ok"), (cmd[:n], "text")]
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
                    term_rows.append([(self.prompt, "ok")])
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
            toks = highlight(text, self.lang)
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


def out_lines(outputs: dict, key: str) -> list[str]:
    return outputs[key]["out"].splitlines()


def race_secs(text: str) -> float:
    m = re.search(r"real\s+(\d+)m([\d.]+)s", text)
    return int(m.group(1)) * 60 + float(m.group(2)) if m else 0.0


F_PANE = ImageFont.truetype("/usr/share/fonts/opentype/fira/FiraSans-Bold.otf", 34)
F_BIG = ImageFont.truetype("/usr/share/fonts/opentype/fira/FiraMono-Bold.otf", 110)
F_RACE = ImageFont.truetype(MONO, 26)


@dataclass
class Race:
    """Two terminals side by side. runs: (pane, key, t_cmd, t_done, label). The timer counts up to the real `time`
    result between t_cmd and t_done; a pane whose screen time is shorter than the real run says it is sped up."""
    outputs: dict
    runs: list
    title: str = "Terminal — pip vs uv"

    def draw(self, t: float) -> Image.Image:
        im = Image.new("RGB", (W, H), C["bg"])
        d = ImageDraw.Draw(im, "RGBA")
        window(d, self.title)
        x0, y0, x1, y1 = WIN
        top = y0 + TITLE_H
        mid = (x0 + x1) // 2
        d.rectangle((x0, top, x1, y1 - 14), fill=C["term"])
        d.rounded_rectangle((x0, top, x1, y1), 14, fill=C["term"])
        d.line((mid, top, mid, y1), fill="#333640", width=3)
        for pane, name, col in ((0, "pip + venv", "#7c8193"), (1, "uv", "#de5fe9")):
            px = x0 + 40 + pane * (mid - x0)
            d.text((px, top + 24), name, font=F_PANE, fill=col)
        for pane in (0, 1):
            px = x0 + 40 + pane * (mid - x0)
            mine = [r for r in self.runs if r[0] == pane and t >= r[2]]
            if not mine:
                continue
            _, key, tc, td, label = mine[-1]
            cmd = self.outputs[key]["cmd"]
            real = race_secs(self.outputs[key]["out"])
            # wrapped command line(s)
            y = top + 210
            d.text((px, y), "$ ", font=F_RACE, fill=C["ok"])
            words, line, rows = cmd.split(" "), "", []
            for w_ in words:
                if F_RACE.getlength(line + " " + w_) > mid - x0 - 120 and line:
                    rows.append(line)
                    line = w_
                else:
                    line = (line + " " + w_).strip()
            rows.append(line)
            typed = min(1.0, (t - tc) / 1.0)
            n = int(sum(len(r) for r in rows) * typed)
            for r in rows:
                d.text((px + 28, y), r[:max(0, n)], font=F_RACE, fill=C["text"])
                n -= len(r)
                y += 32
            # timer
            done = t >= td
            shown = real if done else real * max(0.0, (t - tc - 1.0)) / max(0.1, td - tc - 1.0)
            txt = f"{shown:.2f} s" if real < 1 else f"{shown:.1f} s"
            col = (126, 231, 135) if done and pane == 1 else ((255, 212, 59) if done else (230, 230, 230))
            tw = d.textlength(txt, font=F_BIG)
            d.text((px + (mid - x0 - 80 - tw) / 2, top + 400), txt, font=F_BIG, fill=col)
            if label:
                lw = d.textlength(label, font=F_UI)
                d.text((px + (mid - x0 - 80 - lw) / 2, top + 540), label, font=F_UI, fill=C["dim"])
            if not done and td - tc - 1.0 < real * 0.8:
                d.text((px, top + 580), "(sped up)", font=F_UI, fill=C["dim"])
            for _p, k0, _tc0, td0, lab0 in [r for r in self.runs if r[0] == pane and r[3] <= tc]:  # earlier result stays
                d.text((px, top + 720), f"{lab0}: {race_secs(self.outputs[k0]['out']):.1f} s", font=F_PANE,
                       fill=(126, 231, 135))
            if done:  # the real `time` line, as printed
                d.text((px, top + 660), f"real {self.outputs[key]['out'].split('real')[-1].strip()}", font=F_RACE,
                       fill=C["dim"])
        return im


@dataclass
class Palette:
    """VS Code with the command palette picking the project's .venv interpreter (drawn; the choices are the real
    interpreters on this machine)."""
    t_open: float
    t_type: float
    t_pick: float
    choices: list

    def draw(self, t: float) -> Image.Image:
        im = Image.new("RGB", (W, H), C["bg"])
        d = ImageDraw.Draw(im, "RGBA")
        window(d, "weather — Visual Studio Code")
        x0, y0, x1, y1 = WIN
        top = y0 + TITLE_H
        d.rectangle((x0, top, x0 + 380, y1 - 40), fill="#181920")  # explorer
        d.text((x0 + 24, top + 16), "EXPLORER", font=F_UI, fill=C["dim"])
        tree = ["WEATHER", "  .venv", "  src / weather", "     __init__.py", "  .python-version", "  pyproject.toml",
                "  README.md", "  uv.lock"]
        for i, s in enumerate(tree):
            col = (255, 212, 59) if ".venv" in s else C["text"]
            d.text((x0 + 24, top + 60 + i * 36), s, font=F_URL, fill=col)
        for i, line in enumerate(demo("weather_init.py")):  # the app open in the editor
            y = top + 60 + i * 34
            d.text((x0 + 410, y), f"{i + 1:>2}", font=F_TERM, fill=C["ln"])
            draw_text(d, x0 + 460, y, line, "dim", F_TERM)
        d.rectangle((x0, y1 - 40, x1, y1 - 14), fill="#3a3d9e")  # status bar
        d.rounded_rectangle((x0, y1 - 40, x1, y1), 14, fill="#3a3d9e")
        status = "Python 3.15.2 ('.venv')" if t >= self.t_pick + 0.6 else "Select Interpreter"
        d.text((x1 - 420, y1 - 36), status, font=F_UI, fill="#ffffff")
        if self.t_open <= t < self.t_pick + 0.6:
            px0, px1 = (x0 + x1) // 2 - 420, (x0 + x1) // 2 + 420
            d.rounded_rectangle((px0, top + 10, px1, top + 70 + 52 * len(self.choices) + 20), 10, fill="#252632",
                                outline="#454860")
            q = "> Python: Select Interpreter"
            n = len(q) if t >= self.t_type + 1.2 else max(1, int(len(q) * max(0.0, t - self.t_type) / 1.2))
            d.rounded_rectangle((px0 + 14, top + 22, px1 - 14, top + 62), 6, fill="#1b1c25", outline="#3a7bd5")
            d.text((px0 + 26, top + 30), q[:n], font=F_URL, fill=C["text"])
            if t >= self.t_type + 1.2:
                for i, (label, path) in enumerate(self.choices):
                    yy = top + 80 + i * 52
                    if i == 0 and t >= self.t_pick - 0.6:
                        d.rectangle((px0 + 4, yy - 6, px1 - 4, yy + 42), fill="#04395e")
                    d.text((px0 + 26, yy), label, font=F_URL, fill=C["text"])
                    d.text((px0 + 26 + d.textlength(label, font=F_URL) + 16, yy + 2), path, font=F_UI, fill=C["dim"])
        return im


F_RECAP = ImageFont.truetype("/usr/share/fonts/opentype/fira/FiraMono-Bold.otf", 46)
F_RECAP_S = ImageFont.truetype("/usr/share/fonts/opentype/fira/FiraSans-Regular.otf", 38)


@dataclass
class Recap:
    """Cheat sheet: rows (time, command, what) slide in on their cue; `footer` (time, text) at the end."""
    rows: list
    footer: tuple

    def draw(self, t: float) -> Image.Image:
        im = Image.new("RGB", (W, H), (14, 18, 32))
        d = ImageDraw.Draw(im, "RGBA")
        d.text((140, 90), "uv cheat sheet", font=F_TITLE, fill=(255, 212, 59))
        for i, (rt, cmd, what) in enumerate(self.rows):
            if t < rt:
                continue
            u = ease((t - rt) / 0.4)
            y = 230 + i * 118
            x = 140 - 60 * (1 - u)
            a = int(255 * u)
            d.rounded_rectangle((x, y, x + 1260, y + 92), 18, fill=(30, 36, 56, a))
            d.text((x + 34, y + 20), cmd, font=F_RECAP, fill=(255, 255, 255, a))
            d.text((x + 34 + d.textlength(cmd, font=F_RECAP) + 36, y + 26), what, font=F_RECAP_S,
                   fill=(160, 170, 195, a))
        ft, text = self.footer
        if t >= ft:
            a = int(255 * ease((t - ft) / 0.5))
            d.text((140, 900), text, font=F_CALL, fill=(126, 231, 135, a))
        return im


def NO_WARN(line: str) -> bool:
    """Hide uv's "not on your PATH" hint: it only appears because the capture used scratch bin dirs."""
    return not line.startswith("warning:")


# ---------------------------------------------------------------- the timeline
def build(words: list, outputs: dict) -> list[tuple[float, object]]:
    q = Cues(words)
    sc: list[tuple[float, object]] = []
    T = "Terminal — uv"

    def term(start, events, title=T, **kw):
        kw.setdefault("editor_h", 0)
        kw.setdefault("term_lines", 22)
        kw.setdefault("term_size", 28)
        sc.append((start, Code(title, outputs=outputs, events=events, **kw)))

    def run(key, t_cmd, t_out, keep=None):
        return [("cmd", t_cmd, key), ("out", t_cmd, (key, max(0.9, t_out - t_cmd), keep))]

    # S0 the race
    pip_done = q.at("about 19")
    uv_t = q.at("exact same packages")
    uv_done = q.at("about 5")
    warm_t = q.at("once uv has")
    warm_done = q.at("takes 5 hundredths")
    sc.append((0.0, Race(outputs, [
        (0, "race_pip", 0.3, pip_done, "6 packages, empty cache"),
        (1, "race_uv", uv_t, uv_done, "same 6 packages, empty cache"),
        (1, "race_uv_warm", warm_t, warm_done, "again, with uv's cache"),
    ])))
    rep = q.at("uv replaces pip")
    home = "docs.astral.sh/uv/"
    sc.append((rep, Web(home, tab="uv", keys=[
        (rep, "home__highlights", 40, 1.2, 960),
        (q.at("the 2026 way"), "home__highlights", 380, 1.2, 960)], move=4.0)))

    # S1 install uv
    s1 = q.at("uv is a single program")
    inst = "docs.astral.sh/uv/getting-started/installation/"
    sc.append((s1, Web(inst, tab="Installation | uv", keys=[
        (s1, "install__installing-uv", 0, 1.25, 960),
        (q.at("paste this one line"), "install__installing-uv", 560, 1.9, 960),
        (q.at("on windows"), "install__windows", 420, 1.9, 960),
        (q.at("homebrew"), "install__installing-uv", 1560, 1.3, 960),
        (q.at("and even pip"), "install__installing-uv", 1560, 1.3, 960)], move=1.1)))
    chk = q.at("then check it")
    term(chk, run("version", q.at("with uv"), q.at("i am on")), term_size=36)

    # S2 Python versions
    s2 = q.at("first surprise")
    pi = q.at("uv python install downloads")
    ev = run("py_install", pi, q.at("here it fetched"), keep=NO_WARN)
    ev += run("py_list", q.at("uv python list"), q.at("shows every python"), keep=lambda s: "->" not in s)
    ev += run("run314", q.at("and uv run dash"), q.at("runs anything"))
    term(s2, ev, term_size=24, term_lines=26)

    # S3 uv init
    s3 = q.at("now a real project")
    it = q.at("uv init weather")
    ev = run("init", it, it + 1.0) + run("init_tree", q.at("older tutorials"), q.at("since uv"))
    term(s3, ev)
    pp = q.at("and pyproject")
    sc.append((pp, Code("pyproject.toml — weather", outputs=outputs, tab0="pyproject.toml", editor_h=940, events=[
        ("show", pp, out_lines(outputs, "init_pyproject")),
        ("hl", q.at("a command entry"), (9, 10, q.at("and a build system"))),
        ("hl", q.at("and a build system"), (12, 14, q.at("that avoids") + 2.5))], lang="toml", term_lines=1)))
    flat = q.at("if you want the old")
    initurl = "docs.astral.sh/uv/concepts/projects/init/"
    sc.append((flat, Web(initurl, tab="Creating projects | uv", keys=[
        (flat, "init__creating-a-project-without-a-build-system", 150, 1.45, 1000)])))

    # S4 uv add + lock
    s4 = q.at("and notice")
    ad = q.at("uv add requests rich")
    term(s4, run("add", ad, q.at("in about one second")), prompt="~/demo/weather $ ", term_size=24, term_lines=26)
    pj = q.at("pyproject now lists")
    lock = q.at("a new file appeared")
    pins = q.at("pins the exact")
    tr = q.at("uv tree shows")
    sc.append((pj, Code("pyproject.toml — weather", outputs=outputs, tab0="pyproject.toml", editor_h=560, lang="toml",
                        prompt="~/demo/weather $ ", events=[
                            ("show", pj, out_lines(outputs, "add_pyproject")),
                            ("hl", pj + 0.4, (7, 10, lock)),
                            ("tab", lock, "uv.lock"),
                            ("show", lock, out_lines(outputs, "lock_head")),
                            ("hl", pins, (6, 24, tr))] + run("tree", tr, tr + 0.8))))

    # S5 uv run / sync
    s5 = q.at("here is a tiny app")
    rn = q.at("just uv run")
    chk5 = q.at("uv checks the")
    sy = q.at("run uv sync")
    pk10 = q.at("10 packages installed")
    sc.append((s5, Code("__init__.py — weather", outputs=outputs, tab0="src/weather/__init__.py", editor_h=600, font_size=22, lh=31,
                        prompt="~/demo/weather $ ", events=[("show", s5, demo("weather_init.py")),
                                                            ("hl", s5 + 0.8, (5, 5, s5 + 4.0))]
                        + run("run", rn, chk5) + run("sync", sy, pk10))))

    # S6 single-file scripts
    s6 = q.at("this is my favorite")
    needs = q.at("this script needs")
    norm6 = q.at("normally")
    sa = q.at("uv add dash dash script")
    blk = q.at("in this comment block")
    ev = [("show", s6, demo("hello_rich.py")), ("hl", needs, (1, 1, norm6))]
    ev += run("add_script", sa, sa + 1.4)
    ev += [("show", blk - 0.8, out_lines(outputs, "script_after")), ("hl", blk, (1, 6, blk + 3.0))]
    sc.append((s6, Code("hello_rich.py — Visual Studio Code", outputs=outputs, tab0="hello_rich.py", editor_h=560,
                        events=ev)))
    pep = q.at("it is a python standard")
    sc.append((pep, Web("peps.python.org/pep-0723/", tab="PEP 723 – Inline script metadata", keys=[
        (pep, "pep723__example", 0, 1.45, 900)])))
    rs = q.at("now uv run")
    sc.append((rs, Code("hello_rich.py — Visual Studio Code", outputs=outputs, tab0="hello_rich.py", editor_h=560,
                        events=[("show", rs, out_lines(outputs, "script_after"))] + run("run_script", q.at("uv run hello"),
                                                                                         q.at("uv builds a")))))

    # S7 tools
    s7 = q.at("command line tools like")
    ti = q.at("uv tool install")
    once = q.at("installs it once")
    ux = q.at("uvx rough check")
    finds = q.at("and it finds")
    unused = q.at("the unused import")
    already = q.at("already have")
    sc.append((s7, Code("messy.py — Visual Studio Code", outputs=outputs, tab0="messy.py", editor_h=360, term_lines=19,
                        events=[("show", s7, demo("messy.py"))] + run("tool_install", ti, once, keep=NO_WARN)
                        + run("uvx_ruff", ux, finds) + [("hl", unused, (1, 1, already))])))

    # S8 migrate
    s8 = already
    ar = q.at("uv add dash r")
    ev = [("show", s8, demo("requirements.txt")), ("hl", s8 + 0.5, (1, 6, ar))]
    ev += run("add_r", ar, q.at("moves every package"), keep=lambda s: not s.startswith(" + "))
    ev += run("add_r_deps", q.at("into py project"), q.at("in one go"))
    sc.append((s8, Code("requirements.txt — legacy", outputs=outputs, tab0="requirements.txt", editor_h=300,
                        term_lines=20, prompt="~/demo/legacy $ ", events=ev)))
    pipt = q.at("uv pip install dash")
    sc.append((pipt, Race(outputs, [(1, "race_uv", pipt - 1.0, pipt - 0.9, "the command from the race")],
                          title="Terminal — the race, again")))

    # S9 VS Code + recap
    s9 = q.at("in vs code")
    pal = q.at("open the command")
    sc.append((s9, Palette(pal, q.at("choose python"), q.at("and pick dot"), [
        ("Python 3.15.2 ('.venv': venv)", "./.venv/bin/python"),
        ("Python 3.14.10", "~/.local/bin/python3.14"),
        ("Python 3.12.3", "/usr/bin/python3")])))
    rc = q.at("quick recap")
    sc.append((rc, Recap([
        (q.at("uv python install for"), "uv python install", "get any Python"),
        (q.at("uv init and"), "uv init  ·  uv add", "projects + uv.lock"),
        (q.at("uv run to run"), "uv run  ·  uv sync", "run, no activate"),
        (q.at("uv add dash dash script for"), "uv add --script", "single-file scripts"),
        (q.at("uvx for tools"), "uvx  ·  uv tool install", "tools"),
    ], (q.at("in the next video"), "Next: build an MCP server in Python, with uv"))))

    OVERLAY["sections"] = [
        (0.15, "THE RACE", "pip vs uv", False),
        (s1, "STEP 1", "Install uv", False),
        (s2, "STEP 2", "Install any Python", False),
        (s3, "STEP 3", "uv init", True),
        (s4, "STEP 4", "uv add + uv.lock", True),
        (s5, "STEP 5", "uv run · uv sync", True),
        (s6, "STEP 6", "Single-file scripts", False),
        (s7, "STEP 7", "Tools: uv tool · uvx", False),
        (s8, "STEP 8", "Move an old project", False),
        (s9, "STEP 9", "VS Code + recap", False),
    ]
    q2 = Cues(words)  # callouts look cues up again from the start, in spoken order
    OVERLAY["callouts"] = [
        (rep + 0.2, "pip + venv + pyenv + pipx → uv", 3.6, False),
        (q2.at("paste this one line"), "curl -LsSf https://astral.sh/uv/install.sh | sh", 2.4, True),
        (pi + 0.4, "No installer, no admin rights", 4.0, False),
        (q2.at("older tutorials"), "Old tutorials: main.py", 3.2, False),
        (q2.at("since uv"), "Since uv 0.12: src/ package + build system", 5.0, False),
        (flat + 1.5, "uv init --no-package → flat layout", 3.6, True),
        (lock + 0.3, "uv.lock: exact versions → commit it", 4.5, False),
        (rn + 0.3, "No activate needed", 3.2, False),
        (sy + 0.6, "uv sync = same env from the lock", 3.5, False),
        (blk + 0.2, "PEP 723 inline metadata", 3.5, False),
        (ti + 0.3, "Installed once, available everywhere", 3.6, False),
        (ux + 0.3, "uvx = run a tool without installing", 3.6, False),
        (ar + 0.3, "requirements.txt → pyproject.toml", 3.6, False),
    ]
    return sc


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
