"""Resolve a cue's sound name to a file, with its licence.

The bank is the seam between the cue table and the sources:

1. **Local files win.** A hand-picked sound (including a Pixabay download) in
   ``assets/sfx/`` is used without a network call and recorded as ``local``.
2. **An ordered resolver chain** handles the automated tier. The engine does
   not import earshot or an HTTP client here; the caller injects the chain
   (earshot over MCP -> ElevenLabs -> Freesound -> Openverse) so the bank stays
   testable and the provider decision lives in one place.

Every resolved sound carries a :class:`LicenceRecord`; :meth:`SfxBank.credits`
turns the records into the text that belongs in a video description.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

#: Extensions a local hand-picked sound may use, in preference order.
LOCAL_EXTENSIONS: tuple[str, ...] = ("mp3", "wav", "ogg", "flac")


class SfxBankError(RuntimeError):
    """No sound could be resolved for a cue."""


@dataclass(frozen=True)
class LicenceRecord:
    """Provenance for one sound; all fields are optional for local files."""

    title: str = ""
    creator: str = ""
    source_url: str = ""
    license: str = ""
    license_url: str = ""

    def credit_line(self) -> str:
        parts = [self.title or "Untitled"]
        if self.creator:
            parts.append(f"by {self.creator}")
        if self.source_url:
            parts.append(f"({self.source_url})")
        if self.license:
            parts.append(f"licensed under {self.license}")
        return " ".join(parts)


@dataclass(frozen=True)
class ResolvedSfx:
    """A file for a cue plus where it came from."""

    path: Path
    provider: str
    licence: LicenceRecord = LicenceRecord()


#: Return a sound, or ``None`` when this tier has no match.
Resolver = Callable[[str], Awaitable[ResolvedSfx | None]]


class SfxBank:
    """Local-first sound resolution with one injected network resolver."""

    def __init__(
        self,
        *,
        local_dir: Path | str | None = None,
        resolver: Resolver | None = None,
        resolvers: Sequence[Resolver] = (),
        extensions: Sequence[str] = LOCAL_EXTENSIONS,
    ) -> None:
        self.local_dir = Path(local_dir) if local_dir is not None else None
        # ``resolver`` is the one-provider shorthand; ``resolvers`` preserves
        # the order the caller wants (earshot first, free indexes last).
        self.resolvers: tuple[Resolver, ...] = (
            tuple(resolvers) if resolvers else ((resolver,) if resolver is not None else ())
        )
        self.extensions = tuple(extension.lstrip(".").lower() for extension in extensions)

    async def resolve(self, sound: str) -> ResolvedSfx:
        """Return the first local match, else ask the resolver."""
        name = sound.strip()
        if not name:
            raise SfxBankError("a cue must name a sound")
        local = self._local(name)
        if local is not None:
            return ResolvedSfx(
                path=local,
                provider="local",
                licence=LicenceRecord(title=name, source_url=str(local), license="local"),
            )
        if not self.resolvers:
            raise SfxBankError(
                f"no local SFX for {name!r} in {self.local_dir or '<no local dir>'}; "
                "no provider resolver is configured"
            )
        for resolver in self.resolvers:
            resolved = await resolver(name)
            if resolved is not None:
                return resolved
        raise SfxBankError(f"no provider in the chain found a sound for {name!r}")

    def _local(self, sound: str) -> Path | None:
        if self.local_dir is None:
            return None
        for extension in self.extensions:
            candidate = self.local_dir / f"{sound}.{extension}"
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def credits(resolved: Sequence[ResolvedSfx], *, include_local: bool = False) -> str:
        """A ``credits.txt`` body for the resolved sounds."""
        lines = ["Sound effects:"]
        seen: set[str] = set()
        for item in resolved:
            # Local picks and code-generated sounds need no attribution; the
            # stock providers carry a licence that does.
            if not include_local and (
                item.provider == "local" or item.licence.license == "generated"
            ):
                continue
            line = item.licence.credit_line()
            if line in seen:
                continue
            seen.add(line)
            lines.append(f"- {line}")
        if len(lines) == 1:
            lines.append("- none required")
        return "\n".join(lines) + "\n"

    @classmethod
    def write_credits(
        cls,
        path: Path | str,
        resolved: Sequence[ResolvedSfx],
        *,
        include_local: bool = False,
    ) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(cls.credits(resolved, include_local=include_local), encoding="utf-8")
        return target
