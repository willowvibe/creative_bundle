"""Render the layered SVG avatar and build animated RGBA frames.

Each top-level ``<g id="...">`` in the SVG is a layer.  Headless Chrome renders
one layer at a time to a transparent PNG by hiding every other group with
injected CSS; PIL then composites ``base`` + one eyes layer + one mouth layer.
The 2 x 9 combinations are cached in memory because an animation reuses them
hundreds of times.
"""

from __future__ import annotations

import inspect
import math
import random
import re
import shutil
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import structlog
from PIL import Image
from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, run_subprocess

from talking_avatar.bubbles import (
    AvatarAction,
    Bubble,
    BubbleArt,
    draw_bubble,
    render_bubble_art,
)
from talking_avatar.timeline import REST_SHAPE, VisemeFrame, blink_frames

logger = structlog.get_logger(__name__)

BASE_LAYER = "base"
EYE_LAYERS = ("eyes-open", "eyes-closed")
MOUTH_LAYERS = (
    "mouth-X",
    "mouth-A",
    "mouth-B",
    "mouth-C",
    "mouth-D",
    "mouth-E",
    "mouth-F",
    "mouth-G",
    "mouth-H",
)
ALL_LAYERS = (BASE_LAYER, *EYE_LAYERS, *MOUTH_LAYERS)

_SVG_ROOT = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)

#: Headroom around the avatar so the vertical bob and syllable bounce do not
#: clip the antenna or the body.
MOTION_PADDING = 0.05
BOB_AMPLITUDE = 0.01
RMS_LIFT = 0.03
BOB_PERIOD_SECONDS = 2.4

BREATH_AMPLITUDE = 0.005
BREATH_PERIOD_SECONDS = 4.0
ENTRANCE_SECONDS = 0.5
EXIT_SECONDS = 0.5
SENTENCE_GAP_SECONDS = 0.35
TILT_PEAK_DEGREES = 1.5
TILT_EASE_IN_SECONDS = 0.4
TILT_HOLD_SECONDS = 0.6
TILT_EASE_OUT_SECONDS = 0.6
TILT_MIN_INTERVAL_SECONDS = 2.5


@dataclass(frozen=True)
class TiltEvent:
    """A scheduled head tilt: ``direction`` is +1 or -1."""

    time: float
    direction: int


class AvatarRenderError(RuntimeError):
    """Raised when the avatar layers cannot be rendered."""


def find_chrome() -> str | None:
    """Return the Chrome/Chromium executable used for layer screenshots."""
    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    ):
        executable = shutil.which(name)
        if executable:
            return executable
    return None


def _layer_html(svg_text: str, layer_id: str, size: int) -> str:
    """Return HTML that renders only ``layer_id`` at ``size`` pixels."""
    root = _SVG_ROOT.search(svg_text)
    if root is None:
        raise AvatarRenderError("avatar SVG has no <svg> root element")
    layer_style = (
        "<style>"
        "svg > g { display: none !important; }"
        f"#{layer_id} {{ display: inline !important; }}"
        "</style>"
    )
    body = svg_text[: root.end()] + layer_style + svg_text[root.end() :]
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        "<style>"
        "html, body { margin: 0; padding: 0; background: transparent; overflow: hidden; }"
        f"svg {{ display: block; width: {size}px; height: {size}px; }}"
        "</style></head>"
        f"<body>{body}</body></html>"
    )


PNG_MOUTH_SHAPES = tuple("ABCDEFGH")


