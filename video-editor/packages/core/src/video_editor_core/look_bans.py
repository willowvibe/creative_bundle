"""Deterministic anti-generic checks over the Composition IR (P4.1).

The reference workflow's harness bans generic output in prose ("no centred text
on a gradient"). This module turns the measurable part of that rule into IR
checks so it can gate a build without a model call:

* every layer uses the same motion primitive;
* every text layer uses one font weight;
* text is centred on a full-bleed gradient in two or more scenes;
* every scene has the same length (within 10 %);
* a pack asked for 2.5D layers but the composition has no depth or parallax.

The checks are advisory by default; `gather_composition_qa(strict_look=True)`
turns a finding into a failing gate.
"""

from __future__ import annotations

from dataclasses import dataclass

from video_editor_core.composition import Composition, CompositionLayer

_TEXT_KINDS = {"text", "rich_text"}
_DEPTH_MARKERS = ("__depth", "__fg", "__bg", "__near")


@dataclass(frozen=True)
class LookBanReport:
    """Findings from the anti-generic checks (empty means clean)."""

    findings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.findings

    def as_dict(self) -> dict[str, object]:
        return {"ok": self.ok, "findings": list(self.findings)}


def _text_layers(composition: Composition) -> list[CompositionLayer]:
    return [
        layer for scene in composition.scenes for layer in scene.layers if layer.kind in _TEXT_KINDS
    ]


def _centred(layer: CompositionLayer, *, width: int, height: int) -> bool:
    centre_x = layer.rect.x + layer.rect.width / 2
    centre_y = layer.rect.y + layer.rect.height / 2
    return abs(centre_x - width / 2) <= 0.05 * width and abs(centre_y - height / 2) <= 0.10 * height


def look_ban_findings(
    composition: Composition,
    *,
    expect_depth: bool = False,
) -> tuple[str, ...]:
    """Return the anti-generic findings for ``composition``."""
    findings: list[str] = []
    motions = [
        motion.name
        for scene in composition.scenes
        for layer in scene.layers
        for motion in layer.motions
    ]
    if len(motions) >= 4 and len(set(motions)) == 1:
        findings.append(f"every layer uses the same motion ({motions[0]})")

    weights = {
        int(layer.style["font_weight"])
        for layer in _text_layers(composition)
        if layer.style.get("font_weight") is not None
    }
    if len(_text_layers(composition)) >= 3 and len(weights) == 1:
        findings.append(f"every text layer uses one font weight ({next(iter(weights))})")

    canvas_area = composition.canvas.width * composition.canvas.height
    centred_on_gradient = 0
    for scene in composition.scenes:
        full_bleed = any(
            layer.kind == "shape"
            and layer.content.gradient
            and layer.rect.width * layer.rect.height >= 0.85 * canvas_area
            for layer in scene.layers
        )
        if not full_bleed:
            continue
        if any(
            _centred(layer, width=composition.canvas.width, height=composition.canvas.height)
            for layer in scene.layers
            if layer.kind in _TEXT_KINDS
        ):
            centred_on_gradient += 1
    if centred_on_gradient >= 2:
        findings.append(f"{centred_on_gradient} scenes centre text on a full-bleed gradient")

    lengths = [scene.end - scene.start for scene in composition.scenes]
    if len(lengths) >= 3:
        mean = sum(lengths) / len(lengths)
        if mean > 0 and (max(lengths) - min(lengths)) / mean <= 0.10:
            findings.append(
                f"all {len(lengths)} scenes are the same length "
                f"({min(lengths):.2f}–{max(lengths):.2f}s)"
            )

    if expect_depth:
        has_depth = any(
            any(marker in (layer.content.source or "") for marker in _DEPTH_MARKERS)
            or any(motion.name == "parallax" for motion in layer.motions)
            for scene in composition.scenes
            for layer in scene.layers
        )
        if not has_depth:
            findings.append("the pack asks for 2.5D layers but no depth/parallax layer is present")

    return tuple(findings)
