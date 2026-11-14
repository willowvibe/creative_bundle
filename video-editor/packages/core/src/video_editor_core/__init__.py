"""Engine core shared by all video editor skills.

Holds the render cache, EDL/timeline model, the HyperFrames CLI wrapper, and
the render graph (the single declarative description of a job's final render)
plus its compilers and renderer.
"""

from video_editor_core.compile_ffmpeg import (
    compile_ffmpeg,
    compile_ffmpeg_audio,
    compile_ffmpeg_mux_audio,
)
from video_editor_core.compile_hyperframes import compile_hyperframes
from video_editor_core.composition_router import choose_engine, graph_needs_hyperframes
from video_editor_core.hyperframes_project import compile_hyperframes_project
from video_editor_core.render_graph import (
    AudioTrack,
    Cut,
    Layer,
    Rect,
    RenderGraph,
    Timing,
    edl_to_cuts,
)
from video_editor_core.render_graph_builder import RenderGraphBuilder
from video_editor_core.renderer import (
    FFmpegRenderer,
    HyperFramesRenderer,
    Renderer,
    RenderError,
    render_graph,
    select_renderer,
)
from video_editor_core.ticks import (
    TICKS_PER_SECOND,
    TickConversionError,
    format_seconds,
    frame_count,
    frames_for_ticks,
    seconds_to_ticks,
    snap_to_frame,
    ticks_for_frames,
    ticks_to_seconds,
)
from video_editor_core.timeline import (
    EDL,
    Segment,
    SegmentDiff,
    compute_timeline_segments,
    diff_edls,
)

__all__ = [
    "AudioTrack",
    "Cut",
    "EDL",
    "FFmpegRenderer",
    "HyperFramesRenderer",
    "Layer",
    "Rect",
    "RenderError",
    "RenderGraph",
    "RenderGraphBuilder",
    "Renderer",
    "Segment",
    "SegmentDiff",
    "TICKS_PER_SECOND",
    "TickConversionError",
    "Timing",
    "compile_ffmpeg",
    "compile_ffmpeg_audio",
    "compile_ffmpeg_mux_audio",
    "compile_hyperframes",
    "compile_hyperframes_project",
    "compute_timeline_segments",
    "choose_engine",
    "diff_edls",
    "edl_to_cuts",
    "format_seconds",
    "frame_count",
    "frames_for_ticks",
    "graph_needs_hyperframes",
    "render_graph",
    "seconds_to_ticks",
    "select_renderer",
    "snap_to_frame",
    "ticks_for_frames",
    "ticks_to_seconds",
]
