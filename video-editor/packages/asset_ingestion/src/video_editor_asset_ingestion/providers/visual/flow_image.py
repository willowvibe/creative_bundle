"""Google Flow stills through flow-mcp: free images (Nano Banana, 0 Flow credits) from the signed-in Flow session.

flow-mcp (``~/flow-mcp``, pinned ``84b548f``) drives the Flow web app in a Chrome window and speaks MCP over stdio.
Its job queue lives in that process, so this provider keeps one flow-mcp process open and runs one image at a time
(one Chrome window). Every scene is sent with ``max_credits: 0``: Flow skips anything it would charge for, so this
provider can never spend Flow credits.

``flow_status`` is checked first. When Flow is not ready (signed out, no project open) the hint is raised as a
``FlowImageError`` so the manager falls through to the next provider in the image chain.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any

import structlog
from PIL import Image

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.base import GenerativeProvider

logger = structlog.get_logger(__name__)

DEFAULT_FLOW_MCP_DIR = "~/flow-mcp"
DEFAULT_TIMEOUT_SECONDS = 600.0
#: Flow's image aspect ratios.
ASPECTS = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0, "4:3": 4 / 3, "3:4": 3 / 4}
#: Flow accepts at most three reference images per scene.
MAX_REFERENCES = 3


class FlowImageError(RuntimeError):
    """Flow could not make the image; ``kind`` is ``setup`` when the session needs the user."""

    def __init__(self, message: str, *, kind: str = "other") -> None:
        super().__init__(message)
        self.kind = kind


def flow_aspect(request: AssetRequest) -> str:
    """The Flow aspect closest to the request's width/height (or its aspect_ratio param), 16:9 by default."""
    explicit = request.params.get("aspect_ratio") or (request.params.get("model_kwargs") or {}).get(
        "aspect_ratio"
    )
    if isinstance(explicit, str) and explicit in ASPECTS:
        return explicit
    width, height = request.params.get("width"), request.params.get("height")
    if (
        isinstance(width, int | float)
        and isinstance(height, int | float)
        and width > 0
        and height > 0
    ):
        ratio = width / height
        return min(ASPECTS, key=lambda name: abs(ASPECTS[name] - ratio))
    return "16:9"


def flow_references(request: AssetRequest) -> list[str]:
    """Local reference images as absolute paths (Flow uploads them itself); URLs are not supported."""
    refs = []
    for ref in request.params.get("reference_images") or []:
        text = str(ref)
        if text.startswith(("http://", "https://")):
            raise FlowImageError(f"flow_image takes local reference images only, got URL {text!r}")
        path = Path(text).expanduser().resolve()
        if not path.is_file():
            raise FlowImageError(f"reference image {text!r} does not exist")
        refs.append(str(path))
    if len(refs) > MAX_REFERENCES:
        raise FlowImageError(
            f"Flow takes at most {MAX_REFERENCES} reference images, got {len(refs)}"
        )
    return refs


