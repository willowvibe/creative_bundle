"""Transition skill: auto-select and apply cross-segment transitions.

Given a sequence of rendered segment files and optional per-segment labels,
the skill decides which transition (cut, fade, slide, wipe, etc.) belongs
between each adjacent pair, then renders a single output via FFmpeg.  The
existing ``packages/ffmpeg/transitions.py`` engine is used unchanged; this
skill adds selection logic and project metadata on top.

Selection rules (heuristic, no API calls):

- Very short segments (< 1.0 s) or speech-heavy segments get a hard cut.
- Calm / B-roll / music segments get a gentle fade.
- Action / beat / montage segments get a left/right slide or wipe.
- Scene-change boundaries get a wipe or radial transition.

The skill can also accept an explicit transition list, which is useful when
an upstream LLM or template has already made a decision.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import structlog
from video_editor_core.timeline import EDL
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_ffmpeg.transitions import (
    DEFAULT_TRANSITION_DURATION,
    TransitionKind,
    concat_with_transitions,
)
from video_editor_state.job import JobContext

logger = structlog.get_logger(__name__)

# Order must match the heuristic selector's preference cascade.
_TRANSITION_CATALOG: frozenset[TransitionKind] = frozenset(
    {
        "none",
        "fade",
        "slideleft",
        "slideright",
        "slidedown",
        "slideup",
        "wipeleft",
        "wiperight",
        "radial",
    }
)

# Keywords used by the rule-based selector.
_SPEECH_LABELS: frozenset[str] = frozenset(
    {"speech", "talk", "talking", "voice", "narration", "dialogue", "host", "a-roll"}
)
_CALM_LABELS: frozenset[str] = frozenset(
    {"broll", "b-roll", "calm", "scenery", "intro", "outro", "music"}
)
_ACTION_LABELS: frozenset[str] = frozenset(
    {"action", "montage", "beat", "fast", "sports", "dance", "energy"}
)
_SCENE_CHANGE_LABELS: frozenset[str] = frozenset(
    {"scene_change", "chapter", "transition", "location"}
)


class TransitionSkill:
    """Apply smart transitions between consecutive video segments."""

    @property
    def name(self) -> str:
        return "transitions"

    def __init__(
        self,
        ctx: JobContext,
        *,
        default: TransitionKind | Literal["auto"] = "auto",
        duration: float = DEFAULT_TRANSITION_DURATION,
        min_segment_duration: float = 1.0,
        render_options: RenderOptions | None = None,
    ):
        self.ctx = ctx
        self.default = default
        self.duration = duration
        self.min_segment_duration = min_segment_duration
        self.render_options = render_options or RenderOptions()
        self.output_dir = ctx.work_dir / "transitions"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _segment_labels(self, segment_paths: Sequence[Path], edl: EDL | None) -> list[str]:
        """Return a label for each segment, preferring EDL segment labels."""
        labels: list[str] = []
        if edl is not None:
            labels = [seg.label for seg in edl.segments]
        # Fallback to empty labels if counts mismatch or EDL missing.
        while len(labels) < len(segment_paths):
            labels.append("")
        return labels[: len(segment_paths)]

    def _durations(self, segment_paths: Sequence[Path]) -> list[float]:
        """Probe each segment and return its duration."""
        durations: list[float] = []
        for path in segment_paths:
            info = probe_media(path)
            durations.append(info.duration or 0.0)
        return durations

    @staticmethod
    def _label_match(label: str, vocabulary: frozenset[str]) -> bool:
        """Return True when any vocabulary token appears in the label."""
        lowered = label.lower()
        return any(token in lowered for token in vocabulary)

    def _select_transition(
        self,
        prev_label: str,
        next_label: str,
        prev_duration: float,
        next_duration: float,
    ) -> TransitionKind:
        """Rule-based transition selection for one segment boundary."""
        if self.default != "auto":
            return self.default

        # Hard cut for very short segments (not enough time for an overlap).
        if prev_duration < self.min_segment_duration or next_duration < self.min_segment_duration:
            return "none"

        # Hard cut when both sides are speech — cross-fades over talking heads
        # feel unnatural.
        if self._label_match(prev_label, _SPEECH_LABELS) and self._label_match(
            next_label, _SPEECH_LABELS
        ):
            return "none"

        # Scene change boundary gets a wipe or radial if we have enough media.
        if self._label_match(prev_label, _SCENE_CHANGE_LABELS) or self._label_match(
            next_label, _SCENE_CHANGE_LABELS
        ):
            return "wipeleft"

        # Action / montage gets motion.
        if self._label_match(prev_label, _ACTION_LABELS) or self._label_match(
            next_label, _ACTION_LABELS
        ):
            return "slideleft"

        # Calm / B-roll gets a fade.
        if self._label_match(prev_label, _CALM_LABELS) or self._label_match(
            next_label, _CALM_LABELS
        ):
            return "fade"

        # Default fallback.
        return "fade"

    def select_transitions(
        self,
        segment_paths: Sequence[Path],
        *,
        edl: EDL | None = None,
        overrides: Sequence[TransitionKind] | None = None,
    ) -> list[TransitionKind]:
        """Return the transition to use between each adjacent pair of segments.

        Args:
            segment_paths: Ordered segment media files.
            edl: Optional EDL whose segment labels drive heuristic selection.
            overrides: Optional explicit per-pair transition list.  Must have
                length ``len(segment_paths) - 1``.

        Returns:
            List of transitions, one fewer than ``segment_paths``.
        """
        if overrides is not None:
            expected = max(0, len(segment_paths) - 1)
            if len(overrides) != expected:
                raise ValueError(f"overrides length {len(overrides)} != {expected} (segments - 1)")
            return list(overrides)

        labels = self._segment_labels(segment_paths, edl)
        durations = self._durations(segment_paths)
        transitions: list[TransitionKind] = []
        for idx in range(1, len(segment_paths)):
            transitions.append(
                self._select_transition(
                    labels[idx - 1],
                    labels[idx],
                    durations[idx - 1],
                    durations[idx],
                )
            )
        return transitions

    def _build_plan(
        self,
        segment_paths: Sequence[Path],
        transitions: Sequence[TransitionKind],
        output_path: Path,
    ) -> Path:
        """Write a JSON plan describing the applied transitions."""
        plan_path = self.output_dir / "plan.json"
        plan = {
            "job_id": self.ctx.job_id,
            "output": str(output_path),
            "default": self.default,
            "duration": self.duration,
            "segments": [str(p) for p in segment_paths],
            "boundaries": [
                {
                    "from_segment": idx,
                    "to_segment": idx + 1,
                    "transition": transition,
                    "duration": self.duration if transition != "none" else 0.0,
                }
                for idx, transition in enumerate(transitions)
            ],
        }
        plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        return plan_path

    def run(
        self,
        segment_paths: Sequence[Path | str],
        *,
        output_name: str = "with_transitions.mp4",
        edl: EDL | None = None,
        transitions: Sequence[TransitionKind] | None = None,
    ) -> dict[str, Any]:
        """Render a single video with transitions between segments.

        Args:
            segment_paths: Ordered segment media files.
            output_name: Output filename under ``transitions/``.
            edl: Optional EDL whose segment labels guide heuristic selection.
            transitions: Optional explicit per-pair transition list.  If omitted,
                transitions are selected automatically.

        Returns:
            Skill output dict with ``output`` and ``plan`` paths plus the
            transition list.
        """
        self.ctx.ensure_dirs()
        paths = [Path(p) for p in segment_paths]
        if len(paths) < 2:
            raise ValueError("at least two segments are required for transitions")

        selected = self.select_transitions(paths, edl=edl, overrides=transitions)
        output_path = self.output_dir / output_name

        logger.info(
            "applying_transitions",
            segments=len(paths),
            transitions=selected,
            duration=self.duration,
        )

        # `concat_with_transitions` accepts a list of transitions for per-pair
        # application, falling back to plain concat for "none" or unsupported.
        rendered = concat_with_transitions(
            paths,
            output_path,
            transition=selected,
            transition_duration=self.duration,
            render_options=self.render_options,
        )
        plan_path = self._build_plan(paths, selected, rendered)

        return {
            "output": str(rendered),
            "plan": str(plan_path),
            "transitions": selected,
        }
