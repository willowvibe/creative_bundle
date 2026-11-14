"""TypeSafe/Jev classifier client — calibrated, batched per-item judgments.

Jev is a classifier, not a chat model: you send one ``state`` plus a map of
typed ``questions`` (``noul`` / ``choice`` / ``score``) and get a probability
distribution per question in a single sub-second call. It complements
``ModelRouter``: use it for the many independent per-item judgments skills
currently do with a whole-prompt LLM (moment scoring, checklist judging,
low-value detection), and keep the LLM for generation.

Endpoint: ``POST https://api.typesafe.ai/v1/systemone`` (see
https://docs.typesafe.ai/api). Key from ``TYPESAFE_API_KEY``.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, cast

import httpx
import structlog
from video_editor_state.errors import VEProviderError

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"

Question = dict[str, Any]


def run_sync[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run ``coro`` from sync code, even when an event loop is already running.

    Skills' ``run()``/``analyze()`` methods are synchronous and may be invoked
    from inside an async MCP handler where ``asyncio.run`` would raise. In that
    case run the coroutine to completion in a worker thread with its own loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    box: dict[str, T] = {}
    errors: dict[str, BaseException] = {}

    def _worker() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            errors["exc"] = exc

    thread = threading.Thread(target=_worker)
    thread.start()
    thread.join()
    if errors:
        raise errors["exc"]
    return box["value"]


def question_noul(
    instruction: str,
    *,
    data: dict[str, Any] | None = None,
    true_label: str | None = None,
    false_label: str | None = None,
) -> Question:
    """Build a yes/no question. Extra ``data`` fields are referenced from
    ``instruction`` in backticks (see the Jev docs)."""
    instructions: Any = instruction
    if data:
        instructions = {**data, "question": instruction}
    q: Question = {"type": "noul", "instructions": instructions}
    if true_label or false_label:
        q["criteria"] = {}
        if true_label:
            q["criteria"]["true"] = true_label
        if false_label:
            q["criteria"]["false"] = false_label
    return q


def question_choice(
    instruction: str,
    criteria: dict[str, Any],
    *,
    data: dict[str, Any] | None = None,
) -> Question:
    """Build a single-choice question; ``criteria`` maps option -> rubric."""
    instructions: Any = instruction
    if data:
        instructions = {**data, "question": instruction}
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def question_score(
    instruction: str,
    levels: list[str],
    *,
    data: dict[str, Any] | None = None,
) -> Question:
    """Build an ordered rating question (2-10 levels)."""
    instructions: Any = instruction
    if data:
        instructions = {**data, "question": instruction}
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


class ClassifyError(VEProviderError):
    """Raised when a classify call cannot be made or the API errors."""


class ClassifyClient:
    """Async TypeSafe/Jev client. Create once per job (like ``ModelRouter``)."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = (base_url or os.getenv("TYPESAFE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or os.getenv("TYPESAFE_API_KEY")
        self.model = model or os.getenv("TYPESAFE_MODEL") or DEFAULT_MODEL
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    @classmethod
    def from_config(cls, path: Path | str | None = None) -> ClassifyClient:
        """Build from ``config.yaml``: the ``classify`` task's provider (or a
        provider named ``typesafe``), falling back to env defaults."""
        from video_editor_config import Settings

        config = Settings.load(path).config.llm
        provider_name, model = config.provider_for_task("classify")
        provider = config.providers.get(provider_name) if provider_name else None
        kwargs = config.client_kwargs(provider_name) if provider else {}
        default_model = provider.default_model if provider else None
        return cls(
            base_url=kwargs.get("base_url"),
            api_key=kwargs.get("api_key"),
            model=model or default_model or None,
        )

    def available(self) -> bool:
        return bool(self.api_key)

    async def _get_client(self) -> httpx.AsyncClient:
        async with self._lock:
            if self._client is None or self._client.is_closed:
                headers = {"Content-Type": "application/json"}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                self._client = httpx.AsyncClient(
                    base_url=self.base_url, headers=headers, timeout=self.timeout
                )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> ClassifyClient:
        await self._get_client()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def classify(
        self,
        state: Any,
        questions: dict[str, Question],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Evaluate ``state`` against ``questions``; returns the parsed body
        (``{"model", "answers", "usage"}``). Raises ``ClassifyError``."""
        if not self.api_key:
            raise ClassifyError("TYPESAFE_API_KEY is not set")
        if not isinstance(questions, dict) or not questions:
            raise ClassifyError("questions must be a non-empty map")
        payload = {"model": self.model, "state": state, "questions": questions}
        client = await self._get_client()
        try:
            response = await client.post(
                "/v1/systemone", json=payload, timeout=timeout or self.timeout
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise ClassifyError(
                f"HTTP {exc.response.status_code}: {exc.response.text[:300]}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - map any transport error to one type
            raise ClassifyError(f"{type(exc).__name__}: {exc}") from exc
        logger.info("classify_call", model=data.get("model"), questions=len(questions))
        return cast("dict[str, Any]", data)

    async def classify_batch(
        self,
        items: list[Any],
        build: Any,
        *,
        batch_size: int = 40,
        budget_chars: int = 24_000,
    ) -> list[dict[str, Any]]:
        """Judge every item with its own question, in as few requests as
        possible. ``build(item) -> Question``. Every item in a chunk is judged
        in ONE call; returns the per-item Answer dicts aligned to ``items``
        (``{}`` where the API returned nothing)."""
        answers: list[dict[str, Any]] = [{} for _ in items]
        i = 0
        while i < len(items):
            questions: dict[str, Question] = {}
            budget = 0
            start = i
            while i < len(items) and len(questions) < batch_size:
                question = build(items[i])
                cost = len(json.dumps(question))
                if questions and budget + cost > budget_chars:
                    break
                questions[f"p{i - start}"] = question
                budget += cost
                i += 1
            body = await self.classify({"state": "Batch evaluation."}, questions)
            got = body.get("answers") or {}
            for j in range(len(questions)):
                answers[start + j] = got.get(f"p{j}") or {}
        return answers
