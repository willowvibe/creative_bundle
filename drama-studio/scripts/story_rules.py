"""Story gate for drama episodes: the five rules that keep people watching.

Adapted from Andy Lo's micro-drama workflow ("Micro Dramas Made $11B", 2026-10-31)
and written down in docs/DRAMA_STORY_AND_CONTINUITY.md.

    usage: story_rules.py <episode>            -> review/story_rules.md + .json, advisory
           story_rules.py <episode> --strict   -> exit 1 when a rule fails
           story_rules.py <episode> --json     -> print the JSON and write nothing

The five rules:

1. Question first   - the opener makes the viewer ask "why" and withholds it.
2. Make us care     - the lead does something kind and is punished for it.
3. Viewer knows more- the audience sees a danger the character cannot.
4. Every beat turns - each beat changes the situation (but / therefore, not and then).
5. Payoff, reopen   - the win lands, a bigger question opens, then stop.

Rules 1, 2, 4 and 5 are checked mechanically against `shots.json` (and
`v2/visuals.json` when the episode has one). Rule 3 is genuinely a judgement
about staging, so it is reported for review rather than passed automatically.

This is a *story* gate, not a render gate: it never looks at pixels, and it is
advisory until a show sets `--strict` in its own script. It is deliberately kept
out of `drama_review.py`'s approval hashes — an episode's text can pass the
review gate and still be a bad story.
"""

from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Words that open a question when they are the first word of a line.
_INTERROGATIVE = {
    "what",
    "why",
    "how",
    "who",
    "whose",
    "where",
    "when",
    "which",
    "did",
    "do",
    "does",
    "is",
    "are",
    "was",
    "were",
    "can",
    "could",
    "will",
    "would",
    "should",
    "have",
    "has",
    "am",
}

#: A selfless act: the beat that makes the audience side with someone. Kept to
#: phrases that name an act — "take care" is a farewell, not a kindness beat.
_KINDNESS = {
    "save",
    "saves",
    "saved",
    "saving",
    "protect",
    "protects",
    "protected",
    "help",
    "helps",
    "helped",
    "defend",
    "defends",
    "defended",
    "rescue",
    "rescues",
    "rescued",
    "sacrifice",
    "sacrifices",
    "sacrificed",
    "shield",
    "shields",
    "shielded",
    "guard",
    "guards",
    "guarded",
    "stand up for",
    "stood up for",
    "cares for",
    "cared for",
    "caring for",
    "takes care of",
    "took care of",
    "gives up",
    "gave up",
    "risks his life",
    "risks her life",
    "puts himself in danger",
    "puts herself in danger",
}

#: Staging where the audience sees what the character cannot.
_IRONY = {
    "unaware",
    "unseen",
    "unnoticed",
    "hidden",
    "behind him",
    "behind her",
    "doesn't know",
    "does not know",
    "doesn't see",
    "cannot see",
    "can't see",
    "out of sight",
    "in the shadows",
    "watching",
    "approaching",
    "closing in",
    "lurking",
    "overheard",
    "just out of frame",
}

#: Ways an ending promises the next episode without explaining it.
_FORWARD = {
    "next",
    "will",
    "won't",
    "come back",
    "returns",
    "soon",
    "not the end",
    "does not end",
    "doesn't end",
    "remembers",
    "awaits",
    "waiting",
    "promise",
    "to be continued",
    "one day",
}

_POSITIVE = {
    "warm",
    "loving",
    "love",
    "proud",
    "affectionate",
    "calm",
    "happy",
    "joy",
    "relief",
    "relieved",
    "grateful",
    "hope",
    "hopeful",
    "gentle",
    "playful",
    "tender",
    "content",
    "peaceful",
    "safe",
    "comfort",
    "smile",
    "smiling",
    "amused",
    "cheerful",
    "determined",
    "blessed",
}
_NEGATIVE = {
    "grave",
    "worried",
    "worry",
    "dread",
    "crying",
    "cry",
    "weeping",
    "sobbing",
    "broken",
    "heartbroken",
    "terrified",
    "scared",
    "fear",
    "afraid",
    "shock",
    "shocked",
    "panic",
    "alarm",
    "unease",
    "uneasy",
    "despair",
    "grief",
    "angry",
    "furious",
    "rage",
    "stern",
    "pain",
    "sad",
    "devastated",
    "pleading",
    "urgent",
    "tense",
    "suspense",
    "horror",
    "scream",
}

