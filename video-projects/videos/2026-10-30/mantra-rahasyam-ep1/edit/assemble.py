"""Cut the episode: per-visual segments -> video track; voices + chant + score -> mix; English .srt.

usage: assemble.py plan | segments [visual ...] | audio | srt | final | all

Timeline rules (all measured from the files, never assumed):
  * a visual with lines gets `sum(line) + GAP` seconds, so consecutive lines sit GAP apart (half before, half after)
  * a lip-sync visual runs at real time — slowing it would drift the mouth off the voice — and holds its last frame
    if the clip is shorter than the slot
  * a motion visual is slowed up to 1.5x to fill its slot, then held on the last frame for any remainder
  * a visual with no lines keeps its whole clip: the motion is the point, there is nothing to time it to
  * the chant is three cycles of the channel mantra and is the spine of the divine montage: v32 plays it on the
    lipsync clip and v33-v36 are stretched to land the third cycle exactly as the Yama doothas speak
  * v53 is the user's Google Flow clip: used whole, at real time, with its own voice
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/data2/git_repos/creative_repos/video-editor/scripts")
from drama_review import require  # noqa: E402

P = Path(__file__).resolve().parents[1]
require(P, "voices")  # script, dialogue and voice emotion are the user's approved; clips are checked on disk below
E = P / "edit"
SPEC = json.loads((P / "shots.json").read_text())
VISUALS = SPEC["visuals"]
LINES = SPEC["shots"]                       # 40 line entries: id, speaker, vo|line {who, te, en, emotion}
FPS, W, H = 25, 1920, 1080
GAP = 0.5            # silence between consecutive lines: GAP/2 before each line, GAP/2 after
LEAD = TAIL = GAP / 2
SERIES = P.parent.parent / "mantra-rahasyam"
CYCLE = SERIES / "assets" / "audio" / "mantra_cycle.wav"
CHANT = "c11a_line"
CHANT_VISUAL = "v32"
MONTAGE = ("v33", "v34", "v35", "v36")       # stretched so the third chant cycle ends on the last frame
PROVIDED = P / "clips" / "provided" / "c15a_shiva_flow.mp4"
SLOW_CAP = 1.5


# ---------------------------------------------------------------- media helpers

def probe(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout)


def run(*cmd: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *cmd], check=True)


def base(line_id: str) -> str:
    """c05b_line -> c05b: the line id without its vo/line suffix."""
    return line_id.rsplit("_", 1)[0]


def line_audio(line_id: str) -> Path:
    """audio/lines/<line id>_<speaker>.wav. The chant is the shared channel mantra, not a TTS line."""
    if line_id == CHANT:
        return CYCLE
    hits = sorted((P / "audio" / "lines").glob(f"{line_id}_*.wav"))
    if not hits:
        raise SystemExit(f"{line_id}: no audio/lines/{line_id}_*.wav")
    return hits[0]


def line_text(line_id: str) -> str:
    s = next(s for s in LINES if s["id"] == base(line_id))
    return s.get("vo", s.get("line"))["en"]


def clip_of(v: dict) -> Path:
    """Where the visual's footage lives: the review stages first, then the one provided clip."""
    for stage in ("lipsync", "motion"):
        c = P / "clips" / stage / f"{v['id']}.mp4"
        if c.exists():
            return c
    if v["id"] == "v53":
        return PROVIDED
    raise SystemExit(f"{v['id']}: no clip in clips/lipsync or clips/motion")


def is_provided(v: dict) -> bool:
    return v["id"] == "v53"


def chant_bed() -> tuple[Path, float]:
    """Three cycles joined with an 80 ms crossfade. A hard butt-join clicks if the file's loop point is imperfect."""
    out = E / "chant_bed.wav"
    if out.exists() and out.stat().st_mtime >= CYCLE.stat().st_mtime:
        return out, probe(out)
    run("-i", str(CYCLE), "-i", str(CYCLE), "-i", str(CYCLE), "-filter_complex",
        "[0][1]acrossfade=d=0.08:c1=tri:c2=tri[a];[a][2]acrossfade=d=0.08:c1=tri:c2=tri[o]",
        "-map", "[o]", "-ar", "48000", "-ac", "2", str(out))
    return out, probe(out)


def provided_voice() -> Path:
    """v53's own voice, lifted out of the Flow clip: the user wanted that performance, not a re-dub."""
    out = E / "v53_voice.wav"
    if out.exists() and out.stat().st_mtime >= PROVIDED.stat().st_mtime:
        return out
    run("-i", str(PROVIDED), "-vn", "-ac", "2", "-ar", "48000", str(out))
    return out


# ---------------------------------------------------------------- timeline

def _gap(v: dict) -> float:
    """Air to reserve for one line: a tail for a lip-sync shot (the voice starts on the cut), a lead and a tail
    for a motion shot (the picture gets there first)."""
    return TAIL if v["kind"] == "lipsync" else GAP


