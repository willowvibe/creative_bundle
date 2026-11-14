"""Tests for the config-driven transcription settings resolver."""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_tools import gpu as gpu_mod
from video_editor_tools import transcription as tr_mod
from video_editor_tools.transcription import resolve_transcription_settings


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "VE_WHISPER_MODEL",
        "VE_WHISPER_LANGUAGE",
        "VE_WHISPER_DEVICE",
        "VE_WHISPER_COMPUTE_TYPE",
        "VE_GPU",
    ):
        monkeypatch.delenv(key, raising=False)


def test_env_overrides_win(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("VE_WHISPER_MODEL", "medium")
    monkeypatch.setenv("VE_WHISPER_LANGUAGE", "fr")
    monkeypatch.setenv("VE_WHISPER_DEVICE", "cpu")
    monkeypatch.setenv("VE_WHISPER_COMPUTE_TYPE", "int8")
    settings = resolve_transcription_settings()
    assert settings.model == "medium"
    assert settings.language == "fr"
    assert settings.device == "cpu"
    assert settings.compute_type == "int8"


def test_cuda_keeps_turbo_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("VE_WHISPER_DEVICE", "cuda")
    monkeypatch.setenv("VE_WHISPER_MODEL", "large-v3-turbo")
    monkeypatch.setattr(tr_mod, "cuda_available", lambda: False)
    settings = resolve_transcription_settings()
    assert settings.device == "cpu"
    assert settings.model == "large-v3-turbo"
    assert settings.compute_type == "int8"


def test_cuda_maps_plain_large_to_turbo_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("VE_WHISPER_DEVICE", "cuda")
    monkeypatch.setenv("VE_WHISPER_MODEL", "large-v3")
    monkeypatch.setattr(tr_mod, "cuda_available", lambda: False)
    settings = resolve_transcription_settings()
    assert settings.device == "cpu"
    assert settings.model == "large-v3-turbo"
    assert settings.compute_type == "int8"


def test_cuda_keeps_small_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("VE_WHISPER_DEVICE", "cuda")
    monkeypatch.setenv("VE_WHISPER_MODEL", "small")
    monkeypatch.setattr(tr_mod, "cuda_available", lambda: False)
    settings = resolve_transcription_settings()
    assert settings.device == "cpu"
    assert settings.model == "small"
    assert settings.compute_type == "int8"


def test_cuda_kept_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("VE_WHISPER_DEVICE", "cuda")
    monkeypatch.setenv("VE_WHISPER_MODEL", "large-v3-turbo")
    monkeypatch.setenv("VE_WHISPER_COMPUTE_TYPE", "int8_float16")
    monkeypatch.setattr(tr_mod, "cuda_available", lambda: True)
    settings = resolve_transcription_settings()
    assert settings.device == "cuda"
    assert settings.model == "large-v3-turbo"
    assert settings.compute_type == "int8_float16"


def test_compute_type_defaults_per_device(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("VE_WHISPER_DEVICE", "cpu")
    monkeypatch.setattr(tr_mod, "cuda_available", lambda: True)
    monkeypatch.setattr(tr_mod, "_load_transcription_config", lambda: ("small", "en", "cpu", ""))
    settings = resolve_transcription_settings()
    assert settings.compute_type == "int8"


def test_falls_back_when_config_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setattr(
        tr_mod,
        "_load_transcription_config",
        lambda: ("small", "en", "cpu", "int8"),
    )
    settings = resolve_transcription_settings(Path("nonexistent.yaml"))
    assert settings.model == "small"
    assert settings.device == "cpu"


def test_cuda_available_respects_ve_gpu_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VE_GPU", "cpu")
    assert gpu_mod.cuda_available() is False
