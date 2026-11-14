"""The explainer's QA gate: measured build → pass/fail table.

Every value is measured from the file that ships, not recomputed from the
plan, so a passing check says something about the artifact. The thresholds are
the ones the saju build settled on, plus the format's own 30-90 s bound, used
when the script names no ``target_seconds``.

The gate is fail-closed: a check whose measurement is missing reports
``ok=False`` with the reason, so a partial build can be evaluated without ever
passing by accident.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from video_editor_core.scene_plan_composition import canvas_for_aspect
from video_editor_tools.versions import collect_tool_versions

LOUDNESS_TARGET_LUFS = -14.0
LOUDNESS_TOLERANCE_LU = 1.0
TRUE_PEAK_CEILING_DB = -1.0
#: A scene normally starts ``LEAD_IN_SECONDS`` (0.12 s) before its first word,
#: but ``MIN_SCENE_SECONDS`` can push a boundary later (and an interior tail
#: clamps it earlier), so the check leaves room for both instead of demanding
#: an exact match. Words are not guaranteed to sit inside a scene's window.
SCENE_START_TOLERANCE_SECONDS = 0.35
CAPTION_COVERAGE_FLOOR = 0.98
MAX_SILENT_GAP_SECONDS = 1.0
TAIL_MIN_SECONDS = 0.8
TAIL_MAX_SECONDS = 2.0
FORMAT_DURATION_BOUNDS = (30.0, 90.0)
DURATION_TOLERANCE = 0.10
EXPECTED_FPS = 30.0
#: Relative tolerance on the shipped width/height ratio. Encoders round odd
#: dimensions, so an exact pixel match is stricter than the format requires.
ASPECT_RATIO_TOLERANCE = 0.01


@dataclass(frozen=True)
class QaCheck:
    name: str
    ok: bool
    detail: str


@dataclass
class QaInputs:
    """Everything the gate needs, already measured from the finished file."""

    aspect_ratio: str = "9:16"
    width: int = 0
    height: int = 0
    fps: float = EXPECTED_FPS
    duration: float = 0.0
    loudness_lufs: float = LOUDNESS_TARGET_LUFS
    true_peak_db: float = TRUE_PEAK_CEILING_DB - 0.5
    target_seconds: float | None = None
    scene_starts: list[float] = field(default_factory=list)
    first_word_starts: list[float] = field(default_factory=list)
    caption_coverage: float = 1.0
    still_scenes: int = 0
    still_renders: int = 0
    expected_still_renders: int = 0
    max_silent_gap: float = 0.0
    tail_seconds: float = 1.2
    #: Chapter packs have no closing card, so the tail window is not required.
    expect_tail: bool = True
    cost_usd: float = 0.0
    estimate_usd: float = 0.0
    #: Asset ids whose cached provider is a ``stub_*`` placeholder (#281).
    stub_assets: tuple[str, ...] = ()
    #: True when the asset manager could not price/persist a call (#283).
    cost_tracking_degraded: bool = False
    cost_tracking_reasons: tuple[str, ...] = ()


def _canvas_check(inputs: QaInputs) -> QaCheck:
    """The shipped file's probed dimensions and fps against the plan.

    A missing probe is a failing value rather than a pass: the check has to
    measure the file that ships, so an unmeasured build must not clear it.
    """
    expected_width, expected_height = canvas_for_aspect(inputs.aspect_ratio)
    if inputs.width > 0 and inputs.height > 0:
        target = expected_width / expected_height
        delta = abs(inputs.width / inputs.height - target) / target
        aspect_ok = delta <= ASPECT_RATIO_TOLERANCE
        detail = (
            f"{inputs.width}x{inputs.height} vs "
            f"{expected_width}x{expected_height} {inputs.aspect_ratio} "
            f"({delta * 100:+.2f}%) @{inputs.fps:g}fps"
        )
    else:
        aspect_ok = False
        detail = (
            f"no probed dimensions (expected {expected_width}x{expected_height} "
            f"{inputs.aspect_ratio}) @{inputs.fps:g}fps"
        )
    return QaCheck(
        name="canvas",
        ok=aspect_ok and inputs.fps == EXPECTED_FPS,
        detail=detail,
    )


def _duration_check(inputs: QaInputs) -> QaCheck:
    if inputs.target_seconds:
        delta = abs(inputs.duration - inputs.target_seconds) / inputs.target_seconds
        return QaCheck(
            name="duration",
            ok=delta <= DURATION_TOLERANCE,
            detail=f"{inputs.duration:.1f}s (target {inputs.target_seconds:g}, "
            f"{delta * 100:+.1f}%)",
        )
    low, high = FORMAT_DURATION_BOUNDS
    return QaCheck(
        name="duration",
        ok=low <= inputs.duration <= high,
        detail=f"{inputs.duration:.1f}s (format bound {low:g}-{high:g}s)",
    )


def _scene_start_check(inputs: QaInputs) -> QaCheck:
    if not inputs.scene_starts or not inputs.first_word_starts:
        return QaCheck(
            name="scene_starts",
            ok=False,
            detail="scene starts or first words were not measured",
        )
    if len(inputs.scene_starts) != len(inputs.first_word_starts):
        return QaCheck(
            name="scene_starts",
            ok=False,
            detail=f"{len(inputs.scene_starts)} starts for "
            f"{len(inputs.first_word_starts)} first words",
        )
    errors = [
        abs(start - word)
        for start, word in zip(inputs.scene_starts, inputs.first_word_starts, strict=True)
    ]
    within = sum(1 for error in errors if error <= SCENE_START_TOLERANCE_SECONDS)
    worst = max(errors)
    return QaCheck(
        name="scene_starts",
        ok=within == len(errors),
        detail=f"{within}/{len(errors)} within "
        f"{SCENE_START_TOLERANCE_SECONDS:g}s (worst {worst:.2f}s)",
    )


def evaluate_qa(inputs: QaInputs, extra: list[QaCheck] | None = None) -> list[QaCheck]:
    """Run every check; the run fails unless all of them pass.

    ``extra`` carries checks measured outside this module (the composition
    gates), so one report covers the whole build.
    """
    checks = [
        _canvas_check(inputs),
        QaCheck(
            name="loudness",
            ok=abs(inputs.loudness_lufs - LOUDNESS_TARGET_LUFS) <= LOUDNESS_TOLERANCE_LU,
            detail=f"{inputs.loudness_lufs:.1f} LUFS (target {LOUDNESS_TARGET_LUFS:g})",
        ),
        QaCheck(
            name="true_peak",
            ok=inputs.true_peak_db <= TRUE_PEAK_CEILING_DB,
            detail=f"{inputs.true_peak_db:.1f} dBTP (ceiling {TRUE_PEAK_CEILING_DB:g})",
        ),
        _duration_check(inputs),
        _scene_start_check(inputs),
        QaCheck(
            name="captions",
            ok=inputs.caption_coverage >= CAPTION_COVERAGE_FLOOR,
            detail=f"{inputs.caption_coverage * 100:.1f}% of words captioned",
        ),
        QaCheck(
            name="stills",
            ok=inputs.still_renders == inputs.expected_still_renders,
            detail=f"{inputs.still_scenes} scenes -> {inputs.still_renders} renders "
            f"(report says {inputs.expected_still_renders})",
        ),
        QaCheck(
            name="silent_gaps",
            ok=inputs.max_silent_gap <= MAX_SILENT_GAP_SECONDS,
            detail=f"longest gap {inputs.max_silent_gap:.2f}s",
        ),
        QaCheck(
            name="tail",
            ok=(
                not inputs.expect_tail
                or TAIL_MIN_SECONDS <= inputs.tail_seconds <= TAIL_MAX_SECONDS
            ),
            detail=(
                f"{inputs.tail_seconds:.1f}s after the last word"
                if inputs.expect_tail
                else "no end card"
            ),
        ),
        QaCheck(
            name="cost",
            ok=inputs.cost_usd <= inputs.estimate_usd,
            detail=f"${inputs.cost_usd:.2f} of ${inputs.estimate_usd:.2f} estimate",
        ),
        QaCheck(
            name="no_stub_assets",
            ok=not inputs.stub_assets,
            detail=(
                "every asset came from a real provider"
                if not inputs.stub_assets
                else "placeholder provider(s): " + ", ".join(inputs.stub_assets)
            ),
        ),
        QaCheck(
            name="cost_tracking",
            ok=True,
            detail=(
                "complete"
                if not inputs.cost_tracking_degraded
                else "WARNING: degraded - " + "; ".join(inputs.cost_tracking_reasons)
            ),
        ),
    ]
    if extra:
        checks.extend(extra)
    return checks


def qa_report(inputs: QaInputs, checks: list[QaCheck]) -> dict[str, Any]:
    """The JSON payload written to ``build/qa.json``."""
    warnings: list[str] = []
    if inputs.stub_assets:
        warnings.append("placeholder asset provider(s): " + ", ".join(inputs.stub_assets))
    if inputs.cost_tracking_degraded:
        warnings.extend(inputs.cost_tracking_reasons)
    return {
        "passed": all(check.ok for check in checks),
        "checks": [asdict(check) for check in checks],
        "warnings": warnings,
        "tool_versions": collect_tool_versions(),
        "inputs": asdict(inputs),
    }


def format_qa(checks: list[QaCheck]) -> str:
    """One line per check, for the terminal."""
    return "\n".join(
        f"{check.name:<13} {'PASS' if check.ok else 'FAIL'}  {check.detail}" for check in checks
    )