class AvatarRenderer:
    """Render and cache avatar frames at one pixel size.

    ``source`` is either a layered SVG (rendered with headless Chrome) or a
    folder of same-size RGBA PNG sprites: ``base.png`` (open eyes, rest mouth),
    ``eyes_closed.png`` and ``mouth_A.png`` .. ``mouth_H.png`` patches.  Sprite
    folders may also include the optional ``brows_up.png`` and ``eyes_left.png``
    patches; SVG avatars ignore expression actions.
    """

    def __init__(self, source: Path | str, size: int, work_dir: Path | None = None) -> None:
        self.source = Path(source)
        self.size = max(2, int(size))
        if not self.source.exists():
            raise FileNotFoundError(f"Avatar not found: {self.source}")
        self.is_folder = self.source.is_dir()
        self._svg_text = "" if self.is_folder else self.source.read_text(encoding="utf-8")
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        if work_dir is None:
            self._temporary = tempfile.TemporaryDirectory(prefix="talking_avatar_")
            self.work_dir = Path(self._temporary.name)
        else:
            self.work_dir = Path(work_dir)
            self.work_dir.mkdir(parents=True, exist_ok=True)
        self._layers: dict[str, Image.Image] = {}
        self._composites: dict[tuple[str, bool, str], Image.Image] = {}

    @property
    def padding(self) -> int:
        return max(2, int(round(self.size * MOTION_PADDING)))

    @property
    def frame_size(self) -> int:
        """Pixel width/height of the padded RGBA frames streamed to FFmpeg."""
        canvas = self.size + 2 * self.padding
        return canvas + (canvas % 2)

    # -- layer loading -------------------------------------------------

    def _png_layer(self, name: str) -> Image.Image:
        path = self.source / name
        if not path.exists():
            raise AvatarRenderError(f"Avatar sprite folder is missing {name}: {self.source}")
        image = Image.open(path).convert("RGBA")
        if image.width != image.height:
            raise AvatarRenderError(f"Avatar sprite {name} must be square, got {image.size}")
        if image.size != (self.size, self.size):
            image = image.resize((self.size, self.size), Image.Resampling.LANCZOS)
        return image

    def _svg_layer(self, layer_id: str) -> Image.Image:
        if layer_id not in ALL_LAYERS:
            raise AvatarRenderError(f"Unknown avatar layer: {layer_id}")
        chrome = find_chrome()
        if chrome is None:
            raise AvatarRenderError("google-chrome was not found on PATH")
        png_path = self.work_dir / f"{layer_id}-{self.size}.png"
        html_path = self.work_dir / f"{layer_id}-{self.size}.html"
        html_path.write_text(_layer_html(self._svg_text, layer_id, self.size), encoding="utf-8")
        profile_dir = self.work_dir / "chrome-profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            chrome,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            f"--window-size={self.size},{self.size}",
            f"--user-data-dir={profile_dir}",
            "--default-background-color=00000000",
            "--virtual-time-budget=1000",
            f"--screenshot={png_path}",
            html_path.resolve().as_uri(),
        ]
        run_subprocess(cmd, timeout=RENDER_TIMEOUT_SECONDS)
        if not png_path.exists():
            raise AvatarRenderError(f"Chrome produced no screenshot for {layer_id}")
        image = Image.open(png_path).convert("RGBA")
        if image.size != (self.size, self.size):
            image = image.resize((self.size, self.size), Image.Resampling.LANCZOS)
        logger.debug("talking_avatar_layer_rendered", layer=layer_id, size=self.size)
        return image

    def layer(self, name: str) -> Image.Image:
        """Return one cached layer: an SVG group id or a PNG file name."""
        cached = self._layers.get(name)
        if cached is None:
            cached = self._png_layer(name) if self.is_folder else self._svg_layer(name)
            self._layers[name] = cached
        return cached

    # -- composites ----------------------------------------------------

    def _has_sprite(self, name: str) -> bool:
        return (self.source / name).is_file()

    def composite_for_shape(
        self,
        shape: str,
        *,
        eyes_closed: bool = False,
        glance: bool = False,
        brows: bool = False,
    ) -> Image.Image:
        """Return the cached composite for a timeline shape letter.

        The layers stack as base, brows, eyes, then mouth.  Closed eyes win
        over a glance so a blink is never interrupted by the leftward look.
        Optional sprite patches are skipped when the folder does not have them.
        """
        if shape not in PNG_MOUTH_SHAPES:
            shape = REST_SHAPE
        eyes_state = "closed" if eyes_closed else ("left" if glance else "open")
        key = (eyes_state, bool(brows), shape)
        cached = self._composites.get(key)
        if cached is not None:
            return cached
        if self.is_folder:
            image = self.layer("base.png").copy()
            if brows and self._has_sprite("brows_up.png"):
                image.alpha_composite(self.layer("brows_up.png"))
            if eyes_closed:
                image.alpha_composite(self.layer("eyes_closed.png"))
            elif glance and self._has_sprite("eyes_left.png"):
                image.alpha_composite(self.layer("eyes_left.png"))
            if shape != REST_SHAPE:
                image.alpha_composite(self.layer(f"mouth_{shape}.png"))
        else:
            image = self.layer(BASE_LAYER).copy()
            image.alpha_composite(self.layer("eyes-closed" if eyes_closed else "eyes-open"))
            image.alpha_composite(self.layer(f"mouth-{shape}"))
        self._composites[key] = image
        return image


