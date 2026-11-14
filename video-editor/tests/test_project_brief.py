"""Tests for the project.md / ProjectBrief parser and wiring."""

from __future__ import annotations

from pathlib import Path

from video_editor_state.job import JobContext
from video_editor_state.project_brief import (
    ProjectBrief,
    load_project_brief,
    write_project_brief,
)


def test_load_project_brief_returns_none_when_missing(tmp_path: Path) -> None:
    """A work_dir without project.md returns None (not an exception)."""
    work_dir = tmp_path / "empty"
    work_dir.mkdir()
    assert load_project_brief(work_dir) is None


def test_load_project_brief_parses_front_matter(tmp_path: Path) -> None:
    """A project.md with YAML front-matter is parsed into a ProjectBrief."""
    md = tmp_path / "project.md"
    md.write_text(
        "---\n"
        "title: Saju Reel\n"
        "audience: Curious newcomers\n"
        "tone: Calm, mystical\n"
        "target_duration_s: 60\n"
        "key_segments:\n"
        "  - hook\n"
        "  - four pillars\n"
        "  - closing\n"
        "brand: saju\n"
        "---\n"
        "# Saju Reel\n"
        "\n"
        "Notes: keep captions legible. Music ducks under voiceover.\n",
        encoding="utf-8",
    )
    brief = load_project_brief(md.parent)
    assert brief is not None
    assert brief.title == "Saju Reel"
    assert brief.audience == "Curious newcomers"
    assert brief.tone == "Calm, mystical"
    assert brief.target_duration_s == 60.0
    assert brief.key_segments == ["hook", "four pillars", "closing"]
    assert brief.brand == "saju"
    assert "captions legible" in brief.notes


def test_load_project_brief_handles_missing_fields(tmp_path: Path) -> None:
    """A project.md with only a title and a notes paragraph parses with
    empty strings/defaults for the other fields. The parser must not raise
    on partial front-matter."""
    md = tmp_path / "project.md"
    md.write_text(
        "---\ntitle: Minimal\n---\nJust a free-form paragraph of notes here.\n",
        encoding="utf-8",
    )
    brief = load_project_brief(md.parent)
    assert brief is not None
    assert brief.title == "Minimal"
    assert brief.audience == ""
    assert brief.tone == ""
    assert brief.target_duration_s is None
    assert brief.key_segments == []
    assert brief.brand is None
    assert "Just a free-form paragraph" in brief.notes


def test_load_project_brief_handles_no_front_matter(tmp_path: Path) -> None:
    """A project.md without YAML front-matter still produces a brief — the
    first H1 (if any) becomes the title, and the first non-empty paragraph
    becomes the notes. The parser must not assume the file has front-matter."""
    md = tmp_path / "project.md"
    md.write_text(
        "# Saju Reel\n\nKeep captions legible against any background.\n",
        encoding="utf-8",
    )
    brief = load_project_brief(md.parent)
    assert brief is not None
    assert brief.title == "Saju Reel"
    assert "captions legible" in brief.notes


def test_load_project_brief_handles_malformed_front_matter(tmp_path: Path) -> None:
    """Malformed YAML in the front-matter is treated as a plain markdown
    file (no crash). The first H1 becomes the title and the body becomes
    the notes — the parser falls back rather than raising."""
    md = tmp_path / "project.md"
    md.write_text(
        "---\n"
        "title: Broken: [unclosed\n"
        "garbage: !! not yaml\n"
        "---\n"
        "# Recovered Title\n"
        "\n"
        "Recovered body paragraph.\n",
        encoding="utf-8",
    )
    brief = load_project_brief(md.parent)
    assert brief is not None
    # Falls back to extracting the H1 from the body.
    assert brief.title == "Recovered Title"
    assert "Recovered body paragraph" in brief.notes


def test_load_project_brief_handles_utf8_bom(tmp_path: Path) -> None:
    """Bug #43 regression: a UTF-8 BOM at the start of project.md
    (common on Windows / VS Code / Notepad) does not lose the
    front-matter. Before the fix, the BOM leaked into the first
    line, the front-matter regex didn't match, and all structured
    fields were silently lost (the file fell back to H1-as-title
    with empty body)."""
    md = tmp_path / "project.md"
    # Write the file in binary mode to include the BOM bytes.
    md.write_bytes(
        b"\xef\xbb\xbf"  # UTF-8 BOM
        + b"---\n"
        + b"title: With BOM\n"
        + b"audience: Windows users\n"
        + b"brand: saju\n"
        + b"---\n"
        + b"# H1 Title\n"
        + b"\n"
        + b"Body notes here.\n"
    )
    brief = load_project_brief(md.parent)
    assert brief is not None
    assert brief.title == "With BOM"
    assert brief.audience == "Windows users"
    assert brief.brand == "saju"
    assert "Body notes" in brief.notes


def test_load_project_brief_honors_notes_in_front_matter(tmp_path: Path) -> None:
    """Bug #44 regression: ``notes:`` in YAML front-matter is honored
    instead of being silently ignored. A user who explicitly sets
    notes in the front-matter (e.g. an editor writing a longer
    brief) gets the value into the model rather than having it
    replaced by the body paragraph extract.
    """
    md = tmp_path / "project.md"
    md.write_text(
        "---\n"
        "title: My Project\n"
        "notes: 'Editor-supplied notes win'\n"
        "---\n"
        "# H1 Title\n"
        "\n"
        "Body text should NOT be used as notes when front-matter supplies one.\n",
        encoding="utf-8",
    )
    brief = load_project_brief(md.parent)
    assert brief is not None
    assert brief.title == "My Project"
    assert brief.notes == "Editor-supplied notes win"


