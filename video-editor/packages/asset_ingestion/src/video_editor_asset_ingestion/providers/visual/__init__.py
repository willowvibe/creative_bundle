"""Visual asset providers: image generation, video generation, data visualization."""

from __future__ import annotations

from video_editor_asset_ingestion.providers.visual.coverr import CoverrProvider
from video_editor_asset_ingestion.providers.visual.data_viz import MatplotlibDataVizProvider
from video_editor_asset_ingestion.providers.visual.fal_ai import FalAIProvider
from video_editor_asset_ingestion.providers.visual.mixkit import MixkitProvider
from video_editor_asset_ingestion.providers.visual.openrouter import OpenRouterProvider
from video_editor_asset_ingestion.providers.visual.pexels import PexelsProvider
from video_editor_asset_ingestion.providers.visual.pixabay import PixabayProvider
from video_editor_asset_ingestion.providers.visual.poyo_ai import PoyoAIProvider
from video_editor_asset_ingestion.providers.visual.replicate import ReplicateImageProvider
from video_editor_asset_ingestion.providers.visual.stub import StubImageProvider

__all__ = [
    "CoverrProvider",
    "FalAIProvider",
    "MatplotlibDataVizProvider",
    "MixkitProvider",
    "OpenRouterProvider",
    "PexelsProvider",
    "PixabayProvider",
    "PoyoAIProvider",
    "ReplicateImageProvider",
    "StubImageProvider",
]