def bob_offset(frame_time: float, avatar_size: int, rms: float = 0.0) -> int:
    """Vertical offset in pixels: gentle sine bob plus an RMS-scaled bounce."""
    bob = math.sin(2.0 * math.pi * frame_time / BOB_PERIOD_SECONDS) * BOB_AMPLITUDE
    lift = -max(0.0, min(1.0, float(rms))) * RMS_LIFT
    return int(round((bob + lift) * avatar_size))


def max_bob_overscan(avatar_size: int) -> int:
    """Pixels to draw an edge-anchored avatar below the frame so the bob cannot lift it."""
    return max(0, int(math.ceil((BOB_AMPLITUDE + RMS_LIFT) * avatar_size)))


def _smoothstep(value: float) -> float:
    value = max(0.0, min(1.0, float(value)))
    return value * value * (3.0 - 2.0 * value)


def sentence_start_times(timeline: Sequence[VisemeFrame], *, fps: float) -> list[float]:
    """Times where speech resumes after at least a 0.35 s run of ``X``."""
    if fps <= 0:
        return []
    gap_frames = max(1, int(math.ceil(SENTENCE_GAP_SECONDS * fps)))
    starts: list[float] = []
    silent_run = 0
    for frame in timeline:
        if frame.shape == REST_SHAPE:
            silent_run += 1
            continue
        if silent_run >= gap_frames:
            starts.append(frame.time)
        silent_run = 0
    return starts


def plan_tilts(
    timeline: Sequence[VisemeFrame],
    *,
    fps: float = 30.0,
    seed: int = 0,
    bubble_times: Sequence[float] = (),
) -> list[TiltEvent]:
    """Return the seeded tilt schedule, at most one every 2.5 s."""
    triggers = [*sentence_start_times(timeline, fps=fps)]
    triggers.extend(float(value) for value in bubble_times)
    triggers = sorted({round(value, 6) for value in triggers if value >= 0.0})
    rng = random.Random(seed)
    direction = 1 if rng.random() < 0.5 else -1
    tilts: list[TiltEvent] = []
    previous: float | None = None
    for trigger in triggers:
        if previous is not None and trigger - previous < TILT_MIN_INTERVAL_SECONDS - 1e-9:
            continue
        tilts.append(TiltEvent(time=trigger, direction=direction))
        previous = trigger
        direction = -direction
    return tilts


def tilt_angle_degrees(time: float, tilts: Sequence[TiltEvent]) -> float:
    """Angle at ``time``: smoothstep in, hold, smoothstep out, then reset."""
    for event in tilts:
        elapsed = time - event.time
        if elapsed < 0.0:
            break
        if elapsed <= TILT_EASE_IN_SECONDS:
            amount = _smoothstep(elapsed / TILT_EASE_IN_SECONDS)
        elif elapsed <= TILT_EASE_IN_SECONDS + TILT_HOLD_SECONDS:
            amount = 1.0
        elif elapsed <= TILT_EASE_IN_SECONDS + TILT_HOLD_SECONDS + TILT_EASE_OUT_SECONDS:
            tail = (elapsed - TILT_EASE_IN_SECONDS - TILT_HOLD_SECONDS) / TILT_EASE_OUT_SECONDS
            amount = 1.0 - _smoothstep(tail)
        else:
            continue
        return event.direction * TILT_PEAK_DEGREES * amount
    return 0.0


def breath_scale(time: float) -> float:
    """Subtle breathing scale: 1 +/- 0.005 over a four-second cycle."""
    return 1.0 + BREATH_AMPLITUDE * math.sin(2.0 * math.pi * time / BREATH_PERIOD_SECONDS)


def slide_offset(time: float, duration: float, distance: int) -> int:
    """Entrance/exit slide in pixels (0 once the avatar is fully on frame)."""
    if distance <= 0 or duration <= 0.0:
        return 0
    if time < ENTRANCE_SECONDS:
        unit = max(0.0, min(1.0, time / ENTRANCE_SECONDS))
        return int(
            round(distance * (1.0 - unit) ** 2)
        )  # quadratic: visible glide, 25% left at mid-way
    remaining = duration - time
    if remaining < EXIT_SECONDS:
        unit = max(0.0, min(1.0, 1.0 - remaining / EXIT_SECONDS))
        return int(round(distance * unit**2))
    return 0


def _alpha_composite_clipped(
    base: Image.Image,
    image: Image.Image,
    xy: tuple[int, int],
) -> None:
    x, y = xy
    source_x = max(0, -x)
    source_y = max(0, -y)
    dest_x = max(0, x)
    dest_y = max(0, y)
    width = min(image.width - source_x, base.width - dest_x)
    height = min(image.height - source_y, base.height - dest_y)
    if width <= 0 or height <= 0:
        return
    patch = image.crop((source_x, source_y, source_x + width, source_y + height))
    base.alpha_composite(patch, (dest_x, dest_y))


