"""Blind voice trial, round 3: ElevenLabs v4 (with emotion tags) vs round 2's Sarvam and Smallest.ai Pro takes.

usage: voice_trial3.py      -> voice_trial3/blind/*.wav, voice_trial3/key.json, voice_trial3/lengths.json
ElevenLabs takes carry one inline audio tag from the line's intended emotion, and their speed is calibrated
(one measuring render, one final) to the Sarvam take's length. The other two takes are reused from round 2.
"""

import json
import random
import subprocess
from pathlib import Path

import httpx

from voice_trial import ENV, LINES, level
from voice_trial2 import duration

P = Path(__file__).resolve().parents[1]
R2 = P / "voice_trial2"
OUT = P / "voice_trial3"
KEY = ENV["ELEVENLABS_API_KEY"].strip().strip("'\"")
VOICE = {  # native Telugu library voices
    "narrator": ("RoLT1qx7XJRXxWSIG5hS", "Bharadwaj"),
    "doctor": ("ZoV3Y9cqdke2p8nzz7XP", "Vagmin"),
    "lakshmi": ("MSL6SPFstHlNiXg39FEg", "Vijaya"),
    "abhinav": ("9ebvxK9znS3l7PfpMK9l", "Kaka"),
}
TAG = {
    "s03": "[hushed, suspenseful]",
    "s04": "[gently, sorrowful, apologetic]",
    "s05": "[sobbing, desperate]",
    "s15": "[shocked, in disbelief]",
    "s16": "[crying with joy, relieved]",
    "s17": "[whispers, eerie, serious]",
}
PRO = {"narrator": "shankar", "doctor": "manohar", "lakshmi": "sravani", "abhinav": "charan"}
chars = 0


def eleven(text: str, voice: str, speed: float, out: Path) -> None:
    global chars
    r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}", params={"output_format": "mp3_44100_128"},
                   headers={"xi-api-key": KEY},
                   json={"text": text, "model_id": "eleven_v4", "language_code": "te",
                         "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "speed": round(speed, 2)}},
                   timeout=120)
    r.raise_for_status()
    chars += len(text)
    out.parent.mkdir(parents=True, exist_ok=True)
    mp3 = out.with_suffix(".mp3")
    mp3.write_bytes(r.content)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp3), "-ar", "24000", "-ac", "1", str(out)], check=True)
    mp3.unlink()


def main() -> None:
    spec = {s["id"]: s for s in json.loads((P / "shots.json").read_text())["shots"]}
    r2key = json.loads((R2 / "key.json").read_text())
    blind = OUT / "blind"
    blind.mkdir(parents=True, exist_ok=True)
    rng = random.Random(1031)
    key, lengths = {}, {}
    for n, (sid, k, _emotion) in enumerate(LINES, 1):
        item = spec[sid][k]
        who = item.get("who") or spec[sid]["speaker"]
        sv = R2 / "sarvam" / f"{sid}.wav" if who == "lakshmi" else P / "audio/lines" / f"{sid}_{k}_{who}.wav"
        pro = R2 / "smallest" / f"pro_{PRO[who]}" / f"{sid}.wav"
        target = duration(sv)
        voice, vname = VOICE[who]
        el = OUT / "elevenlabs" / vname / f"{sid}.wav"
        text = f"{TAG[sid]} {item['te']}"
        eleven(text, voice, 0.95, el)
        speed = min(1.2, max(0.7, 0.95 * duration(el) / target))
        if abs(speed - 0.95) > 0.03:
            eleven(text, voice, speed, el)
        lengths[sid] = {"sarvam": round(target, 2), "pro": round(duration(pro), 2),
                        f"elevenlabs:{vname}": {"secs": round(duration(el), 2), "speed": round(speed, 2)}}
        takes = [("sarvam", sv), (f"smallest pro:{PRO[who]}", pro), (f"elevenlabs v4:{vname}", el)]
        rng.shuffle(takes)
        key[sid] = {}
        for letter, (name, src) in zip("ABC", takes):
            level(src, blind / f"{n:02d}_{sid}_{letter}.wav")
            key[sid][letter] = name
        assert r2key  # round 2 key must exist: its takes are reused here
    (OUT / "key.json").write_text(json.dumps(key, indent=1) + "\n")
    (OUT / "lengths.json").write_text(json.dumps(lengths, indent=1) + "\n")
    print(json.dumps(lengths, indent=1))
    print(f"ElevenLabs characters this run: {chars}")


if __name__ == "__main__":
    main()