def test_load_project_brief_notes_without_title(tmp_path: Path) -> None:
    """Bug #128 regression: front-matter ``notes:`` is honored even when
    ``title:`` is absent. Previously the parser required ``title`` to be
    present for front-matter notes to win; without it the body paragraph was
    used instead."""
    md = tmp_path / "project.md"
    md.write_text(
        "---\n"
        "notes: Front-matter notes should win\n"
        "---\n"
        "# H1 Title\n"
        "\n"
        "Body paragraph that should not become notes.\n",
        encoding="utf-8",
    )
    brief = load_project_brief(md.parent)
    assert brief is not None
    assert brief.title == "H1 Title"
    assert brief.notes == "Front-matter notes should win"


def test_project_brief_is_pydantic() -> None:
    """ProjectBrief is a Pydantic BaseModel with the documented fields.
    Locking down the schema so a refactor doesn't silently drop a field."""
    fields = set(ProjectBrief.model_fields.keys())
    assert {
        "job_id",
        "title",
        "audience",
        "tone",
        "target_duration_s",
        "key_segments",
        "notes",
        "brand",
    } <= fields


def test_write_project_brief_roundtrip(tmp_path: Path) -> None:
    """Writing a brief to project.md and reading it back yields the same fields."""
    work_dir = tmp_path / "roundtrip"
    work_dir.mkdir()
    brief = ProjectBrief(
        job_id="job-x",
        title="Roundtrip",
        audience="Test audience",
        tone="Neutral",
        target_duration_s=30.0,
        key_segments=["intro", "outro"],
        notes="Some notes.",
        brand="test-brand",
    )
    write_project_brief(work_dir, brief)
    loaded = load_project_brief(work_dir)
    assert loaded is not None
    assert loaded.title == brief.title
    assert loaded.audience == brief.audience
    assert loaded.tone == brief.tone
    assert loaded.target_duration_s == brief.target_duration_s
    assert loaded.key_segments == brief.key_segments
    assert loaded.notes == brief.notes
    assert loaded.brand == brief.brand


def test_write_project_brief_roundtrip_multiline_notes(tmp_path: Path) -> None:
    """Bug #44 follow-up: a multi-line notes value roundtrips without
    leaking literal ``\\n`` escape sequences into the reloaded value.
    The writer collapses newlines to spaces so ``repr()`` stays
    decodable by the front-matter parser.
    """
    work_dir = tmp_path / "multiline"
    work_dir.mkdir()
    brief = ProjectBrief(
        job_id="job-ml",
        title="Multiline",
        notes="First thought.\nSecond thought.\nThird thought.",
    )
    write_project_brief(work_dir, brief)
    loaded = load_project_brief(work_dir)
    assert loaded is not None
    assert loaded.notes == "First thought. Second thought. Third thought."


def test_write_project_brief_roundtrip_backslashes_in_fields(tmp_path: Path) -> None:
    """Bug #46 regression: a field whose value contains a backslash
    (e.g. ``audience="C:\\Users\\Admin"``) must roundtrip without
    doubling the backslashes on read. The writer quotes the value
    with ``repr()`` (which doubles backslashes); the parser used to
    strip the surrounding quotes without un-escaping the contents,
    so ``"back\\slash"`` on disk came back as ``"back\\\\slash"``
    (two backslashes) in memory.
    """
    work_dir = tmp_path / "bs"
    work_dir.mkdir()
    brief = ProjectBrief(
        job_id="job-bs",
        title=r"Has \ backslash",
        audience=r"C:\Users\Alice",
        tone=r"dry\sarcastic",
        notes=r"back\slash and a \n literal",
        brand=r"my\brand",
        key_segments=[r"normal", r"back\slash_seg", r"trail\ing"],
    )
    write_project_brief(work_dir, brief)
    loaded = load_project_brief(work_dir)
    assert loaded is not None
    assert loaded.title == brief.title
    assert loaded.audience == brief.audience
    assert loaded.tone == brief.tone
    assert loaded.notes == brief.notes
    assert loaded.brand == brief.brand
    assert loaded.key_segments == brief.key_segments


def test_load_project_brief_inline_key_segments_respect_commas_in_quotes(
    tmp_path: Path,
) -> None:
    """C19 regression: an inline ``key_segments`` list must not split on
    commas that live inside quoted items."""
    md = tmp_path / "project.md"
    md.write_text(
        '---\nkey_segments: ["intro, hook", "close"]\n---\n# Title\n',
        encoding="utf-8",
    )
    brief = load_project_brief(md.parent)
    assert brief is not None
    assert brief.key_segments == ["intro, hook", "close"]


def test_job_context_carries_project_brief(tmp_path: Path) -> None:
    """JobContext can carry an optional ProjectBrief; ctx.brief exposes it
    as a convenience attribute. When no brief is supplied, ctx.brief is None."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    # Without a brief, ctx.brief is None.
    ctx = JobContext(job_id="job-y", work_root=tmp_path, output_root=tmp_path)
    assert ctx.brief is None

    # With a brief supplied at construction, ctx.brief is the brief.
    brief = ProjectBrief(job_id="job-y", title="Test")
    ctx2 = JobContext(job_id="job-y", work_root=tmp_path, output_root=tmp_path, project_brief=brief)
    assert ctx2.brief is brief
    assert ctx2.brief.title == "Test"
