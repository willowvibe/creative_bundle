"""Tests for the rough cut skill."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rough_cut.rough_cut import RoughCutSkill
from video_editor_core.timeline import EDL, Segment, diff_edls
from video_editor_ffmpeg.probe import probe_media
from video_editor_state.job import JobContext


def _build_transcript(words: list[tuple[str, float, float]]) -> dict[str, object]:
    return {
        "source_audio": "",
        "language": "en",
        "language_probability": 1.0,
        "word_timestamps": True,
        "segments": [],
        "words": [{"word": w, "start": s, "end": e, "probability": 0.95} for w, s, e in words],
    }


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(
        job_id="test-job",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )


@pytest.fixture
def transcript_with_filler(tmp_path: Path) -> Path:
    transcript = _build_transcript(
        [
            ("Hello", 0.0, 0.4),
            ("um", 0.5, 0.7),
            ("world", 0.8, 1.2),
            ("pause", 2.0, 2.5),
            ("Again", 3.0, 3.4),
        ]
    )
    path = tmp_path / "transcript.json"
    path.write_text(json.dumps(transcript), encoding="utf-8")
    return path


def test_rough_cut_with_transcript_override(
    tmp_media: Path,
    ctx: JobContext,
    transcript_with_filler: Path,
) -> None:
    skill = RoughCutSkill(ctx)
    locked = skill.run(tmp_media, transcript_path=transcript_with_filler)

    assert locked.exists()
    info = probe_media(locked)
    assert info.duration is not None
    assert info.duration > 0
    assert info.duration < 3.0  # filler and pause removed

    # EDL was written.
    edl_path = ctx.artifact("plan", "edl.json")
    assert edl_path.exists()


def test_partial_rerender(
    tmp_media: Path,
    ctx: JobContext,
    transcript_with_filler: Path,
) -> None:
    skill = RoughCutSkill(ctx)
    locked = skill.run(tmp_media, transcript_path=transcript_with_filler)
    first_info = probe_media(locked)
    assert first_info.duration is not None
    first_duration = first_info.duration

    edl_path = ctx.artifact("plan", "edl.json")
    with open(edl_path, encoding="utf-8") as f:
        data = json.load(f)
    old_edl = EDL(**data)

    # Modify the last segment slightly, keeping within source bounds.
    new_segments = [Segment(**seg.model_dump()) for seg in old_edl.segments]
    assert new_segments
    last = new_segments[-1]
    last.source_end = min(last.source_end + 0.5, 3.0)
    last.timeline_end = last.timeline_start + (last.source_end - last.source_start)

    new_edl = EDL(
        job_id=old_edl.job_id,
        source_path=old_edl.source_path,
        segments=new_segments,
    )

    diff = diff_edls(old_edl, new_edl)
    assert len(diff.changed) == 1

    locked2 = skill.update_and_render(new_edl)
    second_info = probe_media(locked2)
    assert second_info.duration is not None
    assert second_info.duration > first_duration


def _build_alignment(words: list[tuple[str, float, float]]) -> dict[str, object]:
    """Build a JSON in ElevenLabs' /v1/forced-alignment response shape."""
    return {
        "words": [{"text": w, "start": s, "end": e} for w, s, e in words],
        "characters": [],
        "transcript": " ".join(w for w, _, _ in words),
        "language": "en",
    }