def _motion_canvas(
    composite: Image.Image,
    *,
    pad: int,
    size: int,
    frame_size: int,
    scale: float,
    slide: int,
    angle: float,
    bob: int = 0,
) -> Image.Image:
    if abs(scale - 1.0) > 1e-9:
        scaled = (
            max(1, int(round(size * scale))),
            max(1, int(round(size * scale))),
        )
        image = composite.resize(scaled, Image.Resampling.BICUBIC)
    else:
        image = composite
    left = pad + (size - image.width) // 2
    top = pad + size - image.height + slide + bob
    pivot = (pad + size / 2.0, pad + size + slide + bob)
    canvas = Image.new("RGBA", (frame_size, frame_size), (0, 0, 0, 0))
    _alpha_composite_clipped(canvas, image, (left, top))
    if abs(angle) > 1e-9:
        canvas = canvas.rotate(
            angle,
            resample=Image.Resampling.BICUBIC,
            center=pivot,
            fillcolor=(0, 0, 0, 0),
        )
    return canvas


def _renderer_accepts_expressions(renderer: AvatarRenderer) -> bool:
    """Whether a renderer understands the optional glance/brows keywords."""
    try:
        parameters = inspect.signature(renderer.composite_for_shape).parameters
    except (TypeError, ValueError):  # pragma: no cover - exotic callables
        return False
    return "glance" in parameters and "brows" in parameters


def _action_active(actions: Sequence[AvatarAction], kind: str, time: float) -> bool:
    return any(action.type == kind and action.start <= time < action.end for action in actions)


def _is_action_edge(active: Sequence[bool], index: int) -> bool:
    if not active[index]:
        return False
    before = active[index - 1] if index > 0 else False
    after = active[index + 1] if index + 1 < len(active) else False
    return not before or not after


