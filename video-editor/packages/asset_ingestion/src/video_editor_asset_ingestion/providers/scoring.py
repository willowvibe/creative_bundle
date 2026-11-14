"""Scored provider selection for asset ingestion.

The selector turns a provider registry + project config into a ranked choice
for each task type (image, video, tts, music, sfx, etc.). It scores every
configured provider on cost, quality, latency, reliability, and API-key
availability, then returns the highest-scoring candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog
from video_editor_state.errors import VEProviderError

from video_editor_asset_ingestion.config import AssetProviderConfig, ProviderConfig

logger = structlog.get_logger(__name__)


@dataclass
class ProviderScoreCard:
    """Numeric profile for a provider on a single task type.

    Costs are normalized across candidates, so only relative magnitudes matter.
    Quality and reliability are 0..1. Latency is a rough typical seconds value.
    """

    cost: float = 1.0
    quality: float = 0.5
    latency: float = 10.0
    reliability: float = 0.8


@dataclass
class ProviderCriteria:
    """User- or template-driven scoring weights and optional floor.

    Weights are relative; they are normalized internally so callers can pass
    any non-negative scale. ``min_availability`` requires a configured API key
    unless set to False.

    When ``model`` is set, selection becomes *model-aware*: only providers that
    can serve that model (exact id or via ``models.yaml:model_equivalents``) are
    considered, and each is scored with its **real** per-model unit cost from
    the pricing registry instead of the static score card. This is how the same
    model ends up on whichever provider serves it cheapest.
    """

    cost_weight: float = 1.0
    quality_weight: float = 1.0
    latency_weight: float = 1.0
    reliability_weight: float = 1.0
    min_quality: float = 0.0
    require_api_key: bool = False
    model: str = ""


# Default score cards per task type and provider name. Values are relative
# estimates; projects can override them via config or a custom scoring table.
DEFAULT_SCORES: dict[str, dict[str, ProviderScoreCard]] = {
    "image": {
        # Free on the local CLI, but slow and occasionally flaky. The zero cost
        # matters most: it keeps ``agy_image`` ahead of the paid generators when
        # a chain is not configured and ``auto`` has to score the candidates.
        "agy_image": ProviderScoreCard(cost=0.0, quality=0.85, latency=60.0, reliability=0.75),
        # Free through the signed-in Flow web app; one image at a time with paced pauses.
        "flow_image": ProviderScoreCard(cost=0.0, quality=0.85, latency=90.0, reliability=0.7),
        "fal_ai": ProviderScoreCard(cost=0.03, quality=0.85, latency=5.0, reliability=0.9),
        "poyo_ai": ProviderScoreCard(cost=0.01, quality=0.80, latency=8.0, reliability=0.85),
        "replicate_image": ProviderScoreCard(
            cost=0.04, quality=0.78, latency=15.0, reliability=0.8
        ),
        "pexels": ProviderScoreCard(cost=0.001, quality=0.70, latency=2.0, reliability=0.95),
        "pixabay": ProviderScoreCard(cost=0.0, quality=0.68, latency=3.0, reliability=0.9),
        "coverr": ProviderScoreCard(cost=0.0, quality=0.65, latency=5.0, reliability=0.8),
        "mixkit": ProviderScoreCard(cost=0.0, quality=0.60, latency=5.0, reliability=0.8),
        "stub_image": ProviderScoreCard(cost=0.0, quality=0.5, latency=0.1, reliability=1.0),
    },
    "video": {
        "hyperframes_video": ProviderScoreCard(
            cost=0.02, quality=0.82, latency=20.0, reliability=0.85
        ),
        "poyo_ai": ProviderScoreCard(cost=0.015, quality=0.75, latency=25.0, reliability=0.8),
        "fal_ai": ProviderScoreCard(cost=0.05, quality=0.80, latency=30.0, reliability=0.8),
        "pexels": ProviderScoreCard(cost=0.001, quality=0.68, latency=3.0, reliability=0.95),
        "pixabay": ProviderScoreCard(cost=0.0, quality=0.66, latency=3.0, reliability=0.9),
        "coverr": ProviderScoreCard(cost=0.0, quality=0.65, latency=5.0, reliability=0.8),
        "mixkit": ProviderScoreCard(cost=0.0, quality=0.60, latency=5.0, reliability=0.8),
        "stub_hyperframes_video": ProviderScoreCard(
            cost=0.0, quality=0.5, latency=0.1, reliability=1.0
        ),
    },
    "tts": {
        "elevenlabs_tts": ProviderScoreCard(cost=0.18, quality=0.95, latency=2.0, reliability=0.95),
        "openai_tts": ProviderScoreCard(cost=0.015, quality=0.80, latency=1.5, reliability=0.9),
        "sarvam_tts": ProviderScoreCard(cost=0.005, quality=0.75, latency=1.5, reliability=0.85),
        "kokoro_tts": ProviderScoreCard(cost=0.0, quality=0.70, latency=1.0, reliability=0.95),
        "kitten_tts": ProviderScoreCard(cost=0.0, quality=0.55, latency=3.0, reliability=0.9),
        "moss_tts": ProviderScoreCard(cost=0.0, quality=0.65, latency=5.0, reliability=0.85),
        "stub_tts": ProviderScoreCard(cost=0.0, quality=0.5, latency=0.1, reliability=1.0),
    },
    "music": {
        "elevenlabs_music": ProviderScoreCard(
            cost=0.10, quality=0.90, latency=10.0, reliability=0.9
        ),
        "poyo_music": ProviderScoreCard(cost=0.015, quality=0.75, latency=15.0, reliability=0.8),
        "ace_step": ProviderScoreCard(cost=0.0, quality=0.75, latency=15.0, reliability=0.8),
        "stub_music": ProviderScoreCard(cost=0.0, quality=0.5, latency=0.1, reliability=1.0),
    },
    "sfx": {
        "elevenlabs_sfx": ProviderScoreCard(cost=0.08, quality=0.88, latency=5.0, reliability=0.9),
        "stub_sfx": ProviderScoreCard(cost=0.0, quality=0.5, latency=0.1, reliability=1.0),
    },
    "alignment": {
        "elevenlabs_forced_alignment": ProviderScoreCard(
            cost=0.0, quality=0.95, latency=2.0, reliability=0.95
        ),
        "stub_alignment": ProviderScoreCard(cost=0.0, quality=0.5, latency=0.1, reliability=1.0),
    },
    "isolation": {
        "elevenlabs_isolation": ProviderScoreCard(
            cost=0.0, quality=0.90, latency=3.0, reliability=0.9
        ),
        "stub_isolation": ProviderScoreCard(cost=0.0, quality=0.5, latency=0.1, reliability=1.0),
    },
    "speech_to_speech": {
        "elevenlabs_speech_to_speech": ProviderScoreCard(
            cost=0.15, quality=0.90, latency=4.0, reliability=0.9
        ),
        "stub_speech_to_speech": ProviderScoreCard(
            cost=0.0, quality=0.5, latency=0.1, reliability=1.0
        ),
    },
    "data_viz": {
        "matplotlib_data_viz": ProviderScoreCard(
            cost=0.0, quality=0.90, latency=1.0, reliability=1.0
        ),
    },
}


class ProviderSelectionError(VEProviderError):
    """Raised when no provider satisfies the requested criteria."""


@dataclass
class ScoredProvider:
    """A provider candidate with its computed score."""

    name: str
    config: ProviderConfig
    score: float
    details: dict[str, Any] = field(default_factory=dict)


class ScoredProviderSelector:
    """Pick a provider using weighted scoring across configured candidates."""

    def __init__(
        self,
        config: AssetProviderConfig,
        scores: dict[str, dict[str, ProviderScoreCard]] | None = None,
    ) -> None:
        self.config = config
        self.scores = scores if scores is not None else DEFAULT_SCORES

    def select(
        self,
        task_type: str,
        preferred: str | None = None,
        criteria: ProviderCriteria | None = None,
        *,
        eligible: set[str] | None = None,
    ) -> tuple[str, ProviderConfig]:
        """Return the best provider name and config for a task type.

        If ``preferred`` is provided and configured, it wins immediately. Otherwise
        every configured provider for the task type is scored and the highest
        scorer is chosen. ``criteria`` adjusts the relative weights.

        ``eligible`` optionally restricts the scored candidates to those names
        (used by ``"auto"`` image selection to exclude providers that fetch or
        copy rather than generate). Cost/latency normalization is computed over
        the eligible set, so restriction does not distort the scores.
        """
        if preferred is not None and preferred in self.config.providers:
            logger.debug("provider_preferred", task_type=task_type, provider=preferred)
            return preferred, self.config.providers[preferred]

        if preferred is not None:
            raise ProviderSelectionError(
                f"Preferred provider {preferred!r} is not configured for {task_type!r}"
            )

        candidates = self._candidates_for(task_type, criteria, eligible=eligible)
        if not candidates:
            raise ProviderSelectionError(
                f"No configured provider satisfies criteria for {task_type!r}"
            )

        crit = criteria or ProviderCriteria()
        if crit.model:
            # Model-aware mode is cost-first: when a model is pinned, choose the
            # provider that serves it cheapest, using the quality-weighted score
            # only to break cost ties.
            priced = [c for c in candidates if c.details.get("model_unit_cost") is not None]
            if priced:
                best = min(
                    priced,
                    key=lambda c: (c.details["model_unit_cost"], -c.score),
                )
                logger.debug(
                    "provider_selected_cheapest_model",
                    task_type=task_type,
                    provider=best.name,
                    model=crit.model,
                    unit_cost=best.details["model_unit_cost"],
                )
                return best.name, best.config

        best = max(candidates, key=lambda c: c.score)
        logger.debug(
            "provider_selected",
            task_type=task_type,
            provider=best.name,
            score=best.score,
        )
        return best.name, best.config

    def rank(
        self,
        task_type: str,
        criteria: ProviderCriteria | None = None,
    ) -> list[ScoredProvider]:
        """Return all configured providers ranked highest-score first."""
        return sorted(
            self._candidates_for(task_type, criteria),
            key=lambda c: c.score,
            reverse=True,
        )

    def _candidates_for(
        self,
        task_type: str,
        criteria: ProviderCriteria | None,
        *,
        eligible: set[str] | None = None,
    ) -> list[ScoredProvider]:
        crit = criteria or ProviderCriteria()
        task_scores = self.scores.get(task_type, {})

        # Model-aware mode: when a model is named, restrict to providers that
        # can serve it and replace the static cost card with the real per-model
        # unit cost from the pricing registry.
        registry = None
        offering: dict[str, str] = {}
        if crit.model:
            from video_editor_asset_ingestion.pricing import PricingRegistry

            registry = PricingRegistry.load()
            offering = registry.providers_for_model(crit.model)

        # Build score cards for every configured provider. Providers without a
        # default card get a neutral card so scoring never silently excludes
        # a configured provider.
        cards: dict[str, ProviderScoreCard] = {}
        model_costs: dict[str, float | None] = {}
        for name in self.config.providers:
            if eligible is not None and name not in eligible:
                continue
            card = task_scores.get(name, ProviderScoreCard())
            if registry is not None and crit.model:
                if offering and name not in offering:
                    # Provider cannot serve the requested model — drop it.
                    continue
                if name in offering:
                    real_cost = registry.unit_cost(name, offering[name], task_type)
                    model_costs[name] = real_cost
                    if real_cost is not None:
                        card = ProviderScoreCard(
                            cost=real_cost,
                            quality=card.quality,
                            latency=card.latency,
                            reliability=card.reliability,
                        )
            cards[name] = card

        costs = [card.cost for card in cards.values()]
        latencies = [card.latency for card in cards.values()]

        # Avoid division-by-zero when all values are identical.
        cost_span = max(costs) - min(costs) if costs else 1.0
        latency_span = max(latencies) - min(latencies) if latencies else 1.0
        if cost_span == 0:
            cost_span = 1.0
        if latency_span == 0:
            latency_span = 1.0

        weights = [
            crit.cost_weight,
            crit.quality_weight,
            crit.latency_weight,
            crit.reliability_weight,
        ]
        total_weight = sum(weights)
        if total_weight == 0:
            total_weight = 1.0

        candidates: list[ScoredProvider] = []
        for name, provider_cfg in self.config.providers.items():
            if name not in cards:
                continue
            card = cards[name]

            if card.quality < crit.min_quality:
                continue
            has_key = provider_cfg.api_key() not in (None, "")
            if crit.require_api_key and not has_key:
                continue

            # Normalize cost/latency so lower values score higher; quality and
            # reliability already increase with value.
            cost_score = 1.0 - ((card.cost - min(costs)) / cost_span)
            latency_score = 1.0 - ((card.latency - min(latencies)) / latency_span)

            # API-key availability gives a modest bonus but never dominates.
            availability_bonus = 0.1 if has_key else 0.0

            score = (
                (crit.cost_weight / total_weight) * cost_score
                + (crit.quality_weight / total_weight) * card.quality
                + (crit.latency_weight / total_weight) * latency_score
                + (crit.reliability_weight / total_weight) * card.reliability
                + availability_bonus
            )

            candidates.append(
                ScoredProvider(
                    name=name,
                    config=provider_cfg,
                    score=round(score, 4),
                    details={
                        "cost_score": round(cost_score, 4),
                        "latency_score": round(latency_score, 4),
                        "quality": card.quality,
                        "reliability": card.reliability,
                        "has_api_key": has_key,
                        "model": offering.get(name, crit.model) if crit.model else "",
                        "model_unit_cost": model_costs.get(name),
                    },
                )
            )

        return candidates
