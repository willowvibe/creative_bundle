"""Resolvers that connect the SFX bank to the asset manager.

The bank owns *when* a sound is needed; this module owns *how* to ask a
provider. Each resolver tries the providers in order and returns the first
result, with its licence metadata carried through to the credits.

The shipped chain is free-only (``freesound_sfx`` then ``openverse_sfx``); a
paid provider such as ``elevenlabs_sfx`` can be added by the caller, in which
case the job's estimate/approval must include the spend.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

import structlog
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType

from sfx_placement.bank import LicenceRecord, ResolvedSfx, Resolver

logger = structlog.get_logger(__name__)

#: Search/generation prompts for the cue table's sound ids. A stock search and a
#: generated sound both read these; keep them concrete and sound-only.
SFX_PROMPTS: dict[str, str] = {
    "hit": "short punchy impact hit, dry, no music, sound effect",
    "click": "single clean UI click, very short, no music, sound effect",
    "card_flip": "quick card flip, paper whoosh, no music, sound effect",
    "latch": "short metal latch click, no music, sound effect",
    "whoosh": "soft transition whoosh, short, no music, sound effect",
}

#: Stock search wants a short, high-signal query, not a generation prompt.
SFX_SEARCH_QUERIES: dict[str, str] = {
    "hit": "punch impact",
    "click": "ui click",
    "card_flip": "card flip",
    "latch": "metal latch",
    "whoosh": "whoosh",
}

STOCK_PROVIDERS = frozenset({"freesound_sfx", "openverse_sfx"})


def manager_resolver(
    manager: AssetIngestionManager,
    providers: Sequence[str],
    *,
    job_id: str,
    duration_seconds: float = 2.0,
) -> Resolver:
    """Return an async resolver that tries ``providers`` in order."""
    order = tuple(provider for provider in providers if provider)
    pages: dict[str, int] = {}

    async def resolve(sound: str) -> ResolvedSfx | None:
        # Each repeat of the same cue moves one page down the search results,
        # so eight scene cuts do not all get the same whoosh.
        page = pages.get(sound, 0) + 1
        pages[sound] = page
        for provider in order:
            prompt = (
                SFX_SEARCH_QUERIES.get(sound, sound)
                if provider in STOCK_PROVIDERS
                else SFX_PROMPTS.get(sound, f"{sound} sound effect, no music")
            )
            request = AssetRequest(
                job_id=job_id,
                asset_id=f"sfx_{sound}_{uuid4().hex[:8]}",
                scene_id="",
                asset_type=AssetType.SFX,
                prompt=prompt,
                provider=provider,
                params={
                    "page": page,
                    "cue_sound": sound,
                    **({} if provider == "earshot_sfx" else {"duration_seconds": duration_seconds}),
                },
                output_ext="wav" if provider == "earshot_sfx" else "mp3",
            )
            try:
                batch = await manager.resolve_partial([request])
            except Exception as exc:  # noqa: BLE001 - try the next provider
                logger.warning(
                    "sfx_provider_failed",
                    provider=provider,
                    sound=sound,
                    error=str(exc),
                )
                continue
            if not batch.results:
                logger.info(
                    "sfx_provider_missed",
                    provider=provider,
                    sound=sound,
                    reason=batch.failures[0][1] if batch.failures else "no result",
                )
                continue
            return _resolved(batch.results[0], provider)
        return None

    return resolve


def _resolved(result: AssetResult, provider: str) -> ResolvedSfx:
    metadata = result.metadata or {}
    license_name = str(metadata.get("license") or "")
    if not license_name and provider == "elevenlabs_sfx":
        license_name = "generated"
    return ResolvedSfx(
        path=Path(result.local_path),
        provider=provider,
        licence=LicenceRecord(
            title=str(metadata.get("title") or ""),
            creator=str(metadata.get("creator") or ""),
            source_url=str(metadata.get("source_url") or ""),
            license=license_name,
            license_url=str(metadata.get("license_url") or ""),
        ),
    )
