"""Rebuild two shipped videos from their saved inputs and compare frames.

Both cases are fully offline: they read a saved scene plan, stills, narration
and a rendered base video, and use only HyperFrames and FFmpeg. No provider is
called and nothing is spent.

    uv run python scripts/parity_rebuild.py
    uv run python scripts/parity_rebuild.py --case scene-plan
    uv run python scripts/parity_rebuild.py --case musical

Exit status is 0 only when every assertion passes, 1 when an assertion fails,
and 2 when a prerequisite artifact is missing. ``projects/`` and ``videos/`` are
git-ignored build output, so a fresh checkout has to produce them first; the
reference renders alone are 166 MB and stay untracked. If SSIM misses, fix the
primitive defaults or the adapter mapping — never the threshold.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIZ = ROOT / "projects" / "wiz-explainer"
SAJU = ROOT / "projects" / "saju-january-animal"
SAJU_BUILD = ROOT / "videos" / "2026-10-23" / "saju-january-animal" / "build"
WORK = ROOT / "qa" / "parity"

WHOLE_MIN_SSIM = 0.98
SCENE_MIN_SSIM = 0.95
BOUNDARY_MIN_SSIM = 0.95
FPS = 30
#: The legacy caption burn-in reads this host font; the IR path bundles its own.
MUSICAL_FONT = Path("/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf")

#: Everything the two cases read, keyed by case, each with the way to produce
#: it. ``projects/`` and ``videos/`` are git-ignored build output, so a clean
#: checkout has none of them and the harness cannot run at all — the preflight
#: below says so once, with a remedy per path, instead of failing deep inside a
#: case. Per case, so ``--case scene-plan`` is not blocked by a missing saju
#: build.
PREREQUISITES: dict[str, tuple[tuple[Path, str], ...]] = {
    "scene-plan": (
        (
            WIZ / "plan" / "scene_plan.json",
            "build the explainer so the plan is written (ve explain …)",
        ),
        (
            WIZ / "composition" / "rendered.mp4",
            "the video that shipped before the migration; recover it from the "
            "delivery copy rather than re-rendering (a re-render is circular)",
        ),
    ),
    "render-graph": (
        (
            SAJU / "hyperframes" / "captioned.mp4",
            "the captioned video that shipped for projects/saju-january-animal",
        ),
        (
            SAJU / "hyperframes" / "rendered.mp4",
            "the base render that build/overlays.py captions",
        ),
        (
            SAJU / "assets" / "tts" / "timeline.json",
            "the TTS timeline the caption case reads its scene windows from",
        ),
        (
            SAJU_BUILD / "overlays.py",
            "the per-project overlay script the caption case re-runs",
        ),
    ),
    "musical": (
        (
            MUSICAL_FONT,
            "install the Noto Devanagari face (Debian/Ubuntu: fonts-noto-core)",
        ),
    ),
}


def prerequisites_for(case: str) -> tuple[tuple[Path, str], ...]:
    """The entries ``case`` needs; ``all`` needs every case's entries."""
    if case == "all":
        return tuple(entry for entries in PREREQUISITES.values() for entry in entries)
    return PREREQUISITES[case]


def missing_prerequisites(
    entries: Sequence[tuple[Path, str]],
) -> list[tuple[Path, str]]:
    """The entries whose file is absent, each with its remedy."""
    return [(path, remedy) for path, remedy in entries if not Path(path).is_file()]


def within_one_frame(expected: float, actual: float, fps: int = FPS) -> bool:
    if not (math.isfinite(expected) and math.isfinite(actual)):
        return False
    return abs(expected - actual) <= 1.0 / fps + 1e-6


def scene_windows(timeline: dict) -> list[tuple[float, float]]:
    return [
        (float(scene["start"]), float(scene["end"]))
        for scene in timeline.get("scenes", []) or []
        if "start" in scene and "end" in scene
    ]


def ssim(
    reference: Path,
    candidate: Path,
    *,
    start: float | None = None,
    duration: float | None = None,
) -> float:
    if start is not None and duration is None:
        raise ValueError("ssim needs both start and duration, or neither")

    def _input(path: Path) -> list[str]:
        if start is None:
            return ["-i", str(path)]
        return ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(path)]

    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "info",
        *_input(reference),
        *_input(candidate),
        "-lavfi",
        "[0:v][1:v]ssim",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg ssim failed for {reference} vs {candidate} (exit {result.returncode})"
        )
    for line in (result.stderr or "").splitlines():
        if "SSIM" in line and "All:" in line:
            return float(line.split("All:", 1)[1].split()[0])
    raise RuntimeError(f"no SSIM produced for {reference} vs {candidate}")


