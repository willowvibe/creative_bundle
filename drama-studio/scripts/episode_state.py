"""Series state ledger for drama episodes: who knows what, and who holds which prop.

The video workflow this follows ("Micro Dramas Made $11B", 2026-10-31) keeps a
show-notes file that Claude reads before each episode and rewrites after, so
episode nine still knows who learned the secret in episode three. The character
bibles under `assets/characters/*/bible.md` cover identity, wardrobe and voice;
this file covers *state* — the facts and objects a scene can contradict.

    usage: episode_state.py check  <episode> [--state PATH] [--strict]
           episode_state.py update <episode> [--state PATH]

`check` reads `shots.json` and `v2/visuals.json` and reports:

    FAIL  a prop or fact appears before the episode that introduces it
    WARN  a speaker is not declared in the ledger (add them, or fix the name)
    WARN  a character who does not know a fact speaks it after the reveal

`update` appends this episode's characters, props and hook to the ledger. It
never invents knowledge: who learned what stays a human edit.

The state file defaults to `<episode>/state.json`; pass `--state` to keep one
ledger for the whole series. Schema: docs/DRAMA_STORY_AND_CONTINUITY.md.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

_NARRATOR = {"narrator", ""}


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else default


def state_path(episode: Path, override: str | None) -> Path:
    return Path(override).resolve() if override else episode / "state.json"


def _name_rank(name: str) -> int:
    match = re.search(r"(\d+)\s*$", name)
    return int(match.group(1)) if match else 10**6


def _episode_rank(state: dict[str, Any], episode: str) -> int:
    order = state.get("episode_order") or []
    if episode in order:
        return order.index(episode)
    return _name_rank(episode)


def _text_of(episode: Path) -> list[str]:
    """Every searchable string in the episode: lines (te + en) and visual prompts."""
    out: list[str] = []
    shots_path = episode / "shots.json"
    if shots_path.exists():
        for shot in json.loads(shots_path.read_text(encoding="utf-8")).get("shots") or []:
            for key in ("line", "vo"):
                item = shot.get(key) or {}
                out += [str(item.get("en") or ""), str(item.get("te") or "")]
            out.append(str(shot.get("action") or ""))
    visuals_path = episode / "v2" / "visuals.json"
    if visuals_path.exists():
        for visual in json.loads(visuals_path.read_text(encoding="utf-8")).get("visuals") or []:
            out.append(str(visual.get("prompt") or ""))
            out.append(str(visual.get("expr") or ""))
    return [text for text in out if text]


def _speakers(episode: Path) -> set[str]:
    path = episode / "shots.json"
    if not path.exists():
        return set()
    out: set[str] = set()
    for shot in json.loads(path.read_text(encoding="utf-8")).get("shots") or []:
        who = str(shot.get("speaker") or "")
        for key in ("line", "vo"):
            who = who or str((shot.get(key) or {}).get("who") or "")
        if who:
            out.add(who)
    return out


def _matches(texts: list[str], aliases: list[str]) -> list[str]:
    """Return the texts that mention any alias (case-insensitive whole phrase)."""
    hits = []
    for text in texts:
        lowered = text.lower()
        if any(alias.lower() in lowered for alias in aliases if alias):
            hits.append(text)
    return hits


def check(episode: Path, state_file: Path) -> list[dict[str, Any]]:
    """Return issues as ``{"severity", "message"}`` dicts; empty means clean."""
    state = _load_json(state_file, {})
    if not state:
        return [{"severity": "fail", "message": f"no ledger at {state_file}; run `update` first"}]

    issues: list[dict[str, Any]] = []
    texts = _text_of(episode)
    rank = _episode_rank(state, episode.name)

    characters = state.get("characters") or {}
    known_names = {name.lower() for name in characters}
    aliases = {alias.lower() for c in characters.values() for alias in c.get("aliases") or []}
    for speaker in sorted(_speakers(episode) - _NARRATOR):
        if speaker.lower() not in known_names | aliases:
            issues.append(
                {
                    "severity": "warn",
                    "message": f"speaker '{speaker}' is not declared in the ledger",
                }
            )

    for prop, spec in (state.get("props") or {}).items():
        introduced = spec.get("introduced_in") or ""
        if introduced and rank < _episode_rank(state, introduced):
            hits = _matches(texts, [prop, *(spec.get("aliases") or [])])
            if hits:
                issues.append(
                    {
                        "severity": "fail",
                        "message": f"prop '{prop}' appears before {introduced}: {hits[0][:100]}",
                    }
                )

    for fact, spec in (state.get("facts") or {}).items():
        revealed = spec.get("revealed_in") or ""
        hits = _matches(texts, [fact, *(spec.get("aliases") or [])])
        if not hits:
            continue
        if revealed and rank < _episode_rank(state, revealed):
            issues.append(
                {
                    "severity": "fail",
                    "message": f"fact '{fact}' is spoken before {revealed}: {hits[0][:100]}",
                }
            )
            continue
        knowers = {name.lower() for name in spec.get("known_by") or []}
        if revealed and knowers:
            for speaker in sorted(_speakers(episode) - _NARRATOR):
                if speaker.lower() not in knowers and speaker.lower() in known_names:
                    issues.append(
                        {
                            "severity": "warn",
                            "message": f"'{speaker}' speaks '{fact}' but is not marked as knowing it",
                        }
                    )
    return issues


def update(episode: Path, state_file: Path) -> dict[str, Any]:
    """Record this episode's cast, props and closing hook in the ledger."""
    state = _load_json(
        state_file,
        {
            "series": re.sub(r"-ep\d+.*$", "", episode.name) or episode.parent.name,
            "characters": {},
            "props": {},
            "facts": {},
            "episodes": {},
            "episode_order": [],
        },
    )
    for key in ("characters", "props", "facts", "episodes"):
        state.setdefault(key, {})
    order = state.setdefault("episode_order", [])

    name = episode.name
    if name not in order:
        order.append(name)
        order.sort(key=_name_rank)

    texts = _text_of(episode)
    props = sorted(
        prop
        for prop, spec in (state.get("props") or {}).items()
        if _matches(texts, [prop, *(spec.get("aliases") or [])])
    )
    facts = sorted(
        fact
        for fact, spec in (state.get("facts") or {}).items()
        if _matches(texts, [fact, *(spec.get("aliases") or [])])
    )

    hook = ""
    shots_path = episode / "shots.json"
    if shots_path.exists():
        for shot in reversed(json.loads(shots_path.read_text(encoding="utf-8")).get("shots") or []):
            for key in ("line", "vo"):
                hook = str((shot.get(key) or {}).get("en") or "").strip()
                if hook:
                    break
            if hook:
                break

    state["episodes"][name] = {
        "characters": sorted(_speakers(episode) - _NARRATOR),
        "props": props,
        "facts": facts,
        "hook": hook[:240],
    }
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return dict(state["episodes"][name])


def _parse(argv: list[str]) -> tuple[str, Path, Path, bool] | None:
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}
    if len(args) < 2 or args[0] not in ("check", "update"):
        return None
    state_arg = None
    if "--state" in argv:
        index = argv.index("--state")
        if index + 1 >= len(argv):
            return None
        state_arg = argv[index + 1]
        args = [a for a in args if a != state_arg]
    episode = Path(args[1]).resolve()
    return args[0], episode, state_path(episode, state_arg), "--strict" in flags


def main(argv: list[str]) -> int:
    parsed = _parse(argv)
    if parsed is None:
        print(__doc__)
        return 2
    command, episode, state_file, strict = parsed
    if command == "update":
        record = update(episode, state_file)
        print(
            f"{episode.name}: {len(record['characters'])} character(s), "
            f"{len(record['props'])} prop(s), {len(record['facts'])} fact(s) -> {state_file}"
        )
        return 0

    issues = check(episode, state_file)
    if not issues:
        print(f"{episode.name}: ledger clean ({state_file})")
        return 0
    for issue in issues:
        print(f"{issue['severity'].upper()}: {issue['message']}")
    failed = [i for i in issues if i["severity"] == "fail"]
    return 1 if failed and strict else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
