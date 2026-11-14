"""Character refs and shot keyframes on PoYo (nano-banana-2 / nano-banana-2-edit).

usage: images.py chars [name ...]     # 3:4 reference portraits
       images.py shots [shot_id ...]  # 16:9 keyframes, all submitted at once
"""

import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, "/mnt/data2/git_repos/creative_repos/video-editor/scripts")
from drama_review import require  # noqa: E402

P = Path(__file__).resolve().parents[1]
require(P, "voices")  # user approved the dialogue and the voice emotion
ENV = dict(
    line.split("=", 1) for line in Path("/mnt/data2/git_repos/creative_repos/video-editor/.env").read_text().splitlines()
    if "=" in line and not line.startswith("#")
)
SPEC = json.loads((P / "shots.json").read_text())
poyo = httpx.Client(base_url="https://api.poyo.ai", headers={"Authorization": f"Bearer {ENV['POYO_API_KEY'].strip()}"},
                    timeout=120)


def unwrap(d):
    if d.get("code") not in (None, 200):
        raise RuntimeError(d)
    return d.get("data", d)


def submit(model: str, inp: dict) -> str:
    r = poyo.post("/api/generate/submit", json={"model": model, "input": inp})
    r.raise_for_status()
    return unwrap(r.json())["task_id"]


def collect(tasks: dict[str, tuple[str, Path]]) -> None:
    log = []
    t0 = time.time()
    while tasks:
        time.sleep(5)
        for key, (tid, out) in list(tasks.items()):
            s = unwrap(poyo.get(f"/api/generate/status/{tid}").json())
            if s.get("status") == "finished":
                url = next(f["file_url"] for f in s["files"] if "file_url" in f)
                out.write_bytes(httpx.get(url, timeout=120, follow_redirects=True).content)
                log.append({"out": str(out.relative_to(P)), "secs": round(time.time() - t0)})
                print("ok", out.name, flush=True)
                del tasks[key]
            elif s.get("status") == "failed":
                print("FAILED", out.name, s.get("error_message"), flush=True)
                del tasks[key]
        if time.time() - t0 > 1200:
            print("TIMEOUT", list(tasks))
            break
    with (P / "generation_log.jsonl").open("a") as fh:
        for row in log:
            fh.write(json.dumps(row) + "\n")


def upload(path: Path) -> str:
    r = httpx.post("https://rest.alpha.fal.ai/storage/upload/initiate?storage_type=fal-cdn-v3",
                   headers={"Authorization": f"Key {ENV['FAL_KEY'].strip()}"},
                   json={"content_type": "image/jpeg", "file_name": path.name}, timeout=60)
    r.raise_for_status()
    d = r.json()
    httpx.put(d["upload_url"], content=path.read_bytes(), headers={"Content-Type": "image/jpeg"},
              timeout=120).raise_for_status()
    return d["file_url"]


stage, only = sys.argv[1], set(sys.argv[2:])
if stage == "chars":
    tasks = {}
    for name, desc in SPEC["characters"].items():
        if only and name not in only:
            continue
        prompt = (f"Character reference portrait. {desc}. Waist-up, facing the camera, neutral expression, "
                  f"plain light-grey studio wall, soft even daylight. Photoreal, 35mm film still.")
        tasks[name] = (submit("nano-banana-2", {"prompt": prompt, "size": "3:4", "resolution": "2K"}),
                       P / "refs" / f"{name}.jpg")
    collect(tasks)
elif stage == "shots":
    urls = {n: upload(P / "refs" / f"{n}.jpg") for n in SPEC["characters"]}
    tasks = {}
    for shot in SPEC["shots"]:
        if shot["kind"] == "title" or (only and shot["id"] not in only):
            continue
        refs = [urls[n] for n in shot["who"]]
        who = "; ".join(f"image {i + 1} is {SPEC['characters'][n].split(',')[0]}" for i, n in enumerate(shot["who"]))
        if shot.get("screen"):
            refs.append(upload(P / shot["screen"]))
            who += ("; " if who else "") + f"image {len(refs)} is the exact picture to show on the phone screen"
        lead = f"Use the reference images exactly for faces, hair, clothing and jewellery ({who}). " if refs else ""
        prompt = f"{lead}{shot['image']} {shot.get('style', SPEC['style'])} Wide 16:9 cinematic frame, no text, no captions."
        inp = {"prompt": prompt, "size": "16:9", "resolution": "2K"}
        model = "nano-banana-2"
        if refs:
            inp["image_urls"] = refs
            model = "nano-banana-2-edit"
        tasks[shot["id"]] = (submit(model, inp), P / "frames" / f"{shot['id']}.jpg")
    collect(tasks)