class _FlowClient:
    """One flow-mcp process and the JSON-RPC conversation with it."""

    def __init__(self, server: Path) -> None:
        self.server = server
        self.proc: asyncio.subprocess.Process | None = None
        self._next_id = 0

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            "node",
            str(self.server),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=16 * 1024 * 1024,
        )
        await self._request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "video-editor", "version": "1"},
            },
        )
        await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def _send(self, message: dict[str, Any]) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write((json.dumps(message) + "\n").encode())
        await self.proc.stdin.drain()

    async def _request(self, method: str, params: dict[str, Any], timeout: float = 90.0) -> Any:
        assert self.proc and self.proc.stdout
        self._next_id += 1
        ident = self._next_id
        await self._send({"jsonrpc": "2.0", "id": ident, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise FlowImageError(f"flow-mcp did not answer {method} within {timeout:.0f} s")
            line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=left)
            if not line:
                raise FlowImageError("flow-mcp exited")
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("id") != ident:
                continue  # notifications and logs
            if "error" in message:
                raise FlowImageError(f"flow-mcp {method} failed: {message['error']}")
            return message.get("result")

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        """Call one tool and return its JSON reply (flow-mcp answers with JSON text)."""
        result = await self._request("tools/call", {"name": tool, "arguments": arguments})
        text = "".join(part.get("text", "") for part in (result or {}).get("content", []))
        if (result or {}).get("isError"):
            raise FlowImageError(f"{tool}: {text[:500]}")
        try:
            return json.loads(text)
        except ValueError:
            return {"text": text}

    async def close(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=10)
            except TimeoutError:
                self.proc.kill()


class FlowImageProvider(GenerativeProvider):
    """Free stills from Google Flow (Nano Banana) through flow-mcp."""

    provider_name = "flow_image"
    #: One Chrome window drives Flow: one image at a time across every instance in this process.
    _lock = asyncio.Lock()

    def __init__(self, config: ProviderConfig | None = None) -> None:
        self.config = config or ProviderConfig()
        options = self.config.provider_options
        self.server = (
            Path(str(options.get("flow_mcp_dir") or DEFAULT_FLOW_MCP_DIR)).expanduser()
            / "dist"
            / "index.js"
        )
        self.timeout = float(self.config.timeout or DEFAULT_TIMEOUT_SECONDS)
        self.model = str(options.get("model") or "")  # empty: Flow's newest Nano Banana
        #: "upscaled" fetches Flow's free 2K image (slower); "original" is 1376x768 at 16:9.
        self.download_quality = str(options.get("download_quality") or "original")
        self._client: _FlowClient | None = None

    async def _session(self) -> _FlowClient:
        if self._client is None or not self._client.alive:
            if not self.server.exists():
                raise FlowImageError(f"flow-mcp is not built at {self.server}", kind="setup")
            if shutil.which("node") is None:
                raise FlowImageError("node is not installed (flow-mcp needs it)", kind="setup")
            self._client = _FlowClient(self.server)
            await self._client.start()
        return self._client

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        if request.asset_type is not AssetType.IMAGE:
            raise FlowImageError(f"flow_image handles images only, got {request.asset_type}")
        output_path = Path(output_path)
        references = flow_references(request)
        scene: dict[str, Any] = {
            "prompt": request.prompt,
            "type": "image",
            "aspect_ratio": flow_aspect(request),
            "max_credits": 0,  # never spend Flow credits
        }
        if self.model:
            scene["model"] = self.model
        if self.download_quality == "upscaled":
            scene["download_quality"] = "upscaled"
        if references:
            scene["reference_images"] = references
        started = time.monotonic()
        async with self._lock:
            client = await self._session()
            status = await client.call("flow_status", {"detailed": False})
            hint = (status.get("flow") or {}).get("hint")
            if hint:
                raise FlowImageError(f"Flow is not ready: {hint}", kind="setup")
            queued = await client.call(
                "flow_generate", {"project": f"ve-{request.job_id}", "scenes": [scene]}
            )
            job_ids = [job["id"] for job in queued.get("jobs", [])] or [
                str(queued.get("task_id", ""))
            ]
            job = await self._wait(client, [i for i in job_ids if i])
        files = [
            Path(f)
            for f in job.get("files") or []
            if Path(f).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
        ]
        if job.get("status") != "done" or not files:
            reason = job.get("error") or job.get("note") or job.get("status")
            raise FlowImageError(f"Flow made no image: {reason}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(files[0]) as image:
            width, height = image.size
            image.convert("RGB").save(output_path, format="PNG")
        logger.info(
            "flow_image_complete",
            job_id=request.job_id,
            asset_id=request.asset_id,
            path=str(output_path),
        )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.IMAGE,
            scene_id=request.scene_id,
            local_path=str(output_path),
            metadata={
                "provider": self.provider_name,
                "model": self.model or "nano-banana-flow",
                "width": width,
                "height": height,
                "flow_credits": job.get("credits") or 0,
                "cost": 0.0,
                "latency_ms": int((time.monotonic() - started) * 1000),
            },
        )

    async def _wait(self, client: _FlowClient, job_ids: list[str]) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        while True:
            reply = await client.call("flow_wait", {"job_ids": job_ids, "timeout_seconds": 40})
            jobs = reply.get("jobs") or []
            if reply.get("finished") and jobs:
                return dict(jobs[0])
            if time.monotonic() > deadline:
                raise FlowImageError(f"Flow job {job_ids} still running after {self.timeout:.0f} s")

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
