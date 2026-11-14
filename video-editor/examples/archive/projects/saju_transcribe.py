"""Transcribe the Saju voiceover to engine-schema transcript.json.

Uses the same transcribe_audio wrapper the rough-cut skill uses, so
the output is already in the format `ve run --transcript` expects.

Run with:  uv run python scripts/saju_transcribe.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
JOB_ID = "saju-2026-07-03"


def main() -> None:
    from video_editor_tools.whisperx_wrapper import transcribe as transcribe_audio

    audio = REPO_ROOT / "projects" / JOB_ID / "voiceover.mp3"
    out = REPO_ROOT / "projects" / JOB_ID / "transcript.json"

    if not audio.exists():
        sys.exit(f"voiceover not found at {audio}; run saju_generate_voiceover.py first")

    print(f"Transcribing {audio} -> {out}")
    result = transcribe_audio(audio, out)

    words = result.get("words", [])
    segments = result.get("segments", [])
    print(f"  language: {result.get('language')}")
    print(f"  word_count: {len(words)}")
    print(f"  segment_count: {len(segments)}")
    if segments:
        last = segments[-1]
        print(f"  first_segment: {segments[0]['start']:.2f}s  text={segments[0]['text'][:60]!r}")
        print(f"  last_segment:  {last['end']:.2f}s  text={last['text'][:60]!r}")


if __name__ == "__main__":
    main()
