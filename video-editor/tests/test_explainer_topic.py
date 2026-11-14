"""Tests for drafting ``script.md`` from a one-line topic.

The draft is the only LLM call in the explainer pipeline: everything after it
(plan, price, render) is deterministic. These tests use a fake router and
never touch the network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner
from video_editor_core.models.scene_plan import Scene, ScenePlan
from video_editor_pipelines.explainer_script import parse_explainer_script
from video_editor_pipelines.explainer_topic import (
    DRAFT_ATTEMPTS,
    TopicDraftError,
    _validate_draft,
    draft_script_from_topic,
    scene_plan_to_script,
)


def sample_plan() -> ScenePlan:
    return ScenePlan(
        job_id="topic_draft",
        script="Why Google bought Wiz",
        aspect_ratio="9:16",
        scenes=[
            Scene(
                id="scene_1",
                type="narration",
                duration_seconds=4.0,
                narration="Thirty two billion dollars.",
                visual_description="a giant number on warm cream paper",
            ),
            Scene(
                id="scene_2",
                type="narration",
                duration_seconds=4.0,
                narration="Wiz watches the whole cloud.",
                visual_description="",
            ),
        ],
    )


def test_every_scene_becomes_a_still_block():
    text = scene_plan_to_script(sample_plan())
    assert 'title: "Why Google bought Wiz"' in text
    assert "## 1. Thirty two billion dollars" in text
    assert "visual: still" in text
    assert "image: |" in text
    assert "  a giant number on warm cream paper" in text


def test_a_scene_without_a_prompt_falls_back_to_its_narration():
    text = scene_plan_to_script(sample_plan())
    assert "  Wiz watches the whole cloud." in text


def test_the_draft_parses_back_as_an_explainer_script():
    parsed = parse_explainer_script(scene_plan_to_script(sample_plan()))
    assert [scene.visual for scene in parsed.scenes] == ["still", "still"]
    assert parsed.scenes[0].image == "a giant number on warm cream paper"
    assert parsed.scenes[1].image == "Wiz watches the whole cloud."
    assert parsed.scenes[0].narration == "Thirty two billion dollars."
    assert parsed.aspect_ratio == "9:16"
    assert parsed.design == "cream_editorial"
    assert parsed.title == "Why Google bought Wiz"


def test_a_titled_narration_scene_drafts_as_a_designed_still():
    """Display copy on a still is kept, and its unspoken cue is silenced."""
    plan = ScenePlan(
        job_id="topic_draft",
        script="The strait",
        aspect_ratio="9:16",
        scenes=[
            Scene(
                id="scene_1",
                type="narration",
                duration_seconds=6.0,
                narration="Everything moved through here.",
                visual_description="a fishing boat at dawn",
                props={"eyebrow": "OPENING", "headline": "A narrow sea lane"},
            )
        ],
    )
    text = scene_plan_to_script(plan)
    scene = parse_explainer_script(text).scenes[0]
    assert scene.visual == "still"
    assert scene.props["eyebrow"] == "OPENING"
    assert scene.props["headline"] == "A narrow sea lane"
    # The narration never says the headline, so the default cue turns off —
    # the same rule a composite plate follows — and the draft still renders.
    assert scene.props["headline_cue"] is False
    _validate_draft(text, "test")


def test_a_title_with_a_colon_survives_the_round_trip():
    plan = sample_plan()
    plan.script = "Wiz: the $32 billion bet"
    parsed = parse_explainer_script(scene_plan_to_script(plan))
    assert parsed.title == "Wiz: the $32 billion bet"


def test_music_mood_is_carried_into_the_front_matter():
    text = scene_plan_to_script(sample_plan(), music_mood="warm minimal ambient")
    assert "music_mood: warm minimal ambient" in text
    assert parse_explainer_script(text).music_mood == "warm minimal ambient"


def test_the_draft_never_writes_a_duration():
    """Durations come from the spoken narration, never from the author."""
    text = scene_plan_to_script(sample_plan())
    assert "duration" not in text
    assert "duration_seconds" not in text


def test_a_mood_yaml_would_misread_is_quoted_instead_of_truncated():
    """A ``#`` or ``:`` in the mood must not silently become a comment/mapping."""
    text = scene_plan_to_script(sample_plan(), music_mood="warm #ambient: felt")
    assert parse_explainer_script(text).music_mood == "warm #ambient: felt"


