"""The episode's score: three instrumental beds from PoYo, placed on the cut and ducked under the voice.

usage: score.py list | gen [cue ...] | place | all

Suno-backed `generate-music` (~$0.10 a track). ElevenLabs music is disabled engine-wide — ElevenLabs is the
voice. The brief is the user's: light instruments, a score that carries the story rather than competes with it,
so three cues only — the household and its dread, the mantra, the reunion — and the chant section is left alone
apart from one quiet undertrack.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

P = Path(__file__).resolve().parents[1]
OUT = P / "audio" / "music"
NOTES = OUT / "cues.json"
BED = P / "edit" / "score_bed.wav"
TOTAL = 319.48
ENV = dict(l.split("=", 1) for l in Path("/mnt/data2/git_repos/creative_repos/video-editor/.env").read_text().splitlines()
           if "=" in l and not l.startswith("#"))
KEY = ENV["POYO_API_KEY"].strip().strip("'\"")
MODEL = "generate-music"
MV = "V4_5PLUS"
poyo = httpx.Client(base_url="https://api.poyo.ai", headers={"Authorization": f"Bearer {KEY}"}, timeout=180)

CUES = [
    dict(id="a_house", start=0.0,
         prompt="Instrumental cinematic score for a Telugu family drama. Warm veena and bansuri flute over soft "
                "strings, gentle tabla pulse, a loving household morning. It turns slowly dark and uneasy as fate "
                "closes in. Restrained, spacious, no vocals."),
    dict(id="b_mantra", start=182.4,
         prompt="Instrumental devotional underscore. Tanpura drone, distant temple bells, shimmering cymbals, low "
                "sustained strings, sacred and vast, very slow. No vocals, no drum loop."),
    dict(id="c_return", start=246.5,
         prompt="Instrumental emotional finale for an Indian drama. Veena and flute melody over warm string swells, "
                "gentle tabla, tearful hopeful reunion, uplifted and tender. No vocals."),
]


def note() -> dict:
    return json.loads(NOTES.read_text()) if NOTES.exists() else {}


def submit(prompt: str) -> str:
    for attempt in range(6):
        r = poyo.post("/api/generate/submit", json={
            "model": MODEL, "input": {"prompt": prompt, "custom_mode": False, "mv": MV, "instrumental": True}})
        if r.status_code == 429:
            time.sleep(10 * (attempt + 1))
            continue
        r.raise_for_status()
        d = r.json()
        if d.get("code") not in (None, 200):
            raise RuntimeError(d)
        return d.get("data", d)["task_id"]
    raise RuntimeError("rate-limited")


def gen(only: set[str] | None = None) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    notes = note()
    pending = {}
    for cue in CUES:
        out = OUT / f"{cue['id']}.wav"
        if only and cue["id"] not in only:
            continue
        if out.exists():
            print("have", cue["id"], flush=True)
            continue
        tid = submit(cue["prompt"])
        notes[cue["id"]] = {"task_id": tid}
        pending[cue["id"]] = tid
        NOTES.write_text(json.dumps(notes, indent=1) + "\n")
        print("submitted", cue["id"], tid, flush=True)

    t0 = time.time()
    while pending:
        time.sleep(10)
        for cid, tid in list(pending.items()):
            s = poyo.get(f"/api/generate/status/{tid}").json()
            s = s.get("data", s)
            if s.get("status") == "finished":
                url = s["files"][0].get("audio_url") or s["files"][0]["file_url"]
                mp3 = OUT / f"{cid}.mp3"
                mp3.write_bytes(httpx.get(url, timeout=300, follow_redirects=True).content)
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp3), "-ar", "48000", "-ac", "2",
                                str(OUT / f"{cid}.wav")], check=True)
                mp3.unlink()
                notes[cid].update({"file": f"{cid}.wav", "credits": s.get("credits_amount")})
                del pending[cid]
                print("ok", cid, notes[cid].get("credits"), "credits", flush=True)
            elif s.get("status") == "failed":
                notes[cid]["error"] = str(s.get("error_message"))[:300]
                del pending[cid]
                print("FAILED", cid, notes[cid]["error"], flush=True)
            NOTES.write_text(json.dumps(notes, indent=1) + "\n")
        if time.time() - t0 > 1200:
            print("still pending:", list(pending), flush=True)
            return


def dur(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout)


def place() -> Path:
    """Lay the three tracks end to end with 2 s of overlap, and pull each fade-out to the section's real end so no
    track runs into the next section's mood."""
    tracks = [(c, OUT / f"{c['id']}.wav") for c in CUES]
    missing = [str(f) for _, f in tracks if not f.exists()]
    if missing:
        raise SystemExit(f"missing music: {missing} — run `score.py gen`")
    ins, parts, labels = [], [], []
    for i, (cue, f) in enumerate(tracks):
        start = cue["start"]
        nxt = CUES[i + 1]["start"] if i + 1 < len(CUES) else TOTAL
        want = min(dur(f), max(1.0, nxt - start + 2.0))
        ins += ["-i", str(f)]
        fade_out = min(4.0, want / 3)
        parts.append(f"[{i}:a]atrim=0:{want:.3f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:st=0:d={1.5 if i == 0 else 2.0},"
                     f"afade=t=out:st={max(0.0, want - fade_out):.3f}:d={fade_out:.2f},"
                     f"adelay={int(start * 1000)}:all=1[s{i}]")
        labels.append(f"[s{i}]")
    parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:dropout_transition=0,"
                 f"atrim=0:{TOTAL},afade=t=out:st={TOTAL - 2.5}:d=2.5[out]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", ";".join(parts),
                    "-map", "[out]", "-ar", "48000", "-ac", "2", str(BED)], check=True)
    return BED


def show() -> None:
    notes = note()
    for c in CUES:
        f = OUT / f"{c['id']}.wav"
        print(f"{c['start']:7.2f}  {(f'{dur(f):.1f}s' if f.exists() else '—'):>7}  {c['id']:10} "
              f"{notes.get(c['id'], {}).get('credits', '')}")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "list"
    if what == "list":
        show()
    if what in ("gen", "all"):
        gen(set(sys.argv[2:]) or None)
    if what in ("place", "all"):
        print("score", place(), flush=True)
