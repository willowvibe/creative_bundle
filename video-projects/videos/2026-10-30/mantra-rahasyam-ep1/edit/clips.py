"""Video stage: Wan 2.2 motion clips and InfiniteTalk lip-syncs on the RunPod pod.

usage: clips.py --pod <id>[=<workflow>][,...] [--only v01,v02] [--kind motion|lipsync] [--swap N] [--parallel N]
                [--steps N] [--dry-run]
`--pod a=infinitetalk,b=infinitetalk,c=wan22_i2v,d=wan22_i2v` runs each half on its own pods; a bare id takes both.
`--steps` overrides the graph's sampling steps (node 16); the shipped 6 is two more than the LightX2V LoRA wants.
`--swap` sets InfiniteTalk's blocks_to_swap: leave it out for the workflow default (10, fits 32 GB); pass 0 on a
48 GB+ card to stop streaming transformer blocks over PCIe (measured 2.39x faster on a 96 GB PRO 6000).
`--parallel` splits the batch across the pod's extra ComfyUI servers (:8189, :8190, ... started by `rpc pod up
--workers N`). This does not buy throughput: measured 2026-11-01, two servers on one RTX PRO 6000 render at
316 s/window against 159 s/window solo, so the second queue exactly cancels itself. Use `--pod` pools on
separate pods for real parallelism instead.
Clips land in clips/motion/<visual>.mp4 or clips/lipsync/<visual>.mp4, matching the two review stages in
scripts/drama_review.py (motion and lip-sync are approved separately, with different quality risks). clips/_runs.json
is the ledger: an entry whose clip exists is skipped and a failed shot is retried on the next run, so a stopped run
resumes instead of paying twice. v53 is the user's provided Flow clip (clips/provided/), never generated here.

Lip-sync shots feed the shot's own dialogue to InfiniteTalk with the approved frame; motion shots feed the frame
and the shot description to Wan 2.2. Both run on the same pod, so one boot and one model load serve the episode.
Flow stays reserved for the six extraordinary beats (FLOW_BEATS): Wan renders them as drafts, and a Flow take
replaces the file later without touching this script.
"""

import json
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

P = Path(__file__).resolve().parents[1]
SERIES = P.parents[1] / "mantra-rahasyam"          # videos/mantra-rahasyam
RPC = Path("/mnt/data2/git_repos/creative_repos/runpod-comfyui/.venv/bin/rpc")
SPEC = json.loads((P / "shots.json").read_text())
CLIPS = P / "clips"
MOTION = CLIPS / "motion"                          # review stage "videos"
LIPSYNC = CLIPS / "lipsync"                        # review stage "lipsync" (with the SFX and music files)
LEDGER = CLIPS / "_runs.json"


def out_for(v: dict) -> Path:
    """Where the shot's clip belongs: the stage folder its review gate looks in, not one flat clips/."""
    return (LIPSYNC if v["kind"] == "lipsync" else MOTION) / f"{v['id']}.mp4"


def settle() -> None:
    """Move clips rendered before the stage split (flat clips/<id>.mp4) into their stage folder.

    The gate globs clips/motion and clips/lipsync, so a clip left flat can never be approved; running this on
    startup keeps a half-finished batch resumable across the change instead of re-rendering what already exists.
    """
    known = {v["id"]: v for v in SPEC["visuals"]}
    moved = 0
    for flat in sorted(CLIPS.glob("*.mp4")):
        v = known.get(flat.stem)
        if v is None:
            continue
        dest = out_for(v)
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(flat), dest)
        moved += 1
    if moved:
        print(f"settled {moved} clip(s) into their stage folder", flush=True)
FLOW_BEATS = {"v08", "v34", "v35", "v36", "v40", "v43"}  # user rule: Omni 1.1 Flash for extraordinary beats

# Held back on 2026-10-31: the approved still shows a serene smile while its line says crying/sobbing/broken,
# so a lip-sync would animate the wrong performance. v15 kept the tears but not the mouth (teeth, upturned),
# which InfiniteTalk widens into a grin. They need a frame redo before rendering; the stills are the defect.
# Held back on 2026-10-31 while their stills showed a serene smile against a crying/sobbing/broken line. Those four
# stills were then redone face-only on Nano Banana Pro (edit/expression_fix.py) and are back in play, so the hold is
# empty; keep the mechanism for the next frame that contradicts its line rather than editing the batch by hand.
HOLD: dict[str, str] = {}

