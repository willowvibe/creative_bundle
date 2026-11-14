"""Drama v2, graphics: HyperFrames overlays, a cold open and an end card around the assembled cut (2026-11-02).

usage: v2_graphics.py html | render [name ...] | compose | all

User's picks after the first v2 cut (subtitles skipped for this episode):
  * series title card over the opening house shot, place / hospital supers, character name tags on first appearance;
  * the Mahamrityunjaya mantra on screen while it is chanted (s51-s58), each word lit as it is sung;
  * a 4 s cold open (swerve, flatline, Shiva's light) before the title;
  * a 15 s end card: "To be continued", Episode 2 coming soon, Subscribe, the channel's icon and name (no link).
Each overlay is its own HyperFrames composition in v2/graphics/, rendered to a transparent MOV and laid over the
master at its time by FFmpeg. Times come from the animatic timeline (edit/v2_animatic.py), so a re-timed cut moves
the graphics with it. Output: mantra_rahasyam_ep1_v2_final.mp4 (+ .web.mp4 for the board).
"""

import json
import subprocess
import sys
from pathlib import Path

P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P / "edit"))
import v2_animatic as A  # noqa: E402

G = P / "v2" / "graphics"
R = G / "renders"
CUT = P / "v2" / "cut"
MASTER = P / "mantra_rahasyam_ep1_v2.mp4"
FINAL = P / "mantra_rahasyam_ep1_v2_final.mp4"
SHOT = {v["id"]: (a, b) for v, a, b in A.ROWS}
CHANT_AT = A.STARTS["chant"]
COLD = 4.0                                         # cold-open length, prepended
END = 15.0                                         # end-card length, appended
CHANNEL = ("Mantra Shakti", "The Power of Mantras")
CYCLE = 11.5 - 0.08                                # one chant cycle; the bed joins three with an 80 ms crossfade

GOLD, CREAM, MAROON = "#F2C566", "#FFF4DC", "rgba(58,12,12,0.78)"
FONTS = "font-family:'Noto Serif Telugu','Noto Serif',Georgia,serif;"

# word timings inside one cycle, from the loudness dips of mantra_cycle.wav (0.4 / 1.85 / 3.4 / 4.8 / 6.15 / 9.05 s)
LINE_A = [("ఓం", "Om", 0.0), ("త్ర్యంబకం", "Tryambakam", 0.45), ("యజామహే", "Yajāmahe", 1.85),
          ("సుగంధిం", "Sugandhim", 3.4), ("పుష్టివర్ధనం", "Puṣṭivardhanam", 4.8)]
LINE_B = [("ఉర్వారుకమివ", "Urvārukamiva", 6.15), ("బంధనాన్", "Bandhanān", 7.7),
          ("మృత్యోర్ముక్షీయ", "Mṛtyormukṣīya", 9.05), ("మామృతాత్", "Māmṛtāt", 10.4)]
MEANING = ("We worship the three-eyed Lord Shiva, who nourishes all beings. "
           "May He free us from death, as a ripe fruit falls from its vine.")

# (name, shot it sits on, Telugu line, English line); shown 0.5 s into the shot for 3 s
SUPERS = [
    ("place_home", "s02", "హైదరాబాద్ · ఉదయం", "Hyderabad · Morning"),
    ("name_lakshmi", "s03", "లక్ష్మి", "Lakshmi · Abhi's mother"),
    ("name_abhinav", "s05", "అభినవ్", "Abhinav · the son"),
    ("name_raghava", "s07", "రాఘవ", "Raghava · Abhi's father"),
    ("place_hospital", "s29", "సిటీ హాస్పిటల్", "City Hospital"),
    ("name_doctor", "s30", "డా. శ్రీనివాస్", "Dr. Srinivas"),
]


