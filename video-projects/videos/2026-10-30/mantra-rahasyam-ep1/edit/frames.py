"""Images stage, pass B: the scene frames on PoYo, from the user's picked character faces.

usage: frames.py --dry-run [visual_id ...]   # print each shot's model, prompt and references, spend nothing
       frames.py [visual_id ...]             # generate frames/<visual>.png (existing frames are kept)
       frames.py --pro visual_id ...         # pinned redo on Nano Banana Pro (edit)
       frames.py --qwen visual_id ...        # force Qwen Image 3
Models (user rule, 2026-10-31, after the bake-off): Kling O3 by default; Qwen Image 3 for blood or dark beats (the
only model that allowed them; DARK below or "dark": true on a visual); Nano Banana Pro for pinned redos.
Picks come from shots.json "picks" ({"abhinav": "a", ...}). Each character gets two references, a face crop
(identity) and the full portrait (hair, build). Qwen takes 3 references in all, so in a shared Qwen frame each
character gets one. Kling uses its `elements` character input for one person, plain references for two or more
(elements failed for two people on PoYo). Lord Shiva gets his two series images. Each shot's prompt names
the wardrobe and pose for that scene and tells the model not to copy the reference's clothes or pose (the
consistency test showed both leak).
"""

import json
import os
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from consistency_test import upload  # noqa: E402
from refs import LOOK, bible_prompt, collect, submit  # noqa: E402  (refs.py checks the review gates on import)

P = Path(__file__).resolve().parents[1]
SPEC = json.loads((P / "shots.json").read_text())
MAIN = ("abhinav", "lakshmi", "raghava", "shiva")
TASKS = P / "frames" / "_tasks.json"
SOUL = {12}  # scenes where Abhinav appears as a soul
DARK = {"v09"}  # blood or dark beats: Qwen Image 3
DIVINE_CAST = {"shiva", "yama1", "yama2"}
PLAIN_REFS = {"v14", "v16"}  # elements copies the reference's smile; plain refs let a grave expression win
# Grief beats: the models default to a pleasant face, so the emotion is stated physically and last (2026-10-31).
MOOD = {
    "v14": "Nurse Swathi is receiving a terrible call: her face is grave and sympathetic, brows drawn together, eyes "
           "worried, mouth closed with the corners turned down, absolutely no smile, not cheerful.",
    "v15": "Lakshmi has just heard that her son is badly hurt: her face crumples with fear and grief, brows drawn "
           "together, eyes wet with tears about to spill, mouth trembling, no smile.",
    "v16": "Nurse Swathi speaks gravely into the phone, apologetic and deeply worried: brow furrowed, mouth turned "
           "down, eyes sad, no smile.",
    "v17": "Lakshmi cries out in horror: her face is contorted with shock and grief, mouth open in a cry, tears "
           "running down her cheeks, no smile.",
}

# What each character wears in each scene of Episode 1 (series bibles, wardrobe tables).
def wardrobe(who: str, v: dict) -> str:
    sc = v["scene"]
    if who == "abhinav":
        if sc in SOUL or v["id"] == "v35":
            return "in soul form: plain white kurta, softly glowing and slightly translucent, no injuries"
        if sc >= 13 or v["id"] in ("v48", "v49"):
            return ("wearing a pale-blue hospital gown, a white bandage around his head on the right side, an IV line "
                    "taped to his left hand, lying weakly in a hospital bed")
        if v["id"] in ("v06", "v08"):
            return ("wearing a light-blue slim-fit formal shirt with sleeves folded, dark-grey trousers, a black "
                    "open-face helmet on his head, riding a red motorbike")
        return ("wearing a light-blue slim-fit formal shirt with sleeves folded to the forearm, dark-grey trousers, "
                "a black wristwatch on his left wrist")
    if who == "lakshmi":
        return "wearing her maroon cotton saree with a thin gold border, bindi, bangles and mangalsutram"
    if who == "raghava":
        return "wearing a cream half-sleeve cotton shirt, vibhuti stripes on his forehead, rudraksha mala"
    if who == "doctor":
        if v["id"] in ("v28", "v44"):
            return "in blue surgical scrubs with a surgical cap and a surgical mask over his nose and mouth, glasses"
        if v["id"] == "v45":
            return "in blue scrubs with a white coat, a surgical cap pushed back, mask pulled down under his chin, glasses"
        return "wearing a white coat over blue scrubs, a stethoscope around his neck, glasses"
    if who == "nurse1":
        return "wearing a light-blue nurse uniform, hair tied back, an ID badge"
    if who == "nurse2":
        if v["id"] in ("v28", "v44"):
            return "in a light-blue nurse uniform with a surgical cap and a surgical mask over her nose and mouth"
        return "wearing a light-blue nurse uniform with a surgical cap"
    if who == "grandfather":
        return "wearing a white dhoti and angavastram, vibhuti on his forehead, rudraksha mala"
    if who == "passerby":
        return "wearing a khaki auto-driver shirt with a towel on his shoulder"
    if who == "shiva":
        return ("dressed exactly as in images of him: tiger-skin wrap with an orange-red sash, cobra around the neck, "
                "rudraksha malas, crescent moon in his matted locks, holding his trishul with the damaru")
    if who in ("yama1", "yama2"):
        return "in black-and-gold armour, " + ("holding a rope noose" if who == "yama1" else "holding a mace")
    return ""