# Anguished lines go to OmniHuman on PoYo instead (edit/lipsync_poyo.py): InfiniteTalk opens the mouth with both
# rows of teeth and the corners pulled back, which reads as a grin on grief however the still and prompt are set.
# Measured on v15 against the same redone still, 2026-10-31.
POYO_LIPSYNC = {"v15", "v20", "v22", "v24", "v25", "v39"}


def base_id(line_id: str) -> str:
    """c01b_line / c01a_vo -> c01b / c01a (shots.json ids carry no suffix, audio names do)."""
    return line_id.removesuffix("_line").removesuffix("_vo")


def line_audio(line_id: str) -> Path | None:
    """audio/lines/<line id>_<speaker>.wav; the chant is the shared mantra cycle."""
    if base_id(line_id) == "c11a":
        return SERIES / "assets" / "audio" / "mantra_cycle.wav"
    hits = sorted((P / "audio" / "lines").glob(f"{line_id}_*.wav"))
    return hits[0] if hits else None


GRIEF_WORDS = ("worried", "shattered", "crying", "sobbing", "grave", "desperate", "pleading", "broken",
               "heartbroken", "scared", "panicked", "shocked", "trembling", "grief", "somber")
BASE_NEG = ("bright tones, overexposed, static, blurred details, subtitles, worst quality, low quality, ugly, "
            "extra fingers, poorly drawn hands, poorly drawn face, deformed, disfigured, still picture")
MOOD_NEG = "smiling, happy expression, grin, cheerful, laughing, relaxed pleasant face"


def is_grief(emo: str) -> bool:
    return any(w in emo.lower() for w in GRIEF_WORDS)


def prompts_for(v: dict) -> tuple[str, str | None]:
    """InfiniteTalk copies the line's emotion loosely and drifts to a pleasant face, so grief beats say it twice
    and a grief negative goes with them (measured on v15: the clip smiled through a crying still)."""
    if v["kind"] != "lipsync":
        return v["image"], None
    if any(base_id(x) == "c11a" for x in v["lines"]):  # chanting with eyes closed: eye contact contradicts the still
        return ("raghava chants the mantra softly with eyes closed, lips moving with the words, gentle head "
                "movement, staying in frame, photorealistic, no camera cut"), None
    want = {base_id(x) for x in v["lines"]}
    line = next((s for s in SPEC["shots"] if s["id"] in want), None)
    body = (line or {}).get("line") or (line or {}).get("vo") or {}
    emo, who = body.get("emotion", ""), (line or {}).get("speaker", "") or "the person"
    if is_grief(emo):
        return (f"{who} speaks with a {emo} expression; the face stays grief-stricken and distressed, brows drawn "
                f"together, mouth trembling or turned down, tears in the eyes, absolutely no smile; natural head "
                f"movement, staying in frame, photorealistic, no camera cut"), MOOD_NEG
    return (f"{who} speaks {emo or 'naturally'}; natural head movement, steady eye contact, staying in frame, "
            f"photorealistic, no camera cut"), None


def target(pod: str, port: int = 8188) -> list[str]:
    """Where `rpc run` should send the job. 8188 is the pod proxy; the extra servers are only reachable by port."""
    return ["--pod", pod] if port == 8188 else ["--url", f"https://{pod}-{port}.proxy.runpod.net"]


def run(workflow: str, where: list[str], frame: Path, prompt: str, out: Path, audio: Path | None, timeout: int,
        frames: int = 0, negative: str = "", swap: int | None = None, steps: int | None = None) -> None:
    cmd = [str(RPC), "run", workflow, *where,
           "--set", f"image={frame}", "--set", f"prompt={prompt}", "-o", str(out), "--timeout", str(timeout)]
    if frames:
        cmd[-4:-4] = ["--set", f"frames={frames}"]
    if negative:
        cmd[-4:-4] = ["--set", f"negative={negative}"]
    if audio:
        cmd[-4:-4] = ["--set", f"audio={audio}"]
    # block_swap exists only in the InfiniteTalk graph; wan22_i2v is the native ComfyUI Wan path and would reject it.
    if swap is not None and workflow == "infinitetalk":
        cmd[-4:-4] = ["--set", f"block_swap={swap}"]
    # Node 3 loads the LightX2V adapter, a *4-step* distillation: the graph's own 6 steps spend a third of the
    # sampling budget re-refining a latent the LoRA was trained to finish in four.
    if steps is not None and workflow == "infinitetalk":
        cmd[-4:-4] = ["--set", f"steps={steps}"]
    tmp = out  # rpc names the file as ComfyUI saved it; the caller renames it
    if tmp.exists():
        shutil.rmtree(tmp)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-800:] or "rpc run failed")
    print(r.stdout.strip().splitlines()[-2:], flush=True)


