"""Stage the Saju job for a manual rough-cut that wraps the full
60s concatenated source as a single kept segment, then run the
graphics → captions → export skills directly.

The source concatenated.mp4 has a silent audio track (the source
clips were filmed with no live audio). v1 previously left that
silence in place, which produced a final video with no voiceover
even though ``voiceover.mp3`` was sitting in the work directory.
v2 introduced a voiceover mux step. v1 mirrors that here so the
portrait export (saju-reel.mp4) is no longer silent.

Run with:  uv run python scripts/saju_run_pipeline.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
JOB_ID = "saju-2026-07-03"


def _mux_voiceover_onto_video(intake_video: Path, voiceover: Path, out: Path) -> Path:
    """Replace the intake video's audio track with the voiceover. Uses
    ``-c:v copy`` (no re-encode) and AAC for the new audio.

    Mirrors the helper in ``saju_run_pipeline_v2.py`` so a 9:16
    portrait run and a 16:9 long-form run share the same voiceover-
    mux semantics.
    """
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


def main() -> None:
    from video_editor_core.timeline import EDL, Segment
    from video_editor_state.job import JobContext

    work_root = REPO_ROOT / "projects"
    output_root = REPO_ROOT / "outputs"
    work_dir = work_root / JOB_ID
    output_dir = output_root / JOB_ID
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    source = work_dir / "concatenated.mp4"
    if not source.exists():
        sys.exit(f"concatenated.mp4 not found at {source}")

    voiceover = work_dir / "voiceover.mp3"
    if not voiceover.exists():
        sys.exit(f"voiceover.mp3 not found at {voiceover}")

    ctx = JobContext(
        job_id=JOB_ID,
        work_root=work_root,
        output_root=output_root,
    )
    ctx.ensure_dirs()

    # The source already lives at the work root, but rough_cut/intake
    # is where the skill expects the proxy. Copy it in, then mux the
    # voiceover onto it (Bug #34 — the source's audio is silent).
    intake = work_dir / "intake"
    intake.mkdir(parents=True, exist_ok=True)
    intake_silent = intake / "concatenated.mp4"
    shutil.copyfile(source, intake_silent)
    intake_with_vo = intake / "concatenated_with_vo.mp4"
    if not intake_with_vo.exists():
        _mux_voiceover_onto_video(intake_silent, voiceover, intake_with_vo)
    intake_manifest = {
        "source_path": str(intake_with_vo),
        "duration": 60.0,
    }
    (intake / "manifest.json").write_text(json.dumps(intake_manifest, indent=2), encoding="utf-8")

    # Place the transcript where the graphics + captions skills look.
    transcript_dir = work_dir / "transcript"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        REPO_ROOT / "projects" / JOB_ID / "transcript.json",
        transcript_dir / "transcript.json",
    )

    # EDL: a single kept segment that spans the full 60s.
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
    (plan_dir / "edl.json").write_text(json.dumps(edl.model_dump(), indent=2), encoding="utf-8")

    # Use the voiceover-muxed source as the rough_cut "locked" output.
    # (Previously v1 used the silent concatenated.mp4, which produced
    # silent final videos — Bug #34.)
    rough_cut_dir = work_dir / "rough_cut"
    rough_cut_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(intake_with_vo, rough_cut_dir / "locked.mp4")
    shutil.copyfile(intake_with_vo, rough_cut_dir / "assembly.mp4")
    rough_cut_manifest = {
        "source_path": str(intake_with_vo),
        "edl_path": str(plan_dir / "edl.json"),
        "locked_path": str(rough_cut_dir / "locked.mp4"),
        "duration": 60.0,
        "segments": 1,
    }
    (rough_cut_dir / "rough_cut.json").write_text(
        json.dumps(rough_cut_manifest, indent=2), encoding="utf-8"
    )

    print("Manual rough-cut staged.")
    print(f"  source:     {source}")
    print(f"  intake:     {intake_with_vo}")
    print(f"  locked:     {rough_cut_dir / 'locked.mp4'}")
    print(f"  edl:        {plan_dir / 'edl.json'}")
    print(f"  transcript: {transcript_dir / 'transcript.json'}")

    # Now run graphics + captions + export directly.
    from captions.captions import CaptionsSkill
    from export.export import ExportSkill
    from graphics.graphics import GraphicsSkill

    graphics = GraphicsSkill(ctx, presets_dir=REPO_ROOT / "presets")
    graphics_video = graphics.run(preset_name="short_explainer")
    print(f"Graphics: {graphics_video}")

    captions = CaptionsSkill(ctx, graphics_skill=graphics)
    captions_video = captions.run()
    print(f"Captions: {captions_video}")

    export = ExportSkill(ctx)
    final = export.run(output_name="saju-reel.mp4")
    print(f"Export:   {final}")


if __name__ == "__main__":
    main()