def _slots() -> dict[str, float]:
    """Slot lengths, quantised to whole frames: a segment is an integer number of frames, so a sub-frame slot would
    make the picture drift against the audio a little more at every cut."""
    bed_len = chant_bed()[1]
    d: dict[str, float] = {}
    for v in VISUALS:
        vid, lines = v["id"], v["lines"]
        if vid == CHANT_VISUAL:
            d[vid] = probe(clip_of(v))                    # the clip runs real time; the chant runs on past it
        elif vid in MONTAGE:
            d[vid] = (bed_len - d[CHANT_VISUAL]) / len(MONTAGE)
        elif lines:
            # A lip-sync clip was rendered *from* its line: the mouth starts on the wav's first frame, so the voice
            # must start on the cut too and the slot is the line plus a tail. A motion shot has no mouth to match,
            # so the picture leads and the voice comes in half a beat later.
            d[vid] = sum(probe(line_audio(x)) for x in lines) + (_gap(v) * len(lines))
        else:
            d[vid] = probe(clip_of(v))                    # an insert keeps its whole clip
    return {k: round(round(x * FPS) / FPS, 4) for k, x in d.items()}


DUR = _slots()
TOTAL = round(sum(DUR.values()), 3)


def starts() -> dict[str, float]:
    t, out = 0.0, {}
    for v in VISUALS:
        out[v["id"]] = round(t, 3)
        t += DUR[v["id"]]
    return out


ST = starts()


def line_starts() -> list[tuple[str, float, float]]:
    """(line id, start seconds, duration) for every spoken line, chant excluded — it is one long bed."""
    out = []
    for v in VISUALS:
        if is_provided(v):
            out.append((v["lines"][0], ST[v["id"]], probe(provided_voice())))
            continue
        t = ST[v["id"]] + (0.0 if v["kind"] == "lipsync" else LEAD)
        for x in v["lines"]:
            if x == CHANT:
                continue
            dur = probe(line_audio(x))
            out.append((x, round(t, 3), dur))
            t += dur + _gap(v)
    return out


# ---------------------------------------------------------------- video

def seg(v: dict) -> Path:
    vid, d = v["id"], DUR[v["id"]]
    out = E / "segs" / f"{vid}.mp4"
    out.parent.mkdir(exist_ok=True)
    n = int(round(d * FPS))
    enc = ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-an", str(out)]
    clip = clip_of(v)
    cd = probe(clip)
    # Real time where the mouth is on screen (lip-sync, and v53's own performance); slowed only for motion.
    if v["kind"] == "lipsync" or is_provided(v):
        vf = (f"fps={FPS},scale={W}:{H}:flags=lanczos,setsar=1,"
              f"tpad=stop_mode=clone:stop_duration={d},trim=0:{d},setpts=PTS-STARTPTS")
        slow = 1.0
    else:
        slow = min(max(d / cd, 1.0), SLOW_CAP)
        vf = (f"setpts={slow:.3f}*PTS,fps={FPS},scale={W}:{H}:flags=lanczos,setsar=1,"
              f"tpad=stop_mode=clone:stop_duration={d},trim=0:{d},setpts=PTS-STARTPTS")
    run("-i", str(clip), "-vf", vf, "-frames:v", str(n), *enc)
    held = max(0.0, d - cd * slow)
    print(f"  {vid:5} {v['kind']:8} {cd:5.2f}s clip -> {d:5.2f}s slot"
          f"{f', slowed {slow:.2f}x' if slow > 1.0 else ''}{f', held {held:.2f}s' if held > 0.05 else ''}", flush=True)
    return out


# ---------------------------------------------------------------- audio

def events() -> list[tuple[float, Path, str]]:
    """(start seconds, wav, kind). kind is 'voice', 'chant' or 'own' (v53's embedded performance)."""
    ev = []
    spoken = line_starts()                    # measured once: probing per visual would re-probe 40 wavs 55 times
    for v in VISUALS:
        if is_provided(v):
            ev.append((ST[v["id"]], provided_voice(), "own"))
            continue
        if CHANT in v["lines"]:
            ev.append((ST[v["id"]], chant_bed()[0], "chant"))
        for lid, t, _ in spoken:
            if lid in v["lines"]:
                ev.append((t, line_audio(lid), "voice"))
    return sorted(ev)


#: Score gain applied to the mastered bed, in dB, before it is ducked under the voice. A mastered track sits at
#: about -14 LUFS — the delivery loudness — so it has to come down before it is a bed at all. This is the one
#: number to turn when the music is too loud or too quiet; `audio --score-db -20` overrides it for an audition.
#: -17 was picked by ear off the reunion section on 2026-11-01: 3 dB under the first pass, which read as the bed
#: surging back at every 0.5 s gap between lines.
SCORE_DB = -17.0