def page(cid: str, dur: float, body: str, css: str, js: str) -> str:
    """A standalone composition: sized root, one clip, one paused GSAP timeline (hyperframes-core contract)."""
    return f"""<!doctype html>
<html lang="te"><head><meta charset="UTF-8" /><meta name="viewport" content="width=1920, height=1080" />
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>
body{{margin:0;background:transparent;{FONTS}}}
#root{{position:relative;width:1920px;height:1080px;overflow:hidden;}}
.clip{{position:absolute;inset:0;}}
{css}
</style></head><body>
<div id="root" data-composition-id="{cid}" data-start="0" data-width="1920" data-height="1080" data-duration="{dur}">
<section id="{cid}-clip" class="clip" data-start="0" data-duration="{dur}" data-track-index="1">
{body}
</section></div>
<script>
window.__timelines = window.__timelines || {{}};
const tl = gsap.timeline({{ paused: true }});
{js}
window.__timelines["{cid}"] = tl;
</script></body></html>
"""


def title() -> str:
    # the opening shot is a sunlit cream house: the backing has to carry the gold on its own (checked on s01)
    css = f""".glow{{position:absolute;left:310px;top:250px;width:1300px;height:580px;
      background:radial-gradient(ellipse at center, rgba(40,8,8,0.82) 0%, rgba(40,8,8,0.55) 45%, rgba(40,8,8,0.0) 72%);}}
    .stack{{position:absolute;left:0;top:340px;width:1920px;height:400px;text-align:center;}}
    .te{{display:block;font-size:132px;font-weight:700;color:{GOLD};line-height:1.15;
      text-shadow:0 0 28px rgba(255,200,90,0.55),0 4px 12px rgba(0,0,0,0.6);}}
    .en{{display:block;margin-top:6px;font-size:34px;letter-spacing:14px;color:{CREAM};
      text-shadow:0 2px 8px rgba(0,0,0,0.7);}}
    .ep{{display:block;margin-top:22px;font-size:40px;color:{CREAM};text-shadow:0 2px 8px rgba(0,0,0,0.7);}}"""
    body = """<div id="title-glow" class="glow"></div><div id="title-stack" class="stack">
      <span id="title-te" class="te">మంత్ర రహస్యం</span><span id="title-en" class="en">MANTRA RAHASYAM</span>
      <span id="title-ep" class="ep">ఎపిసోడ్ 1 · మృత్యువుతో పోరాటం</span></div>"""
    js = """tl.from("#title-glow",{opacity:0,duration:0.8},0.2)
      .from("#title-te",{opacity:0,y:30,scale:0.96,duration:1.0,ease:"power3.out"},0.3)
      .from("#title-en",{opacity:0,letterSpacing:"30px",duration:0.9,ease:"power2.out"},0.8)
      .from("#title-ep",{opacity:0,y:16,duration:0.7,ease:"power2.out"},1.2)
      .to(["#title-stack","#title-glow"],{opacity:0,duration:0.6,ease:"power1.in"},3.2);"""
    return page("title", 3.84, body, css, js)


def super_(cid: str, te: str, en: str) -> str:
    css = f""".band{{position:absolute;left:0;top:820px;width:900px;height:170px;
      background:linear-gradient(90deg,{MAROON} 0%,rgba(58,12,12,0.55) 60%,rgba(58,12,12,0) 100%);}}
    .bar{{position:absolute;left:96px;top:852px;width:6px;height:106px;background:{GOLD};}}
    .te{{position:absolute;left:126px;top:842px;width:760px;font-size:54px;font-weight:700;color:{GOLD};
      line-height:1.25;text-shadow:0 2px 8px rgba(0,0,0,0.6);}}
    .en{{position:absolute;left:128px;top:922px;width:760px;font-size:28px;color:{CREAM};letter-spacing:2px;}}"""
    body = f"""<div id="{cid}-band" class="band"></div><div id="{cid}-bar" class="bar"></div>
      <div id="{cid}-te" class="te">{te}</div><div id="{cid}-en" class="en">{en}</div>"""
    js = f"""tl.from("#{cid}-band",{{opacity:0,x:-120,duration:0.5,ease:"power2.out"}},0)
      .from("#{cid}-bar",{{scaleY:0,transformOrigin:"top",duration:0.4,ease:"power2.out"}},0.15)
      .from("#{cid}-te",{{opacity:0,x:-24,duration:0.5,ease:"power2.out"}},0.25)
      .from("#{cid}-en",{{opacity:0,x:-16,duration:0.5,ease:"power2.out"}},0.4)
      .to(["#{cid}-band","#{cid}-bar","#{cid}-te","#{cid}-en"],{{opacity:0,duration:0.45}},2.55);"""
    return page(cid, 3.0, body, css, js)


