"""Model router mapping tasks to providers and models with guardrails."""

from __future__ import annotations

import asyncio
import contextlib
import random
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import httpx
import structlog
from pydantic import BaseModel
from video_editor_state.errors import VEProviderError

from video_editor_llm.client import OllamaClient
from video_editor_llm.config import LLMConfig

logger = structlog.get_logger(__name__)

_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


class RoutedRequest(BaseModel):
    """A request annotated with the intended model role."""

    task_type: str
    model: str
    prompt: str
    system: str | None = None
    options: dict[str, Any] | None = None


class LLMBudgetExceededError(RuntimeError):
    """Raised when a job exceeds its configured LLM budget."""


class CircuitBreakerOpenError(VEProviderError):
    """Raised when the LLM circuit breaker trips."""


class LLMProviderError(RuntimeError):
    """Raised when an LLM response is malformed or unusable."""


class ModelRouter:
    """Route prompts to the right provider/model and enforce cost guardrails."""

    TASK_MODELS: dict[str, str] = {
        "plan": "minimax-m3:cloud",
        "decompose": "minimax-m3:cloud",
        "edit_plan": "minimax-m3:cloud",
        "scene_plan": "minimax-m3:cloud",
        "fallback": "glm-5.3:cloud",
        "code": "kimi-k2.7-code:cloud",
        "utility": "glm-5.3-flash:cloud",
        "verify_judge": "glm-5.3-flash:cloud",
        "critique": "glm-5.3-flash:cloud",
    }

    def __init__(
        self,
        client: OllamaClient | None = None,
        config: LLMConfig | None = None,
        log_callback: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.client = client or OllamaClient()
        self.config = config or LLMConfig()
        self.log_callback = log_callback

        # Guardrail state (per-router == per-job when callers create one router per job).
        self._attempts = 0
        self._consecutive_failures = 0
        self._circuit_open = False
        self._tokens_used = 0
        self._estimated_cost_usd = 0.0

    @classmethod
    def from_config(
        cls,
        path: Path | str | None = None,
        log_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> ModelRouter:
        """Create a router from a project-level config.yaml."""
        from video_editor_config import Settings

        config = Settings.load(path).config.llm
        client_kwargs = config.client_kwargs(config.default_provider)
        return cls(client=OllamaClient(**client_kwargs), config=config, log_callback=log_callback)

    def select(self, task_type: str) -> str:
        """Return the model identifier for a task type."""
        provider_name, model = self.config.provider_for_task(task_type)
        if model:
            return model
        return self.TASK_MODELS.get(task_type, self.TASK_MODELS["fallback"])

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token estimate for guardrail accounting."""
        return max(1, len(text) // 4)

    def _estimate_cost_usd(self, tokens: int, model: str, task_type: str = "") -> float:
        """Very rough cost estimate used only for budget guardrails."""
        # Prefer an explicit per-provider cost from config, then fall back to
        # the built-in Ollama Cloud Pro approximate rates.
        provider_name, _ = self.config.provider_for_task(task_type)
        provider = self.config.providers.get(provider_name)
        if provider is not None and provider.cost_per_million_tokens is not None:
            rate = provider.cost_per_million_tokens
        else:
            # Blended (input+output)/2 rates per 1M tokens, refreshed from
            # https://ollama.com/pricing on 2026-10-15. Conservative enough for
            # the guardrail; override per provider with ``cost_per_million_tokens``.
            # Blended (input+output)/2 rates per 1M tokens. Ollama rates from
            # https://ollama.com/pricing; OpenRouter from https://openrouter.ai.
            # Conservative enough for the guardrail; override per provider with
            # ``cost_per_million_tokens``.
            per_million: dict[str, float] = {
                # Ollama Cloud Pro (free-mode routing)
                "minimax-m3:cloud": 1.5,
                "kimi-k2.7-code:cloud": 2.5,
                "glm-5.3:cloud": 2.9,
                "glm-5.3-flash:cloud": 0.33,
                # OpenRouter (default config.yaml routing)
                "anthropic/claude-sonnet-5.5": 6.0,
                "google/gemini-3.8-flash": 2.25,
            }
            rate = per_million.get(model, 2.0)
        return tokens * rate / 1_000_000

    def _check_budget(self, estimated_tokens: int, model: str, task_type: str = "") -> None:
        """Raise if the job would exceed token or cost guardrails."""
        if (
            self.config.max_tokens_per_job is not None
            and self._tokens_used + estimated_tokens > self.config.max_tokens_per_job
        ):
            raise LLMBudgetExceededError(
                f"LLM token budget exceeded: {self._tokens_used + estimated_tokens} > "
                f"{self.config.max_tokens_per_job}"
            )
        if self.config.max_cost_usd_per_job is not None:
            estimated_cost = self._estimated_cost_usd + self._estimate_cost_usd(
                estimated_tokens, model, task_type
            )
            if estimated_cost > self.config.max_cost_usd_per_job:
                raise LLMBudgetExceededError(
                    f"LLM cost budget exceeded: ${estimated_cost:.4f} > "
                    f"${self.config.max_cost_usd_per_job:.4f}"
                )

    def _check_circuit(self) -> None:
        """Raise if the circuit breaker is open.

        The breaker opens AFTER ``circuit_breaker_failure_threshold``
        consecutive failures — i.e. on the (threshold + 1)-th failure. With
        the default ``threshold=5`` we tolerate five back-to-back failures
        and only short-circuit on the sixth. This is the standard
        "open after N failures" semantics and prevents the breaker from
        tripping on a single transient error plus one fallback attempt.
        """
        if self._circuit_open:
            raise CircuitBreakerOpenError(
                "LLM circuit breaker is open after "
                f"{self.config.circuit_breaker_failure_threshold} consecutive failures"
            )

    def reset(self) -> None:
        """Reset all per-job guardrail counters and close the circuit.

        ``ModelRouter`` instances are intended to be created once per job. If
        a single instance is reused across jobs, call ``reset()`` between jobs
        so attempt/failure/cost state from the previous job does not leak
        into the next one.
        """
        self._attempts = 0
        self._consecutive_failures = 0
        self._circuit_open = False
        self._tokens_used = 0
        self._estimated_cost_usd = 0.0

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Return True when ``exc`` represents a transient HTTP error worth retrying."""
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in _RETRYABLE_STATUS_CODES
        return isinstance(
            exc,
            (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError),
        )

    @staticmethod
    def _is_terminal_http_error(exc: Exception) -> bool:
        """Return True when ``exc`` is a non-retryable HTTP client error.

        4xx errors (other than 429) are caller-side or request problems and
        should fail fast without burning fallback attempts.
        """
        return (
            isinstance(exc, httpx.HTTPStatusError)
            and exc.response.status_code not in _RETRYABLE_STATUS_CODES
        )

    def _backoff_seconds(self, attempt: int, exc: Exception) -> float:
        """Compute the delay before retry attempt ``attempt`` (0-indexed).

        Honors a provider's ``Retry-After`` header for 429 responses and
        otherwise uses exponential backoff with optional jitter. Non-retryable
        or non-HTTP errors return 0 so generic failures still fall through to
        the fallback model without unnecessary delay.
        """
        if not self._is_retryable(exc):
            return 0.0

        delay: float = self.config.retry_backoff_base_seconds * (2**attempt)

        if isinstance(exc, httpx.HTTPStatusError):
            retry_after = exc.response.headers.get("retry-after")
            if retry_after:
                with contextlib.suppress(ValueError):
                    delay = float(retry_after)

        if self.config.retry_jitter:
            delay *= 1.0 + random.random()

        return min(delay, self.config.retry_backoff_max_seconds)

    def _record_attempt(self, task_type: str, model: str, prompt: str) -> int:
        """Account for an attempt and return its token estimate."""
        self._attempts += 1
        if self._attempts > self.config.max_llm_attempts_per_job:
            raise LLMBudgetExceededError(
                f"LLM attempt budget exceeded: {self._attempts} > "
                f"{self.config.max_llm_attempts_per_job}"
            )
        estimated_tokens = self._estimate_tokens(prompt)
        self._check_budget(estimated_tokens, model, task_type)
        return estimated_tokens

    def _record_success(self, task_type: str, model: str, tokens_used: int) -> None:
        """Reset failure streak and account for actual-ish token usage."""
        self._consecutive_failures = 0
        self._tokens_used += tokens_used
        self._estimated_cost_usd += self._estimate_cost_usd(tokens_used, model, task_type)
        logger.debug(
            "llm_call_accounted",
            task_type=task_type,
            model=model,
            tokens_used=tokens_used,
            total_tokens=self._tokens_used,
        )

    def _record_failure(self, task_type: str, model: str, error: str) -> None:
        """Increment failure streak and trip the circuit breaker if needed.

        The breaker opens AFTER ``circuit_breaker_failure_threshold``
        consecutive failures (strict ``>``), so the default ``threshold=5``
        allows five failures before tripping on the sixth.
        """
        self._consecutive_failures += 1
        logger.warning(
            "llm_call_failed",
            task_type=task_type,
            model=model,
            error=error,
            consecutive_failures=self._consecutive_failures,
        )
        if self._consecutive_failures > self.config.circuit_breaker_failure_threshold:
            self._circuit_open = True

    def _emit_log(
        self,
        task_type: str,
        model: str,
        status: str,
        error: str | None = None,
        tokens_used: int = 0,
        attempt: int = 0,
    ) -> None:
        """Emit a structured operation record to the optional callback."""
        if not self.log_callback:
            return
        try:
            self.log_callback(
                {
                    "operation": "llm_call",
                    "inputs": {
                        "task_type": task_type,
                        "model": model,
                        "attempt": attempt,
                    },
                    "outputs": {
                        "status": status,
                        "error": error,
                        "tokens_used": tokens_used,
                        "total_attempts": self._attempts,
                        "total_tokens": self._tokens_used,
                        "circuit_open": self._circuit_open,
                    },
                }
            )
        except Exception:  # noqa: BLE001 - try the next model in the fallback chain
            logger.warning("llm_log_callback_failed", task_type=task_type, model=model)

    async def _with_fallback(
        self,
        task_type: str,
        primary_model: str,
        call: Callable[[str], Awaitable[dict[str, Any]]],
        extract_text: Callable[[dict[str, Any]], str],
        fallback: bool = True,
    ) -> str:
        """Shared retry/fallback loop for ``generate()`` and ``chat()``.

        Tries the primary model ``primary_attempts`` times; on persistent
        failure, switches to the resolved fallback model and retries
        ``fallback_attempts`` times. Resets the consecutive-failure streak
        on any success (including a successful fallback) so a single
        fallback doesn't poison the rest of the job.

        Per-attempt accounting: every actual HTTP invocation is counted by
        ``_record_attempt``. Retryable HTTP errors (429/5xx/network) sleep via
        exponential backoff (honoring ``Retry-After``). Non-retryable 4xx
        errors fail fast without burning fallback attempts.
        """
        self._check_circuit()

        # Resolve fallback model from config rather than hardcoding the
        # TASK_MODELS fallback entry, so users can route the fallback task
        # to a different provider in config.yaml.
        fallback_model = self._resolve_fallback_model(task_type)

        prompt_for_budget = getattr(call, "_ve_prompt", "")

        max_total = self.config.primary_attempts + (
            self.config.fallback_attempts if fallback else 0
        )

        last_error = ""
        last_exc: Exception | None = None
        for attempt in range(max_total):
            using_fallback = attempt >= self.config.primary_attempts
            model = fallback_model if using_fallback else primary_model

            # Every actual HTTP attempt is counted against the job budget.
            self._record_attempt(task_type, model, prompt_for_budget)

            # Back off before retrying a prior failure (not before the first
            # attempt). Generic failures skip the delay; retryable HTTP errors
            # honor Retry-After / exponential backoff.
            if attempt > 0 and last_exc is not None:
                delay = self._backoff_seconds(attempt - 1, last_exc)
                if delay > 0:
                    await asyncio.sleep(delay)

            try:
                result = await call(model)
            except Exception as exc:  # noqa: BLE001 - try the next model in the fallback chain
                last_exc = exc
                last_error = str(exc)
                retryable = self._is_retryable(exc)
                self._record_failure(task_type, model, last_error)
                self._emit_log(
                    task_type,
                    model,
                    status="failure",
                    error=last_error,
                    attempt=attempt,
                    tokens_used=0,
                )

                # Non-retryable HTTP client errors fail fast.
                if self._is_terminal_http_error(exc):
                    break

                if using_fallback and self._circuit_open:
                    # We just tripped the breaker — don't keep hammering.
                    break
                # Check the breaker before the next iteration. We swallow
                # CircuitBreakerOpenError so the outer loop can still
                # surface a clear final error message.
                try:
                    self._check_circuit()
                except CircuitBreakerOpenError:
                    break

                # Retryable errors get another attempt (with backoff above).
                # Non-retryable, non-HTTP exceptions still try the fallback
                # model without delay.
                if not retryable and attempt == max_total - 1:
                    break
                continue

            try:
                response_text = extract_text(result)
            except Exception as exc:  # noqa: BLE001 - try the next model in the fallback chain
                last_exc = exc
                last_error = str(exc)
                retryable = self._is_retryable(exc)
                self._record_failure(task_type, model, last_error)
                self._emit_log(
                    task_type,
                    model,
                    status="failure",
                    error=last_error,
                    attempt=attempt,
                    tokens_used=0,
                )
                if self._is_terminal_http_error(exc):
                    break
                if using_fallback and self._circuit_open:
                    break
                try:
                    self._check_circuit()
                except CircuitBreakerOpenError:
                    break
                if not retryable and attempt == max_total - 1:
                    break
                continue

            actual_tokens = self._estimate_tokens(prompt_for_budget + response_text)
            self._record_success(task_type, model, actual_tokens)
            self._emit_log(
                task_type,
                model,
                status="success",
                tokens_used=actual_tokens,
                attempt=attempt,
            )
            return response_text

        final_error = RuntimeError(
            f"LLM call failed after {max_total} attempt(s) for task {task_type!r}: {last_error}"
        )
        if last_exc is not None:
            raise final_error from last_exc
        raise final_error

    def _extract_chat_text(self, response: dict[str, Any]) -> str:
        """Return the assistant message content from a chat response.

        Raises ``LLMProviderError`` when the response has no usable choices.
        """
        choices = response.get("choices") or []
        if not choices:
            raise LLMProviderError("empty choices in chat response")
        content = choices[0].get("message", {}).get("content", "")
        if not content:
            raise LLMProviderError("chat response has no content")
        return cast(str, content)

    def _resolve_fallback_model(self, task_type: str) -> str:
        """Return the model name to use as a fallback for ``task_type``."""
        _, model = self.config.provider_for_task("fallback")
        if model:
            return model
        return self.TASK_MODELS["fallback"]

    def _list_ollama_models(self) -> set[str]:
        """Return the set of model names available on the configured Ollama host.

        Returns an empty set on any error so callers degrade to "skip the check".
        """
        base_url = (
            (self.config or {}).get("base_url", "https://api.ollama.com")
            if isinstance(self.config, dict)
            else "https://api.ollama.com"
        )
        try:
            import httpx

            response = httpx.get(f"{base_url}/api/tags", timeout=5.0)
            response.raise_for_status()
            data = response.json()
            return {m["name"] for m in data.get("models", []) if "name" in m}
        except Exception:  # noqa: BLE001
            return set()

    def _validate_model_known(self, model_name: str) -> None:
        """Log a warning if `model_name` is not in the live ollama list.

        Soft warning only — does not raise. This is a smoke check, not a gate.
        """
        import structlog

        logger = structlog.get_logger(__name__)
        available = self._list_ollama_models()
        if available and model_name not in available:
            logger.warning(
                "model_not_in_ollama_list",
                model=model_name,
                hint="run `ollama list` to see installed models",
            )

    async def generate(
        self,
        task_type: str,
        prompt: str,
        system: str | None = None,
        format: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
        fallback: bool = True,
        images: list[bytes] | None = None,
    ) -> str:
        """Generate text for a task, enforcing guardrails and fallback retries.

        ``images`` turns the request into a vision call (base64 data URLs on the
        OpenAI-compatible chat endpoint); the ``critique`` task routes to the
        configured vision model.
        """
        model = self.select(task_type)

        async def call(m: str) -> dict[str, Any]:
            return await self.client.generate(
                model=m,
                prompt=prompt,
                system=system,
                format=format,
                options=options,
                images=images,
            )

        # Stash the prompt for the budget estimator inside _with_fallback.
        call._ve_prompt = prompt  # type: ignore[attr-defined]
        return await self._with_fallback(
            task_type=task_type,
            primary_model=model,
            call=call,
            extract_text=lambda r: cast(str, r.get("response", "")),
            fallback=fallback,
        )

    async def chat(
        self,
        task_type: str,
        messages: list[dict[str, str]],
        options: dict[str, Any] | None = None,
        fallback: bool = True,
    ) -> str:
        """Chat completion for a task with guardrails and the same
        primary→fallback retry loop as ``generate()``."""
        model = self.select(task_type)
        prompt_text = "\n".join(m.get("content", "") for m in messages)

        async def call(m: str) -> dict[str, Any]:
            result = await self.client.chat(model=m, messages=messages, options=options)
            if not isinstance(result, dict):
                raise TypeError("Streaming chat results are not supported by ModelRouter")
            return result

        call._ve_prompt = prompt_text  # type: ignore[attr-defined]
        return await self._with_fallback(
            task_type=task_type,
            primary_model=model,
            call=call,
            extract_text=lambda r: self._extract_chat_text(r),
            fallback=fallback,
        )
