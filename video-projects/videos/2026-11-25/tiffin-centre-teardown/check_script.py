"""Parse script.md with the explainer engine and check scene count and length.

Run with the video-editor venv, which has the engine installed:
    ../../../../video-editor/.venv/bin/python check_script.py

Telugu words are long, so length is measured in narration characters. The runtime estimate uses
13.2 chars/s, measured from the Harish eleven_v4 Telugu sample at pace 1.0 (204 chars -> 15.5 s),
plus the 0.45 s pause per_scene narration puts between scenes.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE_SRC = HERE.parents[3] / "video-editor" / "packages" / "pipelines" / "src"

try:
    from video_editor_pipelines.explainer_script import load_explainer_script
except ImportError:
    sys.path.insert(0, str(ENGINE_SRC))
    from video_editor_pipelines.explainer_script import load_explainer_script

MIN_SCENES, MAX_SCENES = 18, 24
MIN_CHARS, MAX_CHARS = 4500, 7000
MAX_SCENE_CHARS = 600
CHARS_PER_SECOND = 13.2
SCENE_GAP_S = 0.45


def main(path: Path) -> int:
    script = load_explainer_script(path)
    counts = [len(scene.narration.strip()) for scene in script.scenes]
    total = sum(counts)
    for scene, n in zip(script.scenes, counts):
        print(f"  {n:>4} chars  {scene.title}")
    seconds = total / CHARS_PER_SECOND + SCENE_GAP_S * len(script.scenes)
    print(f"scenes: {len(script.scenes)}  total: {total} chars  est: {seconds / 60:.1f} min")

    problems = []
    if not MIN_SCENES <= len(script.scenes) <= MAX_SCENES:
        problems.append(f"scene count {len(script.scenes)} outside {MIN_SCENES}-{MAX_SCENES}")
    if not MIN_CHARS <= total <= MAX_CHARS:
        problems.append(f"total {total} chars outside {MIN_CHARS}-{MAX_CHARS}")
    problems += [
        f"{scene.title}: {n} chars > {MAX_SCENE_CHARS}"
        for scene, n in zip(script.scenes, counts)
        if n > MAX_SCENE_CHARS
    ]
    if problems:
        print("\n".join(problems))
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "script.md"
    raise SystemExit(main(target))
