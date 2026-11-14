"""Tests for the Freesound SFX provider (no network)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers.audio.freesound_sfx import (
    LICENSE_FILTER,
    FreesoundSFXProvider,
)

PREVIEW = "https://cdn.freesound.test/previews/1_hq.mp3"


def _request(prompt: str = "whoosh") -> AssetRequest:
    return AssetRequest(
        job_id="job",
        asset_id="sfx_whoosh",
        scene_id="scene_0",
        asset_type=AssetType.SFX,
        prompt=prompt,
        output_ext="mp3",
    )


def _config() -> ProviderConfig:
    return ProviderConfig(
        api_key_env="FREESOUND_API_KEY",
        base_url="https://freesound.test",
        timeout=5.0,
    )


def _transport(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://freesound.test",
    )


def _result(preview: str | None = PREVIEW) -> dict:
    previews = {} if preview is None else {"preview-hq-mp3": preview}
    return {
        "id": 1,
        "name": "Whoosh",
        "username": "A Creator",
        "license": "https://creativecommons.org/publicdomain/zero/1.0/",
        "url": "https://freesound.org/s/1",
        "duration": 0.8,
        "previews": previews,
    }


def test_a_cc0_result_is_downloaded_with_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FREESOUND_API_KEY", "test-key")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/apiv2/search/text/":
            seen["query"] = request.url.params["query"]
            seen["filter"] = request.url.params["filter"]
            seen["authorization"] = request.headers.get("authorization", "")
            seen["has_token_param"] = str("token" in request.url.params)
            return httpx.Response(200, json={"results": [_result()]})
        if str(request.url) == PREVIEW:
            return httpx.Response(
                200, content=b"\xff\xfb\x90\x00", headers={"content-type": "audio/mpeg"}
            )
        return httpx.Response(404, json={"detail": "not found"})

    provider = FreesoundSFXProvider(
        _config(), client=_transport(handler), download_client=_transport(handler)
    )
    output = tmp_path / "sfx.mp3"
    result = asyncio.run(provider.generate(_request(), output))

    assert seen["query"] == "whoosh"
    assert seen["authorization"] == "Token test-key"
    assert seen["has_token_param"] == "False"
    assert seen["filter"] == f"{LICENSE_FILTER} duration:[0 TO 3]"
    assert "Creative Commons 0" in seen["filter"]
    assert "Attribution" in seen["filter"]
    assert output.read_bytes() == b"\xff\xfb\x90\x00"
    assert result.asset_type is AssetType.SFX
    assert result.scene_id == "scene_0"
    assert result.metadata["title"] == "Whoosh"
    assert result.metadata["creator"] == "A Creator"
    assert result.metadata["license"] == "CC0 1.0"
    assert result.metadata["license_url"].endswith("zero/1.0/")
    assert result.duration_ms == 800
    assert result.metadata["source_url"] == "https://freesound.org/s/1"


def test_a_missing_api_key_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FREESOUND_API_KEY", raising=False)
    provider = FreesoundSFXProvider(
        _config(), client=_transport(lambda request: httpx.Response(500))
    )
    with pytest.raises(RuntimeError, match="FREESOUND_API_KEY"):
        asyncio.run(provider.generate(_request(), tmp_path / "sfx.mp3"))


def test_no_results_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FREESOUND_API_KEY", "test-key")
    provider = FreesoundSFXProvider(
        _config(),
        client=_transport(lambda request: httpx.Response(200, json={"results": []})),
    )
    with pytest.raises(RuntimeError, match="no CC0/CC BY sound"):
        asyncio.run(provider.generate(_request("obscure"), tmp_path / "sfx.mp3"))


def test_a_result_without_a_preview_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FREESOUND_API_KEY", "test-key")
    provider = FreesoundSFXProvider(
        _config(),
        client=_transport(
            lambda request: httpx.Response(200, json={"results": [_result(preview=None)]})
        ),
    )
    with pytest.raises(RuntimeError, match="no CC0/CC BY sound"):
        asyncio.run(provider.generate(_request(), tmp_path / "sfx.mp3"))


def test_an_unacceptable_licence_result_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first result is not trusted: an NC result must not ship (#301)."""
    monkeypatch.setenv("FREESOUND_API_KEY", "test-key")
    nc = _result()
    nc["license"] = "https://creativecommons.org/licenses/by-nc/4.0/"
    nc["name"] = "Non-commercial bed"
    cc0 = _result()
    cc0["id"] = 2
    cc0["name"] = "CC0 hit"
    cc0["previews"] = {"preview-hq-mp3": "https://cdn.freesound.test/previews/2_hq.mp3"}

    provider = FreesoundSFXProvider(
        _config(),
        client=_transport(
            lambda request: (
                httpx.Response(200, json={"results": [nc, cc0]})
                if request.url.path == "/apiv2/search/text/"
                else httpx.Response(
                    200, content=b"\xff\xfb\x90\x00", headers={"content-type": "audio/mpeg"}
                )
            )
        ),
        download_client=_transport(
            lambda request: httpx.Response(
                200, content=b"\xff\xfb\x90\x00", headers={"content-type": "audio/mpeg"}
            )
        ),
    )
    result = asyncio.run(provider.generate(_request(), tmp_path / "sfx.mp3"))
    assert result.metadata["title"] == "CC0 hit"


