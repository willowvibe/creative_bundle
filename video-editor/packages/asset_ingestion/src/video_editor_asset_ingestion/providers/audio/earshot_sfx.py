"""Earshot SFX provider: render a code-synthesized bank sound.

Earshot ships a bank of 252 sounds stored as recipes, and ``earshot sfx render
<id> -o file.wav`` renders one **without loading the CLAP/AST models** — so a
per-cue subprocess is fast (seconds). The provider runs Earshot in its own
pinned environment (its torch/model dependencies stay out of this venv).

Configuration (``assets.providers.earshot_sfx.provider_options``)::

    command: ["/path/to/earshot/.venv/bin/python", "-m", "earshot"]
    bank_ids:
      hit: cut_hit/punch
      click: ui_pop/step
      card_flip: shutter/quick
      latch: knock/desk
      whoosh: whoosh_fast/whip
    duration_seconds: 1.0   # fallback when the request has none
    apex: 0.32              # optional loudest moment, seconds into the take

The resolver passes the cue's sound name as ``params["cue_sound"]`` and the
desired length as ``params["duration_seconds"]``. A caller can override the
bank id with ``params["earshot_id"]`` (run ``earshot sfx list`` for the ids).
"""

from __future__ import annotations

import asyncio
import shlex
import shutil
from pathlib import Path

import structlog
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.run import CommandError, run_subprocess

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.base import GenerativeProvider

logger = structlog.get_logger(__name__)

#: Cue sound -> Earshot bank id. Override per project in ``bank_ids``.
DEFAULT_BANK_IDS: dict[str, str] = {
    "hit": "cut_hit/punch",
    "click": "ui_pop/step",
    "card_flip": "shutter/quick",
    "latch": "knock/desk",
    "whoosh": "whoosh_fast/whip",
}
DEFAULT_COMMAND = "earshot"


class EarshotSFXProvider(GenerativeProvider):
    """Render one Earshot bank sound for a cue."""

    provider_name = "earshot_sfx"
    consumes_image_quota = False
    generates_images = False

    def __init__(self, config: ProviderConfig | None = None) -> None:
        self.config = config or ProviderConfig()
        options = self.config.provider_options or {}
        raw_command = options.get("command", DEFAULT_COMMAND)
        self.command: list[str] = (
            shlex.split(raw_command) if isinstance(raw_command, str) else list(raw_command)
        )
        self.bank_ids: dict[str, str] = {
            **DEFAULT_BANK_IDS,
            **{str(key): str(value) for key, value in (options.get("bank_ids") or {}).items()},
        }
        raw_duration = options.get("duration_seconds")
        self.duration_seconds = None if raw_duration is None else float(raw_duration)
        apex = options.get("apex")
        self.apex = None if apex is None else float(apex)
        self.timeout = self.config.timeout
        if not self.command:
            raise RuntimeError("Earshot provider_options.command is empty")

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        sound = str(request.params.get("cue_sound") or "")
        bank_id = str(request.params.get("earshot_id") or self.bank_ids.get(sound) or "")
        if not bank_id:
            raise RuntimeError(
                f"Earshot has no bank id for cue {sound or request.prompt!r}; "
                "set provider_options.bank_ids or params['earshot_id']"
            )
        raw_duration = request.params.get("duration_seconds", self.duration_seconds)
        duration: float | None
        if raw_duration is None:
            # Let the bank render its natural length; a cue does not need a
            # whoosh stretched to the generic 2 s generation length.
            duration = None
        else:
            try:
                duration = float(raw_duration)
            except (TypeError, ValueError):
                duration = self.duration_seconds
            if duration is not None:
                duration = min(max(duration, 0.05), 10.0)
        if shutil.which(self.command[0]) is None and not Path(self.command[0]).is_file():
            raise RuntimeError(
                f"Earshot command not found: {self.command[0]}; install Earshot "
                "in its own environment and set provider_options.command"
            )

        command = [*self.command, "sfx", "render", bank_id, "-o", str(output_path)]
        if duration is not None:
            command.extend(["--dur", f"{duration:g}"])
        if self.apex is not None:
            command.extend(["--apex", f"{self.apex:g}"])
        page = _request_page(request)
        if page > 1:
            command.extend(["--seed", str(page)])
        await asyncio.to_thread(self._run, command)
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError(f"Earshot produced no file for {bank_id!r}: {output_path}")
        duration_ms = 0
        try:
            probed = probe_media(output_path).duration
            duration_ms = int(round((probed or 0.0) * 1000))
        except Exception as exc:  # noqa: BLE001 - metadata must not fail the render
            logger.warning("earshot_probe_failed", path=str(output_path), error=str(exc))
        logger.info(
            "earshot_sfx_rendered",
            job_id=request.job_id,
            asset_id=request.asset_id,
            bank_id=bank_id,
        )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.SFX,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=duration_ms,
            metadata={
                "title": bank_id,
                "creator": "earshot",
                "source_url": "https://github.com/Leonxlnx/earshot",
                "license": "generated",
                "bank_id": bank_id,
            },
        )

    def _run(self, command: list[str]) -> None:
        try:
            run_subprocess(command, check=True, timeout=self.timeout)
        except CommandError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            raise RuntimeError(f"Earshot render failed: {detail[-2000:]}") from exc


def _request_page(request: AssetRequest) -> int:
    try:
        page = int(request.params.get("page", 1))
    except (TypeError, ValueError):
        return 1
    return max(1, page)
