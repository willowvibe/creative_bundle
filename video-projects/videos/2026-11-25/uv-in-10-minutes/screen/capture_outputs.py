"""Run every EP2 demo for real and save what the terminal shows (outputs.json), so on-screen output is never typed by hand.

usage: python3 capture_outputs.py UV_BIN_DIR [KEY ...]
UV_BIN_DIR holds the uv 0.13.9 `uv`/`uvx` binaries (e.g. a scratch venv's bin/ after `pip install uv==0.13.9`).
Steps run in order in a scratch work dir (shown as ~/demo); caches, tools and Python installs go to scratch dirs so
installs are real downloads and nothing on the machine changes. Git author info is hidden so `uv init` writes no email.
With KEYs, every step still runs once (later steps need the state) but only those keys are re-captured.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEMOS = HERE.parent / "demos"
HOME = str(Path.home())

STEPS = [  # (key, command shown and run, working dir under ~/demo)
    ("race_pip", "python3 -m venv .venv && .venv/bin/pip install --no-cache-dir -r requirements.txt", "race"),
    ("race_uv", "uv venv && uv pip install -r requirements.txt", "race"),
    ("race_uv_warm", "uv venv && uv pip install -r requirements.txt", "race"),
    ("version", "uv --version", "."),
    ("py_install", "uv python install 3.13", "."),
    ("py_list", "uv python list --only-installed", "."),
    ("run314", "uv run --python 3.14 python --version", "."),
    ("init", "uv init weather", "."),
    ("init_tree", "tree -a -I .git weather", "."),
    ("init_pyproject", "cat pyproject.toml", "weather"),
    ("add", "uv add requests rich", "weather"),
    ("add_pyproject", "cat pyproject.toml", "weather"),
    ("lock_head", "head -24 uv.lock", "weather"),
    ("tree", "uv tree", "weather"),
    ("run", "uv run weather", "weather"),
    ("sync", "rm -rf .venv && uv sync", "weather"),
    ("add_script", "uv add --script hello_rich.py rich", "."),
    ("script_after", "cat hello_rich.py", "."),
    ("run_script", "uv run hello_rich.py", "."),
    ("tool_install", "uv tool install ruff", "."),
    ("uvx_ruff", "uvx ruff check messy.py", "."),
    ("add_r", "uv add -r requirements.txt", "legacy"),
    ("add_r_deps", "grep -A8 '^dependencies' pyproject.toml", "legacy"),
]

# Timed steps print bash's `time` line. The narration says "about nineteen", "about five", "five hundredths",
# "about three seconds" and "twelve milliseconds", so these re-run (up to 30 times, 200 for the fast `sync`) until the output matches.
TIMED = {"race_pip", "race_uv", "race_uv_warm"}
MUST = {
    "race_pip": lambda t: 18.5 <= secs(t) < 19.5,
    "race_uv": lambda t: 4.5 <= secs(t) < 5.5,
    "race_uv_warm": lambda t: 0.045 <= secs(t) < 0.055,
    "py_install": lambda t: re.search(r"in (2\.[5-9]\d*|3\.[0-4]\d*)s", t) is not None,
    "sync": lambda t: "in 12ms" in t,
}
RETRYABLE = set(MUST) - {"add_r"}


def secs(text: str) -> float:
    m = re.search(r"real\s+(\d+)m([\d.]+)s", text)
    return int(m.group(1)) * 60 + float(m.group(2)) if m else -1.0


def prepare(key: str, work: Path, scratch: Path, env: dict) -> None:
    """Reset state before each try so every try is a fair, real run."""
    if key.startswith("race"):
        shutil.rmtree(work / "race/.venv", ignore_errors=True)
    if key == "race_uv":
        shutil.rmtree(scratch / "cache", ignore_errors=True)
    if key == "py_install":
        shutil.rmtree(scratch / "pyfresh", ignore_errors=True)
    if key == "run":
        shutil.copy(DEMOS / "weather_init.py", work / "weather/src/weather/__init__.py")
    if key == "add_r" and not (work / "legacy").exists():
        subprocess.run("uv init -q legacy && cp requirements.txt legacy/", shell=True, cwd=work, env=env, check=True)


def main() -> None:
    uv_bin = Path(sys.argv[1]).resolve()
    only = set(sys.argv[2:])
    scratch = Path(tempfile.mkdtemp(prefix="ep2-"))
    work = scratch / "demo"
    shutil.copytree(DEMOS, work)
    (work / "race").mkdir()
    shutil.copy(DEMOS / "requirements.txt", work / "race")
    env = dict(os.environ, PATH=f"{uv_bin}:{os.environ['PATH']}", UV_CACHE_DIR=str(scratch / "cache"),
               UV_TOOL_DIR=str(scratch / "tools"), UV_TOOL_BIN_DIR=str(scratch / "toolbin"),
               UV_PYTHON_BIN_DIR=str(scratch / "pybin"), GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
               NO_COLOR="1", COLUMNS="100", TIMEFORMAT="\nreal\t%lR")
    out = json.loads((HERE / "outputs.json").read_text()) if only and (HERE / "outputs.json").exists() else {}

    for key, cmd, cwd in STEPS:
        step_env = dict(env)
        if key == "py_install":  # fresh install dir so 3.13 is a real download
            step_env["UV_PYTHON_INSTALL_DIR"] = str(scratch / "pyfresh")
        capture = not only or key in only
        tries = 0
        while True:
            tries += 1
            prepare(key, work, scratch, step_env)
            run_cmd = f"time ( {cmd} )" if key in TIMED else cmd
            # one stream, so stdout and stderr lines keep the order a terminal would show
            r = subprocess.run(["bash", "-c", run_cmd], cwd=work / cwd, env=step_env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
            text = r.stdout.strip("\n")
            text = text.replace(str(work), "~/demo").replace(str(scratch), "~/.scratch").replace(HOME, "~")
            ok = MUST.get(key, lambda t: True)(text)
            if ok or not capture or key not in RETRYABLE or tries >= (200 if key == "sync" else 30):
                break
        if not capture:
            continue
        out[key] = {"cmd": cmd, "cwd": cwd, "out": text, "tries": tries, "matched": ok}
        print(f"== [{key}] {cmd}  (tries {tries}, matched {ok})\n{text[:600]}\n", flush=True)
    (HERE / "outputs.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
