"""Call one flow-mcp tool (Google Flow automation, ~/flow-mcp) over stdio, for sessions that started before it was
registered as an MCP server.

usage: flow_call.py <tool> ['<json arguments>']     e.g. flow_call.py flow_status
Prints the tool's text reply. Long jobs answer with a task id; collect them with flow_wait.
"""

import json
import subprocess
import sys
from pathlib import Path

SERVER = ["node", str(Path.home() / "flow-mcp/dist/index.js")]


def call(tool: str, args: dict | None = None, timeout: float = 600) -> str:
    msgs = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "flow_call", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args or {}},
        },
    ]
    proc = subprocess.Popen(
        SERVER, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
    )
    assert proc.stdin and proc.stdout
    for m in msgs:
        proc.stdin.write(json.dumps(m) + "\n")
    proc.stdin.flush()
    try:
        for line in proc.stdout:
            msg = json.loads(line)
            if msg.get("id") == 2:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return "\n".join(c.get("text", "") for c in msg["result"].get("content", []))
        raise RuntimeError("flow-mcp closed without answering")
    finally:
        proc.stdin.close()
        proc.terminate()


if __name__ == "__main__":
    print(call(sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) > 2 else None))
