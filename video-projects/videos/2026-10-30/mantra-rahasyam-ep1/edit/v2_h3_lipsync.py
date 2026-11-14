"""Drama v2, speaking shots on MiniMax H3 with our audio anchored, on Modal (2026-11-04).

usage: v2_h3_lipsync.py [--only s32,s39] [--out DIR] [--seed N] [--megapixels X] [--workers N] [--dry-run]

Replaces OmniHuman 2.0 for `speak` shots (user, 2026-11-04: H3's lip-sync and emotion are good, it keeps the other
person in frame still, and it costs ~$0.08-0.15 a shot against OmniHuman's $0.42). One `rpc run h3_lipsync --modal`
per shot (runpod-comfyui, Modal class ComfyH3 on an H100). The rules from the lab (../tool-lab/trials/2026-11-04-freefield/README.md)
are built in, so nobody has to remember them:
  * the clip length is the shortest H3 length that covers the line (5 + 17k frames at 24 fps; the graph snaps
    `seconds` up), so the tail after the line stays under ~0.7 s;
  * the audio slice is padded with silence to that length and the prompt says they fall silent after the last word,
    or H3 keeps mouthing in the tail;
  * "the line end" is where the voice stops, not where the slice stops: a slice can end in silence (s39's 2.60 s
    slice is speech to 1.81 s, then 0.8 s of pause, and H3 mouthed through it; user, 2026-11-04). The voice end is
    found with silencedetect; H3 gets the speech only, padded with silence;
  * the clip is cut at the voice end and its last frame held to the slice or clip length, whichever is longer, and
    the original ElevenLabs slice replaces H3's re-synthesised soundtrack.
Output goes to v2/clips/lipsync/<id>.mp4, where v2_assemble.py looks first; existing clips are skipped. Every run is
recorded in v2/clips/_h3_runs.json.
"""

import json
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from math import ceil
from pathlib import Path

P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P / "edit"))
from v2_clips import CLIPS, RPC, VISUALS, audio_slice, kind, still  # noqa: E402
from v2_prompts import EXPR  # noqa: E402  the stills' own expression wording

LEDGER = CLIPS / "_h3_runs.json"
FPS = 24
SEED = 42
MEGAPIXELS = 0.6
#: Modal containers working at once. Each cold container loads ~50 GB of weights (~1-2 min) before its first shot.
WORKERS = 3


def dur(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(path)], capture_output=True, text=True, check=True).stdout)


#: silencedetect settings for the voice end: ElevenLabs slices sit near -60 dB between words, speech well above -35.
SILENCE_DB, SILENCE_MIN = -35, 0.15
#: Frames of the closing mouth kept after the voice stops, before the hold.
AFTER_VOICE = 0.08


def voice_end(path: Path) -> float:
    """Where the voice stops: the start of the slice's trailing silence, or its length if it ends mid-sound."""
    length = dur(path)
    log = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af",
                          f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN}", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    if starts and (len(ends) < len(starts) or ends[-1] >= length - 0.02) and starts[-1] > 0.3:
        return starts[-1]
    return length


def h3_seconds(line: float) -> float:
    """The clip length H3 renders for a line: the graph's own snap, round(s * 24) up to the 5 + 17k frame grid."""
    n = max(5, round(line * FPS))
    return (n + (5 - n % 17) % 17) / FPS


def prompt(v: dict) -> str:
    """Who and where from the still's own prompt, the shot's expression, and the tail instruction."""
    still_prompt = v.get("prompt") or ""
    name = (v["who"] or ["the person"])[0].capitalize()
    m = re.search(rf"{re.escape(name)}, an? ([^,]+)", still_prompt)
    who = f"{name}, {'an' if m and m.group(1)[0] in 'aeiou8' else 'a'} {m.group(1)}," if m else name
    scene = re.search(r"Scene: ([^.]+)\.", still_prompt)
    # the stills' wording keeps the mouth shut on purpose (a lip-sync still starts closed); said to H3 it would
    # fight the speech, so every mouth/lips clause goes
    expr = ", ".join(c for c in EXPR.get(v["expr"] or "", "").replace(":", ",").split(", ")
                     if c and not any(w in c for w in ("mouth", "lips")))
    if any(ln == "chant" for ln, _, _ in v["lines"]):
        act = f"{who} chants the mantra softly in Sanskrit with eyes closed, lips moving gently with the words"
    else:
        act = f"{who} speaks in Telugu to someone near them"
    parts = [f"{act}; {expr}" if expr else act,
             "Only they speak and their lips follow the speech exactly; anyone else in frame stays silent and still",
             scene.group(1) if scene else "",
             "After the last word they fall silent, lips closed, listening, until the end",
             "Static camera. Audio: only the voice, no music"]
    return ". ".join(p for p in parts if p) + "."