def mantra() -> str:
    """Two lines per cycle, each word lit gold as it is sung; transliteration under it; the meaning on the last cycle."""
    dur = round(3 * CYCLE + 0.08, 2)

    def line(key: str, words: list) -> str:
        te = "".join(f'<span id="m-{key}{i}" class="w">{w}</span>' for i, (w, _, _) in enumerate(words))
        tr = " ".join(t for _, t, _ in words)
        return f'<div id="m-{key}" class="line"><div class="te">{te}</div><div class="tr">{tr}</div></div>'

    css = f""".panel{{position:absolute;left:0;top:760px;width:1920px;height:320px;
      background:linear-gradient(0deg,rgba(40,8,8,0.82) 0%,rgba(40,8,8,0.6) 55%,rgba(40,8,8,0) 100%);}}
    .line{{position:absolute;left:0;top:820px;width:1920px;height:150px;text-align:center;opacity:0;}}
    .te{{font-size:62px;font-weight:700;line-height:1.3;}}
    .w{{display:inline-block;margin:0 14px;color:rgba(255,244,220,0.45);}}
    .tr{{margin-top:4px;font-size:28px;letter-spacing:2px;color:rgba(255,244,220,0.85);font-style:italic;}}
    .mean{{position:absolute;left:260px;top:990px;width:1400px;height:70px;text-align:center;font-size:26px;
      color:{CREAM};opacity:0;}}"""
    body = (f'<div id="m-panel" class="panel"></div>{line("a", LINE_A)}{line("b", LINE_B)}'
            f'<div id="m-mean" class="mean">{MEANING}</div>')
    js = ['tl.from("#m-panel",{opacity:0,duration:0.8},0);']
    for c in range(3):
        o = c * CYCLE
        for key, words, show, hide in (("a", LINE_A, 0.0, 6.0), ("b", LINE_B, 6.05, 11.3)):
            ids = [f"#m-{key}{i}" for i in range(len(words))]
            js.append(f'tl.to({json.dumps(ids)},{{color:"rgba(255,244,220,0.45)",duration:0.01}},{o + show:.2f});')
            js.append(f'tl.to("#m-{key}",{{opacity:1,duration:0.35}},{o + show:.2f});')
            for i, (_, _, t) in enumerate(words):
                js.append(f'tl.to("#m-{key}{i}",{{color:"{GOLD}",duration:0.25}},{o + t:.2f});')
            js.append(f'tl.to("#m-{key}",{{opacity:0,duration:0.25}},{o + hide - (0.0 if c < 2 or key == "a" else 0):.2f});')
    js.append(f'tl.to("#m-mean",{{opacity:1,duration:0.6}},{2 * CYCLE:.2f});')
    js.append(f'tl.to(["#m-panel","#m-mean"],{{opacity:0,duration:0.6}},{dur - 0.7:.2f});')
    return page("mantra", dur, body, css, "\n".join(js))


