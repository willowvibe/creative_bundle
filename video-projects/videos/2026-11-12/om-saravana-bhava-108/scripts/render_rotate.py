"""Render v5+: Muruga images rotate through the whole chant, Om-plate interludes between rounds.

Timeline (every cut lands on a ding; ding after chant k = 12.25 + 8.3(k-1)):
  chants 1-2                      Om plate (intro)
  4 rounds over chants 3-90       m1..m5 at 4 chants each, then the Om plate for 2 chants
  finale, chants 91-108           m1..m5 at 4,3,4,3,4 chants under the pushpanjali shower; the last runs to the end
Always on: glitter, a petal wave after every ding, a soft glow on each ding, the 75% Tamil + English
caption and the n / 108 counter.

Memory-safe: one ffmpeg graph with 30 looped image inputs buffered ~24 GB and was OOM-killed (2026-11-13),
so this renders in pieces. Each segment goes to its own clip, each 2 s transition is rendered from the two
clips around it, the pieces are joined with the concat demuxer, and a single light pass then adds the
overlays and the audio. Timing is in whole frames at 30 fps (boundary frame = round(seconds * 30)), so
nothing drifts.

usage (from the project dir):  python3 scripts/render_rotate.py OUT.mp4 [--dry]
"""

import subprocess
import sys
import tempfile
from pathlib import Path

P = Path(__file__).resolve().parent.parent
A, I = P / "art", P / "images"
OUT = Path(sys.argv[1]).resolve()
DRY = "--dry" in sys.argv
TOTAL, XF, FPS = 908.3, 2.0, 30
FONT = "/usr/share/fonts/truetype/noto/NotoSerif-Regular.ttf"


def ding(k):  # time of the ding after chant k (1-based)
    return round(12.25 + 8.3 * (k - 1), 3)


IMGS = ["m1_vel_peacock__agy", "m2_bala_murugan__agy", "m3_portrait__agy", "m4_dandayuthapani__agy",
        "m5_temple_arati__agy"]
# z0 z1 fx0 fy0 fx1 fy1 (focus as a fraction of the frame); odd rounds play the move in reverse
MOVE = {
    0: (1.00, 1.22, 0.51, 0.36, 0.51, 0.30),
    1: (1.25, 1.02, 0.60, 0.32, 0.55, 0.45),
    2: (1.00, 1.18, 0.50, 0.48, 0.50, 0.40),
    3: (1.30, 1.22, 0.50, 0.78, 0.50, 0.24),
    4: (1.32, 1.00, 0.50, 0.30, 0.50, 0.50),
}
IMG_XF = ["circleopen", "hblur", "fade", "circleopen", "fade"]  # transition into image i (within a round)

# ---- build segments: (kind, image index or None, chants) ----
plan = [("om", None, 2)]
for _ in range(4):
    plan += [("img", i, 4) for i in range(5)] + [("om", None, 2)]
plan += [("img", i, n) for i, n in zip(range(5), (4, 3, 4, 3, 4))]
assert sum(n for *_, n in plan) == 108, sum(n for *_, n in plan)

bounds, k = [0.0], 0
for *_, n in plan[:-1]:
    k += n
    bounds.append(ding(k))
FINALE = bounds[-5]  # start of the finale round (ding after chant 90)
segs = []
for j, (kind, idx, _n) in enumerate(plan):
    start = bounds[j]
    end = bounds[j + 1] + XF if j + 1 < len(plan) else TOTAL
    if j == 0:
        trans = None
    elif kind == "om":
        trans = "fade"
    elif plan[j - 1][0] == "om":
        trans = "fadewhite"  # a "divine light" reveal each time the images return
    else:
        trans = IMG_XF[idx]
    segs.append(dict(kind=kind, idx=idx, start=start, dur=round(end - start, 3), trans=trans))

# ---- pre-upscale once (2880 wide: smooth sub-pixel zoompan, lighter than 4K) ----
hi = A / "hi2880"
hi.mkdir(exist_ok=True)
for name in IMGS:
    dst = hi / f"{name}.png"
    if not dst.exists():
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(I / f"{name}.jpg"),
                        "-vf", "scale=2880:1620:flags=lanczos", str(dst)], check=True)


# ---- frame-exact layout ----
def F(sec):
    return int(round(sec * FPS))


XFF = int(XF * FPS)
TOTALF = F(TOTAL)
for j, s in enumerate(segs):
    s["f0"] = F(s["start"])
    s["f1"] = F(segs[j + 1]["start"]) if j + 1 < len(segs) else TOTALF  # where the next one takes over
    s["n"] = s["f1"] - s["f0"] + (XFF if j + 1 < len(segs) else 0)  # clip frames incl. the outgoing overlap

for s in segs:
    print(f"{s['start']:8.2f}  {s['n'] / FPS:6.2f}s  {s['kind']:3s} {'' if s['idx'] is None else 'm' + str(s['idx'] + 1)}  <- {s['trans']}")
print(f"{len(segs)} segments, finale from {FINALE}s, {TOTALF} frames")
if DRY:
    sys.exit(0)

W = OUT.parent / f"{OUT.stem}_parts"
W.mkdir(exist_ok=True)
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p", "-r", str(FPS)]


def ff(*a):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *a], check=True)


