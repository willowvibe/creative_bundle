"""#291: one place that reads a request's aspect, whichever way the caller spelt it."""

from __future__ import annotations

from typing import Any

import pytest
from video_editor_asset_ingestion.models import AssetRequest, AssetType


def _req(**params: Any) -> AssetRequest:
    return AssetRequest(
        asset_id="a",
        job_id="j",
        scene_id="s",
        asset_type=AssetType.IMAGE,
        prompt="p",
        params=params,
    )


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"width": 1080, "height": 1920}, (9, 16)),
        ({"width": 1920, "height": 1080}, (16, 9)),
        ({"aspect_ratio": "9:16"}, (9, 16)),
        ({"aspect_ratio": " 4:5 "}, (4, 5)),
        ({"model_kwargs": {"aspect_ratio": "1:1"}}, (1, 1)),
        ({"model_kwargs": {"size": "2:3"}}, (2, 3)),
        # width/height win over a ratio string.
        ({"width": 1080, "height": 1920, "aspect_ratio": "16:9"}, (9, 16)),
        # A gpt-image size that is not a ratio says nothing about framing.
        ({"model_kwargs": {"size": "1024x1024"}}, None),
        ({"width": True, "height": 3}, None),
        ({"width": 0, "height": 1080}, None),
        ({"aspect_ratio": "wide"}, None),
        ({"aspect_ratio": "0:9"}, None),
        ({}, None),
    ],
)
def test_aspect(params: dict[str, Any], expected: tuple[int, int] | None) -> None:
    assert _req(**params).aspect() == expected
