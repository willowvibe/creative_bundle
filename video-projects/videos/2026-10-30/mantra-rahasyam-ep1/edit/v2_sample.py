"""Drama v2, images stage: render a sample of v2/visuals.json stills on PoYo Kling O3 (2026-11-01).

usage: v2_sample.py --dry-run s03 s37 ...   # print model, references and prompt; spend nothing
       v2_sample.py s03 s37 ...             # render v2/frames/<id>.png (existing files are kept)

Kling O3 at 2K (3.5 credits a still). One person: Kling's `elements` input (face crop + portrait). Two or more, or
Lord Shiva: plain references (elements failed for two people on PoYo, 2026-10-31). The lead sentence ties each
reference to a name and keeps the face but not the reference's expression; the rest is the v2 prompt as written.
Task ids land in v2/frames/_tasks.json at submit, credits in v2/frames/_credits.json at the end.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from consistency_test import upload  # noqa: E402
from frames import face_crop  # noqa: E402
from refs import collect, poyo, submit  # noqa: E402

P = Path(__file__).resolve().parents[1]
OUT = P / "v2" / "frames"
TASKS = OUT / "_tasks.json"
NAME = {"abhinav": "Abhinav", "lakshmi": "Lakshmi", "raghava": "Raghava", "doctor": "Dr. Srinivas",
        "grandfather": "Tatayya", "nurse1": "Nurse Swathi", "nurse2": "Nurse Rani", "passerby": "the auto driver",
        "yama1": "the first Yama Dhootha", "yama2": "the second Yama Dhootha", "shiva": "Lord Shiva"}
# Expressions Kling's `elements` input overrode with the reference's pleasant face (sample 2026-11-01, as in Ep1):
# those shots send plain references instead.
from v2_prompts import EMOTIONAL as NEGATIVE  # noqa: E402
KEEP = "keep the facial features (eyes, eyebrows, nose, jawline, hairline, skin tone) but not the reference's expression"


def request(v: dict, urls: dict[str, str]) -> tuple[str, dict]:
    people = [w for w in v["who"] if w in NAME]
    solo = len(people) == 1 and people[0] != "shiva" and v["expr"] not in NEGATIVE
    if solo:
        lead = f"@Element1 is {NAME[people[0]]}: {KEEP}."
    else:
        n, parts = 0, []
        for who in people:
            k = 2 if who == "shiva" or len(people) == 1 else 1
            idx = " and ".join(f"Image {n + i + 1}" for i in range(k))
            parts.append(f"{idx} {'are' if k == 2 else 'is'} {NAME[who]}" + ("" if who == "shiva" else f": {KEEP}"))
            n += k
        lead = "; ".join(parts) + "." if parts else ""
    inp = {"prompt": f"{lead} {v['prompt']} Avoid: {v['avoid']}.".strip(), "size": "16:9", "resolution": "2K",
           "n": 1, "output_format": "png"}
    if v["engine"] == "qwen":  # blood: the only model that drew it (Ep1); no references in these shots
        return "qwen-image-3", {"prompt": f"{v['prompt']}", "negative_prompt": v["avoid"], "size": "16:9",
                                "resolution": "2K", "output_format": "png"}
    if not people:
        return "kling-o3-image", inp
    if solo:
        crop, portrait = urls[f"face:{people[0]}"], urls[people[0]]
        inp["elements"] = [{"frontal_image_url": crop, "reference_image_urls": [portrait]}]
        inp["image_urls"] = [portrait]
    else:
        inp["image_urls"] = [u for who in people for u in
                             ([urls["shiva"], urls["shiva_full"]] if who == "shiva" else
                              [urls[f"face:{who}"], urls[who]] if len(people) == 1 else [urls[f"face:{who}"]])]
    return "kling-o3-image-edit", inp


def main() -> None:
    dry = "--dry-run" in sys.argv
    ids = [a for a in sys.argv[1:] if not a.startswith("--")]
    tag = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--tag=")), "")
    rows = {v["id"]: v for v in json.loads((P / "v2" / "visuals.json").read_text())["visuals"]}
    picks = json.loads((P / "shots.json").read_text())["picks"]
    OUT.mkdir(parents=True, exist_ok=True)
    saved = json.loads(TASKS.read_text()) if TASKS.exists() else {}
    tasks = {P / rel: tid for rel, tid in saved.items() if not (P / rel).exists()}
    urls: dict[str, str] = {}
    for vid in ids:
        v, out = rows[vid], OUT / f"{vid}{tag}.png"
        if out.exists() or out in tasks:
            continue
        need = {}
        for who in v["who"]:
            if who == "shiva":
                need["shiva"], need["shiva_full"] = P / "refs/shiva_default.png", P / "refs/shiva_full.png"
            elif who in picks:
                ref = P / "refs" / f"{who}_{picks[who]}.png"
                need[who], need[f"face:{who}"] = ref, face_crop(ref)
        if dry:
            model, inp = request(v, {k: f"<{p.name}>" for k, p in need.items()})
            print(f"\n{vid} [{model}] {json.dumps({k: x for k, x in inp.items() if k != 'prompt'})}\n  {inp['prompt']}")
            continue
        for k, p in need.items():
            urls.setdefault(k, upload(p))
        model, inp = request(v, urls)
        tasks[out] = submit(model, inp)
        saved[str(out.relative_to(P))] = tasks[out]
        TASKS.write_text(json.dumps(saved, indent=1) + "\n")
        print("submitted", vid, model, flush=True)
    if dry or not tasks:
        return
    ids_by_out = dict(tasks)
    collect(tasks)
    credits_file = OUT / "_credits.json"
    credits = json.loads(credits_file.read_text()) if credits_file.exists() else {}
    for out, tid in ids_by_out.items():
        s = poyo.get(f"/api/generate/status/{tid}").json()
        credits[out.stem] = s.get("data", s).get("credits_amount")
    credits_file.write_text(json.dumps(credits, indent=1) + "\n")
    print("credits", {k: credits[k] for k in (o.stem for o in ids_by_out)})


if __name__ == "__main__":
    main()
