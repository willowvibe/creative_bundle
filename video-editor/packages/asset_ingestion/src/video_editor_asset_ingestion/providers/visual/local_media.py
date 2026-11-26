"""Local-media provider: copy user-supplied images/clips into the asset cache.

This provider is the ingestion half of the ``musical_video`` template. It does
not call any external API; it validates the source file exists, optionally
probes video duration, and copies the file into the requested output path.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import structlog
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, run_ffmpeg

from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.base import GenerativeProvider

logger = structlog.get_logger(__name__)


class LocalMediaProvider(GenerativeProvider):
    """Copy a local image or video file into the asset cache.

    The source path is read from ``request.prompt`` (which is how the planner
    stores the ``asset_query`` value for local media). The resulting
    ``AssetResult`` preserves the original extension and, for VIDEO assets,
    records the media duration so the renderer can loop/trim correctly.
    """

    provider_name = "local_media"

    #: Copying a user-supplied file generates nothing, so it must not consume
    #: the per-run image budget.
    consumes_image_quota = False

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Copy the source media file to ``output_path``."""
        source = Path(request.prompt)
        if not source.exists():
            raise FileNotFoundError(f"Local media source not found: {source}")
        if not source.is_file():
            raise IsADirectoryError(f"Local media source is a directory: {source}")
        # Bug #198 (provider-side defense in depth): the planner stores the
        # *resolved* real path in ``request.prompt`` (``media.resolve()``),
        # so a symlink here means the request bypassed the planner's
        # containment check. A symlink planted in ``media_dir`` pointing at
        # another tenant's file (or ``/etc/passwd``) would otherwise be
        # followed by ``shutil.copy2`` and pulled into this tenant's asset
        # cache and the composition's assets dir. Reject symlinks outright; the
        # planner's resolved paths are never symlinks, so legitimate
        # in-tree sources are unaffected.
        if source.is_symlink():
            raise ValueError(
                f"Local media source is a symlink and may escape the media "
                f"directory; refusing to follow: {source}"
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != output_path.resolve():
            shutil.copy2(source, output_path)
            logger.info(
                "local_media_copied",
                job_id=request.job_id,
                asset_id=request.asset_id,
                source=str(source),
                dest=str(output_path),
            )
        else:
            logger.info(
                "local_media_already_in_place",
                job_id=request.job_id,
                asset_id=request.asset_id,
                path=str(output_path),
            )

        asset_type = request.asset_type
        duration_ms = 0
        metadata: dict[str, Any] = {"source": str(source.resolve())}
        if request.params.get("ai") is True:
            # The script marked this file as AI-generated: the label pass reads it.
            metadata["ai"] = True

        if asset_type == AssetType.VIDEO:
            info = probe_media(output_path)
            duration_ms = info.duration_ms()
            metadata["duration_ms"] = duration_ms

        return AssetResult(
            asset_id=request.asset_id,
            asset_type=asset_type,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=duration_ms,
            metadata=metadata,
        )


class StubLocalMediaProvider(LocalMediaProvider):
    """Stub variant that writes a tiny valid PNG instead of copying.

    Used in tests where the real filesystem source is irrelevant but the
    downstream renderer still needs a loadable image asset.
    """

    provider_name = "stub_local_media"

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Write a minimal loadable asset to ``output_path``.

        Bug #214: the stub wrote the same 1×1 PNG regardless of
        ``asset_type``. For a ``VIDEO`` request the PNG landed at a ``.mp4``
        path with ``asset_type=VIDEO``, so any downstream ``probe_media`` or
        ``<video>`` load failed. Now IMAGE requests get the tiny PNG
        and VIDEO requests get a minimal valid MP4 generated via ffmpeg (the
        stub is only used in test environments where ffmpeg is present).
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if request.asset_type == AssetType.VIDEO:
            self._write_minimal_mp4(output_path)
            duration_ms = self._probe_duration_ms(output_path)
        else:
            self._write_minimal_png(output_path)
            duration_ms = 0
        logger.info(
            "stub_local_media_generated",
            job_id=request.job_id,
            asset_id=request.asset_id,
            path=str(output_path),
            asset_type=request.asset_type,
        )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=request.asset_type,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=duration_ms,
            metadata={"source": "stub"},
        )

    @staticmethod
    def _write_minimal_png(output_path: Path) -> None:
        # Minimal PNG: 1x1 grey pixel, no filters.
        output_path.write_bytes(
            bytes(
                [
                    0x89,
                    0x50,
                    0x4E,
                    0x47,
                    0x0D,
                    0x0A,
                    0x1A,
                    0x0A,
                    0x00,
                    0x00,
                    0x00,
                    0x0D,
                    0x49,
                    0x48,
                    0x44,
                    0x52,
                    0x00,
                    0x00,
                    0x00,
                    0x01,
                    0x00,
                    0x00,
                    0x00,
                    0x01,
                    0x08,
                    0x02,
                    0x00,
                    0x00,
                    0x00,
                    0x90,
                    0x77,
                    0x53,
                    0xDE,
                    0x00,
                    0x00,
                    0x00,
                    0x0C,
                    0x49,
                    0x44,
                    0x41,
                    0x54,
                    0x08,
                    0xD7,
                    0x63,
                    0xF8,
                    0x0F,
                    0x00,
                    0x00,
                    0x01,
                    0x01,
                    0x00,
                    0x05,
                    0xDD,
                    0xDD,
                    0xA4,
                    0x0E,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x49,
                    0x45,
                    0x4E,
                    0x44,
                    0xAE,
                    0x42,
                    0x60,
                    0x82,
                ]
            )
        )

    @staticmethod
    def _write_minimal_mp4(output_path: Path) -> None:
        """Write a tiny valid MP4 (1s of black) via ffmpeg.

        The stub is used in tests where the real source is irrelevant but the
        downstream renderer still needs a loadable asset of the right type.
        ffmpeg is a hard dev-dependency of this repo, so it is available in
        every environment that runs these tests.
        """
        if shutil.which("ffmpeg") is None:
            raise RuntimeError(
                "StubLocalMediaProvider needs ffmpeg to synthesize a VIDEO "
                f"asset but it is not on PATH (target: {output_path})"
            )
        run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=160x120:d=1:r=10",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=cl=mono",
                "-shortest",
                "-pix_fmt",
                "yuv420p",
                str(output_path),
            ],
            check=True,
            timeout=RENDER_TIMEOUT_SECONDS,
        )

    @staticmethod
    def _probe_duration_ms(output_path: Path) -> int:
        """Best-effort duration probe for the stub MP4; 0 on any failure."""
        try:
            info = probe_media(output_path)
            return info.duration_ms()
        except Exception:  # noqa: BLE001 — best-effort metadata for a stub
            return 0
