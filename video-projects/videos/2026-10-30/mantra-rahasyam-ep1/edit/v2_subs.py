"""Drama v2, subtitles: YouTube-ready .srt files for the finished upload (2026-11-02).

usage: v2_subs.py            # writes v2/subtitles/mantra_rahasyam_ep1.<te|en|hi|ta|kn>.srt

Timed to mantra_rahasyam_ep1_v2_final.mp4: every line's start on the cut (v2/animatic) plus the 4 s cold open, for its
measured length. Telugu and English come from shots.json, Hindi, Tamil and Kannada (2026-11-04) from v2/dub/translations.json (their dubs are
fitted to the Telugu timing, so one timing serves all five). A line longer than ~84 characters is split at the sentence
break nearest its middle into two cues; a cue wraps to at most two lines. The mantra is on screen as graphics, so it
gets one short cue only. Upload in YouTube Studio > Subtitles > Add language > Upload file > With timing.
"""

import json
import sys
from pathlib import Path

P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P / "edit"))
import v2_animatic as A  # noqa: E402
from v2_graphics import COLD  # noqa: E402

OUT = P / "v2" / "subtitles"
SPEC = json.loads((P / "shots.json").read_text())
DUB = json.loads((P / "v2" / "dub" / "translations.json").read_text())
MANTRA = {"te": "ఓం త్ర్యంబకం యజామహే… (మహా మృత్యుంజయ మంత్రం)", "en": "(Raghava chants the Mahamrityunjaya mantra)",
          "hi": "ॐ त्र्यम्बकं यजामहे… (महामृत्युंजय मंत्र)", "ta": "ஓம் த்ர்யம்பகம் யஜாமஹே… (மகா மிருத்யுஞ்ஜய மந்திரம்)",
          "kn": "ಓಂ ತ್ರ್ಯಂಬಕಂ ಯಜಾಮಹೇ… (ಮಹಾ ಮೃತ್ಯುಂಜಯ ಮಂತ್ರ)"}


def text_of(lid: str, lang: str) -> str:
    if lid == "chant":
        return MANTRA[lang]
    if lang in ("hi", "ta", "kn"):
        return DUB[lang][lid]
    base = lid.rsplit("_", 1)[0]
    s = next(s for s in SPEC["shots"] if s["id"] == base)
    return (s.get("line") or s.get("vo"))[lang]


def wrap(text: str, width: int = 42) -> str:
    """At most two lines, broken at the space nearest the middle when the cue is too long for one."""
    if len(text) <= width:
        return text
    spaces = [i for i, c in enumerate(text) if c == " "]
    if not spaces:
        return text
    cut = min(spaces, key=lambda i: abs(i - len(text) / 2))
    return text[:cut] + "\n" + text[cut + 1:]


def cues(lang: str) -> list[tuple[float, float, str]]:
    rows = []
    for lid, t0 in sorted(A.STARTS.items(), key=lambda kv: kv[1]):
        d = A.DUR[lid] if lid != "chant" else 4.0
        text = text_of(lid, lang).strip()
        chunks = [text]
        if len(text) > 84:
            cuts = [i + 1 for i, c in enumerate(text) if c in ".!?।…" and (i + 1 == len(text) or text[i + 1] == " ")]
            cuts = [c for c in cuts if 10 < c < len(text) - 10]
            if cuts:
                c = min(cuts, key=lambda i: abs(i - len(text) / 2))
                chunks = [text[:c].strip(), text[c:].strip()]
        span, t = d / sum(len(c) for c in chunks), t0 + COLD
        for c in chunks:
            rows.append((t, t + len(c) * span, wrap(c)))
            t += len(c) * span
    return rows


def ts(x: float) -> str:
    ms = int(round(x * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for lang in ("te", "en", "hi", "ta", "kn"):
        rows = cues(lang)
        f = OUT / f"mantra_rahasyam_ep1.{lang}.srt"
        f.write_text("".join(f"{i}\n{ts(a)} --> {ts(b)}\n{c}\n\n" for i, (a, b, c) in enumerate(rows, 1)), encoding="utf-8")
        print(f.name, len(rows), "cues")
