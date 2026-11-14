"""Compile a scene plan to HyperFrames and snapshot frames, with no provider calls.

    uv run python scripts/snap_scene_plan.py PLAN_JSON OUT_DIR T1 [T2 ...]

``PLAN_JSON`` is a ``plan/scene_plan.json`` (its media must already exist on
disk). The compiled project lands in ``OUT_DIR/hf`` and the PNG frames in a
fresh child of ``OUT_DIR/frames``; their paths are printed one per line. Use it
to check map and composite plates on real frames before a paid render.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from video_editor_core.hyperframes_cli import snapshot_project
from video_editor_core.scene_plan_composition import compile_scene_plan_project
from video_editor_pipelines.explainer_pipeline import render_plan_document

REPO = Path(__file__).resolve().parents[1]


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 2
    plan_path, out = Path(argv[1]), Path(argv[2])
    times = [float(value) for value in argv[3:]]
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    project = out / "hf"
    shutil.rmtree(project, ignore_errors=True)
    compile_scene_plan_project(render_plan_document(plan), project)
    cli = str(REPO / "node_modules" / ".bin" / "hyperframes")
    for frame in snapshot_project(project, out / "frames", at=times, cli_path=cli):
        print(frame)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
