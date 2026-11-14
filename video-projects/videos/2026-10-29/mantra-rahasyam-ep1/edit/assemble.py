"""Cut the episode: per-shot segments -> video track; voices + mantra + synth score + SFX -> mix; English .srt.

usage: assemble.py segments [shot_id ...] | audio | final | all
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/data2/git_repos/creative_repos/video-editor/scripts")
from drama_review import require  # noqa: E402

P = Path(__file__).resolve().parents[1]
require(P, "voices")  # user approved the dialogue and the voice emotion
E = P / "edit"
SPEC = json.loads((P / "shots.json").read_text())
FPS, W, H = 25, 1920, 1080
GAP = 0.5            # silence between consecutive lines: GAP/2 before each line, GAP/2 after
VO_IN = GAP / 2
SILENT = {"s01": 3.0, "s08": 2.0, "s10": 3.5}   # beats with no speech
SPILL = {"s06": 4.0}                             # s06's line continues over s06b


def _dur(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout)


def _durations() -> dict[str, float]:
    d, carry = {}, 0.0
    for s in SPEC["shots"]:
        sid = s["id"]
        if sid in SILENT:
            d[sid] = SILENT[sid]
        elif s["kind"] == "lipsync":  # drive audio already has a 0.3 s lead and a 0.2 s tail
            d[sid] = round(_dur(E / "drive" / f"{sid}.wav") + 0.2 * (sid == "s09"), 2)
        elif s.get("vo"):
            need = VO_IN + _dur(P / "audio/lines" / f"{sid}_vo_{s['vo']['who']}.wav") + GAP / 2
            if s["kind"] == "title":
                need += 1.0  # hold the end card
            d[sid] = SPILL.get(sid, round(need, 2))
            carry = max(0.0, need - d[sid])
        else:  # a visual that carries the previous shot's line
            d[sid] = round(max(carry, 3.0), 2)
            carry = 0.0
    return d


DUR = _durations()
SHOTS = [s for s in SPEC["shots"] if s["id"] in DUR]


def run(*cmd: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *cmd], check=True)


def probe(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout)


def starts() -> dict[str, float]:
    t, out = 0.0, {}
    for s in SHOTS:
        out[s["id"]] = round(t, 3)
        t += DUR[s["id"]]
    return out


def seg(s: dict) -> Path:
    sid, d = s["id"], DUR[s["id"]]
    out = E / "segs" / f"{sid}.mp4"
    out.parent.mkdir(exist_ok=True)
    n = int(round(d * FPS))
    enc = ["-r", str(FPS), "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-an", str(out)]
    if s["kind"] in ("still", "title"):
        src = E / "title.png" if s["kind"] == "title" else P / "frames" / f"{sid}.jpg"
        move = s.get("move", "slow push-in")
        zoom = f"1+0.10*on/{n}" if "push" in move else "1.10"
        # x/y must not use the runtime z variable (FFmpeg 6.1+, engine bug #227); recompute zoom from `on`.
        zx = f"(1+0.10*on/{n})" if "push" in move else "1.10"
        if "pan right" in move:
            x = f"(iw-iw/{zx})*on/{n}"
        elif "pan left" in move:
            x = f"(iw-iw/{zx})*(1-on/{n})"
        else:
            x = f"(iw-iw/{zx})/2"
        y = f"(ih-ih/{zx})/2"
        if s["kind"] == "title":
            vf = f"scale={W}:{H},fade=t=in:d=0.8"
            run("-loop", "1", "-t", str(d), "-i", str(src), "-vf", vf, "-frames:v", str(n), *enc)
        else:
            vf = (f"scale=3840:2160:force_original_aspect_ratio=increase,crop=3840:2160,"
                  f"zoompan=z='{zoom}':x='{x}':y='{y}':d={n}:s={W}x{H}:fps={FPS}")
            run("-i", str(src), "-vf", vf, "-frames:v", str(n), *enc)
    else:
        clip = P / "clips" / f"{sid}_i.mp4"
        if not clip.exists():
            clip = P / "clips" / f"{sid}.mp4"
        cd = probe(clip)
        if s["kind"] == "motion":
            slow = min(max(d / cd, 1.0), 1.5)
            vf = (f"setpts={slow:.3f}*PTS,fps={FPS},scale={W}:{H}:flags=lanczos,setsar=1,"
                  f"tpad=stop_mode=clone:stop_duration={d},trim=0:{d},setpts=PTS-STARTPTS")
        else:  # lipsync: real time, held on the last frame to fill the slot
            vf = (f"fps={FPS},scale={W}:{H}:flags=lanczos,setsar=1,"
                  f"tpad=stop_mode=clone:stop_duration={d},trim=0:{d},setpts=PTS-STARTPTS")
        run("-i", str(clip), "-vf", vf, "-frames:v", str(n), *enc)
    return out


def events() -> list[tuple[float, Path, str]]:
    """(start seconds, wav, kind) for every voice line and the mantra."""
    st, ev = starts(), []
    for s in SHOTS:
        sid = s["id"]
        if s.get("vo"):
            ev.append((st[sid] + VO_IN, P / "audio/lines" / f"{sid}_vo_{s['vo']['who']}.wav", "voice"))
        if s["kind"] == "lipsync":
            ev.append((st[sid], E / "drive" / f"{sid}.wav", "mantra" if sid == "s09" else "voice"))
    return ev


def audio() -> Path:
    st, total = starts(), sum(DUR.values())
    m0, m1 = st["s09"], st["s09"] + DUR["s09"]           # mantra window
    div1 = st["s15"]                                       # end of the divine section
    ins, voice_labels, mantra_label = [], [], None
    for i, (t, wav, kind) in enumerate(events()):
        ins += ["-i", str(wav)]
        lab = f"e{i}"
        if kind == "mantra":
            mantra_label = lab
        else:
            voice_labels.append(lab)
    k = len(ins) // 2
    parts = []
    for i, (t, wav, kind) in enumerate(events()):
        vol = "1.15" if kind == "mantra" else "1.0"
        parts.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol},adelay={int(t * 1000)}:all=1,"
                     f"apad=whole_dur={total}[e{i}]")
    vmix = "".join(f"[{x}]" for x in voice_labels)
    parts.append(f"{vmix}amix=inputs={len(voice_labels)}:normalize=0,asplit=2[voice][key]")
    # Score: dark drone until the mantra, silence under it, warm divine pad after, soft hopeful pad to the end.
    drone = ("0.22*sin(2*PI*55*t)*(0.6+0.4*sin(2*PI*0.07*t))+0.12*sin(2*PI*82.41*t)*(0.5+0.5*sin(2*PI*0.05*t+1))"
             "+0.07*sin(2*PI*110*t+0.5*sin(2*PI*0.3*t))")
    pad = ("0.10*sin(2*PI*146.83*t)+0.08*sin(2*PI*220*t)+0.07*sin(2*PI*293.66*t)+0.05*sin(2*PI*369.99*t)"
           "+0.04*sin(2*PI*440*t)*(0.5+0.5*sin(2*PI*0.25*t))")
    parts.append(f"aevalsrc='{drone}':s=48000:d={total},lowpass=f=400,aformat=channel_layouts=stereo,"
                 f"volume='if(lt(t,{m0 - 1.5}),1,if(lt(t,{m0}),({m0}-t)/1.5,0))':eval=frame[drone]")
    parts.append(f"aevalsrc='{pad}':s=48000:d={total},aecho=0.8:0.7:120|240:0.35|0.2,aformat=channel_layouts=stereo,"
                 f"volume='if(lt(t,{m1 - 0.5}),0,if(lt(t,{m1 + 2}),(t-{m1 - 0.5})/2.5,if(lt(t,{div1}),1,0.55)))':eval=frame,"
                 f"afade=t=out:st={total - 3}:d=3[pad]")
    parts.append("[drone][pad]amix=inputs=2:normalize=0[score]")
    # SFX: heartbeat + monitor beeps fading into a flatline (s01); beeps and heartbeat return (s13); boom at the third eye.
    s13, s12 = st["s13"], st["s12"]
    hb = "0.9*sin(2*PI*48*t)*exp(-22*mod(t,1.15))+0.55*sin(2*PI*42*t)*exp(-22*mod(t-0.28,1.15))*gte(mod(t,1.15),0.28)"
    parts.append(f"aevalsrc='{hb}':s=48000:d={total},aformat=channel_layouts=stereo,"
                 f"volume='if(lt(t,3.2),1-t/4,if(between(t,{s13},{s13 + 7}),0.8,0))':eval=frame[hb]")
    beep = "0.18*sin(2*PI*1000*t)*lt(mod(t,1.15),0.11)"
    flat = "0.13*sin(2*PI*1000*t)"
    parts.append(f"aevalsrc='{beep}':s=48000:d={total},aformat=channel_layouts=stereo,"
                 f"volume='if(lt(t,2.6),1,if(between(t,{s13 + 0.6},{s13 + 7}),1,0))':eval=frame[beep]")
    parts.append(f"aevalsrc='{flat}':s=48000:d={total},aformat=channel_layouts=stereo,"
                 f"volume='if(between(t,2.6,{DUR['s01'] + 1.5}),if(gt(t,{DUR['s01']}),({DUR['s01'] + 1.5}-t)/1.5,1),"
                 f"if(between(t,{s13},{s13 + 0.6}),1,0))':eval=frame[flat]")
    boom = f"if(gte(t,{s12 + 0.4}),0.9*sin(2*PI*38*(t-{s12 + 0.4}))*exp(-2.2*(t-{s12 + 0.4})),0)"
    parts.append(f"aevalsrc='{boom}':s=48000:d={total},aformat=channel_layouts=stereo[boom]")
    parts.append("[score][hb][beep][flat][boom]amix=inputs=5:normalize=0[music]")
    parts.append("[music][key]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350:makeup=1[ducked]")
    parts.append(f"[voice][{mantra_label}][ducked]amix=inputs=3:normalize=0,"
                 f"loudnorm=I=-14:TP=-1.5:LRA=11,atrim=0:{total}[out]")
    out = E / "mix.wav"
    run(*ins, "-filter_complex", ";".join(parts), "-map", "[out]", "-ar", "48000", str(out))
    assert k == len(events())
    return out


def srt() -> Path:
    st, rows = starts(), []
    for s in SHOTS:
        sid = s["id"]
        if s.get("vo"):
            t0, wav, text = st[sid] + VO_IN, P / "audio/lines" / f"{sid}_vo_{s['vo']['who']}.wav", s["vo"]["en"]
        elif s["kind"] == "lipsync":
            t0, wav, text = st[sid] + 0.3, E / "drive" / f"{sid}.wav", s["line"]["en"]
        else:
            continue
        d = probe(wav) - (0.3 if s["kind"] == "lipsync" else 0)
        chunks = [text]
        if len(text) > 80:  # split long lines at the sentence break nearest the middle
            cuts = [i + 1 for i, c in enumerate(text) if c in ".!?" and 0 < i < len(text) - 2]
            if cuts:
                c = min(cuts, key=lambda i: abs(i - len(text) / 2))
                chunks = [text[:c].strip(), text[c:].strip()]
        span = d / sum(len(c) for c in chunks)
        t = t0
        for c in chunks:
            rows.append((t, t + len(c) * span, c))
            t += len(c) * span

    def ts(x: float) -> str:
        ms = int(round(x * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    out = P / "mantra_rahasyam_ep1.en.srt"
    out.write_text("".join(f"{i}\n{ts(a)} --> {ts(b)}\n{c}\n\n" for i, (a, b, c) in enumerate(rows, 1)))
    return out


def final() -> Path:
    lst = E / "segs" / "list.txt"
    lst.write_text("".join(f"file '{E / 'segs' / (s['id'] + '.mp4')}'\n" for s in SHOTS))
    total = sum(DUR.values())
    out = P / "mantra_rahasyam_ep1.mp4"
    run("-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(E / "mix.wav"),
        "-vf", f"fade=t=in:d=1,fade=t=out:st={total - 1.5}:d=1.5", "-c:v", "libx264", "-crf", "18",
        "-preset", "slow", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-t", str(total),
        "-movflags", "+faststart", str(out))
    return out


if __name__ == "__main__":
    what, only = sys.argv[1], set(sys.argv[2:])
    print(f"episode length {sum(DUR.values()):.1f} s", flush=True)
    if what in ("segments", "all"):
        for s in SHOTS:
            if (only and s["id"] not in only) or (s["kind"] in ("motion", "lipsync")
                                                   and not (P / "clips" / f"{s['id']}.mp4").exists()):
                continue
            print("seg", seg(s).name, flush=True)
    if what in ("audio", "all"):
        print("audio", audio(), flush=True)
        print("srt", srt(), flush=True)
    if what in ("final", "all"):
        print("final", final(), flush=True)
