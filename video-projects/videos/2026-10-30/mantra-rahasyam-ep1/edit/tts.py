"""ElevenLabs v4 Telugu TTS for Episode 1 (restart): every line, one voice per character.

usage: tts.py samples              # one short line per chosen voice + pitch, to check the cast
       tts.py lines [--review-draft]  # every line with its voice and emotion tag (draft: before check 1, on request)
Each line's intended emotion (``emotion`` on the line, else on the shot) goes in front as an inline tag,
e.g. "[sobbing, desperate] ...". Re-running writes new files only for lines whose audio is missing; delete a take
to redo it.
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
# Native Telugu ElevenLabs library voices. The five Episode 1 v3 voices carry over (series bibles in
# videos/mantra-rahasyam/assets/characters/); the seven new ones were cast from the library on 2026-10-30.
CHOSEN = {
    "narrator": "RoLT1qx7XJRXxWSIG5hS",  # Bharadwaj, "Profound Telugu"
    "abhinav": "RmsECPYd2k7yLIMTl1cR",  # Dhruva S, natural and steady (user pick, 2026-10-30; was Kaka)
    "lakshmi": "MSL6SPFstHlNiXg39FEg",  # Vijaya, engaging storyteller
    "raghava": "f4UZfBDD7d4QR1DcXAMf",  # Sai Datta, dramatic
    "doctor": "9ebvxK9znS3l7PfpMK9l",  # Kaka, warm and calm (replaced Vagmin, now Shiva, 2026-10-30)
    "grandfather": "EohubqvAiTiSkWCp1vu7",  # Srirama V, mature, wise, unhurried
    "nurse1": "0U7pUcfIxJzwwTseQz75",  # Janaki, calm and respectful
    "nurse2": "mIVQVyTUp0kTvgxJaNOc",  # Seeta, bold and crisp
    "passerby": "VFbICGvBSvKmHwlI9rzA",  # Jayesh M, real and natural
    "yama1": "vyKB6blbEI9vx3EnT9b4",  # Rakesh Devadasu (pitched down in the mix)
    "yama2": "byBvnAtc0165IHbm4c7a",  # Maddy (pitched down in the mix)
    "shiva": "ZoV3Y9cqdke2p8nzz7XP",  # Vagmin, authoritative (user pick, 2026-10-30); soft echo in the mix
}
PACE = {"narrator": 1.0, "abhinav": 0.95, "lakshmi": 1.0, "raghava": 0.95, "doctor": 1.0, "grandfather": 0.85,
        "nurse1": 1.0, "nurse2": 1.1, "passerby": 1.05, "yama1": 0.85, "yama2": 0.85, "shiva": 0.9}  # range 0.7-1.2
# Higher stability = less expressive. The narrator carries ~30% of the characters' emotion (user, 2026-10-30);
# the gods and their messengers stay steady.
STABILITY = {"narrator": 0.8, "doctor": 0.55, "shiva": 0.6, "yama1": 0.65, "yama2": 0.65, "grandfather": 0.6}


def tts(text: str, voice: str, out: Path, pace: float = 1.0, emotion: str = "", stability: float = 0.5,
        language: str = "te") -> Path:
    """`language` lets the same cast voice the Hindi, Tamil and Kannada dubs (edit/v2_dub.py); Telugu stays the default."""
    tagged = f"[{emotion}] {text}" if emotion else text
    r = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": ENV["ELEVENLABS_API_KEY"].strip().strip("'\"")},
        json={"text": tagged, "model_id": "eleven_v4", "language_code": language,
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
            if not item or (key == "line" and (s.get("audio") or s.get("provided"))):  # recorded or user-supplied
                continue
            who = item.get("who") or s["speaker"]
            out = P / "audio/lines" / f"{s['id']}_{key}_{who}.wav"
            if not out.exists():
                tts(item["te"], CHOSEN[who], out, PACE[who], item.get("emotion") or s.get("emotion", ""),
                    STABILITY.get(who, 0.5))
            total += len(item["te"])
            print(f"{s['id']} {key:4s} {who:8s} {duration(out):5.2f} s  {out.name}")
    print(f"characters: {total}")
