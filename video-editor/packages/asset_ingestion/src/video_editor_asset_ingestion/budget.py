"""Runtime budget governance for generative asset ingestion.

The governor tracks estimated and actual spend per job in arbitrary
"credit" units. A single ``--budget`` value caps the total credit spend for
a run; the manager checks each request against the remaining balance before
invoking a provider.

Costs are intentionally conservative estimates. Providers that expose real
usage/cost metadata can update estimates after generation via
``record_actual``. Until then the estimate stands.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from video_editor_state.errors import VEBudgetError

from video_editor_asset_ingestion.pricing import (
    PricingRegistry,
    estimate_image_cost,
    estimate_music_cost,
    estimate_tts_cost,
    estimate_video_cost,
)

logger = structlog.get_logger(__name__)


class BudgetExceededError(VEBudgetError):
    """Raised when a request would exceed the configured budget."""


@dataclass
class BudgetEstimate:
    """One line-item in the running budget ledger."""

    provider: str
    task_type: str
    estimated_cost: float
    actual_cost: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# Approximate credit cost per request by provider and task type. These are
# rough planning numbers; real provider metadata can override them after the
# fact via ``record_actual``.
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "image": {
        # Quota-based, not credit-based. Also 0.0 in the pricing registry
        # because agy_image has no model-level price entry.
        "agy_image": 0.0,
        # Google Flow stills: Nano Banana costs 0 Flow credits, and the provider sends max_credits 0.
        "flow_image": 0.0,
        "fal_ai": 3.0,
        "poyo_ai": 1.0,
        "replicate_image": 4.0,
        "pexels": 0.0,
        "pixabay": 0.0,
        "coverr": 0.0,
        "mixkit": 0.0,
        "stub_image": 0.0,
    },
    "video": {
        "hyperframes_video": 2.0,
        "poyo_ai": 1.5,
        "fal_ai": 5.0,
        "pexels": 0.0,
        "pixabay": 0.0,
        "coverr": 0.0,
        "mixkit": 0.0,
        "stub_hyperframes_video": 0.0,
    },
    "tts": {
        "elevenlabs_tts": 18.0,
        "openai_tts": 1.5,
        "sarvam_tts": 0.5,
        "kokoro_tts": 0.0,
        "kitten_tts": 0.0,
        "moss_tts": 0.0,
        "stub_tts": 0.0,
    },
    "music": {
        "elevenlabs_music": 10.0,
        "poyo_music": 1.5,
        "ace_step": 0.0,
        "stub_music": 0.0,
    },
    "sfx": {
        "elevenlabs_sfx": 8.0,
        "stub_sfx": 0.0,
    },
    "alignment": {
        "elevenlabs_forced_alignment": 0.0,
        "stub_alignment": 0.0,
    },
    "isolation": {
        "elevenlabs_isolation": 0.0,
        "stub_isolation": 0.0,
    },
    "speech_to_speech": {
        "elevenlabs_speech_to_speech": 15.0,
        "stub_speech_to_speech": 0.0,
    },
    "data_viz": {
        "matplotlib_data_viz": 0.0,
    },
}


def estimate_cost(
    provider: str,
    task_type: str,
    pricing: dict[str, dict[str, float]] | None = None,
    registry: PricingRegistry | None = None,
    model: str = "default",
    char_count: int = 0,
    duration_s: float = 0.0,
    count: int = 1,
    params: dict[str, Any] | None = None,
) -> float:
    """Return the estimated USD cost for one provider/task request.

    Prefers the new PricingRegistry (model-level USD costs). Falls back
    to the legacy DEFAULT_PRICING table (abstract credits) when no
    registry is available.

    ``params`` is the asset request's own params; a model that prices by a
    request attribute (a resolution tier) is priced at the tier the request
    actually asks for (#269).
    """
    if registry is not None:
        if task_type == "tts":
            return estimate_tts_cost(provider, model, char_count, registry)
        elif task_type in ("image", "data_viz"):
            return estimate_image_cost(provider, model, count, registry, params=params)
        elif task_type == "video":
            return estimate_video_cost(
                provider, model, duration_s, registry, params=params, count=count
            )
        elif task_type == "music":
            return estimate_music_cost(provider, model, duration_s, registry, params=params)

    # Legacy credit-based pricing (deprecated, kept for backward compat)
    table = pricing if pricing is not None else DEFAULT_PRICING
    return table.get(task_type, {}).get(provider, 0.0)


@dataclass
class BudgetSnapshot:
    """Serializable budget state for manifests/reports."""

    limit: float | None
    spent: float
    estimated_remaining: float
    estimate_count: int


class BudgetGovernor:
    """Track estimated and actual spend against an optional credit limit."""

    def __init__(
        self,
        limit: float | None = None,
        pricing: dict[str, dict[str, float]] | None = None,
        registry: PricingRegistry | None = None,
    ) -> None:
        self.limit = limit
        self.pricing = pricing if pricing is not None else DEFAULT_PRICING
        self._registry = registry
        self._estimates: list[BudgetEstimate] = []
        self._actual_spent: float = 0.0

    @property
    def spent(self) -> float:
        """Total committed spend: actual + outstanding estimates."""
        outstanding = sum(e.estimated_cost for e in self._estimates if e.actual_cost is None)
        return self._actual_spent + outstanding

    @property
    def remaining(self) -> float | None:
        """Remaining budget, or ``None`` when unlimited."""
        if self.limit is None:
            return None
        return max(0.0, self.limit - self.spent)

    def can_afford(
        self,
        provider: str,
        task_type: str,
        *,
        model: str = "default",
        char_count: int = 0,
        duration_s: float = 0.0,
        count: int = 1,
        params: dict[str, Any] | None = None,
    ) -> bool:
        """Return True if one more request stays within budget."""
        cost = self._estimate(
            provider, task_type, model, char_count, duration_s, count, params=params
        )
        if self.limit is None:
            return True
        return self.spent + cost <= self.limit

    def _estimate(
        self,
        provider: str,
        task_type: str,
        model: str,
        char_count: int,
        duration_s: float,
        count: int,
        params: dict[str, Any] | None = None,
    ) -> float:
        """Cost of one request, priced in USD when a registry is attached."""
        return estimate_cost(
            provider,
            task_type,
            self.pricing,
            registry=self._registry,
            model=model,
            char_count=char_count,
            duration_s=duration_s,
            count=count,
            params=params,
        )

    def allocate(
        self,
        provider: str,
        task_type: str,
        metadata: dict[str, Any] | None = None,
        *,
        model: str = "default",
        char_count: int = 0,
        duration_s: float = 0.0,
        count: int = 1,
        params: dict[str, Any] | None = None,
    ) -> BudgetEstimate:
        """Reserve budget for a pending request.

        Raises ``BudgetExceededError`` when the request would exceed the limit.

        The cost is priced in **USD** when the governor was built with a
        :class:`PricingRegistry` (which the CLI does), so ``--budget`` means the
        same thing as the total printed by ``ve estimate``. Without a registry
        the legacy abstract-credit table is used. ``params`` selects a price
        tier (e.g. a 2K image), so the reservation matches the bill (#269).
        """
        cost = self._estimate(
            provider, task_type, model, char_count, duration_s, count, params=params
        )
        if self.limit is not None and self.spent + cost > self.limit:
            raise BudgetExceededError(
                f"Budget exceeded: {provider}/{task_type} costs {cost} credits, "
                f"remaining {self.remaining}"
            )
        estimate = BudgetEstimate(
            provider=provider,
            task_type=task_type,
            estimated_cost=cost,
            metadata=metadata or {},
        )
        self._estimates.append(estimate)
        logger.debug(
            "budget_allocated",
            provider=provider,
            task_type=task_type,
            cost=cost,
            spent=self.spent,
            limit=self.limit,
        )
        return estimate

    def record_actual(self, estimate: BudgetEstimate, actual_cost: float) -> None:
        """Replace an estimate with a real cost from provider metadata."""
        if estimate.actual_cost is not None:
            # Remove the prior actual from the running total before replacing it.
            self._actual_spent -= estimate.actual_cost
        estimate.actual_cost = actual_cost
        self._actual_spent += actual_cost
        logger.debug(
            "budget_actual_recorded",
            provider=estimate.provider,
            task_type=estimate.task_type,
            actual_cost=actual_cost,
            spent=self.spent,
        )

    def snapshot(self) -> BudgetSnapshot:
        """Return a serializable view of the current budget state."""
        remaining = self.remaining
        return BudgetSnapshot(
            limit=self.limit,
            spent=self.spent,
            estimated_remaining=remaining if remaining is not None else float("inf"),
            estimate_count=len(self._estimates),
        )

    def ledger(self) -> list[BudgetEstimate]:
        """Return a defensive copy of the running estimate ledger."""
        return list(self._estimates)

    def to_dict(self) -> dict[str, Any]:
        """Serialize governor state to a JSON-friendly dict."""
        snap = self.snapshot()
        return {
            "limit": snap.limit,
            "spent": snap.spent,
            "estimated_remaining": snap.estimated_remaining,
            "estimate_count": snap.estimate_count,
            "estimates": [
                {
                    "provider": e.provider,
                    "task_type": e.task_type,
                    "estimated_cost": e.estimated_cost,
                    "actual_cost": e.actual_cost,
                    "metadata": e.metadata,
                }
                for e in self._estimates
            ],
        }

    def save(self, path: Path | str) -> None:
        """Write the current budget state to ``path`` as JSON.

        The parent directory is created if it does not exist.
        """
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.debug("budget_saved", path=str(target), spent=self.spent)

    @classmethod
    def load(
        cls,
        path: Path | str,
        pricing: dict[str, dict[str, float]] | None = None,
    ) -> BudgetGovernor:
        """Restore a governor from a previously saved JSON file."""
        target = Path(path)
        data = json.loads(target.read_text(encoding="utf-8"))
        governor = cls(limit=data.get("limit"), pricing=pricing)
        for entry in data.get("estimates", []):
            estimate = BudgetEstimate(
                provider=entry["provider"],
                task_type=entry["task_type"],
                estimated_cost=entry["estimated_cost"],
                actual_cost=entry.get("actual_cost"),
                metadata=entry.get("metadata", {}),
            )
            governor._estimates.append(estimate)
            if estimate.actual_cost is not None:
                governor._actual_spent += estimate.actual_cost
        return governor