def endcard() -> str:
    css = f""".bg{{position:absolute;inset:0;background:url('assets/endcard_bg.jpg') center/cover no-repeat;}}
    .veil{{position:absolute;inset:0;background:radial-gradient(ellipse at center,rgba(40,8,8,0.45) 0%,rgba(20,4,4,0.82) 100%);}}
    .tbc{{position:absolute;left:0;top:200px;width:1920px;text-align:center;font-size:96px;font-weight:700;color:{GOLD};
      text-shadow:0 0 26px rgba(255,200,90,0.5);}}
    .tbc-en{{position:absolute;left:0;top:330px;width:1920px;text-align:center;font-size:36px;letter-spacing:10px;color:{CREAM};}}
    .next{{position:absolute;left:0;top:420px;width:1920px;text-align:center;font-size:40px;color:{CREAM};}}
    .chan{{position:absolute;left:560px;top:600px;width:800px;height:180px;}}
    .avatar{{position:absolute;left:0;top:0;width:180px;height:180px;border-radius:90px;border:5px solid {GOLD};
      background:url('assets/channel_avatar.jpg') center/cover no-repeat;}}
    .cname{{position:absolute;left:215px;top:22px;width:600px;font-size:62px;font-weight:700;color:{CREAM};}}
    .ctag{{position:absolute;left:218px;top:108px;width:600px;font-size:30px;color:{GOLD};}}
    .sub{{position:absolute;left:810px;top:850px;width:300px;height:84px;border-radius:42px;background:#cc0000;
      color:#fff;font-family:Roboto,'Noto Sans',sans-serif;font-size:34px;font-weight:700;letter-spacing:2px;
      text-align:center;line-height:84px;box-shadow:0 6px 22px rgba(0,0,0,0.5);}}"""
    body = f"""<div id="e-bg" class="bg"></div><div id="e-veil" class="veil"></div>
      <div id="e-tbc" class="tbc">ఇంకా ఉంది…</div><div id="e-tbc-en" class="tbc-en">TO BE CONTINUED</div>
      <div id="e-next" class="next">ఎపిసోడ్ 2 త్వరలో · Episode 2 coming soon</div>
      <div id="e-chan" class="chan"><div id="e-avatar" class="avatar"></div>
        <div id="e-cname" class="cname">{CHANNEL[0]}</div><div id="e-ctag" class="ctag">{CHANNEL[1]}</div></div>
      <div id="e-sub" class="sub">SUBSCRIBE</div>"""
    js = """tl.from("#e-veil",{opacity:0,duration:1.2},0)
      .from("#e-tbc",{opacity:0,y:30,duration:1.0,ease:"power3.out"},0.6)
      .from("#e-tbc-en",{opacity:0,letterSpacing:"26px",duration:0.9},1.2)
      .from("#e-next",{opacity:0,y:16,duration:0.7},2.0)
      .from("#e-avatar",{opacity:0,scale:0.6,duration:0.7,ease:"back.out(1.6)"},3.0)
      .from(["#e-cname","#e-ctag"],{opacity:0,x:-30,duration:0.6,stagger:0.15},3.3)
      .from("#e-sub",{opacity:0,y:40,duration:0.6,ease:"back.out(1.8)"},4.2)
      .to("#e-sub",{scale:1.08,duration:0.35,yoyo:true,repeat:5,ease:"sine.inOut"},5.2)
      .to(["#e-tbc","#e-tbc-en","#e-next","#e-chan","#e-sub","#e-veil","#e-bg"],{opacity:0,duration:1.2},13.6);"""
    return page("endcard", END, body, css, js)


def html() -> list[str]:
    """Write every composition; returns their names."""
    G.mkdir(parents=True, exist_ok=True)
    files = {"title": title(), "mantra": mantra(), "endcard": endcard()}
    files |= {name: super_(name.replace("_", "-"), te, en) for name, _, te, en in SUPERS}
    for name, text in files.items():
        (G / f"{name}.html").write_text(text)
    # the end card sits on the last frame of the cut, softened
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-sseof", "-0.1", "-i", str(CUT / "segs" / "s81.mp4"),
                    "-frames:v", "1", "-vf", "gblur=sigma=6", str(G / "assets" / "endcard_bg.jpg")], check=True)
    return list(files)


def render(names: list[str]) -> None:
    R.mkdir(parents=True, exist_ok=True)
    for name in names:
        fmt = "mp4" if name == "endcard" else "mov"     # the end card is opaque; overlays need alpha
        subprocess.run(["npx", "-y", "hyperframes", "render", str(G), "-c", f"{name}.html", "--format", fmt,
                        "--fps", "30", "--quality", "high", "--quiet", "-o", str(R / f"{name}.{fmt}")], check=True)
        print("rendered", name, flush=True)


def overlays() -> list[tuple[str, float]]:
    """(render, start on the master timeline)."""
    out = [("title", 0.0), ("mantra", CHANT_AT)]
    out += [(name, SHOT[shot][0] + 0.5) for name, shot, _, _ in SUPERS]
    return out


