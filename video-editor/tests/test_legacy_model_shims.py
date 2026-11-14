"""The old engine model paths remain importable for one release."""

from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "edit_plan",
        "job",
        "media",
        "motion",
        "scene_plan",
        "storyteller_script",
        "transcript",
    ],
)
def test_legacy_model_shim_warns(module: str) -> None:
    name = f"video_editor_engine.models.{module}"
    with pytest.warns(DeprecationWarning, match="moved to video_editor_core"):
        importlib.import_module(name)
