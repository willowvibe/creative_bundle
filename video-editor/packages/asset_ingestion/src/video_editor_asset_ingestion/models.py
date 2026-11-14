"""Pydantic models for the generative asset ingestion pipeline."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator
from video_editor_core.models.asset import AssetResult, AssetType, BatchResult

__all__ = ["AssetRequest", "AssetResult", "AssetType", "BatchResult"]

_RATIO = re.compile(r"^\s*(\d+)\s*:\s*(\d+)\s*$")


def _positive_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _ratio(value: object) -> tuple[int, int] | None:
    """``"9:16"`` -> ``(9, 16)``; anything else (``"1024x1024"``, ``"0:9"``) -> None."""
    if not isinstance(value, str):
        return None
    match = _RATIO.match(value)
    if match is None:
        return None
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        return None
    divisor = math.gcd(width, height)
    return width // divisor, height // divisor


class AssetRequest(BaseModel):
    """A single generative asset request."""

    asset_id: str
    job_id: str
    scene_id: str
    asset_type: AssetType
    prompt: str
    provider: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    output_ext: str = ""

    @field_validator("output_ext")
    @classmethod
    def _normalize_ext(cls, value: str) -> str:
        """Strip leading dot so extensions are consistent."""
        return value.lstrip(".")

    def aspect(self) -> tuple[int, int] | None:
        """The framing this request asks for, reduced (``(9, 16)``), or ``None``.

        Callers spell it several ways (#291): ``width``/``height`` (shorts, MCP),
        a top-level ``aspect_ratio`` (storyteller, plans), or a provider preset in
        ``model_kwargs`` (``aspect_ratio`` for nano-banana, a ``size`` ratio for
        gpt-image). Pixel sizes win; a ``size`` like ``"1024x1024"`` is not a ratio.
        """
        params = self.params or {}
        width = _positive_int(params.get("width"))
        height = _positive_int(params.get("height"))
        if width is not None and height is not None:
            divisor = math.gcd(width, height)
            return width // divisor, height // divisor
        kwargs = params.get("model_kwargs")
        kwargs = kwargs if isinstance(kwargs, dict) else {}
        for candidate in (
            params.get("aspect_ratio"),
            kwargs.get("aspect_ratio"),
            kwargs.get("size"),
        ):
            ratio = _ratio(candidate)
            if ratio is not None:
                return ratio
        return None

    def cache_key(self) -> str:
        """Return a SHA-256 hex digest of the deterministic request payload.

        The key intentionally includes ``asset_id`` and ``scene_id`` so that
        per-scene assets (SFX, TTS, visuals) get distinct cache entries even
        when two scenes share the same prompt. Job-level assets such as music
        use stable asset/scene ids (``job_music`` / ``job``), so re-runs still
        hit the cache.
        """
        import hashlib
        import json

        payload = {
            "asset_id": self.asset_id,
            "scene_id": self.scene_id,
            "asset_type": self.asset_type.value,
            "prompt": self.prompt,
            "provider": self.provider,
            "params": self._sorted_params(self.params),
            "output_ext": self.output_ext,
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _sorted_params(value: Any) -> Any:
        """Return a recursively sorted, deterministic version of ``value``.

        Dict keys are sorted. Lists of sortable items are sorted so that
        key order inside chart configs and asset option lists does not
        change the cache key. Lists containing dicts are sorted by a stable
        JSON representation of each item so reordering still hashes the same.
        """
        if isinstance(value, dict):
            return {k: AssetRequest._sorted_params(v) for k, v in sorted(value.items())}
        if isinstance(value, list):
            sorted_items: list[Any] = []
            for item in value:
                normalized = AssetRequest._sorted_params(item)
                if isinstance(normalized, dict):
                    # Use a deterministic JSON key for sorting dicts inside lists.
                    key = json.dumps(normalized, sort_keys=True, ensure_ascii=True)
                    sorted_items.append((key, normalized))
                else:
                    sorted_items.append((normalized, normalized))
            sorted_items.sort(key=lambda pair: pair[0])
            return [pair[1] for pair in sorted_items]
        return value
