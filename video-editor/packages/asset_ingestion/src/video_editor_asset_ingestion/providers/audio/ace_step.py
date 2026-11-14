"""ACE-Step 1.5 music generation provider.

ACE-Step is an MIT-licensed local music generation model that runs on
as little as 4 GB VRAM (or CPU). It also offers a free cloud API at
https://acemusic.ai.

This provider wraps the free cloud API. For local inference, install
the ``ace-step`` package and set ``use_local: true`` in provider options.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import httpx
import structlog

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.providers.audio.music_protocol import MusicProvider, MusicResult

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.acemusic.ai"
DEFAULT_POLL_INTERVAL = 5.0
DEFAULT_POLL_TIMEOUT = 300.0
DEFAULT_TIMEOUT = 300.0


class ACEStepMusicProvider(MusicProvider):
    """Generate music via the ACE-Step cloud API or local inference."""

    provider_name = "ace_step"

    def __init__(self, config: ProviderConfig | None = None):
        self._config = config or ProviderConfig()
        self._api_key: str | None = None
        self._base_url = DEFAULT_BASE_URL
        self._use_local = False
        self._poll_interval = DEFAULT_POLL_INTERVAL
        self._poll_timeout = DEFAULT_POLL_TIMEOUT
        self._timeout = DEFAULT_TIMEOUT

        if config:
            self._api_key = config.api_key()
            self._base_url = (config.base_url or DEFAULT_BASE_URL).rstrip("/")
            self._timeout = config.timeout or DEFAULT_TIMEOUT
            opts = config.provider_options or {}
            self._use_local = opts.get("use_local", False)
            self._poll_interval = float(opts.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL))
            self._poll_timeout = float(opts.get("poll_timeout_seconds", DEFAULT_POLL_TIMEOUT))

    async def generate_music(
        self,
        prompt: str,
        duration_seconds: float,
        output_path: Path,
    ) -> MusicResult:
        """Generate music from a text prompt via ACE-Step API or local inference."""
        if self._use_local:
            return await self._generate_local(prompt, output_path, duration_seconds)

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        payload: dict[str, Any] = {
            "prompt": prompt,
            "duration": int(duration_seconds),
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            # Submit generation job
            resp = await client.post(
                f"{self._base_url}/v1/music/generate",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            job_id: str | None = data.get("id") or data.get("job_id")

            if not job_id:
                raise RuntimeError(f"ACE-Step API returned no job ID: {data}")

            # Poll for completion
            deadline = time.monotonic() + self._poll_timeout
            while time.monotonic() < deadline:
                status_resp = await client.get(
                    f"{self._base_url}/v1/music/status/{job_id}",
                    headers=headers,
                )
                status_resp.raise_for_status()
                status_data = status_resp.json()

                state: str = status_data.get("status", "")
                if state in ("completed", "succeeded"):
                    audio_url: str | None = status_data.get("output_url") or status_data.get("url")
                    if not audio_url:
                        raise RuntimeError(f"ACE-Step job {job_id} completed but no audio URL")
                    # Download the audio file
                    audio_resp = await client.get(audio_url)
                    audio_resp.raise_for_status()
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(audio_resp.content)
                    break
                elif state in ("failed", "error"):
                    error_msg: str = status_data.get("error", "unknown error")
                    raise RuntimeError(f"ACE-Step job {job_id} failed: {error_msg}")

                await asyncio.sleep(self._poll_interval)
            else:
                raise RuntimeError(f"ACE-Step job {job_id} timed out after {self._poll_timeout}s")

        return MusicResult(
            provider=self.provider_name,
            model="ace-step-1.5",
            path=output_path,
            duration_seconds=float(duration_seconds),
        )

    async def _generate_local(
        self, prompt: str, output_path: Path, duration_seconds: float
    ) -> MusicResult:
        """Generate music locally using the ace-step Python package."""
        try:
            import ace_step  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "ace-step package not installed. Install with: pip install ace-step"
            ) from exc

        # Run local inference
        output: bytes = ace_step.generate(
            prompt=prompt,
            duration=int(duration_seconds),
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(output)

        return MusicResult(
            provider=self.provider_name,
            model="ace-step-1.5-local",
            path=output_path,
            duration_seconds=float(duration_seconds),
        )
