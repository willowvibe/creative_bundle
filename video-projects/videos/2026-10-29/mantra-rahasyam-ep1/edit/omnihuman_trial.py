"""Multi-character lip-sync trial: OmniHuman 2.0 (PoYo) on the shots where InfiniteTalk made everyone talk.

usage: omnihuman_trial.py [shot_id ...]     (default s04 s15 s16)
Same start frame and drive audio as the InfiniteTalk clip. OmniHuman has no speaker mask, so the prompt names the
speaker and tells everyone else to keep their mouth closed. Audio under 4 s is padded with silence (the model's
minimum) and the result is trimmed back. Output: clips_omni/<shot>.mp4
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/data2/git_repos/creative_repos/runpod-comfyui/scripts")
sys.path.insert(0, "/mnt/data2/git_repos/creative_repos/runpod-comfyui/src")
from lipsync_trial import upload  # noqa: E402  (fal storage -> public URL)
from rpcomfy.config import load_env_file  # noqa: E402

load_env_file()  # FAL_KEY for the upload

P = Path(__file__).resolve().parents[1]
SPEC = {s["id"]: s for s in json.loads((P / "shots.json").read_text())["shots"]}
ENV = dict(line.split("=", 1) for line in Path("/mnt/data2/git_repos/creative_repos/video-editor/.env").read_text().splitlines()
           if "=" in line and not line.startswith("#"))
TMP = P / "edit" / "_upload"
OUT = P / "clips_omni"
SILENT = {  # who must stay silent, described by what is visible in the frame
    "s04": "Only the doctor facing the camera speaks. The two people in the foreground, seen from behind, "
           "do not speak and do not move their mouths.",
    "s15": "Only the doctor speaks. The nurse behind him keeps her mouth closed and does not speak.",
    "s16": "Only the woman speaks. The young man lying in the bed keeps his mouth closed and does not speak; "
           "he only breathes weakly and looks at her.",
}


def duration(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(path)], capture_output=True, text=True, check=True).stdout)


def poyo(*args: str) -> dict:
    env = {**os.environ, "POYO_API_KEY": ENV["POYO_API_KEY"].strip()}
    r = subprocess.run(["npx", "-y", "@poyoapi/cli", *args, "--format", "json", "--request-source", "skill"],
                       capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise SystemExit(f"poyo {args[0]} failed: {r.stderr[-600:] or r.stdout[-600:]}")
    return json.loads(r.stdout)


def main() -> None:
    TMP.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    for sid in sys.argv[1:] or ["s04", "s15", "s16"]:
        s, out = SPEC[sid], OUT / f"{sid}.mp4"
        if out.exists():
            continue
        drive = P / "edit" / "drive" / f"{sid}.wav"
        secs = duration(drive)
        audio = TMP / f"{sid}_omni.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(drive), "-af", "apad=whole_dur=4.1", str(audio)],
                       check=True)
        speaker = SPEC[sid]["speaker"]
        prompt = f"{SILENT[sid]} The {speaker} speaks with a {s['emotion']} expression, lips matching the speech. " \
                 "Photoreal, cinematic, natural small movements."
        saved = TMP / f"{sid}_omni.task"
        if saved.exists():  # resume a paid task instead of submitting it again
            task = saved.read_text().strip()
        else:
            job = poyo("submit", "omnihuman-2.0", "--image-urls", upload(P / "frames" / f"{sid}.jpg"),
                       "--audio-url", upload(audio), "--prompt", prompt)
            task = job.get("task_id") or job.get("data", {}).get("task_id") or job.get("id")
            saved.write_text(str(task))
        print(f"{sid}: task {task}", flush=True)
        for _ in range(120):
            done = poyo("task", "get", str(task))
            state = json.dumps(done).lower()
            if '"failed"' in state:
                raise SystemExit(f"{sid} failed: {json.dumps(done)[:600]}")
            if '"succeeded"' in state or '"finished"' in state or '"completed"' in state:
                break
            time.sleep(10)
        else:
            raise SystemExit(f"{sid}: still running after 20 min, task {task}")
        print(json.dumps(done)[:600], flush=True)
        url = next(u for u in json.dumps(done).replace('"', " ").split() if u.startswith("http") and ".mp4" in u)
        raw = TMP / f"{sid}_omni_raw.mp4"
        subprocess.run(["curl", "-sSfL", "-o", str(raw), url], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-t", f"{secs:.3f}", "-c:v", "libx264",
                        "-crf", "18", "-c:a", "aac", str(out)], check=True)
        print(f"ok {sid} {secs:.2f} s -> {out.name}", flush=True)


if __name__ == "__main__":
    main()
