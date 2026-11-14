"""Per-project ``project.md`` parser and ``ProjectBrief`` model.

A ``ProjectBrief`` is a small, structured summary of a video project
(title, audience, tone, target duration, key segments, editorial
notes, optional brand override). The pipeline reads
``<work_dir>/project.md`` at job construction time and threads the
brief through to the skills that want it (export, captions, music).

Format
------
``project.md`` is a Markdown file with an optional YAML front-matter
block delimited by ``---``. The parser understands:

* YAML front-matter: ``title``, ``audience``, ``tone``,
  ``target_duration_s`` (float), ``key_segments`` (list of strings),
  ``brand`` (string). Unknown keys are ignored.
* The first H1 (``# Title``) becomes the title when front-matter
  doesn't supply one.
* The first non-empty body paragraph becomes ``notes``.

Malformed YAML falls back to "treat the file as plain markdown" so
the parser never raises on user-edited input. The worst case is a
brief with a title from the H1 and the body as notes — still useful.

The ``write_project_brief()`` inverse function takes a ``ProjectBrief``
and writes it back to ``<work_dir>/project.md`` so the worker can
materialize a brief from a higher-level config (e.g. a JSON payload on
``ve brief``).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from video_editor_state.store import atomic_write_text


class ProjectBrief(BaseModel):
    """Per-project summary carried through the pipeline.

    ``job_id`` is populated by the writer (``ve brief``) and ignored
    on read — the brief describes the work, not the job identity.
    """

    job_id: str = ""
    title: str = ""
    audience: str = ""
    tone: str = ""
    target_duration_s: float | None = None
    key_segments: list[str] = Field(default_factory=list)
    notes: str = ""
    brand: str | None = None


# A bounded set of YAML keys we honor. Anything else is ignored so a
# project.md with extra metadata (e.g. ``director:``) doesn't break
# parsing or pollute the model.
_BRIEF_YAML_KEYS = {
    "title",
    "audience",
    "tone",
    "target_duration_s",
    "key_segments",
    "brand",
    # Bug #44: ``notes`` was on the ProjectBrief model but not in
    # the honor-list, so a user who put ``notes: ...`` in the
    # front-matter had it silently ignored (the body paragraph
    # overwrote it). Honoring ``notes`` here lets a hand-edited
    # brief override the body extract.
    "notes",
}

_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<yaml>.*?)\n---\s*\n?(?P<body>.*)\Z",
    re.DOTALL,
)


def _unquote(value: str) -> str:
    """Strip surrounding quotes (if any) and un-escape the contents.

    A bare value (``foo``) is returned as-is. A single- or double-quoted
    value (``'foo'`` / ``"foo"``) is fed to ``ast.literal_eval`` so
    backslash escapes and other Python literal escapes produced by
    ``repr()`` are correctly reversed. The writer uses ``repr()`` on
    every string field, so the file content is always a valid Python
    string literal — but we keep the bare-value pass-through so
    hand-edited briefs without quotes still parse.
    """
    if len(value) < 2 or value[0] != value[-1] or value[0] not in ("'", '"'):
        return value
    try:
        # ``ast.literal_eval`` on a single-quoted string literal handles
        # ``\\``, ``\n``, ``\t``, ``\uXXXX``, etc. the same way Python's
        # parser would, which is exactly the inverse of ``repr()``.
        result = ast.literal_eval(value)
        # The parser only calls us on values that came out of the
        # front-matter, so a non-str return is a malformed file. Fall
        # back to the raw slice so the user gets the value back
        # rather than a TypeError.
        return result if isinstance(result, str) else value[1:-1]
    except (ValueError, SyntaxError):
        # Defensive: a malformed quoted value (e.g. an unterminated
        # escape) falls back to the raw slice so the user gets the
        # value back rather than a parse error.
        return value[1:-1]


def _parse_front_matter(yaml_text: str) -> dict[str, Any]:
    """Parse the YAML front-matter block into a dict.

    A strict PyYAML ``safe_load`` would require PyYAML; the engine
    doesn't take a dep on it. We parse the small subset we use
    manually so the brief parser has no third-party deps.

    This parser is intentionally strict: a line that looks malformed
    (an unterminated bracket, a ``!!`` YAML tag, etc.) causes
    :class:`ValueError` to propagate so the caller can fall back to
    "treat the file as plain markdown." A line like
    ``title: Broken: [unclosed`` is rejected because a colon
    appears inside the unquoted value (YAML would have parsed
    ``Broken`` as a mapping key).
    """
    result: dict[str, Any] = {}
    list_key: str | None = None
    list_indent: int | None = None
    for raw_line in yaml_text.splitlines():
        # Comments and blank lines.
        if not raw_line.strip():
            list_indent = None
            continue
        if raw_line.lstrip().startswith("#"):
            continue
        # A multi-line list continuation. The first continuation sets
        # the indent; subsequent items must match.
        if list_key is not None:
            indent = len(raw_line) - len(raw_line.lstrip())
            if raw_line.lstrip().startswith("- "):
                if list_indent is None:
                    list_indent = indent
                if indent == list_indent:
                    value = _unquote(raw_line.lstrip()[2:].strip())
                    result[list_key].append(value)
                    continue
            # The list ended — fall through to key/value handling.
            list_key = None
            list_indent = None
        if ":" not in raw_line:
            raise ValueError(f"unexpected line in front-matter: {raw_line!r}")
        key, _, value = raw_line.partition(":")
        key = key.strip()
        if key == "":
            raise ValueError(f"empty key in front-matter: {raw_line!r}")
        if key not in _BRIEF_YAML_KEYS:
            # Unknown key — skip silently rather than reject the
            # whole block, so users can stash extra metadata.
            list_key = None
            list_indent = None
            continue
        # A key with a bare ``!!tag`` YAML directive is malformed.
        if value.lstrip().startswith("!!"):
            raise ValueError(f"YAML tag in front-matter value: {raw_line!r}")
        # Reject unquoted values that contain a ``:`` (they'd be parsed
        # as nested mappings by real YAML) or unbalanced ``[``.
        stripped_value = value.strip()
        if stripped_value and stripped_value[0] not in ("'", '"', "["):
            if ":" in stripped_value:
                raise ValueError(f"unquoted colon in front-matter value: {raw_line!r}")
            if "[" in stripped_value and "]" not in stripped_value:
                raise ValueError(f"unclosed bracket in front-matter value: {raw_line!r}")
        if stripped_value == "":
            # Possibly a multi-line list.
            list_key = key
            list_indent = None
            result.setdefault(key, [])
            continue
        # Strip surrounding quotes (if any) and un-escape the contents.
        # Bug #46: a previous version just sliced off the quote chars
        # which left backslash escapes (``\\``) doubled on read. The
        # writer uses ``repr()`` so the contents are already a valid
        # Python string literal; ``ast.literal_eval`` reverses it.
        stripped_value = _unquote(stripped_value)
        if key == "target_duration_s":
            try:
                result[key] = float(stripped_value)
            except ValueError as exc:
                raise ValueError(f"target_duration_s is not a number: {stripped_value!r}") from exc
        elif key == "key_segments":
            if stripped_value.startswith("[") and stripped_value.endswith("]"):
                try:
                    parsed = ast.literal_eval(stripped_value)
                except (ValueError, SyntaxError) as exc:
                    raise ValueError(
                        f"key_segments inline list is malformed: {stripped_value!r}"
                    ) from exc
                if not isinstance(parsed, list):
                    raise ValueError(f"key_segments must be a list, got {type(parsed).__name__}")
                result[key] = [str(v) for v in parsed]
            else:
                result[key] = [stripped_value]
        else:
            result[key] = stripped_value
        list_key = None
        list_indent = None
    return result


def _extract_h1(body: str) -> str:
    """Return the first H1 text in ``body`` (without the leading ``#``)."""
    match = _H1_RE.search(body)
    return match.group(1).strip() if match else ""


def _extract_notes(body: str) -> str:
    """Return the first non-empty paragraph of ``body``.

    Strips the H1 if present, then walks lines until a non-empty
    paragraph is found. Returns the joined paragraph (or "" if none).
    """
    lines: list[str] = []
    found = False
    for line in body.splitlines():
        stripped = line.strip()
        if not found:
            if not stripped:
                continue
            if stripped.startswith("#"):
                # Skip the H1 line.
                continue
            found = True
        lines.append(stripped)
        if stripped == "" and lines and lines[-1] == "":
            # End of first paragraph.
            break
    # Drop the trailing blank we appended when we hit the next blank.
    while lines and lines[-1] == "":
        lines.pop()
    return " ".join(lines).strip()


def _parse_project_md(text: str) -> ProjectBrief:
    """Parse a ``project.md`` text blob into a ``ProjectBrief``.

    Falls back to plain markdown on malformed front-matter so the
    parser never raises on user input.
    """
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        # No front-matter — use the H1 as the title and the body as notes.
        return ProjectBrief(
            title=_extract_h1(text),
            notes=_extract_notes(text),
        )
    yaml_text = match.group("yaml")
    body = match.group("body")
    try:
        fields = _parse_front_matter(yaml_text)
    except Exception:  # noqa: BLE001 - optional brief field
        # Defensive: _parse_front_matter is best-effort, but if anything
        # else raises, fall back to plain markdown.
        return ProjectBrief(
            title=_extract_h1(body),
            notes=_extract_notes(body),
        )
    title = fields.get("title") or _extract_h1(body)
    notes = fields.get("notes", _extract_notes(body))
    return ProjectBrief(
        title=title,
        audience=fields.get("audience", ""),
        tone=fields.get("tone", ""),
        target_duration_s=fields.get("target_duration_s"),
        key_segments=fields.get("key_segments", []),
        notes=notes,
        brand=fields.get("brand"),
    )


def load_project_brief(work_dir: Path | str) -> ProjectBrief | None:
    """Read ``<work_dir>/project.md`` and return a ``ProjectBrief``.

    Returns ``None`` when the file does not exist (the absence of a
    project brief is not an error condition — it just means the job
    has no project-level metadata).
    """
    path = Path(work_dir) / "project.md"
    if not path.exists():
        return None
    # Bug #43: Python's default utf-8 reader does NOT strip a UTF-8
    # BOM, so a file saved by Notepad / VS Code on Windows leaks the
    # BOM (﻿) into the first line. The front-matter regex would
    # not match, the parser would fall back to plain markdown, and
    # all structured fields would be silently lost. ``utf-8-sig``
    # strips the BOM if present and reads it as plain utf-8 otherwise.
    text = path.read_text(encoding="utf-8-sig")
    return _parse_project_md(text)


def write_project_brief(work_dir: Path | str, brief: ProjectBrief) -> Path:
    """Write ``brief`` back to ``<work_dir>/project.md``.

    The writer uses YAML front-matter so a roundtrip preserves all
    structured fields. The body has a short editorial note so users
    editing the file have a starting point.
    """
    work_dir = Path(work_dir)
    lines: list[str] = ["---"]
    if brief.title:
        # Quote the title in case it contains a colon.
        lines.append(f"title: {brief.title!r}")
    if brief.audience:
        lines.append(f"audience: {brief.audience!r}")
    if brief.tone:
        lines.append(f"tone: {brief.tone!r}")
    if brief.target_duration_s is not None:
        lines.append(f"target_duration_s: {brief.target_duration_s}")
    if brief.key_segments:
        lines.append("key_segments:")
        for seg in brief.key_segments:
            lines.append(f"  - {seg!r}")
    if brief.notes:
        # Bug #44 follow-up: write ``notes`` into the front-matter
        # so a roundtrip preserves user-edited values that aren't
        # in the body. The body still carries the same text for
        # backward-compat readers that ignore the front-matter.
        # Multi-line notes collapse to a single line with spaces so
        # ``repr()`` roundtrips through the parser (a YAML block
        # scalar would also work but complicates the parser).
        flat_notes = " ".join(brief.notes.splitlines()).strip()
        if flat_notes:
            lines.append(f"notes: {flat_notes!r}")
    if brief.brand is not None:
        lines.append(f"brand: {brief.brand!r}")
    lines.append("---")
    lines.append("")
    if brief.title:
        lines.append(f"# {brief.title}")
        lines.append("")
    if brief.notes:
        lines.append(brief.notes)
        lines.append("")
    text = "\n".join(lines)
    path = work_dir / "project.md"
    return atomic_write_text(path, text)
