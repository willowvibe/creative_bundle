"""The musical parity case: the legacy script and the IR, frame for frame.

Marked ``slow`` because it renders two videos (the FFmpeg reference and the
HyperFrames IR path). It is the gate for the port; run it with
``uv run pytest -m slow tests/test_musical_parity.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_core.hyperframes_cli import hyperframes_available

from scripts.parity_rebuild import MUSICAL_FONT, check, run_musical_case

pytestmark = pytest.mark.slow


def test_the_ir_matches_the_script_on_the_musical_fixture(tmp_path: Path) -> None:
    if not MUSICAL_FONT.is_file():
        pytest.skip(f"legacy caption font is missing: {MUSICAL_FONT}")
    if not hyperframes_available():
        pytest.skip("HyperFrames is not available")

    case = run_musical_case(tmp_path)
    assert check(case)
