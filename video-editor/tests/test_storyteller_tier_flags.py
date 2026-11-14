"""Tests that --image-tier and --video-tier flags are validated and forwarded."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner
from video_editor_cli.main import app
from video_editor_llm.router import ModelRouter

VALID_TIERS = ("budget", "standard", "premium")


@pytest.mark.parametrize("tier", VALID_TIERS)
def test_valid_image_tier_is_accepted(tmp_path: Path, tier: str) -> None:
    script = tmp_path / "story.md"
    script.write_text(
        "---\nlanguage: en\ncast:\n  narrator: {voice_id: elevenlabs:Brian, language: en}\n---\n\n## Speaker: narrator\nHello.\n",
        encoding="utf-8",
    )
    with (
        patch(
            "video_editor_cli.main.build_storyteller_scene_plan",
            new=AsyncMock(),
        ) as mock_build,
        patch("video_editor_cli.main.write_scene_plan", new=MagicMock()),
    ):
        result = CliRunner().invoke(
            app,
            [
                "run",
                str(script),
                "--template",
                "storyteller_hd",
                "--image-tier",
                tier,
                "--job-id",
                "x",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "out"),
            ],
        )
    assert result.exit_code == 0, result.output
    assert mock_build.call_args.kwargs.get("image_tier") == tier


@pytest.mark.parametrize("explicit, expected", [(None, "auto"), ("poyo_ai", "poyo_ai")])
def test_run_forwards_the_image_provider(
    tmp_path: Path, explicit: str | None, expected: str
) -> None:
    """``ve run`` defaults stills to the chain, but an explicit provider wins."""
    script = tmp_path / "story.md"
    script.write_text(
        "---\nlanguage: en\ncast:\n  narrator: {voice_id: elevenlabs:Brian, language: en}\n---\n\n## Speaker: narrator\nHello.\n",
        encoding="utf-8",
    )
    args = [
        "run",
        str(script),
        "--template",
        "storyteller_hd",
        "--job-id",
        "x",
        "--work-root",
        str(tmp_path / "work"),
        "--output-root",
        str(tmp_path / "out"),
    ]
    if explicit is not None:
        args += ["--image-provider", explicit]
    with (
        patch(
            "video_editor_cli.main.build_storyteller_scene_plan",
            new=AsyncMock(),
        ) as mock_build,
        patch("video_editor_cli.main.write_scene_plan", new=MagicMock()),
    ):
        result = CliRunner().invoke(app, args)

    assert result.exit_code == 0, result.output
    assert mock_build.call_args.kwargs.get("image_provider") == expected


def test_run_forwards_the_image_provider_to_the_estimate_gate(tmp_path: Path) -> None:
    """The approval gate must price the provider the run will actually use."""
    script = tmp_path / "story.md"
    script.write_text(
        "---\nlanguage: en\ncast:\n  narrator: {voice_id: elevenlabs:Brian, language: en}\n---\n\n## Speaker: narrator\nHello.\n",
        encoding="utf-8",
    )
    # A pre-existing plan is what makes ``ve run`` open the estimate gate.
    plan_dir = tmp_path / "work" / "gate" / "plan"
    plan_dir.mkdir(parents=True)
    (plan_dir / "scene_plan.json").write_text("{}", encoding="utf-8")

    with (
        patch("video_editor_cli.commands.estimate.estimate_command") as mock_estimate,
        patch(
            "video_editor_cli.main.build_storyteller_scene_plan",
            new=AsyncMock(),
        ),
        patch("video_editor_cli.main.write_scene_plan", new=MagicMock()),
    ):
        result = CliRunner().invoke(
            app,
            [
                "run",
                str(script),
                "--template",
                "storyteller_hd",
                "--image-provider",
                "poyo_ai",
                "--job-id",
                "gate",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "out"),
            ],
        )

    assert result.exit_code == 0, result.output
    assert mock_estimate.call_args.kwargs.get("image_provider") == "poyo_ai"


def test_image_provider_is_rejected_on_pipelines_that_build_no_images(
    tmp_path: Path,
) -> None:
    """Silently ignoring a pinned provider would spend on the wrong one."""
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"not really an mp4")

    result = CliRunner().invoke(
        app,
        [
            "run",
            str(media),
            "--template",
            "talking_head_polish",
            "--image-provider",
            "poyo_ai",
            "--job-id",
            "x",
            "--work-root",
            str(tmp_path / "work"),
            "--output-root",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code != 0
    assert "--image-provider is only used by storyteller" in result.output


def test_run_without_an_explicit_image_provider_stays_quiet(tmp_path: Path) -> None:
    """The ``auto`` default is a no-op outside the storyteller branch."""
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"not really an mp4")

    result = CliRunner().invoke(
        app,
        [
            "run",
            str(media),
            "--template",
            "talking_head_polish",
            "--job-id",
            "x",
            "--work-root",
            str(tmp_path / "work"),
            "--output-root",
            str(tmp_path / "out"),
        ],
    )

    # It fails later (the fake clip is not real media), but never on the flag.
    assert "--image-provider" not in result.output


def test_invalid_tier_rejected(tmp_path: Path) -> None:
    script = tmp_path / "story.md"
    script.write_text(
        "---\ncast: {narrator: {voice_id: e:B, language: en}}\n---\n## Speaker: narrator\nx\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        app,
        [
            "run",
            str(script),
            "--template",
            "storyteller_hd",
            "--image-tier",
            "garbage",
            "--job-id",
            "x",
        ],
    )
    assert result.exit_code != 0
    assert "Invalid value" in result.output or "invalid choice" in result.output


def test_storyteller_run_without_config_uses_fallback_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ve run <fixture> --template storyteller_hd`` WITHOUT ``--config`` must
    not pass a ``None`` ``llm_router`` to ``build_storyteller_scene_plan``.

    Regression for the lying-``cast(None, ModelRouter)`` bug: when no
    ``--config`` flag is given, the storyteller branch falls back to the
    repo-root ``config.yaml`` so the planner (which declares a non-Optional
    ``ModelRouter``) gets a real router instead of ``None``.
    """
    script = tmp_path / "story.md"
    script.write_text(
        "---\nlanguage: en\ncast:\n  narrator: {voice_id: elevenlabs:Brian, language: en}\n---\n\n## Speaker: narrator\nHello.\n",
        encoding="utf-8",
    )
    # Drop a minimal config.yaml in the CWD so the fallback path resolves
    # without hitting the developer's real repo-root config.yaml. The
    # planner is mocked, so the router is never used for real LLM calls.
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

    with (
        patch(
            "video_editor_cli.main.build_storyteller_scene_plan",
            new=AsyncMock(),
        ) as mock_build,
        patch("video_editor_cli.main.write_scene_plan", new=MagicMock()),
    ):
        result = CliRunner().invoke(
            app,
            [
                "run",
                str(script),
                "--template",
                "storyteller_hd",
                "--job-id",
                "x",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "out"),
            ],
        )
    assert result.exit_code == 0, result.output
    # The positional ``llm_router`` arg (2nd positional) must be a real
    # ModelRouter, NOT None.
    llm_router_arg = mock_build.call_args.args[1]
    assert isinstance(llm_router_arg, ModelRouter), (
        f"expected ModelRouter, got {type(llm_router_arg).__name__}: {llm_router_arg!r}"
    )


def test_storyteller_run_without_config_and_no_fallback_exits_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When neither ``--config`` nor a CWD ``config.yaml`` is available, the
    storyteller branch must exit cleanly with a helpful message rather than
    crashing on ``None.generate()``.
    """
    script = tmp_path / "story.md"
    script.write_text(
        "---\nlanguage: en\ncast:\n  narrator: {voice_id: elevenlabs:Brian, language: en}\n---\n\n## Speaker: narrator\nHello.\n",
        encoding="utf-8",
    )
    # No config.yaml in the CWD, no --config flag.
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "run",
            str(script),
            "--template",
            "storyteller_hd",
            "--job-id",
            "x",
            "--work-root",
            str(tmp_path / "work"),
            "--output-root",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code != 0
    assert "config" in result.output.lower()
