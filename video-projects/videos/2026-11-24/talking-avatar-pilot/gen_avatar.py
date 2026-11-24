"""Cartoon avatar sprites for the talking-avatar pilot, on PoYo Nano Banana Pro edit ($0.04/image at 2K).

usage:
  gen_avatar.py base [N]            N neutral cartoon base candidates from the photo + style refs (default 2)
  gen_avatar.py variants BASE.png   mouth shapes + eyes-closed, each edited from the chosen base

Budget approved 2026-11-24: up to $1 total. Every task is recorded in avatar/_runs.json with its credits, and
the script refuses to submit once the ledger reaches the cap.
Refs (gitignored): refs/photo.jpg = identity (Harish-photo/crop_IMG_6667.jpg), refs/style.jpg = style
(pics/crop.jpg, ToonMe). Uploads go to fal storage, the same host earlier PoYo runs used.
"""

import json
import sys
import time
from pathlib import Path

import httpx
from PIL import Image, ImageOps

P = Path(__file__).resolve().parent
OUT = P / "avatar" / "raw"
LEDGER = P / "avatar" / "_runs.json"
CAP_USD = 1.36                  # $1.00 approved for the first set + $0.36 approved for batch 2 (2026-11-24)
USD_PER_CREDIT = 0.005          # measured 18 credits = $0.09 per 2K image on 2026-11-24 (pricing.yaml says 8)
MODEL = "nano-banana-pro-edit"
ENV = dict(
    line.split("=", 1) for line in
    Path("/mnt/data2/git_repos/creative_repos/video-editor/.env").read_text().splitlines()
    if "=" in line and not line.startswith("#")
)
KEY = ENV["POYO_API_KEY"].strip().strip("'\"")
FAL = ENV["FAL_KEY"].strip().strip("'\"")
poyo = httpx.Client(base_url="https://api.poyo.ai", headers={"Authorization": f"Bearer {KEY}"}, timeout=180)

BG = "a perfectly flat, uniform chroma-key green background (#00B140) with no shadows, gradients or texture"

BASE_PROMPT = (
    "Turn the man in the first image into a cartoon portrait drawn in the painted cartoon style of the second "
    "image: smooth painted skin shading, clean bold outlines, slightly enlarged expressive eyes, glossy hair "
    "highlights. Keep his identity exactly as in the first image: face shape, hairstyle, moustache and short "
    "beard, skin tone, and his red-and-black plaid shirt over a white t-shirt. Pose: head and shoulders, facing "
    "the camera straight on, head upright and centred, eyes open looking into the camera, relaxed friendly "
    "neutral expression, mouth fully closed with the lips gently together, no teeth showing, no hands in frame. "
    f"Square framing with a little space above the hair, shoulders reaching the bottom edge. Put him on {BG}. "
    "Do not copy the pose, the pointing hand, the open mouth or the t-shirt text of the second image."
)

KEEP = (
    "Keep absolutely everything else pixel-identical to the source image: the same face, eyes, eyebrows, hair, "
    "beard, skin, clothing, lighting, framing, art style and green background. Change only the mouth"
)

# Preston Blair / Rhubarb mouth letters, written as what the mouth physically does.
MOUTHS = {
    "A": "lips pressed firmly together as when saying M, B or P",
    "B": "lips slightly parted with the teeth just visible and close together, as when saying S, T or K",
    "C": "mouth open in a medium wide shape showing the upper teeth, as when saying the E in 'bed'",
    "D": "mouth wide open with the jaw dropped, upper teeth and tongue visible, as when saying the A in 'father'",
    "E": "lips rounded into an open O shape, as when saying the O in 'go'",
    "F": "lips pushed forward into a small tight round pucker, as when saying OO or W",
    "G": "upper teeth resting lightly on the lower lip, as when saying F or V",
    "H": "mouth open with the tongue tip raised behind the upper teeth, as when saying L",
}


def spent() -> float:
    runs = json.loads(LEDGER.read_text()) if LEDGER.exists() else {}
    return sum(r.get("credits", 8) for r in runs.values()) * USD_PER_CREDIT


def record(name: str, data: dict) -> None:
    runs = json.loads(LEDGER.read_text()) if LEDGER.exists() else {}
    runs[name] = {**runs.get(name, {}), **data}
    LEDGER.write_text(json.dumps(runs, indent=1))


