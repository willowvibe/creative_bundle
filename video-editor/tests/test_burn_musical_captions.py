"""Tests for the standalone musical caption burner script.

These cover both the lyric parser (Bug #228 follow-up) and the end-to-end
FFmpeg caption-burning path.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import _require_ffmpeg

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "burn_musical_captions.py"
spec = importlib.util.spec_from_file_location("burn_musical_captions", _SCRIPT)
assert spec is not None and spec.loader is not None
burn_musical_captions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(burn_musical_captions)


# Real Suryastakam lyrics content used during the 2026-07-12 build. It contains
# blank lines, a foldable section header (फलशृति), and verse markers attached
# to lyric lines — a good adversarial input for the parser.
_SURYASTAKAM_LYRICS = """\
आदिदेव नमस्तुभ्यं प्रसीद मम भास्कर ।
दिवाकर नमस्तुभ्यं प्रभाकर नमोऽस्तुते ॥ 1 ॥

सप्ताश्व रथ मारूढं प्रचण्डं कश्यपात्मजम् ।
श्वेत पद्मधरं देवं तं सूर्यं प्रणमाम्यहम् ॥ 2॥

लोहितं रथमारूढं सर्व लोक पितामहम् ।
महा पाप हरं देवं तं सूर्यं प्रणमाम्यहम् ॥ 3॥

त्रैगुण्यं च महाशूरं ब्रह्म विष्णु महेश्वरम् ।
महा पाप हरं देवं तं सूर्यं प्रणमाम्यहम् ॥ 4 ॥

बृंहितं तेजसां पुञ्जं [तेजपूज्यं च] वायु माकाश मेव च ।
प्रभुं च सर्वलोकानां तं सूर्यं प्रणमाम्यहम् ॥ 5 ॥

बन्धूक पुष्पसङ्काशं हार कुण्डल भूषितम् ।
एक चक्रधरं देवं तं सूर्यं प्रणमाम्यहम् ॥ 6 ॥

विश्वेशं विश्व कर्तारं महातेजः प्रदीपनम् ।
महा पाप हरं देवं तं सूर्यं प्रणमाम्यहम् ॥ 7 ॥

तं सूर्यं जगतां नाथं ज्ञान विज्ञान मोक्षदम् ।
महा पाप हरं देवं तं सूर्यं प्रणमाम्यहम् ॥ 8 ॥

फलशृति
सूर्याष्टकं पठेन्नित्यं ग्रहपीडा प्रणाशनम् ।
अपुत्रो लभते पुत्रं दरिद्रो धनवान् भवेत् ॥

आमिषं मधुपानं च यः करोति रवेर्धिने ।
सप्त जन्म भवेद्रोगी जन्म कर्म दरिद्रता ॥

स्त्री तैल मधु मांसानि ये त्यजन्ति रवेर्दिने ।
न व्याधि शोक दारिद्र्यं सूर्यलोकं स गच्छति ॥

इति श्री शिवप्रोक्तं श्री सूर्याष्टकं सम्पूर्णम् ॥
"""


def test_parse_lyrics_counts_one_caption_per_lyric_line() -> None:
    """Bug #228 follow-up: every non-empty lyric line becomes one caption.

    The original parser collapsed multi-line verses into a single caption,
    clipping the second line. After the fix each non-empty source line is
    preserved, blank lines are ignored, and the ``फलशृति`` header is folded
    into the following line as a prefix.
    """
    captions = burn_musical_captions.parse_lyrics(_SURYASTAKAM_LYRICS)
    # 24 non-empty source lines; ``फलशृति`` is folded into the next line,
    # so we expect 23 captions.
    assert len(captions) == 23


def test_parse_lyrics_folds_header_into_next_line() -> None:
    """A section header should prefix the next lyric, not consume its own window."""
    captions = burn_musical_captions.parse_lyrics(_SURYASTAKAM_LYRICS)
    header_caption = next(c for c in captions if "सूर्याष्टकं पठेन्नित्यं" in c)
    assert header_caption.startswith("फलशृति: ")


def test_parse_lyrics_skips_pure_verse_markers() -> None:
    """A line that is only a verse marker (no lyric text) is dropped."""
    text = "\n".join(["॥ 1 ॥", "॥ 2 ॥", " lyric line ", "॥ 3 ॥"])
    captions = burn_musical_captions.parse_lyrics(text)
    assert captions == ["lyric line"]


def test_parse_lyrics_preserves_attached_verse_markers() -> None:
    """Verse markers attached to lyric text are kept as written."""
    captions = burn_musical_captions.parse_lyrics(_SURYASTAKAM_LYRICS)
    # The first verse marker is on the second caption (stanza 1, line 2).
    assert "॥ 1 ॥" in captions[1]


def test_parse_lyrics_handles_trailing_header() -> None:
    """A header with no following line is emitted as its own caption."""
    captions = burn_musical_captions.parse_lyrics("\n\nफलशृति\n")
    assert captions == ["फलशृति"]


def test_build_filter_complex_rejects_empty_captions() -> None:
    """The filter builder must fail loudly rather than emit an empty chain."""
    with pytest.raises(ValueError, match="at least one caption required"):
        burn_musical_captions.build_filter_complex([], 10.0, "/tmp/font.ttf", 40, 22, 0.35)


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available on PATH",
)
def test_burn_captions_end_to_end(tmp_path: Path) -> None:
    """Burn a small caption set onto a real synthesized video and verify output."""
    _require_ffmpeg()

    base_video = tmp_path / "base.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=4:size=320x240:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
            "-pix_fmt",
            "yuv420p",
            str(base_video),
        ],
        check=True,
        capture_output=True,
    )

    lyrics_path = tmp_path / "lyrics.txt"
    lyrics_path.write_text("first line\nsecond line\n", encoding="utf-8")

    output = tmp_path / "final.mp4"
    # We need a font file. Try a common system Devanagari font first; fall back
    # to any available TTF so the test runs on non-Devanagari hosts.
    font_candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    font_path = next((p for p in font_candidates if Path(p).is_file()), None)
    if font_path is None:
        pytest.skip("no suitable system font found for drawtext")

    result = subprocess.run(
        [
            # The test's own interpreter: a bare ``python3`` is the system one
            # unless a venv is on PATH, and the script imports workspace packages.
            sys.executable,
            str(_SCRIPT),
            "--base-video",
            str(base_video),
            "--lyrics",
            str(lyrics_path),
            "--output",
            str(output),
            "--font",
            font_path,
            "--work-dir",
            str(tmp_path / "cap_work"),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"caption burner failed:\n{result.stderr[-2000:]}")
    assert output.is_file()
    duration = burn_musical_captions.probe_duration(output)
    assert duration > 0.0
