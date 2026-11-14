"""Tests for the FFmpeg render-graph compiler.

The compiler returns argv lists; these tests assert the shape of the command
(filtergraph structure, mappings, encode flags) without executing FFmpeg.
"""

from __future__ import annotations

from video_editor_core.compile_ffmpeg import compile_ffmpeg, compile_ffmpeg_mux_audio
from video_editor_core.render_graph import (
    AudioTrack,
    Cut,
    Layer,
    Rect,
    RenderGraph,
    Timing,
)
from video_editor_ffmpeg.render_options import RenderOptions


def _cut(src_start: float, src_end: float, tl_start: float, tl_end: float) -> Cut:
    return Cut(
        source_start=src_start,
        source_end=src_end,
        timeline_start=tl_start,
        timeline_end=tl_end,
    )


def _graph(**overrides: object) -> RenderGraph:
    base: dict[str, object] = {
        "job_id": "job-1",
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "source": "source.mp4",
        "cuts": [_cut(0.0, 5.0, 0.0, 5.0)],
    }
    base.update(overrides)
    return RenderGraph(**base)  # type: ignore[arg-type]


def _filter_complex(cmd: list[str]) -> str:
    return cmd[cmd.index("-filter_complex") + 1]


def test_single_cut_compiles_to_one_pass() -> None:
    command = compile_ffmpeg(_graph(), "out.mp4")
    assert len(command) == 1
    cmd = command[0]
    assert cmd[0] == "ffmpeg"
    fc = _filter_complex(cmd)
    assert "concat=n=1:v=1:a=0" in fc
    assert "loudnorm=I=-16" in fc
    assert cmd[cmd.index("-map") + 1] == "[vout]"
    assert cmd[-1] == "out.mp4"
    assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    assert cmd[cmd.index("-movflags") + 1] == "+faststart"


def test_two_cuts_trim_and_concat() -> None:
    graph = _graph(cuts=[_cut(0.0, 2.0, 0.0, 2.0), _cut(10.0, 13.0, 2.0, 5.0)])
    fc = _filter_complex(compile_ffmpeg(graph, "out.mp4")[0])
    assert "trim=start=0.0:end=2.0" in fc
    assert "trim=start=10.0:end=13.0" in fc
    assert "concat=n=2:v=1:a=0" in fc
    assert "concat=n=2:v=0:a=1" in fc


def test_text_overlay_becomes_timed_drawtext() -> None:
    layer = Layer(
        kind="text",
        name="caption",
        rect=Rect(x=0, y=900, width=1920, height=200),
        text="hello world",
        timing=Timing(start=1.0, end=2.5),
    )
    fc = _filter_complex(compile_ffmpeg(_graph(overlays=[layer]), "out.mp4")[0])
    assert "drawtext=" in fc
    assert "text='hello world'" in fc
    assert "between(t\\,1.000\\,2.500)" in fc


def test_logo_overlay_adds_looped_input_and_overlay() -> None:
    layer = Layer(
        kind="logo",
        name="brand",
        rect=Rect(x=1700, y=1000, width=180, height=60),
        source="brand.png",
    )
    command = compile_ffmpeg(_graph(overlays=[layer]), "out.mp4")
    cmd = command[0]
    # input args for the logo are inserted before -filter_complex
    assert "-loop" in cmd
    assert "brand.png" in cmd
    fc = _filter_complex(cmd)
    assert "overlay=1700:1000" in fc


def test_video_overlay_is_shifted_to_its_timeline_start() -> None:
    """A clip scheduled at t=5 must play its first frame at t=5, not at t=0.

    Without a PTS shift the overlay stream starts playing at the beginning of
    the composition; ``enable`` only hides it, so by the time the layer is
    visible the clip is already part-way through (or finished).
    """
    layer = Layer(
        kind="video",
        name="parallax",
        rect=Rect(x=0, y=0, width=1920, height=1080),
        source="clip.mp4",
        timing=Timing(start=5.0, end=9.0),
    )
    fc = _filter_complex(compile_ffmpeg(_graph(overlays=[layer]), "out.mp4")[0])
    assert "trim=duration=4.000" in fc
    assert "setpts=PTS-STARTPTS+5.000/TB" in fc
    assert "between(t\\,5.000\\,9.000)" in fc


