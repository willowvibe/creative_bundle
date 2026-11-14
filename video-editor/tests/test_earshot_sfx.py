"""Tests for the CLI-backed Earshot SFX provider (no Earshot install)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers.audio.earshot_sfx import EarshotSFXProvider

FAKE = """\
import json
import pathlib
import sys
import wave

args = sys.argv[1:]
out = pathlib.Path(args[args.index("-o") + 1])
out.parent.mkdir(parents=True, exist_ok=True)
with wave.open(str(out), "wb") as handle:
    handle.setnchannels(1)
    handle.setsampwidth(2)
    handle.setframerate(44100)
    handle.writeframes(b"\\x00\\x00" * 8820)
out.with_name("invocation.json").write_text(json.dumps(args))
"""


def _fake_command(tmp_path: Path) -> list[str]:
    script = tmp_path / "fake_earshot.py"
    script.write_text(FAKE, encoding="utf-8")
    return [sys.executable, str(script)]


def _config(tmp_path: Path, **options) -> ProviderConfig:
    return ProviderConfig(
        timeout=10.0,
        provider_options={"command": _fake_command(tmp_path), **options},
    )


def _request(**params) -> AssetRequest:
    return AssetRequest(
        job_id="job",
        asset_id="sfx_hit",
        scene_id="scene_0",
        asset_type=AssetType.SFX,
        prompt="ignored by earshot",
        params=params,
        output_ext="wav",
    )


def test_the_cue_sound_maps_to_the_bank_id(tmp_path: Path) -> None:
    provider = EarshotSFXProvider(_config(tmp_path, bank_ids={"hit": "cut_hit/punch"}))
    out = tmp_path / "hit.wav"
    result = asyncio.run(
        provider.generate(
            _request(cue_sound="hit", duration_seconds=0.5),
            out,
        )
    )
    invocation = json.loads((tmp_path / "invocation.json").read_text())
    assert "cut_hit/punch" in invocation
    assert invocation[invocation.index("--dur") + 1] == "0.5"
    assert result.asset_type is AssetType.SFX
    assert result.metadata["bank_id"] == "cut_hit/punch"
    assert result.metadata["license"] == "generated"
    assert result.duration_ms == pytest.approx(200, abs=50)
    assert result.scene_id == "scene_0"


def test_an_explicit_bank_id_wins(tmp_path: Path) -> None:
    provider = EarshotSFXProvider(_config(tmp_path))
    asyncio.run(
        provider.generate(
            _request(earshot_id="whoosh_big/thump", duration_seconds=0.25),
            tmp_path / "whoosh.wav",
        )
    )
    invocation = json.loads((tmp_path / "invocation.json").read_text())
    assert "whoosh_big/thump" in invocation


def test_apex_is_passed_when_configured(tmp_path: Path) -> None:
    provider = EarshotSFXProvider(_config(tmp_path, apex=0.32))
    asyncio.run(
        provider.generate(_request(cue_sound="whoosh", duration_seconds=0.8), tmp_path / "w.wav")
    )
    invocation = json.loads((tmp_path / "invocation.json").read_text())
    assert invocation[invocation.index("--apex") + 1] == "0.32"


def test_the_natural_length_is_kept_when_no_duration_is_asked_for(
    tmp_path: Path,
) -> None:
    provider = EarshotSFXProvider(_config(tmp_path))
    asyncio.run(
        provider.generate(
            _request(cue_sound="whoosh", page=2),
            tmp_path / "w.wav",
        )
    )
    invocation = json.loads((tmp_path / "invocation.json").read_text())
    assert "--dur" not in invocation
    assert invocation[invocation.index("--seed") + 1] == "2"


def test_a_cue_without_a_bank_id_is_refused(tmp_path: Path) -> None:
    provider = EarshotSFXProvider(_config(tmp_path))
    with pytest.raises(RuntimeError, match="no bank id"):
        asyncio.run(provider.generate(_request(cue_sound="unknown"), tmp_path / "x.wav"))


def test_a_missing_command_is_named(tmp_path: Path) -> None:
    provider = EarshotSFXProvider(
        ProviderConfig(provider_options={"command": ["/no/such/earshot"]})
    )
    with pytest.raises(RuntimeError, match="Earshot command not found"):
        asyncio.run(provider.generate(_request(cue_sound="hit"), tmp_path / "x.wav"))


def test_a_failed_render_carries_the_stderr(tmp_path: Path) -> None:
    script = tmp_path / "failing.py"
    script.write_text('import sys; sys.stderr.write("bank id missing\\n"); sys.exit(2)\n')
    provider = EarshotSFXProvider(
        ProviderConfig(
            timeout=10.0,
            provider_options={"command": [sys.executable, str(script)]},
        )
    )
    with pytest.raises(RuntimeError, match="bank id missing"):
        asyncio.run(provider.generate(_request(cue_sound="hit"), tmp_path / "x.wav"))


def test_the_provider_is_free_and_not_an_image_source() -> None:
    provider = EarshotSFXProvider(ProviderConfig())
    assert provider.provider_name == "earshot_sfx"
    assert provider.consumes_image_quota is False
    assert provider.generates_images is False
