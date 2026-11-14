"""Music skill orchestrator."""

from __future__ import annotations

import json
from pathlib import Path

import structlog
from graphics.models import Brand
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_state.job import JobContext

from music.mix import mix_background_music
from music.models import MusicManifest, MusicStyle

logger = structlog.get_logger(__name__)

DEFAULT_MUSIC_SEARCH_PATHS = [
    "assets/music/default.mp3",
    "assets/music/default.wav",
    "assets/music/default.m4a",
]


class MusicSkill:
    """Mix background music with speech ducking onto the latest video."""

    @property
    def name(self) -> str:
        return "music"

    def __init__(
        self,
        ctx: JobContext,
        music_path: Path | str | None = None,
        style: MusicStyle | None = None,
        brand: Brand | None = None,
        render_options: RenderOptions | None = None,
    ):
        self.ctx = ctx
        self.render_options = render_options or RenderOptions()
        # Priority: explicit style > brand-derived style > hard-coded default.
        # ``music_path`` is then applied on top of the chosen style so
        # callers can pass it positionally (the historical contract).
        if style is not None:
            self.style = style
        elif brand is not None:
            self.style = MusicStyle.from_brand(
                brand, music_path=str(music_path) if music_path else ""
            )
        else:
            self.style = MusicStyle()
        if music_path is not None and style is None:
            # When the caller didn't supply an explicit style, the
            # music_path argument still overrides the style's path
            # (preserves the historical ``MusicSkill(ctx, music_path=...)``
            # contract). When the caller did supply an explicit style,
            # they're responsible for setting music_path on the style.
            self.style.music_path = str(music_path)
        self.manifest_path = ctx.work_dir / "music" / "music.json"

    def _find_input_video(self) -> Path:
        """Locate the most recent video to mix music onto."""
        # Bug #73: prefer a broll-mixed video so all skills agree on the
        # final candidate. Order is newest skill stage → oldest fallback.
        candidates = [
            self.ctx.work_dir / "broll" / "with_broll.mp4",
            self.ctx.work_dir / "captions" / "captions.json",
            self.ctx.work_dir / "graphics" / "with_graphics.mp4",
            self.ctx.work_dir / "rough_cut" / "locked.mp4",
        ]
        for candidate in candidates:
            if candidate.suffix == ".json" and candidate.exists():
                data = json.loads(candidate.read_text(encoding="utf-8"))
                video = data.get("output_video") or data.get("video")
                if video:
                    video_path = Path(video)
                    if video_path.exists():
                        return video_path
            elif candidate.exists():
                return candidate
        raise FileNotFoundError(
            f"No input video found for job {self.ctx.job_id}. Run graphics or captions first."
        )

    def _find_music(self) -> Path:
        """Resolve the background music file path."""
        if self.style.music_path:
            path = Path(self.style.music_path)
            if path.exists():
                return path
            raise FileNotFoundError(f"Specified music file not found: {self.style.music_path}")

        for relative in DEFAULT_MUSIC_SEARCH_PATHS:
            path = Path(relative)
            if path.exists():
                return path
        raise FileNotFoundError("No music file specified and no default found in assets/music/.")

    def run(
        self,
        output_name: str = "with_music.mp4",
        input_video: Path | str | None = None,
    ) -> Path:
        """Mix background music and save the result.

        When ``input_video`` is provided, use it as the base video
        instead of auto-discovering the most recent captions/graphics
        output. This is the hook the v2 skills pipeline uses to
        route an SFX-mixed video into the music stage.
        """
        self.ctx.ensure_dirs()

        if input_video is not None:
            base = Path(input_video)
            if not base.exists():
                raise FileNotFoundError(f"MusicSkill input_video not found: {base}")
            resolved_input = base
        else:
            resolved_input = self._find_input_video()
        music_path = self._find_music()

        output_dir = self.ctx.work_dir / "music"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / output_name

        mix_background_music(
            resolved_input,
            music_path,
            output,
            music_gain_db=self.style.music_gain_db,
            speech_gain_db=self.style.speech_gain_db,
            duck_threshold=self.style.duck_threshold,
            duck_ratio=self.style.duck_ratio,
            attack_ms=self.style.attack_ms,
            release_ms=self.style.release_ms,
            knee_db=self.style.knee_db,
            makeup=self.style.makeup,
            loop=self.style.loop,
            fade_in=self.style.fade_in,
            fade_out=self.style.fade_out,
            render_options=self.render_options,
        )

        info = probe_media(output)
        if info.duration is None:
            raise RuntimeError(f"Could not determine duration for {output}")
        self.ctx.manifest().stage_done("music", {"video": str(output)})
        self.ctx.store.save()
        self._save_manifest(
            input_video=str(resolved_input),
            music_path=str(music_path),
            output=str(output),
            duration=info.duration,
        )
        logger.info("music_complete", job_id=self.ctx.job_id, output=str(output))
        return output

    def _save_manifest(
        self,
        input_video: str,
        music_path: str,
        output: str,
        duration: float,
    ) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = MusicManifest(
            job_id=self.ctx.job_id,
            style=self.style,
            input_video=input_video,
            music_path=music_path,
            output_video=output,
            duration=duration,
        )
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest.model_dump(), f, indent=2)