def test_video_overlay_from_zero_needs_no_shift() -> None:
    """A full-timeline clip keeps its original timing chain."""
    layer = Layer(
        kind="video",
        name="parallax",
        rect=Rect(x=0, y=0, width=1920, height=1080),
        source="clip.mp4",
    )
    fc = _filter_complex(compile_ffmpeg(_graph(overlays=[layer]), "out.mp4")[0])
    assert "setpts=PTS-STARTPTS+0.000/TB" not in fc
    assert "eof_action=pass" in fc


def test_video_overlay_with_start_and_open_end_shifts_but_does_not_trim() -> None:
    layer = Layer(
        kind="video",
        name="parallax",
        rect=Rect(x=0, y=0, width=1920, height=1080),
        source="clip.mp4",
        timing=Timing(start=2.5),
    )
    fc = _filter_complex(compile_ffmpeg(_graph(overlays=[layer]), "out.mp4")[0])
    assert "setpts=PTS-STARTPTS+2.500/TB" in fc
    assert "trim=duration=" not in fc


def test_music_track_with_ducking_uses_sidechain() -> None:
    track = AudioTrack(kind="music", source="music.mp3", gain_db=-6.0, duck=True)
    fc = _filter_complex(compile_ffmpeg(_graph(audio=[track]), "out.mp4")[0])
    assert "adelay" not in fc
    assert "volume=-6.00dB" in fc
    assert "sidechaincompress" in fc
    assert "asplit=2" in fc
    assert "amix=inputs=2" in fc


def test_sfx_track_with_start_is_delayed() -> None:
    track = AudioTrack(kind="sfx", source="whoosh.mp3", start=3.5)
    fc = _filter_complex(compile_ffmpeg(_graph(audio=[track]), "out.mp4")[0])
    assert "adelay=3500|3500" in fc
    assert "sidechaincompress" not in fc


def test_no_audio_graph_is_video_only() -> None:
    command = compile_ffmpeg(_graph(has_audio=False), "out.mp4")
    cmd = command[0]
    fc = _filter_complex(cmd)
    assert "[aout]" not in cmd
    assert "-an" in cmd
    assert "concat=n=1:v=0:a=1" not in fc


def test_silent_source_with_voiceover_mixes_over_silence() -> None:
    """A video-only source can still carry a voiceover bus."""
    track = AudioTrack(kind="voiceover", source="vo.wav")
    graph = _graph(has_audio=False, audio=[track])
    assert graph.emits_audio
    cmd = compile_ffmpeg(graph, "out.mp4")[0]
    fc = _filter_complex(cmd)
    assert "-an" not in cmd
    assert "[aout]" in cmd
    assert "anullsrc" in fc
    assert "[0:a]" not in fc  # the silent source has no audio stream to read


def test_silent_source_mux_does_not_read_source_audio() -> None:
    track = AudioTrack(kind="voiceover", source="vo.wav")
    cmd = compile_ffmpeg_mux_audio(_graph(has_audio=False, audio=[track]), "comp.mp4", "out.mp4")[0]
    fc = _filter_complex(cmd)
    assert "[1:a]" not in fc
    assert "[2:a]" in fc  # the voiceover bus follows the composed video and source


def test_render_options_flow_into_encode_flags() -> None:
    options = RenderOptions(video_codec="h264_nvenc", crf=20, fps=24)
    cmd = compile_ffmpeg(_graph(options=options), "out.mp4")[0]
    assert cmd[cmd.index("-c:v") + 1] == "h264_nvenc"
    assert cmd[cmd.index("-crf") + 1] == "20"
    assert cmd[cmd.index("-r") + 1] == "24"


def test_hwaccel_is_prepended_before_input() -> None:
    cmd = compile_ffmpeg(_graph(options=RenderOptions(hwaccel="cuda")), "out.mp4")[0]
    assert cmd[cmd.index("-hwaccel") + 1] == "cuda"
    assert cmd.index("-hwaccel") < cmd.index("-i")
    assert cmd[cmd.index("-i") + 1] == "source.mp4"


def test_mux_audio_copies_video_and_maps_mixed_audio() -> None:
    track = AudioTrack(kind="music", source="music.mp3", duck=True)
    cmd = compile_ffmpeg_mux_audio(_graph(audio=[track]), "composed.mp4", "out.mp4")[0]
    # input 0 = composed video, input 1 = source (cut audio)
    assert cmd[cmd.index("-i") + 1] == "composed.mp4"
    fc = _filter_complex(cmd)
    assert "[1:a]atrim" in fc
    assert "sidechaincompress" in fc
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[-1] == "out.mp4"
