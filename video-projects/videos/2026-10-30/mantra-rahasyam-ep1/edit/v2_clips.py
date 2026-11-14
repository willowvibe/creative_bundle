"""Drama v2, videos stage: InfiniteTalk lip-syncs and Wan 2.2 motion clips for the v2 shot list (2026-11-01).

usage: v2_clips.py --pod <id>[=<workflow>][,...] [--only s03,s22] [--kind lipsync|motion] [--swap N] [--no-rife]
                   [--dry-run]

Same mechanics as edit/clips.py (Ep1): `rpc run` per shot, `id=workflow` pod pools so lip-sync and motion never evict
each other's weights, a ledger (v2/clips/_runs.json) so a stopped run resumes instead of paying twice. What is v2:
  * the shot list is v2/visuals.json and the stills are v2/frames_clean (a `still:` shot uses the still it names);
  * a speaking shot gets exactly its own slice of audio (the line from/to in visuals.json, the chant cut from the
    three-cycle bed), so every lip-sync is one or two windows and lands on its animatic slot;
  * motion is always 81 frames at the #325 settings (8 steps, high expert LoRA 0.7 at cfg 3), a living still says
    "barely moves" and nothing else, and each clip then gets RIFE 2x (16 -> 32 fps) so the 25 fps cut stops judging
    (#323);
  * grief-risk lip-syncs render first: InfiniteTalk drifts to a smile on them, and two shots is a cheap place to find out.
Hero shots (Flow Omni), camera-move shots (local) and s80 (the user's Flow clip) are not made here.
"""

import json
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P / "edit"))
from v2_prompts import EXPR, GRIEF_RISK  # noqa: E402  the stills' own expression wording

RPC = Path("/mnt/data2/git_repos/creative_repos/runpod-comfyui/.venv/bin/rpc")
V2 = P / "v2"
VISUALS = json.loads((V2 / "visuals.json").read_text())["visuals"]
EDIT = json.loads((V2 / "animatic" / "edit_list.json").read_text())
CLIPS = V2 / "clips"
LEDGER = CLIPS / "_runs.json"
CHANT_BED = P / "edit" / "chant_bed.wav"

LIPSYNC = {"speak"}
MOTION = {"living", "action", "shock", "flashback"}
WAN = {"frames": 81, "steps": 8, "split": 4, "lora_high": 0.7, "cfg_high": 3.0}

BASE_NEG = ("bright tones, overexposed, static, blurred details, subtitles, worst quality, low quality, ugly, "
            "extra fingers, poorly drawn hands, poorly drawn face, deformed, disfigured, still picture, camera cut")
SMILE_NEG = "smiling, happy expression, grin, cheerful, laughing, relaxed pleasant face, showing teeth"
LIVING = ("the person stays almost completely still: only natural blinks and slow breathing, the eyes shift slightly; "
          "no head turn, no mouth movement, no camera movement, photorealistic")


def kind(v: dict) -> str | None:
    return "lipsync" if v["kind"] in LIPSYNC else "motion" if v["kind"] in MOTION else None


def out_for(v: dict) -> Path:
    return CLIPS / kind(v) / f"{v['id']}.mp4"


def still(v: dict) -> Path:
    sid = v.get("still") or v["id"]
    return next((V2 / "frames_clean").glob(f"{sid}.*"))


def line_wav(line: str) -> Path:
    return CHANT_BED if line == "chant" else next((P / "audio" / "lines").glob(f"{line}_*.wav"))


def audio_slice(v: dict) -> Path:
    """The shot's own voice: each (line, from, to) cut from its wav and joined, 48 kHz stereo."""
    out = CLIPS / "_audio" / f"{v['id']}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    ins, parts = [], []
    for i, (ln, a, b) in enumerate(v["lines"]):
        ins += ["-i", str(line_wav(ln))]
        end = f":end={b}" if b is not None else ""
        parts.append(f"[{i}:a]atrim=start={a}{end},asetpts=PTS-STARTPTS,aresample=48000,"
                     f"aformat=channel_layouts=stereo[a{i}]")
    parts.append("".join(f"[a{i}]" for i in range(len(v["lines"]))) + f"concat=n={len(v['lines'])}:v=0:a=1[out]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", ";".join(parts), "-map", "[out]", str(out)],
                   check=True)
    return out