# ---------------------------------------------------------------------------
# Editorial grammar: a plan scene may be a map, composite or typographic plate
# ---------------------------------------------------------------------------


def _editorial_plan(*scenes: Scene) -> ScenePlan:
    return ScenePlan(
        job_id="topic_draft",
        script="The trade that built an atlas",
        aspect_ratio="9:16",
        scenes=list(scenes) or sample_plan().scenes,
    )


def test_a_map_scene_becomes_a_map_block_without_an_image_request():
    plan = _editorial_plan(
        Scene(
            id="map_1",
            type="map",
            duration_seconds=6.0,
            narration="Tokyo held the whole ledger.",
            visual_description="a drawn map",  # must not become a paid still
            props={
                "country": "Japan",
                "focus": "139.7,35.7",
                "label": "Tokyo",
                "eyebrow": "The route",
                "headline": "An island market",
                "callouts": [{"text": "Six thousand ledgers", "cue": "ledger"}],
            },
        )
    )
    text = scene_plan_to_script(plan)
    assert "visual: map" in text
    assert "country: Japan" in text
    assert "label: Tokyo" in text

    parsed = parse_explainer_script(text)
    scene = parsed.scenes[0]
    assert scene.visual == "map"
    assert scene.image == ""
    assert scene.props["country"] == "Japan"
    assert scene.props["focus"] == "139.7,35.7"
    assert scene.props["callouts"] == [{"text": "Six thousand ledgers", "cue": "ledger"}]


def test_a_compare_composite_carries_both_still_prompts():
    plan = _editorial_plan(
        Scene(
            id="compare_1",
            type="composite",
            duration_seconds=6.0,
            narration="Two kinds of wealth.",
            visual_description="a rack of servers in a rented room",
            props={
                "layout": "compare",
                "labels": ["The Rack", "The Stone"],
                "image_b_prompt": "a single cut gem on dark velvet",
            },
        )
    )
    parsed = parse_explainer_script(scene_plan_to_script(plan))
    scene = parsed.scenes[0]
    assert scene.visual == "composite"
    assert scene.layout == "compare"
    assert scene.image == "a rack of servers in a rented room"
    assert scene.image_b == "a single cut gem on dark velvet"
    # The labels are display copy the narration never says, so they are kept
    # but carry no cue; the plate reveals them on its own stagger.
    assert scene.props["labels"] == [
        {"text": "The Rack", "cue": False},
        {"text": "The Stone", "cue": False},
    ]


def test_text_only_composites_need_no_still():
    plan = _editorial_plan(
        Scene(
            id="quote_1",
            type="composite",
            duration_seconds=5.0,
            narration="The past is never dead.",
            props={
                "layout": "quote",
                "quote": "The past is never dead. It is not even past.",
                "attribution": "William Faulkner",
            },
        ),
        Scene(
            id="section_1",
            type="composite",
            duration_seconds=4.0,
            narration="Part two.",
            props={"layout": "section", "number": "02", "headline": "After the Fall"},
        ),
    )
    parsed = parse_explainer_script(scene_plan_to_script(plan))
    assert [scene.layout for scene in parsed.scenes] == ["quote", "section"]
    assert [scene.image for scene in parsed.scenes] == ["", ""]
    assert "image:" not in scene_plan_to_script(plan)
    assert parsed.scenes[0].props["quote"].startswith("The past is never dead")
    assert parsed.scenes[1].props["number"] == "02"


def test_planner_bookkeeping_never_reaches_the_script():
    """``shot_type``/``requires_motion`` are routing hints, not plate grammar."""
    plan = _editorial_plan(
        Scene(
            id="split_1",
            type="composite",
            duration_seconds=6.0,
            narration="The machine sat in a rented room.",
            visual_description="two server racks",
            props={
                "layout": "split",
                "headline": "The Machine",
                "shot_type": "ai-still",
                "shot_type_confidence": 0.91,
                "requires_motion": True,
                "word_times": [{"word": "The", "start": 0.0, "end": 0.1}],
            },
        )
    )
    text = scene_plan_to_script(plan)
    for key in ("shot_type", "shot_type_confidence", "requires_motion", "word_times"):
        assert key not in text
    parsed = parse_explainer_script(text)
    assert "requires_motion" not in parsed.scenes[0].props


