from __future__ import annotations

import json

from video_editor_pipelines.explainer_qa import (
    SCENE_START_TOLERANCE_SECONDS,
    QaCheck,
    QaInputs,
    evaluate_qa,
    format_qa,
    qa_report,
)


def good_inputs(**overrides: object) -> QaInputs:
    values: dict[str, object] = {
        "aspect_ratio": "9:16",
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
        "duration": 50.0,
        "loudness_lufs": -14.0,
        "true_peak_db": -1.4,
        "target_seconds": 50.0,
        "scene_starts": [0.0, 5.0, 10.0],
        "first_word_starts": [0.1, 5.1, 10.2],
        "caption_coverage": 0.995,
        "still_scenes": 3,
        "still_renders": 2,
        "expected_still_renders": 2,
        "max_silent_gap": 0.4,
        "tail_seconds": 1.2,
        "cost_usd": 0.19,
        "estimate_usd": 0.25,
    }
    values.update(overrides)
    return QaInputs(**values)


def failed(checks: list[QaCheck]) -> set[str]:
    return {check.name for check in checks if not check.ok}


def test_a_good_build_passes_every_check():
    checks = evaluate_qa(good_inputs())
    assert [check.name for check in checks] == [
        "canvas",
        "loudness",
        "true_peak",
        "duration",
        "scene_starts",
        "captions",
        "stills",
        "silent_gaps",
        "tail",
        "cost",
        "no_stub_assets",
        "cost_tracking",
    ]
    assert failed(checks) == set()


def test_loudness_beyond_one_lu_fails():
    assert failed(evaluate_qa(good_inputs(loudness_lufs=-16.2))) == {"loudness"}


def test_true_peak_above_minus_one_fails():
    assert failed(evaluate_qa(good_inputs(true_peak_db=-0.6))) == {"true_peak"}


def test_duration_uses_the_format_bound_when_no_target_is_set():
    assert failed(evaluate_qa(good_inputs(target_seconds=None, duration=20.0))) == {"duration"}
    assert failed(evaluate_qa(good_inputs(target_seconds=None, duration=55.0))) == set()


def test_duration_more_than_ten_percent_off_target_fails():
    assert failed(evaluate_qa(good_inputs(duration=58.0))) == {"duration"}


def test_a_scene_start_far_from_any_word_fails():
    checks = evaluate_qa(good_inputs(first_word_starts=[0.1, 5.1, 10.9]))
    assert failed(checks) == {"scene_starts"}


def test_caption_coverage_below_98_percent_fails():
    assert failed(evaluate_qa(good_inputs(caption_coverage=0.95))) == {"captions"}


def test_still_render_count_must_match_the_reuse_report():
    assert failed(evaluate_qa(good_inputs(still_renders=3))) == {"stills"}


def test_a_long_silent_gap_or_a_short_tail_fails():
    checks = evaluate_qa(good_inputs(max_silent_gap=1.4, tail_seconds=0.4))
    assert failed(checks) == {"silent_gaps", "tail"}


def test_cost_over_the_estimate_fails():
    assert failed(evaluate_qa(good_inputs(cost_usd=0.31))) == {"cost"}


def test_report_is_json_serialisable_and_names_the_failures():
    inputs = good_inputs(loudness_lufs=-17.0)
    checks = evaluate_qa(inputs)
    payload = qa_report(inputs, checks)
    assert payload["passed"] is False
    assert json.loads(json.dumps(payload))["checks"][1]["name"] == "loudness"
    assert "FAIL" in format_qa(checks)


def test_canvas_must_run_at_thirty_fps():
    assert failed(evaluate_qa(good_inputs(fps=25.0))) == {"canvas"}


def test_canvas_measures_the_probed_dimensions_against_the_plan_aspect():
    # A 16:9 probe under a 9:16 plan is a different shipped file.
    assert failed(evaluate_qa(good_inputs(width=1920, height=1080))) == {"canvas"}
    assert failed(evaluate_qa(good_inputs(width=1080, height=1920))) == set()
    # No probe means no measurement: the check must fail closed.
    assert failed(evaluate_qa(good_inputs(width=0, height=0))) == {"canvas"}