def describe(who: str) -> str:
    if who in MAIN:
        return bible_prompt(who).split(". Avoid:")[0]
    return SPEC["characters"][who]


def face_crop(ref: Path) -> Path:
    """Upper-middle of a waist-up 3:4 portrait, where the face sits: an identity-only reference."""
    out = ref.parent / "_faces" / ref.name  # outside refs/*.png, so the review gate and board skip it
    if not out.exists():
        out.parent.mkdir(exist_ok=True)
        im = Image.open(ref)
        w, h = im.size
        im.crop((int(0.22 * w), int(0.06 * h), int(0.78 * w), int(0.48 * h))).save(out)
    return out


def engine_for(v: dict, pro: bool = False, qwen: bool = False) -> str:
    if pro:
        return "nano"
    if qwen or v["id"] in DARK or v.get("dark"):
        return "qwen"
    return "kling"


def is_divine(v: dict) -> bool:
    return bool(DIVINE_CAST & set(v["who"])) or v["scene"] in SOUL or v["id"] in ("v35", "v54")


def refs_for(who: str, solo: bool) -> list[Path]:
    """Two references, or one when sharing a Qwen frame (Qwen takes at most 3 in all)."""
    if who == "shiva":
        both = [P / "refs" / "shiva_default.png", P / "refs" / "shiva_full.png"]
        return both if solo else both[:1]
    ref = P / "refs" / f"{who}_{SPEC['picks'][who]}.png"
    return [face_crop(ref), ref] if solo else [face_crop(ref)]


def prompt_for(v: dict, engine: str = "qwen") -> tuple[str, list[Path]]:
    refs, lead = [], []
    mood = MOOD.get(v["id"], "")  # grief beats: the reference's own expression must not be copied
    face = ("same facial features (eyes, eyebrows, nose, jawline, hairline, skin tone), but not the expression"
            if mood else "exactly this face (eyes, eyebrows, nose, lips, jawline, hairline, skin tone)")
    for who in v["who"]:
        mine = refs_for(who, solo=len(v["who"]) == 1 or engine != "qwen")
        name = "Lord Shiva" if who == "shiva" else describe(who).split(",")[0].split(" (")[0]
        idx = [len(refs) + i + 1 for i in range(len(mine))]
        refs += mine
        which = f"Images {idx[0]} and {idx[1]} are" if len(idx) == 2 else f"Image {idx[0]} is"
        lead.append(f"{which} {name}: keep {face}. In this shot {name} is {wardrobe(who, v)}.")
    mortals = [w for w in v["who"] if w != "shiva"]
    rules = ("For the people, use the reference images only for faces and identity; do not copy their clothing, pose, "
             "expression, smile, background or framing. " if mortals else "")
    if "shiva" in v["who"]:
        rules += "Lord Shiva must match his reference images exactly: face, hair, costume and ornaments. "
    if len(v["who"]) > 1:
        rules += f"Exactly {len(v['who'])} named people from the references; any others are background extras. "
    people = " ".join(f"{describe(w)}." for w in v["who"])
    # Everyday shots get the everyday style only; the divine wording leaked glows into ordinary shots (bake-off).
    style = SPEC.get("style_divine", SPEC["style"]) if is_divine(v) else SPEC["style"]
    prompt = (f"{mood} {' '.join(lead)} {rules}{v['image']} {people} {mood} {style} {LOOK} "
              f"Wide 16:9 cinematic frame.")
    return " ".join(prompt.split()), refs


AVOID = ("a different person, a changed face, a copied reference pose, dark or gloomy light, horror, text, watermark, "
         "deformed hands, extra fingers, extra people in the foreground")


def kling_prompt(prompt: str) -> str:
    """'Images 1 and 2 are Abhinav: ...' -> '@Element1 is Abhinav: ...' for Kling's elements input."""
    import re
    return re.sub(r"Images? \d+(?: and \d+)? (?:are|is)", "@Element1 is", prompt, count=1)


