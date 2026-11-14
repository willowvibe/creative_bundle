"""Drama v2, promotion: the YouTube thumbnail and three vertical Shorts cut from the finished episode (2026-11-02).

usage: v2_promo.py html | render | thumbnail | shorts | all

Thumbnail: Raghava's tearful prayer (s38) beside Shiva's glowing face (s63) with one bold Telugu hook, 1280x720 JPG.
Shorts (9:16, 1080x1920): each a span of the finished file (mantra_rahasyam_ep1_v2_final.mp4, so the mantra overlay
comes with it), the 16:9 picture centred over a blurred fill of itself, a HyperFrames overlay on top with a Telugu +
English title and the channel's icon with a "full episode" line, kept clear of the Shorts UI (bottom ~420 px, right
edge). Text is rendered by Chrome (HyperFrames) so the Telugu conjuncts shape correctly.
"""

import subprocess
import sys
from pathlib import Path

P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P / "edit"))
from v2_graphics import COLD, CREAM, FONTS, GOLD, G, R, FINAL, page  # noqa: E402
import v2_animatic as A  # noqa: E402

OUT = P / "v2" / "promo"
SHOT = {v["id"]: (a, b) for v, a, b in A.ROWS}
# (name, start, end on the main cut, Telugu title, English title)
SHORTS = [
    ("short_accident", SHOT["s10"][0], SHOT["s19"][1], "ఒక చిన్నారిని కాపాడబోయి…", "He swerved to save a little girl…"),
    ("short_shiva", SHOT["s57"][0], SHOT["s66"][1], "యమదూతల ముందు శివుడు!", "Lord Shiva stops Yama's messengers"),
    ("short_mantra", SHOT["s51"][0], SHOT["s58"][1], "మహా మృత్యుంజయ మంత్ర శక్తి", "The power of the Mahamrityunjaya mantra"),
]


def thumbnail_html() -> str:
    css = f"""#root{{width:1280px;height:720px;}}
    .left{{position:absolute;left:0;top:0;width:700px;height:720px;
      background:url('assets/thumb_left.jpg') 92% 30%/cover no-repeat;}}
    .right{{position:absolute;left:580px;top:0;width:700px;height:720px;
      background:url('assets/thumb_right.jpg') 50% 25%/cover no-repeat;}}
    .seam{{position:absolute;left:520px;top:0;width:240px;height:720px;
      background:linear-gradient(90deg,rgba(255,236,190,0) 0%,rgba(255,236,190,0.85) 50%,rgba(255,236,190,0) 100%);}}
    .shade{{position:absolute;left:0;top:420px;width:1280px;height:300px;
      background:linear-gradient(0deg,rgba(30,6,6,0.92) 0%,rgba(30,6,6,0.55) 60%,rgba(30,6,6,0) 100%);}}
    .hook{{position:absolute;left:0;top:520px;width:1280px;text-align:center;font-size:92px;font-weight:700;
      color:#FFD84A;line-height:1.2;-webkit-text-stroke:3px #2a0606;
      text-shadow:0 0 24px rgba(255,190,60,0.7),0 6px 0 #2a0606;}}
    .badge{{position:absolute;left:28px;top:26px;width:auto;height:64px;padding:0 24px;border-radius:12px;
      background:rgba(40,8,8,0.82);border:3px solid {GOLD};color:{CREAM};font-size:34px;font-weight:700;
      line-height:62px;}}"""
    body = """<div id="t-left" class="left"></div><div id="t-right" class="right"></div><div id="t-seam" class="seam"></div>
      <div id="t-shade" class="shade"></div><div id="t-hook" class="hook">మృత్యువును జయించిన మంత్రం</div>
      <div id="t-badge" class="badge">మంత్ర రహస్యం · EP 1</div>"""
    html = page("thumbnail", 1.0, body, css, 'tl.to("#t-hook",{opacity:1,duration:0.1},0);')
    return html.replace('data-width="1920" data-height="1080"', 'data-width="1280" data-height="720"') \
               .replace("width=1920, height=1080", "width=1280, height=720") \
               .replace("width:1920px;height:1080px", "width:1280px;height:720px")


