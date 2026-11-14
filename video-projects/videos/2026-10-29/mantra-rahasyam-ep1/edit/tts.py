"""ElevenLabs v4 Telugu TTS for the episode (default voice-over since 2026-10-30; it won the blind voice trial).

usage: tts.py samples              # one short line per chosen voice + pitch, to check the cast
       tts.py lines [--review-draft]  # every line with its voice and emotion tag (draft: before check 1, on request)
Each line's intended emotion (``emotion`` on the line, else on the shot) goes in front as an inline tag,
e.g. "[sobbing, desperate] ...". Episode 1's takes were Sarvam bulbul:v3; re-running writes new files only
for lines whose audio is missing.
"""

import json
import subprocess
import sys
from pathlib import Path

import httpx
import numpy as np

sys.path.insert(0, "/mnt/data2/git_repos/creative_repos/video-editor/scripts")
from drama_review import require  # noqa: E402

P = Path(__file__).resolve().parents[1]
ENV = dict(
    line.split("=", 1) for line in Path("/mnt/data2/git_repos/creative_repos/video-editor/.env").read_text().splitlines()
    if "=" in line and not line.startswith("#")
)
# Native Telugu ElevenLabs library voices. All but Raghava won the blind trial; Raghava's is untested (check 2).
CHOSEN = {
    "narrator": "RoLT1qx7XJRXxWSIG5hS",  # Bharadwaj, "Profound Telugu"
    "raghava": "f4UZfBDD7d4QR1DcXAMf",  # Sai Datta, "Dramatic Telugu" (untested)
    "doctor": "ZoV3Y9cqdke2p8nzz7XP",  # Vagmin
    "abhinav": "9ebvxK9znS3l7PfpMK9l",  # Kaka
    "lakshmi": "MSL6SPFstHlNiXg39FEg",  # Vijaya
}
PACE = {"narrator": 1.0, "raghava": 0.95, "doctor": 1.0, "abhinav": 0.9, "lakshmi": 1.0}  # ElevenLabs range 0.7-1.2
# Higher stability = less expressive. The narrator carries ~30% of the characters' emotion (user, 2026-10-30).
STABILITY = {"narrator": 0.8}


def tts(text: str, voice: str, out: Path, pace: float = 1.0, emotion: str = "", stability: float = 0.5) -> Path:
    tagged = f"[{emotion}] {text}" if emotion else text
    r = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": ENV["ELEVENLABS_API_KEY"].strip().strip("'\"")},
        json={"text": tagged, "model_id": "eleven_v4", "language_code": "te",
              "voice_settings": {"stability": stability, "similarity_boost": 0.75, "speed": pace}},
        timeout=120,
    )
    r.raise_for_status()
    out.parent.mkdir(parents=True, exist_ok=True)
    mp3 = out.with_suffix(".mp3")
    mp3.write_bytes(r.content)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp3), "-ar", "24000", "-ac", "1", str(out)], check=True)
    mp3.unlink()
    return out


def pitch_hz(path: Path) -> float:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32)
    f0s = []
    for i in range(0, len(x) - 1024, 512):
        fr = x[i:i + 1024] - x[i:i + 1024].mean()
        if np.sqrt((fr ** 2).mean()) < 500:
            continue
        ac = np.correlate(fr, fr, "full")[1023:]
        lo, hi = 16000 // 400, 16000 // 70
        lag = lo + int(np.argmax(ac[lo:hi]))
        if ac[lag] > 0.3 * ac[0]:
            f0s.append(16000 / lag)
    return float(np.median(f0s)) if f0s else 0.0


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout)


if sys.argv[1] == "samples":
    text = "ఏ దారీ లేనప్పుడు, ఆ తండ్రికి గుర్తొచ్చింది ఒకే ఒక్క మార్గం."
    for who, voice in CHOSEN.items():
        f = tts(text, voice, P / "audio/samples" / f"{who}.wav", PACE[who])
        print(f"{who:8s} pitch {pitch_hz(f):5.0f} Hz  {duration(f):.1f} s")
elif sys.argv[1] == "lines":
    # Check 1 normally comes first. "--review-draft" voices the lines anyway, only when the user asks to hear the
    # dialogue and its audio together; nothing downstream opens until both checks are approved.
    if "--review-draft" not in sys.argv:
        require(P, "dialogue")  # user approved every line before any voice is generated
    shots = json.loads((P / "shots.json").read_text())["shots"]
    total = 0
    for s in shots:
        for key in ("vo", "line"):
            item = s.get(key)
            if not item or (key == "line" and s.get("audio")):
                continue
            who = item.get("who") or s["speaker"]
            out = P / "audio/lines" / f"{s['id']}_{key}_{who}.wav"
            if not out.exists():
                tts(item["te"], CHOSEN[who], out, PACE[who], item.get("emotion") or s.get("emotion", ""),
                    STABILITY.get(who, 0.5))
            total += len(item["te"])
            print(f"{s['id']} {key:4s} {who:8s} {duration(out):5.2f} s  {out.name}")
    print(f"characters: {total}")
