"""Re-make the episode keyframes with self-hosted Qwen-Image-Edit 2511 (8-step), for comparison with PoYo.

usage: qwen_frames.py <comfyui base url> [shot_id ...]

Character references are the PoYo portraits in refs/ (one person per reference):
1 character  -> [face crop, full portrait]
2-3 characters -> one full portrait each
no character -> prompt only (s08 also gets the channel thumbnail for the phone screen)
Output: frames_qwen/<shot>.png
"""

import copy
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/data2/git_repos/creative_repos/runpod-comfyui/src")
from rpcomfy.comfy import ComfyClient  # noqa: E402
from rpcomfy.workflow import WORKFLOWS_DIR, validate  # noqa: E402

sys.path.insert(0, "/mnt/data2/git_repos/creative_repos/video-editor/scripts")
from drama_review import require  # noqa: E402

P = Path(__file__).resolve().parents[1]
require(P, "voices")  # user approved the dialogue and the voice emotion
SPEC = json.loads((P / "shots.json").read_text())
BASE = json.loads((WORKFLOWS_DIR / "qwen_image_edit_3ref.json").read_text())
TMP = P / "edit" / "_upload"
TMP.mkdir(exist_ok=True)
LOADERS = [("7", "9"), ("8", "10"), ("17", "18")]  # (LoadImage, ImageScaleToTotalPixels) per reference slot


def name(who: str) -> str:
    return SPEC["characters"][who].split(",")[0]


def refs_for(shot: dict) -> tuple[list[Path], str]:
    who = shot["who"]
    if len(who) == 1:
        n = name(who[0])
        return ([P / "refs/faces" / f"{who[0]}.jpg", P / "refs" / f"{who[0]}.jpg"],
                f"Image 1 is {n}'s face and image 2 is {n}'s full portrait (clothing, hair, jewellery). "
                f"Keep exactly {n}'s face from image 1: same eyes, eyebrows, nose, lips, jawline and skin tone.")
    if who:
        parts = [f"image {i + 1} is {name(w)}" for i, w in enumerate(who)]
        return ([P / "refs" / f"{w}.jpg" for w in who],
                f"{'; '.join(parts)}. Keep each person's face, hair, clothing and jewellery exactly as in their "
                f"image; exactly {len(who)} people from the references, no extra people.")
    if shot.get("screen"):
        return [P / shot["screen"]], "Image 1 is the exact picture shown on the phone screen."
    return [], ""


def graph_for(n_refs: int, prompt: str, seed: int = 224) -> dict:
    g = copy.deepcopy(BASE)
    for load, scale in LOADERS[n_refs:]:
        del g[load], g[scale]
    for node in ("11", "12"):
        for i in range(n_refs + 1, 4):
            g[node]["inputs"].pop(f"image{i}", None)
    g["11"]["inputs"]["prompt"] = prompt
    g["14"]["inputs"]["seed"] = seed
    g["16"]["inputs"]["filename_prefix"] = "rpc/qwen_ep1"
    return g


def main() -> None:
    comfy = ComfyClient(sys.argv[1])
    only = set(sys.argv[2:])
    out_dir = P / "frames_qwen"
    out_dir.mkdir(exist_ok=True)
    info = comfy.object_info()
    log = []
    for shot in SPEC["shots"]:
        sid = shot["id"]
        if shot["kind"] == "title" or (only and sid not in only) or (out_dir / f"{sid}.png").exists():
            continue
        refs, lead = refs_for(shot)
        prompt = (f"{lead} {shot['image']} {shot.get('style', SPEC['style'])} "
                  "Wide 16:9 cinematic frame, no text, no captions.").strip()
        g = graph_for(len(refs), prompt)
        # Unique upload names: faces/abhinav.jpg and abhinav.jpg would overwrite each other on the server.
        names = []
        for i, r in enumerate(refs):
            tmp = TMP / f"{sid}_ref{i + 1}_{r.parent.name}_{r.name}"
            shutil.copyfile(r, tmp)
            names.append(comfy.upload(tmp))
        for (load, _), uploaded in zip(LOADERS, names):
            g[load]["inputs"]["image"] = uploaded
        # Uploaded names aren't in the object_info fetched at start, so skip those slots.
        problems = validate(g, info, skip={(load, "image") for load, _ in LOADERS})
        if problems:
            print(f"SKIP {sid}: {problems}", flush=True)
            continue
        t0 = time.time()
        entry = comfy.wait(comfy.submit(g), timeout=1200)
        ref = ComfyClient.output_files(entry)[0]
        comfy.download(ref, out_dir).replace(out_dir / f"{sid}.png")
        secs = round(time.time() - t0)
        print(f"ok {sid} refs={len(refs)} {secs}s", flush=True)
        log.append({"out": f"frames_qwen/{sid}.png", "model": "qwen_image_edit_2511 8-step (self-hosted)",
                    "refs": len(refs), "secs": secs})
    with (P / "generation_log.jsonl").open("a") as fh:
        for row in log:
            fh.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main()