def short_html(cid: str, dur: float, te: str, en: str) -> str:
    css = f"""#root{{width:1080px;height:1920px;}}
    .top{{position:absolute;left:50px;top:250px;width:980px;height:290px;text-align:center;padding-top:34px;
      box-sizing:border-box;border-radius:36px;background:rgba(40,8,8,0.74);}}
    .te{{font-size:76px;font-weight:700;color:{GOLD};line-height:1.25;
      text-shadow:0 0 20px rgba(255,200,90,0.45),0 4px 10px rgba(0,0,0,0.85);}}
    .en{{margin-top:14px;font-size:38px;color:{CREAM};text-shadow:0 3px 8px rgba(0,0,0,0.9);}}
    .cta{{position:absolute;left:90px;top:1330px;width:780px;height:140px;border-radius:70px;
      background:rgba(40,8,8,0.82);border:3px solid {GOLD};}}
    .av{{position:absolute;left:14px;top:14px;width:106px;height:106px;border-radius:53px;
      background:url('assets/channel_avatar.jpg') center/cover no-repeat;}}
    .l1{{position:absolute;left:140px;top:20px;width:620px;font-size:36px;font-weight:700;color:{CREAM};}}
    .l2{{position:absolute;left:140px;top:74px;width:620px;font-size:30px;color:{GOLD};}}"""
    body = f"""<div id="{cid}-top" class="top"><div id="{cid}-te" class="te">{te}</div><div id="{cid}-en" class="en">{en}</div></div>
      <div id="{cid}-cta" class="cta"><div id="{cid}-av" class="av"></div>
        <div id="{cid}-l1" class="l1">పూర్తి ఎపిసోడ్ · Full episode</div>
        <div id="{cid}-l2" class="l2">Mantra Shakti · @Mantrashakti-power</div></div>"""
    js = f"""tl.from("#{cid}-top",{{opacity:0,y:-30,duration:0.7,ease:"power3.out"}},0.2)
      .from("#{cid}-cta",{{opacity:0,y:40,duration:0.6,ease:"back.out(1.6)"}},1.0)
      .to("#{cid}-cta",{{scale:1.04,duration:0.4,yoyo:true,repeat:3,ease:"sine.inOut"}},{dur - 4.0:.2f});"""
    html = page(cid, dur, body, css, js)
    return html.replace('data-width="1920" data-height="1080"', 'data-width="1080" data-height="1920"') \
               .replace("width=1920, height=1080", "width=1080, height=1920") \
               .replace("width:1920px;height:1080px", "width:1080px;height:1920px")


def html() -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(P / "v2" / "frames_clean" / "s38.jpg"), "-vf",
                    "scale=1600:-2", str(G / "assets" / "thumb_left.jpg")], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(next((P / "v2" / "frames_clean").glob("s63.*"))),
                    "-vf", "scale=1600:-2", str(G / "assets" / "thumb_right.jpg")], check=True)
    (G / "thumbnail.html").write_text(thumbnail_html())
    for name, a, b, te, en in SHORTS:
        (G / f"{name}.html").write_text(short_html(name.replace("_", "-"), round(b - a, 2), te, en))


def render() -> None:
    for name in ["thumbnail"] + [s[0] for s in SHORTS]:
        fmt = "mp4" if name == "thumbnail" else "mov"
        subprocess.run(["npx", "-y", "hyperframes", "render", str(G), "-c", f"{name}.html", "--format", fmt,
                        "--fps", "30", "--quality", "high", "--quiet", "-o", str(R / f"{name}.{fmt}")], check=True)
        print("rendered", name, flush=True)


def thumbnail() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / "thumbnail.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "0.5", "-i", str(R / "thumbnail.mp4"), "-frames:v", "1",
                    "-q:v", "2", str(out)], check=True)
    return out


def shorts() -> list[Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    outs = []
    for name, a, b, _, _ in SHORTS:
        s, d = a + COLD, b - a
        out = OUT / f"{name}.mp4"
        fc = ("[0:v]split=2[bg][fg];"
              "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=28,eq=brightness=-0.12[bgb];"
              "[fg]scale=1080:-2[fgs];[bgb][fgs]overlay=0:(H-h)/2[base];"
              "[1:v]fps=25,format=yuva420p[ov];[base][ov]overlay=format=auto,format=yuv420p,"
              f"fade=t=in:d=0.4,fade=t=out:st={d - 0.6:.2f}:d=0.6[v];"
              f"[0:a]afade=t=in:d=0.3,afade=t=out:st={d - 0.8:.2f}:d=0.8[a]")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{s:.3f}", "-t", f"{d:.3f}", "-i", str(FINAL),
                        "-i", str(R / f"{name}.mov"), "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                        "-c:v", "libx264", "-crf", "19", "-preset", "slow", "-r", "25", "-c:a", "aac", "-b:a", "192k",
                        "-t", f"{d:.3f}", "-movflags", "+faststart", str(out)], check=True)
        outs.append(out)
        print("short", out.name, f"{d:.1f}s", flush=True)
    return outs


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("html", "all"):
        html()
    if what in ("render", "all"):
        render()
    if what in ("thumbnail", "all"):
        print("thumbnail", thumbnail())
    if what in ("shorts", "all"):
        shorts()