#: Pre-encode trim for the 720p review copy, in dB. The bitrate is the real lever here, not the trim: this material
#: rang 1.7 dB above the mix peak at 128k and 1.4 dB at 192k — the 800 Hz monitor beep is a sustained tone and low
#: bitrates have the fewest bits for one — so the review copy ships 256k like the master, which rings 1.1 dB and
#: lands -1.1 dBTP off a 0.3 dB trim. Audio is ~2.5 MB per 64k on a 62 MB picture, so the fidelity is nearly free.
WEB_DB = -0.3


def audio() -> Path:
    ins, parts, voice_labels = [], [], []
    chant_label = None
    for i, (t, wav, kind) in enumerate(events()):
        ins += ["-i", str(wav)]
        vol = {"chant": "1.15", "own": "1.0", "voice": "1.0"}[kind]
        parts.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol},"
                     f"adelay={int(t * 1000)}:all=1,apad=whole_dur={TOTAL}[e{i}]")
        if kind == "chant":
            chant_label = f"e{i}"
        else:
            voice_labels.append(f"e{i}")

    vmix = "".join(f"[{x}]" for x in voice_labels)
    parts.append(f"{vmix}amix=inputs={len(voice_labels)}:normalize=0,asplit=2[voice][key]")

    # `edit/score.py` and `edit/sfx.py` own the two beds. Without them the mix still works — the synth bed below
    # is the fallback — so a re-cut never depends on a paid asset being present.
    nxt = len(ins) // 2                       # one input is two list items ("-i", path)
    score_bed, sfx_bed = E / "score_bed.wav", E / "sfx_bed.wav"
    if score_bed.exists():
        ins += ["-i", str(score_bed)]
        parts.append(f"[{nxt}:a]volume={10 ** (SCORE_DB / 20):.3f}[score]")
        nxt += 1
    else:
        c0, div = ST[CHANT_VISUAL], ST[MONTAGE[-1]] + DUR[MONTAGE[-1]]
        reunion = ST["v50"]
        drone = ("0.22*sin(2*PI*55*t)*(0.6+0.4*sin(2*PI*0.07*t))+0.12*sin(2*PI*82.41*t)*(0.5+0.5*sin(2*PI*0.05*t+1))"
                 "+0.07*sin(2*PI*110*t+0.5*sin(2*PI*0.3*t))")
        pad = ("0.10*sin(2*PI*146.83*t)+0.08*sin(2*PI*220*t)+0.07*sin(2*PI*293.66*t)+0.05*sin(2*PI*369.99*t)"
               "+0.04*sin(2*PI*440*t)*(0.5+0.5*sin(2*PI*0.25*t))")
        parts.append(f"aevalsrc='{drone}':s=48000:d={TOTAL},lowpass=f=400,aformat=channel_layouts=stereo,"
                     f"volume='if(lt(t,{c0 - 1.5}),1,if(lt(t,{c0}),({c0}-t)/1.5,0))':eval=frame[drone]")
        parts.append(f"aevalsrc='{pad}':s=48000:d={TOTAL},aecho=0.8:0.7:120|240:0.35|0.2,"
                     f"aformat=channel_layouts=stereo,"
                     f"volume='if(lt(t,{div - 0.5}),0,if(lt(t,{div + 2}),(t-{div - 0.5})/2.5,"
                     f"if(lt(t,{reunion}),1,1.25)))':eval=frame,"
                     f"afade=t=out:st={TOTAL - 3}:d=3[pad]")
        parts.append("[drone][pad]amix=inputs=2:normalize=0[score]")
    # The score ducks under the voice. The effects do not: their levels are drawn under it in `sfx.py`, and a
    # sidechain would pump the room tone at every line.
    parts.append("[score][key]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350:makeup=1[ducked]")
    final = f"[voice][{chant_label}][ducked]"
    if sfx_bed.exists():
        ins += ["-i", str(sfx_bed)]
        final += f"[{nxt}:a]"
        nxt += 1
    parts.append(f"{final}amix=inputs={final.count(']')}:normalize=0,"
                 f"loudnorm=I=-14:TP=-2.0:LRA=11,atrim=0:{TOTAL}[out]")
    out = E / "mix.wav"
    run(*ins, "-filter_complex", ";".join(parts), "-map", "[out]", "-ar", "48000", str(out))
    return out


