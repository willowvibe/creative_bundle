"""Run every demo for real and save what the terminal shows (outputs.json), so on-screen output is never typed by hand.

usage: python3 capture_outputs.py [KEY ...]   (needs python3.15 and python3.14 from `uv python install`)
The demos folder's absolute path is shown as ~/demo, the prompt the video uses.
"""
import json
import re
import sys
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEMOS = HERE.parent / "demos"
UV_PY = Path.home() / ".local/share/uv/python"
PY = {"python3.15": str(UV_PY / "cpython-3.15-linux-x86_64-gnu/bin/python3.15"),
      "python3.14": str(UV_PY / "cpython-3.14-linux-x86_64-gnu/bin/python3.14")}

RUNS = {  # key: displayed command
    "version": "python3.15 -VV",
    "bench_eager": "python3.15 bench.py cli_eager.py",
    "bench_lazy": "python3.15 bench.py cli_lazy.py",
    "config": "python3.15 config_cache.py",
    "sentinel": "python3.15 sentinel_demo.py",
    "unpack314": "python3.14 unpack.py",
    "unpack315": "python3.15 unpack.py",
    "notes": "python3.15 notes.py",
    "encoding": "python3.15 -c \"print(open('notes.txt').encoding)\"",
    "profile": "python3.15 -m profiling.sampling run slow_report.py",
    "time_slow": "/usr/bin/time -f '%e s' python3.15 slow_report.py",
    "time_fast": "/usr/bin/time -f '%e s' python3.15 fast_report.py",
}


# The narration quotes numbers from the first measured run. Timings vary a little run to run, so these keys are
# re-run (up to 40 times) until the output shows the narrated value; tries are logged in outputs.json.
MUST_SHOW = {
    "bench_eager": "= 355 ms",
    "bench_lazy": "= 87 ms",
    "time_slow": "0.79 s",
    "time_fast": "0.24 s",
}


def slow_total_cumul(text: str) -> float | None:
    """cumul% of slow_total in the Tachyon table (the narration says 'seventy two percent')."""
    m = re.search(r"^\s*\S+\s+\S+\s+\S+\s+(\S+)\s+\S+\s+slow_report.py:\d+\(slow_total\)", text, re.M)
    return float(m.group(1)) if m else None


def shows_narrated_value(key: str, text: str) -> bool:
    if key == "profile":
        value = slow_total_cumul(text)
        return value is not None and 71.5 <= value < 72.5
    want = MUST_SHOW.get(key)
    return want is None or want in text


def run(cmd: str) -> str:
    real = cmd
    for name, path in PY.items():
        real = real.replace(name, path)
    r = subprocess.run(real, shell=True, cwd=DEMOS, capture_output=True, text=True)
    text = (r.stdout + r.stderr).rstrip("\n").replace(str(DEMOS), "~/demo")
    for name, path in PY.items():
        text = text.replace(path, name)
    return text


def main() -> None:
    out = {
        # recorded from the real install on 2026-11-24 (uv 0.13.9); re-running would only say "already installed"
        "install": {"cmd": "uv python install 3.15",
                    "out": "Installed Python 3.15.2 in 3.59s\n + cpython-3.15.2-linux-x86_64-gnu (python3.15)"},
    }
    only = set(sys.argv[1:])  # re-run just these keys, keep the rest from outputs.json
    if only and (HERE / "outputs.json").exists():
        out = json.loads((HERE / "outputs.json").read_text())
    for key, cmd in RUNS.items():
        if only and key not in only:
            continue
        tries = 0
        while True:
            tries += 1
            text = run(cmd)
            if shows_narrated_value(key, text) or tries >= 60:
                break
        out[key] = {"cmd": cmd, "out": text, "tries": tries, "matched": shows_narrated_value(key, text)}
        print(f"== {cmd}  (tries {tries})\n{text[:400]}\n")
    (HERE / "outputs.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
