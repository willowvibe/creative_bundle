"""Captions (SRT) for upload: Whisper word timings with Whisper's mishearings fixed to the script's spelling.

usage: python3 make_srt.py   -> out/python315.en.srt
"""
import json
import re
from pathlib import Path

P = Path(__file__).resolve().parent
FIX = [  # (whisper words, script words)
    (["frozen", "deck,"], ["frozendict,"]), (["frozen", "deck"], ["frozendict"]), (["frozen", "diet,"], ["frozendict,"]),
    (["UV", "Python", "install"], ["uv", "python", "install"]), (["UV,"], ["uv,"]), (["UV"], ["uv"]), (["Sentinel."], ["sentinel."]), (["Sentinel"], ["sentinel"]),
    (["-mprofiling", ".sampling,"], ["-m", "profiling.sampling,"]), (["tachyon."], ["Tachyon."]),
    (["tachyon"], ["Tachyon"]), (["merge"], ["merged"]), (["cafe"], ["café,"]), (["type", "error."], ["TypeError."]),
    (["Missing"], ["MISSING"]), (["none"], ["None"]), (["none."], ["None."]),
]


def ts(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def merge_split_tokens(words: list) -> list:
    """Whisper splits '3.15' into '3' + '.15' and 'built-in' into 'built' + '-in': join them back."""
    out: list = []
    for w in words:
        if out and re.match(r"^(\.\d|-in\b|-8)", w[0]):
            out[-1] = [out[-1][0] + w[0], out[-1][1], w[2]]
        else:
            out.append(list(w))
    return out


def main() -> None:
    words = merge_split_tokens(json.load(open(P / "audio/words.json")))
    out, i = [], 0
    while i < len(words):
        for src, dst in FIX:
            if [w[0] for w in words[i:i + len(src)]] == src:
                s, e = words[i][1], words[i + len(src) - 1][2]
                out += [[d, s, e] for d in dst]
                i += len(src)
                break
        else:
            out.append(words[i])
            i += 1
    # "show you the fastest way": the TTS said "uv, the" (one long Whisper token)
    for k in range(len(out) - 2):
        if [w[0] for w in out[k:k + 3]] == ["you", "the", "fastest"]:
            out.insert(k + 1, ["uv,", out[k + 1][1], out[k + 1][1] + 0.35])
            out[k + 2] = ["the", out[k + 1][2], out[k + 2][2]]
            break
    cues, cur = [], []
    for w in out:
        cur.append(w)
        text = " ".join(x[0] for x in cur)
        if len(text) > 38 or re.search(r"[.?!,]$", w[0]) and len(text) > 18:
            cues.append(cur)
            cur = []
    if cur:
        cues.append(cur)
    lines = []
    for n, c in enumerate(cues, 1):
        end = c[-1][2]
        if n < len(cues):
            end = min(end + 0.3, cues[n][0][1])
        lines.append(f"{n}\n{ts(c[0][1])} --> {ts(end)}\n{' '.join(x[0] for x in c)}\n")
    (P / "out").mkdir(exist_ok=True)
    (P / "out/python315.en.srt").write_text("\n".join(lines), encoding="utf-8")
    print(len(cues), "captions")


if __name__ == "__main__":
    main()