def iter_avatar_frames(
    renderer: AvatarRenderer,
    timeline: Sequence[VisemeFrame],
    *,
    fps: float = 30.0,
    seed: int = 0,
    duration: float | None = None,
    crossfade: bool = True,
    bounce: bool = False,
    motion: bool = True,
    bubble_times: Sequence[float] | None = None,
    bubbles: Sequence[Bubble] | None = None,
    actions: Sequence[AvatarAction] | None = None,
    stage_size: tuple[int, int] | None = None,
) -> Iterator[bytes]:
    """Yield padded RGBA frames ready for ``ffmpeg -f rawvideo``.

    ``crossfade`` blends the first frame of a new shape 50/50 with the previous
    composite; closures (``A``) cut in hard.  ``bounce`` is off by default, so
    the head stays still unless the caller explicitly asks for the sine bob and
    loudness lift.  ``motion`` adds breathing, small seeded tilts and an
    entrance/exit slide; ``--no-motion`` keeps every frame still.  When bubbles
    are given the canvas grows into a bottom-right stage and the bubble is drawn
    in its upper-left area.  ``actions`` raise the brows or glance left; the
    first and last frame of each span blends 50/50 with its neutral composite.
    Blinks are always kept and win over a glance.
    """
    total_duration = duration if duration is not None else (len(timeline) / fps if fps else 0.0)
    closed_frames = set(blink_frames(total_duration, fps=fps, seed=seed))

    triggers = [float(value) for value in (bubble_times or ())]
    if bubbles:
        triggers.extend(bubble.start for bubble in bubbles)
    tilts = plan_tilts(timeline, fps=fps, seed=seed, bubble_times=triggers) if motion else []

    action_list = tuple(actions or ())
    brows_active = [_action_active(action_list, "brows", frame.time) for frame in timeline]
    glance_active = [_action_active(action_list, "glance", frame.time) for frame in timeline]
    supports_expressions = _renderer_accepts_expressions(renderer)

    def compose(
        shape: str,
        *,
        eyes_closed: bool,
        glance_on: bool,
        brows_on: bool,
    ) -> Image.Image:
        if supports_expressions:
            return renderer.composite_for_shape(
                shape,
                eyes_closed=eyes_closed,
                glance=glance_on,
                brows=brows_on,
            )
        return renderer.composite_for_shape(shape, eyes_closed=eyes_closed)

    pad = renderer.padding
    frame_size = renderer.frame_size
    bubble_arts: list[tuple[Bubble, BubbleArt, tuple[int, int]]] = []
    if bubbles and stage_size is not None:
        stage_width, stage_height = stage_size
        margin = max(4, int(round(renderer.size * 0.05)))
        max_width = max(24, stage_width - frame_size - 2 * margin)
        max_height = max(24, stage_height - 2 * margin)
        bubble_arts = [
            (
                bubble,
                render_bubble_art(bubble, max_width=max_width, max_height=max_height),
                (margin, margin),
            )
            for bubble in bubbles
        ]

    previous_shape: str | None = None
    for index, frame in enumerate(timeline):
        eyes_closed = index in closed_frames
        brows_on = brows_active[index]
        glance_on = glance_active[index]
        composite = compose(
            frame.shape,
            eyes_closed=eyes_closed,
            glance_on=glance_on,
            brows_on=brows_on,
        )
        if brows_on and _is_action_edge(brows_active, index):
            neutral = compose(
                frame.shape,
                eyes_closed=eyes_closed,
                glance_on=glance_on,
                brows_on=False,
            )
            composite = Image.blend(neutral, composite, 0.5)
        if glance_on and _is_action_edge(glance_active, index):
            neutral = compose(
                frame.shape,
                eyes_closed=eyes_closed,
                glance_on=False,
                brows_on=brows_on,
            )
            composite = Image.blend(neutral, composite, 0.5)
        if (
            crossfade
            and previous_shape is not None
            and frame.shape != previous_shape
            and frame.shape != "A"
        ):
            previous = compose(
                previous_shape,
                eyes_closed=eyes_closed,
                glance_on=glance_on,
                brows_on=brows_on,
            )
            composite = Image.blend(previous, composite, 0.5)
        previous_shape = frame.shape
        offset = bob_offset(frame.time, renderer.size, frame.rms) if bounce else 0
        if motion:
            canvas = _motion_canvas(
                composite,
                pad=pad,
                size=renderer.size,
                frame_size=frame_size,
                scale=breath_scale(frame.time),
                slide=slide_offset(frame.time, total_duration, frame_size),
                angle=tilt_angle_degrees(frame.time, tilts),
                bob=offset,
            )
        else:
            canvas = Image.new("RGBA", (frame_size, frame_size), (0, 0, 0, 0))
            canvas.paste(composite, (pad, pad + offset), composite)

        if stage_size is not None:
            stage_width, stage_height = stage_size
            stage = Image.new("RGBA", (stage_width, stage_height), (0, 0, 0, 0))
            _alpha_composite_clipped(
                stage,
                canvas,
                (stage_width - frame_size, stage_height - frame_size),
            )
            for bubble, art, origin in bubble_arts:
                draw_bubble(stage, bubble, art, origin, frame.time)
            yield stage.tobytes()
        else:
            yield canvas.tobytes()


def render_avatar_frame(
    renderer: AvatarRenderer,
    shape: str,
    *,
    time: float = 0.0,
    rms: float = 0.0,
    eyes_closed: bool = False,
    glance: bool = False,
    brows: bool = False,
    bounce: bool = False,
) -> Image.Image:
    """Render a single padded RGBA frame (useful for previews and tests)."""
    pad = renderer.padding
    frame_size = renderer.frame_size
    canvas = Image.new("RGBA", (frame_size, frame_size), (0, 0, 0, 0))
    if _renderer_accepts_expressions(renderer):
        composite = renderer.composite_for_shape(
            shape,
            eyes_closed=eyes_closed,
            glance=glance,
            brows=brows,
        )
    else:
        composite = renderer.composite_for_shape(shape, eyes_closed=eyes_closed)
    offset = bob_offset(time, renderer.size, rms) if bounce else 0
    canvas.paste(composite, (pad, pad + offset), composite)
    return canvas


__all__ = [
    "ALL_LAYERS",
    "AvatarRenderError",
    "AvatarRenderer",
    "BASE_LAYER",
    "EYE_LAYERS",
    "MOUTH_LAYERS",
    "SENTENCE_GAP_SECONDS",
    "TILT_EASE_IN_SECONDS",
    "TILT_EASE_OUT_SECONDS",
    "TILT_HOLD_SECONDS",
    "TILT_MIN_INTERVAL_SECONDS",
    "TILT_PEAK_DEGREES",
    "TiltEvent",
    "bob_offset",
    "breath_scale",
    "find_chrome",
    "iter_avatar_frames",
    "max_bob_overscan",
    "plan_tilts",
    "render_avatar_frame",
    "sentence_start_times",
    "slide_offset",
    "tilt_angle_degrees",
]
