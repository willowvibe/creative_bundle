"""Tests for the short pipeline's layerize stage (config + subprocess, no models)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml
from video_editor_core.content_pack import ContentPack
from video_editor_pipelines.short_pipeline import (
    ShortPipelineError,
    StillAssets,
    layerize_stills,
)
from video_editor_state.job import JobContext

FAKE_CLI = """
import sys
from pathlib import Path

args = sys.argv[1:]
out = Path(args[args.index("--out") + 1])
source = Path(args[args.index("--images") + 1])
(out / f"{source.stem}-depth.png").write_bytes(b"depth")
(out / f"{source.stem}-cutout.png").write_bytes(b"alpha")
(out / f"{source.stem}-plate.png").write_bytes(b"plate")
"""

PACK = {
    "job_id": "demo",
    "voice": {"voice_id": "sarvam:test"},
    "scenes": [
        {
            "id": "hook",
            "still": {"key": "hero", "prompt": "a subject on a plain background"},
            "layers": "auto",
            "lines": [{"say": "First."}],
        }
    ],
}


@pytest.fixture(autouse=True)
def _isolated_layerize_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the machine-wide layerize cache out of the checkout (#306)."""
    monkeypatch.setenv("VIDEO_EDITOR_DATA_DIR", str(tmp_path / "data"))


def context(tmp_path: Path) -> JobContext:
    ctx = JobContext(
        job_id="layerize-test",
        work_root=tmp_path / "work",
        output_root=tmp_path / "out",
    )
    ctx.ensure_dirs()
    return ctx


def config(tmp_path: Path, command: list[str]) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"layerize": {"command": command}}), encoding="utf-8")
    return path


def test_layerize_adds_the_three_derived_paths(tmp_path: Path) -> None:
    cli = tmp_path / "fake_layerize.py"
    cli.write_text(FAKE_CLI, encoding="utf-8")
    source = tmp_path / "hero.png"
    source.write_bytes(b"png")
    pack = ContentPack.model_validate(PACK)
    stills = StillAssets(paths={"hero": source}, results=())
    ctx = context(tmp_path)

    result = layerize_stills(
        ctx,
        pack,
        stills,
        config_path=config(tmp_path, [sys.executable, str(cli)]),
    )

    assert result.paths["hero"] == source
    assert result.paths["hero__depth"].read_bytes() == b"depth"
    assert result.paths["hero__fg"].read_bytes() == b"alpha"
    assert result.paths["hero__bg"].read_bytes() == b"plate"
    assert result.results == ()


def test_a_missing_command_becomes_a_pipeline_error(tmp_path: Path) -> None:
    source = tmp_path / "hero.png"
    source.write_bytes(b"png")
    pack = ContentPack.model_validate(PACK)
    stills = StillAssets(paths={"hero": source}, results=())
    with pytest.raises(ShortPipelineError, match="layerize.command"):
        layerize_stills(context(tmp_path), pack, stills, config_path=config(tmp_path, []))


def test_the_layerize_cache_lives_under_the_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIDEO_EDITOR_DATA_DIR", str(tmp_path / "data"))
    cli = tmp_path / "fake_layerize.py"
    cli.write_text(FAKE_CLI, encoding="utf-8")
    source = tmp_path / "hero.png"
    source.write_bytes(b"png")
    pack = ContentPack.model_validate(PACK)
    stills = StillAssets(paths={"hero": source}, results=())

    layerize_stills(
        context(tmp_path),
        pack,
        stills,
        config_path=config(tmp_path, [sys.executable, str(cli)]),
    )

    cache = tmp_path / "data" / "cache" / "layerize"
    entries = [path for path in cache.iterdir() if path.is_dir()]
    assert entries and all((entry / ".done").is_file() for entry in entries)
