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


def test_every_ai_still_is_labelled_and_stock_is_not(tmp_path: Path) -> None:
    def still(asset_id: str, provider: str, kind: AssetType = AssetType.IMAGE) -> AssetResult:
        return AssetResult(
            asset_id=asset_id,
            asset_type=kind,
            scene_id="scene_3",
            local_path=str(_still(tmp_path, f"{asset_id}.png")),
            metadata={"provider": provider},
        )

    flow = still("scene_3_fallback_image", "flow_image")
    # The tiffin explainer's stills (single-scene and shot fallbacks) came from poyo_ai.
    poyo = still("scene_4_fallback_image", "poyo_ai")
    shot = still("footage_scene_2_1_fallback_image", "poyo_ai")
    stock = still("footage_scene_5_0", "pexels")
    video = still("footage_scene_6_video", "poyo_ai", AssetType.VIDEO)

    labelled = label_ai_stills(
        [flow, poyo, shot, stock, video], tmp_path / "labelled", text="AI చిత్రం"
    )

    assert {"flow_image", "agy_image", "poyo_ai"} <= LABELLED_PROVIDERS
    for result in labelled[:3]:
        assert Path(result.local_path).parent == tmp_path / "labelled"
        assert result.metadata["ai_label"] == "AI చిత్రం"
    assert labelled[3] is stock
    assert labelled[4] is video


def test_scripts_label_ai_stills_by_default_and_can_opt_out() -> None:
    from video_editor_pipelines.explainer_script import parse_explainer_script

    base = "---\ntitle: T\n{extra}---\n\n## S\nvisual: still\nimage: x\nnarration: y\n"
    assert parse_explainer_script(base.format(extra="")).ai_label == "AI image"
    assert parse_explainer_script(base.format(extra="ai_label: AI చిత్రం\n")).ai_label == "AI చిత్రం"
    assert parse_explainer_script(base.format(extra='ai_label: ""\n')).ai_label == ""


def test_a_result_marked_ai_is_labelled_whatever_its_provider(tmp_path: Path) -> None:
    def result(asset_id: str, name: str, metadata: dict, kind=AssetType.IMAGE) -> AssetResult:  # type: ignore[no-untyped-def]
        path = tmp_path / name
        Image.new("RGB", (1376, 768), (40, 90, 140)).save(path)
        return AssetResult(
            asset_id=asset_id,
            asset_type=kind,
            scene_id="scene_1",
            local_path=str(path),
            metadata=metadata,
        )

    flow_webp = result("footage_scene_1_0", "flow.webp", {"source": "/x", "ai": True})
    own_photo = result("footage_scene_1_1", "own.png", {"source": "/y"})
    ai_video = result("footage_scene_1_2", "v.png", {"ai": True}, AssetType.VIDEO)

    labelled = label_ai_stills([flow_webp, own_photo, ai_video], tmp_path / "labelled", text="AI")

    assert labelled[0].metadata["ai_label"] == "AI"
    assert labelled[0].local_path.endswith("footage_scene_1_0.webp")
    assert Image.open(labelled[0].local_path).size == (1376, 768)
    assert labelled[1] is own_photo  # unmarked own file: untouched
    assert labelled[2] is ai_video  # no pill is burned into a video
