"""Formal protocol for video editor skills."""

from __future__ import annotations

from typing import Any, Protocol

from video_editor_state.job import JobContext


class SkillProtocol(Protocol):
    """A skill is a pipeline stage that takes a job context and returns outputs."""

    @property
    def name(self) -> str: ...

    def run(self, ctx: JobContext, **kwargs: Any) -> dict[str, Any]:
        """Execute the skill and return stage outputs."""
        ...