def request(engine: str, v: dict, prompt: str, urls: list[str]) -> tuple[str, dict]:
    """The PoYo model and input for one frame."""
    if engine == "qwen":
        inp = {"prompt": prompt, "size": "16:9", "resolution": "2K", "seed": 7, "output_format": "png",
               "negative_prompt": AVOID}
        if urls:
            inp["image_urls"] = urls
        return "qwen-image-3", inp
    if engine == "nano":  # no seed or negative prompt; the edit model needs the references
        inp = {"prompt": f"{prompt} Avoid: {AVOID}.", "size": "16:9", "resolution": "2K", "output_format": "png"}
        if urls:
            inp["image_urls"] = urls
        return ("nano-banana-pro-edit" if urls else "nano-banana-pro"), inp
    avoid = AVOID + (", smiling, happy expression, grin" if v["id"] in MOOD else "")
    inp = {"prompt": f"{prompt} Avoid: {avoid}.", "size": "16:9", "resolution": "2K", "n": 1, "output_format": "png"}
    if not urls:
        return "kling-o3-image", inp
    if len(v["who"]) == 1 and v["id"] not in PLAIN_REFS:  # one person: Kling's elements (face crop + portrait)
        inp["prompt"] = kling_prompt(inp["prompt"])
        inp["elements"] = [{"frontal_image_url": urls[0], **({"reference_image_urls": urls[1:]} if len(urls) > 1 else {})}]
        inp["image_urls"] = urls[-1:]
    else:  # two or more people: plain references (elements failed for two people on PoYo, 2026-10-31)
        inp["image_urls"] = urls
    return "kling-o3-image-edit", inp


def main() -> None:
    args = sys.argv[1:]
    dry = "--dry-run" in args
    pro = "--pro" in args
    force_qwen = "--qwen" in args
    only = {a for a in args if not a.startswith("--")}
    if pro and not only:
        raise SystemExit("--pro redoes pinned shots only: name them")
    if dry and os.environ.get("PREVIEW_PICKS"):  # e.g. PREVIEW_PICKS='{"abhinav": "a"}': preview only, never saved
        SPEC["picks"] = {**json.loads(os.environ["PREVIEW_PICKS"]), **SPEC.get("picks", {})}
    missing = [w for v in SPEC["visuals"] for w in v["who"] if w != "shiva" and w not in SPEC.get("picks", {})]
    if missing and not dry:
        raise SystemExit(f"no face pick yet for: {', '.join(sorted(set(missing)))} (shots.json 'picks')")
    (P / "frames").mkdir(exist_ok=True)
    cap_file = P / "review" / "captions.json"
    captions = json.loads(cap_file.read_text())
    urls: dict[Path, str] = {}
    # Every accepted task is saved at once (frames/_tasks.json), so a stopped run resumes instead of paying twice.
    saved = json.loads(TASKS.read_text()) if TASKS.exists() else {}
    tasks = {P / rel: tid for rel, tid in saved.items() if not (P / rel).exists()}
    for v in SPEC["visuals"]:
        if v["kind"] == "provided" or (only and v["id"] not in only):
            continue
        out = P / "frames" / f"{v['id']}.png"
        lines = ", ".join(v["lines"]) or "no dialogue"
        captions[str(out.relative_to(P))] = (f"{v['id']} · scene {v['scene']} · {v['kind']} ({v.get('engine', '')}) · "
                                            f"{lines}. {v['image']}")
        if dry:
            if missing:
                print(f"(picks missing for {sorted(set(missing))}; showing prompts only)")
                missing = []
            if v["who"] and any(w != "shiva" and w not in SPEC.get("picks", {}) for w in v["who"]):
                print(f"{v['id']}: {v['who']} (refs once picked)")
                continue
            engine = engine_for(v, pro, force_qwen)
            prompt, refs = prompt_for(v, engine)
            print(f"\n{v['id']} [{engine}] refs={[r.name for r in refs]}\n  {prompt[:600]}")
            continue
        if out.exists() or out in tasks:
            continue
        engine = engine_for(v, pro, force_qwen)
        prompt, refs = prompt_for(v, engine)
        for r in refs:
            urls.setdefault(r, upload(r))
        model, inp = request(engine, v, prompt, [urls[r] for r in refs])
        tasks[out] = submit(model, inp)
        TASKS.write_text(json.dumps({str(k.relative_to(P)): t for k, t in tasks.items()}, indent=1) + "\n")
        print("submitted", out.name, flush=True)
    if not dry:
        cap_file.write_text(json.dumps(captions, ensure_ascii=False, indent=1) + "\n")
        collect(tasks)


if __name__ == "__main__":
    main()