def duration_of(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


#: The shipped render each case compares against, keyed by case. Selection is
#: per case so ``--case scene-plan`` does not need the saju build to exist.
REFERENCE_PAIRS: dict[str, tuple[tuple[Path, str], ...]] = {
    "scene-plan": ((WIZ / "composition" / "rendered.mp4", "wiz-rendered.mp4"),),
    "render-graph": ((SAJU / "hyperframes" / "captioned.mp4", "saju-captioned.mp4"),),
    # The musical case generates its reference from the fixture, so there is
    # nothing to copy from a shipped build.
    "musical": (),
}


def copy_references(case: str = "all") -> Path:
    """Copy the reference renders so a rebuild can never overwrite them."""
    target = WORK / "reference"
    target.mkdir(parents=True, exist_ok=True)
    wanted = (
        REFERENCE_PAIRS[case]
        if case in REFERENCE_PAIRS
        else tuple(pair for pairs in REFERENCE_PAIRS.values() for pair in pairs)
    )
    for source, name in wanted:
        destination = target / name
        if not source.is_file():
            raise FileNotFoundError(f"missing parity input: {source}")
        if not destination.exists() or source.stat().st_mtime > destination.stat().st_mtime:
            shutil.copyfile(source, destination)
            print(f"reference: {destination.name}")
    return target


def run_scene_plan_case(reference: Path) -> dict:
    from video_editor_cli.composition_qa import gather_composition_qa
    from video_editor_core.scene_plan_composition import render_scene_plan

    work = WORK / "scene-plan"
    work.mkdir(parents=True, exist_ok=True)
    output = work / "rendered.mp4"
    project = work / "project"
    render_scene_plan(WIZ / "plan" / "scene_plan.json", output, project)
    plan = json.loads((WIZ / "plan" / "scene_plan.json").read_text(encoding="utf-8"))
    # §7.1 also requires the QA gates on the rebuild. ``gather_composition_qa``
    # fails closed, so a gate that cannot measure becomes a failing check.
    checks = gather_composition_qa(
        composition_dir=project,
        plan=plan,
        frame_dir=work / "frames",
    )
    return {
        "name": "scene-plan",
        "reference": reference / "wiz-rendered.mp4",
        "candidate": output,
        "windows": _windows_from_plan(plan),
        "composition_checks": checks,
    }


def caption_delays(html: str) -> list[list[float]]:
    """The animation delays of every ``class="ve-word"`` span, in order.

    ``compile_composition`` emits one inline ``animation:`` shorthand per
    word-span animation: ``name duration timing-function delay ...``. The
    fourth whitespace-separated token is therefore the delay. Parsing the
    committed markup (rather than rebuilding the expectation from the plan) is
    what makes this a comparison of two independently produced files.
    """
    spans = re.findall(r'<span class="ve-word" style="([^"]*)">', html)
    delays: list[list[float]] = []
    for style in spans:
        match = re.search(r'animation:([^;"]+)', style)
        values: list[float] = []
        if match is not None:
            for declaration in match.group(1).split(","):
                tokens = declaration.split()
                if len(tokens) < 4:
                    continue
                try:
                    values.append(float(tokens[3].removesuffix("s")))
                except ValueError:
                    values.append(math.nan)
        delays.append(values)
    return delays


def run_render_graph_case(reference: Path) -> dict:
    work = WORK / "render-graph"
    work.mkdir(parents=True, exist_ok=True)
    # overlays.py rewrites hyperframes/captioned.mp4 and its compiled
    # ``captioned.hf/index.html`` in place, and the reference copy was taken
    # from those very files. Keep the committed bytes in memory and put them
    # back once the fresh outputs have been copied/parsed, so the shipped
    # artifacts are never left modified by a parity run.
    committed_path = SAJU / "hyperframes" / "captioned.mp4"
    compiled_html = SAJU / "hyperframes" / "captioned.hf" / "index.html"
    committed = {
        path: (path.read_bytes() if path.is_file() else None)
        for path in (committed_path, compiled_html)
    }
    html_mtime = compiled_html.stat().st_mtime_ns if compiled_html.is_file() else None
    committed_html = committed[compiled_html]
    rebuilt_html: bytes | None = None
    try:
        subprocess.run(
            [sys.executable, str(SAJU_BUILD / "overlays.py")], check=True, cwd=SAJU_BUILD
        )
        candidate = work / "captioned.mp4"
        shutil.copyfile(committed_path, candidate)
        rebuilt_html = compiled_html.read_bytes() if compiled_html.is_file() else None
        regenerated = compiled_html.is_file() and (
            html_mtime is None or compiled_html.stat().st_mtime_ns != html_mtime
        )
    finally:
        for path, original in committed.items():
            if original is None:
                continue
            rebuilt = path.read_bytes() if path.is_file() else None
            if rebuilt != original:
                path.write_bytes(original)
                print(f"[render-graph] committed {path.name} restored (rebuilt bytes differed)")
            else:
                print(f"[render-graph] rebuilt {path.name} is identical to the committed file")

    timeline = json.loads((SAJU / "assets" / "tts" / "timeline.json").read_text(encoding="utf-8"))
    case = {
        "name": "render-graph",
        "reference": reference / "saju-captioned.mp4",
        "candidate": candidate,
        "windows": scene_windows(timeline),
    }
    if committed_html is None:
        case["caption_error"] = f"missing committed HTML at {compiled_html}"
    elif rebuilt_html is None or not regenerated:
        case["caption_error"] = (
            "the rebuild did not regenerate the compiled HTML "
            f"({compiled_html}); cannot assert caption spans"
        )
    else:
        case["captions"] = (
            caption_delays(committed_html.decode("utf-8")),
            caption_delays(rebuilt_html.decode("utf-8")),
        )
    return case


def _run_command(cmd: Sequence[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\n"
            f"{(result.stderr or '')[-2000:]}"
        )


def _generate_musical_inputs(directory: Path) -> tuple[Path, Path, Path, Path]:
    """Create the tiny offline fixture; cached so repeated runs are fast."""
    directory.mkdir(parents=True, exist_ok=True)
    still = directory / "still.png"
    clip = directory / "clip.mp4"
    music = directory / "music.mp3"
    lyrics = directory / "lyrics.txt"
    if not still.is_file():
        _run_command(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x1F2A44:s=1280x720:d=1",
                "-frames:v",
                "1",
                str(still),
            ]
        )
    if not clip.is_file():
        _run_command(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=1280x720:rate=30:duration=6",
                "-pix_fmt",
                "yuv420p",
                str(clip),
            ]
        )
    if not music.is_file():
        _run_command(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=8",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "192k",
                str(music),
            ]
        )
    lyrics.write_text("पहला मुक़ाम\nदूसरा मुक़ाम\nतीसरा मुक़ाम\n", encoding="utf-8")
    return directory, music, lyrics, still


