"""Tests for ve check-config command."""

from __future__ import annotations

import os
from unittest.mock import patch

from video_editor_cli.commands.check_config import validate_provider_keys


class TestValidateProviderKeys:
    def test_reports_missing_keys(self, tmp_path):
        """Providers with missing env vars should report missing_key status."""
        with patch.dict(os.environ, {}, clear=True):
            results = validate_provider_keys()
            # Without any .env, all keyed providers should show missing
            missing = {k: v for k, v in results.items() if v["status"] == "missing_key"}
            assert len(missing) > 0

    def test_reports_present_keys(self, tmp_path):
        """Providers with set env vars should report ready status."""
        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-key-1234"}, clear=True):
            results = validate_provider_keys()
            if "elevenlabs_tts" in results:
                assert results["elevenlabs_tts"]["status"] == "ready"
                # The value is never echoed, not even a prefix.
                message = results["elevenlabs_tts"]["message"]
                assert "test-key-1234" not in message
                assert "test" not in message
                assert "ELEVENLABS_API_KEY is set" in message

    def test_no_key_providers_show_ready(self, tmp_path):
        """Providers without api_key_env should always show ready."""
        with patch.dict(os.environ, {}, clear=True):
            results = validate_provider_keys()
            for _name, info in results.items():
                if info["key_env"] is None:
                    assert info["status"] == "ready"
