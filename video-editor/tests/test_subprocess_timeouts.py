"""#279: every blocking subprocess call in the engine carries a timeout.

A wedged FFmpeg or HyperFrames Chrome used to hang the worker forever, and the
job never reached ``requeue_stale``. Ruff's banned-api rule keeps new modules
on ``video_editor_ffmpeg.run``; this scan additionally checks that any allowed
direct call still passes ``timeout=``.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROOTS = ("packages", "apps", "skills")
BLOCKING = {"run", "check_output", "check_call", "call"}

#: ``Popen`` starts a long-lived process on purpose; it is never waited on here.
ALLOWED_POPEN = {"apps/cli/src/video_editor_cli/commands/preview.py"}


def _untimed_calls() -> list[str]:
    hits: list[str] = []
    for root in ROOTS:
        for path in (REPO / root).rglob("*.py"):
            rel = path.relative_to(REPO).as_posix()
            if "/tests/" in rel or "node_modules" in rel:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "subprocess"
                ):
                    continue
                name = node.func.attr
                keywords = {kw.arg for kw in node.keywords}
                if name in BLOCKING and "timeout" not in keywords:
                    hits.append(f"{rel}:{node.lineno} subprocess.{name}")
                if name == "Popen" and rel not in ALLOWED_POPEN:
                    hits.append(f"{rel}:{node.lineno} subprocess.Popen")
    return hits


def test_every_subprocess_call_has_a_timeout() -> None:
    hits = _untimed_calls()

    assert hits == [], (
        "pass timeout= (video_editor_ffmpeg.run.PROBE_TIMEOUT_SECONDS or "
        "RENDER_TIMEOUT_SECONDS), or use run_ffmpeg/run_subprocess:\n" + "\n".join(hits)
    )
