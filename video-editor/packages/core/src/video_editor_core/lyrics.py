"""Parse a lyrics file into one caption per non-empty line.

Two callers need identical behaviour: the musical-video caption burn-in and the
FableCut bridge (which mirrors the same captions on its timeline). Keeping the
parser here stops them drifting; both import this module.
"""

from __future__ import annotations

#: Section headers in the source that are titles, not lyrics. They are folded
#: into the next non-empty line as a ``"Header: lyric"`` prefix so the viewer
#: still sees them, but they do not waste a caption window on a bare word.
FOLDABLE_HEADERS: frozenset[str] = frozenset({"फलशृति"})


def parse_lyrics(text: str) -> list[str]:
    """Return one caption per non-empty line.

    - Blank lines are ignored.
    - Lines that match ``FOLDABLE_HEADERS`` are folded into the next non-empty
      line as a ``"Header: lyric"`` prefix.
    - Lines consisting only of ``॥``, digits, and spaces (pure verse markers
      with no lyric text) are skipped.
    - Otherwise the line is kept verbatim, including any trailing verse marker
      (e.g. ``"दिवाकर ... नमोऽस्तुते ॥ 1 ॥"``) — these are part of the lyrics as
      written.
    """
    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    captions: list[str] = []
    pending_header: str | None = None
    for line in raw_lines:
        # Pure verse-marker lines: only "॥", digits, whitespace.
        if line and all(char in "॥ \t0123456789" for char in line):
            continue
        if line in FOLDABLE_HEADERS:
            pending_header = line
            continue
        if pending_header is not None:
            captions.append(f"{pending_header}: {line}")
            pending_header = None
        else:
            captions.append(line)
    if pending_header is not None:
        # Trailing header with no following line: render as its own caption.
        captions.append(pending_header)
    return captions
