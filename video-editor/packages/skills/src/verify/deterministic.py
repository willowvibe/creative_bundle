"""Deterministic requirement checks that need no LLM."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from video_editor_ffmpeg.probe import MediaInfo

_ASPECT_RATIOS: dict[str, float] = {
    "16:9": 16 / 9,
    "9:16": 9 / 16,
    "1:1": 1.0,
    "4:3": 4 / 3,
}

_FILLER_WORDS: frozenset[str] = frozenset(
    {
        "um",
        "umm",
        "uh",
        "uuh",
        "ah",
        "aah",
        "eh",
        "er",
        "erm",
        "like",
        "you know",
        "so",
        "actually",
        "basically",
        "literally",
        "sort of",
        "kind of",
        "i mean",
    }
)


def check_duration(
    info: MediaInfo,
    min_seconds: float | None,
    max_seconds: float | None,
) -> tuple[str, str, Any]:
    """Return (result, message, actual) for a duration-range check."""
    if info.duration is None:
        return "FAIL", "could not determine duration", None
    duration = info.duration
    if min_seconds is not None and duration < min_seconds:
        return "FAIL", f"duration {duration:.2f}s < min {min_seconds:.2f}s", duration
    if max_seconds is not None and duration > max_seconds:
        return "FAIL", f"duration {duration:.2f}s > max {max_seconds:.2f}s", duration
    return "PASS", f"duration {duration:.2f}s within range", duration


def check_aspect_ratio(info: MediaInfo, expected_label: str) -> tuple[str, str, Any]:
    """Return (result, message, actual) for an aspect-ratio check."""
    expected = _ASPECT_RATIOS.get(expected_label)
    if expected is None:
        return "UNSURE", f"unknown aspect ratio label {expected_label!r}", None
    if info.width == 0 or info.height == 0:
        return "FAIL", "missing video dimensions", {"width": info.width, "height": info.height}
    actual = info.width / info.height
    # Use a tolerance because actual output may be slightly off after cropping.
    if abs(actual - expected) <= 0.05:
        return "PASS", f"aspect {actual:.4f} matches {expected_label}", actual
    return "FAIL", f"aspect {actual:.4f} does not match {expected_label}", actual


def _load_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def check_required_element(
    element: str,
    work_dir: Path,
    info: MediaInfo,
) -> tuple[str, str, Any]:
    """Return (result, message, actual) for a single required element."""
    if element == "captions":
        captions_video = work_dir / "captions" / "with_captions.mp4"
        captions_json = work_dir / "captions" / "caption_segments.json"
        srt = work_dir / "captions" / "captions.srt"
        present = captions_video.exists() or captions_json.exists() or srt.exists()
        return (
            "PASS" if present else "FAIL",
            "caption artifact found" if present else "no caption artifact found",
            present,
        )

    if element == "intro_hook":
        # Heuristic: first scene/segment starts at 0 and has non-empty narration.
        plan = _load_json(work_dir / "plan" / "scene_plan.json")
        if plan and isinstance(plan.get("scenes"), list) and plan["scenes"]:
            first = plan["scenes"][0]
            ok = first.get("duration_seconds", 0) > 0
            return (
                "PASS" if ok else "FAIL",
                "first scene present" if ok else "first scene empty",
                first,
            )
        edl = _load_json(work_dir / "rough_cut" / "edl.json")
        if edl and isinstance(edl.get("segments"), list) and edl["segments"]:
            first = edl["segments"][0]
            ok = first.get("source_start", 0) == 0.0
            return (
                "PASS" if ok else "FAIL",
                "first segment at 0" if ok else "first segment not at 0",
                first,
            )
        return "FAIL", "no plan or EDL found", None

    if element == "logo":
        brand_paths = [
            work_dir.parents[1] / "presets" / "brand.json",
            work_dir.parents[1] / "presets" / "brands" / f"{work_dir.name}.json",
            work_dir / "brand.json",
        ]
        brand = next((p for p in brand_paths if p.exists()), None)
        if not brand:
            return "FAIL", "no brand.json found", None
        data = _load_json(brand)
        logo = data.get("logo") if isinstance(data, dict) else None
        has_logo = bool(logo) and bool(logo.get("path") if isinstance(logo, dict) else logo)
        return (
            "PASS" if has_logo else "FAIL",
            "logo configured" if has_logo else "logo missing",
            logo,
        )

    if element == "cta_end_card":
        # Heuristic: last scene type is closing/cta or last segment label contains cta.
        plan = _load_json(work_dir / "plan" / "scene_plan.json")
        if plan and isinstance(plan.get("scenes"), list) and plan["scenes"]:
            last = plan["scenes"][-1]
            t = last.get("type", "").lower()
            ok = t in {"closing", "cta"}
            return "PASS" if ok else "UNSURE", f"last scene type: {t}", t
        edl = _load_json(work_dir / "rough_cut" / "edl.json")
        if edl and isinstance(edl.get("segments"), list) and edl["segments"]:
            last = edl["segments"][-1]
            label = str(last.get("label", "")).lower()
            ok = "cta" in label or "call" in label
            return "PASS" if ok else "UNSURE", f"last segment label: {label}", label
        return "UNSURE", "no plan or EDL to check", None

    if element == "music":
        music_video = work_dir / "music" / "with_music.mp4"
        return (
            "PASS" if music_video.exists() else "FAIL",
            "music stage output found" if music_video.exists() else "no music stage output found",
            music_video.exists(),
        )

    if element == "sfx":
        sfx_video = work_dir / "broll" / "with_broll_sfx.mp4"
        sfx_map = work_dir / "sfx" / "sfx_map.json"
        present = sfx_video.exists() or sfx_map.exists()
        return (
            "PASS" if present else "FAIL",
            "SFX mix artifact found" if present else "no SFX mix artifact found",
            present,
        )

    return "UNSURE", f"unknown required element {element!r}", None


def check_forbidden_element(
    element: str,
    work_dir: Path,
    info: MediaInfo,
) -> tuple[str, str, Any]:
    """Return (result, message, actual) for a single forbidden element."""
    if element == "background_music":
        music_video = work_dir / "music" / "with_music.mp4"
        if music_video.exists():
            return "FAIL", "music stage output exists", str(music_video)
        return "PASS", "no music stage output found", False

    if element == "broll_over_face":
        # Best-effort heuristic: if there are broll inserts and the source is a
        # talking-head video, we cannot reliably detect face coverage without a
        # vision model. Return UNSURE rather than invent a verdict.
        broll = _load_json(work_dir / "plan" / "broll_plan.json")
        if not broll:
            broll = _load_json(work_dir / "broll" / "broll_inserts.json")
        inserts = broll if isinstance(broll, list) else []
        if inserts:
            return (
                "UNSURE",
                "broll inserts exist; cannot verify face coverage without vision",
                len(inserts),
            )
        return "PASS", "no broll inserts found", 0

    if element == "captions":
        captions_video = work_dir / "captions" / "with_captions.mp4"
        return (
            "FAIL" if captions_video.exists() else "PASS",
            "captions found (forbidden)" if captions_video.exists() else "no captions found",
            captions_video.exists(),
        )

    return "UNSURE", f"unknown forbidden element {element!r}", None


def check_must_mention(
    phrases: list[str], transcript: list[dict[str, Any]] | None
) -> tuple[str, str, Any]:
    """Return (result, message, actual) checking transcript for required mentions."""
    if not phrases:
        return "PASS", "no phrases to check", []
    if transcript is None:
        return "UNSURE", "no transcript available", []
    text = " ".join(str(w.get("word", "")).lower() for w in transcript)
    missing = [p for p in phrases if p.lower() not in text]
    if missing:
        return "FAIL", f"missing mentions: {missing}", missing
    return "PASS", "all required phrases mentioned", phrases


def check_must_keep_segments(segment_ids: list[str], plan_or_edl: Any) -> tuple[str, str, Any]:
    """Return (result, message, actual) checking that segments still exist."""
    if not segment_ids:
        return "PASS", "no segments to keep", []
    if not plan_or_edl:
        return "UNSURE", "no plan/EDL available", []
    found: list[str] = []
    data = plan_or_edl if isinstance(plan_or_edl, dict) else {}
    if isinstance(data.get("scenes"), list):
        found = [s.get("id") for s in data["scenes"] if s.get("id") in segment_ids]
    elif isinstance(data.get("segments"), list):
        found = [s.get("id") for s in data["segments"] if s.get("id") in segment_ids]
    missing = [sid for sid in segment_ids if sid not in found]
    if missing:
        return "FAIL", f"missing segments: {missing}", missing
    return "PASS", f"kept segments: {found}", found


def check_filler_words(transcript: list[dict[str, Any]] | None) -> tuple[str, str, Any]:
    """Return (result, message, actual) checking for remaining filler words."""
    if transcript is None:
        return "UNSURE", "no transcript available", []
    words = [str(w.get("word", "")).lower().strip(".,!?;:") for w in transcript]
    found = [w for w in words if w in _FILLER_WORDS]
    if found:
        # De-duplicate while preserving order.
        unique: list[str] = []
        seen: set[str] = set()
        for w in found:
            if w not in seen:
                seen.add(w)
                unique.append(w)
        return "FAIL", f"filler words still present: {unique}", unique
    return "PASS", "no filler words detected", []