def test_a_too_long_result_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FREESOUND_API_KEY", "test-key")
    long = _result()
    long["duration"] = 9.0
    short = _result()
    short["id"] = 2
    short["name"] = "Short hit"
    short["duration"] = 0.8
    short["previews"] = {"preview-hq-mp3": "https://cdn.freesound.test/previews/2_hq.mp3"}

    provider = FreesoundSFXProvider(
        _config(),
        client=_transport(
            lambda request: (
                httpx.Response(200, json={"results": [long, short]})
                if request.url.path == "/apiv2/search/text/"
                else httpx.Response(404)
            )
        ),
        download_client=_transport(
            lambda request: httpx.Response(
                200, content=b"\xff\xfb\x90\x00", headers={"content-type": "audio/mpeg"}
            )
        ),
    )
    result = asyncio.run(provider.generate(_request(), tmp_path / "sfx.mp3"))
    assert result.metadata["title"] == "Short hit"


def test_an_empty_prompt_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FREESOUND_API_KEY", "test-key")
    provider = FreesoundSFXProvider(_config(), client=_transport(lambda r: httpx.Response(200)))
    with pytest.raises(RuntimeError, match="needs a search prompt"):
        asyncio.run(provider.generate(_request("  "), tmp_path / "sfx.mp3"))


def test_the_provider_is_free_and_not_an_image_source() -> None:
    provider = FreesoundSFXProvider(_config())
    assert provider.provider_name == "freesound_sfx"
    assert provider.consumes_image_quota is False
    assert provider.generates_images is False


def test_the_request_duration_caps_the_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FREESOUND_API_KEY", "test-key")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/apiv2/search/text/":
            seen["filter"] = request.url.params["filter"]
            return httpx.Response(200, json={"results": [_result()]})
        return httpx.Response(
            200, content=b"\xff\xfb\x90\x00", headers={"content-type": "audio/mpeg"}
        )

    provider = FreesoundSFXProvider(
        _config(), client=_transport(handler), download_client=_transport(handler)
    )
    request = _request().model_copy(update={"params": {"duration_seconds": 1.5}})
    asyncio.run(provider.generate(request, tmp_path / "sfx.mp3"))
    assert seen["filter"].endswith("duration:[0 TO 1.5]")


def test_a_cc_by_licence_gets_a_readable_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FREESOUND_API_KEY", "test-key")
    by = _result()
    by["license"] = "https://creativecommons.org/licenses/by/4.0/"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/apiv2/search/text/":
            return httpx.Response(200, json={"results": [by]})
        return httpx.Response(
            200, content=b"\xff\xfb\x90\x00", headers={"content-type": "audio/mpeg"}
        )

    provider = FreesoundSFXProvider(
        _config(), client=_transport(handler), download_client=_transport(handler)
    )
    result = asyncio.run(provider.generate(_request(), tmp_path / "sfx.mp3"))
    assert result.metadata["license"] == "CC BY 4.0"
    assert result.metadata["license_url"] == "https://creativecommons.org/licenses/by/4.0/"


def test_a_cc_by_3_licence_is_labelled() -> None:
    from video_editor_asset_ingestion.providers.audio.freesound_sfx import _license_label

    assert _license_label("https://creativecommons.org/licenses/by/3.0/") == "CC BY 3.0"
