"""Listen to a rendered film with Earshot: measure, timeline and critique.

This is the offline trial step from the sound-design slice. It runs Earshot's
listening commands against one finished film and writes their JSON next to a
short text summary. It downloads nothing and spends nothing; the models must
already be cached (`earshot doctor --load` once).

    uv run python scripts/earshot_trial.py videos/.../day_master_deck.mp4
    uv run python scripts/earshot_trial.py <film> --out qa/earshot
    uv run python scripts/earshot_trial.py <film> --no-critique

The Earshot command comes from `assets.providers.earshot_sfx.provider_options.
command` (so `config.local.yaml` is honoured), then `EARSHOT_COMMAND`, then
`python -m earshot`. Treat `critique` as advice: its reference set is product
launch films, not every format we make.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

CHECKS = ("measure", "timeline", "critique")


def configured_command() -> list[str]:
    command = _settings_command()
    if command:
        return command
    override = os.environ.get("EARSHOT_COMMAND")
    if override:
        return shlex.split(override)
    return [sys.executable, "-m", "earshot"]


def _settings_command() -> list[str] | None:
    try:
        from video_editor_config import Settings
    except ImportError:
        return None
    providers = Settings.load().config.assets.providers
    provider = providers.get("earshot_sfx")
    command = (provider.provider_options or {}).get("command") if provider else None
    if not command:
        return None
    return [str(part) for part in command]


def run_check(command: Sequence[str], check: str, film: Path, out: Path) -> dict[str, object]:
    target = out / f"{check}.json"
    invocation = [*command, check, str(film), "--json", "--out", str(target)]
    print(f"$ {' '.join(invocation)}", flush=True)
    completed = subprocess.run(invocation, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()[-5:]
        raise SystemExit(f"earshot {check} failed ({completed.returncode}):\n" + "\n".join(detail))
    if not target.is_file():
        raise SystemExit(f"earshot {check} wrote no JSON to {target}")
    return cast(dict[str, object], json.loads(target.read_text()))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("film", type=Path, help="rendered film or audio file")
    parser.add_argument("--out", type=Path, help="output directory (default: <film dir>/earshot)")
    parser.add_argument("--no-critique", action="store_true", help="skip the launch-film scorecard")
    args = parser.parse_args(argv)

    film = args.film.resolve()
    if not film.is_file():
        raise SystemExit(f"no such film: {film}")
    checks = CHECKS if not args.no_critique else CHECKS[:2]
    out = (args.out or film.parent / "earshot").resolve()
    out.mkdir(parents=True, exist_ok=True)

    command = configured_command()
    if shutil.which(command[0]) is None and not Path(command[0]).is_file():
        raise SystemExit(
            f"Earshot command not found: {command[0]}\n"
            "Install it in its own environment and set "
            "assets.providers.earshot_sfx.provider_options.command, "
            "or export EARSHOT_COMMAND='python -m earshot'."
        )

    results = {check: run_check(command, check, film, out) for check in checks}
    for check, result in results.items():
        print(f"\n{check}: {result.get('summary', '(no summary)')}")
    print(f"\nwrote {', '.join(str(out / f'{c}.json') for c in checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
