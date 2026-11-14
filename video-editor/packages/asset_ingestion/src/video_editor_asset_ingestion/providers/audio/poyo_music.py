"""PoYo.ai music-generation provider.

Uses PoYo's unified async API:
  POST /api/generate/submit  -> data.task_id
  GET  /api/generate/status/{task_id} -> data.status + data.files[0].file_url

Default model: ``generate-music`` (Suno-backed, ~$0.10/gen). It renders a bed
in well under a minute, which is what a video build needs.
Fallback model: ``minimax-music-2.6`` (~$0.10/gen, same price, but observed
taking several minutes per track).

The two backends do not share an input schema *or* a result schema: Suno wants
``mv`` plus ``custom_mode`` and reports ``audio_url``; MiniMax rejects both
fields ("unsupported fields: custom_mode, mv"), wants ``is_instrumental`` and
reports ``file_url``. ``_build_input`` and ``_poll`` shape each side.

PoYo's model id for MiniMax uses a dot. The hyphen spelling this provider
originally shipped (``minimax-music-2-6``) is not a PoYo model at all.

``PoyoMusicProvider`` implements the ``MusicProvider`` protocol
(``generate_music -> MusicResult``) for callers that speak that interface, and
also exposes ``GenerativeProvider``'s ``generate(request, path) -> AssetResult``
entry point so ``AssetIngestionManager`` can resolve a ``poyo_music`` request
(the explainer pipeline's default). The two surfaces delegate to the same
submit/poll/download code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import httpx
import structlog
from video_editor_ffmpeg.probe import probe_media

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.audio.music_protocol import MusicProvider, MusicResult
from video_editor_asset_ingestion.providers.poyo_tasks import clear_task, pending_task, record_task

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.poyo.ai"
DEFAULT_MODEL = "generate-music"
DEFAULT_FALLBACK_MODEL = "minimax-music-2.6"
DEFAULT_DURATION_SECONDS = 30.0
POLL_INTERVAL_SECONDS = 5.0
#: Long enough for MiniMax, which has been observed taking several minutes for
#: one bed. Suno answers in well under a minute.
POLL_TIMEOUT_SECONDS = 900.0

#: PoYo fronts several music backends under one endpoint. Suno-backed models
#: are addressed with a version tag; MiniMax-backed ones reject that field.
SUNO_MODELS = frozenset({"generate-music"})
DEFAULT_SUNO_MV = "V4_5PLUS"


class PoyoMusicProvider(MusicProvider):
    """Generate music via PoYo's submit/poll API."""

    provider_name = "poyo_music"

    def __init__(
        self,
        config: ProviderConfig | None = None,
        *,
        music_model: str | None = None,
        fallback_music_model: str | None = None,
    ) -> None:
        self.config = config or ProviderConfig()
        self.base_url = (self.config.base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = self.config.api_key() or ""
        self.timeout = self.config.timeout
        # ``default_model`` is the ``ProviderConfig`` field a YAML file can
        # actually set; ``provider_options["music_model"]`` is the older knob.
        # Reading only the latter silently dropped a project's configured model.
        self.music_model = (
            music_model
            or self.config.provider_options.get("music_model")
            or self.config.default_model
            or DEFAULT_MODEL
        )
        self.fallback_music_model = (
            fallback_music_model
            or self.config.provider_options.get("fallback_music_model")
            or DEFAULT_FALLBACK_MODEL
        )
        self.poll_interval = float(
            self.config.provider_options.get("poll_interval_seconds", POLL_INTERVAL_SECONDS)
        )
        self.poll_timeout = float(
            self.config.provider_options.get("poll_timeout_seconds", POLL_TIMEOUT_SECONDS)
        )
        self.suno_mv = str(self.config.provider_options.get("suno_mv") or DEFAULT_SUNO_MV)
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            self._client = httpx.AsyncClient(
                base_url=self.base_url, headers=headers, timeout=self.timeout
            )
        return self._client

    def _build_input(self, model: str, prompt: str) -> dict[str, Any]:
        """Shape the request body for one music backend.

        PoYo validates ``input`` per model, so one shared body cannot work:
        Suno-backed models require an ``mv`` version tag (and otherwise ask for
        a ``title``/``style``/``lyrics`` pair), while MiniMax-backed models
        reject ``custom_mode`` and ``mv`` as unsupported fields and require
        either ``is_instrumental`` or ``lyrics``. These are background beds,
        so both branches ask for an instrumental.
        """
        if model in SUNO_MODELS:
            return {
                "prompt": prompt,
                "custom_mode": False,
                "mv": self.suno_mv,
                "instrumental": True,
            }
        return {"prompt": prompt, "is_instrumental": True}

    async def _submit(self, client: httpx.AsyncClient, model: str, prompt: str) -> str | None:
        """Submit one generation; ``None`` means this model could not serve it.

        A retracted model id comes back as a 4xx, not as a missing ``task_id``,
        so ``raise_for_status`` used to abort the whole call and the fallback
        branch below never ran. Client errors now log and fall through to the
        fallback model; server errors still raise, because retrying a different
        model would not fix them.
        """
        payload = {"model": model, "input": self._build_input(model, prompt)}
        resp = await client.post("/api/generate/submit", json=payload)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                raise
            logger.warning(
                "poyo_music_submit_rejected",
                model=model,
                status=exc.response.status_code,
                body=exc.response.text[:300],
            )
            return None
        data = (resp.json() or {}).get("data") or {}
        return data.get("task_id")

    async def _poll(self, client: httpx.AsyncClient, task_id: str) -> str | None:
        """Poll until status is finished/failed; return the audio URL on success."""
        import asyncio

        deadline = asyncio.get_event_loop().time() + self.poll_timeout
        while True:
            resp = await client.get(f"/api/generate/status/{task_id}")
            resp.raise_for_status()
            payload = (resp.json() or {}).get("data") or {}
            status = payload.get("status")
            if status == "finished":
                files = payload.get("files") or []
                if not files:
                    raise RuntimeError(f"PoYo returned finished task {task_id!r} with no files")
                # Music tasks report ``audio_url``; other PoYo generators use
                # ``file_url``. Accept both rather than losing a finished track.
                first = files[0]
                return cast("str | None", first.get("audio_url") or first.get("file_url"))
            if status == "failed":
                raise RuntimeError(f"PoYo task {task_id!r} failed: {payload.get('error')}")
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError(
                    f"PoYo task {task_id!r} did not finish within {self.poll_timeout}s"
                )
            await asyncio.sleep(self.poll_interval)

    async def _download(self, url: str, output_path: Path) -> None:
        async with httpx.AsyncClient(timeout=self.timeout) as client:  # noqa: SIM117
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with open(output_path, "wb") as f:
                    async for chunk in resp.aiter_bytes():
                        f.write(chunk)

    async def generate_music(
        self,
        prompt: str,
        duration_seconds: float,
        output_path: Path,
    ) -> MusicResult:
        client = await self._get_client()
        output_path = Path(output_path)
        journal_key = {"prompt": prompt, "models": [self.music_model, self.fallback_music_model]}
        resumed = pending_task(output_path, journal_key)
        task_id = resumed or await self._submit(client, self.music_model, prompt)
        if resumed is not None:
            model_used = self.music_model  # the journal does not say which; the bill is the same
        elif task_id is None:
            logger.warning(
                "poyo_music_model_not_found_falling_back",
                primary=self.music_model,
                fallback=self.fallback_music_model,
            )
            task_id = await self._submit(client, self.fallback_music_model, prompt)
            if task_id is None:
                raise RuntimeError(
                    f"PoYo returned no task_id for primary {self.music_model!r} or fallback {self.fallback_music_model!r}"
                )
            model_used = self.fallback_music_model
        else:
            model_used = self.music_model
        if task_id is None:  # unreachable: both submit branches raise or return an id
            raise RuntimeError("PoYo returned no task_id")
        if resumed is None:
            record_task(output_path, journal_key, task_id, model=model_used)
        try:
            file_url = await self._poll(client, task_id)
        except RuntimeError:
            clear_task(output_path)  # PoYo reported failure (a TimeoutError keeps the journal)
            raise
        if not file_url:
            raise RuntimeError(f"PoYo task {task_id!r} finished with no file_url")
        await self._download(file_url, output_path)
        clear_task(output_path)
        return MusicResult(
            provider=self.provider_name,
            model=model_used,
            path=Path(output_path),
            duration_seconds=self._track_duration(Path(output_path), duration_seconds),
        )

    @staticmethod
    def _track_duration(output_path: Path, requested_seconds: float) -> float:
        """The length of the delivered track, not the length requested.

        PoYo bills per generation and returns a whole composition: a request
        for 30 s came back as 224 s. Reporting the requested number would make
        every downstream loop/trim decision wrong about the file on disk.
        """
        try:
            return probe_media(output_path).duration_ms() / 1000.0
        except Exception as exc:  # noqa: BLE001 - a probe failure is not a build failure
            logger.warning(
                "poyo_music_duration_unprobed",
                path=str(output_path),
                fallback_seconds=requested_seconds,
                error=str(exc),
            )
            return float(requested_seconds)

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """``GenerativeProvider`` entry point used by ``AssetIngestionManager``.

        The manager calls ``generate(request, path)`` on every provider. PoYo
        speaks the ``MusicProvider`` surface, so adapt its ``MusicResult`` to
        the ``AssetResult`` the manager (and the plan attach step) expects,
        carrying the provider/model that actually served the track.
        """
        duration = float(request.params.get("duration_seconds", DEFAULT_DURATION_SECONDS))
        music = await self.generate_music(request.prompt, duration, output_path)
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.MUSIC,
            scene_id=request.scene_id,
            local_path=str(music.path),
            duration_ms=int(round(music.duration_seconds * 1000)),
            metadata={
                "provider": music.provider,
                "model": music.model,
                "duration_seconds": music.duration_seconds,
                "bytes": Path(music.path).stat().st_size,
            },
        )
