"""Tests for named accounts under the shared state home."""

from __future__ import annotations

from pathlib import Path

import pytest

from social_engine.core.accounts import Account, load_account, save_account


@pytest.fixture(autouse=True)
def state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "social-engine"
    monkeypatch.setenv("SOCIAL_ENGINE_HOME", str(home))
    monkeypatch.setattr("social_engine.core.accounts._port_in_use", lambda _port: False)
    return home


def test_loading_an_unknown_account_creates_one_with_a_cdp_port(state_home: Path) -> None:
    account = load_account("youtube", "mantra-shakti")

    assert account.platform == "youtube"
    assert account.name == "mantra-shakti"
    assert account.platform_account_id is None
    assert account.cdp_port == 9333
    assert account.api_audited is False
    assert (state_home / "youtube" / "accounts" / "mantra-shakti.yaml").is_file()


def test_a_second_account_gets_the_next_port(state_home: Path) -> None:
    first = load_account("youtube", "mantra-shakti")
    second = load_account("youtube", "cosmicsaju")

    assert first.cdp_port == 9333
    assert second.cdp_port == 9334


def test_ports_are_unique_across_platforms(state_home: Path) -> None:
    youtube = load_account("youtube", "mantra-shakti")
    instagram = load_account("instagram", "mantra-shakti")
    second_youtube = load_account("youtube", "cosmicsaju")

    assert (youtube.cdp_port, instagram.cdp_port, second_youtube.cdp_port) == (9333, 9334, 9335)


def test_saved_accounts_round_trip(state_home: Path) -> None:
    save_account(
        Account(
            platform="youtube",
            name="cosmicsaju",
            platform_account_id="UC123",
            cdp_port=9337,
            api_audited=True,
        )
    )

    loaded = load_account("youtube", "cosmicsaju")
    assert loaded.platform_account_id == "UC123"
    assert loaded.cdp_port == 9337
    assert loaded.api_audited is True


def test_the_account_file_is_per_platform(state_home: Path) -> None:
    load_account("youtube", "mantra-shakti")
    load_account("instagram", "mantra-shakti")

    assert (state_home / "youtube" / "accounts" / "mantra-shakti.yaml").is_file()
    assert (state_home / "instagram" / "accounts" / "mantra-shakti.yaml").is_file()


def test_invalid_names_cannot_escape_the_home(state_home: Path) -> None:
    with pytest.raises(ValueError, match="invalid account name"):
        load_account("youtube", "../escape")
    with pytest.raises(ValueError, match="invalid platform name"):
        load_account("../youtube", "mantra-shakti")