def finish(raw: Path, line: Path, cut_s: float, out_s: float, out: Path) -> None:
    """Cut where the voice ends, hold the last frame to out_s, lay the original slice back (silence after)."""
    hold = max(0.0, out_s - cut_s)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-i", str(line), "-filter_complex",
                    f"[0:v]trim=end={cut_s:.3f},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={hold:.3f}[v];"
                    f"[1:a]apad=whole_dur={out_s:.3f}[a]",
                    "-map", "[v]", "-map", "[a]", "-t", f"{out_s:.3f}", "-c:v", "libx264", "-crf", "16",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(out)], check=True)


def render(v: dict, out_dir: Path, seed: int, mp: float, ledger: dict, lock: threading.Lock) -> None:
    """One shot into <out_dir>/<id>.mp4; never raises, the ledger records a failure for the next run."""
    out, tmp = out_dir / f"{v['id']}.mp4", CLIPS / "_tmp_h3" / v["id"]
    line = audio_slice(v)
    line_s, speech_s = dur(line), voice_end(line)
    clip_s = h3_seconds(speech_s + AFTER_VOICE)  # the graph snaps the same way, so the clip covers the voice
    cut_s, out_s = min(speech_s + AFTER_VOICE, clip_s), max(clip_s, line_s)
    padded = CLIPS / "_audio" / f"{v['id']}_h3pad.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(line), "-af",
                    f"atrim=end={speech_s:.3f},apad=whole_dur={ceil(clip_s * 10) / 10}", str(padded)], check=True)
    text = prompt(v)
    entry = {"workflow": "h3_lipsync", "seed": seed, "megapixels": mp, "line_s": round(line_s, 3),
             "voice_end_s": round(speech_s, 3), "clip_s": round(clip_s, 3), "out_s": round(out_s, 3), "prompt": text}
    print(f"== {v['id']} {v['expr'] or ''}: slice {line_s:.2f} s, voice ends {speech_s:.2f} s -> H3 clip {clip_s:.2f} s,"
          f" cut {cut_s:.2f} s, out {out_s:.2f} s", flush=True)
    t0 = time.monotonic()
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        cmd = [str(RPC), "run", "h3_lipsync", "--modal", "-o", str(tmp), "--timeout", "1800"]
        for k, val in {"image": still(v), "audio": padded, "prompt": text, "seconds": round(speech_s + AFTER_VOICE, 3),
                       "seed": seed, "megapixels": mp}.items():
            cmd += ["--set", f"{k}={val}"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip()[-800:] or "rpc run failed")
        raw = next(iter(sorted(tmp.glob("*.mp4"))), None)
        if raw is None:
            raise RuntimeError("no mp4 in the output")
        out.parent.mkdir(parents=True, exist_ok=True)
        finish(raw, line, cut_s, out_s, out)
        entry |= {"file": str(out.relative_to(P)) if out.is_relative_to(P) else str(out),
                  "wall_s": round(time.monotonic() - t0, 1)}
        print(f"ok {v['id']} in {entry['wall_s']:.0f} s -> {out}", flush=True)
    except Exception as exc:
        print(f"FAILED {v['id']}: {exc}", flush=True)
        entry["error"] = str(exc)[:400]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        with lock:
            ledger[v["id"]] = entry
            LEDGER.write_text(json.dumps(ledger, indent=1, ensure_ascii=False) + "\n")


def main() -> None:
    args = sys.argv[1:]
    opt = lambda name, default="": args[args.index(name) + 1] if name in args else default
    only = set(opt("--only").split(",")) - {""}
    out_dir = Path(opt("--out", str(CLIPS / "lipsync"))).resolve()
    seed, mp, workers = int(opt("--seed", SEED)), float(opt("--megapixels", MEGAPIXELS)), int(opt("--workers", WORKERS))
    todo = [v for v in VISUALS if kind(v) == "lipsync" and (not only or v["id"] in only)
            and not (out_dir / f"{v['id']}.mp4").exists()]
    if "--dry-run" in args:
        for v in todo:
            line_s, speech_s = dur(audio_slice(v)), voice_end(audio_slice(v))
            print(f"{v['id']} {still(v).name} slice {line_s:.2f} s, voice ends {speech_s:.2f} s -> H3 clip "
                  f"{h3_seconds(speech_s + AFTER_VOICE):.2f} s\n  {prompt(v)}")
        print(f"\n{len(todo)} speaking shots -> {out_dir}")
        return
    ledger = json.loads(LEDGER.read_text()) if LEDGER.exists() else {}
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for f in [pool.submit(render, v, out_dir, seed, mp, ledger, lock) for v in todo]:
            f.result()
    done = sum((out_dir / f"{v['id']}.mp4").exists() for v in todo)
    print(f"\n{done}/{len(todo)} done", flush=True)


if __name__ == "__main__":
    main()
