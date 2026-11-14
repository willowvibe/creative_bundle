"""A/B one shot against itself on a different card or block_swap setting, wall-clocked.

usage: ab_blockswap.py --pod <pod-id> [--swap N] [--repeat 2] [--tag pro6000] v19

Why (2026-10-31): the batch's cost is almost entirely InfiniteTalk sampling time, and the only two levers are the card
and `blocks_to_swap`. The measured baseline is 405 s per 720p window on an RTX PRO 4500 with block_swap=10, so every
candidate number has to be produced the same way to mean anything: same frame, same audio, same prompt, same workflow,
timed around the whole `rpc run` (which includes ComfyUI's own model load on the first run).

Run it twice. The first run pays the model load; the second is the steady-state number to compare with 405 s.
Output goes to clips/_ab/, never to the stage folder, so an experiment can never be mistaken for a finished clip.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from clips import BASE_NEG, line_audio, prompts_for, wan_frames  # noqa: E402

RPC = Path("/mnt/data2/git_repos/creative_repos/runpod-comfyui/.venv/bin/rpc")
SPEC = json.loads((P / "shots.json").read_text())
AB = P / "clips" / "_ab"


def main() -> None:
    args = sys.argv[1:]
    pod = args[args.index("--pod") + 1]
    swap = args[args.index("--swap") + 1] if "--swap" in args else None
    repeat = int(args[args.index("--repeat") + 1]) if "--repeat" in args else 2
    tag = args[args.index("--tag") + 1] if "--tag" in args else "test"
    # ComfyUI caches by prompt graph: an identical second run returns in ~5 s without sampling. A different seed
    # is the cheapest way to force a real execution, so the warm number is a real warm number.
    seed = args[args.index("--seed") + 1] if "--seed" in args else None
    ids = [a for a in args if a.startswith("v") and len(a) == 3]
    if not ids:
        raise SystemExit("give at least one visual id, e.g. v19")
    AB.mkdir(parents=True, exist_ok=True)

    for vid in ids:
        v = next(x for x in SPEC["visuals"] if x["id"] == vid)
        wf = "infinitetalk" if v["kind"] == "lipsync" else "wan22_i2v"
        frame = P / "frames" / f"{vid}.png"
        audio = line_audio(v["lines"][0]) if wf == "infinitetalk" and v["lines"] else None
        prompt, negative = prompts_for(v)
        for run in range(1, repeat + 1):
            out = AB / f"{vid}_swap{swap or 'default'}_{tag}_run{run}"
            cmd = [str(RPC), "run", wf, "--pod", pod,
                   "--set", f"image={frame}", "--set", f"prompt={prompt}", "-o", str(out),
                   "--timeout", "2400"]
            if swap is not None:
                cmd += ["--set", f"block_swap={swap}"]
            if seed is not None:
                cmd += ["--set", f"seed={int(seed) + run - 1}"]
            if negative:
                cmd += ["--set", f"negative={BASE_NEG}, {negative}"]
            if audio:
                cmd += ["--set", f"audio={audio}"]
            if wf == "wan22_i2v":
                cmd += ["--set", f"frames={wan_frames(v)}"]

            t0 = time.time()
            r = subprocess.run(cmd, capture_output=True, text=True)
            secs = time.time() - t0
            if r.returncode != 0:
                print(f"FAILED {vid} run{run} after {secs:.0f}s: "
                      f"{(r.stderr or r.stdout).strip()[-500:]}", flush=True)
                continue
            # rpc treats -o as a directory it fills with whatever ComfyUI saved
            got = sorted(out.glob("*.mp4")) if out.is_dir() else sorted(AB.glob(f"{out.name}*.mp4"))
            size = got[0].stat().st_size // 1024 if got else 0
            print(f"{vid} [{wf}] swap={swap or 'default'} run{run}: {secs:.0f}s "
                  f"({secs / 60:.2f} min, {size} KB)", flush=True)


if __name__ == "__main__":
    main()
