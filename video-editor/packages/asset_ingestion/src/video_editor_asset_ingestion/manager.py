"""Asset ingestion orchestrator with cache-aware concurrent resolution."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
import weakref
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, cast

import httpx
import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential
from video_editor_state.asset_audit import write_cost_tracking_degraded
from video_editor_state.errors import VEProviderError
from video_editor_state.job import JobContext
from video_editor_state.ledger_hooks import record_cost_line

from video_editor_asset_ingestion.budget import (
    BudgetEstimate,
    BudgetExceededError,
    BudgetGovernor,
    estimate_cost,
)
from video_editor_asset_ingestion.config import AssetProviderConfig, ProviderConfigError
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType, BatchResult
from video_editor_asset_ingestion.pricing import PricingRegistry
from video_editor_asset_ingestion.providers.base import GenerativeProvider
from video_editor_asset_ingestion.providers.elevenlabs_policy import (
    VOICE_PROVIDERS,
    ensure_voice_only,
)
from video_editor_asset_ingestion.providers.registry import PROVIDER_REGISTRY, get_provider
from video_editor_asset_ingestion.providers.scoring import ProviderSelectionError
from video_editor_asset_ingestion.providers.visual.local_media import LocalMediaProvider
from video_editor_asset_ingestion.providers.visual.stub import StubImageProvider

logger = structlog.get_logger(__name__)

DEFAULT_CACHE_DIR = ".cache/assets"
META_SUFFIX = ".meta.json"

# Bug #85: per-provider concurrency cap. Cloud providers are the expensive
# ones; local/stub providers can run unbounded. The fallback applies to any
# provider not listed here.
_CLOUD_PROVIDER_LIMITS: dict[str, int] = {
    "openai_tts": 5,
    "elevenlabs_tts": 5,
    "elevenlabs_music": 3,
    "elevenlabs_sfx": 5,
    "elevenlabs_isolation": 3,
    "elevenlabs_speech_to_speech": 3,
    "elevenlabs_forced_alignment": 3,
    "agy_image": 2,
    # One Chrome window drives Flow.
    "flow_image": 1,
    "replicate_image": 3,
    "fal_ai": 3,
    "poyo_ai": 3,
    "openrouter": 3,
    "poyo_music": 3,
    "pexels": 5,
    "pixabay": 5,
    "coverr": 5,
    "mixkit": 5,
    "elevenlabs_clone": 2,
    "hyperframes_video": 2,
    "sarvam_tts": 5,
    "ace_step": 3,
    # Local providers (kitten_tts, moss_tts, kokoro_tts) are intentionally
    # absent — they run on the local machine and don't need rate limiting.
}
_DEFAULT_CLOUD_LIMIT = 3


#: A VIDEO request naming this provider walks ``assets.stock_video_chain``.
STOCK_PROVIDER = "stock"


class StubFallbackRefusedError(VEProviderError):
    """``assets.strict`` refused to replace a missing or failed provider with a stub."""


class CircuitBreakerOpenError(VEProviderError):
    """Raised when a provider's circuit breaker is open."""


class ImageQuotaError(VEProviderError):
    """Raised when a run asks for more images than ``max_images_per_run``."""


class ProviderCircuitBreaker:
    """Simple per-provider circuit breaker.

    Bug #82: after ``failure_threshold`` consecutive final failures within
    ``recovery_seconds``, the breaker opens and fast-fails subsequent requests
    until the recovery window passes. A single success closes it again.
    """

    def __init__(
        self, provider_name: str, failure_threshold: int = 3, recovery_seconds: float = 60.0
    ):
        self.provider_name = provider_name
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.failure_count = 0
        self.last_failure_at: datetime | None = None

    def check(self) -> None:
        """Raise CircuitBreakerOpenError if the breaker is currently open."""
        if self.failure_count < self.failure_threshold:
            return
        if self.last_failure_at is None:
            return
        elapsed = (datetime.now(UTC) - self.last_failure_at).total_seconds()
        if elapsed >= self.recovery_seconds:
            # Recovery window expired — give the provider another chance.
            self.failure_count = 0
            self.last_failure_at = None
            return
        logger.warning(
            "provider_circuit_open",
            provider=self.provider_name,
            failure_count=self.failure_count,
            elapsed=elapsed,
        )
        raise CircuitBreakerOpenError(
            f"Circuit breaker open for {self.provider_name!r} "
            f"({self.failure_count} failures in {elapsed:.1f}s)"
        )

    def record_success(self) -> None:
        """Close the breaker on a successful generation."""
        if self.failure_count > 0:
            logger.info("provider_circuit_closed", provider=self.provider_name)
        self.failure_count = 0
        self.last_failure_at = None

    def record_failure(self, *, immediate: bool = False) -> None:
        """Count a final failure; quota/auth failures open the breaker at once."""
        self.failure_count = (
            max(self.failure_count + 1, self.failure_threshold)
            if immediate
            else self.failure_count + 1
        )
        self.last_failure_at = datetime.now(UTC)
        logger.warning(
            "provider_circuit_failure",
            provider=self.provider_name,
            failure_count=self.failure_count,
            immediate=immediate,
        )


_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


