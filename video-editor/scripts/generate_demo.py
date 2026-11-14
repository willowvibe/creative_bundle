#!/usr/bin/env python3
"""Generate a demo video with synthetic speech that matches a hand-crafted transcript.

The audio is built from short audible tones whose lengths equal the transcript word
durations, with explicit silence inserted for pauses and long gaps. This makes the
demo fixture deterministic: silence detection, filler-word removal, pause trimming,
and caption burn-in all align with the transcript timings even though no real TTS
is installed.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

# Hand-crafted word timings tuned so that:
#   - filler words "um,", "uh," and "So" are isolated and removable
#   - a 1.3s [pause] sits between two speech bursts
#   - several 1.5s silent separators exist to exercise silence detection
DEMO_WORDS = [
    # "Welcome to the video editor engine demo."
    ("Welcome", 0.00, 0.40),
    ("to", 0.40, 0.60),
    ("the", 0.60, 0.75),
    ("video", 0.75, 1.10),
    ("editor", 1.10, 1.50),
    ("engine", 1.50, 1.90),
    ("demo", 1.90, 2.30),
    # filler
    ("um,", 2.30, 2.65),
    # "this is a quick example of how the engine uh processes raw footage"
    ("this", 2.65, 2.95),
    ("is", 2.95, 3.15),
    ("a", 3.15, 3.25),
    ("quick", 3.25, 3.60),
    ("example", 3.60, 4.10),
    ("of", 4.10, 4.25),
    ("how", 4.25, 4.50),
    ("the", 4.50, 4.65),
    ("engine", 4.65, 5.05),
    ("uh,", 5.05, 5.35),
    ("processes", 5.35, 5.80),
    ("raw", 5.80, 6.10),
    ("footage", 6.10, 6.60),
    # long silence separator
    # "So today we will trim silence and remove filler words"
    ("So", 8.10, 8.45),
    ("today", 8.45, 8.80),
    ("we", 8.80, 8.95),
    ("will", 8.95, 9.15),
    ("trim", 9.15, 9.45),
    ("silence", 9.45, 9.90),
    ("and", 9.90, 10.10),
    ("remove", 10.10, 10.50),
    ("filler", 10.50, 10.90),
    ("words", 10.90, 11.40),
    # explicit pause region
    ("[pause]", 12.00, 13.30),
    # "And then we will add captions automatically"
    ("And", 13.40, 13.65),
    ("then", 13.65, 13.90),
    ("we", 13.90, 14.05),
    ("will", 14.05, 14.25),
    ("add", 14.25, 14.50),
    ("captions", 14.50, 15.00),
    ("automatically", 15.00, 15.70),
    # "That's it"
    ("That's", 15.80, 16.15),
    ("it", 16.15, 16.40),
]


def _build_transcript(words: list[tuple[str, float, float]]) -> dict[str, object]:
    word_dicts: list[dict[str, object]] = [
        {"word": word, "start": start, "end": end, "probability": 0.95}
        for word, start, end in words
    ]

    segments: list[dict[str, object]] = []
    current: list[dict[str, object]] = []
    for w in word_dicts:
        if w["word"] == "[pause]":
            if current:
                text = " ".join(str(x["word"]) for x in current)
                segments.append(
                    {
                        "id": len(segments),
                        "start": current[0]["start"],
                        "end": current[-1]["end"],
                        "text": text,
                        "words": list(current),
                    }
                )
                current.clear()
            segments.append(
                {
                    "id": len(segments),
                    "start": w["start"],
                    "end": w["end"],
                    "text": "[pause]",
                    "words": [w],
                }
            )
        else:
            current.append(w)
    if current:
        text = " ".join(str(x["word"]) for x in current)
        segments.append(
            {
                "id": len(segments),
                "start": current[0]["start"],
                "end": current[-1]["end"],
                "text": text,
                "words": list(current),
            }
        )

    return {
        "source_audio": "",
        "language": "en",
        "language_probability": 1.0,
        "word_timestamps": True,
        "segments": segments,
        "words": word_dicts,
    }


def _synth_word_audio(out: Path, duration: float, freq: int = 440) -> None:
    """Generate a short audible tone for a word."""
    expr = f"'0.3*sin(2*PI*{freq}*t + 10*sin(2*PI*4*t))'"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"aevalsrc={expr}:s=16000:c=mono",
            "-t",
            str(duration),
            "-af",
            "volume=0.4",
            "-acodec",
            "pcm_s16le",
            str(out),
        ],
        check=True,
        capture_output=True,
    )


def _silence_audio(out: Path, duration: float) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            str(duration),
            "-acodec",
            "pcm_s16le",
            str(out),
        ],
        check=True,
        capture_output=True,
    )


def main() -> None:
    demo_dir = Path("demo")
    demo_dir.mkdir(exist_ok=True)

    words = DEMO_WORDS
    duration = words[-1][2] + 0.5  # small tail after last word

    # Build audio pieces in order. Long gaps between words become silence.
    pieces: list[Path] = []
    cursor = 0.0
    freq = 320
    for word, start, end in words:
        if word == "[pause]":
            # Explicit pause region is silent.
            if start > cursor:
                gap = demo_dir / f"gap_{len(pieces):04d}.wav"
                _silence_audio(gap, start - cursor)
                pieces.append(gap)
            pause = demo_dir / f"pause_{len(pieces):04d}.wav"
            _silence_audio(pause, end - start)
            pieces.append(pause)
            cursor = end
            continue

        if start > cursor + 0.05:
            gap_dur = start - cursor
            gap = demo_dir / f"gap_{len(pieces):04d}.wav"
            _silence_audio(gap, gap_dur)
            pieces.append(gap)

        word_dur = max(0.05, end - start)
        tone = demo_dir / f"word_{len(pieces):04d}.wav"
        _synth_word_audio(tone, word_dur, freq=freq)
        pieces.append(tone)
        freq = 320 + (freq % 200)  # vary pitch slightly word-to-word
        cursor = end

    if cursor < duration:
        tail = demo_dir / f"tail_{len(pieces):04d}.wav"
        _silence_audio(tail, duration - cursor)
        pieces.append(tail)

    # Concatenate all pieces.
    concat_list = demo_dir / "audio_concat.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        for piece in pieces:
            f.write(f"file '{piece.resolve()}'\n")

    speech_path = demo_dir / "speech.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-acodec",
            "pcm_s16le",
            str(speech_path),
        ],
        check=True,
        capture_output=True,
    )

    # Wrap with a test video.
    raw_path = demo_dir / "raw.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={duration}:size=1920x1080:rate=30",
            "-i",
            str(speech_path),
            "-pix_fmt",
            "yuv420p",
            str(raw_path),
        ],
        check=True,
        capture_output=True,
    )

    # Write matching transcript.
    transcript_path = demo_dir / "transcript.json"
    transcript = _build_transcript(words)
    transcript["source_audio"] = ""
    with open(transcript_path, "w", encoding="utf-8") as f:
        json.dump(transcript, f, indent=2)

    # Generate a background music fixture (a soft sustained tone) so that
    # every documented music workflow runs end-to-end without a real asset.
    # Frequency is in a "background" range that won't fight the speech tone.
    music_path = demo_dir / "music.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=20",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(music_path),
        ],
        check=True,
        capture_output=True,
    )

    # Cleanup intermediate audio pieces.
    for piece in pieces:
        piece.unlink(missing_ok=True)
    concat_list.unlink(missing_ok=True)
    speech_path.unlink(missing_ok=True)

    print(f"Generated {raw_path} ({duration:.1f}s), {transcript_path}, and {music_path}")


if __name__ == "__main__":
    main()