def upload(path: Path) -> str:
    small = OUT.parent / f"_up_{path.stem}.jpg"
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    im.thumbnail((1536, 1536))
    im.save(small, quality=92)
    r = httpx.post("https://rest.alpha.fal.ai/storage/upload/initiate?storage_type=fal-cdn-v3",
                   headers={"Authorization": f"Key {FAL}"},
                   json={"content_type": "image/jpeg", "file_name": small.name}, timeout=60)
    r.raise_for_status()
    d = r.json()
    httpx.put(d["upload_url"], content=small.read_bytes(), headers={"Content-Type": "image/jpeg"},
              timeout=180).raise_for_status()
    return d["file_url"]


def submit(name: str, prompt: str, urls: list[str]) -> str:
    if spent() + 18 * USD_PER_CREDIT > CAP_USD + 1e-9:
        raise SystemExit(f"budget cap ${CAP_USD:.2f} reached (spent ${spent():.2f})")
    r = poyo.post("/api/generate/submit", json={"model": MODEL, "input": {
        "prompt": prompt, "image_urls": urls, "size": "1:1", "resolution": "2K", "output_format": "png"}})
    r.raise_for_status()
    d = r.json()
    if d.get("code") not in (None, 200):
        raise RuntimeError(d)
    tid = d.get("data", d)["task_id"]
    record(name, {"task_id": tid, "model": MODEL, "credits": 18})   # provisional until the status reports it
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
                (OUT / f"{name}.png").write_bytes(httpx.get(url, timeout=300, follow_redirects=True).content)
                record(name, {"status": "finished", "credits": s.get("credits_amount", 8)})
                print("ok", name, "credits", s.get("credits_amount"), flush=True)
                del tasks[name]
            elif s.get("status") == "failed":
                record(name, {"status": "failed", "error": s.get("error_message")})
                print("FAILED", name, s.get("error_message"), flush=True)
                del tasks[name]
    if tasks:
        print("TIMEOUT (task ids kept in ledger)", list(tasks))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    mode = sys.argv[1]
    tasks: dict[str, str] = {}
    if mode == "base":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 2
        urls = [upload(P / "refs" / "photo.jpg"), upload(P / "refs" / "style.jpg")]
        start = len(list(OUT.glob("base_*.png")))
        for i in range(start, start + n):
            tasks[f"base_{i}"] = submit(f"base_{i}", BASE_PROMPT, urls)
    elif mode == "variants":
        base = Path(sys.argv[2])
        only = sys.argv[3:] or [*MOUTHS, "blink"]
        url = upload(base)
        for k in only:
            if k == "blink":
                prompt = (KEEP.replace("Change only the mouth", "Change only the eyes") +
                          ": both eyes gently closed in a natural blink, eyelids relaxed. The mouth stays closed "
                          "exactly as it is.")
            else:
                prompt = f"{KEEP}: {MOUTHS[k]}. Keep the head position and the eyes exactly as they are."
            tasks[f"mouth_{k}" if k != "blink" else "blink"] = submit(
                f"mouth_{k}" if k != "blink" else "blink", prompt, [url])
    elif mode == "batch2":
        # Retakes of the two weak mouths with more physical wording, plus two expression patches.
        url = upload(Path(sys.argv[2]))
        keep_face = KEEP.replace("Change only the mouth", "Change only the {part}")
        jobs = {
            "mouth_G2": f"{KEEP}: the upper front teeth press gently down onto the lower lip and the lower lip "
                        "tucks slightly under them, lips otherwise closed and relaxed, as when saying F or V. "
                        "Keep the head position and the eyes exactly as they are.",
            "mouth_H2": f"{KEEP}: mouth slightly open with the tip of the tongue touching the back of the upper "
                        "front teeth, only the tongue tip visible, centred, the tongue not sticking out of the "
                        "mouth, as when saying L. Keep the head position and the eyes exactly as they are.",
            "brows": keep_face.format(part="eyebrows") + ": both eyebrows raised noticeably in interested "
                     "surprise, the forehead lifting slightly. The eyes stay open exactly as they are and the "
                     "mouth stays closed exactly as it is.",
            "glance": keep_face.format(part="eyes") + ": both eyes look toward the left side of the image, the "
                      "irises and pupils shifted into the left corners of the eyes as if reading something "
                      "beside him. Eyelids, eyebrows, head and mouth stay exactly as they are.",
        }
        for name in sys.argv[3:] or list(jobs):
            tasks[name] = submit(name, jobs[name], [url])
    collect(tasks)
    print(f"spent so far ${spent():.2f} of ${CAP_USD:.2f}")


if __name__ == "__main__":
    main()
