"""#276: a job id is exactly one directory name under its roots."""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_state.job import InvalidJobIdError, JobContext, validate_job_id


@pytest.mark.parametrize(
    "job_id",
    ["", ".", "..", "../escape", "a/b", "..\\x", "/abs", "tab\tname", "nul\x00", "x" * 201],
)
def test_unsafe_job_ids_are_refused(tmp_path: Path, job_id: str) -> None:
    with pytest.raises(InvalidJobIdError):
        JobContext(job_id=job_id, work_root=tmp_path / "w", output_root=tmp_path / "o")


@pytest.mark.parametrize(
    "job_id",
    ["job-1", "mcp-0123abcd", "short-day-master-deck", "My Story 2", "v1.2_final", "..hidden"],
)
def test_ordinary_job_ids_are_accepted(tmp_path: Path, job_id: str) -> None:
    ctx = JobContext(job_id=job_id, work_root=tmp_path / "w", output_root=tmp_path / "o")

    assert ctx.work_dir.parent == tmp_path / "w"
    assert ctx.output_dir.parent == tmp_path / "o"


def test_invalid_job_id_is_a_value_error() -> None:
    # Typer/pydantic callers that catch ValueError keep working.
    with pytest.raises(ValueError):
        validate_job_id("../x")


def test_a_symlinked_job_directory_cannot_escape_its_root(tmp_path: Path) -> None:
    """A valid one-component id can still be a symlink out of the root (#276)."""
    work = tmp_path / "w"
    output = tmp_path / "o"
    work.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (work / "job-1").symlink_to(outside, target_is_directory=True)

    with pytest.raises(InvalidJobIdError, match="escapes"):
        JobContext(job_id="job-1", work_root=work, output_root=output)
