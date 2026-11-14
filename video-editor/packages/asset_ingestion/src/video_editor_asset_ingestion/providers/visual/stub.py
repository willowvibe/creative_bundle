"""Stub visual provider for tests and offline demos."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import structlog
from PIL import Image

from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.base import GenerativeProvider

logger = structlog.get_logger(__name__)

DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080


class StubImageProvider(GenerativeProvider):
    """Generate a simple colored placeholder image without external APIs."""

    provider_name = "stub_image"

    def __init__(self, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT):
        self.width = width
        self.height = height

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Render a vectorized gradient placeholder image to ``output_path``."""
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Vectorized gradient: same visual output as the old pixel loop but
        # orders of magnitude faster for large resolutions (Bug #110).
        x = np.linspace(0, 255, self.width, dtype=np.uint8)
        y = np.linspace(0, 255, self.height, dtype=np.uint8)
        r = np.broadcast_to(x, (self.height, self.width))
        g = np.broadcast_to(y[:, None], (self.height, self.width))
        b = np.full((self.height, self.width), 128, dtype=np.uint8)
        img = Image.fromarray(np.stack([r, g, b], axis=2), mode="RGB")
        img.save(str(output_path))

        logger.info(
            "stub_image_complete",
            job_id=request.job_id,
            asset_id=request.asset_id,
            path=str(output_path),
        )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.IMAGE,
            local_path=str(output_path),
            metadata={"provider": self.provider_name, "width": self.width, "height": self.height},
        )
