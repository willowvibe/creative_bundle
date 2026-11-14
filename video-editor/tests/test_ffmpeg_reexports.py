"""Tests for the tools/ffmpeg_wrapper re-export surface (#105 / A54)."""

from __future__ import annotations

import video_editor_tools.ffmpeg_wrapper as wrapper


def test_ffmpeg_wrapper_reexports_expected_names() -> None:
    """The wrapper exposes the stable API used by skills."""
    expected = {
        "apply_cuts",
        "drawtext_captions",
        "extract_audio",
        "generate_caption_segments",
        "make_preview",
        "make_proxy",
        "normalize_audio",
        "probe_media",
        "remap_transcript_to_cuts",
        "write_srt",
    }
    assert expected.issubset(set(wrapper.__all__))
    for name in expected:
        assert hasattr(wrapper, name), f"missing re-export: {name}"


def test_make_preview_is_make_proxy_with_960_width() -> None:
    """make_preview is a convenience wrapper around make_proxy(width=960)."""
    from video_editor_ffmpeg.proxy import make_preview

    assert make_preview is wrapper.make_preview
    assert make_preview.__name__ == "make_preview"
