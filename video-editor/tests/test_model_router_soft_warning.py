"""Tests for ModelRouter's soft warning when a configured model is missing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


def test_missing_model_logs_warning_but_does_not_raise(tmp_path: Path) -> None:
    """A configured model that isn't in the live ollama list must warn, not raise."""
    # Import lazily to avoid import-time config load.
    from video_editor_llm.router import ModelRouter

    router = ModelRouter.from_config(Path("config.yaml"))
    with (
        patch.object(router, "_list_ollama_models", return_value={"some-other-model:cloud"}),
        patch("video_editor_llm.router.structlog.get_logger") as mock_logger,
    ):
        logger = mock_logger.return_value
        # The call should not raise even though the configured model is missing.
        try:
            router._validate_model_known("minimax-m3:cloud")
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"_validate_model_known should not raise, got: {exc}")
        # And it should have logged a warning.
        assert any(
            call.args and "model_not_in_ollama_list" in (call.args[0] or "")
            for call in logger.warning.call_args_list
        ), "expected a warning containing 'model_not_in_ollama_list'"
