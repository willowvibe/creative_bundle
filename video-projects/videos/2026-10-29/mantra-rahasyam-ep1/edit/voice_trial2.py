"""Blind voice trial, round 2: pace fixed on both sides.

usage: voice_trial2.py      -> voice_trial2/blind/*.wav, voice_trial2/key.json, voice_trial2/lengths.json
Takes per line: Sarvam (Lakshmi re-voiced at pace 1.0, the rest as in Episode 1), Smallest.ai Lightning v3.1 and
Lightning v3.1 Pro. Smallest.ai lines are split at "..." with 0.45 s of silence between the pieces, and their speed is
calibrated (one measuring render, one final) so each take lasts about as long as the Sarvam take.
"""

import base64
import json
import random
import re
import subprocess
from pathlib import Path

import httpx

from voice_trial import ENV, KEY, LINES, level

P = Path(__file__).resolve().parents[1]
OUT = P / "voice_trial2"
SARVAM_VOICE = {"narrator": "ratan", "doctor": "gokul", "abhinav": "aayan", "lakshmi": "rupali"}
SARVAM_PACE = {"narrator": 0.92, "doctor": 0.95, "abhinav": 0.85, "lakshmi": 1.0}  # Lakshmi 0.9 -> 1.0 (too slow)
V31 = {"narrator": "venkat", "doctor": "lokesh", "lakshmi": "lavanya", "abhinav": "harish"}  # round 1's best
PRO = {"narrator": "shankar", "doctor": "manohar", "lakshmi": "sravani", "abhinav": "charan"}
PAUSE = 0.45
chars = 0


def duration(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(path)], capture_output=True, text=True, check=True).stdout)


def sarvam(text: str, who: str, out: Path) -> None:
    r = httpx.post("https://api.sarvam.ai/text-to-speech", headers={"api-subscription-key": ENV["SARVAM_API"].strip()},
                   json={"text": text, "target_language_code": "te-IN", "model": "bulbul:v3",
                         "speaker": SARVAM_VOICE[who], "pace": SARVAM_PACE[who], "speech_sample_rate": 24000},
                   timeout=120)
    r.raise_for_status()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(r.json()["audios"][0]))


def smallest(text: str, voice: str, model: str, speed: float, out: Path) -> None:
    """One take: render each piece between "..." and join them with PAUSE seconds of silence."""
    global chars
    pieces = [p.strip() for p in re.split(r"\.\.\.|…", text) if p.strip(" ,")]
    # A piece this short on its own comes back as babble (v3.1) or silence (Pro): join it to the next with a comma.
    merged: list[str] = []
    for p in pieces:
        if merged and len(merged[-1]) < 12:
            merged[-1] = f"{merged[-1].rstrip(' ,')}, {p}"
        else:
            merged.append(p)
    pieces = merged
    parts = []
    for i, piece in enumerate(pieces):
        r = httpx.post("https://api.smallest.ai/waves/v1/tts", headers={"Authorization": f"Bearer {KEY}"},
                       json={"text": piece, "voice_id": voice, "language": "te", "model": model,
                             "sample_rate": 24000, "output_format": "wav", "speed": round(speed, 2)}, timeout=120)
        r.raise_for_status()
        chars += len(piece)
        f = out.with_name(f"{out.stem}_p{i}.wav")
        f.write_bytes(r.content)
        parts.append(f)
    ins, chain = [], ""
    for i, f in enumerate(parts):
        ins += ["-i", str(f)]
        chain += f"[{i}]aresample=24000,aformat=channel_layouts=mono" + (f",apad=pad_dur={PAUSE}" if i < len(parts) - 1 else "") + f"[a{i}];"
    chain += "".join(f"[a{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", chain, str(out)], check=True)
    for f in parts:
        f.unlink()


def calibrated(text: str, voice: str, model: str, target: float, out: Path) -> float:
    """Render at 0.7, measure, re-render at the speed that should land on the target length."""
    out.parent.mkdir(parents=True, exist_ok=True)
    smallest(text, voice, model, 0.7, out)
    speed = min(1.2, max(0.5, 0.7 * duration(out) / target))
    smallest(text, voice, model, speed, out)
    return speed


def main() -> None:
    spec = {s["id"]: s for s in json.loads((P / "shots.json").read_text())["shots"]}
    blind = OUT / "blind"
    blind.mkdir(parents=True, exist_ok=True)
    rng = random.Random(1030)
    key, lengths = {}, {}
    for n, (sid, k, _emotion) in enumerate(LINES, 1):
        item = spec[sid][k]
        who = item.get("who") or spec[sid]["speaker"]
        sv = P / "audio/lines" / f"{sid}_{k}_{who}.wav"
        if who == "lakshmi":
            sv = OUT / "sarvam" / f"{sid}.wav"
            if not sv.exists():
                sarvam(item["te"], who, sv)
        target = duration(sv)
        takes = [("sarvam", sv)]
        lengths[sid] = {"sarvam": round(target, 2)}
        for tag, model, voice in (("v3.1", "lightning_v3.1", V31[who]), ("pro", "lightning_v3.1_pro", PRO[who])):
            f = OUT / "smallest" / f"{tag}_{voice}" / f"{sid}.wav"
            speed = calibrated(item["te"], voice, model, target, f)
            takes.append((f"smallest {tag}:{voice}", f))
            lengths[sid][f"{tag}:{voice}"] = {"secs": round(duration(f), 2), "speed": round(speed, 2)}
        rng.shuffle(takes)
        key[sid] = {}
        for letter, (name, src) in zip("ABC", takes):
            level(src, blind / f"{n:02d}_{sid}_{letter}.wav")
            key[sid][letter] = name
    (OUT / "key.json").write_text(json.dumps(key, indent=1) + "\n")
    (OUT / "lengths.json").write_text(json.dumps(lengths, indent=1) + "\n")
    print(json.dumps(lengths, indent=1))
    print(f"Smallest.ai characters this run: {chars}")


if __name__ == "__main__":
    main()
