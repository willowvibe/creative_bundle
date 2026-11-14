"""Drama v2, dubs: Hindi and Tamil (2026-11-02) and Kannada (2026-11-04) audio tracks for YouTube multi-language audio.

usage: v2_dub.py voice <hi|ta|kn> | fit <hi|ta|kn> | mix <hi|ta|kn> | all <hi|ta|kn>

The user approved both scripts (v2/dub/translations.json) in chat. Each line is voiced with the same ElevenLabs eleven_v4
voice, pace, stability and emotion tag as its Telugu take (edit/tts.py), only the language changes; s80's line (the
Telugu comes from the user's Flow clip) is voiced in the Shiva voice; the Sanskrit chant is the same bed.
Fit: a take longer than its Telugu line is sped up to fit (atempo, at most MAX_SPEED); a shorter one keeps its pace and
ends early, so every line still starts where the picture expects it. A take that would need more than MAX_SPEED is
re-voiced faster instead (one retry), then reported.
Mix: the same placement, chant, ducked score and SFX as the Telugu cut (edit/v2_assemble.py), then the cold open's
hits and the end card's chant from the Telugu final (they carry no language), so the track is exactly the upload's
length. Output: v2/dub/<lang>/mantra_rahasyam_ep1_<lang>_audio.wav and a review MP4 with that track.
"""

import json
import subprocess
import sys
from pathlib import Path

P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P / "edit"))
import v2_animatic as A  # noqa: E402
from tts import CHOSEN, PACE, STABILITY, tts  # noqa: E402  the Telugu cast, unchanged
from v2_graphics import COLD, END, FINAL  # noqa: E402

DUB = P / "v2" / "dub"
T = json.loads((DUB / "translations.json").read_text())
SPEC = json.loads((P / "shots.json").read_text())["shots"]
MAX_SPEED = 1.25
SCORE_DB = -17.0


def speaker(lid: str) -> tuple[str, str]:
    """(who, emotion) for a line id like c05b_line."""
    base, key = lid.rsplit("_", 1)
    s = next(s for s in SPEC if s["id"] == base)
    item = s.get(key)
    return item.get("who") or s.get("speaker", ""), item.get("emotion") or s.get("emotion", "")


def dur(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(path)], capture_output=True, text=True, check=True).stdout)


def raw(lang: str, lid: str) -> Path:
    return DUB / lang / "lines" / f"{lid}.wav"


def voice(lang: str, only: set[str] | None = None, faster: dict[str, float] | None = None) -> None:
    for lid, text in T[lang].items():
        if only and lid not in only:
            continue
        out = raw(lang, lid)
        if out.exists() and not faster:
            continue
        who, emo = speaker(lid)
        pace = min(1.2, PACE[who] * (faster or {}).get(lid, 1.0))
        tts(text, CHOSEN[who], out, pace, emo, STABILITY.get(who, 0.5), language=lang)
        print(f"  {lid:10} {who:11} {dur(out):5.2f}s (te {A.DUR[lid]:5.2f}s)", flush=True)


def fitted(lang: str, lid: str) -> Path:
    return DUB / lang / "fit" / f"{lid}.wav"


def fit(lang: str) -> list[str]:
    """Speed each over-long take into its Telugu slot; return the lines that would need more than MAX_SPEED."""
    over = []
    for lid in T[lang]:
        src, out, target = raw(lang, lid), fitted(lang, lid), A.DUR[lid]
        out.parent.mkdir(parents=True, exist_ok=True)
        speed = max(1.0, dur(src) / target)
        if speed > MAX_SPEED:
            over.append(lid)
            speed = MAX_SPEED
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", f"atempo={speed:.4f}", "-ar", "48000",
                        "-ac", "2", str(out)], check=True)
    return over


def mix(lang: str) -> Path:
    out_dir = DUB / lang
    ins, parts = [], []
    starts = sorted(A.STARTS.items(), key=lambda kv: kv[1])
    for i, (lid, t) in enumerate(starts):
        src = A.CHANT_BED if lid == "chant" else fitted(lang, lid)
        ins += ["-i", str(src)]
        vol = "1.15" if lid == "chant" else "1.0"
        parts.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol},"
                     f"adelay={int(round(t * 1000))}:all=1,apad=whole_dur={A.TOTAL}[a{i}]")
    n = len(starts)
    parts.append("".join(f"[a{i}]" for i in range(n)) + f"amix=inputs={n}:normalize=0,loudnorm=I=-16:TP=-2:LRA=11,"
                 f"atrim=0:{A.TOTAL},asplit=2[voice][key]")
    cut = P / "v2" / "cut"
    ins += ["-i", str(cut / "score_bed.wav"), "-i", str(cut / "sfx_bed.wav")]
    parts.append(f"[{n}:a]volume={10 ** (SCORE_DB / 20):.3f}[score]")
    parts.append("[score][key]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350:makeup=1[ducked]")
    parts.append(f"[voice][ducked][{n + 1}:a]amix=inputs=3:normalize=0,loudnorm=I=-14:TP=-2.0:LRA=11,"
                 f"apad=pad_dur=1,atrim=0:{A.TOTAL}[main]")  # loudnorm drops its last ~60 ms (ffmpeg 8.1)
    # the cold open's hits and the end card's chant, lifted from the Telugu final: they carry no language
    total = COLD + A.TOTAL + END
    ins += ["-i", str(FINAL)]
    parts.append(f"[{n + 2}:a]atrim=0:{COLD},asetpts=PTS-STARTPTS,aresample=48000[cold]")
    parts.append(f"[{n + 2}:a]atrim={COLD + A.TOTAL}:{total},asetpts=PTS-STARTPTS,aresample=48000[end]")
    # the final's audio stream ends 24 ms before its video, so pad: the track must be exactly the upload's length
    parts.append(f"[cold][main][end]concat=n=3:v=0:a=1,volume=-0.3dB,aresample=48000:async=1:first_pts=0,apad=pad_dur=1,atrim=0:{total}[out]")
    track = out_dir / f"mantra_rahasyam_ep1_{lang}_audio.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", ";".join(parts), "-map", "[out]",
                    "-ar", "48000", "-t", f"{total:.3f}", str(track)], check=True)
    review = out_dir / f"mantra_rahasyam_ep1_{lang}.web.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(FINAL.with_suffix(".web.mp4")), "-i", str(track),
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}",
                    "-movflags", "+faststart", str(review)], check=True)
    return track


if __name__ == "__main__":
    what, lang = sys.argv[1], sys.argv[2]
    if what in ("voice", "all"):
        voice(lang)
    if what in ("fit", "all"):
        over = fit(lang)
        if over:
            # one retry: re-voice those lines faster, by how much they overran
            ratio = {lid: dur(raw(lang, lid)) / A.DUR[lid] / MAX_SPEED for lid in over}
            print("re-voicing faster:", {k: round(v, 2) for k, v in ratio.items()}, flush=True)
            voice(lang, set(over), faster=ratio)
            over = fit(lang)
        print("still over", MAX_SPEED, "x:", over or "none", flush=True)
    if what in ("mix", "all"):
        print("track", mix(lang), flush=True)
