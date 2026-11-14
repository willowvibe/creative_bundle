"""Grief redos: change only the expression on an approved frame, on Nano Banana Pro (edit).

usage: expression_fix.py --dry-run v15 v22 v24 v25
       expression_fix.py v15 v22 v24 v25

Why an edit instead of a fresh generation (2026-10-31): the identity portraits in refs/ are smiling headshots, so
Kling O3 copied the smile into every grief frame however the prompt was worded (v15 kept its teeth, v22/v24 came
out as serene prayer smiles, v25 smiled at the Shiva picture). The framing, wardrobe, hands and light are already
approved, so this re-renders only the face and leaves everything else alone.

The expression is written physically (lips, mouth corners, teeth, brows, eyes) because "no smile" on its own did
not hold: the models read it loosely. The approved frame is archived to frames/_superseded/ before the redo
replaces it, and frames/_expression_runs.json is the resume ledger.
"""

import json
import shutil
import sys
import time
from pathlib import Path

import httpx

P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from consistency_test import upload  # noqa: E402
from refs import poyo, submit  # noqa: E402  (refs.py checks the review gates on import)

SPEC = json.loads((P / "shots.json").read_text())
MODEL = "nano-banana-pro-edit"
LEDGER = P / "frames" / "_expression_runs.json"

KEEP = ("Keep absolutely everything else identical to the source image: the same person and identity, the same "
        "pose, hands and body position, the same clothing and jewellery, the same background, the same lighting, "
        "the same framing, the same camera angle, the same 16:9 composition. Change only the facial expression.")

# One entry per visual: what the line is doing, said in terms a model can draw.
FACES = {
    "v15": ("Lakshmi has just heard over the phone that her son is badly hurt and is fighting fear. Her face "
            "crumples with grief: eyebrows drawn together and raised at the inner ends, eyelids heavy, eyes wet "
            "with tears, a tear running down her cheek, mouth closed with the lips pressed together and the "
            "corners pulled down, jaw tight, chin slightly trembling. No smile at all, no teeth visible."),
    # v20 was missed on the first pass — its line is trembling/desperate but the approved still carries a mild
    # pleasant smile, and OmniHuman turned that into a warm grin on the last second of the line (seen 2026-10-31).
    "v20": ("Raghava is trembling with dread, holding himself together in a hospital corridor. Brows drawn "
            "together and raised at the inner ends, eyes wide and wet, mouth closed with the lips pressed thin "
            "and the corners pulled down, jaw set, chin trembling. No smile at all, no teeth visible."),
    "v22": ("Lakshmi is crying and pleading with a doctor. Brows drawn together in distress, eyes brimming and "
            "red-rimmed, cheeks wet with tears, mouth open as she pleads with the corners turned down, chin "
            "trembling. No smile at all, no teeth visible."),
    "v24": ("Lakshmi is sobbing while praying. Brows crumpled in anguish, eyes squeezed shut with tears running "
            "down her cheeks, mouth open in a sob with the corners pulled down, wet face. No smile at all, no "
            "teeth visible."),
    "v25": ("Raghava is broken and pleading, looking up towards the Shiva picture. Brows drawn together and "
            "raised in despair, eyes wide and wet with tears, mouth turned down and slightly open with "
            "trembling lips. No smile at all, no teeth visible."),
}


def wait(tasks: dict[Path, str], limit: int = 1200) -> None:
    """Poll to a temp file, so nothing touches the approved frame until a real image has arrived."""
    t0 = time.time()
    while tasks and time.time() - t0 < limit:
        time.sleep(5)
        for out, tid in list(tasks.items()):
            s = poyo.get(f"/api/generate/status/{tid}").json()
            s = s.get("data", s)
            if s.get("status") == "finished":
                url = next(f["file_url"] for f in s["files"] if "file_url" in f)
                out.write_bytes(httpx.get(url, timeout=180, follow_redirects=True).content)
                del tasks[out]
            elif s.get("status") == "failed":
                print("FAILED", out.name, s.get("error_message"), flush=True)
                del tasks[out]
    if tasks:
        print("TIMEOUT", [p.name for p in tasks], flush=True)


def main() -> None:
    args = sys.argv[1:]
    dry = "--dry-run" in args
    ids = [a for a in args if not a.startswith("--")] or list(FACES)
    unknown = [i for i in ids if i not in FACES]
    if unknown:
        raise SystemExit(f"no expression note for: {', '.join(unknown)} (add it to FACES)")

    ledger = json.loads(LEDGER.read_text()) if LEDGER.exists() else {}
    tmp = P / "frames" / "_tmp"
    tasks: dict[Path, str] = {}
    for vid in ids:
        frame = P / "frames" / f"{vid}.png"
        if not frame.exists():
            raise SystemExit(f"{vid}: no {frame.relative_to(P)} to edit")
        prompt = f"{FACES[vid]} {KEEP}"
        if dry:
            print(f"\n{vid} [{MODEL}] <- {frame.name}\n  {prompt}")
            continue
        tmp.mkdir(exist_ok=True)
        tasks[tmp / f"{vid}_new.png"] = submit(
            MODEL, {"prompt": prompt, "size": "16:9", "resolution": "2K", "output_format": "png",
                    "image_urls": [upload(frame)]})
        ledger[vid] = {"model": MODEL, "task_id": tasks[tmp / f"{vid}_new.png"]}
        LEDGER.write_text(json.dumps(ledger, indent=1) + "\n")
        print("submitted", vid, ledger[vid]["task_id"], flush=True)

    if dry or not tasks:
        return
    wait(tasks)
    archive = P / "frames" / "_superseded"
    archive.mkdir(exist_ok=True)
    done = 0
    for new in sorted(tmp.glob("*_new.png")):
        vid = new.stem.removesuffix("_new")
        frame = P / "frames" / f"{vid}.png"
        if not new.exists() or new.stat().st_size == 0:
            continue
        shutil.move(str(frame), archive / f"{vid}_smile.png")  # the take the user approved, kept for comparison
        shutil.move(str(new), frame)
        ledger[vid]["file"] = frame.name
        ledger[vid]["archived"] = f"frames/_superseded/{vid}_smile.png"
        done += 1
        print(f"done {vid} ({frame.stat().st_size // 1024} KB)", flush=True)
    try:
        tmp.rmdir()
    except OSError:
        pass
    LEDGER.write_text(json.dumps(ledger, indent=1) + "\n")
    print(f"\n{done} frames re-rendered; look at them before the held shots render", flush=True)


if __name__ == "__main__":
    main()
