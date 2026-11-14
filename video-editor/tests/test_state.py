"""Tests for the state package."""

from __future__ import annotations

import json
from pathlib import Path

from video_editor_state.job import JobContext
from video_editor_state.manifest import JobManifest
from video_editor_state.store import ArtifactStore


def test_job_manifest_roundtrip(tmp_path: Path) -> None:
    manifest = JobManifest(job_id="test-1", work_root=str(tmp_path / "work"))
    manifest.stage_done("ingest", {"sources": 2})
    path = tmp_path / "manifest.json"
    manifest.save(path)

    loaded = JobManifest.load(path)
    assert loaded.job_id == "test-1"
    assert loaded.is_stage_complete("ingest")
    assert loaded.stage_outputs("ingest")["sources"] == 2


def test_artifact_store(tmp_path: Path) -> None:
    ctx = JobContext(
        job_id="test-2",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )
    ctx.ensure_dirs()
    artifact = ctx.artifact("stage", "file.txt")
    artifact.write_text("hello")

    assert artifact.exists()
    assert ctx.store.exists("stage", "file.txt")
    assert not ctx.store.should_run("op", artifact)
    assert ctx.store.should_run("op", artifact, force=True)


def test_artifact_store_output_resolves_to_output_dir(tmp_path: Path) -> None:
    """Bug #112 regression: ``ArtifactStore.output()`` must resolve to the
    sibling ``output`` directory, not rely on a fragile string replacement.
    """
    work_dir = tmp_path / "data" / "work" / "job-1"
    work_dir.mkdir(parents=True)
    store = ArtifactStore(work_dir)
    path = store.output("export", "final.mp4")
    expected = tmp_path / "data" / "output" / "job-1" / "export" / "final.mp4"
    assert path == expected
    assert path.parent.exists()


def test_artifact_store_output_fallback_when_not_named_work(tmp_path: Path) -> None:
    """When the work directory does not end in ``work``, ``output()`` falls
    back to appending an ``output`` sibling directory.
    """
    work_dir = tmp_path / "myjob"
    work_dir.mkdir()
    store = ArtifactStore(work_dir)
    path = store.output("report.json")
    expected = tmp_path / "myjob" / "output" / "report.json"
    assert path == expected
    assert path.parent.exists()


def test_artifact_store_output_prefers_an_explicit_output_dir(tmp_path: Path) -> None:
    """#260: a caller that knows the output root must not be overridden by
    the ``work`` → ``output`` naming heuristic."""
    work_dir = tmp_path / "projects" / "job-1"
    work_dir.mkdir(parents=True)
    output_dir = tmp_path / "renders" / "job-1"
    store = ArtifactStore(work_dir, output_dir=output_dir)

    assert store.output("final.mp4") == output_dir / "final.mp4"


def test_job_context_wires_its_output_root_into_the_store(tmp_path: Path) -> None:
    """#260: ``JobContext`` knows both roots, so ``store.output()`` must use
    the real one rather than guessing from the work path."""
    ctx = JobContext(
        job_id="job-1",
        work_root=tmp_path / "projects",
        output_root=tmp_path / "renders",
    )

    assert ctx.store.output("report.md") == tmp_path / "renders" / "job-1" / "report.md"


def test_job_context_constructor_does_not_mkdir(tmp_path: Path) -> None:
    """Bug #89 regression: constructing a JobContext must not create the
    work/output directories. Callers must explicitly call ensure_dirs().
    """
    work_root = tmp_path / "work"
    output_root = tmp_path / "output"
    ctx = JobContext(
        job_id="lazy",
        work_root=work_root,
        output_root=output_root,
    )
    assert not work_root.exists()
    assert not output_root.exists()

    ctx.ensure_dirs()
    assert ctx.work_dir.exists()
    assert ctx.output_dir.exists()


