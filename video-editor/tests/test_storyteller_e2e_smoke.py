"""End-to-end wiring smoke for the storyteller CLI pipeline.

Mocks ONLY the planner's asset-generation + LLM surface
(``build_storyteller_scene_plan``) with a hand-built REAL ``ScenePlan`` so
``write_scene_plan`` and the downstream ``storyteller_to_fablecut`` exporter
run for real on real data. The goal is to prove the CLI commands connect
end-to-end:

    ve run <fixture> --template storyteller_hd
        -> <work>/<job_id>/plan/scene_plan.json
    ve storyteller-export <job_id> --project-dir <work>/<job_id>/plan/fablecut
        -> <work>/<job_id>/plan/fablecut/project.json

This is a wiring smoke, NOT a re-test of the planner's LLM/asset-gen
internals (those are unit-tested in ``test_storyteller_planner.py`` and
``test_storyteller_fablecut_export.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner
from video_editor_asset_ingestion.models import AssetResult, AssetType
from video_editor_cli.main import app
from video_editor_core.models.scene_plan import Scene, ScenePlan

FIXTURE = Path(__file__).parent / "fixtures" / "storyteller" / "sample_story.md"


def _make_scene(
    scene_id: str,
    speaker: str,
    language: str,
    voice_id: str,
    needs_motion: bool,
    image_path: Path,
    audio_path: Path,
) -> Scene:
    """Build a real Scene with real AssetResult objects.

    Mirrors the helper in ``test_storyteller_fablecut_export.py`` so the
    exporter's ``_copy_assets`` step finds real files on disk.
    """
    return Scene(
        id=scene_id,
        type="story",
        duration_seconds=5.0,
        narration="hello",
        visual_description="a wide shot",
        asset_query="",
        props={
            "speaker": speaker,
            "language": language,
            "voice_id": voice_id,
            "needs_motion": needs_motion,
            "render_track": "video" if needs_motion else "image",
            "image_model": "gpt-image-2",
            "motion_model": "seedance-2-fast" if needs_motion else None,
            "sfx_prompts": [],
        },
        assets=[
            AssetResult(
                asset_id=f"{scene_id}_visual",
                asset_type=AssetType.VIDEO if needs_motion else AssetType.IMAGE,
                local_path=str(image_path),
                scene_id=scene_id,
                duration_ms=4000,
                metadata={},
            ),
            AssetResult(
                asset_id=f"{scene_id}_audio",
                asset_type=AssetType.TTS,
                local_path=str(audio_path),
                scene_id=scene_id,
                duration_ms=2000,
                metadata={"voice_id": voice_id, "language": language},
            ),
        ],
    )


def _build_real_plan(tmp_path: Path) -> ScenePlan:
    """Hand-build a 3-scene ScenePlan matching ``sample_story.md``.

    Speakers/languages/voice_ids mirror the fixture's front-matter:
      - narrator  : elevenlabs:Brian / en         (needs_motion=False)
      - king      : sarvam:shubh     / hi-IN      (needs_motion=True)
      - princess  : sarvam:anushka   / en-IN      (needs_motion=False)

    Fake asset bytes are written into a tmp ``assets/`` dir so the
    exporter's ``_copy_assets`` step sees ``src.exists() == True``.
    """
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    def _asset_pair(scene_id: str, needs_motion: bool) -> tuple[Path, Path]:
        img_name = f"{scene_id}_img.png"
        aud_name = f"{scene_id}_aud.mp3"
        img_path = assets_dir / img_name
        aud_path = assets_dir / aud_name
        img_path.write_bytes(b"FAKE_PNG")
        aud_path.write_bytes(b"FAKE_MP3")
        return img_path, aud_path

    scenes: list[Scene] = []
    for scene_id, speaker, language, voice_id, needs_motion in [
        ("scene_001", "narrator", "en", "elevenlabs:Brian", False),
        ("scene_002", "king", "hi-IN", "sarvam:shubh", True),
        ("scene_003", "princess", "en-IN", "sarvam:anushka", False),
    ]:
        img_path, aud_path = _asset_pair(scene_id, needs_motion)
        scenes.append(
            _make_scene(
                scene_id,
                speaker,
                language,
                voice_id,
                needs_motion,
                img_path,
                aud_path,
            )
        )

    music_path = tmp_path / "music" / "bg.mp3"
    music_path.parent.mkdir(parents=True, exist_ok=True)
    music_path.write_bytes(b"FAKE_MUSIC")

    return ScenePlan(
        job_id="smoke",
        script="",
        scenes=scenes,
        aspect_ratio="16:9",
        music_mood="cinematic",
        music_path=str(music_path),
    )


def _invoke_run(
    tmp_path: Path,
    *,
    image_tier: str | None = None,
    video_tier: str | None = None,
) -> Any:
    """Invoke ``ve run <fixture>`` with the storyteller_hd template."""
    runner = CliRunner()
    args: list[str] = [
        "run",
        str(FIXTURE),
        "--job-id",
        "smoke",
        "--template",
        "storyteller_hd",
        "--work-root",
        str(tmp_path / "work"),
        "--output-root",
        str(tmp_path / "out"),
    ]
    if image_tier is not None:
        args += ["--image-tier", image_tier]
    if video_tier is not None:
        args += ["--video-tier", video_tier]
    return runner.invoke(app, args)


def _invoke_export(tmp_path: Path) -> Any:
    """Invoke ``ve storyteller-export smoke --project-dir <work>/smoke/plan/fablecut``.

    Path alignment (controller-verified): ``ve run`` writes the plan to
    ``<work>/smoke/plan/scene_plan.json``. ``ve storyteller-export`` reads
    ``plan_path = target.parent / "scene_plan.json"``; passing
    ``--project-dir <work>/smoke/plan/fablecut`` makes ``target.parent`` =
    ``<work>/smoke/plan``, so ``plan_path`` matches the run's output.
    """
    runner = CliRunner()
    project_dir = tmp_path / "work" / "smoke" / "plan" / "fablecut"
    return runner.invoke(
        app,
        ["storyteller-export", "smoke", "--project-dir", str(project_dir)],
    )


@pytest.mark.parametrize(
    "image_tier,video_tier,expected_image,expected_video",
    [
        (None, None, None, None),
        ("premium", "budget", "premium", "budget"),
    ],
    ids=["default_tiers", "premium_budget_override"],
)
def test_storyteller_e2e_wiring_smoke(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    image_tier: str | None,
    video_tier: str | None,
    expected_image: str | None,
    expected_video: str | None,
) -> None:
    """ve run -> scene_plan.json -> ve storyteller-export -> fablecut project.json.

    Mocks ``build_storyteller_scene_plan`` with a REAL ScenePlan return
    value so ``write_scene_plan`` and the downstream ``storyteller_to_fablecut``
    exporter run for real on real data. Validates:
      - ``ve run`` exits 0 and writes ``scene_plan.json`` with 3 scenes.
      - The mock was awaited once with the right ``template_name`` and tiers.
      - ``ve storyteller-export`` exits 0 and writes a FableCut ``project.json``
        containing a ``tracks`` key.
    """
    # Deterministic config load (no --config -> fall back to a CWD
    # config.yaml rather than the developer's repo-root config.yaml). The
    # planner is mocked, so the ModelRouter is never used for real LLM
    # calls — we just need *something* valid so the storyteller branch's
    # config-fallback guard accepts it.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(
        "llm:\n"
        "  default_provider: ollama_cloud_pro\n"
        "  providers:\n"
        "    ollama_cloud_pro:\n"
        "      name: ollama_cloud_pro\n"
        "      base_url: https://api.ollama.com\n"
        "      api_key_env: OLLAMA_CLOUD_API_KEY\n"
        "      default_model: kimi-k2.6:cloud\n"
        "  tasks:\n"
        "    scene_plan: { task_type: scene_plan, provider: ollama_cloud_pro, model: minimax-m3:cloud }\n",
        encoding="utf-8",
    )

    real_plan = _build_real_plan(tmp_path)

    with patch(
        "video_editor_cli.main.build_storyteller_scene_plan",
        new=AsyncMock(return_value=real_plan),
    ) as mock_build:
        result = _invoke_run(
            tmp_path,
            image_tier=image_tier,
            video_tier=video_tier,
        )

    assert result.exit_code == 0, result.output
    mock_build.assert_awaited_once()
    kwargs = mock_build.call_args.kwargs
    assert kwargs["template_name"] == "storyteller_hd"
    assert kwargs["image_tier"] == expected_image
    assert kwargs["video_tier"] == expected_video

    plan_path = tmp_path / "work" / "smoke" / "plan" / "scene_plan.json"
    assert plan_path.exists(), f"scene_plan.json not written at {plan_path}"
    parsed = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    assert len(parsed.scenes) == 3

    export_result = _invoke_export(tmp_path)
    assert export_result.exit_code == 0, export_result.output

    project_path = tmp_path / "work" / "smoke" / "plan" / "fablecut" / "project.json"
    assert project_path.exists(), f"project.json not written at {project_path}"
    import json

    payload = json.loads(project_path.read_text(encoding="utf-8"))
    assert "tracks" in payload
