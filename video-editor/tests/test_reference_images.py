"""Reference images reach PoYo: style frames, character sheets and image edits (#318, P4.8)."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers import _hosting
from video_editor_asset_ingestion.providers.visual.poyo_ai import PoyoAIProvider, reference_model
from video_editor_core.content_pack import ContentPack, load_content_pack, still_references

from tests.test_content_pack import PACK


def _pack(**changes: object) -> dict:
    pack = copy.deepcopy(PACK)
    pack["image"] = {"provider": "poyo_ai", "style_reference": "style/frame.png"}
    pack["characters"] = [
        {"name": "Lakshmi", "reference_images": ["refs/lakshmi_front.png", "refs/lakshmi_side.png"]}
    ]
    pack["scenes"].append(
        {
            "id": "gate_open",
            "still": {"key": "gate_open", "prompt": "The same gate, now open", "edit_of": "rat"},
            "lines": [{"say": "The gate opens."}],
        }
    )
    pack["scenes"][0]["still"]["characters"] = ["Lakshmi"]
    pack.update(changes)
    return pack


def test_a_still_sends_the_style_frame_then_its_characters_sheets():
    pack = ContentPack.model_validate(_pack())
    rat = pack.scenes[0].still
    assert rat is not None
    assert still_references(pack, rat) == [
        "style/frame.png",
        "refs/lakshmi_front.png",
        "refs/lakshmi_side.png",
    ]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda p: p["scenes"][0]["still"].update(characters=["Ravi"]), "does not define"),
        (lambda p: p["characters"][0].update(reference_images=[]), "no reference_images"),
        (lambda p: p["scenes"][2]["still"].update(edit_of="nope"), "edit_of must name"),
        (lambda p: p["scenes"][2]["still"].update(edit_of="gate_open"), "edit_of must name"),
        (lambda p: p["image"].update(provider="agy_image"), "poyo_ai only"),
    ],
)
def test_reference_mistakes_are_refused(mutate, message):
    pack = _pack()
    mutate(pack)
    with pytest.raises(ValidationError, match=message):
        ContentPack.model_validate(pack)


def test_an_edit_loop_is_refused():
    pack = _pack()
    pack["scenes"][0]["still"]["edit_of"] = "gate_open"
    with pytest.raises(ValidationError, match="loops back"):
        ContentPack.model_validate(pack)


def test_reference_paths_are_relative_to_the_pack_file(tmp_path: Path):
    path = tmp_path / "short.yaml"
    path.write_text(yaml.safe_dump(_pack()), encoding="utf-8")
    pack = load_content_pack(path)
    assert pack.image.style_reference == str(tmp_path / "style/frame.png")
    assert pack.characters[0].reference_images[0] == str(tmp_path / "refs/lakshmi_front.png")


def test_requests_carry_references_and_edits_wait_for_their_source(tmp_path: Path, monkeypatch):
    from video_editor_pipelines import short_pipeline

    pack = ContentPack.model_validate(_pack())
    requests = {
        r.asset_id: r
        for r in short_pipeline.short_job_requests(pack)
        if r.asset_type == AssetType.IMAGE
    }
    assert requests["still_rat"].params["reference_images"][0] == "style/frame.png"
    assert requests["still_gate_open"].params["edit_of"] == "still_rat"

    waves: list[list[AssetRequest]] = []

    def fake_resolve(ctx, wave, **kwargs):
        waves.append(wave)
        results = []
        for request in wave:
            out = tmp_path / f"{request.asset_id}.png"
            out.write_bytes(b"png")
            results.append(
                AssetResult(
                    asset_id=request.asset_id,
                    asset_type=AssetType.IMAGE,
                    scene_id=request.scene_id,
                    local_path=str(out),
                )
            )
        return results

    monkeypatch.setattr(short_pipeline, "_resolve", fake_resolve)
    stills = short_pipeline.generate_stills(None, pack)  # type: ignore[arg-type]
    assert [[r.asset_id for r in wave] for wave in waves] == [["still_rat"], ["still_gate_open"]]
    edit = waves[1][0]
    assert edit.params["reference_images"][0] == str(
        tmp_path / "still_rat.png"
    )  # the source comes first
    assert set(stills.paths) == {"rat", "gate_open"}


def _image_request(**params: object) -> AssetRequest:
    return AssetRequest(
        asset_id="s1",
        job_id="j",
        scene_id="scene_1",
        asset_type=AssetType.IMAGE,
        prompt="a gate",
        params=dict(params),
    )


def test_references_switch_to_the_edit_variant_with_its_size_field():
    payload = PoyoAIProvider.__new__(PoyoAIProvider)._build_payload(
        _image_request(), "nano-banana-2-lite", image_urls=["https://h/a.png", "https://h/b.png"]
    )
    assert payload["model"] == "nano-banana-2-lite-edit"
    assert payload["input"]["image_urls"] == ["https://h/a.png", "https://h/b.png"]
    assert "size" in payload["input"] and "aspect_ratio" not in payload["input"]


def test_no_references_keeps_the_base_model():
    payload = PoyoAIProvider.__new__(PoyoAIProvider)._build_payload(
        _image_request(), "nano-banana-2-lite"
    )
    assert payload["model"] == "nano-banana-2-lite" and "image_urls" not in payload["input"]


def test_a_model_without_reference_support_says_which_ones_have_it():
    assert reference_model("gpt-image-2") == "gpt-image-2-edit"
    with pytest.raises(ValueError, match="does not take reference images"):
        reference_model("flux-schnell")


@pytest.mark.asyncio
async def test_local_references_are_uploaded_once_and_urls_pass_through(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    image = tmp_path / "ref.png"
    image.write_bytes(b"\x89PNG fake")
    uploads: list[Path] = []

    async def fake_upload(path: Path, content_type: str) -> str:
        uploads.append(path)
        return "https://cdn/ref.png"

    monkeypatch.setattr(_hosting, "_upload", fake_upload)
    first = await _hosting.hosted_urls([str(image), "https://elsewhere/x.jpg"])
    second = await _hosting.hosted_urls([str(image)])
    assert first == ["https://cdn/ref.png", "https://elsewhere/x.jpg"]
    assert second == ["https://cdn/ref.png"]
    assert len(uploads) == 1  # cached by content hash
    with pytest.raises(_hosting.HostingError, match="does not exist"):
        await _hosting.hosted_urls([str(tmp_path / "missing.png")])


def test_flow_image_is_also_a_reference_provider():
    pack = _pack()
    pack["image"]["provider"] = "flow_image"
    assert ContentPack.model_validate(pack).image.provider == "flow_image"
