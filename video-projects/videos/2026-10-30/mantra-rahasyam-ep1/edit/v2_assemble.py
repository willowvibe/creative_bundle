"""Drama v2, the cut: every clip dropped into its animatic slot, score and SFX re-cued, 1080p25 master + 720p review.

usage: v2_assemble.py segments [shot ...] | beds | audio | final | web | all

The timeline is the animatic's (edit/v2_animatic.py, v2/animatic/edit_list.json) so every cut lands where the user
reviewed it. Per shot:
  * speaking shot  -> its OmniHuman clip at real time from the cut (the voice starts on the cut), last frame held;
  * motion / hero  -> its Veo, Kling or Flow clip, slowed up to SLOW_CAP to fill the slot, then the last frame held;
    USE trims a clip to its usable head first (s78's bright flash);
  * shock shot     -> its living-still clip under a quick push-in (the serial zoom);
  * place / insert / end card -> the still under the animatic's camera move;
  * s80            -> the user's Flow clip, real time, its own voice.
Everything is scaled to 1920x1080 and conformed to 25 fps; Flow clips lose the sparkle mark (DELOGO). The sound is the
animatic's voice track (voices + chant + s80's own voice) with the score ducked under it and the SFX bed beside it;
score.py and sfx.py render the beds from their own cue tables, re-timed here to the v2 cut.
"""

import json
import subprocess
import sys
from pathlib import Path

P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P / "edit"))
import v2_animatic as A  # noqa: E402  timeline, stills, camera moves

V2 = P / "v2"
OUT = V2 / "cut"
CLIPS = V2 / "clips"
FPS, W, H = 25, 1920, 1080
A.W, A.H = W, H                                    # the animatic's camera moves, at master size
ROWS, TOTAL = A.ROWS, A.TOTAL
SLOW_CAP = 1.5
USE = {"s78": 2.5}                                 # seconds of the clip worth using (s78 turns dark after its flash)
DELOGO = "delogo=x=1698:y=857:w=84:h=86"           # Flow's sparkle on 1080p video, measured on s70's test clip
FLOW = {"s56", "s57", "s62", "s66"}                # clips generated on Flow (s80 is the user's own clip: no mark)
SCORE_DB = -17.0                                   # same bed level as the v1 cut (assemble.py)


def probe(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(path)], capture_output=True, text=True, check=True).stdout)


def run(*cmd: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *cmd], check=True)


#: A shot that shows another shot's clip (user, 2026-11-02: the curd insert at 0:12 reuses the flashback's motion).
CLIP_OF = {"s04": "s40"}
#: A shot cut from several clips: (clip in v2/clips/motion, from, seconds). User, 2026-11-02: the episode never showed
#: Abhinav saving the girl, so s12 is now the save (Kling O3; Veo refused it) then the lorry charging at the camera
#: (Veo, no people), the impact left to the crash sound and the cut to the aftermath. Same 4.0 s slot.
PARTS = {"s12": [("s12_save", 0.0, 2.9), ("s12_truck", 2.8, 1.1)]}


def clip_of(v: dict) -> Path | None:
    if v["kind"] == "provided":
        return A.PROVIDED
    sid = CLIP_OF.get(v["id"], v["id"])
    for stage in ("lipsync", "motion", "hero"):
        c = CLIPS / stage / f"{sid}.mp4"
        if c.exists():
            return c
    return None


FIT = f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},setsar=1"


