import json

import pytest

from scripts.drama_board import episode_id, items
from scripts.drama_review import _digest


@pytest.fixture
def episode(tmp_path):
    ep = tmp_path / "2026-10-30" / "show-ep1"
    ep.mkdir(parents=True)
    (ep / "script.md").write_text("# Show\n")
    spec = {
        "shots": [
            {"id": "c01a", "vo": {"who": "narrator", "te": "ఒకటి", "en": "One", "emotion": "calm"}}
        ]
    }
    (ep / "shots.json").write_text(json.dumps(spec, ensure_ascii=False))
    return ep


def test_episode_id_keeps_the_date(episode):
    assert episode_id(episode) == "2026-10-30-show-ep1"


def test_items_carry_the_gate_ids_and_hashes(episode):
    (script,) = items(episode, "script")
    assert (
        script["markdown"] == "# Show\n"
        and script["hash"] == _digest(episode, "script")["script.md"]
    )
    (line,) = items(episode, "dialogue")
    assert (line["id"], line["who"], line["te"]) == ("c01a_vo", "Narrator", "ఒకటి")
    assert line["hash"] == _digest(episode, "dialogue")["c01a_vo"] and line["file"] is None


def test_a_stage_with_missing_takes_cannot_be_published(episode):
    with pytest.raises(SystemExit, match="missing"):
        items(episode, "voices")


def test_media_items_point_at_their_files(episode):
    frame = episode / "frames" / "s01.png"
    frame.parent.mkdir()
    frame.write_bytes(b"png")
    (item,) = items(episode, "images")
    assert (item["id"], item["kind"], item["file"]) == ("frames/s01.png", "image", frame)