def prompts(v: dict) -> tuple[str, str]:
    expr = EXPR.get(v["expr"] or "", "")
    who = (v["who"] or ["the person"])[0].capitalize()
    risk = v["expr"] in GRIEF_RISK
    if kind(v) == "lipsync":
        if any(ln == "chant" for ln, _, _ in v["lines"]):
            return (f"{who} chants the mantra softly with eyes closed, lips moving gently with the words, a calm "
                    f"devoted face, slight natural head movement, staying in frame, photorealistic"), BASE_NEG
        # the stills' wording keeps the mouth shut on purpose (a lip-sync still starts closed); said to InfiniteTalk it
        # would fight the speech, so every mouth/lips clause goes
        expr = ", ".join(c for c in expr.replace(":", ",").split(", ") if not any(w in c for w in ("mouth", "lips")))
        face = f"{expr}; " if expr else ""
        if risk:  # said twice: InfiniteTalk copies the emotion loosely and drifts to a pleasant face
            return (f"{who} speaks, {face}the face stays distressed the whole time, brows drawn together, absolutely "
                    f"no smile; natural small head movement, staying in frame, photorealistic"), f"{BASE_NEG}, {SMILE_NEG}"
        return (f"{who} speaks, {face}natural small head movement, staying in frame, photorealistic"), BASE_NEG
    action = v["motion"] or ""
    if v["kind"] in ("living", "shock"):
        text = f"{action}. {LIVING}" if action else LIVING
    else:
        # "clearly visible motion" made Veo sit the defibrillated patient up (s45, 2026-11-02): keep it plausible
        text = (f"{action}. Realistic, physically plausible motion; everyone stays where they are unless the action "
                f"says otherwise; no one stands up, jumps or leaves the frame; steady camera, photorealistic")
    neg = f"{BASE_NEG}, {SMILE_NEG}" if v["expr"] and v["expr"] in GRIEF_RISK else BASE_NEG
    return text, neg


