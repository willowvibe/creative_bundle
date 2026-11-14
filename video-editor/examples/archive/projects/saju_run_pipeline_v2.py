"""v2 pipeline for the Saju reel: 16:9 long-form, bottom captions, and
background music (extracted from the source clips) mixed under the
voiceover with sidechain ducking.

Differences from ``saju_run_pipeline.py`` (v1):
  * Preset:  ``saju_long_form`` (1280x720, 16:9) instead of
    ``short_explainer`` (1080x1920, 9:16). Source is 1280x720, so v2 is
    full-bleed with no letterbox.
  * Captions: ``CaptionStyle(bottom_padding=60, font_size=56,
    border_width=6)`` — animated word-by-word, anchored to the bottom
    of the frame with a thicker border for legibility.
  * Music:  the six source ``seq*.mp4`` clips carry AAC audio. We
    extract & concatenate just the audio streams into a single
    ``background_music.wav`` (stereo, 44.1 kHz) and mix it under the
    voiceover with ``MusicSkill`` (``sidechaincompress`` ducking).
  * Voiceover:  muxed into the intake video's audio track so the
    existing skills (which read audio from the input video, not from
    a sidecar MP3) pick it up.
  * Output:  ``outputs/saju-2026-07-03/saju-reel-16x9.mp4`` — the v1
    portrait export is preserved untouched.

Run with:  uv run python scripts/saju_run_pipeline_v2.py
"""

from __future__ import annotations

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


def main() -> None:
    from video_editor_state.job import JobContext

    work_root = REPO_ROOT / "projects"
    output_root = REPO_ROOT / "outputs"
    work_dir = work_root / JOB_ID
    output_dir = output_root / JOB_ID
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    voiceover = work_dir / "voiceover.mp3"
    if not voiceover.exists():
        sys.exit(f"voiceover.mp3 not found at {voiceover}")

    # ----- Step 1: extract & concat source music --------------------
    music_path = _extract_source_music(SOURCE_DIR, work_dir)
    # Move the music next to the other job artifacts.
    final_music = work_dir / "background_music.wav"
    if music_path != final_music:
        shutil.move(music_path, final_music)
    print(f"Background music: {final_music}")

    # ----- Step 2: stage the rough-cut (same as v1) -----------------
    concatenated = work_dir / "concatenated.mp4"
    if not concatenated.exists():
        sys.exit(f"concatenated.mp4 not found at {concatenated}")

    intake = work_dir / "intake"
    intake.mkdir(parents=True, exist_ok=True)
    intake_source = intake / "concatenated.mp4"
    shutil.copyfile(concatenated, intake_source)

    # Mux the voiceover onto the intake video's audio track so the
    # downstream skills (graphics / music) can read the voice from the
    # input file's audio stream — the same way rough_cut + music expect.
    intake_with_vo = intake / "concatenated_with_vo.mp4"
    _mux_voiceover_onto_video(intake_source, voiceover, intake_with_vo)
    intake_manifest = {
        "source_path": str(intake_with_vo),
        "duration": 60.0,
    }
    (intake / "manifest.json").write_text(
        json.dumps(intake_manifest, indent=2), encoding="utf-8"
    )

    # Place transcript where the graphics + captions skills look.
    transcript_dir = work_dir / "transcript"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        work_dir / "transcript.json",
        transcript_dir / "transcript.json",
    )

    # Single kept segment that spans the full 60s.
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

    # rough_cut/locked.mp4 is what GraphicsSkill reads.
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

    # ----- Step 3: run graphics + captions + music + export --------
    ctx = JobContext(
        job_id=JOB_ID,
        work_root=work_root,
        output_root=output_root,
    )
    ctx.ensure_dirs()

    from captions.captions import CaptionsSkill
    from captions.models import CaptionStyle
    from export.export import ExportSkill
    from graphics.graphics import GraphicsSkill
    from music.models import MusicStyle
    from music.music import MusicSkill

    # 3a. Graphics at 1280x720.
    graphics = GraphicsSkill(ctx, presets_dir=REPO_ROOT / "presets")
    graphics_video = graphics.run(preset_name="saju_long_form")
    print(f"Graphics:  {graphics_video}")

    # 3b. Captions with bottom_padding=60 (60px from bottom) and a
    # thicker, larger font for legibility.
    caption_style = CaptionStyle(
        font_size=56,
        border_width=6,
        bottom_padding=60,
    )
    captions = CaptionsSkill(ctx, graphics_skill=graphics, style=caption_style)
    captions_video = captions.run()
    print(f"Captions:  {captions_video}")

    # 3c. Background music under the voiceover with strong ducking so
    # the voiceover stays clear. music_gain_db=-14dB keeps the music
    # well below speech; duck_ratio=20 is aggressive enough to make
    # music a bed, not a competing source.
    music_style = MusicStyle(
        music_path=str(final_music),
        music_gain_db=-14.0,
        duck_ratio=20.0,
    )
    music = MusicSkill(ctx, style=music_style)
    music_video = music.run()
    print(f"Music:     {music_video}")

    # 3d. Export the final long-form 16:9 reel.
    export = ExportSkill(ctx)
    final = export.run(output_name="saju-reel-16x9.mp4")
    print(f"Export:    {final}")


if __name__ == "__main__":
    main()
