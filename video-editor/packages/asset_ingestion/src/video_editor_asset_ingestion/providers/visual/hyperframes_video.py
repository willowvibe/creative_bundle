"""HyperFrames provider for prompt-driven motion video generation.

The provider builds a minimal self-contained HTML composition from a text
prompt and renders it to MP4 via the HyperFrames CLI. A stub provider is
included for offline CI/local development.
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from pathlib import Path

import structlog
from video_editor_core import hyperframes_cli
from video_editor_ffmpeg.probe import probe_media

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.base import GenerativeProvider

logger = structlog.get_logger(__name__)

DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_FPS = 30
DEFAULT_TIMEOUT = 600

_HYPERFRAMES_PROJECT_JSON = {
    "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
    "registry": "https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry",
    "paths": {
        "blocks": "compositions",
        "components": "compositions/components",
        "assets": "assets",
    },
}


def _hsv_to_hex(h: float, s: float, v: float) -> str:
    """Convert HSV values to a 6-digit hex color string."""
    import colorsys

    h = h / 360.0
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def _build_html(prompt: str, duration_seconds: float, width: int, height: int) -> str:
    """Build a minimal prompt-driven HTML composition for HyperFrames.

    The composition is intentionally simple: an animated gradient atmosphere
    with the prompt as the central text treatment. Future iterations can
    swap in richer templates driven by the prompt.
    """
    colors = {
        "ink": "#0f172a",
        "cream": "#f8fafc",
        "coral": "#e85d4e",
        "sky": "#8bb4f7",
        "lavender": "#c5b5e0",
    }
    return textwrap.dedent(
        f"""
        <!doctype html>
        <html lang="en">
          <head>
            <meta charset="UTF-8" />
            <meta name="viewport" content="width={width}, height={height}" />
            <style>
              * {{ margin: 0; padding: 0; box-sizing: border-box; }}
              html, body {{
                width: {width}px;
                height: {height}px;
                overflow: hidden;
                background: {colors["ink"]};
                font-family: sans-serif;
              }}
              .stage {{
                position: absolute;
                inset: 0;
                display: flex;
                align-items: center;
                justify-content: center;
                background:
                  radial-gradient(circle at 20% 30%, {colors["coral"]}40 0%, transparent 35%),
                  radial-gradient(circle at 80% 70%, {colors["sky"]}35 0%, transparent 35%),
                  radial-gradient(circle at 60% 20%, {colors["lavender"]}30 0%, transparent 25%);
                animation: breathe {max(2.0, duration_seconds)}s ease-in-out infinite alternate;
              }}
              .prompt {{
                width: {int(width * 0.8)}px;
                color: {colors["cream"]};
                font-size: {min(72, max(36, int(width / 30)))}px;
                font-weight: 700;
                text-align: center;
                line-height: 1.25;
                text-shadow: 0 8px 40px rgba(0,0,0,0.45);
                padding: 60px;
              }}
              @keyframes breathe {{
                from {{ transform: scale(1.0); }}
                to {{ transform: scale(1.06); }}
              }}
            </style>
          </head>
          <body>
            <div class="stage">
              <div class="prompt">{prompt}</div>
            </div>
          </body>
        </html>
        """
    ).strip()


class HyperFramesVideoProvider(GenerativeProvider):
    """Generate short motion video clips via the HyperFrames CLI.

    A prompt-driven HTML composition is rendered to MP4. Provider-specific
    options (width, height, fps, cli_path, timeout) are read from
    ``ProviderConfig.provider_options``.
    """

    provider_name = "hyperframes_video"

    def __init__(self, config: ProviderConfig | None = None):
        self.config = config or ProviderConfig()
        self.width = int(self.config.provider_options.get("width", DEFAULT_WIDTH))
        self.height = int(self.config.provider_options.get("height", DEFAULT_HEIGHT))
        self.fps = int(self.config.provider_options.get("fps", DEFAULT_FPS))
        self.cli_path = str(self.config.provider_options.get("cli_path", "hyperframes"))
        self.timeout = int(self.config.provider_options.get("timeout", DEFAULT_TIMEOUT))

    def _prepare_project(self, request: AssetRequest, project_dir: Path) -> None:
        """Write the HyperFrames project files needed for rendering."""
        project_dir.mkdir(parents=True, exist_ok=True)
        duration_seconds = float(
            request.params.get("duration_seconds") or request.params.get("duration") or 5.0
        )

        (project_dir / "hyperframes.json").write_text(
            json.dumps(_HYPERFRAMES_PROJECT_JSON, indent=2),
            encoding="utf-8",
        )
        (project_dir / "index.html").write_text(
            _build_html(
                prompt=request.prompt or "motion scene",
                duration_seconds=duration_seconds,
                width=self.width,
                height=self.height,
            ),
            encoding="utf-8",
        )

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Render a prompt-driven HyperFrames clip to ``output_path``."""
        if request.asset_type != AssetType.VIDEO:
            raise ValueError(
                f"HyperFrames video provider received {request.asset_type.value!r}; expected video"
            )

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        project_dir = output_path.parent / f"hf_project_{request.asset_id}"
        self._prepare_project(request, project_dir)

        logger.info(
            "hyperframes_video_render_start",
            job_id=request.job_id,
            asset_id=request.asset_id,
            project_dir=str(project_dir),
            output=str(output_path),
        )

        # hyperframes_cli.render_segment is synchronous; run it in a thread so
        # the async provider does not block the event loop.
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            hyperframes_cli.render_segment,
            project_dir,
            output_path,
            self.cli_path,
            self.timeout,
            {"fps": self.fps},
        )

        info = probe_media(output_path)
        duration_ms = info.duration_ms()
        logger.info(
            "hyperframes_video_render_complete",
            job_id=request.job_id,
            asset_id=request.asset_id,
            path=str(output_path),
            duration_ms=duration_ms,
        )

        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.VIDEO,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=duration_ms,
            metadata={
                "provider": self.provider_name,
                "width": self.width,
                "height": self.height,
                "fps": self.fps,
            },
        )


class StubHyperFramesVideoProvider(HyperFramesVideoProvider):
    """Offline stub that writes a generated test video with FFmpeg.

    Mirrors the real provider's interface without invoking the HyperFrames CLI,
    so CI and local dev can exercise the motion path without installing
    HyperFrames.
    """

    provider_name = "stub_hyperframes_video"

    def __init__(self, config: ProviderConfig | None = None):
        # The stub ignores config but accepts it for API compatibility.
        super().__init__(config)

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Render a short colored test video with FFmpeg."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        duration = float(
            request.params.get("duration_seconds") or request.params.get("duration") or 5.0
        )
        width = self.width
        height = self.height
        fps = self.fps
        # Use a hue derived from the scene id so tests can visually distinguish outputs.
        hue = abs(hash(request.scene_id)) % 360
        color = _hsv_to_hex(hue, 0.7, 0.9)
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={width}x{height}:d={duration}:r={fps}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={440 + (hue % 200)}:sample_rate=44100",
            "-t",
            str(duration),
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Stub HyperFrames ffmpeg failed: {stderr.decode()}")

        info = probe_media(output_path)
        duration_ms = info.duration_ms()
        logger.info(
            "stub_hyperframes_video_complete",
            job_id=request.job_id,
            asset_id=request.asset_id,
            path=str(output_path),
            duration_ms=duration_ms,
        )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.VIDEO,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=duration_ms,
            metadata={
                "provider": self.provider_name,
                "width": width,
                "height": height,
                "duration_seconds": duration,
            },
        )