def srt() -> Path:
    rows = []
    for lid, t0, d in line_starts():
        text = line_text(lid)
        chunks = [text]
        if len(text) > 80:                       # split long lines at the sentence break nearest the middle
            # A break only counts where the punctuation is followed by a space, so "home... was" cuts after the
            # ellipsis rather than leaving the next caption starting ".. was".
            cuts = [i + 1 for i, c in enumerate(text) if c in ".!?" and (i + 1 == len(text) or text[i + 1] == " ")]
            if cuts:
                c = min(cuts, key=lambda i: abs(i - len(text) / 2))
                chunks = [text[:c].strip(), text[c:].strip()]
        span = d / sum(len(c) for c in chunks)
        t = t0
        for c in chunks:
            rows.append((t, t + len(c) * span, c))
            t += len(c) * span
    # The chant caption is one line held over the first cycle, not the whole 34 s of mantras.
    rows.append((ST[CHANT_VISUAL], ST[CHANT_VISUAL] + 4.0, line_text(CHANT)))
    rows.sort()

    def ts(x: float) -> str:
        ms = int(round(x * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    out = P / "mantra_rahasyam_ep1.en.srt"
    out.write_text("".join(f"{i}\n{ts(a)} --> {ts(b)}\n{c}\n\n" for i, (a, b, c) in enumerate(rows, 1)))
    return out


# ---------------------------------------------------------------- final

def final() -> Path:
    lst = E / "segs" / "list.txt"
    lst.write_text("".join(f"file '{E / 'segs' / (v['id'] + '.mp4')}'\n" for v in VISUALS))
    out = P / "mantra_rahasyam_ep1.mp4"
    # `volume=-0.3dB` is measured codec headroom, not taste: mix.wav is mastered to -2.0 dBTP, and an AAC encoder
    # rings ~1.1 dB above its source peak on this material (the 800 Hz monitor beep is a resonant tone, and tones
    # ring harder than speech), so the delivered file lands at -1.1 dBTP. `loudnorm`'s -2.0 instead of -1.5 is the
    # other half of that: it buys the headroom the encoder needs *without* spending loudness, so the trim can stay
    # 0.3 dB instead of 1.0 dB. Both numbers are measured on the decoded MP4 with `ebur128=peak=true` — that is the
    # only place they are real, since the peak of a lossy file exists only after decoding.
    run("-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(E / "mix.wav"),
        "-vf", f"fade=t=in:d=1,fade=t=out:st={TOTAL - 1.5}:d=1.5", "-c:v", "libx264", "-crf", "18",
        "-preset", "slow", "-pix_fmt", "yuv420p", "-af", "volume=-0.3dB", "-c:a", "aac", "-b:a", "256k",
        "-t", str(TOTAL), "-movflags", "+faststart", str(out))
    return out


def web() -> Path:
    """The 720p review copy the board serves. Its picture comes from the master, its sound comes straight from
    `mix.wav` — never from the master's AAC, because re-encoding an already-lossy track rings twice. That mistake
    put the first review copy at -0.1 dBTP; encoding from the PCM with the bitrate and trim above lands it at
    -1.1 dBTP, the same as the master."""
    out = P / "mantra_rahasyam_ep1.web.mp4"
    run("-i", str(P / "mantra_rahasyam_ep1.mp4"), "-i", str(E / "mix.wav"), "-map", "0:v", "-map", "1:a",
        "-vf", "scale=1280:720:flags=lanczos", "-c:v", "libx264", "-crf", "23", "-preset", "slow",
        "-pix_fmt", "yuv420p", "-af", f"volume={WEB_DB}dB", "-c:a", "aac", "-b:a", "256k",
        "-t", str(TOTAL), "-movflags", "+faststart", str(out))
    return out


def plan() -> None:
    print(f"episode length {TOTAL:.1f} s ({int(TOTAL // 60)}:{TOTAL % 60:04.1f}), {len(VISUALS)} visuals", flush=True)
    print(f"score: drone 0-{ST[CHANT_VISUAL]:.1f}s, chant {ST[CHANT_VISUAL]:.1f}-{ST[MONTAGE[-1]] + DUR[MONTAGE[-1]]:.1f}s"
          f" (3 cycles), pad to {TOTAL:.1f}s, lift at reunion {ST['v50']:.1f}s", flush=True)
    print(f"\n{'visual':6} {'kind':8} {'start':>7} {'slot':>6}  lines", flush=True)
    for v in VISUALS:
        print(f"{v['id']:6} {v['kind']:8} {ST[v['id']]:7.2f} {DUR[v['id']]:6.2f}  {', '.join(v['lines'])}", flush=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--score-db" in args:
        i = args.index("--score-db")
        SCORE_DB = float(args[i + 1])
        del args[i:i + 2]
    what = args[0] if args else "plan"
    only = set(args[1:])
    if what in ("plan",):
        plan()
    if what in ("segments", "all"):
        for v in VISUALS:
            if only and v["id"] not in only:
                continue
            seg(v)
    if what in ("audio", "all"):
        print("audio", audio(), flush=True)
    if what in ("srt", "all"):
        print("srt", srt(), flush=True)
    if what in ("final", "all"):
        print("final", final(), flush=True)
    if what in ("web", "all"):
        print("web", web(), flush=True)