def compose() -> Path:
    """Cold open + master with overlays + end card, one encode; audio: open hits, the master mix, a soft chant."""
    sfx = P / "audio" / "sfx"
    segs = CUT / "segs"
    ov = overlays()
    ins = ["-i", str(MASTER)]
    for name, _ in ov:
        ins += ["-i", str(R / f"{name}.mov")]
    k = len(ov) + 1
    ins += ["-i", str(segs / "s12.mp4"), "-i", str(segs / "s43.mp4"), "-i", str(segs / "s62.mp4"),
            "-i", str(R / "endcard.mp4"),
            "-i", str(sfx / "crash.wav"), "-i", str(sfx / "flatline.wav"), "-i", str(sfx / "shiva_hit.wav"),
            "-i", str(P.parents[1] / "mantra-rahasyam" / "assets" / "audio" / "mantra_cycle.wav")]
    f = []
    # master with every overlay laid at its time (overlays retimed from 30 to 25 fps on the master's clock)
    last = "0:v"
    for i, (name, t) in enumerate(ov, 1):
        f.append(f"[{i}:v]fps=25,format=yuva420p,setpts=PTS-STARTPTS+{t:.3f}/TB[o{i}]")
        f.append(f"[{last}][o{i}]overlay=eof_action=pass:format=auto[m{i}]")
        last = f"m{i}"
    # cold open: swerve, flatline, Shiva's light, each 1.2 s, then a white flash into the title
    f.append(f"[{k}:v]trim=1.0:2.2,setpts=PTS-STARTPTS[c1]")
    f.append(f"[{k + 1}:v]trim=0:1.2,setpts=PTS-STARTPTS[c2]")
    f.append(f"[{k + 2}:v]trim=0.6:1.8,setpts=PTS-STARTPTS[c3]")
    f.append(f"color=white:s=1920x1080:r=25:d=0.4,format=yuv420p[c4]")
    f.append("[c1][c2][c3][c4]concat=n=4:v=1:a=0,fps=25,format=yuv420p[cold]")
    f.append(f"[{k + 3}:v]fps=25,scale=1920:1080,format=yuv420p,setpts=PTS-STARTPTS[end]")
    f.append(f"[{last}]format=yuv420p,setpts=PTS-STARTPTS[main]")
    f.append("[cold][main][end]concat=n=3:v=1:a=0[v]")
    # sound: three hits under the cold open, the master's mix, then one soft chant cycle under the end card
    a = k + 4
    f.append(f"[{a}:a]atrim=0:1.2,volume=0.55,aresample=48000,aformat=channel_layouts=stereo[h1]")
    f.append(f"[{a + 1}:a]atrim=0:1.2,afade=t=out:st=0.9:d=0.3,volume=0.35,aresample=48000,"
             f"aformat=channel_layouts=stereo,adelay=1200:all=1[h2]")
    f.append(f"[{a + 2}:a]atrim=0:1.6,volume=0.6,aresample=48000,aformat=channel_layouts=stereo,adelay=2400:all=1[h3]")
    f.append(f"[h1][h2][h3]amix=inputs=3:normalize=0,apad=whole_dur={COLD}[ca]")
    f.append(f"[{a + 3}:a]aresample=48000,aformat=channel_layouts=stereo,volume=0.32,afade=t=in:d=1.5,"
             f"afade=t=out:st=10.0:d=1.5,apad=whole_dur={END}[ea]")
    f.append(f"[0:a]aresample=48000[ma]")
    f.append("[ca][ma][ea]concat=n=3:v=0:a=1[a]")
    total = COLD + A.TOTAL + END
    subprocess.run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", ";".join(f), "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-crf", "18", "-preset", "slow", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-b:a", "256k", "-t", f"{total:.3f}", "-movflags", "+faststart", str(FINAL)], check=True)
    web = FINAL.with_suffix(".web.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(FINAL), "-vf", "scale=1280:720:flags=lanczos",
                    "-c:v", "libx264", "-crf", "23", "-preset", "slow", "-pix_fmt", "yuv420p", "-c:a", "copy",
                    "-movflags", "+faststart", str(web)], check=True)
    return FINAL


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("html", "all"):
        names = html()
        print("compositions", names)
    if what == "render":
        render(sys.argv[2:] or [p.stem for p in sorted(G.glob("*.html"))])
    if what == "all":
        render(names)
    if what in ("compose", "all"):
        print("final", compose())
