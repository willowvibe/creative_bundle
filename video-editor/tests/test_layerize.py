"""Tests for the layerize subprocess stage (no torch, no models)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from video_editor_core.layerize import LayerizeError, cache_key, layerize_still


def write_cli(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake_layerize.py"
    script.write_text(body, encoding="utf-8")
    return script


GOOD_CLI = """
import sys
from pathlib import Path

args = sys.argv[1:]
out = Path(args[args.index("--out") + 1])
source = Path(args[args.index("--images") + 1])
log = Path(__file__).with_suffix(".log")
log.write_text(log.read_text() + "1\\n" if log.exists() else "1\\n")
(out / f"{source.stem}-depth.png").write_bytes(b"depth-bytes")
(out / f"{source.stem}-cutout.png").write_bytes(b"alpha-bytes")
(out / f"{source.stem}-plate.png").write_bytes(b"plate-bytes")
(out / f"{source.stem}-mask-near.png").write_bytes(b"near-bytes")
"""


def source_image(tmp_path: Path) -> Path:
    source = tmp_path / "hero.png"
    source.write_bytes(b"pretend-png")
    return source


def test_layerize_runs_the_command_once_and_caches(tmp_path: Path) -> None:
    cli = write_cli(tmp_path, GOOD_CLI)
    source = source_image(tmp_path)
    cache = tmp_path / "cache"
    command = [sys.executable, str(cli)]

    first = layerize_still(source, cache, command=command)
    assert first.cached is False
    assert first.depth.read_bytes() == b"depth-bytes"
    assert first.fg.read_bytes() == b"alpha-bytes"
    assert first.bg.read_bytes() == b"plate-bytes"
    assert first.near is not None and first.near.read_bytes() == b"near-bytes"
    assert (cli.with_suffix(".log")).read_text().splitlines() == ["1"]

    second = layerize_still(source, cache, command=command)
    assert second.cached is True
    assert second.depth == first.depth
    assert second.near == first.near
    assert (cli.with_suffix(".log")).read_text().splitlines() == ["1"]


def test_cache_key_changes_with_the_parameters(tmp_path: Path) -> None:
    source = source_image(tmp_path)
    base = cache_key(source, model="m", process_res=1280, cutout="birefnet")
    assert base != cache_key(source, model="m", process_res=504, cutout="birefnet")
    assert base != cache_key(source, model="m", process_res=1280, cutout="ben2")


def test_an_empty_command_names_the_config_section(tmp_path: Path) -> None:
    with pytest.raises(LayerizeError, match="layerize.command"):
        layerize_still(source_image(tmp_path), tmp_path / "cache", command=[])


def test_a_failure_carries_the_stderr(tmp_path: Path) -> None:
    cli = write_cli(
        tmp_path, 'import sys\nsys.stderr.write("model exploded\\n")\nraise SystemExit(3)\n'
    )
    with pytest.raises(LayerizeError, match="model exploded"):
        layerize_still(
            source_image(tmp_path), tmp_path / "cache", command=[sys.executable, str(cli)]
        )


def test_a_missing_output_is_named(tmp_path: Path) -> None:
    cli = write_cli(
        tmp_path,
        "import sys\nfrom pathlib import Path\n"
        "args = sys.argv[1:]\n"
        'out = Path(args[args.index("--out") + 1])\n'
        'source = Path(args[args.index("--images") + 1])\n'
        '(out / f"{source.stem}-depth.png").write_bytes(b"depth")\n',
    )
    with pytest.raises(LayerizeError, match="no fg, bg"):
        layerize_still(
            source_image(tmp_path), tmp_path / "cache", command=[sys.executable, str(cli)]
        )


def test_a_partial_cache_entry_is_rebuilt(tmp_path: Path) -> None:
    """A killed build leaves outputs but no marker (#306)."""
    cli = write_cli(tmp_path, GOOD_CLI)
    source = source_image(tmp_path)
    cache = tmp_path / "cache"
    command = [sys.executable, str(cli)]

    first = layerize_still(source, cache, command=command)
    (first.depth.parent / ".done").unlink()

    second = layerize_still(source, cache, command=command)
    assert second.cached is False
    assert second.depth.read_bytes() == b"depth-bytes"
    assert (cli.with_suffix(".log")).read_text().splitlines() == ["1", "1"]


def test_stale_temp_dirs_are_swept(tmp_path: Path) -> None:
    import os
    import time

    cli = write_cli(tmp_path, GOOD_CLI)
    source = source_image(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    leftover = cache / ".old-key.deadbeef"
    leftover.mkdir()
    (leftover / "depth.png").write_bytes(b"stale")
    old = time.time() - 7 * 60 * 60
    os.utime(leftover, (old, old))

    layerize_still(source, cache, command=[sys.executable, str(cli)])

    assert not leftover.exists()