def test_a_typographic_plate_keeps_its_layout_and_props():
    plan = _editorial_plan(
        Scene(
            id="stat_1",
            type="typographic",
            duration_seconds=4.0,
            narration="Thirty-two billion dollars.",
            props={"layout": "big_stat", "headline": "$32B", "label": "acquisition"},
        )
    )
    parsed = parse_explainer_script(scene_plan_to_script(plan))
    assert parsed.scenes[0].visual == "typographic"
    assert parsed.scenes[0].layout == "big_stat"
    assert parsed.scenes[0].props["headline"] == "$32B"
    assert parsed.scenes[0].image == ""


# ---------------------------------------------------------------------------
# Drafting from a topic: the only network caller
# ---------------------------------------------------------------------------


class _Router:
    """A stand-in ``ModelRouter`` whose every chat call returns ``response``."""

    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.calls = 0
        self.messages: list[dict[str, str]] | None = None

    async def chat(self, **kwargs: Any) -> str:
        self.calls += 1
        self.messages = kwargs.get("messages")
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class _ScriptedRouter:
    """A router that returns a different plan per call (the last one repeats)."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls = 0

    async def chat(self, **kwargs: Any) -> str:
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


def _plan_json(*, scenes: list[dict[str, Any]] | None = None, **overrides: Any) -> str:
    payload: dict[str, Any] = {
        "aspect_ratio": "16:9",
        "music_mood": "cinematic",
        "scenes": scenes
        if scenes is not None
        else [
            {
                "id": "scene_1",
                "type": "opening",
                "duration_seconds": 4.0,
                "narration": "Thirty two billion dollars.",
                "visual_description": "a giant number on warm cream paper",
            },
            {
                "id": "scene_2",
                "type": "narration",
                "duration_seconds": 5.0,
                "narration": "Wiz watches the whole cloud.",
                "visual_description": "a small team watching a vast cloud",
            },
        ],
    }
    payload.update(overrides)
    return json.dumps(payload)


@pytest.fixture(autouse=True)
def _no_classifier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the optional Jev classifier from ever reaching the network."""
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)


@pytest.mark.asyncio
async def test_draft_writes_build_script_generated_md(tmp_path: Path) -> None:
    work = tmp_path / "job"
    text = await draft_script_from_topic("Why Google bought Wiz", _Router(_plan_json()), work)

    generated = work / "build" / "script.generated.md"
    assert generated.read_text(encoding="utf-8") == text
    parsed = parse_explainer_script(text)
    assert parsed.title == "Why Google bought Wiz"
    assert [scene.visual for scene in parsed.scenes] == ["still", "still"]
    assert [scene.narration for scene in parsed.scenes] == [
        "Thirty two billion dollars.",
        "Wiz watches the whole cloud.",
    ]


@pytest.mark.asyncio
async def test_the_draft_carries_the_callers_design_aspect_and_mood(tmp_path: Path) -> None:
    """The LLM's own aspect/mood never override what the caller asked for."""
    text = await draft_script_from_topic(
        "Why Google bought Wiz",
        _Router(_plan_json()),
        tmp_path / "job",
        aspect_ratio="9:16",
        design="cream_editorial",
        music_mood="warm minimal ambient",
    )
    parsed = parse_explainer_script(text)
    assert parsed.aspect_ratio == "9:16"
    assert parsed.design == "cream_editorial"
    assert parsed.music_mood == "warm minimal ambient"
    assert "duration" not in text


@pytest.mark.asyncio
async def test_an_llm_failure_is_a_clear_error(tmp_path: Path) -> None:
    work = tmp_path / "job"
    with pytest.raises(TopicDraftError, match="could not draft"):
        await draft_script_from_topic("Why Google bought Wiz", _Router(RuntimeError("down")), work)
    assert not (work / "build" / "script.generated.md").exists()