def render(v: dict, wf: str, audio: Path | None, where: list[str], swap: int | None, steps: int | None,
           ledger: dict, lock: threading.Lock) -> None:
    """Render one shot into its stage folder and record the outcome. Never raises: a failure is retried next run."""
    frame = P / "frames" / f"{v['id']}.png"
    if not frame.exists():
        print(f"skip {v['id']}: no frame", flush=True)
        return
    prompt, negative = prompts_for(v)
    out = out_for(v)
    out.parent.mkdir(parents=True, exist_ok=True)
    timeout = 2400 if wf == "infinitetalk" else 1800
    frames = wan_frames(v) if wf == "wan22_i2v" else 0
    tail = f" frames={frames}" if frames else ""
    print(f"\n== {v['id']} [{wf}] {audio.name if audio else ''}{tail}", flush=True)
    tmp = CLIPS / "_tmp" / v["id"]
    try:
        run(wf, where, frame, prompt, tmp, audio, timeout, frames,
            f"{BASE_NEG}, {negative}" if negative else "", swap, steps)
        got = sorted(tmp.glob("*.mp4"))
        if not got:
            raise RuntimeError("no mp4 in the output")
        shutil.move(str(got[0]), out)
        print(f"ok {out.name} ({out.stat().st_size // 1024} KB)", flush=True)
        entry = {"workflow": wf, "file": out.name, "frames": frames or None, "flow_beats": v["id"] in FLOW_BEATS}
    except Exception as exc:  # keep the batch going; the ledger records what is missing
        print(f"FAILED {v['id']}: {exc}", flush=True)
        entry = {"workflow": wf, "error": str(exc)[:400]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        # Parallel servers finish in any order, so the ledger is written under a lock rather than per-loop.
        with lock:
            ledger[v["id"]] = entry
            LEDGER.write_text(json.dumps(ledger, indent=1) + "\n")


def dialogue_seconds(v: dict) -> float:
    """How long the shot's own voice runs: what a motion clip has to cover."""
    tot = 0.0
    for line in v["lines"]:
        f = line_audio(line)
        if f:
            tot += float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                         "csv=p=0", str(f)], capture_output=True, text=True).stdout or 0)
    return tot


