"""Drive audio for the lip-sync shots, and the doubled chant.

usage: drive.py
edit/drive/<shot>.wav = 0.3 s lead-in + the line + 0.2 s tail (24 kHz mono), from audio/lines. It feeds both the lip-sync
models and the mix. The chant (s09) is the channel's mantra cycle played CHANT_REPEAT times (user, 2026-10-30). Each
copy keeps its 0.3 s lead-in, which becomes a breath between cycles. The chant clip is repeated the same way:
clips/s09.mp4 becomes CHANT_REPEAT copies of clips/s09_single.mp4, each trimmed to one drive cycle so they stay in sync.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/data2/git_repos/creative_repos/video-editor/scripts")
from drama_review import require  # noqa: E402

P = Path(__file__).resolve().parents[1]
require(P, "voices")  # user approved the dialogue and the voice emotion
SPEC = json.loads((P / "shots.json").read_text())
DRIVE = P / "edit" / "drive"
LEAD, TAIL = 0.3, 0.2
CHANT_REPEAT = 2


def run(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def seconds(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(path)], capture_output=True, text=True, check=True).stdout)


def main() -> None:
    DRIVE.mkdir(exist_ok=True)
    for s in SPEC["shots"]:
        if s["kind"] != "lipsync":
            continue
        sid, out = s["id"], DRIVE / f"{s['id']}.wav"
        if s.get("audio"):  # the recorded chant: one cycle with its lead-in, then repeated
            one = DRIVE / f"_{sid}_one.wav"
            run("-i", str(P / s["audio"]), "-af", f"adelay={int(LEAD * 1000)}:all=1", "-ar", "24000", "-ac", "1", str(one))
            run("-stream_loop", str(CHANT_REPEAT - 1), "-i", str(one), "-c", "copy", str(out))
            cycle = seconds(one)
            one.unlink()
            single = P / "clips" / f"{sid}_single.mp4"
            if not single.exists():
                shutil.copyfile(P / "clips" / f"{sid}.mp4", single)
            part = P / "clips" / f"_{sid}_cycle.mp4"
            run("-i", str(single), "-t", f"{cycle:.3f}", "-an", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
                str(part))
            run("-stream_loop", str(CHANT_REPEAT - 1), "-i", str(part), "-c", "copy", str(P / "clips" / f"{sid}.mp4"))
            part.unlink()
        else:
            who = s["line"].get("who") or s["speaker"]
            line = P / "audio" / "lines" / f"{sid}_line_{who}.wav"
            run("-i", str(line), "-af", f"adelay={int(LEAD * 1000)}:all=1,apad=pad_dur={TAIL}", "-ar", "24000", "-ac", "1",
                str(out))
        print(f"{sid}: drive {seconds(out):.2f} s")
    chant = P / "clips" / "s09.mp4"
    print(f"s09 clip {seconds(chant):.2f} s ({CHANT_REPEAT} x chant)")


if __name__ == "__main__":
    main()
