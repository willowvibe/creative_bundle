"""Tests for YouTube OAuth with fake flows, credentials and services."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from google.auth.exceptions import RefreshError

from social_engine.core.accounts import Account, load_account, save_account
from social_engine.core.errors import AuthExpired, SocialError, WrongAccount
from social_engine.platforms.youtube.auth import SCOPES, authorize, load_credentials


@pytest.fixture(autouse=True)
def state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "social-engine"
    monkeypatch.setenv("SOCIAL_ENGINE_HOME", str(home))
    monkeypatch.setattr("social_engine.core.accounts._port_in_use", lambda _port: False)
    return home


def _secret(home: Path) -> Path:
    path = home / "youtube" / "client_secret.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    return path


def _token(home: Path, content: str = "original") -> Path:
    path = home / "youtube" / "tokens" / "mantra-shakti.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class FakeCredentials:
    def __init__(
        self,
        *,
        expired: bool = False,
        refresh_token: str | None = "refresh",
        payload: str = '{"token": "fresh"}',
        refreshed_payload: str = '{"token": "refreshed"}',
        error: Exception | None = None,
    ) -> None:
        self.expired = expired
        self.refresh_token = refresh_token
        self.payload = payload
        self.refreshed_payload = refreshed_payload
        self.error = error
        self.refresh_calls = 0

    def to_json(self) -> str:
        return self.payload

    def refresh(self, request: object) -> None:
        self.refresh_calls += 1
        if self.error is not None:
            raise self.error
        self.expired = False
        self.payload = self.refreshed_payload


class FakeFlow:
    def __init__(self, credentials: FakeCredentials) -> None:
        self.credentials = credentials
        self.ports: list[int] = []

    def run_local_server(self, *, port: int) -> FakeCredentials:
        self.ports.append(port)
        return self.credentials


def _flow_factory(
    credentials: FakeCredentials,
) -> tuple[object, list[tuple[str, list[str], FakeFlow]]]:
    calls: list[tuple[str, list[str], FakeFlow]] = []

    def factory(path: str, scopes: list[str]) -> FakeFlow:
        flow = FakeFlow(credentials)
        calls.append((path, scopes, flow))
        return flow

    return factory, calls


class FakeExecute:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def execute(self) -> dict[str, object]:
        return self.payload


class FakeService:
    def __init__(self, channel_id: str) -> None:
        self.channel_id = channel_id
        self.list_calls: list[dict[str, object]] = []

    def channels(self) -> FakeService:
        return self

    def list(self, **kwargs: object) -> FakeExecute:
        self.list_calls.append(kwargs)
        return FakeExecute({"items": [{"id": self.channel_id}]})


def _service_factory(
    channel_id: str,
) -> tuple[object, list[tuple[str, str, dict[str, object], FakeService]]]:
    calls: list[tuple[str, str, dict[str, object], FakeService]] = []

    def factory(api: str, version: str, **kwargs: object) -> FakeService:
        service = FakeService(channel_id)
        calls.append((api, version, kwargs, service))
        return service

    return factory, calls


def test_authorize_writes_a_0600_token_and_stores_the_channel_id(state_home: Path) -> None:
    secret = _secret(state_home)
    credentials = FakeCredentials(payload='{"token": "new"}')
    flow_factory, flows = _flow_factory(credentials)
    service_factory, services = _service_factory("UC123")

    result = authorize(
        "mantra-shakti",
        flow_factory=flow_factory,
        service_factory=service_factory,
    )

    assert result is credentials
    token = state_home / "youtube" / "tokens" / "mantra-shakti.json"
    assert token.read_text(encoding="utf-8") == '{"token": "new"}'
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert load_account("youtube", "mantra-shakti").platform_account_id == "UC123"

    assert len(flows) == 1
    assert flows[0][0] == str(secret)
    assert flows[0][1] == SCOPES
    assert flows[0][2].ports == [0]

    assert len(services) == 1
    assert services[0][:2] == ("youtube", "v3")
    assert services[0][2]["credentials"] is credentials
    assert services[0][2]["cache_discovery"] is False
    assert services[0][3].list_calls == [{"mine": True, "part": "id"}]


def test_a_different_channel_raises_wrong_account_and_leaves_files_alone(
    state_home: Path,
) -> None:
    _secret(state_home)
    save_account(
        Account(
            platform="youtube",
            name="mantra-shakti",
            platform_account_id="UC_OLD",
            cdp_port=9333,
        )
    )
    token = _token(state_home, "old token")
    token.chmod(0o644)
    credentials = FakeCredentials(payload="new token")
    flow_factory, _ = _flow_factory(credentials)
    service_factory, _ = _service_factory("UC_NEW")

    with pytest.raises(WrongAccount, match="UC_NEW"):
        authorize(
            "mantra-shakti",
            flow_factory=flow_factory,
            service_factory=service_factory,
        )

    assert token.read_text(encoding="utf-8") == "old token"
    assert stat.S_IMODE(token.stat().st_mode) == 0o644
    assert load_account("youtube", "mantra-shakti").platform_account_id == "UC_OLD"


def test_authorize_requires_the_client_secret(state_home: Path) -> None:
    with pytest.raises(SocialError) as caught:
        authorize("mantra-shakti")

    assert caught.value.code == "missing_client_secret"
    assert "Google Cloud project" in caught.value.next_step
    assert str(state_home / "youtube" / "client_secret.json") in caught.value.next_step


def test_load_credentials_returns_an_unexpired_token_without_rewriting(
    state_home: Path,
) -> None:
    _secret(state_home)
    token = _token(state_home, "original")
    credentials = FakeCredentials(expired=False, payload="new payload")
    calls: list[tuple[str, list[str]]] = []

    def factory(path: str, scopes: list[str]) -> FakeCredentials:
        calls.append((path, scopes))
        return credentials

    result = load_credentials("mantra-shakti", credentials_factory=factory)

    assert result is credentials
    assert calls == [(str(token), SCOPES)]
    assert token.read_text(encoding="utf-8") == "original"


def test_load_credentials_refreshes_and_rewrites_the_token(state_home: Path) -> None:
    _secret(state_home)
    token = _token(state_home, "expired")
    credentials = FakeCredentials(
        expired=True,
        payload="expired",
        refreshed_payload='{"token": "refreshed"}',
    )
    requests: list[object] = []

    def request_factory() -> object:
        request = object()
        requests.append(request)
        return request

    result = load_credentials(
        "mantra-shakti",
        credentials_factory=lambda _path, _scopes: credentials,
        request_factory=request_factory,
    )

    assert result is credentials
    assert credentials.refresh_calls == 1
    assert len(requests) == 1
    assert token.read_text(encoding="utf-8") == '{"token": "refreshed"}'
    assert stat.S_IMODE(token.stat().st_mode) == 0o600


def test_a_refresh_error_maps_to_auth_expired(state_home: Path) -> None:
    _secret(state_home)
    token = _token(state_home, "expired")
    credentials = FakeCredentials(expired=True, error=RefreshError("revoked"))

    with pytest.raises(AuthExpired, match="could not be refreshed"):
        load_credentials(
            "mantra-shakti",
            credentials_factory=lambda _path, _scopes: credentials,
        )

    assert token.read_text(encoding="utf-8") == "expired"


def test_a_missing_token_maps_to_auth_expired(state_home: Path) -> None:
    _secret(state_home)

    with pytest.raises(AuthExpired, match="no YouTube token"):
        load_credentials("mantra-shakti")


def test_an_unreadable_token_maps_to_auth_expired(state_home: Path) -> None:
    _secret(state_home)
    _token(state_home)

    def broken_factory(_path: str, _scopes: list[str]) -> FakeCredentials:
        raise ValueError("not JSON")

    with pytest.raises(AuthExpired, match="unreadable"):
        load_credentials("mantra-shakti", credentials_factory=broken_factory)


def test_load_credentials_requires_the_client_secret(state_home: Path) -> None:
    with pytest.raises(SocialError) as caught:
        load_credentials("mantra-shakti")

    assert caught.value.code == "missing_client_secret"
