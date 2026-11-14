"""ElevenLabs instant voice cloning (IVC) provider.

This provider wraps the ElevenLabs ``/v1/voices/add`` and ``/v1/voices``
endpoints so the engine can clone a speaker from a handful of audio
samples and then use the resulting ``voice_id`` in downstream TTS calls.

The provider implements the standard ``GenerativeProvider`` interface so it
plugs into ``AssetIngestionManager`` and respects caching/circuit breakers.
Pass an ``AssetRequest`` with:

- ``asset_type = AssetType.TTS`` (cloning produces a voice identity usable
  for TTS)
- ``prompt`` = desired voice name
- ``params["samples"]`` = list of audio file paths (``str`` or ``Path``)
- ``params["description"]`` (optional)
- ``params["labels"]`` (optional dict)

The returned ``AssetResult`` stores the cloned ``voice_id`` in
``metadata["voice_id"]`` and persists the registry in the project state
under ``voices/voice_registry.json``.

Direct API helpers are also exposed for callers that don't need the asset
abstraction:

- ``clone_voice(samples, name, ...) -> str``
- ``list_clones() -> list[dict]``
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import structlog

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.base import GenerativeProvider

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.elevenlabs.io"
DEFAULT_TIMEOUT = 120.0


class ElevenLabsVoiceCloneError(RuntimeError):
    """Raised when the ElevenLabs voice clone API returns an error."""


class ElevenLabsVoiceCloneProvider(GenerativeProvider):
    """Clone voices via ElevenLabs and persist the voice registry."""

    provider_name = "elevenlabs_clone"

    def __init__(self, config: ProviderConfig | None = None):
        self.config = config or ProviderConfig()
        self.base_url = (self.config.base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = self.config.api_key()
        self.timeout = self.config.timeout or DEFAULT_TIMEOUT
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {}
            if self.api_key:
                headers["xi-api-key"] = self.api_key
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
            )
        return self._client

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Clone a voice from the samples listed in ``request.params['samples']``.

        The ``output_path`` is ignored for cloning (no media file is produced),
        but a local voice registry JSON is written to the project state when a
        ``JobContext`` is available via ``request.params['work_dir']``.
        """
        if not self.api_key:
            raise RuntimeError("ElevenLabs API key is not configured.")

        sample_paths = self._resolve_samples(request.params.get("samples", []))
        if not sample_paths:
            raise ValueError(
                "ElevenLabsVoiceCloneProvider requires params['samples'] with at least one audio file."
            )

        name = request.prompt.strip() or request.params.get("name") or f"clone-{request.asset_id}"
        description = request.params.get("description", "")
        labels = request.params.get("labels", {})

        voice_id = await self.clone_voice(
            samples=sample_paths,
            name=name,
            description=description,
            labels=labels,
        )

        # Persist the cloned voice in the project registry when we know where
        # the project lives.
        work_dir = request.params.get("work_dir")
        if work_dir:
            self._persist_registry(Path(work_dir), voice_id, name, sample_paths)

        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            scene_id=request.scene_id,
            local_path="",
            duration_ms=0,
            metadata={
                "provider": self.provider_name,
                "voice_id": voice_id,
                "voice_name": name,
                "samples": [str(p) for p in sample_paths],
            },
        )

    async def clone_voice(
        self,
        samples: list[Path],
        name: str,
        description: str = "",
        labels: dict[str, str] | None = None,
    ) -> str:
        """Upload samples to ElevenLabs and return the new ``voice_id``.

        Args:
            samples: Audio files to use for the clone. ElevenLabs recommends
                short, clean samples with minimal background noise.
            name: Display name for the cloned voice.
            description: Optional human-readable description.
            labels: Optional key/value labels for the voice.

        Returns:
            The ElevenLabs ``voice_id`` string.
        """
        if not self.api_key:
            raise RuntimeError("ElevenLabs API key is not configured.")
        if not samples:
            raise ValueError("At least one audio sample is required for voice cloning.")

        files: list[tuple[str, tuple[str, bytes, str]]] = []
        for sample in samples:
            path = Path(sample)
            if not path.exists():
                raise FileNotFoundError(f"Voice sample not found: {path}")
            files.append(("files", (path.name, path.read_bytes(), "application/octet-stream")))

        form_data: dict[str, str] = {"name": name}
        if description:
            form_data["description"] = description
        if labels:
            form_data["labels"] = json.dumps(labels)

        client = await self._get_client()
        logger.info(
            "elevenlabs_clone_request",
            name=name,
            sample_count=len(samples),
        )

        response = await client.post("/v1/voices/add", data=form_data, files=files)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = response.text[:1000]
            raise ElevenLabsVoiceCloneError(
                f"ElevenLabs voice clone failed ({response.status_code}): {body}"
            ) from exc

        data = response.json()
        voice_id = data.get("voice_id")
        if not voice_id:
            raise ElevenLabsVoiceCloneError(f"ElevenLabs response missing voice_id: {data!r}")

        logger.info(
            "elevenlabs_clone_complete",
            name=name,
            voice_id=voice_id,
        )
        return str(voice_id)

    async def list_clones(self) -> list[dict[str, Any]]:
        """Return cloned voices from the ElevenLabs account.

        Built-in ElevenLabs voices are filtered out so only user-added voices
        are returned. Each dict contains at least ``voice_id`` and ``name``.
        """
        if not self.api_key:
            raise RuntimeError("ElevenLabs API key is not configured.")

        client = await self._get_client()
        response = await client.get("/v1/voices")
        response.raise_for_status()
        data = response.json()

        voices = data.get("voices", [])
        clones: list[dict[str, Any]] = []
        for voice in voices:
            # Built-in voices have category "premade"; user voices are "cloned"
            # or "generated". We keep anything that is not premade.
            if voice.get("category") == "premade":
                continue
            clones.append(
                {
                    "voice_id": voice.get("voice_id"),
                    "name": voice.get("name"),
                    "category": voice.get("category"),
                    "description": voice.get("description"),
                    "labels": voice.get("labels", {}),
                }
            )
        return clones

    def _resolve_samples(self, samples: Any) -> list[Path]:
        """Normalize a list of sample paths to ``Path`` objects."""
        if samples is None:
            return []
        if isinstance(samples, (str, Path)):
            samples = [samples]
        return [Path(s) for s in samples if s]

    def _persist_registry(
        self,
        work_dir: Path,
        voice_id: str,
        name: str,
        samples: list[Path],
    ) -> Path:
        """Append the new clone to ``voices/voice_registry.json`` in the project."""
        voices_dir = Path(work_dir) / "voices"
        voices_dir.mkdir(parents=True, exist_ok=True)
        registry_path = voices_dir / "voice_registry.json"

        registry: dict[str, Any] = {"voices": []}
        if registry_path.exists():
            try:
                registry = json.loads(registry_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                registry = {"voices": []}

        voices = registry.setdefault("voices", [])
        # Replace any existing entry with the same name to keep the registry current.
        voices = [v for v in voices if v.get("name") != name]
        voices.append(
            {
                "voice_id": voice_id,
                "name": name,
                "samples": [str(p.resolve().as_posix()) for p in samples],
            }
        )
        registry["voices"] = voices

        registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
        logger.info("voice_registry_persisted", path=str(registry_path), voice_id=voice_id)
        return registry_path

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> ElevenLabsVoiceCloneProvider:
        await self._get_client()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