def rpc(workflow: str, where: list[str], sets: dict, out_dir: Path, timeout: int) -> Path:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    cmd = [str(RPC), "run", workflow, *where, "-o", str(out_dir), "--timeout", str(timeout)]
    for k, val in sets.items():
        cmd += ["--set", f"{k}={val}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-800:] or "rpc run failed")
    got = sorted(out_dir.glob("*.mp4"))
    if not got:
        raise RuntimeError("no mp4 in the output")
    return got[0]


#: InfiniteTalk's blocks_to_swap: 10 fits a 32 GB card at 720p (0 runs out of memory there, measured 2026-10-30);
#: pass `--swap 0` on a 48 GB+ card.
SWAP = 10


def render(v: dict, where: list[str], rife: bool, ledger: dict, lock: threading.Lock) -> None:
    """One shot into v2/clips/<kind>/<id>.mp4; never raises, the ledger records a failure for the next run."""
    k, out, tmp = kind(v), out_for(v), CLIPS / "_tmp" / v["id"]
    out.parent.mkdir(parents=True, exist_ok=True)
    prompt, negative = prompts(v)
    print(f"\n== {v['id']} [{k}] {v['kind']} {v['expr'] or ''}", flush=True)
    try:
        if k == "lipsync":
            got = rpc("infinitetalk", where, {"image": still(v), "audio": audio_slice(v), "prompt": prompt,
                                              "negative": negative, "block_swap": SWAP}, tmp, 2400)
            entry = {"workflow": "infinitetalk", "block_swap": SWAP}
        else:
            raw = CLIPS / "_raw" / f"{v['id']}.mp4"
            entry = {"workflow": "wan22_i2v", **WAN}
            if raw.exists():  # a Wan render that finished before a stopped run: only its RIFE pass is missing
                got = raw
            else:
                got = rpc("wan22_i2v", where, {"image": still(v), "prompt": prompt, "negative": negative, **WAN},
                          tmp, 1800)
            if rife:
                raw.parent.mkdir(parents=True, exist_ok=True)
                if got != raw:
                    shutil.move(str(got), raw)
                got = rpc("interpolate", where, {"video": raw, "multiplier": 2, "fps": 32}, tmp, 900)
                entry["rife"] = "2x 32fps"
        shutil.move(str(got), out)
        print(f"ok {out.relative_to(P)} ({out.stat().st_size // 1024} KB)", flush=True)
        entry |= {"file": str(out.relative_to(P)), "prompt": prompt}
    except Exception as exc:
        print(f"FAILED {v['id']}: {exc}", flush=True)
        entry = {"error": str(exc)[:400]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        with lock:
            ledger[v["id"]] = entry
            LEDGER.write_text(json.dumps(ledger, indent=1) + "\n")


def main() -> None:
    args = sys.argv[1:]
    opt = lambda name: args[args.index(name) + 1] if name in args else ""
    pools: dict[str, list[str]] = {}
    for entry in opt("--pod").split(","):
        if entry:
            pid, _, wf = entry.partition("=")
            pools.setdefault(wf, []).append(pid)
    dry, rife = "--dry-run" in args, "--no-rife" not in args
    only = set(opt("--only").split(",")) - {""}
    want = opt("--kind")
    global SWAP
    SWAP = int(opt("--swap") or SWAP)
    if not pools and not dry:
        raise SystemExit("--pod <pod-id> is required (rpc pod up)")
    slots = {s["id"]: s["seconds"] for s in EDIT["shots"]}

    todo = [v for v in VISUALS if kind(v) and (not only or v["id"] in only) and (not want or kind(v) == want)
            and not out_for(v).exists()]
    todo.sort(key=lambda v: (kind(v) != "lipsync", v["expr"] not in GRIEF_RISK))
    if dry:
        for v in todo:
            p, n = prompts(v)
            extra = f"audio {sum((b if b is not None else 0) - a for _, a, b in v['lines']):.2f}s" \
                if kind(v) == "lipsync" else f"slot {slots[v['id']]:.2f}s"
            print(f"{v['id']} [{kind(v)}] {still(v).name} {extra}\n  + {p}\n  - {n[-80:]}")
        print(f"\n{len(todo)} shots: {sum(kind(v) == 'lipsync' for v in todo)} lip-sync, "
              f"{sum(kind(v) == 'motion' for v in todo)} motion")
        return

    ledger = json.loads(LEDGER.read_text()) if LEDGER.exists() else {}
    lock = threading.Lock()
    shards = []
    for wf, k in (("infinitetalk", "lipsync"), ("wan22_i2v", "motion")):
        shots = [v for v in todo if kind(v) == k]
        pods = pools.get(wf) or [p for g in pools.values() for p in g]
        for i, pod in enumerate(pods):
            if shots[i::len(pods)]:
                shards.append((["--pod", pod], shots[i::len(pods)]))
                print(f"shard {wf} -> {pod}: {len(shots[i::len(pods)])} shots", flush=True)

    def shard(where: list[str], shots: list) -> None:
        for v in shots:
            render(v, where, rife, ledger, lock)

    # one thread per shard; two shards on the same pod still queue on its one ComfyUI
    with ThreadPoolExecutor(max_workers=max(1, len(shards))) as pool:
        for f in [pool.submit(shard, w, s) for w, s in shards]:
            f.result()
    done = sum(out_for(v).exists() for v in VISUALS if kind(v))
    print(f"\n{done}/{sum(1 for v in VISUALS if kind(v))} clips on disk", flush=True)


if __name__ == "__main__":
    main()
