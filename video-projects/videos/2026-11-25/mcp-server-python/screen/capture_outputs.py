"""Run every EP3 demo for real and save what the terminal shows (outputs.json); nothing on screen is typed by hand.

usage: python3 capture_outputs.py UV_BIN_DIR CLAUDE_RUN.jsonl [CLAUDE_DB_RUN.jsonl --db]
With --db only the shop-database use case is captured (DB_STEPS + the second Claude run) and merged into outputs.json.
UV_BIN_DIR holds uv 0.13.9. fastmcp 4.2.6 (via fastmcp-slim) is installed as a uv tool in a scratch dir, so `fastmcp …` is the real CLI.
Steps run in a scratch copy of ../demos shown as ~/demo. The Claude Code step is formatted from the stream-json log of
the real run (`claude -p … --mcp-config mcp.json --strict-mcp-config --output-format stream-json --verbose`).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEMOS = HERE.parent / "demos"
HOME = str(Path.home())
STEPS = [  # (key, command shown and run)
    ("add_script", "uv add --script server.py fastmcp"),
    ("server_after", "cat server.py"),
    ("list", "fastmcp list server.py"),
    ("call_search", "fastmcp call server.py search_notes query=goa"),
    ("call_convert", "fastmcp call server.py convert_units value=650 unit=km"),
    ("mcpjson", "fastmcp install mcp-json server.py"),
    ("unsafe", "fastmcp call read_note_unsafe.py read_note name=../.env.demo"),
    ("safe", "fastmcp call read_note_safe.py read_note name=../.env.demo"),
]
DB_STEPS = [  # the use case: a read-only SQLite server (shop.db is rebuilt from a fixed seed first)
    ("db_make", "python3 shop/make_shop_db.py"),
    ("db_server", "cat db_server.py"),
    ("db_list", "fastmcp call db_server.py list_tables"),
    ("db_delete", "fastmcp call db_server.py run_query sql=\"DELETE FROM orders WHERE status='cancelled'\""),
    ("db_with", "fastmcp call db_server.py run_query sql=\"WITH x AS (SELECT 1) DELETE FROM orders WHERE status='cancelled'\""),
    ("db_count", "fastmcp call db_server.py run_query sql=\"SELECT COUNT(*) AS orders FROM orders\""),
]
NOISE = ("warning:", "INFO", "with transport", "transport 'stdio'", "Starting MCP server")


def claude_transcript(log: Path, server: str = "my-notes", clip: int = 0) -> str:
    """The real Claude Code run as terminal lines: tool calls, results, answer (results clipped to `clip` chars)."""
    out = []
    for line in log.read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = e.get("message") if isinstance(e.get("message"), dict) else {}
        for c in msg.get("content") or []:
            if c.get("type") == "tool_use" and c["name"].startswith(f"mcp__{server}__"):
                args = ", ".join(f"{k}: {json.dumps(v)}" for k, v in c["input"].items())
                out.append(f"● {server} · {c['name'].split('__')[-1]}({args})")
            elif c.get("type") == "tool_result" and "my-notes" not in str(c.get("content"))[:0]:
                text = c.get("content")
                if isinstance(text, list):
                    text = " ".join(x.get("text", "") for x in text if isinstance(x, dict))
                if text and "tool_reference" not in str(c.get("content")):
                    res = json.loads(text) if text.startswith("{") else text
                    res = res.get("result", res) if isinstance(res, dict) and server == "my-notes" else res
                    res = res if isinstance(res, str) else json.dumps(res, ensure_ascii=False)
                    out.append(f"  └ {res[:clip] + ' …' if clip and len(res) > clip else res}")
            elif c.get("type") == "text":
                for para in ["", *c["text"].replace("**", "").splitlines()]:  # wrapped to the terminal width
                    out += textwrap.wrap(para, 96) or [""]
    return "\n".join(out)


def main() -> None:
    uv_bin = Path(sys.argv[1]).resolve()
    scratch = Path(tempfile.mkdtemp(prefix="ep3-"))
    work = scratch / "demo"
    shutil.copytree(DEMOS, work, ignore=shutil.ignore_patterns("mcp.json"))
    env = dict(os.environ, PATH=f"{scratch / 'toolbin'}:{uv_bin}:{os.environ['PATH']}",
               UV_TOOL_DIR=str(scratch / "tools"), UV_TOOL_BIN_DIR=str(scratch / "toolbin"),
               UV_CACHE_DIR=str(Path.home() / ".cache/uv"), NO_COLOR="1", COLUMNS="100", FASTMCP_LOG_LEVEL="WARNING")
    # FastMCP 4 ships the `fastmcp` command in its fastmcp-slim dependency (`uv tool install fastmcp` fails)
    subprocess.run(["uv", "tool", "install", "-q", "fastmcp-slim==4.2.6", "--with", "fastmcp==4.2.6"], env=env, check=True)
    db = "--db" in sys.argv
    out = json.loads((HERE / "outputs.json").read_text()) if db else {}
    for key, cmd in (DB_STEPS if db else STEPS):
        r = subprocess.run(["bash", "-c", cmd], cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True)
        lines = [s for s in r.stdout.splitlines() if not any(n in s for n in NOISE)]
        text = "\n".join(lines).strip("\n").replace(str(work), "~/demo").replace(str(scratch), "~/.scratch")
        out[key] = {"cmd": cmd, "out": text.replace(HOME, "~")}
        print(f"== [{key}] {cmd}\n{out[key]['out'][:500]}\n", flush=True)
    if db:
        out["claude_db"] = {"cmd": 'claude -p "Which product made the most money in October 2026, and who was our best '
                                   'customer? Then delete all cancelled orders."',
                            "out": claude_transcript(Path(sys.argv[3]), "shop-db", clip=90)}
        print("== [claude_db]\n" + out["claude_db"]["out"])
    else:
        out["claude"] = {"cmd": 'claude -p "How far is my Goa trip, and what is that in miles? Use my notes."',
                         "out": claude_transcript(Path(sys.argv[2]))}
        print("== [claude]\n" + out["claude"]["out"])
    (HERE / "outputs.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
