"""v3 pipeline for the Saju reel: brand-aware 16:9 long-form with the
Saju-specific brand (purple/amber palette, Noto Sans KR captions),
project brief loaded from ``projects/saju-2026-07-03/project.md``, and
the QA gate enabled end-to-end.

Differences from ``saju_run_pipeline_v2.py`` (v2):
  * Brand:  loads ``presets/brands/saju-2026-07-03.json`` via
    ``load_brand_for_job`` (job_id-based resolver). The brand supplies
    a Saju-specific palette, the Noto Sans KR caption font, and a
    ``final_loudness`` of -14 LUFS for the QA gate.
  * Brief:  ``projects/saju-2026-07-03/project.md`` is read via
    ``load_project_brief`` and threaded through ``JobContext`` so the
    export skill can render a "Project Brief" section in report.md.
  * QA gate:  ``SkillsPipeline.run(run_qa=True,
    loudness_target_lufs=...)`` runs ``QASkill`` between music and
    export. The brand's ``final_loudness`` (-14 LUFS) is forwarded so
    the gate checks the right target.
  * Captions:  no longer pins ``CaptionStyle(...)``; the brand-derived
    style is used (Noto Sans KR, brand text color).  ``bottom_padding``
    is still supplied because the preset needs it; the font and color
    come from the brand.

Run with:  uv run python scripts/saju_run_pipeline_v3.py [--with-brand] [--qa]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
JOB_ID = "saju-2026-07-03"

# Source clips live outside the repo — point at the user's media dir.
SOURCE_DIR = Path("/home/harish/Videos/saju/core video")

# Source audio extraction params. 44.1 kHz / stereo / PCM16 is what the
# music skill's `sidechaincompress` filter expects (matches the existing
# `assets/music/default.m4a` test fixture format).
MUSIC_SAMPLE_RATE = 44100
MUSIC_CHANNELS = 2


def _extract_source_music(source_dir: Path, work_dir: Path) -> Path:
    """Concatenate the audio stream of every seq*.mp4 in `source_dir`
    into a single PCM WAV at ``work_dir/background_music.wav``.

    Returns the path to the produced WAV. Raises if no source clips
    are found or ffmpeg fails.
    """
    clips = sorted(
        source_dir.glob("seq*.mp4"),
        key=lambda p: _natural_key(p.stem),
    )
    if not clips:
        sys.exit(f"no seq*.mp4 clips found in {source_dir}")

    list_file = work_dir / "_music_concat.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for clip in clips:
            # Concat demuxer requires single-quoted, escaped paths.
            f.write(f"file '{clip.resolve().as_posix()}'\n")

    output = work_dir / "background_music.wav"
    cmd = [
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-map", "0:a",
        "-c:a", "pcm_s16le",
        "-ac", str(MUSIC_CHANNELS),
        "-ar", str(MUSIC_SAMPLE_RATE),
        str(output),
    ]
    print(f"Extracting source music from {len(clips)} clips -> {output.name}")
    subprocess.run(cmd, check=True)
    list_file.unlink(missing_ok=True)
    return output


def _natural_key(stem: str) -> tuple[int, ...]:
    """``"seq 5"`` -> ``(5,)`` so the sort is numeric, not lexical
    (avoids seq1.mp4, seq10.mp4, seq2.mp4 ordering)."""
    return tuple(int(tok) for tok in stem.replace("seq", "").split() if tok.isdigit())


def _mux_voiceover_onto_video(intake_video: Path, voiceover: Path, out: Path) -> Path:
    """Replace the intake video's audio track with the voiceover. Uses
    ``-c:v copy`` (no re-encode) and AAC for the new audio."""
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(intake_video),
        "-i", str(voiceover),
        "-map", "0:v",
        "-map", "1:a",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(out),
    ]
    print(f"Muxing voiceover onto intake video -> {out.name}")
    subprocess.run(cmd, check=True)
    return out


def _stage_intake(work_dir: Path) -> Path:
    """Stage the concatenated intake video with the voiceover muxed in.

    The voiceover-muxed video is placed at ``work_dir/concatenated_with_vo.mp4``
    (NOT inside the ``intake/`` dir) so the downstream ``RoughCutSkill`` can
    copy it into ``intake/concatenated_with_vo.mp4`` without tripping on a
    same-file error. v1 / v2 placed it inside ``intake/`` and bypassed
    rough_cut; v3 goes through the full ``SkillsPipeline`` so the file
    layout matches what the pipeline expects.

    Returns the path to the staged video (work_dir/concatenated_with_vo.mp4).
    """
    voiceover = work_dir / "voiceover.mp3"
    if not voiceover.exists():
        sys.exit(f"voiceover.mp3 not found at {voiceover}")

    concatenated = work_dir / "concatenated.mp4"
    if not concatenated.exists():
        sys.exit(f"concatenated.mp4 not found at {concatenated}")

    # Place transcript where the graphics + captions skills look.
    transcript_dir = work_dir / "transcript"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        work_dir / "transcript.json",
        transcript_dir / "transcript.json",
    )

    intake_with_vo = work_dir / "concatenated_with_vo.mp4"
    if intake_with_vo.exists():
        # Re-running — the existing voiceover-muxed video is fine.
        print(f"Reusing existing voiceover-muxed video: {intake_with_vo}")
        return intake_with_vo
    _mux_voiceover_onto_video(concatenated, voiceover, intake_with_vo)
    return intake_with_vo


def _stage_rough_cut(work_dir: Path, intake_with_vo: Path) -> None:
    """Materialize the rough_cut artifacts the downstream skills expect.

    v3 reuses the v1 single-keep-segment EDL — the Saju reel is a
    single 60s composition, so there is no real cutting to do.
    """
    from video_editor_core.timeline import EDL, Segment

    edl = EDL(
        job_id=JOB_ID,
        source_path=str(intake_with_vo),
        segments=[
            Segment(
                id="seg_0000",
                source_start=0.0,
                source_end=60.0,
                timeline_start=0.0,
                timeline_end=60.0,
                label="keep speech",
            )
        ],
    )
    plan_dir = work_dir / "plan"
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "edl.json").write_text(
        json.dumps(edl.model_dump(), indent=2), encoding="utf-8"
    )

    rough_cut_dir = work_dir / "rough_cut"
    rough_cut_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(intake_with_vo, rough_cut_dir / "locked.mp4")
    shutil.copyfile(intake_with_vo, rough_cut_dir / "assembly.mp4")
    (rough_cut_dir / "rough_cut.json").write_text(
        json.dumps(
            {
                "source_path": str(intake_with_vo),
                "edl_path": str(plan_dir / "edl.json"),
                "locked_path": str(rough_cut_dir / "locked.mp4"),
                "duration": 60.0,
                "segments": 1,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--with-brand",
        action="store_true",
        help=(
            "Resolve and apply the Saju brand profile "
            "(presets/brands/saju-2026-07-03.json) to captions, music, and "
            "QA. The brand supplies the captions font/color, the music "
            "ducking default, and the final-loudness target."
        ),
    )
    parser.add_argument(
        "--qa",
        action="store_true",
        help=(
            "Run the QA gate between music and export. When the brand's "
            "final_loudness differs from the default -16 LUFS, the gate "
            "checks against the brand target."
        ),
    )
    args = parser.parse_args(argv)

    from graphics.preset import load_brand_for_job
    from video_editor_state.job import JobContext
    from video_editor_state.project_brief import (
        load_project_brief,
    )

    work_root = REPO_ROOT / "projects"
    output_root = REPO_ROOT / "outputs"
    work_dir = work_root / JOB_ID
    output_dir = output_root / JOB_ID
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ----- Step 0: read the project brief (if any) -----------------
    brief = load_project_brief(work_dir)
    if brief is not None:
        print(f"Loaded brief: {brief.title!r} (brand={brief.brand!r})")
    else:
        print("No project.md found — proceeding without a project brief.")

    # ----- Step 1: extract & concat source music --------------------
    music_path = _extract_source_music(SOURCE_DIR, work_dir)
    # Move the music next to the other job artifacts.
    final_music = work_dir / "background_music.wav"
    if music_path != final_music:
        shutil.move(music_path, final_music)
    print(f"Background music: {final_music}")

    # ----- Step 2: stage the rough-cut -----------------------------
    intake_with_vo = _stage_intake(work_dir)
    _stage_rough_cut(work_dir, intake_with_vo)
    print(f"Intake staged:   {intake_with_vo}")

    # ----- Step 3: resolve the brand (if requested) -----------------
    brand = None
    if args.with_brand:
        presets_dir = REPO_ROOT / "presets"
        brand = load_brand_for_job(presets_dir, job_id=JOB_ID)
        print(f"Brand loaded:    {brand.brand!r}")
        # Sanity-check the brand has the keys downstream skills read.
        if not brand.defaults.get("final_loudness"):
            print(
                "  WARNING: brand has no 'final_loudness' default; "
                "QA gate will use the global -16 LUFS target."
            )

    # ----- Step 4: run skills via SkillsPipeline --------------------
    ctx = JobContext(
        job_id=JOB_ID,
        work_root=work_root,
        output_root=output_root,
        project_brief=brief,
    )
    ctx.ensure_dirs()

    from video_editor_cli.pipeline import SkillsPipeline

    loudness_target = (
        float(brand.defaults["final_loudness"])
        if brand is not None and "final_loudness" in brand.defaults
        else None
    )
    pipeline = SkillsPipeline(
        ctx,
        preset="saju_long_form",
        presets_dir=REPO_ROOT / "presets",
        music_path=str(final_music),
    )
    pipeline.run(
        intake_with_vo,
        output_name="saju-reel-v3.mp4",
        run_qa=args.qa,
        loudness_target_lufs=loudness_target,
        expected_aspect_ratio="16:9",
    )
    print(f"Pipeline complete (run_qa={args.qa}, with_brand={args.with_brand}).")


if __name__ == "__main__":
    main()
