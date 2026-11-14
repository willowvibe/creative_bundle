import json

import pytest

from scripts.drama_review import ReviewPending, approve, pending, require, write_storyboard


@pytest.fixture
def episode(tmp_path):
    spec = {
        "shots": [
            {"id": "s01", "vo": {"who": "narrator", "te": "ఒకటి", "en": "One", "emotion": "grave"}},
            {
                "id": "s02",
                "kind": "lipsync",
                "speaker": "raghava",
                "emotion": "despair",
                "line": {"te": "రెండు", "en": "Two"},
            },
        ]
    }
    (tmp_path / "shots.json").write_text(json.dumps(spec, ensure_ascii=False))
    (tmp_path / "script.md").write_text("# Script\n")
    approve(tmp_path, "script")
    return tmp_path


def _takes(ep, data=b"take1"):
    for name in ("s01_vo_narrator", "s02_line_raghava"):
        f = ep / "audio/lines" / f"{name}.wav"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)


def test_dialogue_gate_blocks_until_approved(episode):
    with pytest.raises(ReviewPending, match="dialogue"):
        require(episode, "dialogue")
    approve(episode, "dialogue")
    require(episode, "dialogue")


def test_voices_gate_needs_dialogue_and_takes(episode):
    _takes(episode)
    with pytest.raises(ReviewPending, match="dialogue"):
        require(episode, "voices")
    approve(episode, "dialogue")
    with pytest.raises(ReviewPending, match="voices"):
        require(episode, "voices")
    approve(episode, "voices")
    require(episode, "voices")


def test_voices_cannot_be_approved_with_missing_takes(episode):
    approve(episode, "dialogue")
    with pytest.raises(SystemExit, match="missing"):
        approve(episode, "voices")


def test_new_take_reopens_only_that_line(episode):
    _takes(episode)
    approve(episode, "dialogue")
    approve(episode, "voices")
    (episode / "audio/lines/s02_line_raghava.wav").write_bytes(b"take2")
    assert pending(episode, "voices") == ["s02_line"]
    with pytest.raises(ReviewPending):
        require(episode, "voices")


def test_edited_line_reopens_dialogue(episode):
    approve(episode, "dialogue")
    spec = json.loads((episode / "shots.json").read_text())
    spec["shots"][0]["vo"]["te"] = "మార్చారు"
    (episode / "shots.json").write_text(json.dumps(spec, ensure_ascii=False))
    assert pending(episode, "dialogue") == ["s01_vo"]


def test_script_gate_comes_first(tmp_path):
    (tmp_path / "shots.json").write_text('{"shots": []}')
    with pytest.raises(SystemExit, match="missing"):
        approve(tmp_path, "script")
    (tmp_path / "script.md").write_text("# v1\n")
    with pytest.raises(ReviewPending, match="script"):
        require(tmp_path, "dialogue")
    approve(tmp_path, "script")
    (tmp_path / "script.md").write_text("# v2\n")
    assert pending(tmp_path, "script") == ["script.md"]


def test_media_stages_follow_in_order(episode):
    _takes(episode)
    approve(episode, "dialogue")
    approve(episode, "voices")
    frame = episode / "frames/s01.png"
    frame.parent.mkdir()
    frame.write_bytes(b"img1")
    with pytest.raises(ReviewPending, match="images"):
        require(episode, "videos")
    approve(episode, "images")
    clip = episode / "clips/motion/s01.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"clip1")
    approve(episode, "videos")
    with pytest.raises(ReviewPending, match="lipsync"):
        require(episode, "lipsync")
    frame.write_bytes(b"img2")  # a regenerated frame reopens images, and so everything after it
    assert pending(episode, "images") == ["frames/s01.png"]
    with pytest.raises(ReviewPending, match="images"):
        require(episode, "videos")


def test_storyboard_shows_picture_camera_action_line_and_directives(tmp_path):
    """The board is the cheap review point: still + camera + line, one page."""
    shots = {
        "shots": [
            {
                "id": "c01a",
                "scene": 1,
                "speaker": "raghava",
                "line": {
                    "who": "raghava",
                    "te": "ఎక్కడ?",
                    "en": "Where is the ring?",
                    "emotion": "despair",
                },
            }
        ]
    }
    (tmp_path / "shots.json").write_text(json.dumps(shots, ensure_ascii=False))
    (tmp_path / "v2" / "frames").mkdir(parents=True)
    (tmp_path / "v2" / "frames" / "s01.png").write_bytes(b"png")
    (tmp_path / "v2" / "visuals.json").write_text(
        json.dumps(
            {
                "visuals": [
                    {
                        "id": "s01",
                        "scene": 1,
                        "kind": "speak",
                        "cam": "slow push-in",
                        "expr": "despair",
                        "prompt": "Close-up of Raghava at the door. Warm light.",
                        "seconds": 3.0,
                        "lines": [["c01a_line", 0, 3.0]],
                    }
                ]
            }
        )
    )

    out = write_storyboard(tmp_path)
    page = out.read_text()

    assert out.name == "storyboard.html"
    assert "slow push-in" in page
    assert "Where is the ring?" in page
    assert "v2/frames/s01.png" in page
    assert "single continuous take" in page
    assert "spoken exactly as written" in page


def test_storyboard_without_a_visual_plan_says_so(tmp_path):
    (tmp_path / "shots.json").write_text('{"shots": []}')

    with pytest.raises(SystemExit, match="no storyboard"):
        write_storyboard(tmp_path)
