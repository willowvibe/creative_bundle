"""Blind second read of an episode's Telugu dialogue by Sarvam, run before the dialogue gate.

usage: dialogue_check.py <episode_dir> [--runs 3] [--min-votes 2] [--cast FILE]

Sends every spoken line of shots.json (id, speaker, emotion, English, Telugu) to sarvam-105b-conversations --runs
times and keeps the lines flagged in at least --min-votes runs. On Ep1's original script, lines flagged in 2 of 3
runs caught 7 of the reviewer's 15 fixes with 2 false alarms. Its rewrites are unreliable and it misses elder and
devotional register: the rewrites are hints only, Claude rewrites the flagged lines, and the family review stays the gate.

Cast text comes from --cast, else <episode>/cast.md, else just the speaker names. Writes review/dialogue_check.md and
review/dialogue_check.json (raw runs). Needs SARVAM_API in the environment or video-editor/.env.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from drama_review import lines  # noqa: E402

ENV = Path(__file__).resolve().parents[1] / ".env"
URL = "https://api.sarvam.ai/v1/chat/completions"
MODEL = "sarvam-105b-conversations"
MAX_TOKENS = 8000  # higher values make the API answer HTTP 400

PROMPT = """You are a senior Telugu dialogue writer for TV serials and devotional dramas, and a native speaker from Andhra/Telangana.
Below are the dialogue lines of one episode, in story order. Each has an id, the speaker, the intended emotion, the English meaning, and the Telugu line.

{cast}

Review every Telugu line the way an elder in a Telugu family would when listening to it on screen. Flag a line when it:
- sounds unnatural, bookish, clipped or "translated from English" rather than how people really speak,
- uses the wrong respect form or relationship word for who is speaking to whom (e.g. mother to son, nurse to a mother, devotee to god),
- leaves out words a native speaker would naturally say, so it feels incomplete or abrupt,
- does not fit the emotion or the moment.
Do NOT flag a line only because you would phrase it differently; flag it only if a native viewer would notice something off.
Any chant line (a fixed mantra) is never flagged.

Return ONLY a JSON array, one object per FLAGGED line, no other text:
[{{"id": "...", "problem": "one short sentence in English", "rewrite": "the corrected Telugu line"}}]
Return [] if nothing needs changing.

Lines:
{lines}"""


def _api_key() -> str:
    key = os.environ.get("SARVAM_API", "")
    if not key and ENV.exists():
        for raw in ENV.read_text().splitlines():
            name, sep, value = raw.partition("=")
            if sep and name.strip() == "SARVAM_API":
                key = value.strip().strip("'\"")
    if not key:
        raise SystemExit("SARVAM_API must be set in the environment or video-editor/.env")
    return key


def lines_block(items: list[dict]) -> str:
    return "\n".join(
        f"{p['id']} | {p['who']} | {p['emotion']} | {p['en']} | {p['te']}" for p in items
    )


def cast_text(episode: Path, items: list[dict], cast_file: Path | None) -> str:
    path = cast_file or (episode / "cast.md")
    if path.exists():
        return "Cast:\n" + path.read_text().strip()
    speakers = sorted({p["who"] for p in items})
    return "Speakers: " + ", ".join(speakers)


def call(prompt: str, key: str) -> str:
    """One chat completion; returns the reply text."""
    body = json.dumps(
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": MAX_TOKENS,
            "temperature": 0.2,
        }
    ).encode()
    req = urllib.request.Request(
        URL, body, {"api-subscription-key": key, "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=900) as res:
            data = json.load(res)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {e.read().decode()[:300]}") from e
    return data["choices"][0]["message"]["content"] or ""


def parse_flags(text: str) -> list[dict] | None:
    """The `[...]` array in a reply (it may be wrapped in other text); None when there is no parsable array."""
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return None
    try:
        flags = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return (
        [f for f in flags if isinstance(f, dict) and f.get("id")]
        if isinstance(flags, list)
        else None
    )


def normalise(flags: list[dict], ids: list[str]) -> list[dict]:
    """Map ids by the part before the first underscore (the model drops suffixes); drop ids not in the episode."""
    by_shot = {i.split("_")[0]: i for i in ids}
    out = []
    for f in flags:
        real = by_shot.get(str(f["id"]).split("_")[0])
        if real:
            out.append({**f, "id": real})
    return out


def tally(runs: list[list[dict]], min_votes: int) -> dict[str, list[tuple[int, dict]]]:
    """line id -> [(run number, flag)] for lines flagged in at least min_votes distinct runs."""
    votes: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for n, flags in enumerate(runs, 1):
        seen = set()
        for f in flags:
            if f["id"] not in seen:  # a line repeated within one run still counts one vote
                seen.add(f["id"])
                votes[f["id"]].append((n, f))
    return {i: v for i, v in votes.items() if len(v) >= min_votes}


def report(kept: dict, items: list[dict], runs: int, min_votes: int, failed: list[int]) -> str:
    by_id = {p["id"]: p for p in items}
    out = [
        "# Dialogue check (Sarvam second read)",
        "",
        f"Flagged in at least {min_votes} of {runs} runs. The rewrites below are hints only and are unreliable: "
        "Claude rewrites the flagged lines itself. This check misses elder and devotional register, "
        "so the family review stays the gate.",
        "",
    ]
    if failed:
        out += [
            f"Runs with no parsable reply (counted as zero flags): {', '.join(map(str, failed))}",
            "",
        ]
    if not kept:
        out.append("No line reached the vote threshold.")
    for lid in sorted(kept, key=lambda i: [p["id"] for p in items].index(i)):
        p = by_id[lid]
        out += [f"## {lid} ({p['who']}) - {len(kept[lid])}/{runs}", "", f"Current: {p['te']}", ""]
        for n, f in kept[lid]:
            out += [f"- run {n}: {f.get('problem', '')}", f"  hint: {f.get('rewrite', '')}"]
        out.append("")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("episode", type=Path)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--min-votes", type=int, default=2)
    ap.add_argument("--cast", type=Path)
    args = ap.parse_args()

    items = lines(args.episode)
    ids = [p["id"] for p in items]
    key = _api_key()
    prompt = PROMPT.format(cast=cast_text(args.episode, items, args.cast), lines=lines_block(items))

    raw, runs, failed = [], [], []
    for n in range(1, args.runs + 1):
        try:
            text = call(prompt, key)
        except (RuntimeError, urllib.error.URLError, TimeoutError) as e:
            text = ""
            print(f"run {n}: request failed: {e}", file=sys.stderr)
        flags = parse_flags(text)
        if flags is None:
            failed.append(n)
            print(f"run {n}: no parsable JSON array in the reply", file=sys.stderr)
        raw.append({"run": n, "text": text, "flags": flags})
        runs.append(normalise(flags or [], ids))

    kept = tally(runs, args.min_votes)
    review = args.episode / "review"
    review.mkdir(exist_ok=True)
    (review / "dialogue_check.md").write_text(
        report(kept, items, args.runs, args.min_votes, failed)
    )
    (review / "dialogue_check.json").write_text(
        json.dumps(
            {"model": MODEL, "runs": raw, "kept": sorted(kept)}, ensure_ascii=False, indent=1
        )
    )
    print(
        f"dialogue_check: {len(kept)} of {len(items)} lines flagged in >= {args.min_votes}/{args.runs} runs"
        f" ({len(failed)} runs unparsable) -> {review / 'dialogue_check.md'}"
    )


if __name__ == "__main__":
    main()