class TestRoughCutAlignmentPromotion:
    """Session W: ``ve align`` writes ``transcript/alignment.json`` (ElevenLabs
    shape); ``RoughCut.run()`` promotes it to ``transcript/transcript.json``
    (WhisperX shape) so CaptionsSkill, second_pass, and graphics all pick
    up the alignment-driven timestamps without a WhisperX pass.

    Priority order: user-supplied transcript > alignment > WhisperX.
    """

    def test_alignment_json_promoted_to_transcript(
        self,
        tmp_media: Path,
        ctx: JobContext,
    ) -> None:
        """An alignment.json with no transcript.json is converted and written."""
        # Pre-stage the alignment file (this is what ``ve align`` would
        # have written). The transcribe() call should be skipped entirely.
        alignment_path = ctx.artifact("transcript", "alignment.json")
        alignment_path.parent.mkdir(parents=True, exist_ok=True)
        alignment = _build_alignment(
            [
                ("hello", 0.0, 0.4),
                ("world", 0.5, 1.0),
                ("again", 1.5, 2.0),
            ]
        )
        alignment_path.write_text(json.dumps(alignment), encoding="utf-8")

        skill = RoughCutSkill(ctx)
        # Spy on transcribe to assert it is NOT called.
        called = {"transcribe": False}

        def _fake_transcribe(*args: object, **kwargs: object) -> dict[str, object]:
            called["transcribe"] = True
            return {}

        skill.transcribe = _fake_transcribe  # type: ignore[method-assign]
        skill.run(tmp_media)

        # transcript.json was written from the alignment.
        transcript_path = ctx.artifact("transcript", "transcript.json")
        assert transcript_path.exists()
        written = json.loads(transcript_path.read_text(encoding="utf-8"))

        # WhisperX shape: keys renamed, probability defaulted to 1.0.
        assert [w["word"] for w in written["words"]] == ["hello", "world", "again"]
        assert all(w["probability"] == 1.0 for w in written["words"])
        assert written["language"] == "en"

        # transcribe() was NOT called — alignment took priority.
        assert called["transcribe"] is False

        # Manifest stamped with the source so downstream consumers
        # can tell where the transcript came from.
        manifest_path = ctx.work_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        transcribe_outputs = manifest["stages"]["transcribe"]["outputs"]
        assert transcribe_outputs["source"] == "alignment"
        assert transcribe_outputs["transcript"].endswith("transcript.json")

    def test_transcript_takes_precedence_over_alignment(
        self,
        tmp_media: Path,
        ctx: JobContext,
    ) -> None:
        """A pre-existing transcript.json blocks the promotion helper.

        ``_promote_alignment_to_transcript`` returns ``False`` and does
        NOT touch the file. The pre-existing transcript.json is the
        sentinel for whatever wrote it (a prior run, a user-supplied
        path, or a hand-edited file). This locks down the
        "transcript wins over alignment" priority at the helper level,
        which is what the user-visible contract is.
        """
        # Pre-stage both files: a user-edited transcript and a stale alignment.
        transcript_path = ctx.artifact("transcript", "transcript.json")
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        sentinel = {
            "marker": "user-edited",
            "words": [{"word": "marker", "start": 0.0, "end": 1.0, "probability": 0.5}],
        }
        transcript_path.write_text(json.dumps(sentinel), encoding="utf-8")

        alignment_path = ctx.artifact("transcript", "alignment.json")
        alignment_path.write_text(
            json.dumps(_build_alignment([("hello", 0.0, 0.4)])),
            encoding="utf-8",
        )

        skill = RoughCutSkill(ctx)
        promoted = skill._promote_alignment_to_transcript()

        # Helper refuses to promote — transcript.json wins.
        assert promoted is False
        # File is byte-identical to the sentinel.
        preserved = json.loads(transcript_path.read_text(encoding="utf-8"))
        assert preserved == sentinel

    def test_no_alignment_falls_back_to_transcribe(
        self,
        tmp_media: Path,
        ctx: JobContext,
    ) -> None:
        """When neither transcript nor alignment is present, transcribe() runs."""
        # Ensure neither file exists.
        transcript_path = ctx.artifact("transcript", "transcript.json")
        assert not transcript_path.exists()

        skill = RoughCutSkill(ctx)
        called = {"transcribe": False}

        def _spy_transcribe(*args: object, **kwargs: object) -> dict[str, object]:
            called["transcribe"] = True
            # Simulate WhisperX writing transcript.json.
            transcript_path.parent.mkdir(parents=True, exist_ok=True)
            transcript_path.write_text(
                json.dumps(_build_transcript([("from", 0.0, 0.5), ("whisperx", 0.6, 1.0)])),
                encoding="utf-8",
            )
            return {}

        # Bind the spy to the instance.
        skill.transcribe = _spy_transcribe  # type: ignore[method-assign]
        skill.run(tmp_media)

        assert called["transcribe"] is True
        # transcribe() wrote the transcript.json with the WhisperX result.
        assert transcript_path.exists()

    def test_user_supplied_transcript_takes_precedence_over_alignment(
        self,
        tmp_media: Path,
        ctx: JobContext,
        transcript_with_filler: Path,
    ) -> None:
        """All three branches exercised: user > alignment > WhisperX.

        The user supplies ``transcript_path=transcript_with_filler``; we
        also pre-stage an ``alignment.json`` to confirm the user wins
        (the alignment is silently ignored).
        """
        alignment_path = ctx.artifact("transcript", "alignment.json")
        alignment_path.parent.mkdir(parents=True, exist_ok=True)
        alignment_path.write_text(
            json.dumps(_build_alignment([("hello", 0.0, 0.4)])),
            encoding="utf-8",
        )

        skill = RoughCutSkill(ctx)
        called = {"transcribe": False, "promote": False}

        def _spy_transcribe(*args: object, **kwargs: object) -> dict[str, object]:
            called["transcribe"] = True
            return {}

        original_promote = skill._promote_alignment_to_transcript

        def _spy_promote() -> bool:
            called["promote"] = True
            return original_promote()

        skill.transcribe = _spy_transcribe  # type: ignore[method-assign]
        skill._promote_alignment_to_transcript = _spy_promote  # type: ignore[method-assign]
        skill.run(tmp_media, transcript_path=transcript_with_filler)

        # User-supplied path wins; promotion AND transcribe are skipped.
        assert called["transcribe"] is False
        assert called["promote"] is False

        # The user-supplied transcript is the one that landed at the canonical path.
        canonical = ctx.artifact("transcript", "transcript.json")
        written = json.loads(canonical.read_text(encoding="utf-8"))
        words = [w["word"] for w in written["words"]]
        assert "um" in words  # user fixture has "um" filler word
        assert "hello" not in words  # alignment would have produced this
