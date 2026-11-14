"""Blind voice trial: Sarvam bulbul:v3 (the takes already used in Episode 1) vs two Smallest.ai Lightning v3.1 voices.

usage: voice_trial.py      -> voice_trial/review.html (blind A/B/C per line), voice_trial/key.json
Same Telugu text and pace as the Sarvam takes. Every take is loudness-matched (-18 LUFS) so volume doesn't decide.
"""

import json
import random
import shutil
import subprocess
from pathlib import Path

import httpx

P = Path(__file__).resolve().parents[1]
OUT = P / "voice_trial"
ENV = dict(line.split("=", 1) for line in Path("/mnt/data2/git_repos/creative_repos/video-editor/.env").read_text().splitlines()
           if "=" in line and not line.startswith("#"))
KEY = ENV["SMALLEST_API_KEY"].strip().strip("'\"")
PACE = {"narrator": 0.92, "doctor": 0.95, "abhinav": 0.85, "lakshmi": 0.9}  # same as tts.py
# Two candidates per character from the South-Indian voice set (voices tagged with 6 languages incl. Telugu).
CANDIDATES = {"narrator": ["teja", "venkat"], "doctor": ["prashanth", "lokesh"],
              "lakshmi": ["nandini", "lavanya"], "abhinav": ["arun", "harish"]}
LINES = [  # (shot, key, intended emotion)
    ("s03", "vo", "suspense, wonder"),
    ("s04", "line", "grave, gentle, sorry"),
    ("s05", "line", "sobbing, desperate"),
    ("s15", "line", "shocked, disbelief"),
    ("s16", "line", "tears of joy, relief"),
    ("s17", "line", "whispering, eerie, serious"),
]


def smallest(text: str, voice: str, speed: float, out: Path) -> None:
    r = httpx.post("https://api.smallest.ai/waves/v1/tts", headers={"Authorization": f"Bearer {KEY}"},
                   json={"text": text, "voice_id": voice, "language": "te", "model": "lightning_v3.1",
                         "sample_rate": 24000, "output_format": "wav", "speed": speed}, timeout=120)
    r.raise_for_status()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(r.content)


def level(src: Path, dst: Path) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", "loudnorm=I=-18:TP=-1.5:LRA=11",
                    "-ar", "24000", "-ac", "1", str(dst)], check=True)


def main() -> None:
    spec = {s["id"]: s for s in json.loads((P / "shots.json").read_text())["shots"]}
    blind = OUT / "blind"
    blind.mkdir(parents=True, exist_ok=True)
    rng = random.Random(1029)
    key, rows, chars = {}, [], 0
    for n, (sid, k, emotion) in enumerate(LINES, 1):
        s = spec[sid]
        item = s[k]
        who = item.get("who") or s["speaker"]
        takes = [("sarvam", P / "audio/lines" / f"{sid}_{k}_{who}.wav")]
        for v in CANDIDATES[who]:
            f = OUT / "smallest" / v / f"{sid}.wav"
            if not f.exists():
                smallest(item["te"], v, PACE[who], f)
                chars += len(item["te"])
            takes.append((f"smallest:{v}", f))
        rng.shuffle(takes)
        key[sid] = {}
        cells = []
        for letter, (name, src) in zip("ABC", takes):
            dst = blind / f"{n:02d}_{sid}_{letter}.wav"
            level(src, dst)
            key[sid][letter] = name
            cells.append(f"<td><b>{letter}</b><br><audio controls preload='none' src='blind/{dst.name}'></audio></td>")
        rows.append(f"<tr><td>{n}</td><td>{sid}<br><small>{who}</small></td><td lang='te'>{item['te']}<br>"
                    f"<small>{item['en']}</small></td><td>{emotion}</td>{''.join(cells)}</tr>")
    (OUT / "key.json").write_text(json.dumps(key, indent=1) + "\n")
    (OUT / "review.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>Voice trial</title><style>body{font:15px system-ui;margin:16px}"
        "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:6px;vertical-align:top}small{color:#555}"
        "</style><h1>Blind voice trial</h1><p>Each line has three takes, A, B and C, in random order. Which one "
        "carries the intended emotion best, and does any sound wrong (pronunciation, gender, age)?</p><table><tr>"
        "<th>#</th><th>Line</th><th>Text</th><th>Intended emotion</th><th>A</th><th>B</th><th>C</th></tr>"
        + "".join(rows) + "</table>\n")
    print(f"{len(LINES)} lines, Smallest.ai characters this run: {chars}")
    shutil.copy(OUT / "review.html", OUT / "index.html")


if __name__ == "__main__":
    main()
