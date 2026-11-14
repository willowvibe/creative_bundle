"""flow_image: free Google Flow stills through flow-mcp, never spending Flow credits."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers.registry import PROVIDER_REGISTRY
from video_editor_asset_ingestion.providers.visual import flow_image
from video_editor_asset_ingestion.providers.visual.flow_image import (
    FlowImageError,
    FlowImageProvider,
    flow_aspect,
    flow_references,
)


def _request(**params: Any) -> AssetRequest:
    return AssetRequest(
        asset_id="s1",
        job_id="demo",
        scene_id="scene_1",
        asset_type=AssetType.IMAGE,
        prompt="a gate",
        params=params,
    )


class FakeClient:
    def __init__(self, tmp: Path, *, hint: str | None = None, status: str = "done") -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.tmp, self.hint, self.status = tmp, hint, status
        self.alive = True

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((tool, arguments))
        if tool == "flow_status":
            return {"flow": {"signedIn": True, "inProject": self.hint is None, "hint": self.hint}}
        if tool == "flow_generate":
            return {"output_dir": str(self.tmp), "jobs": [{"id": "job1", "status": "queued"}]}
        image = self.tmp / "scene-01.jpg"
        Image.new("RGB", (1376, 768), "red").save(image)
        return {
            "finished": True,
            "jobs": [{"id": "job1", "status": self.status, "files": [str(image)], "credits": 0}],
        }

    async def close(self) -> None:
        self.alive = False


def _provider(monkeypatch: pytest.MonkeyPatch, client: FakeClient) -> FlowImageProvider:
    provider = FlowImageProvider()

    async def session() -> FakeClient:
        return client

    monkeypatch.setattr(provider, "_session", session)
    return provider


def test_flow_image_is_registered():
    assert PROVIDER_REGISTRY["flow_image"] is FlowImageProvider


@pytest.mark.parametrize(
    ("params", "aspect"),
    [
        ({"width": 1080, "height": 1920}, "9:16"),
        ({"width": 1920, "height": 1080}, "16:9"),
        ({"aspect_ratio": "1:1"}, "1:1"),
        ({}, "16:9"),
    ],
)
def test_aspect_maps_to_flows_nearest(params, aspect):
    assert flow_aspect(_request(**params)) == aspect


def test_references_must_be_local_and_at_most_three(tmp_path: Path):
    ref = tmp_path / "r.png"
    ref.write_bytes(b"x")
    assert flow_references(_request(reference_images=[str(ref)])) == [str(ref.resolve())]
    with pytest.raises(FlowImageError, match="local reference images only"):
        flow_references(_request(reference_images=["https://x/y.png"]))
    with pytest.raises(FlowImageError, match="at most 3"):
        flow_references(_request(reference_images=[str(ref)] * 4))


@pytest.mark.asyncio
async def test_a_still_is_made_for_free_and_saved_as_png(tmp_path: Path, monkeypatch):
    client = FakeClient(tmp_path)
    provider = _provider(monkeypatch, client)
    out = tmp_path / "out.png"
    result = await provider.generate(_request(width=1920, height=1080), out)
    generate = dict(client.calls)["flow_generate"]
    scene = generate["scenes"][0]
    assert (
        scene["type"] == "image" and scene["max_credits"] == 0 and scene["aspect_ratio"] == "16:9"
    )
    assert generate["project"] == "ve-demo"
    assert Image.open(out).format == "PNG"
    assert result.metadata["cost"] == 0.0 and result.metadata["provider"] == "flow_image"


@pytest.mark.asyncio
async def test_no_open_project_fails_fast_so_the_chain_moves_on(tmp_path: Path, monkeypatch):
    client = FakeClient(tmp_path, hint="Signed in, but no project is open.")
    provider = _provider(monkeypatch, client)
    with pytest.raises(FlowImageError) as exc:
        await provider.generate(_request(), tmp_path / "out.png")
    assert exc.value.kind == "setup"
    assert [tool for tool, _ in client.calls] == ["flow_status"]  # nothing queued


@pytest.mark.asyncio
async def test_a_failed_flow_job_raises(tmp_path: Path, monkeypatch):
    provider = _provider(monkeypatch, FakeClient(tmp_path, status="failed"))
    with pytest.raises(FlowImageError, match="made no image"):
        await provider.generate(_request(), tmp_path / "out.png")


def test_the_image_chain_tries_flow_before_paid_providers():
    from video_editor_config import Settings

    chain = Settings.load(None).config.assets.image_chain
    assert chain.index("flow_image") < chain.index("poyo_ai")
    assert flow_image.DEFAULT_FLOW_MCP_DIR == "~/flow-mcp"
