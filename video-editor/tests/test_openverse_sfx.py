"""Tests for the Openverse SFX provider (no network)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers.audio.openverse_sfx import (
    OpenverseSFXProvider,
)

AUDIO = "https://93.184.216.34/351256_hq.mp3"


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
    return ProviderConfig(base_url="https://api.openverse.test", timeout=5.0)


def _transport(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.openverse.test",
    )


def _download_transport(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )


def _result(
    *,
    license: str = "cc0",
    url: str | None = AUDIO,
    title: str = "Deep Whoosh #1",
) -> dict:
    return {
        "id": "abc",
        "title": title,
        "creator": "Kinoton",
        "foreign_landing_url": "https://freesound.org/people/Kinoton/sounds/351256",
        "url": url,
        "license": license,
        "license_version": "1.0",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "provider": "freesound",
        "duration": 3155,
    }


def test_the_first_acceptable_licence_is_downloaded(tmp_path: Path) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/audio/":
            seen["q"] = request.url.params["q"]
            seen["license_type"] = request.url.params["license_type"]
            # ShareAlike matches the API filter but is excluded here.
            return httpx.Response(
                200,
                json={"results": [_result(license="by-sa"), _result()]},
            )
        if str(request.url) == AUDIO:
            return httpx.Response(
                200, content=b"\xff\xfb\x90\x00", headers={"content-type": "audio/mpeg"}
            )
        return httpx.Response(404, json={"detail": "not found"})

    provider = OpenverseSFXProvider(
        _config(), client=_transport(handler), download_client=_download_transport(handler)
    )
    output = tmp_path / "sfx.mp3"
    result = asyncio.run(provider.generate(_request(), output))

    assert seen == {"q": "whoosh", "license_type": "commercial,modification"}
    assert output.read_bytes() == b"\xff\xfb\x90\x00"
    assert result.asset_type is AssetType.SFX
    assert result.duration_ms == 3155
    assert result.metadata["title"] == "Deep Whoosh #1"
    assert result.metadata["creator"] == "Kinoton"
    assert result.metadata["license"] == "CC0 1.0"
    assert result.metadata["source_url"].startswith("https://freesound.org/")
    assert result.metadata["provider"] == "freesound"


def test_nc_nd_and_sa_only_results_are_refused(tmp_path: Path) -> None:
    provider = OpenverseSFXProvider(
        _config(),
        client=_transport(
            lambda request: httpx.Response(
                200,
                json={
                    "results": [
                        _result(license="by-nc"),
                        _result(license="by-nd"),
                        _result(license="by-sa"),
                    ]
                },
            )
        ),
    )
    with pytest.raises(RuntimeError, match="no CC0/PDM/CC BY"):
        asyncio.run(provider.generate(_request(), tmp_path / "sfx.mp3"))


def test_a_result_without_an_audio_url_is_refused(tmp_path: Path) -> None:
    provider = OpenverseSFXProvider(
        _config(),
        client=_transport(
            lambda request: httpx.Response(200, json={"results": [_result(url=None)]})
        ),
    )
    with pytest.raises(RuntimeError, match="no audio URL"):
        asyncio.run(provider.generate(_request(), tmp_path / "sfx.mp3"))


def test_an_empty_prompt_is_refused(tmp_path: Path) -> None:
    provider = OpenverseSFXProvider(
        _config(), client=_transport(lambda request: httpx.Response(200))
    )
    with pytest.raises(RuntimeError, match="needs a search prompt"):
        asyncio.run(provider.generate(_request("  "), tmp_path / "sfx.mp3"))


def test_the_provider_is_free_and_not_an_image_source() -> None:
    provider = OpenverseSFXProvider(_config())
    assert provider.provider_name == "openverse_sfx"
    assert provider.consumes_image_quota is False
    assert provider.generates_images is False


def test_a_short_result_is_preferred_over_a_long_one(tmp_path: Path) -> None:
    long = _result(title="Long bed")
    long["duration"] = 9000
    short = _result(title="Short hit")
    short["id"] = "short"
    short["duration"] = 900
    short["url"] = "https://93.184.216.35/short.mp3"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/audio/":
            return httpx.Response(200, json={"results": [long, short]})
        return httpx.Response(
            200, content=b"\xff\xfb\x90\x00", headers={"content-type": "audio/mpeg"}
        )

    provider = OpenverseSFXProvider(
        _config(), client=_transport(handler), download_client=_download_transport(handler)
    )
    result = asyncio.run(provider.generate(_request(), tmp_path / "sfx.mp3"))
    assert result.metadata["title"] == "Short hit"
    assert result.duration_ms == 900


def test_the_audio_download_does_not_carry_the_api_token(tmp_path: Path) -> None:
    """#300: the bearer token belongs to the API host, not the indexed host."""
    seen: dict[str, str | None] = {}

    def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [_result()]})

    def download_handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(
            200, content=b"\xff\xfb\x90\x00", headers={"content-type": "audio/mpeg"}
        )

    api_client = httpx.AsyncClient(
        transport=httpx.MockTransport(api_handler),
        base_url="https://api.openverse.test",
        headers={"Authorization": "Bearer secret-token"},
    )
    provider = OpenverseSFXProvider(
        _config(), client=api_client, download_client=_download_transport(download_handler)
    )
    asyncio.run(provider.generate(_request(), tmp_path / "sfx.mp3"))

    assert seen["authorization"] is None


def test_a_non_https_audio_url_is_refused(tmp_path: Path) -> None:
    provider = OpenverseSFXProvider(
        _config(),
        client=_transport(
            lambda request: httpx.Response(
                200, json={"results": [_result(url="http://93.184.216.34/a.mp3")]}
            )
        ),
    )
    with pytest.raises(RuntimeError, match="must use https"):
        asyncio.run(provider.generate(_request(), tmp_path / "sfx.mp3"))


def test_a_private_audio_host_is_refused(tmp_path: Path) -> None:
    provider = OpenverseSFXProvider(
        _config(),
        client=_transport(
            lambda request: httpx.Response(
                200, json={"results": [_result(url="https://127.0.0.1/a.mp3")]}
            )
        ),
    )
    with pytest.raises(RuntimeError, match="non-public address"):
        asyncio.run(provider.generate(_request(), tmp_path / "sfx.mp3"))


def test_a_cdn_redirect_is_followed(tmp_path: Path) -> None:
    def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [_result()]})

    def download_handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == AUDIO:
            return httpx.Response(302, headers={"location": "https://93.184.216.35/final.mp3"})
        return httpx.Response(
            200, content=b"\xff\xfb\x90\x00", headers={"content-type": "audio/mpeg"}
        )

    provider = OpenverseSFXProvider(
        _config(),
        client=_transport(api_handler),
        download_client=_download_transport(download_handler),
    )
    output = tmp_path / "sfx.mp3"
    asyncio.run(provider.generate(_request(), output))
    assert output.read_bytes() == b"\xff\xfb\x90\x00"
