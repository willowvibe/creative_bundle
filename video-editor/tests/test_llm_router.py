"""Regression tests for the LLM router retry/backoff and guardrail reset.

These cover the remaining MEDIUM audit findings A44/A45:

* per-attempt budget accounting,
* exponential backoff with ``Retry-After`` for retryable HTTP errors,
* fail-fast for non-retryable 4xx errors,
* provider-level ``cost_per_million_tokens`` override,
* ``reset()`` boundary so guardrail state does not leak across jobs.
"""

from __future__ import annotations

import time

import httpx
import pytest
from video_editor_llm.config import LLMConfig
from video_editor_llm.router import CircuitBreakerOpenError, ModelRouter


def _http_error(status: int, retry_after: str | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.ollama.com/v1/chat/completions")
    headers: dict[str, str] = {}
    if retry_after is not None:
        headers["retry-after"] = retry_after
    response = httpx.Response(status, headers=headers, request=request)
    return httpx.HTTPStatusError("error", request=request, response=response)


class TestRouterRetryAndBackoff:
    """Retry classification, backoff, and fail-fast behaviour."""

    @pytest.mark.asyncio
    async def test_429_backoff_uses_retry_after_header(self) -> None:
        """A 429 response with Retry-After delays the next attempt."""
        config = LLMConfig(
            primary_attempts=1,
            fallback_attempts=1,
            retry_backoff_base_seconds=10.0,
            retry_backoff_max_seconds=30.0,
            retry_jitter=False,
        )
        router = ModelRouter(config=config)
        calls: list[str] = []

        async def fake_generate(model: str, **kwargs: object) -> dict[str, object]:
            calls.append(model)
            if model == "minimax-m3:cloud":
                raise _http_error(429, retry_after="0.2")
            return {"response": "ok"}

        router.client.generate = fake_generate  # type: ignore[method-assign,assignment]

        start = time.monotonic()
        result = await router.generate("plan", "hello")
        elapsed = time.monotonic() - start

        assert result == "ok"
        assert calls == ["minimax-m3:cloud", "glm-5.3:cloud"]
        # Retry-After 0.2s should dominate the configured 10s base.
        assert elapsed >= 0.18

    @pytest.mark.asyncio
    async def test_401_fails_fast_without_fallback(self) -> None:
        """Non-retryable 4xx errors should not burn fallback attempts."""
        config = LLMConfig(primary_attempts=1, fallback_attempts=2)
        router = ModelRouter(config=config)
        calls: list[str] = []

        async def fake_generate(model: str, **kwargs: object) -> dict[str, object]:
            calls.append(model)
            raise _http_error(401)

        router.client.generate = fake_generate  # type: ignore[method-assign,assignment]

        with pytest.raises(RuntimeError, match="LLM call failed"):
            await router.generate("plan", "hello")

        # Only the primary model was tried; fallback attempts were skipped.
        assert calls == ["minimax-m3:cloud"]

    @pytest.mark.asyncio
    async def test_500_retries_primary_with_exponential_backoff(self) -> None:
        """A 500 on the primary model is retried on the same model."""
        config = LLMConfig(
            primary_attempts=2,
            fallback_attempts=0,
            retry_backoff_base_seconds=0.1,
            retry_jitter=False,
        )
        router = ModelRouter(config=config)
        calls: list[str] = []

        async def fake_generate(model: str, **kwargs: object) -> dict[str, object]:
            calls.append(model)
            if len(calls) == 1:
                raise _http_error(500)
            return {"response": "ok"}

        router.client.generate = fake_generate  # type: ignore[method-assign,assignment]

        start = time.monotonic()
        result = await router.generate("plan", "hello")
        elapsed = time.monotonic() - start

        assert result == "ok"
        assert calls == ["minimax-m3:cloud", "minimax-m3:cloud"]
        # First retry backoff = base * 2**0 = 0.1s.
        assert elapsed >= 0.08


class TestRouterGuardrailReset:
    """Per-job guardrail boundary and provider-cost overrides."""

    @pytest.mark.asyncio
    async def test_reset_clears_guardrail_state(self) -> None:
        """``reset()`` closes the circuit and zeroes all counters."""
        config = LLMConfig(
            primary_attempts=1,
            fallback_attempts=0,
            circuit_breaker_failure_threshold=0,
        )
        router = ModelRouter(config=config)

        async def fail(model: str, **kwargs: object) -> dict[str, object]:  # noqa: ARG001
            raise RuntimeError("nope")

        router.client.generate = fail  # type: ignore[method-assign,assignment]

        with pytest.raises(RuntimeError, match="LLM call failed"):
            await router.generate("plan", "x")
        assert router._circuit_open is True  # noqa: SLF001
        assert router._consecutive_failures == 1  # noqa: SLF001

        router.reset()
        assert router._circuit_open is False  # noqa: SLF001
        assert router._consecutive_failures == 0  # noqa: SLF001
        assert router._attempts == 0  # noqa: SLF001
        assert router._tokens_used == 0  # noqa: SLF001
        assert router._estimated_cost_usd == 0.0  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_circuit_state_accumulates_across_calls(self) -> None:
        """Guardrails are per-router; failures from one call trip the next."""
        config = LLMConfig(
            primary_attempts=1,
            fallback_attempts=0,
            circuit_breaker_failure_threshold=0,
        )
        router = ModelRouter(config=config)

        async def fail(model: str, **kwargs: object) -> dict[str, object]:  # noqa: ARG001
            raise RuntimeError("nope")

        router.client.generate = fail  # type: ignore[method-assign,assignment]

        with pytest.raises(RuntimeError, match="LLM call failed"):
            await router.generate("plan", "x")

        with pytest.raises(CircuitBreakerOpenError):
            await router.generate("plan", "x")

    @pytest.mark.asyncio
    async def test_provider_cost_per_million_tokens_used_in_budget(self) -> None:
        """``cost_per_million_tokens`` on the provider overrides the hardcoded rate."""
        config = LLMConfig.model_validate(
            {
                "providers": {
                    "openrouter": {
                        "base_url": "https://openrouter.ai/api/v1",
                        "default_model": "big-model",
                        "cost_per_million_tokens": 1000.0,
                    }
                },
                "tasks": {"scene_plan": {"provider": "openrouter", "model": "big-model"}},
                "default_provider": "openrouter",
                "max_cost_usd_per_job": 0.0001,
            }
        )
        router = ModelRouter(config=config)

        async def succeed(model: str, **kwargs: object) -> dict[str, object]:  # noqa: ARG001
            return {"response": "ok"}

        router.client.generate = succeed  # type: ignore[method-assign,assignment]

        # A 100-character prompt is ~25 tokens; at $1000/1M tokens the
        # estimated cost of one attempt is ~$0.025, which exceeds the
        # $0.0001 budget before any HTTP call is made.
        with pytest.raises(Exception, match="cost budget exceeded"):
            await router.generate("scene_plan", "x" * 100)


class TestVisionRouting:
    @pytest.mark.asyncio
    async def test_critique_passes_images_to_the_client(self) -> None:
        router = ModelRouter(config=LLMConfig())
        seen: dict[str, object] = {}

        async def fake_generate(model: str, **kwargs: object) -> dict[str, object]:
            seen.update(kwargs)
            seen["model"] = model
            return {"response": "score"}

        router.client.generate = fake_generate  # type: ignore[method-assign,assignment]

        frames = [b"\xff\xd8jpeg"]
        assert await router.generate("critique", "score", images=frames) == "score"
        assert seen["images"] == frames
        assert seen["model"] == "glm-5.3-flash:cloud"