def test_job_context_default_roots_do_not_raise_on_construction(
    monkeypatch, tmp_path: Path
) -> None:
    """Bug #105 / #260 regression: default roots must not raise PermissionError
    on construction (the constructor does not ``mkdir``), and on a machine
    without the container mounts they resolve to repo-local directories rather
    than ``/work`` / ``/output``.
    """
    monkeypatch.delenv("VE_WORK_ROOT", raising=False)
    monkeypatch.delenv("VE_OUTPUT_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)

    ctx = JobContext(job_id="default-roots")
    if not Path("/work").is_dir():
        assert ctx.work_root == Path("projects")
        assert ctx.output_root == Path("outputs")
    assert ctx.work_dir == ctx.work_root / "default-roots"
    assert ctx.output_dir == ctx.output_root / "default-roots"


def test_job_context_default_roots_honour_env(monkeypatch, tmp_path: Path) -> None:
    """``VE_WORK_ROOT`` / ``VE_OUTPUT_ROOT`` beat the built-in defaults (#260)."""
    work, out = tmp_path / "w", tmp_path / "o"
    monkeypatch.setenv("VE_WORK_ROOT", str(work))
    monkeypatch.setenv("VE_OUTPUT_ROOT", str(out))

    ctx = JobContext(job_id="env-roots")
    assert ctx.work_root == work
    assert ctx.output_root == out


def test_job_manifest_default_roots_match_context(monkeypatch, tmp_path: Path) -> None:
    """A manifest built outside a context must not record container paths."""
    monkeypatch.delenv("VE_WORK_ROOT", raising=False)
    monkeypatch.delenv("VE_OUTPUT_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)

    manifest = JobManifest(job_id="bare")
    ctx = JobContext(job_id="bare")
    assert manifest.work_root == str(ctx.work_root)
    assert manifest.output_root == str(ctx.output_root)


def test_artifact_store_constructor_does_not_mkdir(tmp_path: Path) -> None:
    """Bug #89 regression: ArtifactStore must not mkdir on construction."""
    work_dir = tmp_path / "not_yet"
    ArtifactStore(work_dir)
    assert not work_dir.exists()


def test_artifact_store_write_json_is_atomic(tmp_path: Path) -> None:
    """Bug #90 regression: ArtifactStore.write_json must write atomically so
    readers never see a torn JSON object.
    """
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    store = ArtifactStore(work_dir)
    path = store.write_json("stage", "data.json", data={"value": 42})
    assert path.exists()
    assert path.read_text(encoding="utf-8") == '{\n  "value": 42\n}'
    # No .tmp files left next to the artifact.
    assert not any(p.name.endswith(".tmp") for p in path.parent.iterdir())


class TestManifestAtomicWrite:
    """Regression tests for the atomic JobManifest.save() (Bug #9) and
    OperationLog tenant_id propagation (Bug #15)."""

    def test_save_does_not_leave_tmp_file_on_success(self, tmp_path: Path) -> None:
        """Bug #9 regression: a successful save must clean up its .tmp file
        so the work tree doesn't accumulate cruft over the life of a job.
        """
        manifest = JobManifest(job_id="atomic-1", work_root=str(tmp_path / "work"))
        path = tmp_path / "manifest.json"
        manifest.save(path)
        assert path.exists()
        # No .tmp leftover next to the final file.
        siblings = list(path.parent.iterdir())
        assert not any(p.name.endswith(".tmp") for p in siblings), siblings

    def test_save_replaces_existing_file_atomically(self, tmp_path: Path) -> None:
        """Bug #9 regression: a second save() should fully replace the
        first — readers should never see a half-written JSON.
        """
        path = tmp_path / "manifest.json"
        first = JobManifest(job_id="v1", work_root=str(tmp_path / "work"))
        first.save(path)
        first_size = path.stat().st_size

        second = JobManifest(job_id="v2", work_root=str(tmp_path / "work"), tenant_id="acme")
        second.stage_done("ingest", {"sources": 5})
        second.save(path)

        # The new file must be valid JSON, not a half-written mix.
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["job_id"] == "v2"
        assert data["tenant_id"] == "acme"
        assert "ingest" in data["stages"]
        # And the new file is reasonably different in size from the first.
        assert path.stat().st_size != first_size

    def test_log_operation_persists_tenant_id(self, tmp_path: Path) -> None:
        """Bug #15 regression: log_operation() must accept a tenant_id and
        store it on the OperationLog so multi-tenant logs can be filtered.
        """
        manifest = JobManifest(
            job_id="multi-tenant", work_root=str(tmp_path / "work"), tenant_id="acme"
        )
        manifest.log_operation("render", inputs={"a": 1}, outputs={"b": 2}, tenant_id="acme")
        assert len(manifest.operations) == 1
        assert manifest.operations[0].tenant_id == "acme"
        assert manifest.operations[0].operation == "render"

    def test_log_operation_defaults_to_empty_tenant_id(self, tmp_path: Path) -> None:
        """When called without a tenant_id, OperationLog.tenant_id should
        default to empty string (NOT error out)."""
        manifest = JobManifest(job_id="default-tenant", work_root=str(tmp_path / "work"))
        manifest.log_operation("plan", inputs={}, outputs={})
        assert manifest.operations[0].tenant_id == ""

    def test_artifact_store_record_injects_manifest_tenant_id(self, tmp_path: Path) -> None:
        """Bug #15 regression: ArtifactStore.record() should auto-inject the
        manifest's tenant_id so callers don't have to thread it through.
        """
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        # Pre-seed a manifest with a tenant_id by writing the file.
        manifest = JobManifest(job_id="store-1", work_root=str(work_dir), tenant_id="globex")
        manifest.save(work_dir / "manifest.json")

        store = ArtifactStore(work_dir)
        store.record("render", inputs={"k": 1}, outputs={"k": 2})

        # The recorded operation should pick up the manifest's tenant_id.
        assert len(store.manifest.operations) == 1
        assert store.manifest.operations[0].tenant_id == "globex"

    def test_save_uses_unique_tmp_file(self, tmp_path: Path) -> None:
        """Bug #61 regression: concurrent saves must use distinct temp
        files so they don't overwrite each other during the write phase."""
        path = tmp_path / "manifest.json"

        first = JobManifest(job_id="first", work_root=str(tmp_path / "work"))
        second = JobManifest(job_id="second", work_root=str(tmp_path / "work"), tenant_id="acme")

        import threading

        errors: list[Exception] = []

        def save1() -> None:
            try:
                first.save(path)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        def save2() -> None:
            try:
                second.save(path)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        t1 = threading.Thread(target=save1)
        t2 = threading.Thread(target=save2)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, errors
        loaded = JobManifest.load(path)
        # Either first or second may win the race, but the file must be valid.
        assert loaded.job_id in {"first", "second"}


def test_job_context_from_manifest_roundtrips_to_original_path(tmp_path: Path) -> None:
    """C20 regression: loading a context from an arbitrary manifest path and
    saving it must write back to that same path, not silently re-home to
    ``<work_root>/<job_id>/manifest.json``."""
    work_dir = tmp_path / "work" / "remote-job"
    work_dir.mkdir(parents=True)
    original = tmp_path / "exported" / "manifest.json"
    original.parent.mkdir(parents=True)
    manifest = JobManifest(
        job_id="remote-job",
        work_root=str(tmp_path / "work"),
        output_root=str(tmp_path / "output"),
    )
    manifest.save(original)

    ctx = JobContext.from_manifest(original)
    ctx.record("test_op", {"k": 1}, {"k": 2})

    assert original.exists()
    loaded = JobManifest.load(original)
    assert loaded.job_id == "remote-job"
    assert any(op.operation == "test_op" for op in loaded.operations)


def test_job_context_manifest_records_actual_roots(tmp_path: Path) -> None:
    """A new manifest must describe where the job actually lives.

    ``ArtifactStore`` builds ``JobManifest`` with the ``/work`` ``/output``
    defaults; ``JobContext`` never replaced them, so ``manifest.json`` lied
    about its own roots and ``JobContext.from_manifest`` re-homed the job to
    the filesystem root.
    """
    work_root = tmp_path / "data" / "work"
    output_root = tmp_path / "data" / "output"
    ctx = JobContext(job_id="roots", work_root=work_root, output_root=output_root)

    ctx.ensure_dirs()
    ctx.store.save()

    assert ctx.manifest().work_root == str(work_root)
    assert ctx.manifest().output_root == str(output_root)

    reloaded = JobContext.from_manifest(ctx.store.manifest_path)
    assert reloaded.work_dir == work_root / "roots"
    assert reloaded.output_dir == output_root / "roots"
