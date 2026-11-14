"""CLI command group."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import structlog
import typer
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.scene_assets import (
    alignment_request,
    music_request,
    sfx_requests_for_scene,
)
from video_editor_config import Settings
from video_editor_core.models.scene_plan import ScenePlan
from video_editor_ffmpeg.broll import mix_sfx_into_video
from video_editor_pipelines.handlers import _safe_filename

from video_editor_cli import main as cli_main

logger = structlog.get_logger(__name__)


def register_assets(app: typer.Typer) -> None:
    """Register the assets commands."""

    @app.command()
    def music_generate(
        job_id: str = cli_main._JOB_ID_ARG,
        prompt: str = typer.Option(
            ..., "--prompt", "-p", help="Music generation prompt (natural language)"
        ),
        provider: str = cli_main._MUSIC_PROVIDER_OPT,
        duration_seconds: int = cli_main._MUSIC_DURATION_OPT,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
        config_path: Path | None = cli_main._CONFIG_OPT,
    ) -> None:
        """Generate one AI music track for a job and save it to ``<work>/music/``.

        The generated track is named ``generated_music.mp3`` so
        ``MusicSkill``'s auto-discovery picks it up as a fallback when
        no explicit ``--music-path`` is set. Cost: ~$0.10 per 30s clip
        at the ElevenLabs default music model.
        """

        ctx = cli_main._context(job_id, work_root, output_root)
        config = Settings.load(config_path).config.assets
        manager = AssetIngestionManager(ctx, config=config)
        request = music_request(
            job_id=job_id,
            prompt=prompt,
            duration_seconds=duration_seconds,
            provider=provider,
        )
        results = asyncio.run(manager.resolve([request]))
        music_dir = ctx.work_dir / "music"
        music_dir.mkdir(parents=True, exist_ok=True)
        final_path = music_dir / "generated_music.mp3"
        # The manager writes to its cache; copy to the canonical path
        # so MusicSkill can find it.
        shutil.copyfile(Path(results[0].local_path), final_path)
        typer.echo(f"Generated music: {final_path} ({results[0].duration_ms}ms)")

    @app.command()
    def sfx_generate(
        job_id: str = cli_main._JOB_ID_ARG,
        provider: str = cli_main._SFX_PROVIDER_OPT,
        duration_seconds: float = cli_main._SFX_DURATION_OPT,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
        config_path: Path | None = cli_main._CONFIG_OPT,
        plan_name: str = cli_main._ASSETS_PLAN_NAME_OPT,
    ) -> None:
        """Generate one AI SFX clip per scene in the scene plan.

        Saves each clip to ``<work>/sfx/<scene_id>.mp3`` and writes a
        JSON mapping ``<work>/sfx/sfx_map.json`` that ``ve sfx-mix``
        reads to schedule the clips on the timeline. Cost: ~$0.02
        per 5s clip at the ElevenLabs default SFX model.
        """
        import json

        ctx = cli_main._context(job_id, work_root, output_root)
        plan_path = ctx.work_dir / "plan" / plan_name
        if not plan_path.exists():
            typer.echo(f"Scene plan not found: {plan_path}", err=True)
            raise typer.Exit(1)
        plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))

        config = Settings.load(config_path).config.assets
        manager = AssetIngestionManager(ctx, config=config)
        requests = [
            req
            for scene in plan.scenes
            for req in sfx_requests_for_scene(
                scene,
                job_id,
                sfx_provider=provider,
                duration_seconds=duration_seconds,
            )
        ]
        if not requests:
            typer.echo("No scenes with visual_description found; nothing to generate.")
            return

        results = asyncio.run(manager.resolve(requests))
        sfx_dir = ctx.work_dir / "sfx"
        sfx_dir.mkdir(parents=True, exist_ok=True)
        sfx_map: dict[str, str] = {}
        for result in results:
            out_path = sfx_dir / f"{result.scene_id}.mp3"
            shutil.copyfile(Path(result.local_path), out_path)
            sfx_map[result.scene_id] = str(out_path)
        sfx_map_path = sfx_dir / "sfx_map.json"
        sfx_map_path.write_text(json.dumps(sfx_map, indent=2), encoding="utf-8")
        typer.echo(f"Generated {len(results)} SFX clip(s) -> {sfx_dir} (map: {sfx_map_path})")

    @app.command()
    def sfx_mix(
        job_id: str = cli_main._JOB_ID_ARG,
        sfx_gain_db: float = cli_main._SFX_GAIN_DB_OPT,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
        plan_name: str = cli_main._ASSETS_PLAN_NAME_OPT,
        base_video: str = typer.Option(
            "with_broll.mp4", "--base", help="Base video filename inside <work>/broll/"
        ),
    ) -> None:
        """Mix per-scene SFX clips into the audio track of a base video.

        Reads ``<work>/sfx/sfx_map.json`` and ``<work>/plan/<plan_name>``
        for SFX paths and scene start times. Writes
        ``<work>/broll/with_broll_sfx.mp4``. Backward-compatible: if
        ``sfx_map.json`` is missing or empty, the base video is copied
        through unchanged.
        """

        ctx = cli_main._context(job_id, work_root, output_root)
        plan_path = ctx.work_dir / "plan" / plan_name
        sfx_map_path = ctx.work_dir / "sfx" / "sfx_map.json"
        base_video_path = ctx.work_dir / "broll" / base_video

        if not plan_path.exists():
            typer.echo(f"Scene plan not found: {plan_path}", err=True)
            raise typer.Exit(1)
        if not base_video_path.exists():
            typer.echo(f"Base video not found: {base_video_path}", err=True)
            raise typer.Exit(1)
        if not sfx_map_path.exists():
            # Backward-compat: no SFX map -> copy through.
            output_path = ctx.work_dir / "broll" / "with_broll_sfx.mp4"
            shutil.copyfile(base_video_path, output_path)
            typer.echo(f"No sfx_map.json; copied base -> {output_path}")
            return

        plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        sfx_map: dict[str, str] = json.loads(sfx_map_path.read_text(encoding="utf-8"))

        # Build the (path, start_time) timeline by walking the scene plan
        # in order. Each scene's start_time is the cumulative duration of
        # all preceding scenes; if the plan has explicit start offsets we
        # honor those instead.
        timeline: list[tuple[Path, float]] = []
        cursor = 0.0
        for scene in plan.scenes:
            sfx_path_str = sfx_map.get(scene.id)
            if not sfx_path_str:
                cursor += scene.duration_seconds
                continue
            sfx_path = Path(sfx_path_str)
            if not sfx_path.exists():
                cursor += scene.duration_seconds
                continue
            timeline.append((sfx_path, cursor))
            cursor += scene.duration_seconds

        output_path = ctx.work_dir / "broll" / "with_broll_sfx.mp4"
        mix_sfx_into_video(
            base_video=base_video_path,
            sfx_timeline=timeline,
            output_path=output_path,
            sfx_gain_db=sfx_gain_db,
        )
        typer.echo(f"Mixed {len(timeline)} SFX clip(s) -> {output_path} (gain: {sfx_gain_db} dB)")

    @app.command()
    def align(
        job_id: str = cli_main._JOB_ID_ARG,
        audio: str = cli_main._ALIGN_AUDIO_OPT,
        text: str = cli_main._ALIGN_TEXT_OPT,
        provider: str = cli_main._ALIGN_PROVIDER_OPT,
        output_name: str = cli_main._ALIGN_OUTPUT_OPT,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
        config_path: Path | None = cli_main._CONFIG_OPT,
    ) -> None:
        """Align a known transcript text to an audio file via forced alignment.

        Writes the alignment JSON (ElevenLabs' ``{words, characters,
        transcript, language}`` shape) to ``<work>/transcript/<output_name>``.
        Useful for captioning audio that doesn't have a pre-computed
        transcript (stock music, archival audio, foreign clips). The
        CaptionsSkill consumes the same shape that
        ``forced_alignment_response_to_transcript()`` produces.

        Cost: free at the ElevenLabs default.
        """

        audio_path = Path(audio)
        if not audio_path.exists():
            typer.echo(f"Audio file not found: {audio_path}", err=True)
            raise typer.Exit(1)

        ctx = cli_main._context(job_id, work_root, output_root)
        config = Settings.load(config_path).config.assets
        manager = AssetIngestionManager(ctx, config=config)
        request = alignment_request(
            job_id=job_id,
            audio_path=str(audio_path),
            text=text,
            provider=provider,
        )
        results = asyncio.run(manager.resolve([request]))
        transcript_dir = ctx.work_dir / "transcript"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        final_path = transcript_dir / output_name
        shutil.copyfile(Path(results[0].local_path), final_path)
        typer.echo(
            f"Aligned {results[0].metadata.get('word_count', '?')} words "
            f"-> {final_path} (provider: {provider})"
        )

    @app.command()
    def audio_isolate(
        job_id: str = cli_main._JOB_ID_ARG,
        audio: Path = cli_main._ISOLATE_AUDIO_OPT,
        provider: str = cli_main._ISOLATE_PROVIDER_OPT,
        output_name: str = cli_main._ISOLATE_OUTPUT_OPT,
        file_format: str = cli_main._ISOLATE_FILE_FORMAT_OPT,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
        config_path: Path | None = cli_main._CONFIG_OPT,
    ) -> None:
        """Isolate voice/audio from background noise via ElevenLabs.

        Writes the isolated audio track to ``<work>/audio/<output_name>``.
        The source can be any audio or video file; the output is always an
        audio file (MP3 by default). Use ``--isolate-provider stub_isolation``
        for offline tests.

        Cost: free at the ElevenLabs default.
        """

        # Bug #181: Typer's ``Path`` type does not expand ``~`` (only the shell
        # does, and quoting defeats that), so expand it here for parity with
        # other path-taking commands.
        audio_path = audio.expanduser()
        if not audio_path.exists():
            typer.echo(f"Audio file not found: {audio_path}", err=True)
            raise typer.Exit(1)

        ctx = cli_main._context(job_id, work_root, output_root)
        config = Settings.load(config_path).config.assets
        manager = AssetIngestionManager(ctx, config=config)
        safe_output_name = _safe_filename(output_name, "output_name")
        output_ext = Path(safe_output_name).suffix.lstrip(".") or "mp3"
        request = cli_main.isolation_request(
            job_id=job_id,
            audio_path=str(audio_path),
            provider=provider,
            file_format=file_format,
            output_ext=output_ext,
        )
        results = asyncio.run(manager.resolve([request]))
        output_dir = ctx.work_dir / "audio"
        output_dir.mkdir(parents=True, exist_ok=True)
        final_path = output_dir / safe_output_name
        shutil.copyfile(Path(results[0].local_path), final_path)
        typer.echo(
            f"Isolated audio -> {final_path} (provider: {provider}, "
            f"duration_ms: {results[0].duration_ms})"
        )

    @app.command()
    def speech_to_speech(
        job_id: str = cli_main._JOB_ID_ARG,
        audio: Path = cli_main._STS_AUDIO_OPT,
        voice_id: str = cli_main._STS_VOICE_ID_OPT,
        provider: str = cli_main._STS_PROVIDER_OPT,
        model_id: str = cli_main._STS_MODEL_ID_OPT,
        output_name: str = cli_main._STS_OUTPUT_OPT,
        remove_background_noise: bool = cli_main._STS_REMOVE_BG_OPT,
        output_format: str = cli_main._STS_OUTPUT_FORMAT_OPT,
        output_ext: str = cli_main._STS_OUTPUT_EXT_OPT,
        seed: int = cli_main._STS_SEED_OPT,
        stability: float = cli_main._STS_STABILITY_OPT,
        similarity_boost: float = cli_main._STS_SIMILARITY_OPT,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
        config_path: Path | None = cli_main._CONFIG_OPT,
    ) -> None:
        """Convert speech in an audio file to a target voice via ElevenLabs.

        Writes the converted audio track to ``<work>/audio/<output_name>``.
        The source can be any audio or video file; the output is an audio
        file whose extension is taken from ``--output-name`` (or overridden
        by ``--output-ext``). Use ``--sts-provider stub_speech_to_speech`` for
        offline tests.
        """

        # Bug #181: expand ``~`` so a quoted ``--audio '~/music/x.mp3'`` resolves
        # the same way an unquoted shell-expanded path would.
        audio_path = audio.expanduser()
        if not audio_path.exists():
            typer.echo(f"Audio file not found: {audio_path}", err=True)
            raise typer.Exit(1)

        ctx = cli_main._context(job_id, work_root, output_root)
        config = Settings.load(config_path).config.assets
        manager = AssetIngestionManager(ctx, config=config)
        safe_output_name = _safe_filename(output_name, "output_name")
        effective_output_ext = output_ext or Path(safe_output_name).suffix.lstrip(".") or "mp3"
        if effective_output_ext != Path(safe_output_name).suffix.lstrip("."):
            safe_output_name = f"{Path(safe_output_name).stem}.{effective_output_ext}"

        provider_config = config.providers.get(provider) or ProviderConfig()
        effective_voice_id = voice_id or provider_config.default_voice
        if not effective_voice_id:
            typer.echo(
                "--voice-id is required (or set default_voice for the provider in config.yaml)",
                err=True,
            )
            raise typer.Exit(1)
        effective_model_id = model_id or provider_config.default_model or "eleven_english_sts_v2"

        voice_settings: dict[str, Any] = {}
        if stability:
            voice_settings["stability"] = stability
        if similarity_boost:
            voice_settings["similarity_boost"] = similarity_boost

        extra_params: dict[str, Any] = {}
        if seed:
            extra_params["seed"] = seed
        if voice_settings:
            extra_params["voice_settings"] = voice_settings

        request = cli_main.speech_to_speech_request(
            job_id=job_id,
            audio_path=str(audio_path),
            voice_id=effective_voice_id,
            provider=provider,
            model_id=effective_model_id,
            output_format=output_format or "mp3_44100_128",
            remove_background_noise=remove_background_noise,
            output_ext=effective_output_ext,
            **extra_params,
        )
        results = asyncio.run(manager.resolve([request]))
        output_dir = ctx.work_dir / "audio"
        output_dir.mkdir(parents=True, exist_ok=True)
        final_path = output_dir / safe_output_name
        shutil.copyfile(Path(results[0].local_path), final_path)
        typer.echo(
            f"Converted speech -> {final_path} (provider: {provider}, "
            f"voice_id: {effective_voice_id}, duration_ms: {results[0].duration_ms})"
        )

    @app.command()
    def audio(
        source: Path = cli_main._RUN_SOURCE_ARG,
        job_id: str = cli_main._JOB_ID_ARG,
        voiceover: Path = cli_main._VOICEOVER_OPT,
        voiceover_gain_db: float = cli_main._VOICEOVER_GAIN_OPT,
        output_name: str = cli_main._AUDIO_OUTPUT_NAME_OPT,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
    ) -> None:
        """Mux an external voiceover file onto a source video's audio track.

        Replaces the source video's audio with ``--voiceover`` (e.g. a TTS
        narration file) and writes ``<work>/intake/<output_name>``. The result
        is a normal video whose audio IS the speech, ready to feed into
        ``ve run`` so the music stage ducks against the voiceover instead of
        against silent source audio (Issue A).

        Example:
            ve audio ./raw.mp4 -j saju -v ./voiceover.mp3 --voiceover-gain-db 2.0
            ve run ./projects/saju/intake/with_voiceover.mp4 -j saju --music-prompt "..."
        """
        from video_editor_ffmpeg.voiceover import mux_voiceover

        if not source.exists():
            typer.echo(f"Source video not found: {source}", err=True)
            raise typer.Exit(1)
        if not voiceover.exists():
            typer.echo(f"Voiceover file not found: {voiceover}", err=True)
            raise typer.Exit(1)

        ctx = cli_main._context(job_id, work_root, output_root)
        output_dir = ctx.work_dir / "intake"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / output_name
        mux_voiceover(
            source_video=source,
            voiceover_path=voiceover,
            output_path=output_path,
            voiceover_gain_db=voiceover_gain_db,
        )
        typer.echo(
            f"Muxed voiceover -> {output_path} (gain: {voiceover_gain_db} dB). "
            f"Feed this into `ve run` to continue the pipeline."
        )

    @app.command(name="tts-test")
    def tts_test(
        text: str = cli_main._TTS_TEST_TEXT_OPT,
        voice: str = cli_main._TTS_TEST_VOICE_OPT,
        language: str = cli_main._TTS_TEST_LANGUAGE_OPT,
        job_id: str = cli_main._TTS_TEST_JOB_ID_OPT,
    ) -> None:
        """Audition a single TTS voice; writes audio to projects/<job_id>/assets/tts/audition.mp3."""

        out = Path(f"projects/{job_id}/assets/tts/audition.mp3")
        out.parent.mkdir(parents=True, exist_ok=True)
        request = AssetRequest(
            asset_id=f"{job_id}_audition",
            job_id=job_id,
            scene_id=job_id,
            asset_type=AssetType.TTS,
            prompt=text,
            provider="tts_router",
            params={"voice_id": voice, "language": language},
        )
        asyncio.run(cli_main._tts_test_synthesize(request, out))
        typer.echo(f"Audition written to {out}")
