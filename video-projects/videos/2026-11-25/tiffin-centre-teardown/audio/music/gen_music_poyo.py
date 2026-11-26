"""Background music bed on PoYo Suno (generate-music, V4_5PLUS), instrumental.

usage: python3 gen_music_poyo.py   (approved 2026-11-25 (tiffin render): ~$0.10; ledger in _runs.json records the real credits)
Downloads every file PoYo returns (Suno usually gives two takes) as bed_<n>.mp3.
"""
import json
import time
from pathlib import Path

import httpx

P = Path(__file__).resolve().parent
ENV = dict(line.split("=", 1) for line in
           Path("/mnt/data2/git_repos/creative_repos/video-editor/.env").read_text().splitlines()
           if "=" in line and not line.startswith("#"))
KEY = ENV["POYO_API_KEY"].strip().strip("'\"")
PROMPT = ("Warm light Indian morning instrumental for a calm Telugu explainer: soft mridangam and tabla pattern, "
          "gentle veena and bansuri phrases, light tanpura drone, warm acoustic guitar, steady 90 bpm, no vocals, "
          "no big drops or crescendos, unobtrusive and consistent from start to end so it sits quietly under a voice-over.")


def main() -> None:
    c = httpx.Client(base_url="https://api.poyo.ai", headers={"Authorization": f"Bearer {KEY}"}, timeout=120)
    r = c.post("/api/generate/submit", json={"model": "generate-music", "input": {
        "prompt": PROMPT, "custom_mode": False, "mv": "V4_5PLUS", "instrumental": True}})
    r.raise_for_status()
    tid = r.json()["data"]["task_id"]
    print("task", tid, flush=True)
    for _ in range(90):
        time.sleep(8)
        s = c.get(f"/api/generate/status/{tid}").json().get("data", {})
        if s.get("status") == "finished":
            files = s.get("files") or []
            for n, f in enumerate(files, 1):
                url = f.get("audio_url") or f.get("file_url")
                (P / f"bed_{n}.mp3").write_bytes(httpx.get(url, timeout=300, follow_redirects=True).content)
            (P / "_runs.json").write_text(json.dumps({"task_id": tid, "model": "generate-music", "mv": "V4_5PLUS",
                                                      "credits": s.get("credits_amount"), "files": len(files),
                                                      "prompt": PROMPT}, indent=1))
            print("ok files", len(files), "credits", s.get("credits_amount"))
            return
        if s.get("status") == "failed":
            raise SystemExit(f"failed: {s.get('error') or s.get('error_message')}")
    raise SystemExit(f"timeout; task {tid}")


if __name__ == "__main__":
    main()
