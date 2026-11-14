"""Tests for the CJK glyph gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from video_editor_core.glyphs import (
    GlyphManifestError,
    cjk_characters,
    is_cjk,
    load_glyph_manifest,
    undeclared_glyphs,
)


def test_the_bundled_manifest_loads_and_declares_hanja():
    manifest = load_glyph_manifest()
    assert "四" in manifest and "사" in manifest
    assert manifest["四"]["reading"] == "sa"


def test_is_cjk_covers_han_and_hangul():
    assert is_cjk("四") and is_cjk("사")
    assert not is_cjk("a") and not is_cjk("4")


def test_cjk_characters_dedupes_and_ignores_latin():
    assert cjk_characters("四柱 four 사주 四") == {"四", "柱", "사", "주"}


def test_a_declared_character_passes():
    manifest = load_glyph_manifest()
    assert undeclared_glyphs(["四柱"], manifest) == []


def test_an_undeclared_hanja_fails():
    manifest = load_glyph_manifest()
    assert undeclared_glyphs(["龍"], manifest) == [("龍", "龍")]


def test_a_missing_manifest_is_an_error(tmp_path: Path):
    with pytest.raises(GlyphManifestError):
        load_glyph_manifest("nope", directory=tmp_path)


def test_a_manifest_without_characters_is_an_error(tmp_path: Path):
    (tmp_path / "bad.json").write_text(json.dumps({"characters": []}), encoding="utf-8")
    with pytest.raises(GlyphManifestError):
        load_glyph_manifest("bad", directory=tmp_path)


def test_invalid_utf8_is_a_manifest_error(tmp_path: Path):
    (tmp_path / "bad.json").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(GlyphManifestError):
        load_glyph_manifest("bad", directory=tmp_path)


def test_a_non_object_character_value_is_a_manifest_error(tmp_path: Path):
    (tmp_path / "bad.json").write_text(json.dumps({"characters": {"四": 5}}), encoding="utf-8")
    with pytest.raises(GlyphManifestError):
        load_glyph_manifest("bad", directory=tmp_path)
