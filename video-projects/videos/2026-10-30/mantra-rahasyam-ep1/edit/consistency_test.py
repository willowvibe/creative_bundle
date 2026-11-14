"""Character consistency check: does Qwen Image 3 (PoYo) keep a picked face across different shots?

usage: consistency_test.py <character> <ref option> <shot id> [<shot id> ...]
Each shot is made from its shots.json visual prompt, the series bible prompt block, and the picked reference image.
Output: edit/consistency/<character>_<shot>.png and a side-by-side sheet with the reference first.
"""

import json
import subprocess
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from refs import LOOK, bible_prompt, collect, submit  # noqa: E402  (refs.py checks the review gates on import)

P = Path(__file__).resolve().parents[1]
ENV = dict(
    line.split("=", 1) for line in Path("/mnt/data2/git_repos/creative_repos/video-editor/.env").read_text().splitlines()
    if "=" in line and not line.startswith("#")
)
OUT = P / "edit" / "consistency"


def upload(path: Path) -> str:
    """Host a local reference on fal storage so PoYo can fetch it."""
    r = httpx.post("https://rest.alpha.fal.ai/storage/upload/initiate?storage_type=fal-cdn-v3",
                   headers={"Authorization": f"Key {ENV['FAL_KEY'].strip().strip(chr(39) + chr(34))}"},
                   json={"content_type": "image/png", "file_name": path.name}, timeout=60)
    r.raise_for_status()
    d = r.json()
    httpx.put(d["upload_url"], content=path.read_bytes(), headers={"Content-Type": "image/png"},
              timeout=120).raise_for_status()
    return d["file_url"]


def main() -> None:
    who, opt, shots = sys.argv[1], sys.argv[2], sys.argv[3:]
    spec = json.loads((P / "shots.json").read_text())
    visuals = {v["id"]: v for v in spec["visuals"]}
    ref = P / "refs" / f"{who}_{opt}.png"
    url = upload(ref)
    OUT.mkdir(parents=True, exist_ok=True)
    name = who.title()
    tasks = {}
    for sid in shots:
        out = OUT / f"{who}_{sid}.png"
        if out.exists():
            continue
        prompt = (f"Image 1 is {name}. Keep exactly {name}'s face from image 1: same eyes, eyebrows, nose, lips, "
                  f"jawline, hairline and skin tone. {visuals[sid]['image']} {name}: {bible_prompt(who)} "
                  f"{spec['style']} {LOOK} Wide 16:9 cinematic frame.")
        tasks[out] = submit("qwen-image-3", {"prompt": prompt, "image_urls": [url], "size": "16:9", "resolution": "2K",
                                             "seed": 7, "output_format": "png",
                                             "negative_prompt": "different person, changed face, dark, gloomy, text, "
                                                                "watermark, deformed hands, extra fingers"})
        print("submitted", out.name, flush=True)
    collect(tasks)
    made = [OUT / f"{who}_{s}.png" for s in shots if (OUT / f"{who}_{s}.png").exists()]
    inputs = [str(ref)] + [str(m) for m in made]
    filt = "".join(f"[{i}]scale=-1:480[s{i}];" for i in range(len(inputs)))
    filt += "".join(f"[s{i}]" for i in range(len(inputs))) + f"hstack={len(inputs)}"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *sum((["-i", f] for f in inputs), []), "-filter_complex", filt,
                    str(OUT / f"{who}_sheet.jpg")], check=True)
    print(OUT / f"{who}_sheet.jpg")


if __name__ == "__main__":
    main()
