"""Captions (SRT) for upload: Whisper word timings with Whisper's mishearings fixed to the script's spelling.

usage: python3 make_srt.py [--offset 4.0]   -> out/uv.en.srt  (offset = intro length in the uploaded cut)
Copied from EP1; the FIX table is EP2's.
"""
import argparse
import json
import re
from pathlib import Path

P = Path(__file__).resolve().parent
FIX = [  # (whisper words, script words)
    (["penv,"], ["pyenv,"]), (["MacOS"], ["macOS"]), (["Winget,"], ["winget,"]),
    (["uv-dash", "version."], ["uv", "--version."]), (["-dash", "version."], ["--version."]),
    (["admin", "writes."], ["admin", "rights."]),
    (["dash", "dash", "python"], ["--python"]), (["dash", "dash", "no", "package."], ["--no-package."]),
    (["dash", "dash", "script"], ["--script"]), (["dash", "r"], ["-r"]),
    (["main", ".py"], ["main.py"]), (["pyproject.2ml"], ["pyproject.toml"]),
    (["Pyproject"], ["pyproject"]), (["py", "project"], ["pyproject"]),
    (["uv", ".log."], ["uv.lock."]), (["log", "file"], ["lock", "file"]), (["log", "file,"], ["lock", "file,"]),
    (["log", "file."], ["lock", "file."]), (["Committed", "to", "get,"], ["Commit", "it", "to", "git."]),
    (["pep", "723."], ["PEP", "723."]), (["hello", "rich", ".py."], ["hello_rich.py."]),
    (["rough"], ["Ruff"]), (["rough."], ["Ruff."]), (["install,", "rough"], ["install", "ruff"]),
    (["uvx", "rough", "check"], ["uvx", "ruff", "check"]),
    (["requirements", "dot", "txt"], ["requirements.txt"]), (["requirements", "dot", "txt."], ["requirements.txt."]),
    (["requirements", "dot", "txt", "file."], ["requirements.txt", "file?"]),
    (["vs", "code,"], ["VS", "Code,"]), (["vs", "code"], ["VS", "Code"]),
    (["dot", "when", "folder"], [".venv", "folder"]), (["dot", "when"], [".venv."]),
    (["python,", "select", "interpreter"], ["Python:", "Select", "Interpreter,"]),
    (["quick", "recap."], ["Quick", "recap."]), (["mcp"], ["MCP"]),
]


def ts(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def merge_split_tokens(words: list) -> list:
    """Whisper splits '3.15' into '3' + '.15' and 'ready-made' into 'ready' + '-made': join them back."""
    out: list = []
    for w in words:
        if out and re.match(r"^(\.\d|-made)", w[0]):
            out[-1] = [out[-1][0] + w[0], out[-1][1], w[2]]
        else:
            out.append(list(w))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offset", type=float, default=0.0)
    a = ap.parse_args()
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
    for k, w in enumerate(out):  # the language is "Python"; the command word after "uv" stays lowercase
        if re.fullmatch(r"python[.,]?", w[0]) and not (k and out[k - 1][0] == "uv"):
            out[k] = ["P" + w[0][1:], w[1], w[2]]
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
        lines.append(f"{n}\n{ts(c[0][1] + a.offset)} --> {ts(end + a.offset)}\n{' '.join(x[0] for x in c)}\n")
    (P / "out").mkdir(exist_ok=True)
    (P / "out/uv.en.srt").write_text("\n".join(lines), encoding="utf-8")
    print(len(cues), "captions")


if __name__ == "__main__":
    main()
