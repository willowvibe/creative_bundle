"""Image model bake-off: the PoYo models cheaper than Qwen Image 3, on three shots of this episode.

usage: bakeoff.py            -> edit/bakeoff/<model>_<shot>.png, then edit/bakeoff/sheet.jpg
Shots: v05 (one person, Abhinav), v18 (two people, Raghava + Lakshmi), v12 (no characters, the ambulance).
Prompts and references are exactly what frames.py sends to Qwen; Kling gets its `elements` character input instead
of plain references. The Qwen frames already made are the baseline row.
"""

import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from consistency_test import upload  # noqa: E402
from frames import SPEC, prompt_for  # noqa: E402
from refs import collect, submit  # noqa: E402

P = Path(__file__).resolve().parents[1]
OUT = P / "edit" / "bakeoff"
SHOTS = ["v05", "v18", "v12"]
BASELINE = {"v05": P / "frames/v05.png", "v18": P / "frames/v18.png", "v12": P / "edit/_superseded/v12_qwen.png"}
MODELS = {  # label: (model with references, model for prompt only or None, credits at 2K)
    "flux-3-klein": ("flux-3-klein-edit", "flux-3-klein", 2.8),
    "kling-o3": ("kling-o3-image-edit", "kling-o3-image", 3.5),
    "kling-o1": ("kling-o1-image-edit", None, 3.5),
    "seedream-5-flash": ("seedream-5.0-flash-edit", "seedream-5.0-flash", 4),
    "wan-2.7": ("wan-2.7-image", "wan-2.7-image", 4.2),
}


def kling_prompt(prompt: str) -> str:
    """'Images 1 and 2 are Abhinav: ...' -> '@Element1 is Abhinav: ...' (one element per person)."""
    n = 0

    def swap(m: re.Match) -> str:
        nonlocal n
        n += 1
        return f"@Element{n} is"

    return re.sub(r"Images? \d+(?: and \d+)? (?:are|is)", swap, prompt)


def inputs(label: str, refs: list[Path], prompt: str, people: int) -> tuple[str, dict]:
    with_refs, text_only, _ = MODELS[label]
    model = with_refs if refs else text_only
    urls = [upload(r) for r in refs]
    if label.startswith("kling"):
        inp = {"prompt": kling_prompt(prompt) if refs else prompt, "size": "16:9", "resolution": "2K", "n": 1,
               "output_format": "png"}
        if refs and people > 1:  # Kling's `elements` failed on PoYo for two people (2026-10-31): plain references
            inp["prompt"] = prompt
            inp["image_urls"] = urls
            return model, inp
        if refs:
            per = len(refs) // people  # references per person, in prompt order
            inp["elements"] = [{"frontal_image_url": urls[i * per],
                                **({"reference_image_urls": urls[i * per + 1:(i + 1) * per]} if per > 1 else {})}
                               for i in range(people)]  # an empty reference list makes PoYo fail
            inp["image_urls"] = [urls[(i + 1) * per - 1] for i in range(people)]
        return model, inp
    if label == "wan-2.7":
        inp = {"prompt": prompt, "size": {"width": 1920, "height": 1080}, "n": 1, "seed": 7}
    else:
        inp = {"prompt": prompt, "size": "16:9", "resolution": "2K", "output_format": "png"}
        if label == "flux-3-klein":
            inp["n"] = 1
    if urls:
        inp["image_urls"] = urls
    return model, inp


def sheet() -> Path:
    rows = [("qwen-image-3 (baseline)", BASELINE)] + [
        (lab, {s: OUT / f"{lab}_{s}.png" for s in SHOTS}) for lab in MODELS]
    W, H, L = 512, 288, 210
    img = Image.new("RGB", (L + W * len(SHOTS), (H + 6) * len(rows) + 26), "white")
    d = ImageDraw.Draw(img)
    for c, s in enumerate(SHOTS):
        d.text((L + c * W + 6, 6), {"v05": "v05: one person", "v18": "v18: two people", "v12": "v12: no characters"}[s],
               fill="black")
    for r, (lab, files) in enumerate(rows):
        y = 26 + r * (H + 6)
        price = "4.8 cr" if lab.startswith("qwen") else f"{MODELS[lab][2]} cr"
        d.text((6, y + H // 2 - 8), f"{lab}\n{price}", fill="black")
        for c, s in enumerate(SHOTS):
            f = files[s]
            if f.exists():
                img.paste(Image.open(f).convert("RGB").resize((W, H)), (L + c * W, y))
            else:
                d.text((L + c * W + 20, y + H // 2), "not available / failed", fill="gray")
    out = OUT / "sheet.jpg"
    img.save(out, quality=88)
    return out


def main() -> None:
    OUT.mkdir(exist_ok=True)
    visuals = {v["id"]: v for v in SPEC["visuals"]}
    tasks = {}
    for lab in MODELS:
        for s in SHOTS:
            out = OUT / f"{lab}_{s}.png"
            prompt, refs = prompt_for(visuals[s])
            if out.exists() or (not refs and MODELS[lab][1] is None):
                continue
            model, inp = inputs(lab, refs, prompt, len(visuals[s]["who"]))
            try:
                tasks[out] = submit(model, inp)
                print("submitted", out.name, flush=True)
            except Exception as e:
                print("REFUSED", out.name, str(e)[:240], flush=True)
    collect(tasks)
    print(sheet())


if __name__ == "__main__":
    main()
