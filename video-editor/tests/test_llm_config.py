"""Tests for project-level LLM provider configuration."""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_llm.config import LLMConfig
from video_editor_llm.router import LLMProviderError, ModelRouter


def _write_config(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_load_empty_config(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    _write_config(path, "")
    config = LLMConfig.load(path)
    assert config.providers == {}
    assert config.tasks == {}


def test_provider_for_task_uses_routing(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    _write_config(
        path,
        """
providers:
  openrouter:
    base_url: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY
    default_model: openai/gpt-4o
  ollama:
    base_url: https://api.ollama.com
    api_key_env: OLLAMA_API_KEY
    default_model: qwen3.6:cloud
default_provider: ollama
tasks:
  edit_plan:
    provider: openrouter
    model: anthropic/claude-3.5-sonnet
""",
    )
    config = LLMConfig.load(path)
    provider, model = config.provider_for_task("edit_plan")
    assert provider == "openrouter"
    assert model == "anthropic/claude-3.5-sonnet"


def test_provider_for_task_falls_back_to_default_provider_model(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    _write_config(
        path,
        """
providers:
  ollama:
    base_url: https://api.ollama.com
    default_model: glm-5.1:cloud
default_provider: ollama
""",
    )
    config = LLMConfig.load(path)
    provider, model = config.provider_for_task("unknown_task")
    assert provider == "ollama"
    assert model == "glm-5.1:cloud"


def test_router_select_uses_config_model(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    _write_config(
        path,
        """
providers:
  openrouter:
    base_url: https://openrouter.ai/api/v1
    default_model: openai/gpt-4o
tasks:
  edit_plan:
    provider: openrouter
    model: anthropic/claude-3.5-sonnet
""",
    )
    router = ModelRouter.from_config(path)
    assert router.select("edit_plan") == "anthropic/claude-3.5-sonnet"


def test_router_select_falls_back_to_default_models() -> None:
    router = ModelRouter()
    assert router.select("edit_plan") == "minimax-m3:cloud"
    assert router.select("code") == "kimi-k2.7-code:cloud"
    assert router.select("unknown") == "glm-5.3:cloud"


def test_client_kwargs_reads_api_key_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_API_KEY", "secret-key")
    path = tmp_path / "config.yaml"
    _write_config(
        path,
        """
providers:
  ollama:
    base_url: https://api.ollama.com
    api_key_env: TEST_API_KEY
    default_model: kimi-k2.6:cloud
""",
    )
    config = LLMConfig.load(path)
    kwargs = config.client_kwargs("ollama")
    assert kwargs["base_url"] == "https://api.ollama.com"
    assert kwargs["api_key"] == "secret-key"


def test_config_load_from_env_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "from_env.yaml"
    _write_config(path, "default_provider: ollama\n")
    monkeypatch.setenv("VIDEO_EDITOR_CONFIG", str(path))
    config = LLMConfig.load()
    assert config.default_provider == "ollama"


def test_config_load_from_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path / "config.yaml", "default_provider: openrouter\n")
    config = LLMConfig.load()
    assert config.default_provider == "openrouter"


def test_load_reads_llm_section_when_present(tmp_path: Path) -> None:
    """Session S regression: the sectioned ``llm:`` form must work.

    Pre-fix, ``LLMConfig.load`` validated the full YAML, so the
    ``llm:`` block was silently dropped (LLMConfig has no
    ``llm`` field). This made the project's own ``config.yaml``
    ``llm:`` section a no-op for ``ve plan-script`` and friends —
    they always fell through to the LLMClient's env-based defaults.

    The sectioned form is the convention used by the rest of
    ``config.yaml`` (``assets:``, ``render:``, ``transcription:``,
    etc.) and is now the preferred form.
    """
    path = tmp_path / "config.yaml"
    _write_config(
        path,
        """
llm:
  default_provider: ollama
  providers:
    ollama:
      base_url: http://localhost:11434
      default_model: kimi-k2.7-code:cloud
  tasks:
    scene_plan:
      provider: ollama
      model: kimi-k2.7-code:cloud
""",
    )
    config = LLMConfig.load(path)
    assert config.default_provider == "ollama"
    assert list(config.providers.keys()) == ["ollama"]
    assert config.providers["ollama"].base_url == "http://localhost:11434"
    # client_kwargs must return the section's base_url, not the
    # LLMClient's env-based default.
    assert config.client_kwargs(config.default_provider) == {
        "base_url": "http://localhost:11434",
        "api_key": None,
    }
    # The LLM section's tasks must be honored (not silently dropped).
    assert config.provider_for_task("scene_plan") == (
        "ollama",
        "kimi-k2.7-code:cloud",
    )


def test_load_sectioned_form_does_not_pollute_from_other_sections(tmp_path: Path) -> None:
    """The ``llm:`` extractor must not pull in sibling sections like
    ``assets:`` or ``transcription:`` — they belong to other packages.
    """
    path = tmp_path / "config.yaml"
    _write_config(
        path,
        """
llm:
  default_provider: ollama
  providers:
    ollama:
      base_url: http://localhost:11434
assets:
  providers:
    fal_ai:
      api_key_env: FAL_KEY
transcription:
  engine: whisperx
""",
    )
    config = LLMConfig.load(path)
    # Only the LLM section's providers make it into the LLMConfig.
    assert list(config.providers.keys()) == ["ollama"]


class TestRouterRetryAndCircuitBreaker:
    """Regression tests for the router's primary/fallback loop, chat() retries,
    and circuit-breaker threshold semantics (Session B: bugs #7, #8, #13)."""

    @pytest.mark.asyncio
    async def test_generate_falls_back_to_fallback_model_after_primary_fails(self) -> None:
        """Bug #13 regression: with primary_attempts=1 and fallback_attempts=2,
        a single primary failure should NOT abort — we should switch to the
        fallback model and retry up to 2 times.
        """
        from video_editor_llm.config import LLMConfig

        config = LLMConfig(primary_attempts=1, fallback_attempts=2)
        router = ModelRouter(config=config)

        calls: list[str] = []

        async def fake_generate(model: str, **kwargs: object) -> dict[str, object]:
            calls.append(model)
            if model == "minimax-m3:cloud":
                raise RuntimeError("primary down")
            return {"response": "ok"}

        router.client.generate = fake_generate  # type: ignore[method-assign,assignment]
        result = await router.generate("plan", "hello")
        assert result == "ok"
        # 1 primary attempt + 1 fallback attempt (we succeed on the 1st fallback)
        assert calls == ["minimax-m3:cloud", "glm-5.3:cloud"]

    @pytest.mark.asyncio
    async def test_chat_uses_fallback_retry_loop(self) -> None:
        """Bug #8 regression: chat() must share the primary→fallback retry
        logic, not just retry the primary.
        """
        config = LLMConfig(primary_attempts=1, fallback_attempts=2)
        router = ModelRouter(config=config)

        calls: list[str] = []

        async def fake_chat(model: str, **kwargs: object) -> dict[str, object]:
            calls.append(model)
            if model == "kimi-k2.7-code:cloud":
                raise RuntimeError("primary chat down")
            return {"choices": [{"message": {"content": "hello"}}]}

        router.client.chat = fake_chat  # type: ignore[method-assign,assignment]
        result = await router.chat("code", [{"role": "user", "content": "say hi"}])
        assert result == "hello"
        # We should have hit the primary once, then switched to fallback.
        assert "kimi-k2.7-code:cloud" in calls
        assert "glm-5.3:cloud" in calls

    @pytest.mark.asyncio
    async def test_chat_raises_provider_error_on_empty_choices(self) -> None:
        """C25 regression: empty choices must raise a typed LLMProviderError,
        not an IndexError that leaks out as a retry loop failure."""
        config = LLMConfig(primary_attempts=1, fallback_attempts=0)
        router = ModelRouter(config=config)

        async def fake_chat(model: str, **kwargs: object) -> dict[str, object]:
            return {"choices": []}

        router.client.chat = fake_chat  # type: ignore[method-assign,assignment]

        with pytest.raises(RuntimeError, match="empty choices in chat response") as exc_info:
            await router.chat("code", [{"role": "user", "content": "hi"}])

        assert isinstance(exc_info.value.__cause__, LLMProviderError)

    @pytest.mark.asyncio
    async def test_chat_raises_provider_error_on_missing_content(self) -> None:
        """A response with choices but no content should also be surfaced as
        an LLMProviderError rather than returning an empty string."""
        config = LLMConfig(primary_attempts=1, fallback_attempts=0)
        router = ModelRouter(config=config)

        async def fake_chat(model: str, **kwargs: object) -> dict[str, object]:
            return {"choices": [{"message": {}}]}

        router.client.chat = fake_chat  # type: ignore[method-assign,assignment]

        with pytest.raises(RuntimeError, match="chat response has no content") as exc_info:
            await router.chat("code", [{"role": "user", "content": "hi"}])

        assert isinstance(exc_info.value.__cause__, LLMProviderError)

    @pytest.mark.asyncio
    async def test_circuit_breaker_does_not_trip_on_first_fallback(self) -> None:
        """Bug #7 regression: the circuit breaker must tolerate the default
        threshold (5) consecutive failures, not trip immediately on the
        2nd failure.
        """
        from video_editor_llm.config import LLMConfig

        # 1 primary attempt + 2 fallback attempts = 3 attempts per call.
        # Default threshold=5 means we tolerate 5 failures before tripping.
        config = LLMConfig(
            primary_attempts=1,
            fallback_attempts=2,
            circuit_breaker_failure_threshold=5,
        )
        router = ModelRouter(config=config)

        call_count = 0

        async def always_fail(model: str, **kwargs: object) -> dict[str, object]:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("nope")

        router.client.generate = always_fail  # type: ignore[method-assign,assignment]
        with pytest.raises(RuntimeError, match="LLM call failed"):
            await router.generate("plan", "x")
        # 1 primary + 2 fallback = 3 failures; breaker only opens after 5.
        assert call_count == 3
        assert router._circuit_open is False  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_circuit_breaker_trips_after_threshold_plus_one(self) -> None:
        """Bug #7 regression: threshold=0 means >0 consecutive failures trips."""
        from video_editor_llm.config import LLMConfig

        config = LLMConfig(
            primary_attempts=1,
            fallback_attempts=0,
            circuit_breaker_failure_threshold=0,
        )
        router = ModelRouter(config=config)

        async def always_fail(model: str, **kwargs: object) -> dict[str, object]:
            raise RuntimeError("nope")

        router.client.generate = always_fail  # type: ignore[method-assign,assignment]
        with pytest.raises(RuntimeError, match="LLM call failed"):
            await router.generate("plan", "x")
        # Threshold=0 means > 0 = trip, so 1 failure trips the breaker.
        assert router._circuit_open is True  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_successful_fallback_resets_failure_streak(self) -> None:
        """A successful fallback should reset the consecutive failure counter
        so a single fallback doesn't poison the rest of the job.
        """
        from video_editor_llm.config import LLMConfig

        config = LLMConfig(primary_attempts=1, fallback_attempts=1)
        router = ModelRouter(config=config)

        async def first_fails_then_succeeds(model: str, **kwargs: object) -> dict[str, object]:
            if model == "minimax-m3:cloud":
                raise RuntimeError("primary down")
            return {"response": "ok"}

        router.client.generate = first_fails_then_succeeds  # type: ignore[method-assign,assignment]
        result = await router.generate("plan", "x")
        assert result == "ok"
        # Failure streak was reset by the successful fallback.
        assert router._consecutive_failures == 0  # noqa: SLF001


class TestConfigValidationWarnings:
    """Bug #70/#65 regression: unknown keys and missing api_key_env are logged."""

    def test_load_warns_on_unknown_llm_keys(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """C26 regression: warnings must go through structlog so they are
        visible in production logging, not silently filtered Python warnings."""
        path = tmp_path / "config.yaml"
        _write_config(
            path,
            """
llm:
  default_provider: ollama
  providers:
    ollama:
      base_url: http://localhost:11434
      default_model: kimi-k2.7-code:cloud
  unknown_llm_key: ignored
""",
        )
        with caplog.at_level("WARNING", logger="video_editor_llm.config"):
            LLMConfig.load(path)

        assert "unknown keys" in caplog.text
        assert "unknown_llm_key" in caplog.text

    def test_client_kwargs_warns_on_missing_api_key_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Bug #65/C26 regression: missing api_key_env must be structlog-warned."""
        path = tmp_path / "config.yaml"
        _write_config(
            path,
            """
providers:
  ollama:
    base_url: http://localhost:11434
    api_key_env: MISSING_OLLAMA_KEY
    default_model: kimi-k2.7-code:cloud
default_provider: ollama
""",
        )
        monkeypatch.delenv("MISSING_OLLAMA_KEY", raising=False)
        config = LLMConfig.load(path)
        with caplog.at_level("WARNING", logger="video_editor_llm.config"):
            config.client_kwargs("ollama")

        assert "MISSING_OLLAMA_KEY" in caplog.text
        assert "unauthenticated" in caplog.text
