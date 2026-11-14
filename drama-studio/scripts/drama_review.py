"""User review gates for drama episodes, in the order the user set (2026-10-30):

    script -> dialogue -> voices (audio) -> images -> videos -> lipsync (+ SFX, light instruments) -> final stitching

Each stage must be approved before any work of the next stage is paid for; `final` is the stitched film itself
(its `*.web.mp4`), so it is reviewed on the board like any other stage and is the last gate before delivery.

usage: drama_review.py dialogue <episode>        -> review/dialogue.md: every line (speaker, Telugu, English, emotion)
       drama_review.py voices <episode>          -> review/voices.html: each take with a player and its intended emotion
       drama_review.py reel <episode>            -> review/dialogue_audio_review.mp4: every line's card + take
       drama_review.py sheet images|videos|lipsync|final <episode>  -> review/<stage>.html: every file of that stage
       drama_review.py storyboard <episode>      -> review/storyboard.html: picture, camera, action, line per shot
       drama_review.py approve <stage> <episode>
       drama_review.py status <episode>

An approval stores a hash of what the user saw (script text, line text, audio or media bytes), so editing a line or
regenerating a take, frame or clip reopens the gate for that item. Episode scripts call `require(episode, stage)` before
spending money on the next stage.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from glob import glob
from pathlib import Path

STAGES = ("script", "dialogue", "voices", "images", "videos", "lipsync", "final")

#: Stages judged on files rather than on shots.json lines: what the user looks at for each.
STAGE_FILES = {
    "script": ["script.md"],
    "images": [
        "refs/*.png",
        "refs/*.jpg",
        "frames/*.png",
        "frames/*.jpg",
    ],  # character references, then frames
    "videos": ["clips/motion/*.mp4"],
    "lipsync": ["clips/lipsync/*.mp4", "audio/sfx/*.wav", "audio/music/*.wav"],
    # the stitched film (the light copy reviewers can actually stream), then its thumbnails and Shorts
    "final": ["*.web.mp4", "promo/*.jpg", "promo/*.mp4"],
}


class ReviewPending(SystemExit):
    pass


def lines(episode: Path) -> list[dict]:
    """Every spoken item in shots.json: narrator VO, character lines, and pre-recorded audio such as the mantra."""
    spec = json.loads((episode / "shots.json").read_text())
    out = []
    for s in spec["shots"]:
        for key in ("vo", "line"):
            item = s.get(key)
            if not item:
                continue
            who = item.get("who") or s.get("speaker", "")
            audio = (
                s.get("audio")
                if key == "line" and s.get("audio")
                else f"audio/lines/{s['id']}_{key}_{who}.wav"
            )
            out.append(
                {
                    "id": f"{s['id']}_{key}",
                    "who": who,
                    "te": item.get("te", ""),
                    "en": item.get("en", ""),
                    "emotion": item.get("emotion") or s.get("emotion", ""),
                    "audio": audio,
                }
            )
    return out


def stage_files(episode: Path, stage: str) -> list[Path]:
    return sorted({Path(f) for pattern in STAGE_FILES[stage] for f in glob(str(episode / pattern))})


def _digest(episode: Path, stage: str) -> dict[str, str]:
    if stage in STAGE_FILES:
        files = stage_files(episode, stage)
        if not files:
            return {STAGE_FILES[stage][0]: "missing"}
        return {
            str(f.relative_to(episode)): hashlib.sha256(f.read_bytes()).hexdigest() for f in files
        }
    if stage == "dialogue":
        return {
            x["id"]: hashlib.sha256(
                f"{x['who']}|{x['te']}|{x['en']}|{x['emotion']}".encode()
            ).hexdigest()
            for x in lines(episode)
        }
    out = {}
    for x in lines(episode):
        f = episode / x["audio"]
        out[x["id"]] = hashlib.sha256(f.read_bytes()).hexdigest() if f.exists() else "missing"
    return out


def _approvals(episode: Path) -> dict:
    f = episode / "review" / "approvals.json"
    return json.loads(f.read_text()) if f.exists() else {}


def pending(episode: Path, stage: str) -> list[str]:
    """Line ids whose current text/audio differs from what the user approved (all of them if never approved)."""
    approved = _approvals(episode).get(stage, {}).get("items", {})
    return [k for k, v in _digest(episode, stage).items() if approved.get(k) != v or v == "missing"]


def require(episode: Path, stage: str) -> None:
    """Stop the calling script unless the user approved this stage (and every earlier one) as it is now."""
    for st in STAGES[: STAGES.index(stage) + 1]:
        todo = pending(episode, st)
        if todo:
            raise ReviewPending(
                f"review gate: {st} not approved for {len(todo)} line(s): {', '.join(todo[:8])}"
                f"{' ...' if len(todo) > 8 else ''}\n  run: python scripts/drama_review.py {st} {episode}\n"
                f"  then ask the user; on their yes: python scripts/drama_review.py approve {st} {episode}"
            )


def write_dialogue(episode: Path) -> Path:
    rows = ["| # | Line | Speaker | Telugu | English | Emotion |", "|---|---|---|---|---|---|"]
    for i, x in enumerate(lines(episode), 1):
        rows.append(
            f"| {i} | {x['id']} | {x['who']} | {x['te']} | {x['en']} | {x['emotion'] or '—'} |"
        )
    out = episode / "review" / "dialogue.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        "# Check 1: dialogue\n\nIs every line right (words, speaker, meaning, intended emotion)?\n\n"
        + "\n".join(rows)
        + "\n"
    )
    return out


def write_voices(episode: Path) -> Path:
    rows = []
    for i, x in enumerate(lines(episode), 1):
        f = episode / x["audio"]
        player = (
            f'<audio controls preload="none" src="../{html.escape(x["audio"])}"></audio>'
            if f.exists()
            else "<b>missing</b>"
        )
        rows.append(
            f"<tr><td>{i}</td><td>{x['id']}</td><td>{html.escape(x['who'])}</td>"
            f"<td lang='te'>{html.escape(x['te'])}<br><small>{html.escape(x['en'])}</small></td>"
            f"<td>{html.escape(x['emotion'] or '—')}</td><td>{player}</td></tr>"
        )
    out = episode / "review" / "voices.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        "<!doctype html><meta charset='utf-8'><title>Voice review</title>"
        "<style>body{font:15px system-ui;margin:16px}table{border-collapse:collapse}td,th{border:1px solid #ccc;"
        "padding:6px;vertical-align:top}small{color:#555}</style>"
        "<h1>Check 2: voice emotion</h1><p>Play each take. Does the voice carry the intended emotion? "
        "Note the line numbers that need a new take.</p>"
        "<table><tr><th>#</th><th>Line</th><th>Speaker</th><th>Text</th><th>Intended emotion</th><th>Take</th></tr>"
        + "".join(rows)
        + "</table>\n"
    )
    return out


FONT_BOLD = "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"
FONT = "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"


def _esc(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "’")
        .replace(",", "\\,")
        .replace("%", " percent")
    )


def _card(
    path: Path, audio: Path, title: str, body: str, note: str, seconds: float | None = None
) -> None:
    """One reel card: title, English text and a note over the take. Text is English only (drawtext can't shape Telugu)."""
    if seconds is None:
        seconds = float(
            subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "csv=p=0",
                    str(audio),
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
    rows, draw = [""], []
    for word in body.split():
        rows[-1:] = [rows[-1]] if len(rows[-1]) + len(word) < 70 else [rows[-1], ""]
        rows[-1] = f"{rows[-1]} {word}".strip()
    draw.append(
        f"drawtext=fontfile={FONT_BOLD}:text='{_esc(title)}':x=60:y=80:fontsize=40:fontcolor=white"
    )
    draw += [
        f"drawtext=fontfile={FONT}:text='{_esc(r)}':x=60:y={170 + 42 * i}:fontsize=32:fontcolor=0xdddddd"
        for i, r in enumerate(rows)
    ]
    draw.append(
        f"drawtext=fontfile={FONT_BOLD}:text='{_esc(note)}':x=60:y=420:fontsize=34:fontcolor=0xffd060"
    )
    total = seconds + 1.0
    # adelay needs all=1: after the stereo upmix a bare adelay delays only the left channel (a doubled voice).
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x101418:s=1280x720:d={total}:r=25",
            "-t",
            f"{seconds}",
            "-i",
            str(audio),
            "-filter_complex",
            f"[0]{','.join(draw)}[v];[1]aresample=48000,aformat=channel_layouts=stereo,adelay=400:all=1,apad[a]",
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-t",
            f"{total}",
            "-c:v",
            "libx264",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )


def write_reel(episode: Path, voices: dict[str, str] | None = None) -> Path:
    """review/dialogue_audio_review.mp4: every line's card followed by its take, for review away from a desk."""
    tmp = episode / "review" / "_reel"
    tmp.mkdir(parents=True, exist_ok=True)
    cards = []
    for i, x in enumerate(lines(episode), 1):
        recorded = x["id"].endswith("_line") and not x["audio"].startswith("audio/lines/")
        who = x["who"].title() + (f" ({voices[x['who']]})" if voices and x["who"] in voices else "")
        card = tmp / f"{i:02d}.mp4"
        _card(
            card,
            episode / x["audio"],
            f"{i}. {x['id']} - {who}",
            x["en"],
            f"Intended emotion: {x['emotion'] or '-'}",
            12.0 if recorded else None,
        )
        cards.append(card)
    (tmp / "list.txt").write_text("".join(f"file '{c.name}'\n" for c in cards))
    out = episode / "review" / "dialogue_audio_review.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(tmp / "list.txt"),
            "-c",
            "copy",
            str(out),
        ],
        check=True,
    )
    shutil.rmtree(tmp)
    return out


