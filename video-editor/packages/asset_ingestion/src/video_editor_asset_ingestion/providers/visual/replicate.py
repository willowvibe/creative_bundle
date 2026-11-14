"""Replicate image/video generation provider."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import httpx
import structlog

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers._media import MAX_IMAGE_BYTES, save_response_bytes
from video_editor_asset_ingestion.providers.base import GenerativeProvider

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.replicate.com"
DEFAULT_IMAGE_MODEL = "black-forest-labs/flux-schnell"


class ReplicateImageProvider(GenerativeProvider):
    """Generate images via the Replicate API."""

    provider_name = "replicate_image"

    #: Synthesizes images from a prompt; eligible for ``"auto"`` stills.
    generates_images = True

    def __init__(self, config: ProviderConfig | None = None):
        self.config = config or ProviderConfig()
        self.base_url = (self.config.base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = self.config.api_key()
        self.model = self.config.default_model or DEFAULT_IMAGE_MODEL
        self.timeout = self.config.timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {
                "Content-Type": "application/json",
                "Prefer": "wait",
            }
            if self.api_key:
                headers["Authorization"] = f"Token {self.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
            )
        return self._client

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Call Replicate and download the resulting image to ``output_path``."""
        if not self.api_key:
            raise RuntimeError("Replicate API token is not configured.")

        model = request.params.get("model") or self.model
        payload: dict[str, Any] = {
            "input": {
                "prompt": request.prompt,
                **request.params.get("model_kwargs", {}),
            },
        }

        client = await self._get_client()
        logger.info(
            "replicate_image_request",
            job_id=request.job_id,
            asset_id=request.asset_id,
            model=model,
        )
        response = await client.post(
            f"/v1/models/{model}/predictions",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

        # Replicate's ``Prefer: wait`` only blocks for ~10s. If the model is
        # still running, the response contains ``status: starting/processing``
        # and a ``urls.get`` polling endpoint. Poll until completion.
        if data.get("status") != "succeeded":
            data = await self._poll_until_complete(client, data)
        output_url = self._extract_output_url(data)
        if not output_url:
            raise RuntimeError(f"No output URL in Replicate response: {data}")

        image_response = await client.get(output_url)
        image_response.raise_for_status()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        save_response_bytes(image_response, output_path, max_bytes=MAX_IMAGE_BYTES)

        logger.info(
            "replicate_image_complete",
            job_id=request.job_id,
            asset_id=request.asset_id,
            path=str(output_path),
        )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.IMAGE,
            local_path=str(output_path),
            metadata={"provider": self.provider_name, "model": model},
        )

    async def _poll_until_complete(
        self,
        client: httpx.AsyncClient,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Poll a Replicate prediction until it reaches a terminal state."""
        poll_url = data.get("urls", {}).get("get")
        if not poll_url:
            raise RuntimeError(f"No polling URL in Replicate response: {data}")

        # Cap polling budget at 10 minutes to avoid runaway loops.
        timeout_seconds = min(float(self.timeout or 300.0), 600.0)
        deadline = time.monotonic() + timeout_seconds
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            await asyncio.sleep(1.0)
            poll_response = await client.get(poll_url)
            poll_response.raise_for_status()
            data = poll_response.json()
            status = data.get("status")
            if status == "succeeded":
                logger.info(
                    "replicate_poll_succeeded",
                    poll_url=poll_url,
                    attempts=attempt,
                )
                return data
            if status in ("failed", "canceled"):
                error = data.get("error") or f"Replicate prediction {status}"
                raise RuntimeError(error)
            logger.debug(
                "replicate_poll_pending",
                poll_url=poll_url,
                status=status,
                attempts=attempt,
            )
        raise RuntimeError(f"Replicate prediction timed out polling {poll_url}")

    @staticmethod
    def _extract_output_url(data: dict[str, Any]) -> str:
        """Pull the first output URL from a Replicate prediction response."""
        output = data.get("output")
        if isinstance(output, str):
            return output
        if isinstance(output, list) and output:
            return str(output[0])
        return ""

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> ReplicateImageProvider:
        await self._get_client()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
