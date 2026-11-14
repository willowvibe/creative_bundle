"""Blind PoYo vs Qwen keyframe sheets: per shot, A and B side by side in a random order.

usage: compare_frames.py   -> edit/compare/sheet_*.jpg, edit/compare/refs.jpg, edit/compare/key.json
"""

import json
import random
import subprocess
from pathlib import Path

P = Path(__file__).resolve().parents[1]
OUT = P / "edit" / "compare"
FONT = "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"
SPEC = json.loads((P / "shots.json").read_text())
shots = [s["id"] for s in SPEC["shots"] if s["kind"] != "title" and (P / "frames_qwen" / f"{s['id']}.png").exists()]
rng = random.Random(1029)
OUT.mkdir(parents=True, exist_ok=True)
key = {}


def tile(src: Path, label: str, dst: Path) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf",
                    f"scale=900:-2,pad=900:506:(ow-iw)/2:(oh-ih)/2,"
                    f"drawtext=fontfile={FONT}:text='{label}':x=16:y=12:fontsize=40:fontcolor=yellow:box=1:boxcolor=black@0.6",
                    "-q:v", "3", str(dst)], check=True)


rows = []
for sid in shots:
    pair = [("poyo", P / "frames" / f"{sid}.jpg"), ("qwen", P / "frames_qwen" / f"{sid}.png")]
    rng.shuffle(pair)
    key[sid] = {"A": pair[0][0], "B": pair[1][0]}
    a, b = OUT / f"_{sid}_A.jpg", OUT / f"_{sid}_B.jpg"
    tile(pair[0][1], f"{sid}  A", a)
    tile(pair[1][1], f"{sid}  B", b)
    rows.append((a, b))

for n in range(0, len(rows), 4):
    chunk = rows[n:n + 4]
    ins = [x for r in chunk for p in r for x in ("-i", str(p))]
    f = ";".join(f"[{2 * i}][{2 * i + 1}]hstack[r{i}]" for i in range(len(chunk)))
    f += ";" + "".join(f"[r{i}]" for i in range(len(chunk))) + f"vstack={len(chunk)}" if len(chunk) > 1 else ";[r0]null"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", f, "-q:v", "3",
                    str(OUT / f"sheet_{n // 4 + 1}.jpg")], check=True)

chars = list(SPEC["characters"])
ins = [x for c in chars for x in ("-i", str(P / "refs" / f"{c}.jpg"))]
f = ";".join(f"[{i}]scale=-2:520,drawtext=fontfile={FONT}:text='{c}':x=10:y=10:fontsize=30:fontcolor=yellow:"
             f"box=1:boxcolor=black@0.6[c{i}]" for i, c in enumerate(chars))
f += ";" + "".join(f"[c{i}]" for i in range(len(chars))) + f"hstack={len(chars)}"
subprocess.run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", f, "-q:v", "3", str(OUT / "refs.jpg")], check=True)

(OUT / "key.json").write_text(json.dumps(key, indent=1) + "\n")
for p in OUT.glob("_*.jpg"):
    p.unlink()
print(f"{len(rows)} shots, {len(list(OUT.glob('sheet_*.jpg')))} sheets; key in {OUT / 'key.json'}")