def wan_frames(v: dict) -> int:
    """81 frames = 5.06 s at 16 fps. Long-voice shots get more frames, capped at 121 (7.56 s): past that a 32 GB
    card gets slow and a second clip is the cheaper cut in assembly."""
    need = dialogue_seconds(v) + 0.8
    frames = int(-(-need * 16 // 4) * 4 + 1)  # length must be 4k+1
    return max(81, min(frames, 121))


def main() -> None:
    args = sys.argv[1:]
    # `--pod` takes `id=workflow` entries. A 32 GB card holds one model family at a time, so putting lip-sync and
    # motion on different pods means neither half evicts the other's weights mid-batch; a bare `id` takes every kind.
    pools: dict[str, list[str]] = {}
    for entry in (args[args.index("--pod") + 1] if "--pod" in args else "").split(","):
        if not entry:
            continue
        pid, _, wf = entry.partition("=")
        pools.setdefault(wf, []).append(pid)
    dry = "--dry-run" in args
    only = set(args[args.index("--only") + 1].split(",")) if "--only" in args else set()
    kind = args[args.index("--kind") + 1] if "--kind" in args else ""
    # 10 is the workflow default and is what fits a 32 GB card at 720p; 0 only fits >=48 GB. Measured 2026-10-31 on a
    # PRO 6000 96 GB: 318 s warm against 760 s on a PRO 4500 32 GB for the same two-window clip.
    swap = int(args[args.index("--swap") + 1]) if "--swap" in args else None
    # Must match `rpc pod up --workers N`: each extra server needs its port declared at pod creation.
    parallel = int(args[args.index("--parallel") + 1]) if "--parallel" in args else 1
    # Leave unset to keep whatever the workflow declares; the graph currently runs the 4-step distill at 6.
    steps = int(args[args.index("--steps") + 1]) if "--steps" in args else None
    if not pools and not dry:
        raise SystemExit("--pod <pod-id> is required (rpc pod up)")

    settle()
    CLIPS.mkdir(exist_ok=True)
    ledger = json.loads(LEDGER.read_text()) if LEDGER.exists() else {}
    todo = []
    for v in SPEC["visuals"]:
        out = out_for(v)
        if only and v["id"] not in only:
            continue
        if kind and v["kind"] != kind:
            continue
        if v["id"] in HOLD and not only:
            print(f"hold {v['id']}: {HOLD[v['id']]}", flush=True)
            continue
        if v["id"] in POYO_LIPSYNC and not only:
            continue  # edit/lipsync_poyo.py owns these clips
        if out.exists():
            continue
        if v["kind"] == "lipsync":
            audio = line_audio(v["lines"][0]) if v["lines"] else None
            if audio is None:
                print(f"skip {v['id']}: no audio for {v['lines']}", flush=True)
                continue
            todo.append((v, "infinitetalk", audio))
        elif v["kind"] == "motion":
            if v["id"] == "v53":  # the user's Flow clip is the source of truth
                continue
            todo.append((v, "wan22_i2v", None))

    # Grief lip-syncs are the batch's whole quality risk (the model drifts to a smile), so they render first:
    # a bad look costs two shots to discover instead of an eight-hour run. Motion last, since a Wan draft is
    # cheap to throw away when a Flow take replaces it.
    rank = {"infinitetalk": 0, "wan22_i2v": 1}
    todo.sort(key=lambda t: (rank[t[1]], 0 if prompts_for(t[0])[1] else 1))
    servers = max(1, parallel)
    where_pods = ",".join(p for group in pools.values() for p in group) or "(dry run)"
    print(f"{len(todo)} shots to render on {where_pods} across {servers} server(s)", flush=True)

    # Build one shard per (pod, workflow). Shots are grouped by the graph that renders them, then split across
    # the pods assigned to that graph; a shot whose graph was never assigned goes to every pod, which is the
    # single-pod case. Round-robin inside a group, not contiguous blocks: the list is sorted worst-first, so each
    # pod gets the same mix of grief and ordinary lines instead of one inheriting the whole slow tail.
    shards: list[tuple[list[str], list]] = []
    for wf in ("infinitetalk", "wan22_i2v"):
        shots = [t for t in todo if t[1] == wf]
        if not shots:
            continue
        pods = pools.get(wf) or [p for group in pools.values() for p in group]
        if len(pods) == 1 and parallel > 1:
            # One pod, several ComfyUI servers: same machine, so the SMs are shared and throughput does not scale.
            where_list = [target(pods[0], 8188 + i) for i in range(parallel)]
        else:
            # One pod = one physical GPU: separate machines scale linearly, unlike extra servers sharing one card.
            where_list = [target(p) for p in pods]
        for i, where in enumerate(where_list):
            shards.append((where, shots[i::len(where_list)]))
            print(f"  shard {wf} -> {where[-1]}: {len(shards[-1][1])} shots", flush=True)

    if dry:
        for v, wf, audio in todo:
            frame = P / "frames" / f"{v['id']}.png"
            if not frame.exists():
                print(f"skip {v['id']}: no frame", flush=True)
                continue
            prompt, negative = prompts_for(v)
            print(f"\n{v['id']} [{wf}] frame={frame.name} audio={audio.name if audio else '-'}\n  {prompt[:300]}"
                  + (f"\n  NEG {negative}" if negative else ""))
        return

    lock = threading.Lock()

    def shard(where: list[str], shots: list) -> None:
        for v, wf, audio in shots:
            render(v, wf, audio, where, swap, steps, ledger, lock)

    if len(shards) > 1:
        with ThreadPoolExecutor(max_workers=len(shards)) as pool:
            futures = [pool.submit(shard, where, shots) for where, shots in shards]
            for f in futures:
                f.result()  # a shard's exception surfaces here instead of vanishing into the pool
    elif shards:
        shard(*shards[0])

    done = sum(1 for v in SPEC["visuals"] if out_for(v).exists())
    print(f"\n{done} clips on disk", flush=True)


if __name__ == "__main__":
    main()
