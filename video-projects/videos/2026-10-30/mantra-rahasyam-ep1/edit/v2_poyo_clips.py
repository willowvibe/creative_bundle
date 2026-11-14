"""Drama v2, videos stage on PoYo: OmniHuman 2.0 lip-syncs and Veo 3.1 Lite motion clips (2026-11-02).

usage: v2_poyo_clips.py [--only s26,s32] [--kind lipsync|motion] [--dry-run]

The user dropped RunPod for the PoYo + Flow route on 2026-11-02 (pods were too slow to come up). Same shot list,
stills, audio slices and prompts as edit/v2_clips.py; only the engines change:
  * a speaking shot -> omnihuman-2.0 with its own audio slice, padded with silence to the model's 4 s minimum
    (21 credits per output second; assembly trims the clip back to the slot);
  * a motion shot -> veo3.1-lite-official, 720p, silent, 16:9, the shortest of 4/6/8 s that covers its slot
    (3.6 credits per second).
Hero shots stay on Flow and camera-move shots stay local. Every task id is written to v2/clips/_poyo_runs.json at
submit, so a stopped run re-collects instead of paying twice; the credits PoYo reports per task are recorded there.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P / "edit"))
from avatar_test import poyo, submit, upload  # noqa: E402  fal-hosted inputs, rate-limit-aware submit
from v2_clips import CLIPS, VISUALS, EDIT, audio_slice, kind, prompts, still  # noqa: E402

LEDGER = CLIPS / "_poyo_runs.json"
OMNI, VEO, KLING = "omnihuman-2.0", "veo3.1-lite-official", "kling-o3/standard"
MIN_AUDIO = 4.0
#: Shots with the little girl: PoYo's Veo refused s11 and Flow never finished processing either upload (2026-11-02),
#: so both go to Kling O3 (silent, 10 credits/s, 3-15 s) with wording that keeps her safe at the roadside.
ON_KLING = {"s11", "s12"}


def pkind(v: dict) -> str | None:
    """kind() plus the hero shot s12, which comes back to PoYo as a motion clip."""
    return "motion" if v["id"] in ON_KLING else kind(v)


def out_for(v: dict) -> Path:
    return CLIPS / pkind(v) / f"{v['id']}.mp4"


def dur(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(path)], capture_output=True, text=True, check=True).stdout)


def padded(v: dict) -> Path:
    """The shot's audio slice, silence-padded to OmniHuman's 4 s minimum."""
    src = audio_slice(v)
    if dur(src) >= MIN_AUDIO:
        return src
    out = src.with_name(f"{v['id']}_pad.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", f"apad=whole_dur={MIN_AUDIO}", str(out)],
                   check=True)
    return out


def veo_seconds(slot: float) -> int:
    return 4 if slot <= 4 else 6 if slot <= 6 else 8


def mime(path: Path) -> str:
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".wav": "audio/wav"}[path.suffix]


#: Motion notes that read fine on a shot list but get over-acted by a video model: said exactly instead.
MOTION_FIX = {
    "s45": ("the doctor presses the paddles to the patient's bare chest; the patient's chest jumps slightly once and "
            "he stays lying flat on the table, eyes closed; the nurse watches the monitor"),
    "s13": "dust drifts in the sunlight and settles slowly; the fallen motorbike and the helmet stay still",
    "s16": ("the onlookers lean in slightly and murmur; the kneeling man checks his breathing; Abhinav stays lying "
            "still on the road, eyes closed"),
    "s18": "the stretcher rolls quickly towards the camera; the injured young man stays lying still on it, eyes closed",
    "s40": ("Lakshmi gently lifts the spoon to Abhinav's mouth and both smile softly; slow, tender, dreamlike "
            "movement in warm morning light"),
    # first takes walked away with their backs to camera (s64) and turned the white flash into a dark scene (s78)
    "s64": ("the two Yama Dhoothas slowly bow their heads and lower their weapons, still facing forward towards the "
            "camera; they do not turn around and do not walk away"),
    "s78": ("the blinding white-gold light around Lord Shiva's silhouette flares brighter for a moment and then fades "
            "back to soft white; the whole frame stays luminous and bright, never dark"),
    "s11": ("a little girl in a yellow frock happily skips along the edge of the street after her bouncing red ball; "
            "bright daylight, people and auto-rickshaws in the background"),
    "s12": ("the young man on the red motorbike brakes hard and steers to one side, the bike leaning; the little girl "
            "stands safely at the roadside holding her ball; traffic in the background"),
}


def job(v: dict, slots: dict) -> tuple[str, dict, float]:
    """(model, input, estimated credits) for one shot."""
    v = {**v, "motion": MOTION_FIX.get(v["id"], v["motion"])}
    prompt, negative = prompts(v)
    img = still(v)
    if kind(v) == "lipsync":
        wav = padded(v)
        # OmniHuman takes no negative prompt: the no-smile instruction lives in the positive one
        return OMNI, {"image_urls": [upload(img, mime(img))], "audio_url": upload(wav, mime(wav)),
                      "prompt": prompt}, 21 * dur(wav)
    if v["id"] in ON_KLING:
        secs = max(3, round(slots[v["id"]]))
        return KLING, {"prompt": f"{prompt} Avoid: {negative}.", "image_urls": [upload(img, mime(img))],
                       "duration": secs, "multi_shots": False, "sound": False}, 10 * secs
    secs = veo_seconds(slots[v["id"]])
    return VEO, {"prompt": f"{prompt} Avoid: {negative}.", "image_urls": [upload(img, mime(img))],
                 "duration": secs, "resolution": "720p", "aspect_ratio": "16:9", "sound": False}, 3.6 * secs


def collect(ledger: dict, limit: int = 2400) -> None:
    t0 = time.time()
    while time.time() - t0 < limit:
        open_ = {sid: e for sid, e in ledger.items() if e.get("status") == "submitted"}
        if not open_:
            return
        time.sleep(10)
        for sid, e in open_.items():
            s = poyo.get(f"/api/generate/status/{e['task_id']}").json()
            s = s.get("data", s)
            if s.get("status") == "finished":
                url = next(f["file_url"] for f in s["files"] if "file_url" in f)
                out = P / e["file"]
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(httpx.get(url, timeout=300, follow_redirects=True).content)
                e |= {"status": "done", "credits": s.get("credits_amount") or s.get("credits")}
                print(f"ok {sid} {e['model']} credits={e['credits']}", flush=True)
            elif s.get("status") == "failed":
                e |= {"status": "failed", "error": str(s.get("error_message"))[:300]}
                print(f"FAILED {sid}: {e['error']}", flush=True)
            LEDGER.write_text(json.dumps(ledger, indent=1) + "\n")
    print("TIMEOUT:", [sid for sid, e in ledger.items() if e.get("status") == "submitted"], flush=True)


def main() -> None:
    args = sys.argv[1:]
    opt = lambda name: args[args.index(name) + 1] if name in args else ""
    only, want, dry = set(opt("--only").split(",")) - {""}, opt("--kind"), "--dry-run" in args
    slots = {s["id"]: s["seconds"] for s in EDIT["shots"]}
    ledger = json.loads(LEDGER.read_text()) if LEDGER.exists() else {}
    todo = [v for v in VISUALS if pkind(v) and (not only or v["id"] in only) and (not want or pkind(v) == want)
            and not out_for(v).exists() and ledger.get(v["id"], {}).get("status") != "submitted"]
    if dry:
        for v in todo:
            model = OMNI if kind(v) == "lipsync" else VEO
            est = 21 * max(MIN_AUDIO, dur(audio_slice(v))) if model == OMNI else 3.6 * veo_seconds(slots[v["id"]])
            print(f"{v['id']} {model} ~{est:.0f} cr | {prompts(v)[0][:110]}")
        return
    for v in todo:
        model, inp, est = job(v, slots)
        tid = submit(model, inp)
        ledger[v["id"]] = {"model": model, "task_id": tid, "status": "submitted", "estimate": round(est, 1),
                           "file": str(out_for(v).relative_to(P)), "prompt": inp["prompt"]}
        LEDGER.write_text(json.dumps(ledger, indent=1) + "\n")
    collect(ledger)


if __name__ == "__main__":
    main()
