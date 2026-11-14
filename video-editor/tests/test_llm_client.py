"""Regression tests for the Ollama LLM client."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from video_editor_llm.client import OllamaClient


async def _async_iter_lines(*lines: str) -> AsyncIterator[str]:
    for line in lines:
        yield line


def _make_aiter_lines(*lines: str) -> Any:
    async def aiter_lines() -> AsyncIterator[str]:
        for line in lines:
            yield line

    return aiter_lines


class TestGetClientConcurrency:
    """Bug #119 regression: concurrent first-use of ``_get_client`` must not
    leak multiple ``httpx.AsyncClient`` instances.
    """

    @pytest.mark.asyncio
    async def test_concurrent_get_client_creates_one_instance(self) -> None:
        """Many coroutines awaiting ``_get_client`` at once see a single client."""
        client = OllamaClient()
        created: list[MagicMock] = []

        def _track_new(*args: object, **kwargs: object) -> MagicMock:
            instance = MagicMock()
            instance.is_closed = False
            created.append(instance)
            return instance

        with patch("video_editor_llm.client.httpx.AsyncClient", side_effect=_track_new):
            await asyncio.gather(*[client._get_client() for _ in range(20)])

        assert len(created) == 1
        assert client._client is created[0]

    @pytest.mark.asyncio
    async def test_get_client_reuses_existing_instance(self) -> None:
        """Sequential calls after the first one reuse the same client."""
        client = OllamaClient()
        first = await client._get_client()
        second = await client._get_client()
        assert first is second
        await client.close()

    @pytest.mark.asyncio
    async def test_get_client_recreates_after_close(self) -> None:
        """After the client is closed, the next call creates a fresh instance."""
        client = OllamaClient()
        first = await client._get_client()
        await client.close()
        second = await client._get_client()
        assert first is not second
        assert first.is_closed
        assert not second.is_closed

    @pytest.mark.asyncio
    async def test_chat_posts_non_streaming_request(self) -> None:
        """``chat`` returns the parsed JSON response from a regular POST."""
        client = OllamaClient()
        fake_response = MagicMock()
        fake_response.json.return_value = {"choices": [{"message": {"content": "hello"}}]}
        fake_response.raise_for_status = MagicMock()
        fake_client = MagicMock()
        fake_client.post = AsyncMock(return_value=fake_response)
        fake_client.is_closed = False

        with patch.object(client, "_get_client", new=AsyncMock(return_value=fake_client)):
            result = await client.chat("llama3", [{"role": "user", "content": "hi"}])

        assert result["choices"][0]["message"]["content"] == "hello"
        _call_kwargs = fake_client.post.call_args.kwargs
        assert _call_kwargs["json"]["stream"] is False

    @pytest.mark.asyncio
    async def test_chat_stream_uses_http_streaming(self) -> None:
        """C27 regression: ``chat_stream`` uses ``httpx.stream`` and yields
        incremental JSON chunks, not a buffered response."""
        client = OllamaClient()
        fake_response = MagicMock()
        fake_response.aiter_lines = _make_aiter_lines(
            'data: {"choices":[{"delta":{"content":"hello"}}]}',
            "data: [DONE]",
        )
        fake_response.raise_for_status = MagicMock()
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(return_value=fake_response)
        stream_cm.__aexit__ = AsyncMock(return_value=False)
        fake_client = MagicMock()
        fake_client.stream = MagicMock(return_value=stream_cm)
        fake_client.is_closed = False

        with patch.object(client, "_get_client", new=AsyncMock(return_value=fake_client)):
            chunks = [
                chunk
                async for chunk in client.chat_stream("llama3", [{"role": "user", "content": "hi"}])
            ]

        assert len(chunks) == 1
        assert chunks[0]["choices"][0]["delta"]["content"] == "hello"
        fake_client.stream.assert_called_once()
        _call = fake_client.stream.call_args
        assert _call.args[0] == "POST"
        assert _call.args[1] == "/v1/chat/completions"
        assert _call.kwargs["json"]["stream"] is True
        await client.close()


@pytest.mark.asyncio
async def test_generate_with_images_uses_chat_and_data_urls() -> None:
    """A vision call goes to the OpenAI-compatible chat endpoint with a PNG part."""
    client = OllamaClient(api_key="test")
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={"choices": [{"message": {"content": "rubric ok"}}]})
    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=response)
    fake_client.is_closed = False

    with patch.object(client, "_get_client", new=AsyncMock(return_value=fake_client)):
        result = await client.generate(
            "google/gemini-3.8-flash",
            "score this contact sheet",
            images=[b"\x89PNG\r\n\x1a\nrest"],
        )

    assert result["response"] == "rubric ok"
    call = fake_client.post.call_args
    assert call.args[0] == "/v1/chat/completions"
    content = call.kwargs["json"]["messages"][-1]["content"]
    assert content[0] == {"type": "text", "text": "score this contact sheet"}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    await client.close()
