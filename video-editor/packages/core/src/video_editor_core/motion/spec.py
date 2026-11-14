"""Motion primitives compile to deterministic, seekable CSS.

A primitive is a pure function: given its parameters it returns the
``@keyframes`` it needs, the ``animation`` declaration that applies them, any
extra static properties, and any markup the effect requires. Nothing here
touches the DOM, so every effect stays seekable through
``Animation.currentTime`` and HyperFrames' deterministic frame seek keeps
working.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

DEFAULT_EASE = "cubic-bezier(.2,.8,.2,1)"


@dataclass(frozen=True)
class MotionCSS:
    """The CSS one motion contributes to a layer."""

    keyframes: str = ""
    declaration: str = ""
    props: str = ""
    markup: str = ""

    def __add__(self, other: MotionCSS) -> MotionCSS:
        return MotionCSS(
            keyframes="\n".join(p for p in (self.keyframes, other.keyframes) if p),
            declaration=", ".join(p for p in (self.declaration, other.declaration) if p),
            props=" ".join(p for p in (self.props, other.props) if p),
            markup=self.markup + other.markup,
        )


def keyframes_name(name: str, params: Mapping[str, Any]) -> str:
    """A CSS identifier that is identical for identical name and parameters.

    Duration and easing are deliberately not part of the key: they live in the
    ``animation`` declaration, while keyframes stay percentage based, so ten
    staggered cells sharing one move emit a single ``@keyframes`` block.
    """
    payload = json.dumps([name, dict(params)], sort_keys=True, default=str)
    return f"ve_{name}_{hashlib.sha1(payload.encode()).hexdigest()[:8]}"
