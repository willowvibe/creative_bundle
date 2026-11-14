"""AI stills carry our own "AI image" label where Gemini's sparkle sits."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from video_editor_asset_ingestion.models import AssetResult, AssetType
from video_editor_pipelines.ai_label import (
    LABELLED_PROVIDERS,
    label_ai_still,
    label_ai_stills,
    sparkle_box,
)


def _still(tmp_path: Path, name: str = "s.png", size: tuple[int, int] = (1376, 768)) -> Path:
    path = tmp_path / name
    Image.new("RGB", size, (40, 90, 140)).save(path)
    return path


def test_the_label_covers_the_sparkle_and_leaves_the_rest_alone(tmp_path: Path) -> None:
    source = _still(tmp_path)
    out = label_ai_still(source, tmp_path / "labelled.png", text="AI image")

    image = Image.open(out).convert("RGB")
    x0, y0, x1, y1 = sparkle_box(image.size)
    centre = ((x0 + x1) // 2, (y0 + y1) // 2)
    assert image.getpixel(centre) != (40, 90, 140)
    assert image.getpixel((10, 10)) == (40, 90, 140)
    assert image.size == (1376, 768)


def test_the_sparkle_box_matches_the_measured_flow_mark() -> None:
    # Measured on three Flow stills: centre ~97 px in from both edges, ~48 px wide.
    x0, y0, x1, y1 = sparkle_box((1376, 768))
    assert x0 <= 1376 - 97 - 24 and x1 >= 1376 - 97 + 24
    assert y0 <= 768 - 97 - 24 and y1 >= 768 - 97 + 24


def test_only_branded_providers_are_labelled(tmp_path: Path) -> None:
    flow = AssetResult(
        asset_id="scene_3_fallback_image",
        asset_type=AssetType.IMAGE,
        scene_id="scene_3",
        local_path=str(_still(tmp_path, "flow.png")),
        metadata={"provider": "flow_image"},
    )
    poyo = AssetResult(
        asset_id="scene_4_image",
        asset_type=AssetType.IMAGE,
        scene_id="scene_4",
        local_path=str(_still(tmp_path, "poyo.png")),
        metadata={"provider": "poyo_ai"},
    )

    labelled = label_ai_stills([flow, poyo], tmp_path / "labelled", text="AI image")

    assert "flow_image" in LABELLED_PROVIDERS and "agy_image" in LABELLED_PROVIDERS
    assert Path(labelled[0].local_path).parent == tmp_path / "labelled"
    assert labelled[0].metadata["ai_label"] == "AI image"
    assert labelled[1] is poyo


def test_scripts_label_ai_stills_by_default_and_can_opt_out() -> None:
    from video_editor_pipelines.explainer_script import parse_explainer_script

    base = "---\ntitle: T\n{extra}---\n\n## S\nvisual: still\nimage: x\nnarration: y\n"
    assert parse_explainer_script(base.format(extra="")).ai_label == "AI image"
    assert parse_explainer_script(base.format(extra="ai_label: AI చిత్రం\n")).ai_label == "AI చిత్రం"
    assert parse_explainer_script(base.format(extra='ai_label: ""\n')).ai_label == ""
