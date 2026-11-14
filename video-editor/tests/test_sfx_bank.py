"""Tests for the SFX bank (local-first resolution and credits)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sfx_placement.bank import (
    LicenceRecord,
    ResolvedSfx,
    SfxBank,
    SfxBankError,
)


def test_a_local_file_wins_without_calling_the_resolver(tmp_path: Path) -> None:
    local = tmp_path / "sfx"
    local.mkdir()
    clip = local / "hit.mp3"
    clip.write_bytes(b"mp3")
    called: list[str] = []

    async def resolver(sound: str) -> ResolvedSfx | None:
        called.append(sound)
        return None

    bank = SfxBank(local_dir=local, resolver=resolver)
    resolved = asyncio.run(bank.resolve("hit"))
    assert resolved.path == clip
    assert resolved.provider == "local"
    assert called == []


def test_the_resolver_supplies_a_missing_sound(tmp_path: Path) -> None:
    generated = tmp_path / "generated.mp3"
    generated.write_bytes(b"mp3")

    async def resolver(sound: str) -> ResolvedSfx | None:
        assert sound == "whoosh"
        return ResolvedSfx(
            path=generated,
            provider="earshot",
            licence=LicenceRecord(
                title="whoosh",
                creator="earshot",
                source_url="https://example.test/whoosh",
                license="generated",
            ),
        )

    bank = SfxBank(local_dir=tmp_path / "empty", resolver=resolver)
    resolved = asyncio.run(bank.resolve("whoosh"))
    assert resolved.path == generated
    assert resolved.provider == "earshot"


def test_a_missing_sound_without_a_resolver_is_named(tmp_path: Path) -> None:
    bank = SfxBank(local_dir=tmp_path)
    with pytest.raises(SfxBankError, match="no local SFX"):
        asyncio.run(bank.resolve("hit"))


def test_a_resolver_that_finds_nothing_is_an_error(tmp_path: Path) -> None:
    async def resolver(sound: str) -> ResolvedSfx | None:
        return None

    bank = SfxBank(local_dir=tmp_path, resolver=resolver)
    with pytest.raises(SfxBankError, match="no provider in the chain"):
        asyncio.run(bank.resolve("hit"))


def test_a_blank_sound_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SfxBankError, match="must name a sound"):
        asyncio.run(SfxBank(local_dir=tmp_path).resolve("  "))


def test_credits_skip_locals_but_can_include_them(tmp_path: Path) -> None:
    local = tmp_path / "hit.mp3"
    local.write_bytes(b"mp3")
    resolved = [
        ResolvedSfx(
            path=local,
            provider="local",
            licence=LicenceRecord(title="hit", source_url=str(local), license="local"),
        ),
        ResolvedSfx(
            path=tmp_path / "whoosh.mp3",
            provider="freesound",
            licence=LicenceRecord(
                title="Whoosh",
                creator="A Creator",
                source_url="https://freesound.org/s/1",
                license="CC BY 4.0",
                license_url="https://creativecommons.org/licenses/by/4.0/",
            ),
        ),
    ]
    text = SfxBank.credits(resolved)
    assert "Whoosh by A Creator" in text
    assert "CC BY 4.0" in text
    assert "hit" not in text

    with_local = SfxBank.credits(resolved, include_local=True)
    assert "hit" in with_local


def test_write_credits_creates_the_file(tmp_path: Path) -> None:
    path = SfxBank.write_credits(tmp_path / "credits.txt", [])
    assert path.is_file()
    assert "none required" in path.read_text(encoding="utf-8")


def test_the_resolvers_are_tried_in_order(tmp_path: Path) -> None:
    calls: list[str] = []

    async def first(sound: str) -> ResolvedSfx | None:
        calls.append("first")
        return None

    async def second(sound: str) -> ResolvedSfx | None:
        calls.append("second")
        return ResolvedSfx(
            path=tmp_path / "found.mp3",
            provider="freesound",
            licence=LicenceRecord(title=sound, license="CC0 1.0"),
        )

    bank = SfxBank(local_dir=tmp_path, resolvers=[first, second])
    resolved = asyncio.run(bank.resolve("whoosh"))
    assert resolved.provider == "freesound"
    assert calls == ["first", "second"]


def test_a_chain_that_finds_nothing_is_an_error(tmp_path: Path) -> None:
    async def none(sound: str) -> ResolvedSfx | None:
        return None

    bank = SfxBank(local_dir=tmp_path, resolvers=[none, none])
    with pytest.raises(SfxBankError, match="no provider in the chain"):
        asyncio.run(bank.resolve("whoosh"))


def test_credits_do_not_repeat_the_same_sound() -> None:
    item = ResolvedSfx(
        path=Path("whoosh.mp3"),
        provider="freesound",
        licence=LicenceRecord(
            title="Whoosh",
            creator="A Creator",
            source_url="https://freesound.org/s/1",
            license="CC0 1.0",
        ),
    )
    text = SfxBank.credits([item, item, item])
    assert text.count("Whoosh by A Creator") == 1


def test_generated_sounds_need_no_credit() -> None:
    item = ResolvedSfx(
        path=Path("whoosh.wav"),
        provider="earshot_sfx",
        licence=LicenceRecord(title="whoosh_fast/crisp", license="generated"),
    )
    assert "whoosh_fast" not in SfxBank.credits([item])
    assert "whoosh_fast" in SfxBank.credits([item], include_local=True)