def write_sheet(episode: Path, stage: str) -> Path:
    """review/<stage>.html: every image, clip or audio file of a file stage, for the user to look through."""
    tags = {".png": "img", ".jpg": "img", ".mp4": "video", ".wav": "audio"}
    rows = []
    for f in stage_files(episode, stage):
        rel = html.escape(str(f.relative_to(episode)))
        tag = tags.get(f.suffix, "a")
        media = (
            f'<img src="../{rel}" loading="lazy">'
            if tag == "img"
            else f'<{tag} controls preload="none" src="../{rel}"></{tag}>'
        )
        rows.append(f"<figure>{media}<figcaption>{rel}</figcaption></figure>")
    out = episode / "review" / f"{stage}.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        f"<!doctype html><meta charset='utf-8'><title>{stage.title()} review</title>"
        "<style>body{font:15px system-ui;margin:16px}figure{display:inline-block;margin:8px;width:420px}"
        "img,video{width:100%}audio{width:100%}</style>"
        f"<h1>Check: {stage}</h1><p>Note the file names that need a redo.</p>"
        + "".join(rows)
        + "\n"
    )
    return out


#: The three clauses a video prompt is missing most often (docs/DRAMA_STORY_AND_CONTINUITY.md).
_VIDEO_DIRECTIVES = (
    "single continuous take, no cuts, no camera changes",
    "and nobody else in frame",
    "spoken exactly as written",
)


