"""Decide which stills can show the same rendered image.

The plan-level reuse mechanism already exists (#271): a scene whose
``props.image_scene`` names another scene shows that scene's still, the cost
gate skips its IMAGE request, and the renderer shares the file. This module
supplies the *decision*.

Two still scenes join a cluster when the Jaccard overlap of their word sets is
at least the script's ``reuse_threshold``. The word set is built from the
``image`` prompt **and** the narration, because a reprise usually restates its
subject in words rather than in the prompt. A ``subject:`` tag overrides the
score: tagged scenes merge with the same tag, and never with a different or
untagged scene, so "two racks that look alike but mean different things" is
one line in the script away from splitting.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from video_editor_pipelines.explainer_script import DEFAULT_REUSE_THRESHOLD

DEFAULT_THRESHOLD = DEFAULT_REUSE_THRESHOLD
DEFAULT_CREDITS_PER_RENDER = 5
DEFAULT_USD_PER_CREDIT = 0.005

_WORD_RE = re.compile(r"[a-z0-9$%][a-z0-9$%'_-]*")

# A trailing "es" is plural marker only after these stems. An "s"-final stem is
# ambiguous ("buses" from "bus" vs "houses" from "house"); only an "ss" stem is
# unambiguous, so a bare "s" is handled by the plain trailing-"s" rule.
_ES_STEM_ENDINGS = ("x", "z", "ch", "sh", "ss")

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "for",
        "from",
        "has",
        "have",
        "her",
        "his",
        "in",
        "into",
        "is",
        "it",
        "its",
        "itself",
        "of",
        "on",
        "one",
        "or",
        "our",
        "out",
        "over",
        "so",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "to",
        "too",
        "under",
        "up",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "with",
        "you",
        "your",
    }
)


def _fold_plural(token: str) -> str:
    """Fold a trailing plural so "racks"/"rack" compare equal.

    The trailing "es" is stripped only when the stem is a sibilant ending
    ("boxes", "watches", "classes"); elsewhere the "e" belongs to the word
    itself ("machines", "houses", "cares"), so only the "s" is removed.
    """
    if token.endswith("ies") and len(token) > 4:
        return f"{token[:-3]}y"
    if token.endswith("es") and len(token) > 4:
        stem = token[:-2]
        if stem.endswith(_ES_STEM_ENDINGS):
            return stem
    if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        return token[:-1]
    return token


def subject_words(scene: Any) -> set[str]:
    """The comparable word set for a still scene: prompt plus narration."""
    text = f"{getattr(scene, 'image', '')} {getattr(scene, 'narration', '')}".lower()
    words: set[str] = set()
    for match in _WORD_RE.finditer(text):
        token = match.group(0).strip("'_-")
        if not token or token in _STOPWORDS:
            continue
        if len(token) == 1 and not token.isdigit():
            continue
        words.add(_fold_plural(token))
    return words


def jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    """Overlap over union; an empty side never matches."""
    left_set, right_set = set(left), set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


@dataclass
class ReuseCluster:
    """Still scenes that render once; the owner's image is shown by the rest."""

    owner: str
    scenes: list[str] = field(default_factory=list)
    words: set[str] = field(default_factory=set)
    subject: str = ""
    overlap: float = 0.0


@dataclass
class ReusePlan:
    """The clustering result, in script order."""

    clusters: list[ReuseCluster] = field(default_factory=list)
    unclustered: list[str] = field(default_factory=list)
    #: Stills a composite scene asks for beyond its first (``image_b``). They
    #: are not reuse candidates, but they are paid renders the QA count must
    #: include.
    extra_renders: int = 0

    @property
    def still_scenes(self) -> int:
        return sum(len(cluster.scenes) for cluster in self.clusters) + len(self.unclustered)

    @property
    def base_renders(self) -> int:
        return len(self.clusters) + len(self.unclustered)

    @property
    def renders(self) -> int:
        return self.base_renders + self.extra_renders

    @property
    def saved(self) -> int:
        return max(0, self.still_scenes - self.base_renders)


def plan_still_reuse(
    scenes: Sequence[Any],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    enabled: bool = True,
) -> ReusePlan:
    """Cluster the still scenes of ``scenes``; deterministic for a given input."""
    groups: list[ReuseCluster] = []
    extra_renders = 0
    for scene in scenes:
        visual = getattr(scene, "visual", "")
        image = str(getattr(scene, "image", "") or "").strip()
        if visual == "composite" and str(getattr(scene, "image_b", "") or "").strip():
            extra_renders += 1
        if visual != "still" and not (visual == "composite" and image):
            continue
        if not enabled:
            groups.append(
                ReuseCluster(owner=scene.id, scenes=[scene.id], words=subject_words(scene))
            )
            continue
        words = subject_words(scene)
        subject = str(getattr(scene, "subject", "") or "").strip().lower()
        chosen: ReuseCluster | None = None
        for group in groups:
            if subject or group.subject:
                join = bool(subject) and subject == group.subject
                overlap = jaccard(words, group.words)
            else:
                overlap = jaccard(words, group.words)
                join = overlap >= threshold
            if join:
                group.scenes.append(scene.id)
                group.words |= words
                group.overlap = max(group.overlap, overlap)
                chosen = group
                break
        if chosen is None:
            groups.append(
                ReuseCluster(owner=scene.id, scenes=[scene.id], words=words, subject=subject)
            )

    reuse = ReusePlan(extra_renders=extra_renders)
    for group in groups:
        if len(group.scenes) > 1:
            reuse.clusters.append(group)
        else:
            reuse.unclustered.extend(group.scenes)
    return reuse


def apply_reuse(plan: dict[str, Any], reuse: ReusePlan) -> None:
    """Write ``props.image_scene`` on every cluster member but the owner."""
    by_id = {
        str(scene.get("id")): scene
        for scene in plan.get("scenes", []) or []
        if isinstance(scene, dict)
    }
    for cluster in reuse.clusters:
        for scene_id in cluster.scenes[1:]:
            scene = by_id.get(scene_id)
            if scene is None:
                continue
            props = scene.setdefault("props", {})
            if isinstance(props, dict):
                props["image_scene"] = cluster.owner


def format_reuse_report(
    reuse: ReusePlan,
    *,
    credits_per_render: int = DEFAULT_CREDITS_PER_RENDER,
    usd_per_credit: float = DEFAULT_USD_PER_CREDIT,
) -> str:
    """Human-readable cluster table, printed before the purchase."""
    saved_usd = reuse.saved * credits_per_render * usd_per_credit
    lines = [
        f"still reuse: {reuse.still_scenes} still scenes -> {reuse.renders} renders "
        f"({reuse.saved} saved, {reuse.saved * credits_per_render} credits / "
        f"${saved_usd:.3f})"
    ]
    for cluster in reuse.clusters:
        scenes = ", ".join(cluster.scenes)
        lines.append(
            f"  cluster {cluster.owner:<10} scenes {scenes:<24} "
            f"overlap {cluster.overlap:.2f}   -> render of {cluster.owner}"
        )
    if reuse.unclustered:
        scenes = ", ".join(reuse.unclustered)
        lines.append(f"  unclustered          scenes {scenes:<24}   own render")
    return "\n".join(lines)
