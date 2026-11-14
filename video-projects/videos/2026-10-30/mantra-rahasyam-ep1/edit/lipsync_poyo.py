"""PoYo lip-sync for the shots InfiniteTalk cannot act: six anguished lines where a teeth-bearing mouth reads as a grin.

usage: lipsync_poyo.py [--dry-run] [--model omnihuman|kling] [visual ...]   (default: ANGUISHED, OmniHuman)

Why this exists (2026-10-31): InfiniteTalk on RunPod is cheap and fine for ordinary talking, but on grief it opens the
mouth with both rows of teeth and the corners pulled back, which reads as a strangled smile however the still and the
prompt are set. OmniHuman 2.0 driven from the same still holds the still's framing and keeps the mouth pained
(measured on v15: see edit/avatar_test/). Six shots is ~537 credits (~$2.70); the other 23 lip-sync shots keep the
RunPod route.

Clips land in clips/lipsync/<visual>.mp4, the same stage folder clips.py writes its InfiniteTalk takes to, so the
lip-sync review gate and assembly do not care which model made one.
credits_amount from the task status is recorded per shot: it, not the catalogue, is the authority on cost.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from avatar_test import poyo, submit, upload  # noqa: E402
from clips import dialogue_seconds, line_audio  # noqa: E402

CLIPS = P / "clips"
LIPSYNC = CLIPS / "lipsync"                        # the review stage "lipsync" globs this folder
LEDGER = CLIPS / "_poyo_runs.json"
PADDED = CLIPS / "_tmp" / "padded"
MODELS = {"omnihuman": "omnihuman-2.0", "kling": "kling-avatar-2.0/pro"}

# Anguished lines where the mouth must stay downturned. v17 (screaming) and v47 (crying with joy) are deliberately
# absent: a wide open mouth with teeth is correct for a scream and for joy.
ANGUISHED = {"v15", "v20", "v22", "v24", "v25", "v39"}


def pad(path: Path, minimum: float = 4.0) -> Path:
    """OmniHuman needs at least 4 s of audio; padding also sets what Kling bills, so both models get the same file."""
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(path)], capture_output=True, text=True).stdout)
    if dur >= minimum:
        return path
    PADDED.mkdir(parents=True, exist_ok=True)
    out = PADDED / f"{path.stem}_{dur + 0.5:.2f}.wav"
    if not out.exists():
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-af", f"apad=whole_dur={dur + 0.5:.2f}",
                        str(out)], check=True)
    return out


PROMPT = ("Natural talking performance from the reference still: the expression stays as it is in the image, mouth "
          "pained, no smile, calm head movement, steady framing.")


def main() -> None:
    args = sys.argv[1:]
    dry = "--dry-run" in args
    redo = "--redo" in args
    model = MODELS[args[args.index("--model") + 1]] if "--model" in args else MODELS["omnihuman"]
    ids = [a for a in args if not a.startswith("--")] or sorted(ANGUISHED)
    ids = [i for i in ids if not i.startswith(("omnihuman", "kling"))]

    ledger = json.loads(LEDGER.read_text()) if LEDGER.exists() else {}
    tasks: dict[Path, str] = {}
    audio_of: dict[Path, Path] = {}
    LIPSYNC.mkdir(parents=True, exist_ok=True)
    for vid in ids:
        frame, out = P / "frames" / f"{vid}.png", LIPSYNC / f"{vid}.mp4"
        if not frame.exists():
            raise SystemExit(f"{vid}: no frames/{vid}.png")
        spec = next(v for v in json.loads((P / "shots.json").read_text())["visuals"] if v["id"] == vid)
        wav = line_audio(spec["lines"][0])
        if wav is None:
            raise SystemExit(f"{vid}: no audio for {spec['lines']}")
        # A shot that is in the ledger *and* has a delivered clip is finished and paid for. The old guard only
        # skipped shots missing from the ledger, so re-running the script on a complete stage re-rendered all six
        # (~692 credits, ~$3.46) for clips already on disk. --redo is the only way past this.
        spent_on = (ledger.get(vid) or {}).get("credits")
        if out.exists() and spent_on and not redo:
            print(f"keep {vid}: delivered, {spent_on} credits already spent (--redo to replace)", flush=True)
            continue
        if out.exists() and vid not in ledger:
            print(f"keep {vid}: {out.name} already there", flush=True)
            continue
        if dry:
            print(f"{vid} [{model}] frame={frame.name} audio={wav.name} ({dialogue_seconds(spec):.2f}s)", flush=True)
            continue
        tmp = CLIPS / "_tmp" / f"{vid}_poyo.mp4"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        # A redo replaces the shot's entry, so keep what the replaced attempt cost: the episode's spend report is
        # built from this file and a bare overwrite would silently refund a take we actually paid for.
        prior = ledger.get(vid) or {}
        carried = [{k: prior[k] for k in ("task_id", "file", "credits") if k in prior}] if prior.get("credits") else []
        task = submit(model, {"image_urls": [upload(frame, "image/png")],
                              "audio_url": upload(pad(wav), "audio/wav"), "prompt": PROMPT})
        tasks[tmp] = task
        audio_of[tmp] = wav
        ledger[vid] = {"model": model, "task_id": task}
        if carried:
            ledger[vid]["prior"] = (prior.get("prior") or []) + carried
        LEDGER.write_text(json.dumps(ledger, indent=1) + "\n")
        print("submitted", vid, task, flush=True)

    if dry or not tasks:
        return
    # Collect to temp, then move, so a bad render never replaces a good clip.
    while tasks:
        time.sleep(8)
        for tmp, tid in list(tasks.items()):
            s = poyo.get(f"/api/generate/status/{tid}").json()
            s = s.get("data", s)
            vid = tmp.name.removesuffix("_poyo.mp4")
            if s.get("status") == "finished":
                url = next(f["file_url"] for f in s["files"] if "file_url" in f)
                tmp.write_bytes(httpx.get(url, timeout=300, follow_redirects=True).content)
                out = LIPSYNC / f"{vid}.mp4"
                tmp.replace(out)
                ledger[vid].update({"file": out.name, "credits": s.get("credits_amount")})
                del tasks[tmp]
                print(f"ok {out.name} ({out.stat().st_size // 1024} KB, {ledger[vid]['credits']} credits)", flush=True)
            elif s.get("status") == "failed":
                ledger[vid]["error"] = str(s.get("error_message"))[:300]
                del tasks[tmp]
                print(f"FAILED {vid}: {ledger[vid]['error']}", flush=True)
            LEDGER.write_text(json.dumps(ledger, indent=1) + "\n")
    spent = sum((v.get("credits") or 0) + sum(p.get("credits") or 0 for p in (v.get("prior") or []))
                for v in ledger.values())
    print(f"\n{len(ledger)} PoYo lip-syncs, {spent:.0f} credits (~${spent * 0.005:.2f})", flush=True)


if __name__ == "__main__":
    main()
