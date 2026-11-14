"""Images stage, pass A: character reference candidates for the user to pick from.

usage: refs.py [name ...]
Main characters (series bibles) on PoYo Nano Banana Pro: 3 options each. Lord Shiva: 4 compositions (user's note).
Minor characters (shots.json) on PoYo Qwen Image 3: 2 options each. Output: refs/<name>_<a|b|c|d>.png plus captions
in review/captions.json. Existing files are kept, so re-running only fills gaps; delete an option to redo it.
"""

import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, "/mnt/data2/git_repos/creative_repos/video-editor/scripts")
from drama_review import require  # noqa: E402

P = Path(__file__).resolve().parents[1]
require(P, "voices")  # script, dialogue and audio are approved
ENV = dict(
    line.split("=", 1) for line in Path("/mnt/data2/git_repos/creative_repos/video-editor/.env").read_text().splitlines()
    if "=" in line and not line.startswith("#")
)
SPEC = json.loads((P / "shots.json").read_text())
poyo = httpx.Client(base_url="https://api.poyo.ai",
                    headers={"Authorization": f"Bearer {ENV['POYO_API_KEY'].strip().strip(chr(39) + chr(34))}"},
                    timeout=120)

LOOK = ("Photoreal cinematic still, 35mm film, natural skin texture, soft bright daylight, warm and clean colours. "
        "No text, no watermark.")
PORTRAIT = "Character reference portrait: waist-up, facing the camera, gentle natural expression, plain warm off-white wall."
MAIN = {
    "abhinav": "wearing a light-blue slim-fit formal shirt with sleeves folded to the forearm.",
    "lakshmi": "",  # her saree is in the prompt block
    "raghava": "",
}
MINOR = ["doctor", "grandfather", "nurse1", "nurse2", "passerby", "yama1", "yama2"]
MINOR_SCENE = {
    "doctor": "in a bright modern hospital corridor",
    "grandfather": "in a warm sunlit courtyard of a traditional Telugu home, golden light",
    "nurse1": "at a bright hospital nurses' station",
    "nurse2": "in a bright clean operating theatre",
    "passerby": "beside a yellow-and-green auto-rickshaw on a sunny Hyderabad street",
    "yama1": "in a soft misty otherworldly space, pale blue and silver light, calm and dignified, not scary",
    "yama2": "in a soft misty otherworldly space, pale blue and silver light, calm and dignified, not scary",
}


def bible_prompt(name: str) -> str:
    text = (P / "../../mantra-rahasyam/assets/characters" / name / "bible.md").read_text()
    block = text.split("## Image prompt block", 1)[1]
    avoid = block.split("**Avoid:**", 1)[1].split("\n\n", 1)[0]
    prompt = " ".join(line.lstrip("> ").strip() for line in block.split("**Avoid:**")[0].splitlines()
                      if line.startswith(">"))
    return f"{prompt}. Avoid: {' '.join(avoid.split())}"


TASKS = P / "refs" / "_tasks.json"  # every accepted task id, saved at once, so a crash never loses a paid task


def submit(model: str, inp: dict) -> str:
    for attempt in range(8):
        r = poyo.post("/api/generate/submit", json={"model": model, "input": inp})
        if r.status_code == 429:  # PoYo rate limit: back off and retry the same request
            time.sleep(10 * (attempt + 1))
            continue
        r.raise_for_status()
        d = r.json()
        if d.get("code") not in (None, 200):
            raise RuntimeError(d)
        time.sleep(2)  # stay under the submit rate limit
        return d.get("data", d)["task_id"]
    raise RuntimeError("PoYo kept rate-limiting the submit")


def collect(tasks: dict[Path, str]) -> None:
    def forget(out: Path) -> None:  # a finished or failed task leaves the resume list
        del tasks[out]
        for tf in (TASKS, P / "frames" / "_tasks.json"):  # refs.py and frames.py each keep their own list
            if tf.exists():
                saved = json.loads(tf.read_text())
                saved.pop(str(out.relative_to(P)), None)
                tf.write_text(json.dumps(saved, indent=1) + "\n")

    t0 = time.time()
    while tasks and time.time() - t0 < 1200:
        time.sleep(5)
        for out, tid in list(tasks.items()):
            s = poyo.get(f"/api/generate/status/{tid}").json()
            s = s.get("data", s)
            if s.get("status") == "finished":
                url = next(f["file_url"] for f in s["files"] if "file_url" in f)
                out.write_bytes(httpx.get(url, timeout=120, follow_redirects=True).content)
                print("ok", out.name, flush=True)
                forget(out)
            elif s.get("status") == "failed":
                print("FAILED", out.name, s.get("error_message"), flush=True)
                forget(out)
    if tasks:
        print("TIMEOUT", [p.name for p in tasks])


def main() -> None:
    only = set(sys.argv[1:])
    (P / "refs").mkdir(exist_ok=True)
    cap_file = P / "review" / "captions.json"
    captions = json.loads(cap_file.read_text()) if cap_file.exists() else {}
    jobs: list[tuple[Path, str, dict, str]] = []
    for name, extra in MAIN.items():
        for opt in "abc":
            jobs.append((P / "refs" / f"{name}_{opt}.png", "nano-banana-pro",
                         {"prompt": f"{PORTRAIT} {bible_prompt(name)} {extra} {LOOK}", "size": "3:4",
                          "resolution": "2K", "output_format": "png"},
                         f"{name.title()}, option {opt.upper()} (Nano Banana Pro). Pick the face for the series."))
    # Lord Shiva: the user's own default image (refs/shiva_default.png) replaced the four options on 2026-10-31.
    for name in MINOR:
        for opt, seed in zip("ab", (11, 29)):
            desc = SPEC["characters"][name]
            jobs.append((P / "refs" / f"{name}_{opt}.png", "qwen-image-3",
                         {"prompt": f"{PORTRAIT.replace('plain warm off-white wall', MINOR_SCENE[name])} {desc}. {LOOK}",
                          "size": "3:4", "resolution": "2K", "seed": seed, "output_format": "png",
                          "negative_prompt": "dark, gloomy, horror, deformed hands, extra fingers, text, watermark"},
                         f"{desc.split(',')[0]}, option {opt.upper()} (Qwen Image 3)."))
    saved = json.loads(TASKS.read_text()) if TASKS.exists() else {}
    tasks = {P / rel: tid for rel, tid in saved.items() if not (P / rel).exists()}  # resume unfinished tasks
    for out, model, inp, caption in jobs:
        name = out.stem.rsplit("_", 1)[0]
        captions[str(out.relative_to(P))] = caption
        if (only and name not in only) or out.exists() or out in tasks:
            continue
        tasks[out] = submit(model, inp)
        TASKS.write_text(json.dumps({str(k.relative_to(P)): v for k, v in tasks.items()}, indent=1) + "\n")
        print("submitted", out.name, flush=True)
    cap_file.write_text(json.dumps(captions, ensure_ascii=False, indent=1) + "\n")
    collect(tasks)


if __name__ == "__main__":
    main()