_ELLIPSIS = 160


@dataclass
class Beat:
    """One scene of the episode, as the story gate sees it."""

    scene: int
    shots: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    cams: list[str] = field(default_factory=list)
    action: str = ""
    line: str = ""
    emotions: list[str] = field(default_factory=list)
    #: Optional author annotation: "up", "down", "worse", "better" or "twist".
    turn: str = ""

    @property
    def valence(self) -> int:
        """+1 hopeful, -1 dark, 0 unresolved — read from the declared emotions."""
        score = 0
        for emotion in self.emotions:
            words = set(re.findall(r"[a-z']+", emotion.lower()))
            score += len(words & _POSITIVE) - len(words & _NEGATIVE)
        return (score > 0) - (score < 0)


@dataclass
class RuleResult:
    number: int
    name: str
    status: str  # "pass" | "fail" | "review"
    detail: str
    evidence: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status == "pass"


def _short(text: str, limit: int = _ELLIPSIS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def load_shots(episode: Path) -> list[dict[str, Any]]:
    spec = json.loads((episode / "shots.json").read_text(encoding="utf-8"))
    return list(spec.get("shots") or [])


def load_visuals(episode: Path) -> list[dict[str, Any]]:
    path = episode / "v2" / "visuals.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("visuals") or [])


def _snippet(text: str, word: str, width: int = 120) -> str:
    """Return the text around ``word`` so the report can show why a rule fired."""
    index = text.lower().find(word.lower())
    if index < 0:
        return _short(text, width)
    start = max(0, index - width // 3)
    return _short(text[start : start + width], width)


def _shot_text(shot: dict[str, Any]) -> str:
    for key in ("line", "vo"):
        item = shot.get(key) or {}
        if item.get("en"):
            return str(item["en"])
    return ""


def _shot_emotion(shot: dict[str, Any]) -> str:
    for key in ("line", "vo"):
        item = shot.get(key) or {}
        if item.get("emotion"):
            return str(item["emotion"])
    return str(shot.get("emotion") or "")


def build_beats(shots: list[dict[str, Any]], visuals: list[dict[str, Any]]) -> list[Beat]:
    """Group the shot list (and the visual plan) into scene-level beats."""
    beats: dict[int, Beat] = {}
    for shot in shots:
        scene = int(shot.get("scene") or 1)
        beat = beats.setdefault(scene, Beat(scene=scene))
        if shot.get("id"):
            beat.shots.append(str(shot["id"]))
        line = _shot_text(shot)
        if line:
            beat.line = (beat.line + " " + line).strip()
        emotion = _shot_emotion(shot)
        if emotion:
            beat.emotions.append(emotion)
        if shot.get("turn"):
            beat.turn = str(shot["turn"])

    for visual in visuals:
        scene = int(visual.get("scene") or 1)
        beat = beats.setdefault(scene, Beat(scene=scene))
        if visual.get("kind"):
            beat.kinds.append(str(visual["kind"]))
        if visual.get("cam"):
            beat.cams.append(str(visual["cam"]))
        prompt = str(visual.get("prompt") or "")
        action = str(visual.get("expr") or "") or prompt
        if action:
            beat.action = (beat.action + " " + action).strip()
        if visual.get("turn"):
            beat.turn = str(visual["turn"])
    return [beats[key] for key in sorted(beats)]


def _first_sentence(text: str) -> str:
    match = re.match(r"(.+?[.!?])(\s|$)", text.strip())
    return match.group(1) if match else text.strip()


def _question_opener(beats: list[Beat]) -> RuleResult:
    window = max(1, math.ceil(len(beats) * 0.15))
    for beat in beats[:window]:
        if "?" in beat.line or "?" in beat.action:
            return RuleResult(
                1,
                "Question first",
                "pass",
                f"the opener asks a question in scene {beat.scene}",
                [_short(beat.line or beat.action)],
            )
        first = re.match(r"\W*([a-z']+)", beat.line.strip().lower())
        if first and first.group(1) in _INTERROGATIVE:
            return RuleResult(
                1,
                "Question first",
                "pass",
                f"the opener starts with '{first.group(1)}' in scene {beat.scene}",
                [_short(beat.line)],
            )
    if not beats:
        return RuleResult(1, "Question first", "fail", "the episode has no scenes")
    opener = beats[0].line or beats[0].action
    return RuleResult(
        1,
        "Question first",
        "fail",
        f"the first {window} scene(s) never ask a question",
        [_short(opener)] if opener else [],
    )


def _kindness_beat(beats: list[Beat]) -> RuleResult:
    half = max(1, math.ceil(len(beats) / 2))
    for beat in beats[:half]:
        for source in (beat.line, beat.action):
            if not source:
                continue
            word = next(
                (w for w in sorted(_KINDNESS) if re.search(rf"\b{re.escape(w)}\b", source.lower())),
                None,
            )
            if word:
                return RuleResult(
                    2,
                    "Make us care",
                    "pass",
                    f"selfless beat in scene {beat.scene} ('{word}')",
                    [_snippet(source, word)],
                )
    return RuleResult(
        2, "Make us care", "fail", f"no kind, fair or selfless act in the first {half} scene(s)"
    )


def _viewer_knows_more(beats: list[Beat]) -> RuleResult:
    for beat in beats:
        haystack = f"{beat.action} {' '.join(beat.kinds)}"
        word = next((w for w in sorted(_IRONY) if w in haystack.lower()), None)
        if word:
            return RuleResult(
                3,
                "Viewer knows more",
                "pass",
                f"scene {beat.scene} shows what the character cannot see ('{word}')",
                [_snippet(haystack, word)],
            )
    return RuleResult(
        3,
        "Viewer knows more",
        "review",
        "no staging cue found; confirm by eye that the audience sees a danger the character does not",
    )


def _every_beat_turns(beats: list[Beat]) -> RuleResult:
    if len(beats) < 2:
        return RuleResult(4, "Every beat turns", "review", "not enough scenes to judge a turn")

    # A run of three or more scenes in the same direction is the "and then"
    # failure. Two in a row is ordinary escalation (bad → worse), so it passes.
    runs: list[list[Beat]] = []
    run = [beats[0]]
    for beat in beats[1:]:
        previous = run[-1]
        if beat.valence and previous.valence and beat.valence == previous.valence:
            run.append(beat)
        else:
            if len(run) >= 3:
                runs.append(run)
            run = [beat]
    if len(run) >= 3:
        runs.append(run)

    evidence = [
        f"scenes {r[0].scene}–{r[-1].scene} stay "
        f"{'hopeful' if r[0].valence > 0 else 'dark'} for {len(r)} beats"
        for r in runs
    ]
    unannotated = [b.scene for b in beats if not b.turn]
    if unannotated:
        detail = f"{len(unannotated)} scene(s) declare no turn"
        if evidence:
            detail += f"; {len(runs)} same-direction run(s) to confirm"
        return RuleResult(4, "Every beat turns", "review", detail, evidence)

    if runs:
        return RuleResult(
            4,
            "Every beat turns",
            "fail",
            f"{len(runs)} run(s) of three or more beats in one direction",
            evidence,
        )
    return RuleResult(4, "Every beat turns", "pass", "every declared turn changes direction")


def _payoff_then_reopen(beats: list[Beat]) -> RuleResult:
    if not beats:
        return RuleResult(5, "Payoff then reopen", "fail", "the episode has no scenes")
    last = beats[-1]
    haystack = f"{last.line} {last.action}".lower()
    if "?" in haystack:
        return RuleResult(
            5,
            "Payoff then reopen",
            "pass",
            f"the last scene closes on a question (scene {last.scene})",
            [_short(last.line or last.action)],
        )
    word = next((w for w in sorted(_FORWARD) if w in haystack), None)
    if word:
        return RuleResult(
            5,
            "Payoff then reopen",
            "pass",
            f"the last scene promises more ('{word}', scene {last.scene})",
            [_short(last.line or last.action)],
        )
    return RuleResult(
        5,
        "Payoff then reopen",
        "fail",
        f"scene {last.scene} ends without a question or a forward hook",
        [_short(last.line or last.action)],
    )


RULES = (
    _question_opener,
    _kindness_beat,
    _viewer_knows_more,
    _every_beat_turns,
    _payoff_then_reopen,
)


def evaluate(episode: Path) -> tuple[list[RuleResult], list[Beat]]:
    beats = build_beats(load_shots(episode), load_visuals(episode))
    return [rule(beats) for rule in RULES], beats


def _markdown(episode: Path, results: list[RuleResult], beats: list[Beat]) -> str:
    icon = {"pass": "PASS", "fail": "FAIL", "review": "REVIEW"}
    rows = ["| # | Rule | Verdict | Why |", "|---|---|---|---|"]
    for r in results:
        rows.append(f"| {r.number} | {r.name} | **{icon[r.status]}** | {_short(r.detail, 200)} |")

    body = [
        f"# Story check: {episode.name}",
        "",
        "Judged on `shots.json` (and `v2/visuals.json` when present). "
        "Rules 1, 2, 4 and 5 are mechanical; rule 3 is a staging judgement for the reviewer.",
        "",
        "## Verdicts",
        "",
        *rows,
        "",
    ]

    for r in results:
        body += [f"## {r.number}. {r.name}", f"**{icon[r.status]}** — {r.detail}", ""]
        for line in r.evidence:
            body.append(f"> {line}")
        if r.evidence:
            body.append("")

    body += [
        "## Beat table",
        "",
        "| # | Scene | Shots | Kind | Camera | Face / action | Line | Turn |",
        "|---|---|---|---|---|---|---|---|",
    ]
    previous = None
    for i, beat in enumerate(beats, 1):
        if beat.turn:
            turn = beat.turn
        elif previous is None:
            turn = "—"
        elif beat.valence and previous.valence and beat.valence != previous.valence:
            turn = "changes"
        elif beat.valence and previous.valence:
            turn = "same"
        else:
            turn = "unresolved"
        body.append(
            f"| {i} | {beat.scene} | {', '.join(beat.shots) or '—'} | {', '.join(dict.fromkeys(beat.kinds)) or '—'} "
            f"| {_short(', '.join(dict.fromkeys(beat.cams)), 60) or '—'} "
            f"| {_short(_first_sentence(beat.action), 90) or '—'} "
            f"| {_short(beat.line, 120) or '—'} | {turn} |"
        )
        previous = beat
    return "\n".join(body) + "\n"


def write_report(
    episode: Path,
    results: list[RuleResult] | None = None,
    beats: list[Beat] | None = None,
) -> tuple[Path, Path]:
    if results is None or beats is None:
        results, beats = evaluate(episode)
    out = episode / "review" / "story_rules.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(_markdown(episode, results, beats), encoding="utf-8")

    data = {
        "episode": episode.name,
        "rules": [
            {
                "number": r.number,
                "name": r.name,
                "status": r.status,
                "detail": r.detail,
                "evidence": r.evidence,
            }
            for r in results
        ],
        "beats": [
            {
                "scene": b.scene,
                "shots": b.shots,
                "kinds": b.kinds,
                "cams": b.cams,
                "action": b.action,
                "line": b.line,
                "valence": b.valence,
            }
            for b in beats
        ],
        "failed": [r.number for r in results if r.status == "fail"],
        "review": [r.number for r in results if r.status == "review"],
    }
    json_out = episode / "review" / "story_rules.json"
    json_out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return out, json_out


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}
    if len(args) != 1 or flags - {"--strict", "--json"}:
        print(__doc__)
        return 2
    episode = Path(args[0]).resolve()
    if "--json" in flags:
        results, beats = evaluate(episode)
        print(
            json.dumps(
                {
                    "episode": episode.name,
                    "rules": [
                        {"number": r.number, "name": r.name, "status": r.status, "detail": r.detail}
                        for r in results
                    ],
                    "beats": [
                        {"scene": b.scene, "valence": b.valence, "line": _short(b.line)}
                        for b in beats
                    ],
                },
                indent=1,
                ensure_ascii=False,
            )
        )
        failed = [r for r in results if r.status == "fail"]
        return 1 if failed and "--strict" in flags else 0

    results, beats = evaluate(episode)
    md, js = write_report(episode, results, beats)
    for r in results:
        print(f"{r.number}. {r.name}: {r.status.upper()} — {r.detail}")
    print(f"\n{md}\n{js}")
    failed = [r for r in results if r.status == "fail"]
    return 1 if failed and "--strict" in flags else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
