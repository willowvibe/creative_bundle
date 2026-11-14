from __future__ import annotations

from video_editor_pipelines.explainer_script import ScriptScene
from video_editor_pipelines.still_reuse import (
    _fold_plural,
    apply_reuse,
    format_reuse_report,
    jaccard,
    plan_still_reuse,
    subject_words,
)


def still(scene_id: str, image: str, narration: str = "", subject: str = "") -> ScriptScene:
    return ScriptScene(
        id=scene_id,
        title=scene_id,
        visual="still",
        narration=narration or f"Narration for {scene_id}.",
        image=image,
        subject=subject,
    )


def typographic(scene_id: str) -> ScriptScene:
    return ScriptScene(
        id=scene_id,
        title=scene_id,
        visual="typographic",
        narration="A number.",
        layout="big_stat",
        props={"headline": "$32B"},
    )


def test_jaccard_counts_overlap_over_union():
    assert jaccard({"a", "b"}, {"b", "c"}) == 1 / 3
    assert jaccard(set(), {"b"}) == 0.0


def test_plural_folding_keeps_singular_and_plural_together():
    assert _fold_plural("machine") == _fold_plural("machines")
    assert _fold_plural("house") == _fold_plural("houses")
    assert _fold_plural("rack") == _fold_plural("racks")
    assert _fold_plural("box") == _fold_plural("boxes")


def test_plural_folding_does_not_invent_a_different_word():
    assert _fold_plural("cares") != "car"
    assert _fold_plural("hates") != "hat"


def test_words_come_from_prompt_and_narration_with_stopwords_and_plurals_folded():
    scene = still(
        "scene_1",
        "two black server racks in a data centre",
        "The racks hold the cloud.",
    )
    words = subject_words(scene)
    assert "racks" not in words and "rack" in words
    assert "server" in words
    assert "the" not in words


def test_reprised_subject_joins_the_first_scenes_cluster():
    scenes = [
        still(
            "scene_1",
            "black server racks in a cream data centre",
            "Server racks full of rented machines.",
        ),
        typographic("scene_2"),
        still(
            "scene_3",
            "cream data centre server racks, red sticker",
            "The server racks again, rented.",
        ),
    ]
    reuse = plan_still_reuse(scenes, threshold=0.20)
    assert [cluster.owner for cluster in reuse.clusters] == ["scene_1"]
    assert reuse.clusters[0].scenes == ["scene_1", "scene_3"]
    assert reuse.still_scenes == 2
    assert reuse.renders == 1
    assert reuse.saved == 1


def test_different_subjects_never_merge():
    scenes = [
        still("scene_1", "cream data centre server racks", "Rented racks."),
        still("scene_2", "cream data centre server racks", "Rented racks.", subject="racks"),
    ]
    reuse = plan_still_reuse(scenes, threshold=0.20)
    assert reuse.clusters == []
    assert reuse.unclustered == ["scene_1", "scene_2"]


def test_distinct_subjects_never_merge():
    scenes = [
        still("scene_1", "cream data centre server racks", "Rented racks.", subject="racks"),
        still("scene_2", "cream data centre server racks", "Rented racks.", subject="metals"),
    ]
    reuse = plan_still_reuse(scenes, threshold=0.20)
    assert reuse.clusters == []
    assert reuse.unclustered == ["scene_1", "scene_2"]


def test_subject_tags_match_case_and_whitespace_insensitively():
    scenes = [
        still("scene_1", "a gem on velvet", "A gem.", subject="Gem"),
        still("scene_2", "a red gate at dusk", "The gate.", subject=" gem "),
    ]
    reuse = plan_still_reuse(scenes, threshold=0.99)
    assert reuse.clusters[0].scenes == ["scene_1", "scene_2"]
    assert reuse.clusters[0].subject == "gem"


def test_shared_subject_always_merges_even_with_unrelated_prompts():
    scenes = [
        still("scene_1", "a gem on velvet", "A gem.", subject="gem"),
        still("scene_2", "a red gate at dusk", "The gate.", subject="gem"),
    ]
    reuse = plan_still_reuse(scenes, threshold=0.99)
    assert reuse.clusters[0].scenes == ["scene_1", "scene_2"]


def test_disabled_reuse_keeps_every_render():
    scenes = [
        still("scene_1", "black server racks", "Racks."),
        still("scene_2", "black server racks", "Racks."),
    ]
    reuse = plan_still_reuse(scenes, threshold=0.10, enabled=False)
    assert reuse.clusters == []
    assert reuse.renders == 2


def test_reuse_is_deterministic():
    scenes = [
        still("scene_1", "black server racks", "Racks."),
        still("scene_2", "black server racks", "Racks."),
    ]
    first = plan_still_reuse(scenes, threshold=0.20)
    second = plan_still_reuse(scenes, threshold=0.20)
    assert [(c.owner, c.scenes) for c in first.clusters] == [
        (c.owner, c.scenes) for c in second.clusters
    ]


def test_apply_reuse_writes_image_scene_for_members_only():
    scenes = [
        still("scene_1", "black server racks", "Racks."),
        still("scene_2", "black server racks", "Racks."),
    ]
    plan = {
        "scenes": [
            {"id": "scene_1", "props": {}},
            {"id": "scene_2", "props": {}},
        ]
    }
    apply_reuse(plan, plan_still_reuse(scenes, threshold=0.20))
    assert plan["scenes"][0]["props"] == {}
    assert plan["scenes"][1]["props"] == {"image_scene": "scene_1"}


def test_report_names_clusters_and_counts():
    scenes = [
        still("scene_1", "black server racks", "Racks."),
        still("scene_2", "black server racks", "Racks."),
        still("scene_3", "a gem on velvet", "A gem."),
    ]
    report = format_reuse_report(plan_still_reuse(scenes, threshold=0.20))
    assert "3 still scenes -> 2 renders (1 saved, 5 credits / $0.025)" in report
    assert "scene_1" in report and "scene_2" in report
    assert "unclustered" in report and "scene_3" in report