def run_musical_case(work_dir: Path | None = None) -> dict:
    """Render the same musical fixture through the script and the IR."""
    from video_editor_pipelines.musical_pipeline import run_musical_job
    from video_editor_state.job import JobContext

    work = Path(work_dir) if work_dir is not None else WORK / "musical"
    work.mkdir(parents=True, exist_ok=True)
    media, music, lyrics, _still = _generate_musical_inputs(work / "inputs")

    reference_base = work / "reference.mp4"
    reference = work / "reference-captions.mp4"
    candidate = work / "candidate.mp4"

    _run_command(
        [
            sys.executable,
            str(ROOT / "scripts" / "compose_musical_video.py"),
            "--music",
            str(music),
            "--media-dir",
            str(media),
            "--output",
            str(reference_base),
            "--segment-seconds",
            "4",
            "--transition-duration",
            "1",
            "--width",
            "1280",
            "--height",
            "720",
        ]
    )
    _run_command(
        [
            sys.executable,
            str(ROOT / "scripts" / "burn_musical_captions.py"),
            "--base-video",
            str(reference_base),
            "--lyrics",
            str(lyrics),
            "--output",
            str(reference),
        ]
    )

    ctx = JobContext(
        job_id="parity-musical",
        work_root=work / "ir-work",
        output_root=work / "ir-out",
    )
    result = run_musical_job(
        ctx,
        music_path=music,
        media_dir=media,
        lyrics_path=lyrics,
        output_path=candidate,
        width=1280,
        height=720,
        fps=FPS,
        segment_seconds=4.0,
        transition_seconds=1.0,
    )
    plan = json.loads(result.composition_path.read_text(encoding="utf-8"))
    windows = [(float(scene["start"]), float(scene["end"])) for scene in plan["scenes"]]
    boundaries = [(start - 0.5, start + 0.5) for start, _end in windows[1:]]
    return {
        "name": "musical",
        "reference": reference,
        "candidate": candidate,
        "windows": windows,
        "boundary_windows": boundaries,
        "composition_checks": result.checks,
        # The musical reference is the legacy script plus drawtext, not a
        # shipped HyperFrames render; the plan's criterion is 0.95 per scene
        # and boundary, so the whole-video floor is looser than the shipped
        # cases' 0.98.
        "whole_min": 0.95,
    }


