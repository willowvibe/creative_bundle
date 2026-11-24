"""Thumbnail concepts on PoYo Nano Banana Pro edit, with the cartoon avatar as the identity reference.

usage: python3 gen_thumbs_poyo.py [NAME ...]   (default: all concepts)
Budget approved 2026-11-24: up to $0.36 (3 concepts at $0.09 + one retake). Ledger: thumb/_runs.json; the script
refuses to submit past the cap. Google Flow (free) was tried first and failed on a UI change.
"""
import json
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

P = Path(__file__).resolve().parent
LEDGER = P / "_runs.json"
CAP_USD = 0.36
USD_PER_CREDIT = 0.005   # 18 credits = $0.09 per 2K image, measured 2026-11-24
MODEL = "nano-banana-pro-edit"
AVATAR = P.parents[1] / "talking-avatar-pilot/avatar/sprites/base.png"
ENV = dict(line.split("=", 1) for line in
           Path("/mnt/data2/git_repos/creative_repos/video-editor/.env").read_text().splitlines()
           if "=" in line and not line.startswith("#"))
KEY = ENV["POYO_API_KEY"].strip().strip("'\"")
FAL = ENV["FAL_KEY"].strip().strip("'\"")
poyo = httpx.Client(base_url="https://api.poyo.ai", headers={"Authorization": f"Bearer {KEY}"}, timeout=180)

MAN = ("the exact cartoon man from the reference image (same face, same wavy black hair, same short beard, same red "
       "and black plaid shirt over a white t-shirt, same clean semi-realistic cartoon style)")
TAIL = (" High contrast, bold, readable at small phone size. Keep the bottom-right corner free of text and faces "
        "(a duration badge goes there). Spell all text exactly as quoted, nothing else written.")
CONCEPTS = {
    "thumb_p1": (f"YouTube thumbnail, 16:9. On the right half: {MAN}, chest up, excited smile, eyebrows raised, "
                 "pointing with one hand toward the left. On the left half: the word \"PYTHON\" in bold white, under it "
                 "a huge glowing \"3.15\" in Python yellow (#FFD43B) with a thick dark outline, and under that a red "
                 "rounded label with bold white text \"6 NEW TOOLS\". Background: deep navy to Python-blue gradient, "
                 "soft yellow glow behind the man, faint code lines." + TAIL),
    "thumb_p2": (f"YouTube thumbnail, 16:9. On the right: {MAN}, chest up, amazed open-mouth expression, holding a "
                 "laptop turned toward the viewer; its dark code-editor screen shows one line \"lazy import json\" in "
                 "purple monospace. On the left, big stacked bold text \"PYTHON 3.15\" (white and yellow) and below it "
                 "\"IS OUT!\" in bright yellow, thick dark outlines. Background: dark blue tech gradient with blue and "
                 "yellow glow." + TAIL),
    "thumb_p3": (f"YouTube thumbnail, 16:9. On the right: {MAN}, chest up, confident grin, thumbs up. On the left: a "
                 "stylized speedometer with the needle in the red zone, dial in Python blue and yellow. Big bold text "
                 "top left \"PYTHON 3.15\" in yellow with dark outline, and under the gauge bold white text "
                 "\"4x FASTER STARTUP\". Background: dark navy with yellow speed streaks." + TAIL),
}


def spent() -> float:
    runs = json.loads(LEDGER.read_text()) if LEDGER.exists() else {}
    return sum(r.get("credits", 18) for r in runs.values()) * USD_PER_CREDIT


def record(name: str, data: dict) -> None:
    runs = json.loads(LEDGER.read_text()) if LEDGER.exists() else {}
    runs[name] = {**runs.get(name, {}), **data}
    LEDGER.write_text(json.dumps(runs, indent=1))


def upload_avatar() -> str:
    im = Image.open(AVATAR).convert("RGBA")
    im = im.crop(im.getbbox())
    flat = Image.new("RGB", im.size, (235, 235, 235))
    flat.paste(im, (0, 0), im)
    flat.thumbnail((1536, 1536))
    small = P / "_up_avatar.jpg"
    flat.save(small, quality=92)
    r = httpx.post("https://rest.alpha.fal.ai/storage/upload/initiate?storage_type=fal-cdn-v3",
                   headers={"Authorization": f"Key {FAL}"},
                   json={"content_type": "image/jpeg", "file_name": small.name}, timeout=60)
    r.raise_for_status()
    d = r.json()
    httpx.put(d["upload_url"], content=small.read_bytes(), headers={"Content-Type": "image/jpeg"},
              timeout=180).raise_for_status()
    return d["file_url"]


def submit(name: str, prompt: str, url: str) -> str:
    if spent() + 18 * USD_PER_CREDIT > CAP_USD + 1e-9:
        raise SystemExit(f"budget cap ${CAP_USD:.2f} reached (spent ${spent():.2f})")
    r = poyo.post("/api/generate/submit", json={"model": MODEL, "input": {
        "prompt": prompt, "image_urls": [url], "size": "16:9", "resolution": "2K", "output_format": "png"}})
    r.raise_for_status()
    d = r.json()
    if d.get("code") not in (None, 200):
        raise RuntimeError(d)
    tid = d.get("data", d)["task_id"]
    record(name, {"task_id": tid, "model": MODEL, "credits": 18})
    print("submitted", name, tid, flush=True)
    return tid


def collect(tasks: dict[str, str], limit: int = 900) -> None:
    t0 = time.time()
    while tasks and time.time() - t0 < limit:
        time.sleep(8)
        for name, tid in list(tasks.items()):
            s = poyo.get(f"/api/generate/status/{tid}").json()
            s = s.get("data", s)
            if s.get("status") == "finished":
                url = next(f["file_url"] for f in s["files"] if "file_url" in f)
                (P / f"{name}.png").write_bytes(httpx.get(url, timeout=300, follow_redirects=True).content)
                record(name, {"status": "finished", "credits": s.get("credits_amount", 18)})
                print("ok", name, "credits", s.get("credits_amount"), flush=True)
                del tasks[name]
            elif s.get("status") == "failed":
                record(name, {"status": "failed", "error": s.get("error_message")})
                print("FAILED", name, s.get("error_message"), flush=True)
                del tasks[name]
    if tasks:
        print("TIMEOUT (task ids kept in ledger)", list(tasks))


def main() -> None:
    names = sys.argv[1:] or list(CONCEPTS)
    url = upload_avatar()
    tasks = {n: submit(n, CONCEPTS[n], url) for n in names}
    collect(tasks)
    print(f"spent ${spent():.2f} of ${CAP_USD:.2f}")


if __name__ == "__main__":
    main()