def _video_directives(has_line: bool) -> list[str]:
    return list(_VIDEO_DIRECTIVES) if has_line else list(_VIDEO_DIRECTIVES[:2])


def _first_sentence(text: str) -> str:
    match = re.match(r"(.+?[.!?])(\s|$)", text.strip())
    return match.group(1) if match else text.strip()


def write_storyboard(episode: Path) -> Path:
    """review/storyboard.html: the one-page board — picture, camera, action, line.

    This is the cheapest review point in the pipeline: fixing a still costs nothing, fixing a shot costs
    money. Each row also carries the three video directives the generator must be given, so the board
    doubles as the prompt checklist.
    """
    visuals_path = episode / "v2" / "visuals.json"
    if not visuals_path.exists():
        raise SystemExit(f"no storyboard at {visuals_path}; run the image plan first")
    visuals = json.loads(visuals_path.read_text(encoding="utf-8")).get("visuals") or []
    by_id = {x["id"]: x for x in lines(episode)}

    def _still(visual: dict) -> str | None:
        for candidate in (
            visual.get("still"),
            f"v2/frames/{visual['id']}.png",
            f"frames/{visual['id']}.png",
        ):
            if candidate and (episode / str(candidate)).exists():
                return str(candidate)
        return None

    rows = []
    for visual in visuals:
        still = _still(visual)
        media = (
            f'<img src="../{html.escape(still)}" loading="lazy">'
            if still
            else '<div class="missing">no still yet</div>'
        )
        spoken = " ".join(
            html.escape(str(by_id[entry[0]].get("en") or ""))
            for entry in (visual.get("lines") or [])
            if entry[0] in by_id
        )
        directives = _video_directives(bool(spoken))
        rows.append(
            f"<tr><td><b>{html.escape(str(visual.get('id', '')))}</b>"
            f"<br><small>{float(visual.get('seconds') or 0):.1f}s</small></td>"
            f"<td>{media}</td>"
            f"<td>{html.escape(str(visual.get('kind', '')))}"
            f"<br><small>{html.escape(str(visual.get('cam') or '—'))}</small></td>"
            f"<td>{html.escape(_first_sentence(str(visual.get('prompt') or '')))}"
            f"<br><small>{html.escape(str(visual.get('expr') or ''))}</small></td>"
            f"<td lang='te'>{spoken or '—'}</td>"
            f"<td><small>{'<br>'.join(html.escape(d) for d in directives)}</small></td></tr>"
        )
    out = episode / "review" / "storyboard.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        "<!doctype html><meta charset='utf-8'><title>Storyboard review</title>"
        "<style>body{font:15px system-ui;margin:16px}table{border-collapse:collapse}"
        "td,th{border:1px solid #ccc;padding:6px;vertical-align:top;max-width:320px}"
        "img{width:100%}.missing{background:#eee;color:#666;padding:24px;text-align:center}"
        "small{color:#555}</style>"
        "<h1>Check: storyboard</h1><p>One picture, one camera move, one action and one line per shot. "
        "Fix a still here; fixing a shot costs money. Approve before any video is generated.</p>"
        "<table><tr><th>Shot</th><th>Picture</th><th>Kind / camera</th><th>Action</th>"
        "<th>Line</th><th>Video prompt must say</th></tr>" + "".join(rows) + "</table>\n"
    )
    return out


