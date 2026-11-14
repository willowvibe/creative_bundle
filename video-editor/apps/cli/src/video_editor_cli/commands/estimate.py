"""ve estimate: tally asset costs and prompt for approval before generation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
from video_editor_asset_ingestion.pricing import (
    PricingRegistry,
    estimate_image_cost,
    estimate_music_cost,
    estimate_tts_cost,
    estimate_video_cost,
)
from video_editor_asset_ingestion.scene_assets import requests_from_plan
from video_editor_config import ProjectConfig, Settings
from video_editor_core.models.scene_plan import ScenePlan
from video_editor_pipelines.pricing import (
    _format_table,
    _resolve_model,
    auto_image_pricing_provider,
)
from video_editor_state.job import JobContext

from video_editor_cli.paths import default_output_root, default_work_root

# Fallback ElevenLabs voice IDs used when no brand profile is configured.
_DEFAULT_VOICE_OPTIONS: dict[str, dict[str, str]] = {
    "professional": {
        "id": "ATYMJR0SnaN1mSrsnZEi",
        "label": "professional (ATYMJR0SnaN1mSrsnZEi)",
    },
    "instant": {
        "id": "32EwRSV41WufKgzh80Ri",
        "label": "instant (32EwRSV41WufKgzh80Ri)",
    },
}


def _load_voice_options(config_path: Path | None = None) -> dict[str, dict[str, str]]:
    """Load voice options from the project brand profile, with built-in fallback."""
    try:
        config = ProjectConfig.load(config_path)
        voices = getattr(config.brand, "voices", None)
        if voices:
            return {name: {"id": info.id, "label": info.label} for name, info in voices.items()}
    except Exception:  # noqa: BLE001, S110 - the ledger may be disabled; fall back to the static price
        # If config cannot be loaded, fall back to defaults so estimate stays usable.
        pass
    return _DEFAULT_VOICE_OPTIONS


# Providers that genuinely cost nothing. Anything else reporting $0.00 means
# the registry has no price for the model, which must not pass the gate
# silently (#262).
FREE_PROVIDERS = frozenset(
    {
        "pexels",
        "pixabay",
        "coverr",
        "mixkit",
        "local_media",
        "matplotlib_data_viz",
        "hyperframes_video",
    }
)


def _count_chars(scenes: list[Any]) -> int:
    """Count total narration characters across all scenes."""
    return sum(len(getattr(s, "narration", "") or "") for s in scenes)


def _tally_costs(
    plan: ScenePlan,
    registry: PricingRegistry,
    tts_provider: str,
    image_provider: str,
    video_provider: str,
    music_provider: str,
) -> list[dict[str, Any]]:
    """Build a list of cost line items from a scene plan."""
    items: list[dict[str, Any]] = []
    seen: dict[tuple[str, str, str, str], int] = {}
    params_by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    tts_chars_by_key: dict[tuple[str, str, str, str], list[int]] = {}

    for req in requests_from_plan(
        plan,
        plan.job_id,
        tts_provider=tts_provider,
        image_provider=image_provider,
        video_provider=video_provider,
    ):
        provider = req.provider or "unknown"
        asset_type = (
            req.asset_type.value if hasattr(req.asset_type, "value") else str(req.asset_type)
        )
        requested = req.params.get("model")
        model = _resolve_model(
            provider,
            asset_type,
            explicit=requested.strip()
            if isinstance(requested, str) and requested.strip()
            else None,
        )
        # Price tiers (e.g. an image resolution) live in the request's
        # params, so two scenes that differ only by tier are two lines
        # priced at two rates (#269). TTS is the exception: Sarvam bills
        # per call and the pipeline makes one call for the whole script,
        # so per-scene params must not split it into a line per scene
        # (#263) — that would price the narration eight times over.
        pricing_params = {} if asset_type == "tts" else dict(req.params)
        key = (
            provider,
            model,
            asset_type,
            json.dumps(pricing_params, sort_keys=True, default=str),
        )
        seen[key] = seen.get(key, 0) + 1
        params_by_key[key] = pricing_params
        if asset_type == "tts":
            tts_chars_by_key.setdefault(key, []).append(len(req.prompt))

    # Total duration is computed once and reused for video/music estimates.
    total_duration = sum(s.duration_seconds for s in plan.scenes)

    for (provider, model, asset_type, params_key), count in seen.items():
        req_params = params_by_key[(provider, model, asset_type, params_key)]
        if asset_type == "tts":
            total_chars = _count_chars(plan.scenes)
            total_cost = estimate_tts_cost(
                provider,
                model,
                total_chars,
                registry,
                char_counts=tts_chars_by_key.get(key),
            )
            unit_cost = total_cost / max(count, 1)
        elif asset_type == "image" or asset_type == "data_viz":
            unit_cost = estimate_image_cost(provider, model, 1, registry, params=req_params)
            total_cost = estimate_image_cost(provider, model, count, registry, params=req_params)
        elif asset_type == "video":
            unit_cost = estimate_video_cost(provider, model, 1.0, registry, params=req_params)
            total_cost = estimate_video_cost(
                provider, model, total_duration, registry, params=req_params, count=count
            )
        else:
            unit_cost = 0.0
            total_cost = 0.0

        items.append(
            {
                "provider": provider,
                "model": model,
                "task_type": asset_type,
                "count": count,
                "unit_cost": round(unit_cost, 6),
                "total_cost": round(total_cost, 4),
                "currency": "USD",
            }
        )

    # Add music cost (one track for the full duration) only when the plan
    # asks for one. The CLI flag defaults to a provider name, so pricing it
    # unconditionally approved a track a voice-only job would never buy
    # (#266). A plan that supplies its own bed (`music_path`) buys nothing.
    if music_provider and plan.music_mood and not plan.music_path:
        music_model = _resolve_model(music_provider, "music")
        music_cost = estimate_music_cost(music_provider, music_model, total_duration, registry)
        items.append(
            {
                "provider": music_provider,
                "model": music_model,
                "task_type": "music",
                "count": 1,
                "unit_cost": round(music_cost, 6),
                "total_cost": round(music_cost, 4),
                "currency": "USD",
            }
        )

    return items


def estimate_command(
    job_id: str,
    work_root: str | None = None,
    output_root: str | None = None,
    config_path: Path | None = None,  # reserved for future use (provider config overrides)
    auto_approve: bool = False,
    tts_provider: str = "elevenlabs_tts",
    image_provider: str = "poyo_ai",
    video_provider: str = "poyo_ai",
    music_provider: str = "poyo_music",
    voice: str = "instant",
    final: bool = False,
) -> None:
    """Tally estimated costs for a scene plan and prompt for approval."""
    ctx = JobContext(
        job_id=job_id,
        work_root=work_root or default_work_root(),
        output_root=output_root or default_output_root(),
    )

    plan_path = ctx.work_dir / "plan" / "scene_plan.json"
    if not plan_path.exists():
        typer.echo(f"Scene plan not found: {plan_path}", err=True)
        raise typer.Exit(1)

    plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    if not plan.scenes:
        typer.echo("No scenes to estimate. Run ve plan-script first.", err=True)
        raise typer.Exit(1)

    # Load pricing and check freshness
    registry = PricingRegistry.load()
    registry.check_freshness()

    # ``auto`` is the CLI default; it names no price row of its own, so resolve
    # it to the chain member that is expected to be billed before tallying.
    if image_provider == "auto":
        image_provider = auto_image_pricing_provider(
            Settings.load(config_path).config.assets.image_chain_for(final), registry
        )

    # Resolve voice from brand profile (falls back to built-in IDs)
    voice_options = _load_voice_options(config_path)
    voice_info = voice_options.get(voice, voice_options["instant"])

    # Non-ElevenLabs TTS providers carry their own voice ids. Sarvam, for
    # example, is addressed by a model-specific speaker (``shubh_en_narration``
    # for v4 Flash) that lives in the plan's per-scene ``props.voice_id``.
    # Prefer the plan's casting so the estimate records the voice that will
    # actually be synthesised.
    if tts_provider != "elevenlabs_tts":
        planned_voice = ""
        for scene in plan.scenes:
            candidate = scene.props.get("voice_id")
            if isinstance(candidate, str) and candidate.strip():
                planned_voice = candidate.strip()
                break
        if planned_voice:
            voice_info = {"id": planned_voice, "label": f"{tts_provider} ({planned_voice})"}

    # Tally costs
    items = _tally_costs(
        plan,
        registry,
        tts_provider=tts_provider,
        image_provider=image_provider,
        video_provider=video_provider,
        music_provider=music_provider,
    )
    total_cost = round(sum(item["total_cost"] for item in items), 4)
    total_duration = sum(s.duration_seconds for s in plan.scenes)

    # Display
    typer.echo(
        f"\nEstimated costs for {job_id} ({len(plan.scenes)} scenes, ~{total_duration:.0f}s):"
    )
    typer.echo(_format_table(items, total_cost))
    unpriced = [
        item
        for item in items
        if item["total_cost"] == 0.0 and item["provider"] not in FREE_PROVIDERS
    ]
    for item in unpriced:
        typer.echo(
            f"Warning: no price for {item['provider']}/{item['model']} "
            f"({item['count']} x {item['task_type']}); the total below is too low. "
            "Add it to models.yaml or set a different --*-provider.",
            err=True,
        )
    typer.echo(f"\nVoice: {voice_info['label']}")

    if not auto_approve:
        typer.echo(f"\nTotal: ${total_cost:.4f}")
        answer = typer.prompt("Proceed?", default="n", show_default=False)
        if answer.lower() not in ("y", "yes"):
            typer.echo("Aborted. Edit the scene plan or re-run with different providers.")
            raise typer.Exit(0)

    # Write cost estimate
    estimate_data = {
        "job_id": job_id,
        "approved_at": datetime.now(UTC).isoformat(),
        "voice_id": voice_info["id"],
        "voice_name": voice,
        "line_items": items,
        "total_cost": total_cost,
        "currency": "USD",
    }
    estimate_path = ctx.work_dir / "plan" / "cost_estimate.json"
    estimate_path.parent.mkdir(parents=True, exist_ok=True)
    estimate_path.write_text(json.dumps(estimate_data, indent=2), encoding="utf-8")

    # Mark manifest stage
    manifest = ctx.manifest()
    manifest.stage_done("estimate", {"cost_estimate": str(estimate_path), "total_cost": total_cost})
    ctx.store.save()

    typer.echo(f"Cost estimate approved and saved to {estimate_path}")


def register_estimate(app: typer.Typer) -> None:
    """Register the estimate command on the Typer app."""

    @app.command(name="estimate")
    def estimate(
        job_id: str = typer.Argument(..., help="Job identifier"),
        work_root: str | None = typer.Option(
            None,
            "--work-root",
            help="Work directory root (default: VE_WORK_ROOT, else /work or ./projects)",
        ),
        output_root: str | None = typer.Option(
            None,
            "--output-root",
            help="Output directory root (default: VE_OUTPUT_ROOT, else /output or ./outputs)",
        ),
        config_path: Path | None = typer.Option(
            None, "--config", "-c", help="Path to project config.yaml"
        ),
        auto_approve: bool = typer.Option(False, "--yes", "-y", help="Skip the approval prompt"),
        tts_provider: str = typer.Option(
            "elevenlabs_tts", "--tts-provider", help="TTS provider for estimation"
        ),
        image_provider: str = typer.Option(
            "poyo_ai", "--image-provider", help="Image provider for estimation"
        ),
        video_provider: str = typer.Option(
            "poyo_ai", "--video-provider", help="Video provider for estimation"
        ),
        music_provider: str = typer.Option(
            "poyo_music", "--music-provider", help="Music provider for estimation"
        ),
        voice: str = typer.Option(
            "instant", "--voice", help="ElevenLabs voice: instant or professional"
        ),
        final: bool = typer.Option(
            False, "--final", help="Price the final image chain instead of drafts"
        ),
    ) -> None:
        """Tally estimated costs for a scene plan and prompt for approval."""
        estimate_command(
            job_id=job_id,
            work_root=work_root,
            output_root=output_root,
            config_path=config_path,
            auto_approve=auto_approve,
            tts_provider=tts_provider,
            image_provider=image_provider,
            video_provider=video_provider,
            music_provider=music_provider,
            voice=voice,
            final=final,
        )
