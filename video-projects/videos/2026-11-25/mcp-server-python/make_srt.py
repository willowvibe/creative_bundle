"""Captions (SRT) for upload: Whisper word timings with Whisper's mishearings fixed to the script's spelling.

usage: python3 make_srt.py [--offset 4.0]   -> out/mcp.en.srt  (offset = intro length in the uploaded cut)
Copied from EP2; the FIX table is EP3's.
"""
import argparse
import json
import re
from pathlib import Path

P = Path(__file__).resolve().parent
FIX = [  # (whisper words, script words)
    (["uvadd", "-scriptserver", ".pifastmcp."], ["uv", "add", "--script", "server.py", "fastmcp."]),
    (["mynotes."], ["My", "Notes."]), (["atmcp", ".tool", "decorator."], ["@mcp.tool", "decorator."]),
    (["mcp", ".run"], ["mcp.run()"]), (["FastMCP", "list"], ["fastmcp", "list"]), (["FastMCP", "call"], ["fastmcp", "call"]),
    (["FastMCP", "install"], ["fastmcp", "install"]), (["cloud", "desktop,"], ["Claude", "Desktop,"]),
    (["cloud", "code,"], ["Claude", "Code,"]), (["cloud", "code"], ["Claude", "Code"]), (["cursor,"], ["Cursor,"]),
    (["Our", "mcp", ".json"], ["or", "mcp-json"]), (["read", "node"], ["read_note"]), (["nodes", "folder"], ["notes", "folder"]),
    (["AP", "key."], ["API", "key."]), (["The", "fixes", "tool", "lines,"], ["The", "fix", "is", "two", "lines:"]),
    (["diffuse"], ["refuse"]), (["My", "rules", "never"], ["My", "rules:", "never"]),
    (["UV", "add", "dash", "dash", "script"], ["uv", "add", "--script"]), (["UV", "video,"], ["uv", "video,"]),
    (["Add", "mcp", ".tool"], ["@mcp.tool"]), (["Fast", "mcp", ".list"], ["fastmcp", "list"]),
    (["fast", "mcp", ".call"], ["fastmcp", "call"]), (["Fast", "mcp", ".install"], ["fastmcp", "install"]),
    (["model", "context", "protocol,"], ["Model", "Context", "Protocol,"]),
    (["functions,", "I", "wrote."], ["functions", "I", "wrote."]), (["dot", "dot", "slash", "dot", "env,"], ["../.env,"]),
    # use case (shop database)
    (["List", "tables"], ["list_tables"]), (["Run", "query"], ["run_query"]), (["list", "tables"], ["list_tables"]),
    (["through", "a", "run", "query"], ["through", "run_query"]), (["skips,", "cancelled"], ["skips", "cancelled"]),
    (["cold", "brew", "kit,"], ["Cold", "Brew", "Kit,"]), (["gets", "passed"], ["gets", "past"]),
    (["delete", "is", "refused"], ["DELETE", "is", "refused"]),
]


def ts(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def merge_split_tokens(words: list) -> list:
    """Whisper splits '3.15' into '3' + '.15', '19,000' into '19' + ',000' and 'ready-made' into 'ready' + '-made': join them back."""
    out: list = []
    for w in words:
        if out and re.match(r"^(\.\d|,\d|-made|-only|-C\b)", w[0]):
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
    (P / "out/mcp.en.srt").write_text("\n".join(lines), encoding="utf-8")
    print(len(cues), "captions")


if __name__ == "__main__":
    main()
