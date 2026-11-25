"""Draw the MCP video's screen track (EP3): real web pages, editor/terminal with real outputs, and animated dark-screen
explainer diagrams (diagram.py), timed to narration. Copied from EP2's renderer; build() is EP3's timeline.

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
    ("out", t, (key, delay, keep[, prompt])) output of outputs[key] appears `delay` s after t; keep = line filter;
                                  prompt=False: no new prompt after it (the next part of the same output follows)
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
    term_top: int = 0  # empty band above the terminal rows (keeps section chip + callouts off the text)

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
                key, delay, keep = pay[:3]
                if t < et + delay:
                    continue
                text = self.outputs[key]["out"].splitlines()
                if keep:
                    text = [s for s in text if keep(s)]
                rows_shown = int((t - et - delay) / 0.035) + 1  # fast scroll-in like a real terminal
                for s in text[:rows_shown]:
                    term_rows.append([(s, term_color(s))])
                if rows_shown >= len(text) and (len(pay) < 4 or pay[3]):
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
            y = ty + 40 + self.term_top + j * tl
            for s, col in row:
                x = draw_text(d, x, y, s, col, tfont)
            if j == len(rows) - 1:
                last_x = x
        blink = (t * 2) % 2 < 1.3
        if caret and caret[0] == "ed":
            cx, cy = caret[1]
            d.rectangle((cx + 2, cy + 3, cx + 5, cy + self.font_size + 6), fill=C["text"])
        elif blink:
            yb = ty + 40 + self.term_top + (len(rows) - 1) * tl
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
    title: str = "uv cheat sheet"

    def draw(self, t: float) -> Image.Image:
        im = Image.new("RGB", (W, H), (14, 18, 32))
        d = ImageDraw.Draw(im, "RGBA")
        d.text((140, 90), self.title, font=F_TITLE, fill=(255, 212, 59))
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
    return not line.startswith("warning:")


def split_claude(outputs: dict) -> None:
    """Split the real Claude Code transcript into its three tool calls and the answer, so each lands on its cue."""
    lines = outputs["claude"]["out"].splitlines()
    calls = [i for i, s in enumerate(lines) if s.startswith("●")]
    parts = [lines[calls[0]:calls[1]], lines[calls[1]:calls[2]], lines[calls[2]:calls[2] + 2], lines[calls[2] + 2:]]
    for k, part in enumerate(parts, 1):
        outputs[f"claude_{k}"] = {"cmd": outputs["claude"]["cmd"], "out": "\n".join(part)}


def split_claude_db(outputs: dict) -> None:
    """The use-case Claude run: each tool call (long SQL clipped to one terminal row), the answer, the delete reply."""
    lines = outputs["claude_db"]["out"].splitlines()
    calls = [i for i, s in enumerate(lines) if s.startswith("●")]
    clip = lambda s: s if len(s) <= 128 else s[:126] + " …"
    for k, i in enumerate(calls):
        outputs[f"db_c{k}"] = {"cmd": outputs["claude_db"]["cmd"], "out": "\n".join(clip(s) for s in lines[i:i + 2])}
    rest = lines[calls[-1] + 2:]
    find = lambda prefix: next(i for i, s in enumerate(rest) if s.startswith(prefix))
    outputs["db_ans"] = {"cmd": "", "out": "\n".join([""] + rest[find("Top product"):find("second with") + 1])}
    about = find("About deleting")
    outputs["db_del"] = {"cmd": "", "out": "\n".join([""] + rest[find("I've answered"):find("I've answered") + 2] + [""]
                                                    + rest[about:about + 3])}


# ---------------------------------------------------------------- the timeline
def build(words: list, outputs: dict) -> list[tuple[float, object]]:
    from diagram import Caption, Diagram, Edge, Node
    q = Cues(words)
    sc: list[tuple[float, object]] = []
    split_claude(outputs)
    server = out_lines(outputs, "server_after")

    def run(key, t_cmd, t_out, keep=None):
        return [("cmd", t_cmd, key), ("out", t_cmd, (key, max(0.8, t_out - t_cmd), keep))]

    def out_only(key, t):
        return [("out", t, (key, 0.0, None))]

    # S0 hook: the real Claude Code run
    c1, c2, c3, c4 = q.at("it searched"), q.at("found 650"), q.at("called a converter"), q.at("about 404")
    sc.append((0.0, Code("Terminal — claude", outputs=outputs, editor_h=0, term_lines=20, term_size=27,
                         events=[("cmd", 0.3, "claude")] + out_only("claude_1", c1) + out_only("claude_2", c2)
                         + out_only("claude_3", c3) + out_only("claude_4", c4))))

    # D1: how MCP connects (teaser from "the thing that connects", then again after the MCP page)
    t_conn, t_mcp = q.at("the thing that connects"), q.at("called mcp")
    t_build, t_test, t_connect, t_leak = q.at("we build"), q.at("test it"), q.at("connect it"), q.at("cannot leak")
    s1 = q.at("mcp the model")
    usb = q.at("think of it")
    once = q.at("you write a server")
    t_claude, t_vs, t_cur = q.at("and claude"), q.at("vs code"), q.at("cursor and")
    offers, decides = q.at("your server offers"), q.at("the ai decides")
    d1 = Diagram({
        "claude": Node(300, 300, "Claude", t_conn, icon="bot", color="purple", w=260, h=190, states=[(t_claude + 0.3, "flash")]),
        "vscode": Node(300, 540, "VS Code", t_vs, icon="code-xml", color="purple", w=260, h=190),
        "cursor": Node(300, 780, "Cursor", t_cur, icon="monitor", color="purple", w=260, h=190),
        "mcp": Node(800, 540, "MCP", t_mcp, icon="plug", sub="like USB-C for AI", color="yellow", w=300),
        "srv": Node(1250, 540, "Your server", t_mcp + 0.5, icon="server", sub="server.py", color="cyan", w=290,
                    states=[(once + 0.3, "flash")]),
        "t1": Node(1660, 360, "search_notes", offers + 0.4, icon="search", sub="notes/*.md", color="green", w=300, h=220),
        "t2": Node(1660, 720, "convert_units", offers + 0.8, icon="arrow-left-right", sub="km · kg · °C", color="green",
                   w=300, h=220),
    }, [Edge("claude", "mcp", t_mcp, "purple", packets=[(decides + 0.2, "", False)]),
        Edge("vscode", "mcp", t_vs + 0.3, "purple"), Edge("cursor", "mcp", t_cur + 0.3, "purple"),
        Edge("mcp", "srv", t_mcp + 0.4, "yellow", packets=[(decides + 0.9, "call a tool", False)]),
        Edge("srv", "t1", offers + 0.3, "green", packets=[(decides + 1.6, 'search_notes("Goa")', False)]),
        Edge("srv", "t2", offers + 0.7, "green")],
        [Caption(t_build, 960, 980, "build", 40, "cyan", until=s1, anchor="mm"),
         Caption(t_test, 1160, 980, "test", 40, "green", until=s1),
         Caption(t_connect, 1360, 980, "connect", 40, "yellow", until=s1),
         Caption(t_leak, 1600, 980, "no leaks", 40, "red", until=s1),
         Caption(once, 960, 980, "Write the server once. Every MCP app can use it.", 36, "white", until=decides),
         Caption(decides, 960, 980, "The AI decides when to call your tools.", 36, "white")],
        title=(t_conn, "MCP: the connection between AI apps and your code"))
    sc.append((t_conn, d1))
    sc.append((s1, Web("modelcontextprotocol.io/docs/getting-started/intro", tab="What is MCP?", keys=[
        (s1, "mcp__page-title", 0, 1.15, 900), (s1 + 3.0, "mcp__page-title", 380, 1.4, 900)], move=2.0)))
    sc.append((usb, d1))

    # S2 setup
    s2 = q.at("we will use fastmcp")
    sc.append((s2, Web("gofastmcp.com/getting-started/welcome", tab="FastMCP", keys=[
        (s2, "fastmcp__page-title", 0, 1.25, 900), (q.at("on version 4"), "fastmcp__page-title", 0, 2.0, 1080)])))
    fol = q.at("following my")
    add_t = q.at("single file script", 1.0)
    writes = q.at("that writes the")
    sc.append((fol, Code("server.py — Visual Studio Code", outputs=outputs, tab0="server.py", editor_h=600,
                         events=[("show", fol, server[6:]), *run("add_script", add_t, add_t + 1.6),
                                 ("show", writes - 0.6, server), ("hl", writes, (1, 6, writes + 4.5))],
                         font_size=22, lh=30)))

    # S3 tool one
    s3 = q.at("here is the whole")
    cr, each, srch, imp = q.at("we create a"), q.at("then each tool"), q.at("this one searches"), q.at("the important part")
    hints, doc, turns = q.at("type hints"), q.at("the docstring"), q.at("fastmcp turns")
    ev = [("show", s3, server), ("hl", cr, (11, 11, each)), ("hl", each, (15, 16, srch)), ("hl", srch, (17, 23, imp)),
          ("hl", hints, (16, 16, turns)), ("hl", doc, (17, 17, turns))]
    sc.append((s3, Code("server.py — Visual Studio Code", outputs=outputs, tab0="server.py", editor_h=940, term_lines=1,
                        events=ev, font_size=19, lh=24)))
    desc = q.at("the description")
    clear = q.at("so a clear")
    code = Node(480, 560, "your function", turns, color="cyan", w=800, h=250, code_size=24, lines=[
        "@mcp.tool", "def search_notes(query: str, limit: int = 5):", '    """Search my markdown notes for a', '    word or phrase. ..."""'],
        hl=[(turns + 0.6, desc, 1), (desc, clear + 3.5, 2)])
    js = Node(1440, 560, "what the AI sees", turns + 0.4, color="green", w=780, h=330, code_size=22, lines=[
        '"name": "search_notes",', '"description": "Search my markdown', '   notes for a word or phrase. ...",',
        '"inputSchema": {', '  "query": {"type": "string"},', '  "limit": {"type": "integer", "default": 5},',
        '  "required": ["query"] }'], hl=[(turns + 0.6, desc, 4), (turns + 0.6, desc, 5), (desc, clear + 3.5, 1)])
    sc.append((turns, Diagram({"code": code, "js": js}, [
        Edge(code.line_xy(1), js.line_xy(4, "l"), turns + 0.6, "yellow", until=desc),
        Edge(code.line_xy(2), js.line_xy(1, "l"), desc, "pink")],
        [Caption(turns + 0.8, 960, 860, "type hints → input schema", 36, "yellow", until=desc),
         Caption(desc + 0.2, 960, 860, "docstring → the description the AI reads", 36, "pink", until=clear),
         Caption(clear + 0.2, 960, 860, "A clear docstring = the AI knows when to use your tool", 36, "white")],
        title=(turns, "FastMCP writes the tool description for you"))))

    # S4 tool two
    s4 = q.at("the second tool")
    bot = q.at("at the bottom")
    sc.append((s4, Code("server.py — Visual Studio Code", outputs=outputs, tab0="server.py", editor_h=940, term_lines=1,
                        events=[("show", s4, server), ("hl", s4 + 0.3, (26, 36, bot)), ("hl", bot, (39, 40, bot + 4.5))],
                        font_size=19, lh=24)))

    # S5 test without AI
    s5 = q.at("before connecting")
    ev = run("list", q.at("list shows"), q.at("shows both tools"))
    ev += run("call_search", q.at("call runs"), q.at("and it finds"))
    ev += run("call_convert", q.at("convert 650"), q.at("and you get"))
    sc.append((s5, Code("Terminal — fastmcp", outputs=outputs, editor_h=0, term_lines=30, term_size=22, events=ev)))

    # S6 connect + the three real tool calls
    s6 = q.at("now let's connect")
    inst, ready = q.at("install has"), q.at("has ready commands")
    gave = q.at("i gave this")
    sc.append((s6, Code("Terminal — fastmcp", outputs=outputs, editor_h=0, term_lines=22, term_size=26,
                        events=run("mcpjson", inst, ready))))
    sc.append((gave, Code("Terminal — claude", outputs=outputs, editor_h=0, term_lines=20, term_size=27,
                          events=[("cmd", gave, "claude")])))
    watch = q.at("watch the tool")
    g1, g2, g3 = q.at("searched for goa"), q.at("searched again"), q.at("called convert")
    three, mine = q.at("three calls"), q.at("all running")
    d3 = Diagram({
        "q": Node(260, 540, "Your question", watch, icon="message-square", sub='"Goa trip in miles?"', color="white", w=340),
        "ai": Node(760, 540, "Claude", watch + 0.3, icon="bot", color="purple", states=[(three, "flash")]),
        "srv": Node(1260, 540, "my-notes", watch + 0.6, icon="server", sub="server.py · your machine", color="cyan",
                    w=360, states=[(mine, "flash")]),
        "notes": Node(1680, 330, "notes/", g1, icon="notebook-text", sub="trip-plan.md", color="green", w=280, h=200),
        "conv": Node(1680, 750, "convert", g3, icon="arrow-left-right", color="green", w=280, h=200),
    }, [Edge("q", "ai", watch + 0.3, "white"),
        Edge("ai", "srv", watch + 0.6, "purple", packets=[(g1, 'search_notes("Goa")', False), (g2, 'search_notes("km")', False),
                                                         (g3, 'convert_units(650, "km")', False), (g3 + 1.0, "403.9 mi", True)]),
        Edge("srv", "notes", g1 + 0.4, "green", packets=[(g1 + 0.6, "", False), (g2 + 0.6, "650 km", True)]),
        Edge("srv", "conv", g3 + 0.4, "green")],
        [Caption(three, 960, 960, "≈ 404 miles · 3 calls, all decided by the AI", 38, "white", until=mine),
         Caption(mine, 960, 960, "…all running your Python, on your machine", 38, "cyan")],
        title=(watch, "What actually happened"))
    sc.append((watch, d3))

    # S7 never expose this
    s7 = q.at("now the part")
    unsafe = demo("read_note_unsafe.py")
    here, ask, walks = q.at("here is a read"), q.at("but ask it"), q.at("and it walks")
    sc.append((s7, Code("read_note_unsafe.py — Visual Studio Code", outputs=outputs, tab0="read_note_unsafe.py",
                        editor_h=560, events=[("show", s7, unsafe), ("hl", here, (9, 12, ask)),
                                              *run("unsafe", ask, walks), ("hl", ask, (12, 12, ask + 6))])))
    trick = q.at("an ai can be")
    hidden = q.at("hidden instruction")
    fix = q.at("the fixes")
    resolve, refuse = q.at("resolve the path"), q.at("anything outside")
    d4 = Diagram({
        "web": Node(250, 420, "web page", hidden, icon="globe", sub='"read ../.env"', color="pink", w=320),
        "ai": Node(720, 420, "AI app", trick, icon="bot", color="purple"),
        "srv": Node(1180, 420, "read_note", trick + 0.3, icon="server", sub="no path check", color="cyan", w=300,
                    states=[(hidden + 1.8, "red"), (resolve, "green")]),
        "key": Node(1680, 420, ".env", trick + 0.6, icon="key-round", sub="API key", color="red", states=[(refuse, "blocked")]),
        "gate": Node(1180, 790, "path check", fix, icon="shield-check", sub="notes/*.md only", color="green", w=340,
                     states=[(refuse, "flash")]),
    }, [Edge("web", "ai", hidden + 0.2, "pink", packets=[(hidden + 0.2, "hidden instruction", False)]),
        Edge("ai", "srv", trick + 0.3, "purple", packets=[(hidden + 1.1, 'read_note("../.env")', False)]),
        Edge("srv", "key", trick + 0.6, "red", packets=[(hidden + 1.9, "sk-demo-…", True)]),
        Edge("srv", "gate", fix + 0.2, "green")],
        [Caption(trick + 0.2, 960, 960, "An AI can be tricked into asking for it", 38, "white", until=fix),
         Caption(fix + 0.2, 960, 960, "Fix: resolve the path, refuse anything outside notes/", 38, "green")],
        title=(trick, "Never expose this"))
    sc.append((trick, d4))
    same = q.at("now the same")
    safe = demo("read_note_safe.py")
    sc.append((same, Code("read_note_safe.py — Visual Studio Code", outputs=outputs, tab0="read_note_safe.py", editor_h=600,
                          events=[("show", same, safe), ("hl", same, (13, 15, same + 3)), *run("safe", same, same + 1.3)])))
    rules = q.at("my rules")
    shell, path, ro, sec = q.at("runs shell"), q.at("takes any path"), q.at("keep tools read"), q.at("never put secrets")
    d5 = Diagram({
        "sh": Node(360, 520, "no shell commands", shell, icon="terminal", color="red", w=360, h=240, states=[(shell + 0.5, "blocked")]),
        "pa": Node(760, 520, "no raw paths", path, icon="folder-open", color="red", w=340, h=240, states=[(path + 0.5, "blocked")]),
        "ro": Node(1160, 520, "read-only first", ro, icon="lock", color="green", w=340, h=240),
        "se": Node(1560, 520, "secrets out of reach", sec, icon="key-round", color="green", w=380, h=240),
    }, title=(rules, "My MCP security rules"))
    sc.append((rules, d5))

    # U0–U5 use case: talk to your shop database (a read-only SQLite server, demos/db_server.py)
    split_claude_db(outputs)
    u0 = q.at("now a real use case")
    owners, give = q.at("most owners"), q.at("so let's give")
    shop = q.at("this is a demo")
    n8, n12, n360 = q.at("8 products"), q.at("12 customers"), q.at("360 orders")
    sc.append((u0, Diagram({
        "you": Node(220, 330, "Shop owner", u0 + 0.3, icon="user", sub="can't write SQL", color="white", w=320),
        "ai": Node(660, 330, "AI", give, icon="bot", color="purple"),
        "db": Node(1110, 330, "shop.db", u0 + 0.6, icon="database", sub="SQLite", color="cyan", w=300),
        "p": Node(760, 690, "products", n8, icon="table-2", sub="8 rows", color="green", w=260, h=200),
        "c": Node(1110, 690, "customers", n12, icon="table-2", sub="12 rows", color="green", w=260, h=200),
        "o": Node(1460, 690, "orders", n360, icon="table-2", sub="360 rows", color="green", w=260, h=200),
    }, [Edge("you", "ai", give, "white", packets=[(give + 0.5, "plain English", False)]),
        Edge("ai", "db", give + 0.4, "purple", packets=[(give + 1.2, "safe access", False)]),
        Edge("db", "p", n8, "green"), Edge("db", "c", n12, "green"), Edge("db", "o", n360, "green")],
        [Caption(owners, 960, 990, "Every business has a database. Most owners can't write SQL.", 36, "white", until=give),
         Caption(n360 + 0.8, 960, 990, "fictional demo shop · orders from Sep to Nov 2026", 32, "dim")],
        title=(u0, "Use case: talk to your database"))))

    two = q.at("the server is just")
    lt_, rq_ = q.at("list tables returns"), q.at("run query takes")
    locks, l1, l2, slips = q.at("two locks"), q.at("first the tool"), q.at("second the database"), q.at("so even if")
    sc.append((two, Code("db_server.py — Visual Studio Code", outputs=outputs, tab0="db_server.py", editor_h=940,
                         term_lines=1, font_size=19, lh=23, events=[
                             ("show", two, demo("db_server.py")), ("hl", lt_, (15, 20, rq_)), ("hl", rq_, (23, 33, locks)),
                             ("hl", l1, (26, 27, slips)), ("hl", l2, (11, 12, slips))])))
    first_chk, refuses = q.at("first check"), q.at("sqlite refuses")
    sc.append((slips, Diagram({
        "q": Node(260, 480, "AI's query", slips, icon="message-square", sub="WITH … DELETE", color="pink", w=320,
                  states=[(refuses + 0.2, "blocked")]),
        "k1": Node(720, 480, "Lock 1", slips + 0.2, icon="shield-check", sub="your code: SELECT only", color="yellow", w=340),
        "k2": Node(1190, 480, "Lock 2", slips + 0.4, icon="lock", sub="SQLite: read-only file", color="green", w=340,
                   states=[(refuses, "flash")]),
        "db": Node(1650, 480, "shop.db", slips + 0.6, icon="database", sub="360 orders", color="cyan", w=280),
    }, [Edge("q", "k1", slips + 0.2, "pink", packets=[(slips + 0.7, "WITH … DELETE", False)]),
        Edge("k1", "k2", slips + 0.4, "yellow", packets=[(first_chk + 0.1, "slips past", False)]),
        Edge("k2", "db", slips + 0.6, "red")],
        [Caption(refuses + 0.2, 960, 900, "Lock 2 catches what lock 1 misses", 40, "white")],
        title=(slips, "Two locks, not one"))))

    flow = q.at("here is how a question")
    ask_, calls_ = q.at("you ask in"), q.at("calls list tables")
    writes_, runs_, turns_ = q.at("writes the sql"), q.at("runs it through"), q.at("turns the rows")
    sc.append((flow, Diagram({
        "q": Node(250, 400, "You ask", ask_, icon="message-square", sub='"Top product in October?"', color="white", w=360),
        "ai": Node(740, 400, "Claude", flow + 0.3, icon="bot", color="purple", states=[(writes_, "flash")]),
        "srv": Node(1200, 400, "shop-db", flow + 0.6, icon="server", sub="db_server.py", color="cyan", w=300),
        "db": Node(1660, 400, "shop.db", flow + 0.9, icon="database", sub="read-only", color="cyan", w=280),
        "sql": Node(800, 790, "SQL written by the AI", writes_, color="yellow", w=600, h=210, code_size=24, lines=[
            "SELECT p.name, SUM(o.qty*p.price_inr)", "FROM orders o JOIN products p …", "WHERE October AND not cancelled"]),
        "ans": Node(250, 790, "Answer", turns_ + 0.3, icon="sparkles", sub="in plain English", color="green", w=340, h=200),
    }, [Edge("q", "ai", ask_ + 0.2, "white", packets=[(ask_ + 0.4, "question", False)]),
        Edge("ai", "srv", flow + 0.6, "purple", packets=[(calls_, "list_tables()", False), (calls_ + 1.2, "schema", True),
                                                        (runs_, "run_query(sql)", False), (runs_ + 1.3, "rows", True)]),
        Edge("srv", "db", flow + 0.9, "cyan", packets=[(calls_ + 0.6, "", False), (runs_ + 0.6, "", False)]),
        Edge("ai", "sql", writes_, "yellow"), Edge("ai", "ans", turns_ + 0.2, "green")],
        [Caption(writes_ + 0.4, 1460, 790, "you never write the SQL", 36, "yellow")],
        title=(flow, "How a question flows"))))

    asked = q.at("i asked claude")
    read_s, five = q.at("it read the schema"), q.at("then wrote five")
    term = Code("Terminal — claude", outputs=outputs, editor_h=0, term_lines=27, term_size=21, term_top=150,
                events=[("cmd", asked, "db_c0"), ("out", read_s, ("db_c0", 0.0, None, False))]
                + [("out", five + 0.5 * k, (f"db_c{k}", 0.0, None, False)) for k in range(1, 6)])
    sc.append((asked, term))
    one = q.at("here is one of them")
    j1, f1, s1c, o1 = q.at("it joins orders"), q.at("filters to october"), q.at("cancelled orders"), q.at("sorts by money")
    notw = q.at("i did not write")
    ans_t = q.at("the answer")
    sql = Node(960, 500, "written by Claude", one, color="cyan", w=1500, h=440, code_size=28, lines=[
        "SELECT c.name, c.city, SUM(o.qty*p.price_inr) spend, COUNT(*) orders",
        "FROM orders o", "JOIN products p ON p.id=o.product_id", "JOIN customers c ON c.id=o.customer_id",
        "WHERE o.order_date>='2026-10-01' AND o.order_date<'2026-11-01'", "  AND o.status<>'cancelled'",
        "GROUP BY c.id", "ORDER BY spend DESC"],
        hl=[(j1, f1, 1), (j1, f1, 2), (j1, f1, 3), (f1, s1c, 4), (s1c, o1, 5), (o1, ans_t, 7)])
    sc.append((one, Diagram({"sql": sql}, [], [
        Caption(one + 0.3, 960, 830, "1 of the 5 queries Claude wrote, from the real run", 34, "dim", until=notw),
        Caption(notw, 960, 830, "I did not write a single line of this SQL", 40, "white")],
        title=(one, "Claude wrote the SQL"))))
    term.events.append(("out", ans_t + 0.2, ("db_ans", 0.0, None, False)))
    sc.append((ans_t, term))
    stopped = q.at("claude stopped")
    term.events.append(("out", stopped, ("db_del", 0.0, None)))

    test_ = q.at("so let's test the locks")
    plain, refused = q.at("a plain delete"), q.at("is refused")
    sneaky_cmd, blocks = refused + 1.0, q.at("sqlite blocks it")
    att = q.at("attempt to write")
    allx, still = q.at("all 360"), q.at("still there")
    sc.append((test_, Code("Terminal — fastmcp", outputs=outputs, editor_h=0, term_lines=20, term_size=22, term_top=150, events=[
        ("cmd", test_ + 0.3, "db_delete"), ("out", test_ + 0.3, ("db_delete", refused - test_ - 0.3, None)),
        ("cmd", sneaky_cmd, "db_with"), ("out", sneaky_cmd, ("db_with", blocks - sneaky_cmd, None)),
        ("cmd", att + 1.6, "db_count"), ("out", att + 1.6, ("db_count", max(2.2, still - att - 1.6), None))])))

    pat = q.at("that is the pattern")
    small_, schema_, safety_, prompt_ = q.at("small clear tools"), q.at("a schema the"), q.at("safety enforced"), q.at("not just in")
    sc.append((pat, Diagram({
        "t": Node(420, 500, "small, clear tools", small_, icon="wand-sparkles", sub="one job each", color="cyan", w=420, h=260),
        "s": Node(960, 500, "discoverable schema", schema_, icon="table-2", sub="list_tables first", color="yellow", w=420, h=260),
        "g": Node(1500, 500, "safety in code + system", safety_, icon="shield-check", sub="not in the prompt", color="green",
                  w=440, h=260, states=[(prompt_, "flash")]),
    }, [], [Caption(prompt_ + 0.2, 960, 860, "…not just in the prompt", 40, "pink")],
        title=(pat, "The pattern for any real MCP server"))))
    swap = q.at("swap sqlite")
    exp, tix, logs_ = q.at("expenses sheet"), q.at("support tickets"), q.at("server logs")
    expose = q.at("expose them")
    sc.append((swap, Diagram({
        "srv": Node(930, 500, "your MCP server", swap, icon="server", sub="read-only tools", color="cyan", w=360),
        "db": Node(420, 290, "company database", swap + 0.3, icon="database", sub="an analyst in seconds", color="purple", w=400),
        "ex": Node(1440, 290, "expenses sheet", exp, icon="receipt", color="green", w=360),
        "tk": Node(420, 710, "support tickets", tix, icon="ticket", color="yellow", w=360),
        "lg": Node(1380, 710, "server logs", logs_, icon="scroll-text", color="pink", w=360),
    }, [Edge("srv", "db", swap + 0.3, "purple"), Edge("srv", "ex", exp, "green"), Edge("srv", "tk", tix, "yellow"),
        Edge("srv", "lg", logs_, "pink")],
        [Caption(expose, 860, 960, "read-only · clear tools · let the AI do the digging", 38, "green")],
        title=(swap, "Same pattern, any data"))))

    # S8 recap
    rc = q.at("quick recap")
    sc.append((rc, Recap([
        (q.at("uv add dash"), "uv add --script", "setup in one file"),
        (q.at("add mcp"), "@mcp.tool", "typed function + docstring"),
        (q.at("fast mcp .list"), "fastmcp list · call", "test without AI"),
        (q.at("fast mcp .install"), "fastmcp install", "connect any app"),
        (q.at("lock down"), "resolve() + check", "lock down every path"),
    ], (q.at("what tool would"), "What tool would you give your AI first?"), title="MCP cheat sheet")))

    OVERLAY["sections"] = [
        (0.15, "MCP SERVER", "in Python, with FastMCP", False),
        (s1, "WHAT IS MCP", "USB-C for AI", False),
        (s2, "STEP 1", "Setup with uv", False),
        (s3, "STEP 2", "Tool 1: search_notes", True),
        (s4, "STEP 3", "Tool 2: convert_units", True),
        (s5, "STEP 4", "Test without AI", False),
        (s6, "STEP 5", "Connect it to an AI app", False),
        (s7, "STEP 6", "Never expose this", False),
        (u0, "USE CASE", "Talk to your database", False),
        (rc, "RECAP", "Your first MCP server", False),
    ]
    q2 = Cues(words)
    OVERLAY["callouts"] = [
        (q2.at("single file script", 1.0), "uv add --script server.py fastmcp", 3.4, True),
        (q2.at("then each tool"), "@mcp.tool → a tool the AI can call", 3.6, False),
        (q2.at("about 35"), "~35 lines · 2 tools", 2.6, False),
        (q2.at("list shows"), "fastmcp list server.py", 3.0, True),
        (q2.at("install has"), "fastmcp install claude-code | claude-desktop | mcp-json", 4.2, True),
        (q2.at("but ask it"), 'read_note("../.env.demo")', 3.4, True),
        (q2.at("first the tool"), "lock 1: SELECT only, in your code", 3.2, False),
        (q2.at("second the database"), "lock 2: mode=ro, in SQLite itself", 3.6, False),
        (q2.at("cold brew kit"), "Cold Brew Kit · ₹19,000 · 20 units", 3.6, False),
        (q2.at("arjun patel"), "Best customer: Arjun Patel (Pune) · ₹18,899", 3.8, False),
        (q2.at("masala chai"), "Masala Chai: most units (60), 5th on revenue", 3.6, False),
        (q2.at("is refused"), "lock 1 refused it", 2.6, False),
        (q2.at("sqlite blocks it"), "lock 2: SQLite blocked the write", 3.4, False),
        (q2.at("still there"), "360 orders · nothing deleted", 3.0, False),
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
