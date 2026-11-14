"""One-shot comparison: the same still + the same line on Kling Avatar 2.0 Pro and OmniHuman 2.0.

usage: avatar_test.py [visual_id] [line_id]      (defaults: v15, c05b_line)
Both models take exactly one image and one driving audio file, so this is a like-for-like test.
Kling Avatar Pro bills 14 credits per request (2-60 s audio); OmniHuman bills 21 credits per second
of output and needs at least 4 s of audio, so a shorter line is padded with silence.
Output: edit/avatar_test/<visual>_<model>_<voice>.mp4 and a labelled side-by-side sheet.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

P = Path(__file__).resolve().parents[1]
OUT = P / "edit" / "avatar_test"
ENV = dict(
    line.split("=", 1) for line in Path("/mnt/data2/git_repos/creative_repos/video-editor/.env").read_text().splitlines()
    if "=" in line and not line.startswith("#")
)
KEY = ENV["POYO_API_KEY"].strip().strip("'\"")
poyo = httpx.Client(base_url="https://api.poyo.ai", headers={"Authorization": f"Bearer {KEY}"}, timeout=180)


def upload(path: Path, content_type: str) -> str:
    """Host a local file on fal storage (the same host the frames pass uses) so PoYo can fetch it."""
    r = httpx.post("https://rest.alpha.fal.ai/storage/upload/initiate?storage_type=fal-cdn-v3",
                   headers={"Authorization": f"Key {ENV['FAL_KEY'].strip().strip(chr(39) + chr(34))}"},
                   json={"content_type": content_type, "file_name": path.name}, timeout=60)
    r.raise_for_status()
    d = r.json()
    httpx.put(d["upload_url"], content=path.read_bytes(), headers={"Content-Type": content_type},
              timeout=180).raise_for_status()
    return d["file_url"]


def submit(model: str, inp: dict) -> str:
    for attempt in range(6):
        r = poyo.post("/api/generate/submit", json={"model": model, "input": inp})
        if r.status_code == 429:
            time.sleep(10 * (attempt + 1))
            continue
        r.raise_for_status()
        d = r.json()
        if d.get("code") not in (None, 200):
            raise RuntimeError(d)
        print("submitted", model, d.get("data", d)["task_id"], flush=True)
        return d.get("data", d)["task_id"]
    raise RuntimeError("rate-limited")


def collect(tasks: dict[Path, str], limit: int = 1800) -> None:
    t0 = time.time()
    while tasks and time.time() - t0 < limit:
        time.sleep(10)
        for out, tid in list(tasks.items()):
            s = poyo.get(f"/api/generate/status/{tid}").json()
            s = s.get("data", s)
            if s.get("status") == "finished":
                url = next(f["file_url"] for f in s["files"] if "file_url" in f)
                out.write_bytes(httpx.get(url, timeout=300, follow_redirects=True).content)
                print("ok", out.name, flush=True)
                del tasks[out]
            elif s.get("status") == "failed":
                print("FAILED", out.name, s.get("error_message"), flush=True)
                del tasks[out]
    if tasks:
        print("TIMEOUT", [p.name for p in tasks])


def main() -> None:
    vis = sys.argv[1] if len(sys.argv) > 1 else "v15"
    line = sys.argv[2] if len(sys.argv) > 2 else "c05b_line"
    only = sys.argv[3] if len(sys.argv) > 3 else None  # "omnihuman" or "kling" to retry one side only
    OUT.mkdir(parents=True, exist_ok=True)
    image = P / "frames" / f"{vis}.png"
    wav = next((P / "audio" / "lines").glob(f"{line}_*.wav"))
    # OmniHuman needs >= 4 s; pad with silence so both models get byte-identical audio.
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                "csv=p=0", str(wav)], capture_output=True, text=True).stdout)
    padded = OUT / f"{line}_padded.wav"
    if dur < 4.0:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-af",
                        f"apad=whole_dur={dur + 0.5:.2f}", str(padded)], check=True)
    else:
        padded = wav
    img_url, aud_url = upload(image, "image/png"), upload(padded, "audio/wav")
    inp = {"image_urls": [img_url], "audio_url": aud_url,
           "prompt": "Natural talking performance, calm head movement, steady eye contact."}
    tasks = {}
    if only in (None, "kling"):
        tasks[OUT / f"{vis}_kling_avatar_pro.mp4"] = submit("kling-avatar-2.0/pro", inp)
    if only in (None, "omnihuman"):
        tasks[OUT / f"{vis}_omnihuman_2.mp4"] = submit("omnihuman-2.0", inp)
    (OUT / "tasks.json").write_text(json.dumps({str(k.name): v for k, v in tasks.items()}, indent=1) + "\n")
    collect(tasks)


if __name__ == "__main__":
    main()
