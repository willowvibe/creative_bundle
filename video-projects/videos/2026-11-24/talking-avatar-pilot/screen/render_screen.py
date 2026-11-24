"""Draw the pilot's demo 'screen recording' (editor + terminal) frame by frame, timed to narration cue words.

usage: render_screen.py CUES.json DURATION_SECONDS OUT.mp4

Replaces recording demo.html in a browser (agent-browser's recorder hung twice on 2026-11-24). Every frame is a pure
function of time, so code lines appear exactly on their cue words (cues.json, from Whisper word times).
Layout keeps the bottom-right corner free of text for the avatar (about x 1560-1890, y 780-1080 at 1080p).
"""

import json
import math
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1920, 1080, 30
FONT = ImageFont.truetype("/usr/share/fonts/opentype/fira/FiraMono-Regular.otf", 27)
SMALL = ImageFont.truetype("/usr/share/fonts/opentype/fira/FiraMono-Regular.otf", 19)
LH = 42
C = {"bg": "#14151a", "win": "#1e1f26", "bar": "#2d2f3b", "term": "#0f1015", "text": "#e6e6e6", "dim": "#7c8193",
     "kw": "#c792ea", "str": "#c3e88d", "fn": "#82aaff", "num": "#f78c6c", "ok": "#7ee787", "ln": "#4b5063"}

# (cue, tokens) per editor line; None cue = blank line shown with the import line
CODE = [
    ("import", [("import", "kw"), (" httpx", "text")]),
    (None, []),
    ("get", [("r", "text"), (" = httpx.", "text"), ("get", "fn"), ("(", "text"),
             ('"https://uselessfacts.jsph.pl/api/v2/facts/random"', "str"), (",", "text")]),
    ("timeout", [("              timeout=", "text"), ("10", "num"), (")", "text")]),
    ("status", [("print", "fn"), ("(r.status_code)", "text")]),
    ("json", [("print", "fn"), ("(r.", "text"), ("json", "fn"), ("()[", "text"), ('"text"', "str"), ("])", "text")]),
]
PROMPT = [("~/demo $ ", "ok")]


def type_dur(tokens):
    n = sum(len(t) for t, _ in tokens)
    return min(3.2, 0.9 + n / 30)


def draw_tokens(d, x, y, tokens, visible=None, font=FONT):
    left = visible if visible is not None else 10**9
    for text, col in tokens:
        if left <= 0:
            break
        part = text[:left]
        d.text((x, y), part, font=font, fill=C[col])
        x += d.textlength(part, font=font)
        left -= len(text)
    return x


def frame(t, cues):
    im = Image.new("RGB", (W, H), C["bg"])
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = 28, 28, W - 28, H - 28
    d.rounded_rectangle((x0, y0, x1, y1), 14, fill=C["win"])
    d.rounded_rectangle((x0, y0, x1, y0 + 46), 14, fill=C["bar"])
    d.rectangle((x0, y0 + 30, x1, y0 + 46), fill=C["bar"])
    for i, col in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        d.ellipse((x0 + 20 + i * 24, y0 + 16, x0 + 34 + i * 24, y0 + 30), fill=col)
    d.rounded_rectangle((x0 + 110, y0 + 8, x0 + 230, y0 + 46), 8, fill=C["win"])
    d.text((x0 + 128, y0 + 14), "fact.py", font=SMALL, fill=C["text"])

    # editor
    ty = y1 - 320
    ex, ey = x0 + 40, y0 + 80
    caret = None
    for i, (cue, toks) in enumerate(CODE):
        start = cues["import"] if cue is None else cues[cue]
        d.text((ex, ey + i * LH), f"{i + 1:>2}", font=FONT, fill=C["ln"])
        if t < start or not toks:
            continue
        dur = type_dur(toks)
        n = sum(len(s) for s, _ in toks)
        vis = n if t >= start + dur else int((t - start) / dur * n)
        endx = draw_tokens(d, ex + 70, ey + i * LH, toks, vis)
        if vis < n:
            caret = (endx, ey + i * LH)
    if t >= cues["tip"]:  # highlight the timeout value
        tx = ex + 70 + d.textlength("              timeout=", font=FONT)
        a = min(1.0, (t - cues["tip"]) / 0.4)
        hl = Image.new("RGBA", (int(d.textlength("10", font=FONT)) + 12, LH - 4), (255, 209, 102, int(70 * a)))
        im.paste(hl, (int(tx) - 6, ey + 3 * LH - 2), hl)
        draw_tokens(d, tx, ey + 3 * LH, [("10", "num")])

    # terminal
    d.rectangle((x0, ty, x1, y1 - 14), fill=C["term"])
    d.rounded_rectangle((x0, ty, x1, y1), 14, fill=C["term"])
    d.line((x0, ty, x1, ty), fill="#333640", width=2)
    lines = [PROMPT]
    if t >= cues["install"]:
        lines = [PROMPT + [("pip install httpx", "text")]]
        if t >= cues["install"] + 1.6:
            lines += [[("Collecting httpx", "dim")], [("  Downloading httpx-0.28.1-py3-none-any.whl (73 kB)", "dim")],
                      [("Successfully installed httpx-0.28.1", "dim")], PROMPT]
    if t >= cues["run"]:
        lines = [PROMPT + [("python fact.py", "text")]]
        if t >= cues["run"] + 1.2:
            lines += [[("200", "text")], [('A group of flamingos is called a "flamboyance".', "text")], PROMPT]
    for j, toks in enumerate(lines):
        endx = draw_tokens(d, x0 + 30, ty + 22 + j * LH, toks)
        if j == len(lines) - 1 and toks is PROMPT and caret is None:
            caret = (endx, ty + 22 + j * LH)
    if caret and (t * 2) % 2 < 1.3:
        d.rectangle((caret[0] + 2, caret[1] + 4, caret[0] + 15, caret[1] + 34), fill=C["text"])
    return im


def main():
    cues = json.load(open(sys.argv[1]))
    dur = float(sys.argv[2])
    out = sys.argv[3]
    n = int(math.ceil(dur * FPS))
    p = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                          "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                          "-pix_fmt", "yuv420p", out], stdin=subprocess.PIPE)
    for i in range(n):
        p.stdin.write(frame(i / FPS, cues).tobytes())
    p.stdin.close()
    p.wait()
    print("wrote", out, n, "frames")


if __name__ == "__main__":
    main()
