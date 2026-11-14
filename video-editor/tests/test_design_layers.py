"""Tests for the optional ``layers`` block on a design."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from video_editor_core.designs import DesignError, DesignTokens, load_design

PALETTE = {"bg": "#000000", "ink": "#FFFFFF", "accent": "#F59E0B", "muted": "#888888"}


def design_file(tmp_path: Path, **extra) -> Path:
    data = {"palette": PALETTE, **extra}
    path = tmp_path / "series.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path.parent


def test_a_design_without_layers_still_loads(tmp_path: Path):
    tokens = load_design("series", design_file(tmp_path))
    assert tokens.layers.safe_area == ""
    assert tokens.layers.caption_preset == "karaoke"


def test_the_layers_block_is_parsed(tmp_path: Path):
    tokens = load_design(
        "series",
        design_file(
            tmp_path,
            layers={
                "safe_area": "vertical_short_strict",
                "pill": {"bg": "rgba(38,17,74,0.82)", "size": 42},
                "caption_preset": "karaoke",
            },
        ),
    )
    assert tokens.layers.safe_area == "vertical_short_strict"
    assert tokens.layers.pill["size"] == 42


def test_unknown_layer_keys_are_allowed(tmp_path: Path):
    tokens = load_design("series", design_file(tmp_path, layers={"glow": {"color": "#fff"}}))
    assert tokens.layers.model_extra["glow"] == {"color": "#fff"}


def test_a_design_without_a_palette_is_still_rejected(tmp_path: Path):
    (tmp_path / "broken.json").write_text(json.dumps({"layers": {}}), encoding="utf-8")
    with pytest.raises(DesignError):
        load_design("broken", tmp_path)


def test_design_tokens_validate_directly():
    tokens = DesignTokens(name="x", palette=PALETTE)  # type: ignore[arg-type]
    assert tokens.layers.caption_preset == "karaoke"
