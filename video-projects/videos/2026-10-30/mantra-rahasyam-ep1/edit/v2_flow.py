"""Drama v2, images stage: Flow Nano Banana Pro scenes for the `flow-nb` stills of v2/visuals.json (2026-11-01).

usage: v2_flow.py [id ...]      # print the flow_generate `scenes` JSON for those ids (default: every flow-nb still
                                # without a file in v2/frames), and save it to v2/flow_batch.json
       v2_flow.py --collect P   # copy finished stills from ~/flow-mcp-out/P/scene-NN.jpeg to v2/frames/<id>.jpg

Flow reads its references directly, so its prompt is shorter than the PoYo one: the expression first and last, the
shot, each person's wardrobe and a short identity, the scene mood. Up to 3 references: face crop + portrait for one
person, one face crop each for two or three.
"""

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frames import face_crop  # noqa: E402
from v2_prompts import EXPR, IDENTITY, SCENE, V, wardrobe  # noqa: E402

P = Path(__file__).resolve().parents[1]
FRAMES = P / "v2" / "frames"
BATCH = P / "v2" / ("flow_retake.json" if "--retake" in sys.argv else "flow_batch.json")
LOOK = "Photoreal cinematic film still, 35mm, natural skin texture, bright clean light. No text, no watermark."
AVOID = "Avoid: looking into the lens, a different face, text, extra fingers, dark or gloomy light."


def short_id(who: str) -> str:
    return ", ".join(IDENTITY[who].split(", ")[:3])


def scene(v: dict, picks: dict) -> dict:
    people = [w for w in v["who"] if w in picks or w == "shiva"]
    refs: list[str] = []
    for who in people:
        if who == "shiva":
            refs += [str(P / "refs/shiva_default.png"), str(P / "refs/shiva_full.png")][: 3 - len(refs)]
            continue
        ref = P / "refs" / f"{who}_{picks[who]}.png"
        refs += [str(face_crop(ref))] + ([str(ref)] if len(people) == 1 else [])
    refs = refs[:3]
    if v["refs"] and not people:  # the Shiva picture inserts
        refs = [str(P / "refs/shiva_default.png")]
    names = " and ".join(IDENTITY[w].split(",")[0] for w in v["who"] if w in IDENTITY)
    if not v["who"]:  # an insert or establishing shot: no people at all
        pic = " The framed picture shows Lord Shiva as in the reference image." if refs else ""
        empty = " No people in the frame." if refs else " No people and no faces anywhere in the frame."
        prompt = (f"{v['framing']}: {v['action']}.{empty}{pic} Scene: "
                  f"{SCENE[v['place'] or v['scene']][1]} Photoreal cinematic film still, 35mm, bright clean light. "
                  f"No text, no letters, no watermark.")
        return {"type": "image", "model": "Nano Banana Pro", "aspect_ratio": "16:9", "download_quality": "upscaled",
                "max_credits": 0, "reference_images": refs, "prompt": " ".join(prompt.split())}
    expr = EXPR[v["expr"]] if v["expr"] else ""
    lead = "Reference images show the faces of " + names + "; keep the faces, not the references' expression or " \
           "clothes. " if refs else ""
    extra = " Nobody looks into the camera lens."
    if v["kind"] in ("speak", "ots"):
        extra += " Three-quarter view, both eyes visible, mouth closed at rest, one face only in frame."
    dress = " ".join(f"{short_id(w)}, {wardrobe(w, v)}." for w in v["who"] if w in IDENTITY and wardrobe(w, v))
    mood = SCENE[v["place"] or v["scene"]][1]
    first, again = (f"{names}: {expr}. ", f" Again: {expr}.") if expr else ("", "")
    prompt = (f"{lead}{first}{v['framing']}: {v['action']}.{extra} {dress} Scene: {mood} {LOOK}{again} {AVOID}"
              + (" No smile, no grin." if "no smile" in expr else ""))
    return {"type": "image", "model": "Nano Banana Pro", "aspect_ratio": "16:9", "download_quality": "upscaled",
            "max_credits": 0, "reference_images": refs, "prompt": " ".join(prompt.split())}


def main() -> None:
    if "--collect" in sys.argv:
        proj = Path.home() / "flow-mcp-out" / sys.argv[sys.argv.index("--collect") + 1]
        ids = json.loads(BATCH.read_text())["ids"]
        for i, vid in enumerate(ids, 1):
            src = proj / f"scene-{i:02d}.jpeg"
            if src.exists() and not (FRAMES / f"{vid}.jpg").exists():
                shutil.copy(src, FRAMES / f"{vid}.jpg")
                print("ok", vid)
            elif not src.exists():
                print("missing", vid)
        return
    picks = json.loads((P / "shots.json").read_text())["picks"]
    rows = {v["id"]: v for v in json.loads((P / "v2" / "visuals.json").read_text())["visuals"]}
    want = [a for a in sys.argv[1:] if not a.startswith("--")] or [
        r for r, x in rows.items() if x["engine"] == "flow-nb" and not list(FRAMES.glob(f"{r}.*"))]
    src = {v["id"]: v for v in V}
    scenes = [scene(src[i], picks) for i in want]
    BATCH.write_text(json.dumps({"ids": want, "scenes": scenes}, indent=1))
    print(json.dumps(scenes))


if __name__ == "__main__":
    main()