def segment(row: tuple[dict, float, float]) -> Path:
    v, a, b = row
    slot, n = b - a, int(round((b - a) * FPS))
    out = OUT / "segs" / f"{v['id']}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    enc = ["-frames:v", str(n), "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p", "-an",
           str(out)]
    if v["id"] in PARTS:
        parts = PARTS[v["id"]]
        ins, chain = [], []
        for i, (name, frm, secs) in enumerate(parts):
            ins += ["-i", str(CLIPS / "motion" / f"{name}.mp4")]
            chain.append(f"[{i}:v]trim={frm:.3f}:{frm + secs:.3f},setpts=PTS-STARTPTS,fps={FPS},{FIT}[p{i}]")
        chain.append("".join(f"[p{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=0,"
                     f"tpad=stop_mode=clone:stop_duration={slot:.3f}[v]")
        run(*ins, "-filter_complex", ";".join(chain), "-map", "[v]", *enc)
        return out
    clip = clip_of(v)
    if clip is None:                               # place, insert, end card: the still under its camera move
        run("-loop", "1", "-i", str(A.still(v)), "-vf",
            f"scale={2 * W}:{2 * H}:force_original_aspect_ratio=increase,crop={2 * W}:{2 * H},"
            f"{A.motion(v, n)},setsar=1", *enc)
        return out
    pre = [DELOGO] if v["id"] in FLOW else []
    use = min(probe(clip), USE.get(v["id"], 1e9))
    # real time where a mouth is on screen; motion is stretched (up to the cap) rather than frozen early
    slow = 1.0 if v["kind"] in ("speak", "provided") else min(max(slot / use, 1.0), SLOW_CAP)
    zoom = (f",zoompan=z='if(lt(on,10),1+0.14*on/10,1.14+0.02*(on-10)/{n})':x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={FPS}") if v["kind"] == "shock" else ""
    vf = ",".join([*pre, f"trim=0:{use:.3f}", "setpts=PTS-STARTPTS", f"setpts={slow:.4f}*PTS", f"fps={FPS}", FIT]) \
        + zoom + f",tpad=stop_mode=clone:stop_duration={slot:.3f}"
    run("-i", str(clip), "-vf", vf, *enc)
    return out


# ---------------------------------------------------------------- beds, re-timed to the v2 cut

# SFX: each v1 cue re-anchored on the v2 shot or line carrying the same event (v2 times from the animatic plan).
SFX_V2 = {
    "home_birds": (0.0, 33.0), "plate_set": (8.27, None), "scooter_go": (32.6, None), "street_bed": (33.6, 24.0),
    # the collision lands on the lorry filling the frame at the end of s12 (40.8 + 2.9 s save + ~0.95 s into the lorry).
    # It sat at 41.1 while s12 was the near-miss take (user asked for it earlier there); the save shot moved the
    # visible impact to the end of the shot, so the sound followed it.
    "crash": (44.6, None), "crash_tail": (45.0, None), "crowd_walla": (48.3, 10.5), "siren_far": (58.8, 9.7),
    "phone_ring": (68.2, None), "temple_bell": (90.06, None), "hosp_atmo": (98.2, 40.0),
    "monitor": (98.2, 43.72), "door": (117.9, None), "prayer_bell": (118.44, None), "flatline": (141.92, None),
    "defib": (144.3, None),  # user, 2026-11-02: 2 s earlier than the s45 cut
    "memory": (151.0, None), "divine_light": (180.3, None), "dootha": (203.18, None),
    "chains": (207.2, None), "shiva_hit": (215.64, None), "shiva_shine": (217.2, None), "release": (225.7, None),
    "flat_wait": (229.1, 3.2), "beep_return": (232.3, 42.7), "shiva_voice": (275.03, 10.2),
}
MONITOR_RAMP = (134.8, 141.92, 0.0, 6.0)           # the beeps build into the flatline, as in v1
SCORE_V2 = {"a_house": 0.0, "b_mantra": 167.4, "c_return": 227.9}
# The times above are on the cut before the s19 ring pause; everything after the call moves by the pause. The ring
# itself becomes a ringback heard alone for that second, stopping as the nurse speaks.
SHIFT_AFTER, SHIFT = 68.5, sum(A.PAUSE_BEFORE.values())
RING = (68.2, 1.0)


def shifted(t: float) -> float:
    return t + SHIFT if t > SHIFT_AFTER else t


def beds() -> tuple[Path, Path]:
    import score
    import sfx
    missing = set(SFX_V2) ^ {c["id"] for c in sfx.CUES}
    if missing:
        raise SystemExit(f"SFX_V2 and sfx.CUES disagree on: {sorted(missing)}")
    cues = []
    for c in sfx.CUES:
        t, d = SFX_V2[c["id"]]
        c = {**c, "t": shifted(t), "dur": d or c["dur"]}
        if c["id"] == "phone_ring":
            c |= {"t": RING[0], "dur": RING[1], "fo": 0.15}
        if c.get("ramp"):
            a, b, d0, d1 = MONITOR_RAMP
            c["ramp"] = (shifted(a), shifted(b), d0, d1)
        cues.append(c)
    sfx.CUES, sfx.BED = cues, OUT / "sfx_bed.wav"
    score.CUES = [{**c, "start": shifted(SCORE_V2[c["id"]])} for c in score.CUES]
    score.TOTAL, score.BED = TOTAL, OUT / "score_bed.wav"
    OUT.mkdir(parents=True, exist_ok=True)
    return score.place(), sfx.place()


def audio() -> Path:
    """Voices on top; the score ducked under them; the SFX at their own drawn levels (no sidechain: it would pump)."""
    voices, score_bed, sfx_bed = A.OUT / "voices.wav", OUT / "score_bed.wav", OUT / "sfx_bed.wav"
    out = OUT / "mix.wav"
    run("-i", str(voices), "-i", str(score_bed), "-i", str(sfx_bed), "-filter_complex",
        "[0:a]aformat=channel_layouts=stereo,asplit=2[voice][key];"
        f"[1:a]volume={10 ** (SCORE_DB / 20):.3f}[score];"
        "[score][key]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350:makeup=1[ducked];"
        f"[voice][ducked][2:a]amix=inputs=3:normalize=0,loudnorm=I=-14:TP=-2.0:LRA=11,atrim=0:{TOTAL}[out]",
        "-map", "[out]", "-ar", "48000", str(out))
    return out


def final() -> Path:
    lst = OUT / "segs" / "list.txt"
    lst.write_text("".join(f"file '{OUT / 'segs' / (v['id'] + '.mp4')}'\n" for v, _, _ in ROWS))
    out = P / "mantra_rahasyam_ep1_v2.mp4"
    run("-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(OUT / "mix.wav"),
        "-vf", f"fade=t=in:d=1,fade=t=out:st={TOTAL - 1.5:.3f}:d=1.5", "-c:v", "libx264", "-crf", "18",
        "-preset", "slow", "-pix_fmt", "yuv420p", "-af", "volume=-0.3dB", "-c:a", "aac", "-b:a", "256k",
        "-t", f"{TOTAL:.3f}", "-movflags", "+faststart", str(out))
    return out


def web() -> Path:
    """720p review copy; its sound comes from mix.wav, never re-encoded from the master's AAC (assemble.py)."""
    out = P / "mantra_rahasyam_ep1_v2.web.mp4"
    run("-i", str(P / "mantra_rahasyam_ep1_v2.mp4"), "-i", str(OUT / "mix.wav"), "-map", "0:v", "-map", "1:a",
        "-vf", "scale=1280:720:flags=lanczos", "-c:v", "libx264", "-crf", "23", "-preset", "slow",
        "-pix_fmt", "yuv420p", "-af", "volume=-0.3dB", "-c:a", "aac", "-b:a", "256k", "-t", f"{TOTAL:.3f}",
        "-movflags", "+faststart", str(out))
    return out


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    only = set(sys.argv[2:])
    if what in ("segments", "all"):
        from concurrent.futures import ThreadPoolExecutor
        rows = [r for r in ROWS if not only or r[0]["id"] in only]
        with ThreadPoolExecutor(6) as pool:
            for o in pool.map(segment, rows):
                print("  ", o.name, flush=True)
    if what in ("beds", "all"):
        print("beds", beds(), flush=True)
    if what in ("audio", "all"):
        print("mix", audio(), flush=True)
    if what in ("final", "all"):
        print("final", final(), flush=True)
    if what in ("web", "all"):
        print("web", web(), flush=True)