# ---- 1. one clip per segment ----
appear = {i: 0 for i in range(5)}
for j, s in enumerate(segs):
    out = W / f"clip{j:02d}.mp4"
    if s["kind"] == "om":
        vf = (f"[0:v]crop=1920:1080:x='192+150*sin(2*PI*t/97)':y='108+90*sin(2*PI*t/131)',format=rgba[b];"
              f"[b][1:v]overlay=0:0:format=auto,format=yuv420p,setsar=1,trim=end_frame={s['n']}[v]")
        ins = ["-loop", "1", "-framerate", str(FPS), "-i", str(A / "bg.png"),
               "-loop", "1", "-framerate", str(FPS), "-i", str(A / "om.png")]
    else:
        z0, z1, fx0, fy0, fx1, fy1 = MOVE[s["idx"]]
        if appear[s["idx"]] % 2 == 1:  # reverse the move on alternate appearances
            z0, z1, fx0, fy0, fx1, fy1 = z1, z0, fx1, fy1, fx0, fy0
        appear[s["idx"]] += 1
        N = s["n"]
        E = f"(3*pow(on/{N},2)-2*pow(on/{N},3))"
        Z, FX, FY = (f"({a}+({b}-{a})*{E})" for a, b in ((z0, z1), (fx0, fx1), (fy0, fy1)))
        vf = (f"[0:v]zoompan=z='{Z}':x='max(0,min(iw-iw/zoom,iw*{FX}-iw/zoom/2))':"
              f"y='max(0,min(ih-ih/zoom,ih*{FY}-ih/zoom/2))':d=1:s=1920x1080:fps={FPS},format=rgba[z];"
              f"[z][1:v]overlay=0:0:format=auto,format=yuv420p,setsar=1,trim=end_frame={N}[v]")
        ins = ["-loop", "1", "-framerate", str(FPS), "-i", str(hi / f"{IMGS[s['idx']]}.png"),
               "-loop", "1", "-framerate", str(FPS), "-i", str(A / "shade.png")]
    if not out.exists():
        ff(*ins, "-filter_complex", vf, "-map", "[v]", "-frames:v", str(s["n"]), *ENC, str(out))
    print("clip", j, flush=True)

# ---- 2. bodies (each clip minus its overlaps) and 3. transitions ----
pieces = []
for j, s in enumerate(segs):
    c = W / f"clip{j:02d}.mp4"
    if j > 0:
        t = W / f"trans{j:02d}.mp4"
        p = segs[j - 1]
        if not t.exists():
            ff("-i", str(W / f"clip{j - 1:02d}.mp4"), "-i", str(c), "-filter_complex",
               f"[0:v]trim=start_frame={p['n'] - XFF},setpts=PTS-STARTPTS[a];"
               f"[1:v]trim=end_frame={XFF},setpts=PTS-STARTPTS[b];"
               f"[a][b]xfade=transition={s['trans']}:duration={XF}:offset=0[v]",
               "-map", "[v]", "-frames:v", str(XFF), *ENC, str(t))
        pieces.append(t)
    b0 = XFF if j > 0 else 0
    b1 = s["n"] - (XFF if j + 1 < len(segs) else 0)
    body = W / f"body{j:02d}.mp4"
    if not body.exists():
        ff("-i", str(c), "-vf", f"trim=start_frame={b0}:end_frame={b1},setpts=PTS-STARTPTS",
           "-frames:v", str(b1 - b0), *ENC, str(body))
    pieces.append(body)
    print("piece", j, flush=True)

lst = W / "list.txt"
lst.write_text("".join(f"file '{x}'\n" for x in pieces))

# ---- 4. one light pass: glow, glitter, petals, caption, counter, fades, audio ----
g = (
    f"[0:v]eq=brightness='0.045*exp(-mod(t-12.25,8.3)/0.9)*gte(t,12.25)':eval=frame,format=rgba[xg];"
    f"[xg][1:v]overlay=0:0:format=auto[xgl];"
    f"[xgl][2:v]overlay=0:0:format=auto:enable='between(t,12,{FINALE})'[xpd];"
    f"[xpd][3:v]overlay=0:0:format=auto:enable='gte(t,{FINALE - 1})'[xps];"
    f"[xps][4:v]overlay=0:0:format=auto[xc];"
    f"[xc]drawtext=fontfile={FONT}:fontsize=38:fontcolor=0xFFE0A0@0.80:shadowcolor=black@0.6:shadowx=2:"
    f"shadowy=2:x=(w-text_w)/2:y=h-78:enable='gte(t,4.9)':"
    r"text='%{eif\:clip(floor((t-5)/8.3)+1\,1\,108)\:d} / 108',"
    f"fade=t=in:st=0:d=3,fade=t=out:st={TOTAL - 6}:d=6,format=yuv420p[v]"
)
gf = OUT.with_suffix(".ffgraph")
gf.write_text(g)
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "warning", "-stats", "-y",
                "-f", "concat", "-safe", "0", "-i", str(lst),
                "-stream_loop", "-1", "-i", str(A / "glitter.mov"),
                "-stream_loop", "-1", "-i", str(A / "petals_ding.mov"),
                "-stream_loop", "-1", "-i", str(A / "petals_shower.mov"),
                "-loop", "1", "-framerate", str(FPS), "-i", str(A / "caption.png"),
                "-i", str(P / "audio/final/om_saravana_bhava_108_final.wav"),
                "-filter_complex_script", str(gf), "-map", "[v]", "-map", "5:a", "-frames:v", str(TOTALF),
                "-t", str(TOTAL), "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", str(FPS),
                "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", str(OUT)], check=True)
print("done", OUT)