def _windows_from_plan(plan: dict) -> list[tuple[float, float]]:
    windows: list[tuple[float, float]] = []
    cursor = 0.0
    for scene in plan.get("scenes", []) or []:
        length = float(scene.get("duration_seconds", 0.0))
        if length <= 0:
            continue
        windows.append((cursor, cursor + length))
        cursor += length
    return windows


def check(case: dict) -> bool:
    name = case["name"]
    if not case["windows"]:
        print(f"[{name}] FAIL no scene windows")
        return False

    reference = case["reference"]
    candidate = case["candidate"]
    ok = True

    if not within_one_frame(duration_of(reference), duration_of(candidate)):
        print(
            f"[{name}] FAIL duration {duration_of(candidate):.3f} vs {duration_of(reference):.3f}"
        )
        ok = False
    else:
        print(f"[{name}] duration {duration_of(candidate):.3f}s")

    whole = ssim(reference, candidate)
    whole_min = float(case.get("whole_min", WHOLE_MIN_SSIM))
    print(f"[{name}] whole-video SSIM {whole:.4f} (min {whole_min})")
    ok = ok and whole >= whole_min

    for index, (start, end) in enumerate(case["windows"]):
        score = ssim(reference, candidate, start=start, duration=max(0.1, end - start))
        print(f"[{name}] scene {index} [{start:.2f}-{end:.2f}] SSIM {score:.4f}")
        ok = ok and score >= SCENE_MIN_SSIM

    for index, (start, end) in enumerate(case.get("boundary_windows", [])):
        score = ssim(reference, candidate, start=start, duration=max(0.1, end - start))
        print(
            f"[{name}] boundary {index} [{start:.2f}-{end:.2f}] SSIM {score:.4f} "
            f"(min {BOUNDARY_MIN_SSIM})"
        )
        ok = ok and score >= BOUNDARY_MIN_SSIM

    for gate in case.get("composition_checks", []):
        print(f"[{name}] gate {gate.name}: {'PASS' if gate.ok else 'FAIL'} {gate.detail}")
        ok = ok and gate.ok

    if "caption_error" in case:
        print(f"[{name}] FAIL {case['caption_error']}")
        ok = False
    if "captions" in case:
        reference_spans, candidate_spans = case["captions"]
        if len(reference_spans) != len(candidate_spans):
            print(
                f"[{name}] FAIL caption span count {len(candidate_spans)} vs {len(reference_spans)}"
            )
            ok = False
        else:
            mismatches = 0
            worst = 0.0
            for expected, actual in zip(reference_spans, candidate_spans, strict=True):
                if len(expected) != len(actual):
                    mismatches += 1
                    worst = math.inf
                    continue
                for reference_delay, candidate_delay in zip(expected, actual, strict=True):
                    if not within_one_frame(reference_delay, candidate_delay):
                        mismatches += 1
                        if math.isfinite(reference_delay) and math.isfinite(candidate_delay):
                            worst = max(worst, abs(reference_delay - candidate_delay))
                        else:
                            worst = math.inf
            print(
                f"[{name}] caption spans {len(reference_spans)}, "
                f"{mismatches} delay mismatches (worst {worst:.3f}s)"
            )
            ok = ok and mismatches == 0

    print(f"[{name}] {'PASS' if ok else 'FAIL'}")
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--case",
        choices=["scene-plan", "render-graph", "musical", "all"],
        default="all",
    )
    args = parser.parse_args(argv)

    missing = missing_prerequisites(prerequisites_for(args.case))
    if missing:
        print("parity prerequisites are missing from this checkout:")
        for path, remedy in missing:
            print(f"  {path}\n    -> {remedy}")
        print(
            "These are git-ignored build artifacts, so a fresh checkout must "
            "produce or recover them first."
        )
        return 2

    reference = copy_references(args.case)
    cases = []
    if args.case in {"scene-plan", "all"}:
        cases.append(run_scene_plan_case(reference))
    if args.case in {"render-graph", "all"}:
        cases.append(run_render_graph_case(reference))
    if args.case in {"musical", "all"}:
        cases.append(run_musical_case())
    ok = True
    for case in cases:
        ok = check(case) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
