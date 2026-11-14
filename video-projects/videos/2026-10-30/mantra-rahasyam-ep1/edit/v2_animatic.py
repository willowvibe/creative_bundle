"""Drama v2, animatic: the approved stills cut to the reused audio, before any GPU spend (2026-11-01).

usage: v2_animatic.py plan | segments [shot ...] | audio | final | all

Every still gets a camera move in place of its future clip, labelled with its shot id and the route that will
replace it, so the user can check pacing, order and where each line lands. The timeline it prints is the edit list
the GPU clips are cut to later.

Timeline rules (durations measured from the wavs, never taken from visuals.json's rounded numbers):
  * every line sits on one audio timeline, GAP apart; a line split across shots (J/L-cut) stays continuous over the cut
  * a speaking shot starts on its voice (Drama rules: the voice starts ON the cut), so the shot before it keeps the
    whole gap as its tail; any other shot opens GAP/2 ahead of its line, the picture getting there first
  * a shot with no line holds for its set length; the chant is the three-cycle bed, s80 is the user's Flow clip at
    real time with its own voice
  * boundaries are rounded to whole frames once, so cuts never drift against the audio
"""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

P = Path(__file__).resolve().parents[1]
V2 = P / "v2"
OUT = V2 / "animatic"
STILLS = V2 / "frames_clean"
VISUALS = json.loads((V2 / "visuals.json").read_text())["visuals"]
FPS, W, H = 25, 1280, 720
GAP = 0.5
CHANT_BED = P / "edit" / "chant_bed.wav"            # three channel-mantra cycles, built by assemble.py
PROVIDED = P / "clips" / "provided" / "c15a_shiva_flow.mp4"
PROVIDED_VOICE = P / "edit" / "v53_voice.wav"       # that clip's own voice, lifted by assemble.py
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

#: What will replace each still, shown in the label.
ROUTE = {"speak": "lip-sync", "living": "living still", "action": "motion", "shock": "zoom + living still",
         "flashback": "motion", "hero": "Flow clip", "establishing": "camera move", "insert": "camera move",
         "endcard": "camera move", "provided": "Flow clip (final)"}


def probe(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout)


def run(*cmd: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *cmd], check=True)


def line_audio(line: str) -> Path:
    if line == "chant":
        return CHANT_BED
    if line == "c15a_line":
        return PROVIDED_VOICE
    return next((P / "audio" / "lines").glob(f"{line}_*.wav"))


DUR = {ln: probe(line_audio(ln)) for v in VISUALS for ln, _, _ in v["lines"]}


def segs(v: dict) -> list[tuple[str, float, float]]:
    """(line, from, to); a `to` within 50 ms of the wav's end means the line ends in this shot."""
    return [(ln, a, DUR[ln] if b is None or b >= DUR[ln] - 0.05 else b) for ln, a, b in v["lines"]]


def ends_line(v: dict) -> bool:
    s = segs(v)
    return bool(s) and s[-1][2] >= DUR[s[-1][0]] - 1e-6


def opens_line(v: dict) -> bool:
    s = segs(v)
    return bool(s) and s[0][1] == 0


# ---------------------------------------------------------------- timeline

#: Extra air before a shot, given to the shot ahead of it (user, 2026-11-02: let the phone ring a second on s19
#: before the nurse speaks on s20, instead of the ring running under her line).
PAUSE_BEFORE = {"s20": 1.0}


def timeline() -> tuple[list[tuple[dict, float, float]], dict[str, float]]:
    """[(visual, start, end)] in seconds, and each line's start on the audio timeline."""
    t, last_end, starts, rows = 0.0, -GAP, {}, []
    for i, v in enumerate(VISUALS):
        nxt = VISUALS[i + 1] if i + 1 < len(VISUALS) else None
        if v["id"] in PAUSE_BEFORE and rows:
            prev, a, b = rows[-1]
            rows[-1] = (prev, a, b + PAUSE_BEFORE[v["id"]])
            t += PAUSE_BEFORE[v["id"]]
        s, start = segs(v), t
        if not s:
            if v["kind"] == "provided":
                raise SystemExit(f"{v['id']}: provided shot without its line")
            t += v["seconds"]
            rows.append((v, start, t))
            continue
        cursor = t + (0.0 if v["kind"] in ("speak", "provided") else GAP / 2)
        for ln, a, b in s:
            if a == 0:                                    # a new line: at least GAP after the last one ended
                starts[ln] = max(cursor, last_end + GAP)
            cursor = starts[ln] + b
            if b >= DUR[ln] - 1e-6:
                last_end = cursor
        if v["kind"] == "provided":
            cursor = max(cursor, starts[s[0][0]] + probe(PROVIDED))
        if ends_line(v):
            # the next speaking shot opens on its voice, so this shot keeps the whole gap; otherwise it splits
            cursor += GAP if (nxt and opens_line(nxt) and nxt["kind"] in ("speak", "provided")) else GAP / 2
        t = cursor
        rows.append((v, start, t))
    # whole frames, rounded once at each boundary
    q = lambda x: round(x * FPS) / FPS
    return [(v, q(a), q(b)) for v, a, b in rows], starts


ROWS, STARTS = timeline()
TOTAL = ROWS[-1][2]


# ---------------------------------------------------------------- picture

