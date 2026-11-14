"""Base provider interface for generative asset ingestion."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from video_editor_asset_ingestion.models import AssetRequest, AssetResult


class GenerativeProvider(ABC):
    """Abstract connector for a single generative media provider."""

    provider_name: str = ""

    #: Whether serving a request through this provider consumes one unit of
    #: ``max_images_per_run``. Providers that ingest existing media (copying a
    #: local file) do not generate and so do not count.
    consumes_image_quota: bool = True

    #: Whether this provider synthesizes images from a prompt. ``"auto"`` image
    #: selection restricts its candidates to these when no image chain is
    #: configured, so a stock library cannot silently replace a generated still.
    #: Providers that fetch or copy existing media (stock, local files) and
    #: providers that produce another asset type (TTS, music, data viz) leave
    #: this ``False``; the restriction is dropped when nothing qualifies.
    generates_images: bool = False

    @abstractmethod
    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Generate or fetch the asset and write it to ``output_path``.

        Args:
            request: Normalized asset request.
            output_path: Destination file path (parent directory already exists).

        Returns:
            ``AssetResult`` describing the locally cached asset.
        """
        ...
