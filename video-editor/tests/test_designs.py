from __future__ import annotations

import json

import pytest
from video_editor_core.designs import DesignError, Layers, designs_dir, load_design


def test_default_design_loads_with_bundled_fonts():
    design = load_design()
    assert design.name == "cream_editorial"
    assert design.palette.bg.startswith("#")
    assert design.type.display in {"Inter", "Montserrat"}
    assert 1.0 < design.motion.drift <= 1.30


def test_named_design_loads():
    assert load_design("ink_noir").theme == "dark"


def test_unknown_design_is_rejected():
    with pytest.raises(DesignError, match="unknown design"):
        load_design("no_such_design")


@pytest.mark.parametrize("name", ["../cream_editorial", "cream/editorial", "Cream Editor"])
def test_names_with_separators_or_spaces_are_rejected(name: str):
    with pytest.raises(DesignError, match="invalid design name"):
        load_design(name)


def test_unbundled_font_is_a_validation_error(tmp_path):
    path = tmp_path / "serif.json"
    path.write_text(
        json.dumps(
            {
                "name": "serif",
                "palette": {
                    "bg": "#EFEAE3",
                    "ink": "#141210",
                    "accent": "#C8102E",
                    "muted": "#8A8378",
                },
                "type": {"display": "Playfair Display", "body": "Inter"},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(DesignError, match="not bundled"):
        load_design("serif", directory=tmp_path)


def test_missing_palette_key_is_a_validation_error(tmp_path):
    path = tmp_path / "thin.json"
    path.write_text(
        json.dumps({"name": "thin", "palette": {"bg": "#fff", "ink": "#000"}}),
        encoding="utf-8",
    )
    with pytest.raises(DesignError, match="accent"):
        load_design("thin", directory=tmp_path)


def test_designs_dir_sits_at_the_repo_root():
    assert (designs_dir() / "cream_editorial.json").is_file()


def test_layers_carry_the_caption_line_block() -> None:
    layers = Layers(caption_line={"font_size": 64, "box": {"y": 1240}})
    assert layers.caption_line["font_size"] == 64
    assert layers.caption_line["box"] == {"y": 1240}


def test_layers_default_to_no_caption_line() -> None:
    assert Layers().caption_line == {}


def test_the_saju_design_is_a_valid_short_design() -> None:
    tokens = load_design("saju_series")
    assert tokens.layers.safe_area == "vertical_short"
    assert tokens.layers.caption_preset == "karaoke"
    assert tokens.type.display == "Cormorant Garamond"
    assert tokens.type.body == "Inter"
    box = tokens.layers.caption_line["box"]
    # Shared caption standard with day-master-deck: 1356 + 180 = 1536,
    # exactly the vertical_short safe bottom edge at 1920 x 0.80.
    assert box == {"x": 90, "y": 1356, "width": 900, "height": 180}
    assert box["y"] + box["height"] <= 1536
    assert tokens.layers.end_card["url_font_size"] == 36
    assert "Noto Sans KR" not in {tokens.type.display, tokens.type.body}


def _write_design(tmp_path, extra: dict) -> None:
    path = tmp_path / "graded.json"
    payload = {
        "name": "graded",
        "palette": {"bg": "#EFEAE3", "ink": "#141210", "accent": "#C8102E", "muted": "#8A8378"},
        **extra,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_a_design_can_carry_a_grade(tmp_path):
    _write_design(
        tmp_path,
        {"grade": {"preset": "vintage-wash", "details": {"vignette": 0.12, "grain": 0.1}}},
    )
    tokens = load_design("graded", directory=tmp_path)
    assert tokens.grade is not None
    assert tokens.grade.preset == "vintage-wash"
    assert tokens.grade.details == {"vignette": 0.12, "grain": 0.1}
    assert tokens.grade.intensity == 1.0


def test_an_unknown_grade_preset_is_rejected(tmp_path):
    _write_design(tmp_path, {"grade": {"preset": "not-a-preset"}})
    with pytest.raises(DesignError, match="unknown grade preset"):
        load_design("graded", directory=tmp_path)


def test_an_unknown_grade_control_is_rejected(tmp_path):
    _write_design(tmp_path, {"grade": {"adjust": {"bloom": 0.2}}})
    with pytest.raises(DesignError, match="unknown key"):
        load_design("graded", directory=tmp_path)