def motion(v: dict, n: int) -> str:
    """A zoompan for the shot's camera note; faces get a gentle push (the animatic stands in for their clips)."""
    cam, k = v["cam"].lower(), v["kind"]
    if "pull" in cam or "out" in cam.split():
        z = f"1.08-0.08*on/{n}"
    elif k == "shock":
        z = f"if(lt(on,{min(10, n)}),1+0.14*on/{min(10, n)},1.14+0.02*(on-{min(10, n)})/{n})"
    elif "push" in cam or k in ("action", "hero", "establishing"):
        z = f"1+0.08*on/{n}"
    else:
        z = f"1+0.04*on/{n}"
    x = "iw/2-(iw/zoom/2)"
    if "pan left" in cam:
        x = f"(iw-iw/zoom)*(1-on/{n})"
    elif "pan right" in cam or "drift" in cam:
        x = f"(iw-iw/zoom)*on/{n}"
    return f"zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)':d={n}:s={W}x{H}:fps={FPS}"


def still(v: dict) -> Path:
    sid = v.get("still") or v["id"]
    hits = sorted(STILLS.glob(f"{sid}.*"))
    if not hits:
        raise SystemExit(f"{v['id']}: no still {sid}.* in {STILLS}")
    return hits[0]


def label(v: dict) -> str:
    reuse = f" (still of {v['still']})" if v.get("still") else ""
    text = f"{v['id']}  {ROUTE.get(v['kind'], v['kind'])}{reuse}"
    return (f"drawtext=fontfile={FONT}:text='{text}':x=24:y=20:fontsize=22:fontcolor=white:"
            f"box=1:boxcolor=black@0.55:boxborderw=8")


def segment(row: tuple[dict, float, float]) -> Path:
    v, a, b = row
    n = int(round((b - a) * FPS))
    out = OUT / "segs" / f"{v['id']}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    enc = ["-frames:v", str(n), "-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p",
           "-an", str(out)]
    if v["kind"] == "provided":
        # the user's clip at real time; its last frame holds if the slot runs past it
        run("-i", str(PROVIDED), "-vf", f"fps={FPS},scale={W}:{H}:flags=lanczos,setsar=1,"
            f"tpad=stop_mode=clone:stop_duration={b - a},{label(v)}", *enc)
    else:
        run("-loop", "1", "-i", str(still(v)), "-vf",
            f"scale={2 * W}:{2 * H}:force_original_aspect_ratio=increase,crop={2 * W}:{2 * H},"
            f"{motion(v, n)},setsar=1,{label(v)}", *enc)
    return out


# ---------------------------------------------------------------- sound

def audio() -> Path:
    """Voices and the chant only: the score and SFX beds are cued to the v1 cut and get re-cued to this timeline
    once it is locked."""
    ins, parts = [], []
    for i, (ln, t) in enumerate(sorted(STARTS.items(), key=lambda kv: kv[1])):
        ins += ["-i", str(line_audio(ln))]
        vol = "1.15" if ln == "chant" else "1.0"
        # adelay on every channel: a bare adelay delays only the left one (ffmpeg-adelay-all-channels)
        parts.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol},"
                     f"adelay={int(round(t * 1000))}:all=1,apad=whole_dur={TOTAL}[a{i}]")
    mix = "".join(f"[a{i}]" for i in range(len(STARTS)))
    parts.append(f"{mix}amix=inputs={len(STARTS)}:normalize=0,loudnorm=I=-16:TP=-2:LRA=11,atrim=0:{TOTAL}[out]")
    out = OUT / "voices.wav"
    OUT.mkdir(parents=True, exist_ok=True)
    run(*ins, "-filter_complex", ";".join(parts), "-map", "[out]", "-ar", "48000", str(out))
    return out


def final() -> Path:
    lst = OUT / "segs" / "list.txt"
    lst.write_text("".join(f"file '{OUT / 'segs' / (v['id'] + '.mp4')}'\n" for v, _, _ in ROWS))
    out = OUT / "mantra_rahasyam_ep1_v2_animatic.mp4"
    run("-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(OUT / "voices.wav"), "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-t", f"{TOTAL:.3f}", "-movflags", "+faststart", str(out))
    return out


def plan() -> None:
    print(f"animatic {TOTAL:.1f} s ({int(TOTAL // 60)}:{TOTAL % 60:04.1f}), {len(ROWS)} shots")
    for v, a, b in ROWS:
        lines = ", ".join(f"{ln}@{STARTS[ln]:.2f}" if s == 0 else f"{ln}+{s:.1f}" for ln, s, _ in segs(v))
        print(f"{v['id']:4} {v['kind']:12} {a:7.2f} {b - a:5.2f}  {lines}")
    (OUT).mkdir(parents=True, exist_ok=True)
    (OUT / "edit_list.json").write_text(json.dumps(
        {"fps": FPS, "total": TOTAL, "shots": [{"id": v["id"], "kind": v["kind"], "start": a, "end": b,
                                                 "seconds": round(b - a, 3)} for v, a, b in ROWS],
         "lines": {ln: round(t, 3) for ln, t in STARTS.items()}}, indent=1))


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "plan"
    only = set(sys.argv[2:])
    if what in ("plan", "all"):
        plan()
    if what in ("segments", "all"):
        rows = [r for r in ROWS if not only or r[0]["id"] in only]
        with ThreadPoolExecutor(6) as pool:
            for out in pool.map(segment, rows):
                print("  ", out.name, flush=True)
    if what in ("audio", "all"):
        print("audio", audio())
    if what in ("final", "all"):
        print("final", final())