def approve(episode: Path, stage: str) -> None:
    missing = [k for k, v in _digest(episode, stage).items() if v == "missing"]
    if missing:
        raise SystemExit(f"cannot approve {stage}, missing: {', '.join(missing)}")
    data = _approvals(episode)
    data[stage] = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "items": _digest(episode, stage),
    }
    f = episode / "review" / "approvals.json"
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps(data, indent=1) + "\n")


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "reel":
        print(write_reel(Path(argv[1]).resolve()))
        return 0
    if len(argv) == 2 and argv[0] == "storyboard":
        print(write_storyboard(Path(argv[1]).resolve()))
        return 0
    if len(argv) == 3 and argv[0] == "sheet" and argv[1] in ("images", "videos", "lipsync"):
        print(write_sheet(Path(argv[2]).resolve(), argv[1]))
        return 0
    if len(argv) >= 2 and argv[0] in ("dialogue", "voices", "status"):
        episode = Path(argv[1]).resolve()
        if argv[0] == "dialogue":
            print(write_dialogue(episode))
            # Check 2 judges each take against its intended emotion, so every line should name one.
            bare = [x["id"] for x in lines(episode) if not x["emotion"]]
            if bare:
                print(f"no intended emotion on {len(bare)} line(s): {', '.join(bare)}")
        elif argv[0] == "voices":
            print(write_voices(episode))
        for st in STAGES:
            todo = pending(episode, st)
            print(f"{st}: {'approved' if not todo else f'{len(todo)} line(s) awaiting approval'}")
        return 0
    if len(argv) == 3 and argv[0] == "approve" and argv[1] in STAGES:
        episode = Path(argv[2]).resolve()
        earlier = STAGES[STAGES.index(argv[1]) - 1] if argv[1] != STAGES[0] else None
        if earlier:
            require(episode, earlier)
        approve(episode, argv[1])
        print(f"{argv[1]} approved for {episode.name}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
