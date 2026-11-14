"""Tests for the bundled-font helpers used by HTML compositions."""

from __future__ import annotations

from pathlib import Path

from video_editor_core.fonts import BUNDLED_FONTS, FONT_DIR, font_face_css, font_stack


def test_bundled_font_files_and_licences_exist() -> None:
    for family, filename in BUNDLED_FONTS.items():
        assert (FONT_DIR / filename).is_file(), family
        assert (FONT_DIR / f"OFL-{family}.txt").is_file(), family


def test_font_stack_leads_with_family_and_keeps_fallbacks() -> None:
    stack = font_stack("Montserrat")
    assert stack.startswith("'Montserrat', ")
    assert stack.endswith("sans-serif")
    assert font_stack(None).startswith("'Inter', ")


def test_font_face_css_copies_only_bundled_families(tmp_path: Path) -> None:
    css = font_face_css(["Montserrat", "Coolvetica", "Montserrat"], tmp_path / "assets" / "fonts")
    assert css.count("@font-face") == 1
    assert "font-family: 'Montserrat'" in css
    assert "url('assets/fonts/Montserrat.ttf')" in css
    assert (tmp_path / "assets" / "fonts" / "Montserrat.ttf").is_file()
    assert not (tmp_path / "assets" / "fonts" / "Coolvetica.ttf").exists()


def test_cjk_families_get_cjk_fallbacks() -> None:
    stack = font_stack("Noto Serif KR")
    assert stack.startswith("'Noto Serif KR', ")
    assert "Noto Serif CJK KR" in stack
    assert stack.endswith("sans-serif")


def test_the_series_fonts_are_bundled() -> None:
    assert "Cormorant Garamond" in BUNDLED_FONTS
    assert "Noto Serif KR" in BUNDLED_FONTS
    assert "Noto Sans Devanagari" in BUNDLED_FONTS
    for family in ("Cormorant Garamond", "Noto Serif KR", "Noto Sans Devanagari"):
        assert (FONT_DIR / BUNDLED_FONTS[family]).stat().st_size > 5_000


def test_font_face_css_reports_the_file_format(tmp_path: Path) -> None:
    css = font_face_css(["Noto Serif KR"], tmp_path / "fonts")
    assert "format('truetype')" in css
    assert "font-family: 'Noto Serif KR'" in css
    assert (tmp_path / "fonts" / "NotoSerifKR.ttf").is_file()


def test_a_woff2_font_reports_its_own_format(tmp_path: Path) -> None:
    css = font_face_css(["Noto Sans Devanagari"], tmp_path / "fonts")
    assert "format('woff2')" in css
    assert "url('assets/fonts/NotoSansDevanagari.woff2')" in css
    assert (tmp_path / "fonts" / "NotoSansDevanagari.woff2").is_file()