def _is_retryable(exc: BaseException) -> bool:
    """Return True when ``exc`` is a transient failure worth retrying.

    We deliberately do NOT include ``subprocess.CalledProcessError`` here.
    A failing ffmpeg probe or local command is a caller-side problem (bad
    input, missing codec, etc.) and retrying it just wastes time and masks
    the real error. Only transport-level failures and retryable HTTP status
    codes are retried (Bug #121).
    """
    if isinstance(exc, (ConnectionError, TimeoutError, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS_CODES
    return False


class AssetIngestionManager:
    """Resolve a batch of asset requests in parallel, respecting the local cache."""

    # Bug #85: class-level per-provider semaphores enforce a global
    # concurrency bound regardless of how many managers are alive.
    _semaphores: ClassVar[dict[str, asyncio.Semaphore]] = {}
    # Bug #82: class-level circuit breakers so a degraded provider is
    # fast-failed across managers and across batches.
    _circuit_breakers: ClassVar[dict[str, ProviderCircuitBreaker]] = {}

    def __init__(
        self,
        ctx: JobContext,
        config: AssetProviderConfig | None = None,
        cache_dir: Path | str | None = None,
        budget: BudgetGovernor | None = None,
        final: bool = False,
    ):
        self.ctx = ctx
        if config is None:
            from video_editor_config import Settings

            config = Settings.load().config.assets
        self.config = config
        self.budget = budget
        #: Final renders use ``assets.image_chain_final`` when configured.
        self.final = final
        # Priced lazily: only a build without a budget needs it (see
        # ``_request_cost``), and loading pricing.yaml is not free.
        self._pricing_registry: PricingRegistry | None = None
        # Resolve once: providers (and child processes they spawn) receive
        # these paths, and a relative path would be resolved a second time
        # against a different cwd.
        if cache_dir is not None:
            self.cache_dir = (ctx.work_dir / Path(cache_dir)).resolve()
        else:
            self.cache_dir = (ctx.work_dir / DEFAULT_CACHE_DIR).resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Bug #67: per-cache-key lock prevents concurrent same-key requests
        # from interleaving partial writes to the same files.
        # Bug #15: use a WeakValueDictionary so locks are released when no
        # coroutine is actively holding them, preventing a long-running worker
        # from leaking memory for every distinct cache key it processes.
        self._key_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )
        # Bug #84: provider instances are reused across requests in the same
        # manager. This avoids repeatedly creating HTTP clients / reloading model
        # weights for every request in a batch.
        self._provider_instances: dict[str, GenerativeProvider] = {}
        #: Image generations started by this manager, for ``max_images_per_run``.
        self._images_started = 0
        #: #283: set when a pricing, budget-snapshot or ledger write failed.
        #: QA reads the persisted reasons rather than trusting the totals.
        self.cost_tracking_degraded = False
        self.cost_tracking_reasons: list[str] = []
        #: #292: one agy preflight per manager run; a failure drops it from the
        #: chain for this run instead of paying its 300 s timeout on every still.
        self._agy_preflight_done = False
        self._agy_available = False
        self._agy_preflight_lock = asyncio.Lock()

    def _cache_paths(self, request: AssetRequest) -> tuple[Path, Path]:
        """Return (media_path, meta_path) for a request."""
        key = request.cache_key()
        ext = request.output_ext or "bin"
        media_path = self.cache_dir / f"{key}.{ext}"
        meta_path = self.cache_dir / f"{key}{META_SUFFIX}"
        return media_path, meta_path

    def _enrich_result(self, result: AssetResult, request: AssetRequest) -> AssetResult:
        """Ensure the result carries identifying fields from its request."""
        updates: dict[str, Any] = {"cached": result.cached or False}
        if not result.asset_id:
            updates["asset_id"] = request.asset_id
        if not result.scene_id:
            updates["scene_id"] = request.scene_id
        return result.model_copy(update=updates)

    def _load_cached(self, request: AssetRequest) -> AssetResult | None:
        """Load a cached result if both media and meta files exist and are valid."""
        media_path, meta_path = self._cache_paths(request)
        if not media_path.exists() or not meta_path.exists():
            return None
        try:
            with open(meta_path, encoding="utf-8") as f:
                data = json.load(f)
            result = AssetResult(**data)
            if result.resolved_path().exists():
                return self._enrich_result(result, request).model_copy(update={"cached": True})
        except Exception:  # noqa: BLE001 - try the next provider in the chain
            logger.warning(
                "cache_meta_corrupted",
                job_id=request.job_id,
                asset_id=request.asset_id,
                meta=str(meta_path),
            )
        return None

    def _save_cached(self, result: AssetResult, meta_path: Path) -> None:
        """Write the cache meta file for a resolved asset atomically.

        Bug #67: write to a uniquely-named temp file and ``os.replace`` it
        into place so a crash mid-write cannot leave a truncated meta file.
        """
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = meta_path.with_suffix(f"{meta_path.suffix}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(result.model_dump(mode="json"), f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, meta_path)

    def _key_lock(self, key: str) -> asyncio.Lock:
        """Return the per-cache-key concurrency lock, creating it if needed."""
        lock = self._key_locks.get(key)
        if lock is None:
            new_lock = asyncio.Lock()
            existing = self._key_locks.setdefault(key, new_lock)
            lock = existing if existing is not None else new_lock
        return lock

    def _mark_cost_tracking_degraded(self, reason: str) -> None:
        """Record a spend-tracking failure and persist it for QA (#283).

        The failure itself stays non-fatal: a render can finish without a
        ledger row. What must not happen is the build silently presenting its
        incomplete total as complete, so the reason is written beside the job
        and surfaced in ``qa.json`` and ``ve cost-report``.
        """
        self.cost_tracking_degraded = True
        if reason in self.cost_tracking_reasons:
            return
        self.cost_tracking_reasons.append(reason)
        logger.warning(
            "cost_tracking_degraded",
            job_id=self.ctx.job_id,
            reason=reason,
            reasons=list(self.cost_tracking_reasons),
        )
        try:
            write_cost_tracking_degraded(self.ctx.work_dir, self.cost_tracking_reasons)
        except OSError:
            logger.warning(
                "cost_tracking_degraded_marker_failed",
                job_id=self.ctx.job_id,
                exc_info=True,
            )
        try:
            self.ctx.ledger.record_issue(
                self.ctx.job_id,
                severity="medium",
                category="cost",
                code="COST_TRACKING_DEGRADED",
                description=reason,
                job_id=self.ctx.job_id,
            )
        except Exception:  # noqa: BLE001 — the marker above is the fallback
            logger.warning(
                "cost_tracking_degraded_issue_failed",
                job_id=self.ctx.job_id,
                exc_info=True,
            )

    def _persist_budget_snapshot(self) -> None:
        """Write the current budget state to the job work dir if a governor is attached."""
        if self.budget is None:
            return
        try:
            self.budget.save(self.ctx.work_dir / "budget" / "budget.json")
        except Exception:  # noqa: BLE001 - try the next provider in the chain
            logger.warning("budget_persist_failed", job_id=self.ctx.job_id, exc_info=True)
            self._mark_cost_tracking_degraded(
                f"budget snapshot could not be written for {self.ctx.job_id}"
            )

    def _request_cost(self, request: AssetRequest, provider_name: str | None) -> float | None:
        """Price one request from the registry when no budget priced it (#273).

        The short path runs without a governor — its approval gate is the CLI
        estimate — so every ledger row it wrote had NULL cost and
        ``ve cost-report`` attributed none of the build. A provider that
        reports its own cost still wins in ``_record_cost_line``.
        """
        if not provider_name:
            return None
        try:
            if self._pricing_registry is None:
                self._pricing_registry = PricingRegistry.load()
            context = self._budget_context(request, provider_name)
            return float(
                estimate_cost(
                    provider_name,
                    request.asset_type.value,
                    registry=self._pricing_registry,
                    model=context["model"],
                    char_count=context["char_count"],
                    duration_s=context["duration_s"],
                    count=context["count"],
                    params=context["params"],
                )
            )
        except Exception:  # noqa: BLE001 - try the next provider in the chain
            logger.warning(
                "cost_line_pricing_failed",
                job_id=request.job_id,
                asset_id=request.asset_id,
                provider=provider_name,
                exc_info=True,
            )
            self._mark_cost_tracking_degraded(
                f"could not price {request.asset_type.value} call to {provider_name} "
                f"(asset {request.asset_id})"
            )
            return None

    def _record_cost_line(
        self,
        request: AssetRequest,
        result: AssetResult,
        estimate: BudgetEstimate | None,
        *,
        status: str = "success",
    ) -> None:
        """Write a cost_lines row to the project ledger if one is attached."""
        try:
            actual = result.metadata.get("cost")
            fallback_from = result.metadata.get("_fallback_from")
            chain_attempted = result.metadata.get("chain_attempted")
            provider_name = result.metadata.get("provider") or request.provider or fallback_from
            metadata: dict[str, Any] | None = None
            if request.scene_id or fallback_from or chain_attempted:
                metadata = {"scene_id": request.scene_id}
                if fallback_from:
                    metadata["fallback_from"] = fallback_from
                if chain_attempted:
                    metadata["chain_attempted"] = list(chain_attempted)
            estimated = estimate.estimated_cost if estimate else None
            actual_cost: float | None
            if status == "cached":
                # A cache hit bought nothing: record zero rather than NULL so
                # the row is counted without inflating per-project spend.
                estimated = 0.0
                actual_cost = 0.0
            elif actual is not None:
                actual_cost = float(actual)
            else:
                if estimated is None:
                    estimated = self._request_cost(request, provider_name)
                actual_cost = estimated
            if actual_cost is None and status != "cached":
                self._mark_cost_tracking_degraded(
                    f"unpriced {request.asset_type.value} call to {provider_name} "
                    f"(asset {request.asset_id})"
                )
            record_cost_line(
                self.ctx,
                asset_id=request.asset_id,
                provider=provider_name,
                task_type=request.asset_type.value,
                model=result.metadata.get("model"),
                estimated_cost=estimated,
                actual_cost=actual_cost,
                currency="credits",
                status=status,
                job_id=request.job_id,
                metadata=metadata,
            )
        except Exception:  # noqa: BLE001 - try the next provider in the chain
            logger.warning("ledger_cost_line_failed", job_id=self.ctx.job_id, exc_info=True)
            self._mark_cost_tracking_degraded(
                f"ledger cost line could not be written for asset {request.asset_id}"
            )

    _AUTO_FALLBACKS: ClassVar[dict[str, str]] = {
        "tts": "stub_tts",
        "image": "stub_image",
        "video": "stub_hyperframes_video",
        "data_viz": "matplotlib_data_viz",
        "music": "stub_music",
        "sfx": "stub_sfx",
        "alignment": "stub_alignment",
        "isolation": "stub_isolation",
        "speech_to_speech": "stub_speech_to_speech",
    }

    def _generative_image_providers(self) -> set[str]:
        """Configured providers that actually *generate* an image.

        Eligibility is a positive marker (``GenerativeProvider.generates_images``)
        rather than "anything that is not stock": TTS, music and data-viz
        providers all inherit ``GenerativeProvider`` but cannot serve an image
        request, and letting one into the candidate set would send a still to a
        provider that writes audio. Stock libraries, local-file ingestion and
        the image stubs leave the marker ``False``. When nothing qualifies, the
        caller keeps the unfiltered scored selection (see
        :meth:`_select_auto_provider`).
        """
        names: set[str] = set()
        for name in self.config.providers:
            provider_cls = PROVIDER_REGISTRY.get(name)
            if not isinstance(provider_cls, type):
                continue
            if not issubclass(provider_cls, GenerativeProvider):
                continue
            if not provider_cls.generates_images:
                continue
            if issubclass(provider_cls, LocalMediaProvider | StubImageProvider):
                continue
            names.add(name)
        return names

    def _select_auto_provider(self, request: AssetRequest) -> str:
        """Pick the provider for an ``"auto"`` request.

        When a model is named on the request, selection is model-aware and
        cost-first: only providers that can serve that model are considered and
        each is scored with its real per-model unit cost, so the same model
        lands on whichever configured provider serves it cheapest.

        With no ``image_chain`` configured, an ``"auto"`` *image* is restricted
        to providers that generate; otherwise the free stock libraries outscore
        the paid generators and a still silently becomes a photograph. The
        restriction is dropped when no configured provider generates, so a
        stub (or stock) result is still better than refusing the asset.
        """
        model = request.params.get("model")
        criteria = {"model": model} if isinstance(model, str) and model else None
        eligible: set[str] | None = None
        if request.asset_type is AssetType.IMAGE and not self._image_chain():
            generative = self._generative_image_providers()
            if generative:
                eligible = generative
        # ElevenLabs is voice-over only, so scoring never picks its music/SFX/alignment providers.
        blocked = {
            n
            for n in self.config.providers
            if n.startswith("elevenlabs") and n not in VOICE_PROVIDERS
        }
        if blocked:
            eligible = (eligible if eligible is not None else set(self.config.providers)) - blocked
        try:
            provider_name, _ = self.config.select_provider(
                request.asset_type.value, criteria=criteria, eligible=eligible
            )
        except ProviderSelectionError as exc:
            provider_name = self._AUTO_FALLBACKS.get(request.asset_type.value, "stub_image")
            if self.config.strict and provider_name.startswith("stub_"):
                raise StubFallbackRefusedError(
                    f"no configured provider can serve {request.asset_type.value!r} "
                    f"(asset {request.asset_id}); assets.strict refuses the "
                    f"{provider_name} placeholder"
                ) from exc
            logger.warning(
                "provider_auto_fallback",
                job_id=request.job_id,
                asset_id=request.asset_id,
                asset_type=request.asset_type.value,
                fallback=provider_name,
            )
        if request.asset_type is AssetType.IMAGE and not self._image_chain():
            selected_cls = PROVIDER_REGISTRY.get(provider_name)
            if isinstance(selected_cls, type) and not getattr(
                selected_cls, "generates_images", False
            ):
                # Nothing configured generates stills, so the scored selector
                # reached for a stock library or a stub. Either way the asset is
                # not the generated scene the prompt asked for, and a caller
                # reading only the result would never know.
                logger.warning(
                    "provider_auto_stock_fallback",
                    job_id=request.job_id,
                    asset_id=request.asset_id,
                    provider=provider_name,
                    reason="no configured provider generates images; serving a non-generated asset",
                )
        return provider_name

    def _resolve_auto_provider(self, request: AssetRequest) -> str:
        """Select the ``"auto"`` provider and translate the named model to that
        provider's id (e.g. ``gpt-image-2`` -> ``openai/gpt-image-2``).

        Called once per request from :meth:`_resolve_one`; the resolved provider
        name is written back onto the request so later re-resolution does not
        re-run (and re-translate) selection.
        """
        provider_name = self._select_auto_provider(request)
        model = request.params.get("model")
        if provider_name and isinstance(model, str) and model:
            from video_editor_asset_ingestion.pricing import PricingRegistry

            resolved = PricingRegistry.load().resolve_model(provider_name, model)
            if resolved != model:
                request.params["model"] = resolved
                logger.info(
                    "provider_model_translated",
                    asset_type=request.asset_type.value,
                    provider=provider_name,
                    canonical=model,
                    resolved=resolved,
                )
        return provider_name

    def _resolve_provider(
        self, request: AssetRequest, provider_name: str | None = None
    ) -> GenerativeProvider:
        """Instantiate (or reuse) the right provider for a request."""
        provider_name = (
            provider_name
            or request.provider
            or self.config.defaults.get(request.asset_type.value, "")
        )
        if provider_name == "auto":
            provider_name = self._select_auto_provider(request)
        if not provider_name:
            raise RuntimeError(f"No provider configured for asset type {request.asset_type.value}")
        # ElevenLabs is voice-over only: fail before building a music/SFX/alignment provider.
        ensure_voice_only(provider_name)

        # Bug #84: reuse the provider instance across requests in this manager.
        if provider_name in self._provider_instances:
            return self._provider_instances[provider_name]

        provider_cls = get_provider(provider_name)

        # Providers that accept a ProviderConfig for API keys / timeouts / options.
        if provider_name in {
            "openai_tts",
            "elevenlabs_tts",
            "elevenlabs_clone",
            "elevenlabs_music",
            "elevenlabs_sfx",
            "elevenlabs_isolation",
            "elevenlabs_speech_to_speech",
            "elevenlabs_forced_alignment",
            "replicate_image",
            "fal_ai",
            "poyo_ai",
            "agy_image",
            "flow_image",
            # PoYo Music reads ``POYO_API_KEY`` from its ProviderConfig; without
            # it the manager would construct a provider with no credentials.
            "poyo_music",
            "openrouter",
            "pexels",
            "pixabay",
            "hyperframes_video",
            # Free SFX providers read their token (Freesound) or optional
            # token (Openverse) from ProviderConfig; without it the
            # provider constructs with no credentials.
            "freesound_sfx",
            "openverse_sfx",
            "earshot_sfx",
        }:
            _, provider_config = self.config.provider_for(request.asset_type.value, provider_name)
            provider = cast(Callable[..., GenerativeProvider], provider_cls)(provider_config)
        elif provider_name == "stub_tts" or provider_name in {
            "stub_image",
            "matplotlib_data_viz",
            "stub_alignment",
            "stub_isolation",
            "stub_speech_to_speech",
            "stub_hyperframes_video",
        }:
            provider = cast(GenerativeProvider, provider_cls())
        else:
            provider = cast(GenerativeProvider, provider_cls())

        self._provider_instances[provider_name] = provider
        return provider

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _generate_one(
        self,
        request: AssetRequest,
        media_path: Path,
        meta_path: Path,
        estimate: BudgetEstimate | None = None,
        *,
        provider_name: str | None = None,
        stub_fallback: bool = True,
    ) -> tuple[AssetResult, str]:
        """Generate a single asset through its provider and cache the result.

        For ``fal_ai`` requests, if generation fails after the tenacity retry
        budget is exhausted, we fall back to ``StubImageProvider`` so the
        pipeline can still complete with a placeholder image rather than
        failing the whole job. The fallback is logged at warning level so
        operators notice when FAL is degraded. ``stub_fallback`` is true only
        for the chain's terminal attempt, so an earlier FAL attempt fails
        normally and the chain can fall through to the next provider.

        When a ``BudgetGovernor`` is attached, the estimated cost is reserved
        before generation and the actual cost (from provider metadata) is
        recorded afterward.

        Returns the result and the ledger status to record (``"success"`` or
        ``"fallback"``). The cost line itself is written by the caller: only
        the chain walk knows the fallback provenance it must carry.
        """
        provider = self._resolve_provider(request, provider_name)
        logger.info(
            "asset_generate_start",
            job_id=request.job_id,
            asset_id=request.asset_id,
            provider=provider.provider_name,
        )
        try:
            result = await provider.generate(request, media_path)
        except Exception as exc:
            # ``assets.strict`` (#281): a failed terminal attempt raises rather
            # than shipping a placeholder still.
            if (
                stub_fallback
                and not self.config.strict
                and provider.provider_name == "fal_ai"
                and request.asset_type in {AssetType.IMAGE, AssetType.VIDEO}
            ):
                logger.warning(
                    "asset_provider_fallback",
                    job_id=request.job_id,
                    asset_id=request.asset_id,
                    original_provider=provider.provider_name,
                    fallback_provider=StubImageProvider.provider_name,
                    error=str(exc),
                )
                fallback = StubImageProvider()
                result = await fallback.generate(request, media_path)
                result = self._enrich_result(result, request)
                # Bug #53: do NOT cache fallback results. Caching a stub
                # poisons the cache key so FAL is never retried after an
                # outage. The fallback result is returned uncached; the next
                # run will try the real provider again.
                # Bug #13: mark the result as a fallback so _resolve_one can
                # still count the primary-provider failure against the circuit
                # breaker even though the returned AssetResult looks successful.
                result.metadata["_fallback_from"] = provider.provider_name
                logger.info(
                    "asset_generate_complete_fallback",
                    job_id=request.job_id,
                    asset_id=request.asset_id,
                    cached=False,
                    path=str(media_path),
                )
                return result, "fallback"
            raise

        result = self._enrich_result(result, request)
        if self.budget is not None and estimate is not None:
            actual = result.metadata.get("cost")
            self.budget.record_actual(
                estimate, actual if actual is not None else estimate.estimated_cost
            )
            self._persist_budget_snapshot()
        try:
            self._save_cached(result, meta_path)
        except Exception as exc:  # noqa: BLE001
            # The asset was generated (and possibly paid for); a cache write
            # failure must not be mistaken for a provider failure and re-run
            # the chain for a second charge.
            logger.warning(
                "asset_cache_write_failed",
                job_id=request.job_id,
                asset_id=request.asset_id,
                provider=provider.provider_name,
                meta=str(meta_path),
                error=str(exc),
            )
        logger.info(
            "asset_generate_complete",
            job_id=request.job_id,
            asset_id=request.asset_id,
            cached=False,
            path=str(media_path),
        )
        return result, "success"

    def _provider_semaphore(self, provider_name: str) -> asyncio.Semaphore:
        """Return the class-level concurrency semaphore for ``provider_name``."""
        if provider_name not in self._semaphores:
            limit = _CLOUD_PROVIDER_LIMITS.get(provider_name, _DEFAULT_CLOUD_LIMIT)
            self._semaphores[provider_name] = asyncio.Semaphore(limit)
        return self._semaphores[provider_name]

    def _circuit_breaker(self, provider_name: str) -> ProviderCircuitBreaker:
        """Return the class-level circuit breaker for ``provider_name``.

        ``agy_image`` gets a long window: one quota/auth failure can otherwise
        burn ten minutes per still before the next provider gets a turn.
        """
        if provider_name not in self._circuit_breakers:
            recovery = 1800.0 if provider_name in ("agy_image", "flow_image") else 60.0
            provider_cfg = self.config.providers.get(provider_name)
            if provider_cfg is not None:
                configured = (provider_cfg.provider_options or {}).get("recovery_seconds")
                if isinstance(configured, int | float) and not isinstance(configured, bool):
                    recovery = float(configured)
            self._circuit_breakers[provider_name] = ProviderCircuitBreaker(
                provider_name, recovery_seconds=recovery
            )
        return self._circuit_breakers[provider_name]

    def _budget_context(self, request: AssetRequest, provider_name: str) -> dict[str, Any]:
        """Pricing context for one request, so the budget prices in real USD.

        The model id comes from the request's own params first (a plan may pin
        a model per scene), then from the provider's configured default. The
        params travel with it so a tiered provider reserves the right tier —
        a 2K image is not the 1K price (#269).
        """
        params = request.params or {}
        provider_cfg = self.config.providers.get(provider_name)
        model = ""
        if isinstance(params.get("model"), str):
            model = params["model"]
        elif provider_cfg is not None:
            model = (
                provider_cfg.default_model
                or provider_cfg.provider_options.get("tts_model", "")
                or provider_cfg.provider_options.get("video_model", "")
                or provider_cfg.provider_options.get("music_model", "")
                or ""
            )
        duration_s = 0.0
        for key in ("duration_seconds", "duration"):
            value = params.get(key)
            if isinstance(value, (int, float)) and value > 0:
                duration_s = float(value)
                break
        return {
            "model": model or "default",
            "char_count": len(request.prompt or ""),
            "duration_s": duration_s,
            "count": 1,
            "params": params,
        }

    def _image_chain(self) -> list[str]:
        """The configured image chain for this run (draft or final)."""
        return self.config.image_chain_for(self.final)

    async def _agy_preflight_ok(self, request: AssetRequest) -> bool:
        """Check the local agent once per manager run (#292)."""
        if self._agy_preflight_done:
            return self._agy_available
        async with self._agy_preflight_lock:
            if self._agy_preflight_done:
                return self._agy_available
            self._agy_preflight_done = True
            try:
                provider = self._resolve_provider(request, "agy_image")
                preflight = getattr(provider, "preflight", None)
                if preflight is None:
                    self._agy_available = True
                else:
                    await asyncio.wait_for(preflight(), timeout=20.0)
                    self._agy_available = True
            except Exception as exc:  # noqa: BLE001 — a failed probe degrades the run
                self._agy_available = False
                logger.warning(
                    "agy_preflight_failed",
                    job_id=self.ctx.job_id,
                    error=str(exc),
                    action="dropping agy_image from this run's image chain",
                )
        return self._agy_available

    async def _chain_for_request(self, request: AssetRequest) -> list[str]:
        """Provider chain after the per-run agy preflight (#292)."""
        chain = self._provider_chain(request)
        if "agy_image" not in chain:
            return chain
        if await self._agy_preflight_ok(request):
            return chain
        return [name for name in chain if name != "agy_image"]

    def _provider_chain(self, request: AssetRequest) -> list[str]:
        """Return the providers to try, in order, for this request.

        An explicit provider means exactly that provider; otherwise the
        configured image chain applies; otherwise the existing default/scored
        selection is used unchanged.
        """
        explicit = request.provider
        if explicit == STOCK_PROVIDER and request.asset_type in (AssetType.VIDEO, AssetType.IMAGE):
            # The same free libraries serve photos and clips (Pexels, Pixabay).
            return self.config.stock_video_chain_for()
        if explicit and explicit != "auto":
            return [explicit]
        if request.asset_type is AssetType.IMAGE and self._image_chain():
            return self._image_chain()
        # The request wins over the config default: a request-level "auto" must
        # reach the scored selector rather than being swallowed by the default.
        name = explicit or self.config.defaults.get(request.asset_type.value, "")
        if name == "auto":
            name = self._resolve_auto_provider(request)
            request.provider = name
        return [name] if name else []

    async def _attempt_provider(
        self,
        request: AssetRequest,
        provider_name: str,
        media_path: Path,
        meta_path: Path,
        *,
        stub_fallback: bool,
    ) -> tuple[AssetResult, BudgetEstimate | None, str]:
        """Run one provider with its own budget reservation, semaphore, and breaker.

        Returns the result, the reservation the attempt made (if any), and the
        ledger status for the caller to record.
        """
        breaker = self._circuit_breaker(provider_name)
        # Check before reserving: an open breaker costs nothing, so there is
        # no reservation to unwind. The error propagates to the chain loop.
        breaker.check()

        estimate: BudgetEstimate | None = None
        if self.budget is not None:
            estimate = self.budget.allocate(
                provider_name,
                request.asset_type.value,
                metadata={"asset_id": request.asset_id},
                **self._budget_context(request, provider_name),
            )
            self._persist_budget_snapshot()

        async with self._provider_semaphore(provider_name):
            try:
                result, cost_status = await self._generate_one(
                    request,
                    media_path,
                    meta_path,
                    estimate=estimate,
                    provider_name=provider_name,
                    stub_fallback=stub_fallback,
                )
            except ProviderConfigError:
                # Configuration mistakes are not signs that the provider is
                # unhealthy, so they must not trip its breaker. The
                # reservation covers nothing spent. (``BudgetExceededError``
                # cannot occur here: ``allocate`` above is outside the try.)
                self._release_estimate(estimate)
                self._persist_budget_snapshot()
                raise
            except Exception as exc:
                breaker.record_failure(immediate=getattr(exc, "kind", None) in {"quota", "auth"})
                self._release_estimate(estimate)
                self._persist_budget_snapshot()
                raise
            if result.metadata.get("_fallback_from") == provider_name:
                # A placeholder came back: the provider failed, nothing was
                # purchased, so the reservation must not stay outstanding.
                breaker.record_failure()
                self._release_estimate(estimate)
                self._persist_budget_snapshot()
            else:
                breaker.record_success()
            return result, estimate, cost_status

    def _release_estimate(self, estimate: BudgetEstimate | None) -> None:
        """Undo a budget reservation for an attempt that produced nothing.

        Only an *outstanding* reservation is released. An attempt that already
        recorded an actual cost was paid for (e.g. generation succeeded but
        caching then failed), so its spend must stand.
        """
        if estimate is not None and self.budget is not None and estimate.actual_cost is None:
            self.budget.record_actual(estimate, 0.0)

    def _count_image(self) -> None:
        """Count one chain image generation against the per-run cap.

        The cap bounds *chain runs*: every image request the configured
        ``image_chain`` serves counts, whether the attempt lands on the
        quota-based ``agy_image`` or a paid fallback. One request that falls
        through agy to poyo therefore consumes one unit for two generations.

        It is inert when no chain is configured, and inert per request when
        the request pins its own provider: neither job opted into the chain,
        so neither must be capped.

        The field has no lower bound, so a non-positive ``max_images_per_run``
        means "no cap" rather than "refuse every image".
        """
        limit = self.config.max_images_per_run
        # Check before incrementing so the counter means "generations started"
        # rather than "requests seen". There is no await between the two
        # statements, so concurrent callers still admit exactly ``limit``.
        if limit > 0 and self._images_started >= limit:
            raise ImageQuotaError(
                f"Image cap reached: {limit} images per run (max_images_per_run); "
                f"refusing image {self._images_started + 1}"
            )
        self._images_started += 1

    @staticmethod
    def _chain_consumes_quota(chain: list[str]) -> bool:
        """Return whether resolving through ``chain`` spends image budget.

        ``max_images_per_run`` bounds *generations*, not requests. A chain
        whose providers all ingest existing media (``local_media`` copies a
        file) generates nothing, so it must not count against the cap.

        A provider counts when any name in the chain declares
        ``consumes_image_quota`` (the base default). An unregistered name is
        treated as counting: over-counting may stop a run early, but
        under-counting would let an unknown provider overspend.
        """
        for name in chain:
            provider_cls = PROVIDER_REGISTRY.get(name)
            # ``getattr`` with a True default also covers registered music
            # providers, which are not ``GenerativeProvider`` subclasses.
            if provider_cls is None or getattr(provider_cls, "consumes_image_quota", True):
                return True
        return False

    async def _resolve_one(self, request: AssetRequest) -> AssetResult:
        """Resolve one request from cache or by generating it.

        ``_fallback_from`` and ``chain_attempted`` describe the run rather than
        the asset: the chain loop attaches them only after ``_generate_one`` has
        already written the cache meta, so they are not in the cached metadata.
        The cost line, by contrast, is written here *after* they are attached,
        so a fallback row attributes the provider that failed (design §5.2).

        ``max_images_per_run`` is charged here, once per image request that
        enters a configured ``image_chain``. A request that pins a provider
        bypasses the chain and is not charged; with no chain configured the
        cap is inert.
        """
        key = request.cache_key()
        async with self._key_lock(key):
            cached = self._load_cached(request)
            if cached is not None:
                logger.info(
                    "asset_cache_hit",
                    job_id=request.job_id,
                    asset_id=request.asset_id,
                )
                self._record_cost_line(request, cached, None, status="cached")
                return cached

            media_path, meta_path = self._cache_paths(request)
            chain = await self._chain_for_request(request)
            if not chain:
                raise RuntimeError(
                    f"No provider configured for asset type {request.asset_type.value}"
                )
            # The cap bounds requests that enter the chain. A request that
            # pins its own provider bypasses the chain entirely, so its paid
            # (or local) generation is not a chain run and must not count —
            # otherwise the (limit+1)th pinned image would abort a job that
            # never opted into the cap.
            pinned_provider = bool(request.provider) and request.provider != "auto"
            if (
                request.asset_type is AssetType.IMAGE
                and not pinned_provider
                and self._image_chain()
                and self._chain_consumes_quota(chain)
            ):
                self._count_image()

            failures: list[tuple[str, str]] = []
            skipped: list[str] = []
            visited: list[str] = []
            for index, provider_name in enumerate(chain):
                last = index == len(chain) - 1
                visited.append(provider_name)
                try:
                    result, estimate, cost_status = await self._attempt_provider(
                        request,
                        provider_name,
                        media_path,
                        meta_path,
                        stub_fallback=last,
                    )
                except Exception as exc:  # noqa: BLE001
                    if isinstance(exc, CircuitBreakerOpenError):
                        # It never ran, so it is not a fallback *source*; keep
                        # the skipped names separately so the log still shows
                        # why the chain moved on.
                        skipped.append(provider_name)
                        event = "provider_chain_skipped"
                    else:
                        failures.append((provider_name, str(exc)))
                        if isinstance(exc, BudgetExceededError):
                            event = "provider_chain_budget_exceeded"
                        elif isinstance(exc, ProviderConfigError):
                            event = "provider_chain_misconfigured"
                        else:
                            event = "provider_chain_failed"
                    logger.warning(
                        event,
                        job_id=request.job_id,
                        asset_id=request.asset_id,
                        provider=provider_name,
                        error=str(exc),
                        remaining=chain[index + 1 :],
                        skipped=skipped,
                    )
                    if not last:
                        continue
                    raise
                # Always record which providers the chain touched, even on a
                # clean first-provider success: without it a chain run and a
                # pinned single-provider run produce identical metadata.
                result.metadata["chain_attempted"] = list(visited)
                if failures:
                    result.metadata["_fallback_from"] = failures[0][0]
                self._record_cost_line(request, result, estimate, status=cost_status)
                return result

            # Unreachable: ``chain`` was proven non-empty above, and the loop
            # either returns a result or re-raises on its last attempt. This
            # terminal raise exists only so the type checker can see that.
            raise AssertionError("unreachable: provider chain was empty")

    async def resolve_partial(self, requests: list[AssetRequest]) -> BatchResult:
        """Resolve a batch, then close long-lived provider sessions."""
        try:
            return await self._resolve_partial_inner(requests)
        finally:
            await self.aclose()

    async def _resolve_partial_inner(self, requests: list[AssetRequest]) -> BatchResult:
        """Resolve a batch and return both successes and failures.

        Bug #83: callers that can tolerate partial results (e.g. per-scene
        SFX generation) receive a :class:`BatchResult` instead of an
        all-or-nothing exception. Item-level failures are collected in
        :attr:`BatchResult.failures`.

        The one exception is :class:`ImageQuotaError`: a
        ``max_images_per_run`` breach is a run-level abort, not an item-level
        failure, so it escapes instead of being recorded in ``failures``.
        Letting the batch degrade would hand the pipeline scenes that are
        missing precisely because the budget ran out.

        Raises:
            ImageQuotaError: The run asked for more image generations than
                ``max_images_per_run`` allows.
        """
        if not requests:
            return BatchResult()

        logger.info(
            "asset_batch_start",
            job_id=self.ctx.job_id,
            count=len(requests),
        )
        results = await asyncio.gather(
            *(self._resolve_one(r) for r in requests),
            return_exceptions=True,
        )

        successes: list[AssetResult] = []
        failures: list[tuple[str, str]] = []
        for request, result in zip(requests, results, strict=False):
            if isinstance(result, ImageQuotaError):
                # A cap breach stops the whole run. Degrading to a partial
                # batch would let the pipeline proceed with scenes missing,
                # which is exactly what ``max_images_per_run`` exists to
                # prevent.
                logger.error(
                    "image_quota_exceeded",
                    job_id=self.ctx.job_id,
                    started=self._images_started,
                    limit=self.config.max_images_per_run,
                )
                raise result
            if isinstance(result, AssetResult):
                successes.append(result)
            else:
                failures.append((request.asset_id, repr(result)))
                logger.error(
                    "asset_batch_failure",
                    asset_id=request.asset_id,
                    error=str(result),
                )

        logger.info(
            "asset_batch_partial_complete",
            job_id=self.ctx.job_id,
            success=len(successes),
            failures=len(failures),
        )
        return BatchResult(results=successes, failures=failures)

    async def aclose(self) -> None:
        """Close long-lived provider resources (the agy stream sessions)."""
        for provider in list(self._provider_instances.values()):
            close = getattr(provider, "aclose", None)
            if close is None:
                continue
            try:
                await close()
            except Exception:  # noqa: BLE001 — cleanup must not hide the job
                logger.warning(
                    "asset_provider_close_failed",
                    job_id=self.ctx.job_id,
                    provider=getattr(provider, "provider_name", "unknown"),
                    exc_info=True,
                )
        self._provider_instances.clear()

    async def resolve(self, requests: list[AssetRequest]) -> list[AssetResult]:
        """Resolve all asset requests concurrently.

        Retains the original all-or-nothing contract; use
        :meth:`resolve_partial` when partial success is acceptable.
        """
        batch = await self.resolve_partial(requests)
        if batch.failures:
            first_error = batch.failures[0][1]
            raise RuntimeError(f"{len(batch.failures)} asset request(s) failed: {first_error}")
        return batch.results
