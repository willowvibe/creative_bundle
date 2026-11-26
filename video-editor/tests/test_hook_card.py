"""The hook overlay: parsing, markup and the adapter layer."""

from __future__ import annotations

import pytest
from video_editor_core.adapters.from_scene_plan import from_scene_plan
from video_editor_core.hook_card import Hook, parse_hook, render_hook
from video_editor_pipelines.explainer_script import ScriptError, parse_explainer_script


def test_parse_hook_defaults_and_validation():
    assert parse_hook(None) is None
    assert parse_hook({"headline": "  Why?  "}) == Hook(headline="Why?", sub="", seconds=3.0)
    assert parse_hook({"headline": "A", "sub": "b", "seconds": 5}).seconds == 5.0
    for bad in (
        {"headline": ""},
        {"sub": "no headline"},
        {"headline": "A", "seconds": 0.5},
        {"headline": "A", "seconds": 9},
        {"headline": "A", "seconds": "3"},
        {"headline": "A", "extra": 1},
        "just text",
    ):
        with pytest.raises(ValueError):
            parse_hook(bad)


def test_markup_escapes_text_and_only_the_panel_animates():
    markup, css = render_hook(Hook("<b>₹34,000 & more</b>", "ఖర్చు <i>", 4.0), index=2, width=1080)
    assert "&lt;b&gt;₹34,000 &amp; more&lt;/b&gt;" in markup
    assert "ఖర్చు &lt;i&gt;" in markup
    assert "<b>" not in markup
    # one animation, on the panel itself, spanning `seconds`; children are static
    assert markup.count("animation:") == 1
    assert "hook_2 4.00s" in markup
    assert "@keyframes hook_2" in css
    assert "100% { opacity: 0;" in css  # gone by `seconds`
    assert "hook-headline" in css and "#FDB813" in css


def _plan(hook=None):
    props = {"hook": hook} if hook is not None else {}
    return {
        "job_id": "hook_job",
        "aspect_ratio": "9:16",
        "scenes": [
            {
                "id": "s1",
                "type": "narration",
                "duration_seconds": 6.0,
                "props": {**props, "tally": {"label": "cash", "value": 0}},
            }
        ],
    }


def test_a_scene_with_a_hook_gets_exactly_one_hook_layer():
    composition = from_scene_plan(
        _plan({"headline": "Rent <1>", "sub": "per month", "seconds": 2.5})
    )
    layers = composition.scenes[0].layers
    hooks = [layer for layer in layers if layer.preset == "hook"]
    assert len(hooks) == 1
    hook = hooks[0]
    assert hook.kind == "html" and hook.id == "scene_0_hook"
    assert "Rent &lt;1&gt;" in hook.content.text and "per month" in hook.content.text
    assert "2.50s" in hook.content.text
    # centred horizontally, ~70% wide, vertical centre at 38%
    assert hook.rect.x * 2 + hook.rect.width == 1080
    assert hook.rect.width == pytest.approx(1080 * 0.7, abs=1)
    assert hook.rect.y + hook.rect.height / 2 == pytest.approx(1920 * 0.38, abs=1)
    # z: above footage, below the tally chip
    tally = next(layer for layer in layers if layer.preset == "tally")
    assert hook.z > 0 and hook.z < tally.z


def test_a_scene_without_a_hook_gets_none():
    layers = from_scene_plan(_plan()).scenes[0].layers
    assert not [layer for layer in layers if layer.preset == "hook"]


def _script(hook_yaml: str) -> str:
    return (
        "---\ntitle: T\n---\n\n## Scene 1\nvisual: still\nimage: a still\n"
        f"narration: hello\n{hook_yaml}\n"
    )


def test_explainer_script_carries_a_hook_into_props():
    script = parse_explainer_script(_script("hook: {headline: Big, sub: small, seconds: 2}"))
    assert script.scenes[0].props["hook"] == {"headline": "Big", "sub": "small", "seconds": 2}


@pytest.mark.parametrize(
    "hook",
    ["hook: {headline: ''}", "hook: {sub: x}", "hook: {headline: A, seconds: 12}"],
)
def test_explainer_script_rejects_a_bad_hook(hook):
    with pytest.raises(ScriptError, match="hook"):
        parse_explainer_script(_script(hook))
