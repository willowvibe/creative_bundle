"""Tests for the talking-avatar skill."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image
from talking_avatar.avatar import (
    AvatarRenderer,
    breath_scale,
    find_chrome,
    iter_avatar_frames,
    max_bob_overscan,
    plan_tilts,
    slide_offset,
    tilt_angle_degrees,
)
from talking_avatar.bubbles import (
    DEFAULT_BROWS_HOLD_SECONDS,
    DEFAULT_GLANCE_HOLD_SECONDS,
    AvatarAction,
    Bubble,
    BubbleError,
    BubbleSpec,
    bubble_start_times,
    load_bubbles,
    parse_bubbles,
    render_bubble_art,
    resolve_actions,
    resolve_bubbles,
    schedule_bubbles,
)
from talking_avatar.hybrid import (
    AlignHints,
    HintsError,
    HintWord,
    aligner_closures,
    build_hybrid_timeline,
    fix,
    load_hints,
    parse_hints,
    slots,
    words_with_phones,
)
from talking_avatar.rhubarb import (
    DEFAULT_LEAD,
    MouthCue,
    RhubarbError,
    apply_stress_open,
    build_rhubarb_timeline,
    enforce_closure_hold,
    find_rhubarb_binary,
    merge_single_frame_runs,
    sample_shape,
    shift_cues,
)
from talking_avatar.talking_avatar import (
    LIPSYNC_ENGINES,
    TalkingAvatarError,
    TalkingAvatarSkill,
    align_hints_cache_path,
    find_lipsync_python,
    generate_align_hints,
)
from talking_avatar.timeline import (
    OPEN_SHAPES,
    VisemeFrame,
    WordTiming,
    blink_frames,
    build_viseme_timeline,
    grapheme_to_shape,
    word_to_shapes,
)

FPS = 30.0
REPO_ROOT = Path(__file__).resolve().parents[1]
BOLT_SVG = REPO_ROOT / "templates" / "avatars" / "bolt" / "bolt.svg"

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")


def _rhubarb_available() -> bool:
    try:
        find_rhubarb_binary()
    except RhubarbError:
        return False
    return True


needs_rhubarb = pytest.mark.skipif(not _rhubarb_available(), reason="rhubarb missing")


@pytest.mark.parametrize(
    ("grapheme", "shape"),
    [
        ("m", "A"),
        ("b", "A"),
        ("p", "A"),
        ("f", "G"),
        ("v", "G"),
        ("o", "E"),
        ("u", "F"),
        ("w", "F"),
        ("oo", "F"),
        ("a", "D"),
        ("e", "C"),
        ("i", "C"),
        ("y", "C"),
        ("l", "H"),
        ("t", "B"),
        ("s", "B"),
    ],
)
def test_grapheme_mapping(grapheme: str, shape: str) -> None:
    assert grapheme_to_shape(grapheme) == shape


def test_grapheme_non_letter_is_none() -> None:
    assert grapheme_to_shape("1") is None
    assert grapheme_to_shape("") is None


def test_word_shapes_collapse_repeats_and_oo() -> None:
    assert word_to_shapes("mama") == ["A", "D", "A", "D"]
    assert word_to_shapes("moon") == ["A", "F", "B"]
    assert word_to_shapes("ll") == ["H"]


def test_silence_frames_are_rest() -> None:
    timeline = build_viseme_timeline([("hello", 0.0, 1.0)], [0.0] * 30, duration=1.0, fps=FPS)
    assert {frame.shape for frame in timeline} == {"X"}


def test_loud_burst_produces_open_shape() -> None:
    envelope = [0.0] * 10 + [1.0] * 10 + [0.0] * 10
    timeline = build_viseme_timeline(None, envelope, duration=1.0, fps=FPS)
    burst = {frame.shape for frame in timeline[12:19]}
    assert burst <= {"C", "D", "H"}
    assert "D" in burst
    assert {frame.shape for frame in timeline[:8]} == {"X"}


def test_rounded_vowel_replaces_open_shape() -> None:
    timeline = build_viseme_timeline(
        [("oo", 0.0, 1.0)],
        [1.0] * 30,
        duration=1.0,
        fps=FPS,
        language="en",
    )
    shapes = {frame.shape for frame in timeline}
    assert shapes <= {"E", "F"}
    assert "E" in shapes


def test_bilabial_word_forces_lip_closure() -> None:
    timeline = build_viseme_timeline(
        [("mama", 0.0, 1.0)],
        [1.0] * 30,
        duration=1.0,
        fps=FPS,
        language="en",
    )
    assert "A" in {frame.shape for frame in timeline}


def test_shift_cues_moves_every_cue_earlier() -> None:
    cues = [MouthCue(0.010, 0.050, "A"), MouthCue(1.0, 1.5, "B")]
    shifted = shift_cues(cues, DEFAULT_LEAD)
    assert shifted[0].start == 0.0
    assert shifted[0].end == pytest.approx(0.03)
    assert shifted[1].start == pytest.approx(0.98)
    assert shifted[1].end == pytest.approx(1.48)
    assert [cue.value for cue in shifted] == ["A", "B"]


def test_sample_shape_uses_covering_cue_and_rests_outside() -> None:
    cues = [MouthCue(0.0, 0.1, "A"), MouthCue(0.1, 0.2, "C")]
    assert sample_shape(cues, 0.0) == "A"
    assert sample_shape(cues, 0.05) == "A"
    assert sample_shape(cues, 0.1) == "C"
    assert sample_shape(cues, 0.2) == "C"
    assert sample_shape(cues, 0.5) == "X"
    gap = [MouthCue(0.0, 0.1, "A"), MouthCue(0.2, 0.3, "C")]
    assert sample_shape(gap, 0.1) == "X"
    assert sample_shape(gap, 0.15) == "X"


def test_closure_runs_are_held_for_two_frames() -> None:
    assert enforce_closure_hold(["B", "A", "C", "C"]) == ["B", "A", "A", "C"]
    cues = [MouthCue(0.0, 0.09, "B"), MouthCue(0.09, 0.12, "A"), MouthCue(0.12, 0.3, "C")]
    timeline = build_rhubarb_timeline(cues, duration=0.3, fps=FPS, lead=0.0)
    runs: list[tuple[str, int]] = []
    for frame in timeline:
        if runs and runs[-1][0] == frame.shape:
            runs[-1] = (frame.shape, runs[-1][1] + 1)
        else:
            runs.append((frame.shape, 1))
    closure_runs = [count for shape, count in runs if shape == "A"]
    assert closure_runs
    assert all(count >= 2 for count in closure_runs)


def test_single_frame_runs_take_previous_shape() -> None:
    assert merge_single_frame_runs(["B", "C", "D"]) == ["B", "B", "B"]
    assert merge_single_frame_runs(["B", "C", "C"]) == ["B", "C", "C"]
    cues = [MouthCue(0.0, 0.09, "B"), MouthCue(0.09, 0.12, "C"), MouthCue(0.12, 0.3, "B")]
    timeline = build_rhubarb_timeline(cues, duration=0.3, fps=FPS, lead=0.0)
    assert "C" not in {frame.shape for frame in timeline}


def test_stress_open_upgrades_only_loud_c_and_e() -> None:
    shapes = ["C", "E", "C", "E", "A", "B", "F", "G", "H", "X"]
    envelope = [1.0, 1.0, 0.001, 0.001, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    upgraded, count = apply_stress_open(shapes, envelope, percentile=80)
    assert count == 2
    assert upgraded[:4] == ["D", "D", "C", "E"]
    assert upgraded[4:] == ["A", "B", "F", "G", "H", "X"]


def test_stress_open_never_creates_isolated_d_and_can_be_disabled() -> None:
    cues = [MouthCue(0.0, 0.1, "C"), MouthCue(0.1, 0.2, "E")]
    envelope = [1.0] * 12
    on = build_rhubarb_timeline(
        cues,
        duration=0.4,
        fps=10,
        lead=0.0,
        envelope=envelope,
        stress_open=True,
    )
    off = build_rhubarb_timeline(
        cues,
        duration=0.4,
        fps=10,
        lead=0.0,
        envelope=envelope,
        stress_open=False,
    )
    assert "D" in {frame.shape for frame in on}
    assert {frame.shape for frame in off} <= {"C", "E", "X"}
    runs: list[tuple[str, int]] = []
    for frame in on:
        if runs and runs[-1][0] == frame.shape:
            runs[-1] = (frame.shape, runs[-1][1] + 1)
        else:
            runs.append((frame.shape, 1))
    assert all(count >= 2 for shape, count in runs if shape == "D")


def test_words_none_animates_from_audio() -> None:
    envelope = [0.0] * 10 + [1.0] * 10 + [0.0] * 10
    timeline = build_viseme_timeline(None, envelope, duration=1.0, fps=FPS)
    assert any(frame.shape in OPEN_SHAPES for frame in timeline)
    assert any(frame.shape == "X" for frame in timeline)


def _frame_rms(signal: list[float], *, sample_rate: int, fps: float) -> list[float]:
    hop = max(1, int(round(sample_rate / fps)))
    frame_count = len(signal) // hop
    return [
        math.sqrt(sum(value * value for value in signal[index * hop : (index + 1) * hop]) / hop)
        for index in range(frame_count)
    ]


def _pearson(left: list[float], right: list[float]) -> float:
    count = min(len(left), len(right))
    left = left[:count]
    right = right[:count]
    mean_left = sum(left) / count
    mean_right = sum(right) / count
    covariance = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right, strict=True))
    variance_left = sum((a - mean_left) ** 2 for a in left)
    variance_right = sum((b - mean_right) ** 2 for b in right)
    return covariance / math.sqrt(variance_left * variance_right)


SHAPE_OPENNESS = {
    "X": 0.0,
    "A": 0.0,
    "B": 0.3,
    "G": 0.3,
    "F": 0.4,
    "C": 0.7,
    "H": 0.7,
    "E": 0.8,
    "D": 1.0,
}


def test_mouth_openness_tracks_audio_rms() -> None:
    sample_rate = 16000
    burst_seconds = 0.15
    silence_seconds = 0.15
    period = burst_seconds + silence_seconds
    signal = [
        (
            0.5 * math.sin(2.0 * math.pi * 220.0 * index / sample_rate)
            if (index / sample_rate) % period < burst_seconds
            else 0.0
        )
        for index in range(sample_rate * 2)
    ]
    envelope = _frame_rms(signal, sample_rate=sample_rate, fps=FPS)
    timeline = build_viseme_timeline(
        None,
        envelope,
        duration=len(envelope) / FPS,
        fps=FPS,
        rms_fps=FPS,
    )
    mouth = [SHAPE_OPENNESS[frame.shape] for frame in timeline]
    frame_rms = [frame.rms for frame in timeline]
    assert _pearson(mouth, frame_rms) > 0.7


def test_each_shape_lasts_at_least_two_frames() -> None:
    envelope = [0.0] * 10 + [1.0] * 20 + [0.0] * 10
    timeline = build_viseme_timeline([("mama", 0.0, 1.3)], envelope, duration=1.3, fps=FPS)
    runs: list[tuple[str, int]] = []
    for frame in timeline:
        if runs and runs[-1][0] == frame.shape:
            runs[-1] = (frame.shape, runs[-1][1] + 1)
        else:
            runs.append((frame.shape, 1))
    spoken = [count for shape, count in runs if shape != "X"]
    assert spoken
    assert all(count >= 2 for count in spoken)


def test_blink_determinism_and_shape() -> None:
    first = blink_frames(20.0, fps=FPS, seed=3)
    assert first == blink_frames(20.0, fps=FPS, seed=3)
    assert first != blink_frames(20.0, fps=FPS, seed=4)
    assert first
    # Blinks are 3-frame runs separated by 2.5-5 s.
    starts = [f for f in first if f - 1 not in first]
    assert all({s, s + 1, s + 2} <= set(first) for s in starts)
    gaps = [(b - a) / FPS for a, b in zip(starts, starts[1:], strict=False)]
    assert all(2.4 <= gap <= 5.1 for gap in gaps)


def _make_sprites(
    folder: Path,
    size: int = 64,
    *,
    brows: bool = False,
    eyes_left: bool = False,
) -> None:
    folder.mkdir(parents=True)
    transparent = (0, 0, 0, 0)
    Image.new("RGBA", (size, size), (200, 200, 200, 255)).save(folder / "base.png")
    eyes = Image.new("RGBA", (size, size), transparent)
    eyes.paste((0, 0, 255, 255), (8, 8, 24, 16))
    eyes.save(folder / "eyes_closed.png")
    if eyes_left:
        left = Image.new("RGBA", (size, size), transparent)
        left.paste((0, 255, 0, 255), (8, 8, 24, 16))
        left.save(folder / "eyes_left.png")
    if brows:
        raised = Image.new("RGBA", (size, size), transparent)
        raised.paste((0, 255, 255, 255), (32, 4, 48, 12))
        raised.save(folder / "brows_up.png")
    for index, letter in enumerate("ABCDEFGH"):
        mouth = Image.new("RGBA", (size, size), transparent)
        mouth.paste((255, 0, index * 30, 255), (20, 40, 44, 56))
        mouth.save(folder / f"mouth_{letter}.png")


def test_png_folder_compositing(tmp_path: Path) -> None:
    folder = tmp_path / "sprites"
    _make_sprites(folder)
    renderer = AvatarRenderer(folder, 32)
    rest = renderer.composite_for_shape("X")
    talk = renderer.composite_for_shape("D")
    blink = renderer.composite_for_shape("X", eyes_closed=True)
    assert rest.size == (32, 32)
    # Rest is the bare base: no mouth patch, no closed eyes.
    assert rest.getpixel((16, 24)) == (200, 200, 200, 255)
    assert talk.getpixel((16, 24))[:2] == (255, 0)
    assert blink.getpixel((8, 6))[:3] == (0, 0, 255)
    assert rest.getpixel((8, 6))[:3] == (200, 200, 200)
    # Combos are cached, and at most 18 exist.
    assert renderer.composite_for_shape("D") is talk
    for letter in "ABCDEFGHX":
        for closed in (False, True):
            renderer.composite_for_shape(letter, eyes_closed=closed)
    assert len(renderer._composites) == 18
    assert renderer.frame_size % 2 == 0


def test_action_entries_parse_with_action_defaults(tmp_path: Path) -> None:
    path = tmp_path / "bubbles.yaml"
    path.write_text(
        "- action: glance\n"
        "  word: install\n"
        "- action: brows\n"
        "  word: install\n"
        "  nth: 2\n"
        "- action: glance\n"
        "  time: 4.0\n"
        "  hold: 0.5\n"
        "- time: 1.0\n"
        "  text: hi\n"
        "  brows: false\n",
        encoding="utf-8",
    )
    specs = load_bubbles(path)
    assert specs[0].action == "glance"
    assert specs[0].text == ""
    assert specs[0].word == "install"
    assert specs[0].hold == DEFAULT_GLANCE_HOLD_SECONDS
    assert specs[1].action == "brows"
    assert specs[1].nth == 2
    assert specs[1].hold == DEFAULT_BROWS_HOLD_SECONDS
    assert specs[2].time == 4.0
    assert specs[2].hold == 0.5
    assert specs[3].brows is False
    with pytest.raises(BubbleError, match="action"):
        parse_bubbles([{"action": "wink", "time": 1.0}])


def test_action_entries_are_not_bubbles_and_do_not_delay_text() -> None:
    words = [WordTiming("install", 1.0, 1.2)]
    specs = [
        BubbleSpec(action="glance", word="install"),
        BubbleSpec(text="pip install", word="install"),
    ]
    bubbles = resolve_bubbles(specs, words)
    assert [bubble.text for bubble in bubbles] == ["pip install"]
    assert bubbles[0].start == pytest.approx(1.0)
    assert bubble_start_times(bubbles) == [1.0]

    actions = resolve_actions(specs, words, bubbles)
    glances = [action for action in actions if action.type == "glance"]
    brows = [action for action in actions if action.type == "brows"]
    assert len(glances) == 1
    assert glances[0].type == "glance"
    assert glances[0].start == pytest.approx(1.0)
    assert glances[0].end == pytest.approx(1.0 + DEFAULT_GLANCE_HOLD_SECONDS)
    # The text bubble adds one brows raise at its pop-in.
    assert len(brows) == 1
    assert brows[0].start == pytest.approx(1.0)
    assert brows[0].end == pytest.approx(1.0 + DEFAULT_BROWS_HOLD_SECONDS)


def _sprite_pixel(frame: Image.Image, renderer: AvatarRenderer, x: int, y: int) -> tuple:
    return frame.getpixel((renderer.padding + x, renderer.padding + y))


def test_bubble_raises_brows_then_respects_brows_false(tmp_path: Path) -> None:
    folder = tmp_path / "sprites"
    _make_sprites(folder, brows=True)
    renderer = AvatarRenderer(folder, 64)
    duration = 1.6
    timeline = [
        VisemeFrame(frame=index, time=index / FPS, shape="X", rms=0.0)
        for index in range(int(duration * FPS))
    ]
    brows_pixel = (40, 8)

    def frames_for(brows: bool) -> list[Image.Image]:
        spec = BubbleSpec(text="hi", time=0.5, brows=brows)
        bubbles = resolve_bubbles([spec], [])
        actions = resolve_actions([spec], [], bubbles)
        return _decode_frames(renderer, timeline, actions=actions, duration=duration, motion=False)

    before = frames_for(True)[int(0.4 * FPS)]
    assert _sprite_pixel(before, renderer, *brows_pixel) == (200, 200, 200, 255)
    during = frames_for(True)[int(0.8 * FPS)]
    assert _sprite_pixel(during, renderer, *brows_pixel) == (0, 255, 255, 255)

    suppressed = frames_for(False)[int(0.8 * FPS)]
    assert _sprite_pixel(suppressed, renderer, *brows_pixel) == (200, 200, 200, 255)


def test_glance_shows_eyes_left_but_a_blink_wins(tmp_path: Path) -> None:
    folder = tmp_path / "sprites"
    _make_sprites(folder, eyes_left=True)
    renderer = AvatarRenderer(folder, 64)
    duration = 10.0
    timeline = [
        VisemeFrame(frame=index, time=index / FPS, shape="X", rms=0.0)
        for index in range(int(duration * FPS))
    ]
    actions = [AvatarAction("glance", 0.5, 9.0)]
    frames = _decode_frames(renderer, timeline, actions=actions, duration=duration, motion=False)
    closed = set(blink_frames(duration, fps=FPS, seed=0))
    eye_pixel = (12, 12)

    left_index = next(
        index for index in range(int(1.0 * FPS), int(8.0 * FPS)) if index not in closed
    )
    blink_index = next(index for index in range(int(1.0 * FPS), int(8.0 * FPS)) if index in closed)
    assert _sprite_pixel(frames[left_index], renderer, *eye_pixel) == (0, 255, 0, 255)
    assert _sprite_pixel(frames[blink_index], renderer, *eye_pixel) == (0, 0, 255, 255)


def test_expression_edges_blend_with_the_neutral_composite(tmp_path: Path) -> None:
    folder = tmp_path / "sprites"
    _make_sprites(folder, brows=True)
    renderer = AvatarRenderer(folder, 64)
    duration = 1.0
    timeline = [
        VisemeFrame(frame=index, time=index / FPS, shape="X", rms=0.0)
        for index in range(int(duration * FPS))
    ]
    actions = [AvatarAction("brows", 0.3, 0.9)]
    frames = _decode_frames(renderer, timeline, actions=actions, duration=duration, motion=False)
    brows_pixel = (40, 8)
    first = frames[int(0.3 * FPS)]
    middle = frames[int(0.6 * FPS)]
    last = frames[int(0.9 * FPS) - 1]
    base = (200, 200, 200, 255)
    raised = (0, 255, 255, 255)
    assert _sprite_pixel(middle, renderer, *brows_pixel) == raised
    assert _sprite_pixel(first, renderer, *brows_pixel) == _sprite_pixel(
        last, renderer, *brows_pixel
    )
    assert _sprite_pixel(first, renderer, *brows_pixel) not in {base, raised}


def test_folders_without_optional_sprites_render_as_before(tmp_path: Path) -> None:
    folder = tmp_path / "sprites"
    _make_sprites(folder)
    renderer = AvatarRenderer(folder, 32)
    neutral = renderer.composite_for_shape("D")
    with_actions = renderer.composite_for_shape("D", brows=True, glance=True)
    assert with_actions.tobytes() == neutral.tobytes()
    blink = renderer.composite_for_shape("D", eyes_closed=True)
    assert blink.getpixel((8, 6))[:3] == (0, 0, 255)


class _SolidRenderer:
    """A tiny stand-in for AvatarRenderer whose shapes are flat colours."""

    _COLOURS = {
        "A": (255, 0, 0, 255),
        "B": (0, 0, 255, 255),
        "C": (0, 255, 0, 255),
        "X": (0, 0, 0, 0),
    }

    def __init__(self, size: int = 100) -> None:
        self.size = size
        self.padding = 5
        self.frame_size = size + 2 * self.padding

    def composite_for_shape(
        self,
        shape: str,
        *,
        eyes_closed: bool = False,
        glance: bool = False,
        brows: bool = False,
    ) -> Image.Image:
        return Image.new("RGBA", (self.size, self.size), self._COLOURS[shape])


def _decode_frames(renderer: _SolidRenderer, timeline, **kwargs) -> list[Image.Image]:
    kwargs.setdefault("motion", False)
    return [
        Image.frombytes("RGBA", (renderer.frame_size, renderer.frame_size), data)
        for data in iter_avatar_frames(renderer, timeline, **kwargs)
    ]


def _decode_stage_frames(
    renderer: _SolidRenderer, timeline, stage_size: tuple[int, int], **kwargs
) -> list[Image.Image]:
    kwargs.setdefault("motion", False)
    return [
        Image.frombytes("RGBA", stage_size, data)
        for data in iter_avatar_frames(renderer, timeline, stage_size=stage_size, **kwargs)
    ]


def _has_visible_pixels(image: Image.Image, box: tuple[int, int, int, int]) -> bool:
    region = image.crop(box)
    return region.getbbox() is not None


def _first_visible_row(frame: Image.Image) -> int:
    for y in range(frame.height):
        for x in range(frame.width):
            if frame.getpixel((x, y))[3] > 0:
                return y
    return frame.height


def test_crossfade_blends_open_shapes_and_cuts_into_closure() -> None:
    renderer = _SolidRenderer()
    timeline = [
        VisemeFrame(frame=0, time=0.0, shape="B", rms=0.0),
        VisemeFrame(frame=1, time=1 / FPS, shape="C", rms=0.0),
        VisemeFrame(frame=2, time=2 / FPS, shape="A", rms=0.0),
    ]
    frames = _decode_frames(renderer, timeline)
    centre = (renderer.frame_size // 2, renderer.frame_size // 2)
    assert frames[0].getpixel(centre) == (0, 0, 255, 255)
    blended = frames[1].getpixel(centre)
    assert blended[0] == 0
    assert blended[1] == pytest.approx(128, abs=1)
    assert blended[2] == pytest.approx(128, abs=1)
    # Closures cut in hard instead of blending with the previous shape.
    assert frames[2].getpixel(centre) == (255, 0, 0, 255)


def test_crossfade_can_be_disabled() -> None:
    renderer = _SolidRenderer()
    timeline = [
        VisemeFrame(frame=0, time=0.0, shape="B", rms=0.0),
        VisemeFrame(frame=1, time=1 / FPS, shape="C", rms=0.0),
    ]
    frames = _decode_frames(renderer, timeline, crossfade=False)
    centre = (renderer.frame_size // 2, renderer.frame_size // 2)
    assert frames[1].getpixel(centre) == (0, 255, 0, 255)


def test_bounce_is_off_by_default() -> None:
    renderer = _SolidRenderer()
    timeline = [
        VisemeFrame(frame=0, time=0.0, shape="B", rms=0.0),
        VisemeFrame(frame=1, time=0.0, shape="B", rms=1.0),
    ]
    still = _decode_frames(renderer, timeline)
    assert _first_visible_row(still[0]) == _first_visible_row(still[1])
    bouncy = _decode_frames(renderer, timeline, bounce=True)
    assert _first_visible_row(bouncy[0]) != _first_visible_row(bouncy[1])


def test_no_motion_keeps_identical_pixels_for_same_shape() -> None:
    renderer = _SolidRenderer(size=120)
    timeline = [
        VisemeFrame(frame=0, time=0.5, shape="B", rms=0.0),
        VisemeFrame(frame=1, time=2.0, shape="B", rms=0.0),
    ]
    frames = _decode_frames(renderer, timeline, motion=False, duration=3.0)
    assert frames[0].tobytes() == frames[1].tobytes()


def test_motion_starts_fully_offscreen_and_slides_in() -> None:
    renderer = _SolidRenderer(size=100)
    timeline = [
        VisemeFrame(frame=0, time=0.0, shape="B", rms=0.0),
        VisemeFrame(frame=1, time=1.0, shape="B", rms=0.0),
    ]
    frames = _decode_frames(renderer, timeline, motion=True, duration=2.0)
    assert frames[0].getbbox() is None
    assert frames[1].getbbox() is not None


def test_slide_offset_entrance_and_exit() -> None:
    distance = 100
    assert slide_offset(0.0, 2.0, distance) == distance
    assert slide_offset(0.5, 2.0, distance) == 0
    assert slide_offset(1.0, 2.0, distance) == 0
    assert slide_offset(1.8, 2.0, distance) > 0
    assert slide_offset(2.0, 2.0, distance) == distance


def test_tilt_triggers_respect_minimum_spacing() -> None:
    timeline = [VisemeFrame(frame=i, time=i / FPS, shape="X", rms=0.0) for i in range(300)]
    tilts = plan_tilts(
        timeline,
        fps=FPS,
        seed=4,
        bubble_times=[1.0, 1.2, 1.5, 5.0, 5.1],
    )
    times = [event.time for event in tilts]
    assert times == [1.0, 5.0]
    assert all(b - a >= 2.5 for a, b in zip(times, times[1:], strict=False))
    assert tilts[0].direction == -tilts[1].direction
    assert tilt_angle_degrees(1.0, tilts) == 0.0
    assert abs(tilt_angle_degrees(1.5, tilts)) == pytest.approx(1.5)
    assert tilt_angle_degrees(5.0 - 0.01, tilts) == 0.0


def test_breathing_keeps_bottom_row_flush_and_moves_the_top() -> None:
    renderer = _SolidRenderer(size=400)
    timeline = [
        VisemeFrame(frame=0, time=0.5, shape="B", rms=0.0),
        VisemeFrame(frame=1, time=1.0, shape="B", rms=0.0),
    ]
    frames = _decode_frames(renderer, timeline, motion=True, duration=3.0)
    bottom = renderer.padding + renderer.size - 1
    for frame in frames:
        assert frame.getpixel((renderer.frame_size // 2, bottom))[3] > 0
    assert breath_scale(0.5) != pytest.approx(breath_scale(1.0))
    assert _first_visible_row(frames[0]) != _first_visible_row(frames[1])


def test_load_bubbles_yaml_word_nth_time_and_emoji(tmp_path: Path) -> None:
    path = tmp_path / "bubbles.yaml"
    path.write_text(
        "- word: install\n"
        "  nth: 2\n"
        '  text: "pip install httpx"\n'
        '  emoji: "📦"\n'
        "  hold: 1.5\n"
        "- time: 9.4\n"
        '  text: "Done"\n',
        encoding="utf-8",
    )
    specs = load_bubbles(path)
    assert specs[0].word == "install"
    assert specs[0].nth == 2
    assert specs[0].text == "pip install httpx"
    assert specs[0].emoji == "📦"
    assert specs[0].hold == 1.5
    assert specs[1].time == 9.4
    assert specs[1].text == "Done"
    assert parse_bubbles({"bubbles": [{"time": 1, "text": "x"}]})[0].time == 1.0


def test_resolve_bubbles_uses_word_occurrence_and_strips_punctuation() -> None:
    words = [
        WordTiming("Install,", 1.0, 1.2),
        WordTiming("install", 2.0, 2.2),
    ]
    resolved = resolve_bubbles([BubbleSpec(text="second", word="INSTALL", nth=2)], words)
    assert resolved[0].start == pytest.approx(2.0)
    with pytest.raises(BubbleError, match="missing"):
        resolve_bubbles([BubbleSpec(text="bad", word="missing")], words)


def test_overlapping_bubbles_are_pushed_until_the_previous_fades() -> None:
    scheduled = schedule_bubbles(
        [
            Bubble(text="first", start=1.0, end=2.0),
            Bubble(text="second", start=1.2, end=4.0),
        ]
    )
    assert scheduled[0].start == 1.0
    assert scheduled[0].end == 2.0
    assert scheduled[1].start == 2.0
    assert scheduled[1].end == pytest.approx(4.8)
    assert bubble_start_times(scheduled) == [1.0, 2.0]


def test_bubble_is_only_drawn_inside_its_window() -> None:
    renderer = _SolidRenderer(size=200)
    stage = (480, 340)
    duration = 3.0
    timeline = [
        VisemeFrame(frame=index, time=index / FPS, shape="B", rms=0.0)
        for index in range(int(duration * FPS))
    ]
    bubble = Bubble(text="pip install httpx", start=1.0, end=2.0, emoji="📦")
    frames = _decode_stage_frames(
        renderer,
        timeline,
        stage,
        bubbles=[bubble],
        duration=duration,
    )
    upper_left = (0, 0, stage[0] // 2, stage[1] // 2)
    assert not _has_visible_pixels(frames[int(0.5 * FPS)], upper_left)
    assert _has_visible_pixels(frames[int(1.5 * FPS)], upper_left)
    assert not _has_visible_pixels(frames[int(2.5 * FPS)], upper_left)


def test_larger_stage_does_not_move_the_avatar() -> None:
    renderer = _SolidRenderer(size=120)
    timeline = [VisemeFrame(frame=0, time=1.0, shape="B", rms=0.0)]
    plain = _decode_frames(renderer, timeline, duration=3.0, motion=False)[0]
    stage = (renderer.frame_size + 160, renderer.frame_size + 100)
    staged = _decode_stage_frames(renderer, timeline, stage, duration=3.0, motion=False)[0]
    extra_x = stage[0] - renderer.frame_size
    extra_y = stage[1] - renderer.frame_size
    avatar = staged.crop((extra_x, extra_y, stage[0], stage[1]))
    assert avatar.tobytes() == plain.tobytes()


def test_render_bubble_art_produces_visible_pixels() -> None:
    art = render_bubble_art(Bubble(text="hello", start=0.0, end=1.0), max_width=220, max_height=140)
    assert art.image.getbbox() is not None
    assert 0 <= art.tail[0] < art.image.width
    assert 0 <= art.tail[1] < art.image.height


# A static background keeps the corner-change assertions about the avatar only.
def _make_inputs(tmp_path: Path) -> tuple[Path, Path]:
    screen = tmp_path / "screen.mp4"
    audio = tmp_path / "voice.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:size=640x360:rate=30",
            "-t",
            "1.5",
            "-pix_fmt",
            "yuv420p",
            str(screen),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=2",
            str(audio),
        ],
        check=True,
    )
    return screen, audio


def _frame_corner(video: Path, t: float, box: tuple[int, int, int, int]) -> bytes:
    out = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(t),
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            f"format=rgb24,crop={box[2]}:{box[3]}:{box[0]}:{box[1]}",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        check=True,
        capture_output=True,
    )
    return out.stdout


def _probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(out.stdout.strip())


WORDS = [("mama", 0.1, 0.6), ("hello", 0.7, 1.2), ("moon", 1.3, 1.9)]


@needs_ffmpeg
def test_end_to_end_png_folder(tmp_path: Path) -> None:
    screen, audio = _make_inputs(tmp_path)
    sprites = tmp_path / "sprites"
    _make_sprites(sprites, size=128)
    out = tmp_path / "out.mp4"
    result = TalkingAvatarSkill().run(
        screen,
        audio,
        out,
        words=WORDS,
        avatar_path=sprites,
        size=0.4,
        margin=16,
        lipsync="loudness",
        motion=False,
    )
    assert out.exists()
    # Output length follows the 2 s audio even though the screen clip is 1.5 s.
    assert _probe_duration(out) == pytest.approx(2.0, abs=0.15)
    sidecar = json.loads(Path(result["timeline"]).read_text())
    assert sidecar["fps"] == FPS
    assert len(sidecar["frames"]) == 60
    # Avatar is bottom-right: 0.4 * 360 = 144 px, margin 16. It is anchored
    # flush to the bottom edge with a small overscan so the bob cannot lift it.
    avatar_top = 360 + max_bob_overscan(144) - 144
    box = (640 - 16 - 144, avatar_top, 144, 360 - avatar_top)
    # Frame 0 is a rest mouth; t=0.35 is mid-"mama" (open/closed lips differ).
    assert _frame_corner(out, 0.0, box) != _frame_corner(out, 0.35, box)
    # The avatar is actually present: the corner differs from the bare clip.
    assert _frame_corner(out, 0.35, box) != _frame_corner(screen, 0.35, box)


@needs_ffmpeg
@pytest.mark.skipif(find_chrome() is None, reason="google-chrome missing")
def test_end_to_end_svg(tmp_path: Path) -> None:
    screen, audio = _make_inputs(tmp_path)
    out = tmp_path / "svg.mp4"
    TalkingAvatarSkill().run(
        screen,
        audio,
        out,
        words=WORDS,
        avatar_path=BOLT_SVG,
        size=0.4,
        lipsync="loudness",
        motion=False,
    )
    assert _probe_duration(out) == pytest.approx(2.0, abs=0.15)
    avatar_top = 360 + max_bob_overscan(144) - 144
    box = (640 - 32 - 144, avatar_top, 144, 360 - avatar_top)
    assert _frame_corner(out, 0.0, box) != _frame_corner(out, 0.35, box)


@needs_ffmpeg
def test_bottom_corner_is_flush_with_frame_edge(tmp_path: Path) -> None:
    screen, audio = _make_inputs(tmp_path)
    sprites = tmp_path / "sprites"
    _make_sprites(sprites, size=128)
    out = tmp_path / "br.mp4"
    size, margin = 0.4, 16
    TalkingAvatarSkill().run(
        screen,
        audio,
        out,
        words=WORDS,
        avatar_path=sprites,
        position="br",
        size=size,
        margin=margin,
        lipsync="loudness",
        motion=False,
    )
    avatar_pixels = int(round(360 * size))
    left = 640 - margin - avatar_pixels
    # The avatar's bottom row is the last row of the frame, even at the peak of
    # the upward bob, so the shoulders run off the bottom edge instead of
    # floating above it.
    for timestamp in (0.0, 0.35, 0.9, 1.5, 1.8):
        row = _frame_corner(out, timestamp, (left, 359, avatar_pixels, 1))
        pixels = [row[index : index + 3] for index in range(0, len(row), 3)]
        assert len(pixels) == avatar_pixels
        assert all(sum(abs(channel - 128) for channel in pixel) > 60 for pixel in pixels)


@needs_ffmpeg
@needs_rhubarb
def test_end_to_end_rhubarb_sidecar(tmp_path: Path) -> None:
    screen, audio = _make_inputs(tmp_path)
    sprites = tmp_path / "sprites"
    _make_sprites(sprites, size=128)
    script = tmp_path / "script.txt"
    script.write_text("mama hello moon", encoding="utf-8")
    out = tmp_path / "rhubarb.mp4"
    result = TalkingAvatarSkill().run(
        screen,
        audio,
        out,
        words=WORDS,
        avatar_path=sprites,
        size=0.4,
        margin=16,
        lipsync="rhubarb",
        script=script,
        motion=False,
    )
    sidecar = json.loads(Path(result["timeline"]).read_text())
    assert sidecar["engine"] == "rhubarb"
    assert sidecar["script_source"] == "file"
    assert sidecar["frames"]
    assert len(sidecar["frames"]) == 60
    assert sidecar["stress_open"] is True
    assert isinstance(sidecar["stress_upgraded"], int)


@needs_ffmpeg
def test_missing_rhubarb_falls_back_to_loudness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("ffmpeg/ffprobe missing")
    screen, audio = _make_inputs(tmp_path)
    sprites = tmp_path / "sprites"
    _make_sprites(sprites, size=128)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ffmpeg").symlink_to(ffmpeg)
    (bin_dir / "ffprobe").symlink_to(ffprobe)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("RHUBARB_BIN", raising=False)

    out = tmp_path / "fallback.mp4"
    result = TalkingAvatarSkill().run(
        screen,
        audio,
        out,
        words=WORDS,
        avatar_path=sprites,
        size=0.4,
        margin=16,
        lipsync="rhubarb",
        motion=False,
    )
    sidecar = json.loads(Path(result["timeline"]).read_text())
    assert sidecar["engine"] == "loudness"
    assert sidecar["frames"]
    assert sidecar["cues"] == []


# --- hybrid lip sync (Rhubarb + measured M/B/P hints) ---


def _hints_payload() -> dict[str, object]:
    return {
        "version": 1,
        "bilabial": [0.15, 1.35],
        "words": [
            {"text": "mama", "start": 0.1, "end": 0.6, "phones": ["M", "AA1", "M", "AH0"]},
            {"text": "hello", "start": 0.7, "end": 1.2, "phones": ["HH", "AH0", "L", "OW1"]},
            {"text": "moon", "start": 1.3, "end": 1.9, "phones": ["M", "UW1", "N"]},
        ],
    }


def test_hybrid_is_a_lipsync_engine() -> None:
    assert "hybrid" in LIPSYNC_ENGINES


def test_aligner_closures_move_onto_marks_and_stray_closures_become_b() -> None:
    shapes = ["C", "A", "A", "C", "C", "C", "C", "C"]
    out = aligner_closures(shapes, [0.5], fps=10)
    assert out == ["C", "B", "B", "C", "A", "A", "C", "C"]


def test_slots_weight_vowels_twice_consonants() -> None:
    spread = slots(("K", "AE1", "T"), 0.0, 0.4)
    durations = [round(end - start, 6) for _phone, start, end in spread]
    assert durations == [0.1, 0.2, 0.1]
    assert spread[-1][2] == pytest.approx(0.4)


def test_fix_inserts_missing_closure_open_and_rounded_mouths() -> None:
    shapes = ["B"] * 20
    words = [
        HintWord("map", 0.0, 0.6, ("M", "AE1", "P")),
        HintWord("go", 1.0, 1.6, ("G", "OW1")),
    ]
    out, counts = fix(shapes, words, fps=10)
    assert counts == {"closure": 1, "open": 1, "rounded": 1}
    assert "A" in out[0:7]
    assert "C" in out[0:7]
    assert "E" in out[10:17]


def test_fix_leaves_words_that_already_have_their_mouths() -> None:
    shapes = ["A", "A", "C", "C", "B", "B"]
    out, counts = fix(shapes, [HintWord("map", 0.0, 0.5, ("M", "AE1", "P"))], fps=10)
    assert out == shapes
    assert counts == {"closure": 0, "open": 0, "rounded": 0}


def test_load_hints_validates_version_and_reads_file(tmp_path: Path) -> None:
    path = tmp_path / "hints.json"
    path.write_text(json.dumps(_hints_payload()), encoding="utf-8")
    hints = load_hints(path)
    assert isinstance(hints, AlignHints)
    assert hints.bilabial == (0.15, 1.35)
    assert hints.words[2].phones == ("M", "UW1", "N")
    with pytest.raises(HintsError):
        load_hints({**_hints_payload(), "version": 2})


def test_generate_align_hints_runs_once_then_uses_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import talking_avatar.talking_avatar as ta

    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"audio")
    script = tmp_path / "script.txt"
    script.write_text("mama hello moon", encoding="utf-8")
    calls: list[tuple[str, Path]] = []

    def fake_run(python: str, _audio: Path, _script: Path, output: Path) -> None:
        calls.append((python, Path(output)))
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(_hints_payload()), encoding="utf-8")

    monkeypatch.setattr(ta, "run_align_script", fake_run)
    first = generate_align_hints(audio, script, tmp_path / "work", python="py")
    second = generate_align_hints(audio, script, tmp_path / "work", python="py")
    assert first == second
    assert len(calls) == 1
    assert calls[0][1] == align_hints_cache_path(audio, script, tmp_path / "work")


def test_find_lipsync_python_honours_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VE_LIPSYNC_PYTHON", str(tmp_path / "missing-python"))
    assert find_lipsync_python() is None
    fake = tmp_path / "python"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("VE_LIPSYNC_PYTHON", str(fake))
    assert find_lipsync_python() == str(fake)


@needs_ffmpeg
def test_hybrid_without_script_or_hints_errors(tmp_path: Path) -> None:
    screen, audio = _make_inputs(tmp_path)
    with pytest.raises(TalkingAvatarError, match="--script"):
        TalkingAvatarSkill().run(
            screen, audio, tmp_path / "out.mp4", words=WORDS, lipsync="hybrid", motion=False
        )


@needs_ffmpeg
@needs_rhubarb
def test_hybrid_falls_back_to_rhubarb_without_aligner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import talking_avatar.talking_avatar as ta

    monkeypatch.setenv("VE_LIPSYNC_PYTHON", str(tmp_path / "missing-python"))
    monkeypatch.setattr(ta, "run_align_script", lambda *a, **k: pytest.fail("aligner must not run"))
    screen, audio = _make_inputs(tmp_path)
    sprites = tmp_path / "sprites"
    _make_sprites(sprites, size=128)
    script = tmp_path / "script.txt"
    script.write_text("mama hello moon", encoding="utf-8")
    result = TalkingAvatarSkill().run(
        screen,
        audio,
        tmp_path / "out.mp4",
        words=WORDS,
        avatar_path=sprites,
        size=0.4,
        lipsync="hybrid",
        script=script,
        motion=False,
    )
    sidecar = json.loads(Path(result["timeline"]).read_text())
    assert sidecar["engine"] == "rhubarb"


@needs_ffmpeg
@needs_rhubarb
def test_hybrid_with_hints_file_skips_aligner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import talking_avatar.talking_avatar as ta

    monkeypatch.setattr(ta, "run_align_script", lambda *a, **k: pytest.fail("aligner must not run"))
    screen, audio = _make_inputs(tmp_path)
    sprites = tmp_path / "sprites"
    _make_sprites(sprites, size=128)
    hints = tmp_path / "hints.json"
    hints.write_text(json.dumps(_hints_payload()), encoding="utf-8")
    result = TalkingAvatarSkill().run(
        screen,
        audio,
        tmp_path / "out.mp4",
        words=WORDS,
        avatar_path=sprites,
        size=0.4,
        lipsync="hybrid",
        align_hints=hints,
        motion=False,
    )
    sidecar = json.loads(Path(result["timeline"]).read_text())
    assert sidecar["engine"] == "hybrid"
    shapes = [frame["shape"] for frame in sidecar["frames"]]
    # 30 fps: closures sit on the 0.15 s and 1.35 s marks
    assert shapes[4] == "A" and shapes[40] == "A"


def test_words_with_phones_takes_phones_by_text_and_engine_spans() -> None:
    hints = parse_hints(_hints_payload())
    words = words_with_phones([("Mama,", 0.0, 0.4), WordTiming("unknown", 0.5, 0.9)], hints)
    assert words[0] == HintWord("Mama,", 0.0, 0.4, ("M", "AA1", "M", "AH0"))
    assert words[1].phones == ()


def test_hybrid_timeline_delays_rhubarb_one_whole_frame() -> None:
    cues = [MouthCue(0.0, 0.3, "C"), MouthCue(0.3, 0.6, "E")]
    hints = parse_hints({"version": 1, "bilabial": [], "words": []})
    plain = build_rhubarb_timeline(cues, duration=0.6, fps=10, lead=0.0, stress_open=False)
    hybrid = build_hybrid_timeline(cues, hints, duration=0.6, fps=10, lead=0.0, stress_open=False)
    assert [f.shape for f in hybrid] == ["X"] + [f.shape for f in plain][:-1]
