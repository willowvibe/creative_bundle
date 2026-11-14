"""Job context and helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self

from video_editor_state.errors import VEConfigError
from video_editor_state.ledger import NullLedger, ProjectLedger
from video_editor_state.manifest import JobManifest
from video_editor_state.paths import default_output_root, default_work_root
from video_editor_state.project_brief import ProjectBrief
from video_editor_state.store import ArtifactStore

MAX_JOB_ID_LENGTH = 200


class InvalidJobIdError(VEConfigError, ValueError):
    """A job id that would place the job's directories outside their roots."""


def validate_job_id(job_id: str) -> str:
    """Return ``job_id`` when it is safe to use as one path component (#276).

    The id becomes ``<work_root>/<job_id>`` and ``<output_root>/<job_id>``, and
    it reaches here from CLI flags, queue payloads and MCP tool arguments. An id
    is refused when it is empty, too long, ``.`` or ``..``, or contains a path
    separator or a control character — anything that would make it more than one
    directory name. Spaces and other printable characters stay legal: storyteller
    jobs are named after their project folder.
    """
    if not isinstance(job_id, str) or not job_id:
        raise InvalidJobIdError("job_id must be a non-empty string")
    if len(job_id) > MAX_JOB_ID_LENGTH:
        raise InvalidJobIdError(f"job_id is longer than {MAX_JOB_ID_LENGTH} characters")
    if job_id in {".", ".."}:
        raise InvalidJobIdError(f"job_id {job_id!r} is not a directory name")
    if "/" in job_id or "\\" in job_id or any(ord(ch) < 32 or ord(ch) == 127 for ch in job_id):
        raise InvalidJobIdError(
            f"job_id {job_id!r} must not contain path separators or control characters"
        )
    return job_id


class JobContext:
    """Runtime context for a single job."""

    def __init__(
        self,
        job_id: str,
        work_root: Path | str | None = None,
        output_root: Path | str | None = None,
        *,
        tenant_id: str = "default",
        project_brief: ProjectBrief | None = None,
        ledger: ProjectLedger | NullLedger | None = None,
    ):
        self.job_id = validate_job_id(job_id)
        self.tenant_id = tenant_id
        # ``None`` resolves through ``video_editor_state.paths`` so a library
        # caller never lands on the container-only ``/work`` / ``/output``
        # on a developer machine (#260).
        self.work_root = Path(work_root if work_root is not None else default_work_root())
        self.output_root = Path(output_root if output_root is not None else default_output_root())
        # One validated path component: the job dirs are direct children of
        # their roots. Resolve as well, so a pre-existing symlink at
        # ``<root>/<job_id>`` cannot redirect writes outside the root (#276).
        self.work_dir = self.work_root / job_id
        self.output_dir = self.output_root / job_id
        self._assert_inside(self.work_dir, self.work_root, "work_dir")
        self._assert_inside(self.output_dir, self.output_root, "output_dir")
        self.store = ArtifactStore(self.work_dir, output_dir=self.output_dir)
        # Record the roots this job actually uses so ``from_manifest`` (and
        # anything reading manifest.json) re-homes the job correctly.
        self.store.manifest.work_root = str(self.work_root)
        self.store.manifest.output_root = str(self.output_root)
        # Carry the per-project brief through the pipeline so skills
        # can read ``ctx.brief.notes`` / ``ctx.brief.title`` without
        # having to re-parse ``work_dir/project.md`` themselves.
        # ``None`` means "no project.md present" — the historic default.
        self.project_brief = project_brief
        self.ledger = ledger or NullLedger()

    @staticmethod
    def _assert_inside(path: Path, root: Path, label: str) -> None:
        """Refuse a job directory that resolves outside its configured root.

        ``validate_job_id`` already keeps the id to one component; this catches
        the remaining case of a symlinked job directory created before the job
        starts. ``strict=False`` means a not-yet-created directory still
        resolves, which is the common case in ``__init__``.
        """
        try:
            resolved = path.resolve()
            resolved_root = root.resolve()
        except (OSError, RuntimeError) as exc:
            raise InvalidJobIdError(f"{label} cannot be resolved under {root}: {exc}") from exc
        if not resolved.is_relative_to(resolved_root):
            raise InvalidJobIdError(f"{label} {resolved} escapes its root {resolved_root}")

    @classmethod
    def from_manifest(cls, manifest_path: Path | str) -> Self:
        """Load context from an existing manifest file.

        The artifact store is bound to the manifest file's directory so
        subsequent ``store.save()`` calls write back to the same file rather
        than re-homing it to ``<work_root>/<job_id>/manifest.json``.
        """
        manifest_path = Path(manifest_path)
        manifest = JobManifest.load(manifest_path)
        ctx = cls(
            job_id=manifest.job_id,
            work_root=manifest.work_root,
            output_root=manifest.output_root,
            tenant_id=manifest.tenant_id,
        )
        # C20 regression: align the store with the loaded manifest path so a
        # save roundtrips to the original file.
        ctx.store.manifest_path = manifest_path
        ctx.store.manifest = manifest
        return ctx

    @staticmethod
    def _resolve_ledger(config_path: Path | str | None = None) -> ProjectLedger | NullLedger:
        """Build a ledger backend from an optional project config.yaml.

        Returns a no-op ``NullLedger`` when the ledger is disabled or no
        config is provided, so the engine never fails because of missing DB
        setup.
        """
        from video_editor_config import ProjectConfig

        project_config = ProjectConfig.load(config_path)
        return project_config.ledger_instance()

    def ensure_dirs(self) -> None:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def manifest(self) -> JobManifest:
        return self.store.manifest

    def artifact(self, *parts: str) -> Path:
        return self.store.artifact(*parts)

    def record(self, operation: str, inputs: dict[str, Any], outputs: dict[str, Any]) -> None:
        self.store.record(operation, inputs, outputs)

    @property
    def brief(self) -> ProjectBrief | None:
        """Convenience accessor for the optional ``ProjectBrief``.

        Skills that want project-level metadata read
        ``ctx.brief.notes`` / ``ctx.brief.title`` etc. without
        having to import ``load_project_brief`` themselves. ``None``
        when no project.md was provided at construction time.
        """
        return self.project_brief

    def load_brief_from_workdir(self) -> ProjectBrief | None:
        """Auto-discover and cache a ``ProjectBrief`` from ``<work_dir>/project.md``.

        This is the lazy path: a job constructed without an explicit
        ``project_brief=`` can still pick one up if a ``project.md``
        was dropped into the work directory before the skill ran.
        Returns the (now-cached) brief, or ``None`` if no
        ``project.md`` exists. Safe to call multiple times; the
        cached brief wins on subsequent calls.
        """
        if self.project_brief is not None:
            return self.project_brief
        from video_editor_state.project_brief import load_project_brief

        brief = load_project_brief(self.work_dir)
        if brief is not None:
            brief.job_id = self.job_id
            self.project_brief = brief
        return self.project_brief