@pytest.mark.asyncio
async def test_a_malformed_plan_is_a_clear_error_not_an_empty_script(tmp_path: Path) -> None:
    work = tmp_path / "job"
    with pytest.raises(TopicDraftError, match="could not draft"):
        await draft_script_from_topic("Why Google bought Wiz", _Router("not json"), work)
    assert not (work / "build" / "script.generated.md").exists()


@pytest.mark.asyncio
async def test_a_silent_scene_is_a_clear_error(tmp_path: Path) -> None:
    """A plan scene with no narration would become an invented heading."""
    scenes = [
        {
            "id": "scene_1",
            "type": "narration",
            "duration_seconds": 4.0,
            "narration": "",
            "visual_description": "a silent chart",
        }
    ]
    with pytest.raises(TopicDraftError, match="no narration"):
        await draft_script_from_topic(
            "Why Google bought Wiz", _Router(_plan_json(scenes=scenes)), tmp_path / "job"
        )


@pytest.mark.asyncio
async def test_a_draft_that_does_not_parse_never_reaches_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The round-trip through ``parse_explainer_script`` gates the write."""
    work = tmp_path / "job"
    monkeypatch.setattr(
        "video_editor_pipelines.explainer_topic.scene_plan_to_script",
        lambda plan, **kwargs: (
            "---\ntitle: ''\n---\n\n## One\nvisual: still\nimage: x\nnarration: y\n"
        ),
    )
    with pytest.raises(TopicDraftError, match="not a valid explainer script"):
        await draft_script_from_topic("Why Google bought Wiz", _Router(_plan_json()), work)
    assert not (work / "build" / "script.generated.md").exists()


@pytest.mark.asyncio
async def test_a_blank_topic_is_rejected_before_any_call(tmp_path: Path) -> None:
    router = _Router(_plan_json())
    with pytest.raises(TopicDraftError, match="topic"):
        await draft_script_from_topic("   ", router, tmp_path / "job")
    assert router.calls == 0


@pytest.mark.asyncio
async def test_a_draft_can_emit_editorial_plates(tmp_path: Path) -> None:
    scenes = [
        {
            "id": "scene_1",
            "type": "narration",
            "duration_seconds": 4.0,
            "narration": "A map is an argument.",
            "visual_description": "an antique atlas open on a desk",
        },
        {
            "id": "scene_2",
            "type": "map",
            "duration_seconds": 6.0,
            "narration": "Tokyo sat at the centre of the ledger.",
            "visual_description": "",
            "props": {
                "country": "Japan",
                "label": "Tokyo",
                "callouts": ["Six thousand ledgers"],
            },
        },
        {
            "id": "scene_3",
            "type": "composite",
            "duration_seconds": 6.0,
            "narration": "The racks held the whole market.",
            "visual_description": "two server racks in a rented room",
            "props": {"layout": "split", "headline": "The Machine", "side": "right"},
        },
    ]
    router = _Router(_plan_json(scenes=scenes))
    text = await draft_script_from_topic("How an atlas was made", router, tmp_path / "job")

    parsed = parse_explainer_script(text)
    assert [scene.visual for scene in parsed.scenes] == ["still", "map", "composite"]
    assert parsed.scenes[1].props["country"] == "Japan"
    assert parsed.scenes[2].layout == "split"
    assert parsed.scenes[2].props["side"] == "right"

    # The planner was actually asked for the editorial grammar.
    assert router.messages is not None
    prompt = "\n".join(message["content"] for message in router.messages)
    assert "Editorial mode" in prompt
    assert "composite" in prompt


@pytest.mark.asyncio
async def test_an_unrenderable_plate_fails_before_the_write(tmp_path: Path) -> None:
    """An unknown country is caught by the real map lookup, before the gate."""
    scenes = [
        {
            "id": "scene_1",
            "type": "map",
            "duration_seconds": 6.0,
            "narration": "A place that does not exist.",
            "props": {"country": "Atlantis"},
        }
    ]
    work = tmp_path / "job"
    with pytest.raises(TopicDraftError, match="cannot be rendered"):
        await draft_script_from_topic("A lost island", _Router(_plan_json(scenes=scenes)), work)
    assert not (work / "build" / "script.generated.md").exists()


@pytest.mark.asyncio
async def test_a_compare_without_a_second_prompt_fails_before_the_write(tmp_path: Path) -> None:
    scenes = [
        {
            "id": "scene_1",
            "type": "composite",
            "duration_seconds": 6.0,
            "narration": "Two kinds of wealth.",
            "visual_description": "the first still",
            "props": {"layout": "compare", "labels": ["A", "B"]},
        }
    ]
    work = tmp_path / "job"
    with pytest.raises(TopicDraftError, match="not a valid explainer script"):
        await draft_script_from_topic(
            "Two kinds of wealth", _Router(_plan_json(scenes=scenes)), work
        )
    assert not (work / "build" / "script.generated.md").exists()


@pytest.mark.asyncio
async def test_a_silenced_cue_is_logged_and_written_next_to_the_generated_script(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    scenes = [
        {
            "id": "map_1",
            "type": "map",
            "duration_seconds": 6.0,
            "narration": "The sea drew a shortcut.",
            "props": {"country": "Indonesia", "callouts": ["Singapore"]},
        }
    ]
    work = tmp_path / "job"

    with caplog.at_level("WARNING", logger="video_editor_pipelines.explainer_topic"):
        await draft_script_from_topic("The strait", _Router(_plan_json(scenes=scenes)), work)

    records = json.loads((work / "build" / "silenced_cues.json").read_text(encoding="utf-8"))
    assert records == [{"scene": "map_1", "item": "item_0", "phrase": "Singapore"}]
    assert "cue_silenced" in caplog.text
    assert "map_1" in caplog.text
    assert "item_0" in caplog.text
    assert "Singapore" in caplog.text


@pytest.mark.asyncio
async def test_a_clean_draft_removes_a_stale_silenced_cues_file(tmp_path: Path) -> None:
    work = tmp_path / "job"
    build = work / "build"
    build.mkdir(parents=True)
    stale = build / "silenced_cues.json"
    stale.write_text('[{"scene": "old"}]', encoding="utf-8")

    await draft_script_from_topic("Why Google bought Wiz", _Router(_plan_json()), work)

    assert not stale.exists()


# ---------------------------------------------------------------------------
# CLI wiring: --from-topic
# ---------------------------------------------------------------------------


def test_from_topic_and_a_script_argument_are_alternatives(tmp_path: Path) -> None:
    from video_editor_cli.main import app

    script = tmp_path / "script.md"
    script.write_text(
        "---\ntitle: T\n---\n\n## One\nvisual: still\nimage: x\nnarration: y\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        app, ["explain", str(script), "--from-topic", "Wiz", "--job-id", "job"]
    )
    assert result.exit_code == 2
    assert "--from-topic" in result.output


def test_explain_without_a_script_or_topic_is_a_usage_error(tmp_path: Path) -> None:
    from video_editor_cli.main import app

    result = CliRunner().invoke(
        app,
        [
            "explain",
            "--job-id",
            "job",
            "--work-root",
            str(tmp_path),
            "--output-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 2
    assert "--from-topic" in result.output


def test_a_draft_failure_stops_the_cli_before_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from video_editor_cli import main

    async def _boom(*args: Any, **kwargs: Any) -> str:
        raise TopicDraftError("the provider is down")

    monkeypatch.setattr(main, "_discover_config_path", lambda *a, **k: tmp_path / "config.yaml")
    monkeypatch.setattr(main, "_llm_router", lambda *a, **k: object())
    monkeypatch.setattr("video_editor_pipelines.explainer_topic.draft_script_from_topic", _boom)

    result = CliRunner().invoke(
        main.app,
        [
            "explain",
            "--from-topic",
            "Why Google bought Wiz",
            "--job-id",
            "job",
            "--work-root",
            str(tmp_path),
            "--output-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert "Could not draft a script" in result.output


# ---------------------------------------------------------------------------
# Cues are words the scene actually speaks, not labels
# ---------------------------------------------------------------------------


def test_a_cue_label_is_stripped_when_the_scene_speaks_the_words():
    plan = _editorial_plan(
        Scene(
            id="map_1",
            type="map",
            duration_seconds=6.0,
            narration="Tokyo held the whole ledger.",
            props={
                "country": "Japan",
                "callouts": [{"text": "The ledger", "cue": "narration: ledger"}],
            },
        )
    )
    parsed = parse_explainer_script(scene_plan_to_script(plan))
    assert parsed.scenes[0].props["callouts"] == [{"text": "The ledger", "cue": "ledger"}]


def test_a_cue_the_scene_never_says_is_dropped_before_it_can_fail_qa():
    """A card with an impossible cue stays; only the cue is removed."""
    plan = _editorial_plan(
        Scene(
            id="map_1",
            type="map",
            duration_seconds=6.0,
            narration="The sea drew a shortcut.",
            props={
                "country": "Indonesia",
                "callouts": [
                    {"text": "Singapore", "cue": "narration: Singapore"},
                    "A narrow gate",
                ],
            },
        )
    )
    parsed = parse_explainer_script(scene_plan_to_script(plan))
    # Both callouts stay on the plate; neither keeps a cue the scene never says.
    assert parsed.scenes[0].props["callouts"] == [
        {"text": "Singapore", "cue": False},
        {"text": "A narrow gate", "cue": False},
    ]


def test_nested_fact_cues_are_checked_against_the_scene_narration():
    plan = _editorial_plan(
        Scene(
            id="split_1",
            type="composite",
            duration_seconds=6.0,
            narration="A land bridge once linked the peninsula to Sumatra.",
            visual_description="a dry land bridge",
            props={
                "layout": "split",
                "facts": [
                    {"text": "The land bridge", "cue": "land bridge"},
                    {"text": "Later trade", "cue": "Singapore"},
                ],
            },
        )
    )
    parsed = parse_explainer_script(scene_plan_to_script(plan))
    assert parsed.scenes[0].props["facts"] == [
        {"text": "The land bridge", "cue": "land bridge"},
        {"text": "Later trade", "cue": False},
    ]


@pytest.mark.asyncio
async def test_a_rejected_draft_is_redrafted_before_the_gate(tmp_path: Path) -> None:
    """One bad layout shape costs a second free draft, not the whole run."""
    broken = _plan_json(
        scenes=[
            {
                "id": "scene_1",
                "type": "typographic",
                "duration_seconds": 4.0,
                "narration": "Three steps.",
                "props": {"layout": "sequence", "steps": ["one", "two"]},
            }
        ]
    )
    good = _plan_json()
    router = _ScriptedRouter([broken, good])

    text = await draft_script_from_topic("Why Google bought Wiz", router, tmp_path / "job")

    assert router.calls == 2
    assert [scene.visual for scene in parse_explainer_script(text).scenes] == ["still", "still"]


@pytest.mark.asyncio
async def test_a_draft_that_stays_unrenderable_fails_after_the_attempt_budget(
    tmp_path: Path,
) -> None:
    broken = _plan_json(
        scenes=[
            {
                "id": "scene_1",
                "type": "typographic",
                "duration_seconds": 4.0,
                "narration": "Three steps.",
                "props": {"layout": "sequence", "steps": ["one", "two"]},
            }
        ]
    )
    router = _ScriptedRouter([broken])

    with pytest.raises(TopicDraftError, match="cannot be rendered"):
        await draft_script_from_topic("Why Google bought Wiz", router, tmp_path / "job")
    assert router.calls == DRAFT_ATTEMPTS


def test_a_draft_that_cues_unspoken_words_fails_before_the_cost_gate():
    """An authored plate cue the scene never says is caught in memory."""
    text = """\
---
title: The strait
aspect_ratio: 9:16
design: cream_editorial
---

## 1. A chapter

visual: composite
layout: section
headline: Empires on the Water
number: "02"
narration: |
  The sea drew a shortcut.
"""
    with pytest.raises(TopicDraftError, match="cues words its scenes never say"):
        _validate_draft(text, "script.generated.md")