def test_canvas_accepts_a_small_rounding_difference():
    # 1078x1920 is 0.19% off the target ratio; ffmpeg can round even sizes.
    assert failed(evaluate_qa(good_inputs(width=1078, height=1920))) == set()


def test_missing_scene_timings_fail_rather_than_pass():
    checks = evaluate_qa(good_inputs(scene_starts=[], first_word_starts=[]))
    assert failed(checks) == {"scene_starts"}
    check = next(check for check in checks if check.name == "scene_starts")
    assert check.detail


def test_mismatched_scene_and_word_counts_fail():
    assert failed(evaluate_qa(good_inputs(first_word_starts=[0.1, 5.1]))) == {"scene_starts"}


def test_a_partial_build_never_passes():
    # Every field is defaulted so a partial build can still be evaluated, but
    # the checks with nothing measured must fail rather than pass silently.
    assert failed(evaluate_qa(QaInputs())) == {"canvas", "duration", "scene_starts"}


def test_thresholds_are_inclusive_at_their_bounds():
    assert failed(evaluate_qa(good_inputs(loudness_lufs=-15.0))) == set()
    assert failed(evaluate_qa(good_inputs(true_peak_db=-1.0))) == set()
    assert failed(evaluate_qa(good_inputs(duration=55.0))) == set()
    assert failed(evaluate_qa(good_inputs(tail_seconds=0.8))) == set()
    assert failed(evaluate_qa(good_inputs(tail_seconds=2.0))) == set()
    assert failed(evaluate_qa(good_inputs(tail_seconds=2.01))) == {"tail"}
    assert failed(evaluate_qa(good_inputs(cost_usd=0.25, estimate_usd=0.25))) == set()


def test_scene_start_tolerance_is_inclusive():
    at_limit = 10.0 + SCENE_START_TOLERANCE_SECONDS
    assert failed(evaluate_qa(good_inputs(first_word_starts=[0.1, 5.1, at_limit]))) == set()
    past_limit = at_limit + 0.01
    assert failed(evaluate_qa(good_inputs(first_word_starts=[0.1, 5.1, past_limit]))) == {
        "scene_starts"
    }


def test_format_qa_prints_one_line_per_check():
    checks = evaluate_qa(good_inputs())
    lines = format_qa(checks).splitlines()
    assert len(lines) == len(checks)
    assert all("PASS" in line for line in lines)


def test_a_failed_extra_check_fails_the_run():
    inputs = good_inputs()
    baseline = evaluate_qa(inputs)
    checks = evaluate_qa(inputs, [QaCheck("composition_gates", False, "boom")])
    assert "composition_gates" in [check.name for check in checks]
    assert not all(check.ok for check in checks)
    extra = next(check for check in checks if check.name == "composition_gates")
    assert extra.detail == "boom"
    # No extra checks: the plain call is byte-for-byte the same list.
    assert evaluate_qa(inputs) == baseline


def test_placeholder_assets_fail_the_gate():
    checks = evaluate_qa(good_inputs(stub_assets=("still_rat",)))
    assert failed(checks) == {"no_stub_assets"}


def test_degraded_cost_tracking_is_a_warning_not_a_failure():
    checks = evaluate_qa(
        good_inputs(cost_tracking_degraded=True, cost_tracking_reasons=("pricing failed",))
    )
    assert failed(checks) == set()
    check = next(check for check in checks if check.name == "cost_tracking")
    assert "WARNING" in check.detail and "pricing failed" in check.detail


def test_qa_json_surfaces_warnings():
    report = qa_report(
        good_inputs(
            stub_assets=("still_rat",),
            cost_tracking_degraded=True,
            cost_tracking_reasons=("pricing failed",),
        ),
        evaluate_qa(good_inputs(stub_assets=("still_rat",))),
    )
    assert any("still_rat" in warning for warning in report["warnings"])
    assert any("pricing failed" in warning for warning in report["warnings"])
