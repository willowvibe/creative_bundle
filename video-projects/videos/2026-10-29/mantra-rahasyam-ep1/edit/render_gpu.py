"""Render motion clips (Wan 2.2) then lip-sync shots (InfiniteTalk) on the RunPod pod.

usage: render_gpu.py motion|interp|lipsync [shot_id ...]
Runs `rpc run` from the runpod-comfyui repo; skips shots whose clip already exists.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/data2/git_repos/creative_repos/video-editor/scripts")
from drama_review import require  # noqa: E402

P = Path(__file__).resolve().parents[1]
require(P, "voices")  # user approved the dialogue and the voice emotion
RPC = "/mnt/data2/git_repos/creative_repos/runpod-comfyui/.venv/bin/rpc"
SPEC = json.loads((P / "shots.json").read_text())
NEG_MOTION = "blurry, distorted face, extra fingers, deformed hands, text, watermark, static, low quality"
NEG_TALK = ("smiling, laughing, grinning, cheerful, bright tones, overexposed, static, blurred details, subtitles, "
            "worst quality, low quality, extra fingers, poorly drawn hands, deformed face, still picture")
TAIL = " Photoreal, natural motion, realistic hands and faces, no text, no camera shake."

stage, only = sys.argv[1], set(sys.argv[2:])
log = []
for s in SPEC["shots"]:
    sid = s["id"]
    if only and sid not in only:
        continue
    out = P / "clips" / (f"{sid}_i.mp4" if stage == "interp" else f"{sid}.mp4")
    if out.exists():
        continue
    tmp = P / "clips" / f"_{sid}"
    if stage == "motion" and s["kind"] == "motion":
        args = ["wan22_i2v", "--set", f"image={P / 'frames' / f'{sid}.jpg'}", "--set", f"prompt={s['motion']}{TAIL}",
                "--set", f"negative={NEG_MOTION}", "--set", "seed=224"]
    elif stage == "interp" and s["kind"] == "motion" and (P / "clips" / f"{sid}.mp4").exists():
        args = ["interpolate", "--set", f"video={P / 'clips' / f'{sid}.mp4'}"]
    elif stage == "lipsync" and s["kind"] == "lipsync":
        neg = NEG_TALK.replace("smiling, laughing, grinning, cheerful, ", "") if "joy" in s["emotion"] else NEG_TALK
        speaker = SPEC["characters"][s["speaker"]].split(",")[0]
        prompt = (f"{speaker} is speaking, lips matching the speech, {s['emotion']}. {s['image']} "
                  "Only the speaker talks; anyone else stays still. Photoreal, cinematic.")
        args = ["infinitetalk", "--set", "base_model=Wan2_1-I2V-14B-720P_fp8_e4m3fn.safetensors",
                "--set", "width=1280", "--set", "height=720", "--set", f"block_swap={os.environ.get('BLOCK_SWAP', '10')}",
                "--set", f"image={P / 'frames' / f'{sid}.jpg'}",
                "--set", f"audio={P / 'edit' / 'drive' / f'{sid}.wav'}", "--set", f"prompt={prompt}",
                "--set", f"negative={neg}", "--set", "seed=7"]
    else:
        continue
    t0 = time.time()
    r = subprocess.run([RPC, "run", *args, "-o", str(tmp)], capture_output=True, text=True)
    secs = round(time.time() - t0)
    made = sorted(tmp.glob("*.mp4")) if tmp.exists() else []
    if r.returncode != 0 or not made:
        print(f"FAILED {sid} after {secs}s: {r.stderr[-400:] or r.stdout[-400:]}", flush=True)
        continue
    made[0].replace(out)
    tmp.rmdir()
    print(f"ok {sid} {secs}s", flush=True)
    log.append({"out": f"clips/{sid}.mp4", "workflow": args[0], "secs": secs})

with (P / "generation_log.jsonl").open("a") as fh:
    for row in log:
        fh.write(json.dumps(row) + "\n")
