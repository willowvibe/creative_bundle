"""Measure lip-sync hints with wav2vec2 and g2p_en.

Run this script with the optional alignment environment, not with the video
editor's Python::

    .venv-lipsync/bin/python align_script.py AUDIO SCRIPT_TXT OUT_JSON

It is deliberately self-contained (stdlib + numpy + torch + transformers +
g2p_en) so the video editor never depends on the trial project.  It performs
the same CTC Viterbi letter alignment as ``align_letters.py``, keeps only the
M/B/P moments that are actually pronounced, and records the phones each word
needs.  The output is::

    {"version": 1,
     "bilabial": [seconds, ...],
     "words": [{"text": "word", "start": s, "end": e, "phones": [...]}, ...]}

Create the environment with::

    uv venv .venv-lipsync
    uv pip install --python .venv-lipsync/bin/python torch \
        --index-url https://download.pytorch.org/whl/cpu
    uv pip install --python .venv-lipsync/bin/python transformers g2p_en numpy

Model: facebook/wav2vec2-base-960h (Apache-2.0), run on CPU.
"""

from __future__ import annotations

import json
import re
import subprocess  # noqa: TID251 - standalone script; no video_editor imports allowed
import sys
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np
import torch
from g2p_en import G2p
from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

MODEL = "facebook/wav2vec2-base-960h"
VERSION = 1
SILENT = ("MB", "MN")
LETTER = {  # ARPAbet for spelled-out letters
    "A": "EY1",
    "B": "B IY1",
    "C": "S IY1",
    "D": "D IY1",
    "E": "IY1",
    "F": "EH1 F",
    "G": "JH IY1",
    "H": "EY1 CH",
    "I": "AY1",
    "J": "JH EY1",
    "K": "K EY1",
    "L": "EH1 L",
    "M": "EH1 M",
    "N": "EH1 N",
    "O": "OW1",
    "P": "P IY1",
    "Q": "K Y UW1",
    "R": "AA1 R",
    "S": "EH1 S",
    "T": "T IY1",
    "U": "Y UW1",
    "V": "V IY1",
    "W": "D AH1 B AH0 L Y UW0",
    "X": "EH1 K S",
    "Y": "W AY1",
    "Z": "Z IY1",
}
LEXICON = {  # tech words g2p_en gets wrong; extend as videos need
    "json": "JH EY1 S AH0 N",
    "httpx": "EY1 CH T IY1 T IY1 P IY1 EH1 K S",
    "github": "G IH1 T HH AH2 B",
    "api": "EY1 P IY1 AY1",
    "sql": "EH1 S K Y UW1 EH1 L",
    "url": "Y UW1 AA1 R EH1 L",
    "npm": "EH1 N P IY1 EH1 M",
}

_G2P: Any = None


def _g2p() -> Any:
    global _G2P
    if _G2P is None:
        _G2P = G2p()
    return _G2P


def load_audio(path: str) -> Any:
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            path,
            "-f",
            "f32le",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, np.float32)


def align(emission: Any, tokens: list[int], blank: int = 0) -> list[tuple[int, int]]:
    """Return the (token_index, frame) path of the best monotonic alignment."""
    frames, count = emission.size(0), len(tokens)
    trellis = torch.full((frames + 1, count + 1), -float("inf"))
    trellis[0, 0] = 0
    trellis[1:, 0] = torch.cumsum(emission[:, blank], 0)
    for frame in range(frames):
        trellis[frame + 1, 1:] = torch.maximum(
            trellis[frame, 1:] + emission[frame, blank],
            trellis[frame, :-1] + emission[frame, tokens],
        )
    frame, index, path = frames, count, []
    while index > 0 and frame > 0:
        stay = trellis[frame - 1, index] + emission[frame - 1, blank]
        change = trellis[frame - 1, index - 1] + emission[frame - 1, tokens[index - 1]]
        if change > stay:
            path.append((index - 1, frame - 1))
            index -= 1
        frame -= 1
    return path[::-1]


def clean(word: str) -> str:
    return re.sub(r"[^A-Za-z']", "", word)


@cache
def phones(word: str) -> tuple[str, ...]:
    value = clean(word)
    if not value:
        return ()
    if value.lower() in LEXICON:
        return tuple(LEXICON[value.lower()].split())
    if value.isupper() and 2 <= len(value) <= 5 and value.lower() not in {"pip", "ok"}:
        return tuple(" ".join(LETTER[char] for char in value).split())
    return tuple(p for p in _g2p()(value) if p.strip() and p not in {"'", " "})


def bilabial_times(letters: list[list[Any]]) -> list[float]:
    """Measured M/B/P letter centres, skipping silent ``mb``/``mn`` and ``ph``."""
    marks: list[float] = []
    for index, (char, start, end) in enumerate(letters):
        if char not in "MBP":
            continue
        pair = (letters[index - 1][0] + char) if index else ""
        if pair in SILENT and char == "B":
            continue
        if char == "P" and index + 1 < len(letters) and letters[index + 1][0] == "H":
            continue
        marks.append(round((float(start) + float(end)) / 2.0, 6))
    return marks


def align_words(audio: str, script: str) -> tuple[list[float], list[dict[str, Any]]]:
    processor = Wav2Vec2Processor.from_pretrained(MODEL)
    model = Wav2Vec2ForCTC.from_pretrained(MODEL).eval()
    wav = load_audio(audio)
    with torch.inference_mode():
        logits = model(
            processor(wav, sampling_rate=16000, return_tensors="pt").input_values
        ).logits[0]
    emission = torch.log_softmax(logits, dim=-1)
    seconds_per_frame = len(wav) / 16000 / emission.size(0)

    tokens_in: list[tuple[str, str]] = []
    for raw in Path(script).read_text(encoding="utf-8").split():
        cleaned = clean(raw)
        aligned = cleaned.upper()
        if aligned:
            tokens_in.append((cleaned, aligned))

    vocab = processor.tokenizer.get_vocab()
    text = "|".join(aligned for _cleaned, aligned in tokens_in)
    tokens = [vocab[char] for char in text]
    path = align(emission, tokens, blank=vocab.get("<pad>", 0))

    first: dict[int, int] = {}
    last: dict[int, int] = {}
    for token_index, frame in path:
        first.setdefault(token_index, frame)
        last[token_index] = frame

    marks: list[float] = []
    words: list[dict[str, Any]] = []
    cursor = 0
    for cleaned, aligned in tokens_in:
        letters: list[list[Any]] = []
        for char in aligned:
            if cursor in first:
                letters.append(
                    [
                        char,
                        round(first[cursor] * seconds_per_frame, 3),
                        round((last[cursor] + 1) * seconds_per_frame, 3),
                    ]
                )
            cursor += 1
        cursor += 1  # word separator
        if not letters:
            continue
        marks.extend(bilabial_times(letters))
        words.append(
            {
                "text": cleaned,
                "start": letters[0][1],
                "end": letters[-1][2],
                "phones": list(phones(cleaned)),
            }
        )
    return sorted(marks), words


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: align_script.py AUDIO SCRIPT_TXT OUT_JSON")
    audio, script, out = sys.argv[1:4]
    marks, words = align_words(audio, script)
    payload = {"version": VERSION, "bilabial": marks, "words": words}
    Path(out).write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"aligned {len(words)} words; {len(marks)} bilabial marks")


if __name__ == "__main__":
    main()
