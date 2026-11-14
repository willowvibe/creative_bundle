"""Ollama Cloud Pro client."""

from __future__ import annotations

import asyncio
import base64
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.ollama.com"


def _image_data_url(image: bytes) -> str:
    """Detect the common frame formats and return a data URL for chat input."""
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif image.startswith(b"\xff\xd8"):
        mime = "image/jpeg"
    elif image.startswith(b"RIFF") and image[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"


class OllamaClient:
    """Async HTTP client for Ollama Cloud Pro."""

    def __init__(
        self, base_url: str | None = None, api_key: str | None = None, timeout: float = 120.0
    ):
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or os.getenv("OLLAMA_API_KEY")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        async with self._lock:
            if self._client is None or self._client.is_closed:
                headers: dict[str, str] = {"Content-Type": "application/json"}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                self._client = httpx.AsyncClient(
                    base_url=self.base_url,
                    headers=headers,
                    timeout=self.timeout,
                )
        return self._client

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a non-streaming chat completion request."""
        client = await self._get_client()
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": options or {},
        }
        logger.info("ollama_chat_request", model=model, messages=len(messages))
        response = await client.post("/v1/chat/completions", json=payload)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data

    async def chat_stream(
        self,
        model: str,
        messages: list[dict[str, str]],
        options: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Send a streaming chat completion request and yield JSON deltas.

        Uses ``httpx.AsyncClient.stream`` so response bytes are delivered
        incrementally. Each yielded chunk is one server-sent event payload
        (with ``data: `` prefix and ``[DONE]`` terminator stripped).
        """
        client = await self._get_client()
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": options or {},
        }
        logger.info("ollama_chat_stream_request", model=model, messages=len(messages))
        async with client.stream("POST", "/v1/chat/completions", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                # Ollama/compatible endpoints prefix streaming JSON lines with
                # "data: "; strip it if present.
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    break
                try:
                    yield __import__("json").loads(line)
                except ValueError:
                    logger.warning("ollama_stream_unparseable_line", line=line[:200])

    async def generate(
        self,
        model: str,
        prompt: str,
        system: str | None = None,
        format: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
        images: list[bytes] | None = None,
    ) -> dict[str, Any]:
        """Send a generate request.

        With ``images`` this uses the OpenAI-compatible chat endpoint (which is
        what OpenRouter's vision models accept) and returns the same
        ``{"response": text}`` shape as the text-only path.
        """
        if images:
            content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
            content.extend(
                {"type": "image_url", "image_url": {"url": _image_data_url(image)}}
                for image in images
            )
            messages: list[dict[str, Any]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": content})
            logger.info(
                "ollama_vision_request",
                model=model,
                prompt_len=len(prompt),
                images=len(images),
            )
            chat_data = await self.chat(model, messages, options=options)
            text = ""
            choices = chat_data.get("choices") or []
            if choices:
                text = str((choices[0].get("message") or {}).get("content") or "")
            return {**chat_data, "response": text}

        client = await self._get_client()
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": options or {},
        }
        if system:
            payload["system"] = system
        if format:
            payload["format"] = format
        logger.info("ollama_generate_request", model=model, prompt_len=len(prompt))
        response = await client.post("/api/generate", json=payload)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> OllamaClient:
        await self._get_client()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
