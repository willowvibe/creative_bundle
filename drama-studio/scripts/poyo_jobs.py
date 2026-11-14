"""PoYo jobs that are never paid for twice: every task id is written to a ledger the moment PoYo accepts it.

PoYo bills at submit and cannot list past tasks, so an episode script that dies between submit and download used to
lose the render (two Ep1 scripts did). Use this from episode scripts instead of calling the API directly:

    from scripts.poyo_jobs import Ledger
    jobs = Ledger(EPISODE / "v2/clips/_poyo_runs.json")
    jobs.submit("v07", "veo3.1-lite-official", {"prompt": ..., "image_urls": [...]})   # skips if v07 is known
    jobs.collect()                                      # polls every submitted task until it finishes or fails
    jobs.download("v07", EPISODE / "clips/motion/v07.mp4")

A key that is already submitted or finished is not submitted again, even after a crash; a failed one can be resubmitted.
The engine's PoYo providers do the same through video_editor_asset_ingestion.providers.poyo_tasks.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BASE = "https://api.poyo.ai"
REPO = Path(__file__).resolve().parents[1]


def api_key() -> str:
    key = os.environ.get("POYO_API_KEY", "")
    if not key and (REPO / ".env").exists():
        for line in (REPO / ".env").read_text().splitlines():
            if line.startswith("POYO_API_KEY="):
                key = line.split("=", 1)[1]
    key = key.strip().strip("'\"")
    if not key:
        raise RuntimeError("POYO_API_KEY is not set (environment or drama-studio/.env)")
    return key


def _http(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read())
    if payload.get("code") not in (None, 200):
        raise RuntimeError(f"PoYo {path}: {payload}")
    return payload.get("data", payload)


class Ledger:
    """A JSON ledger of PoYo tasks keyed by the episode's own ids (shot ids, line ids)."""

    def __init__(self, path: Path, http: Any = _http) -> None:
        self.path = path
        self.http = http
        self.rows: dict[str, dict] = json.loads(path.read_text()) if path.exists() else {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.rows, indent=2))
        tmp.replace(self.path)

    def submit(self, key: str, model: str, inputs: dict, *, retries: int = 6) -> str:
        """Submit ``key`` unless it is already submitted or finished; return its task id."""
        row = self.rows.get(key)
        if row and row.get("status") in ("submitted", "finished"):
            return str(row["task_id"])
        for attempt in range(retries):
            try:
                task_id = str(
                    self.http("POST", "/api/generate/submit", {"model": model, "input": inputs})[
                        "task_id"
                    ]
                )
                break
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == retries - 1:
                    raise
                time.sleep(10 * (attempt + 1))
        self.rows[key] = {
            "model": model,
            "task_id": task_id,
            "status": "submitted",
            "submitted_at": datetime.now(UTC).isoformat(),
        }
        self._save()  # before anything else can fail: this is the paid part
        return task_id

    def collect(self, *, timeout: float = 2400, interval: float = 10) -> list[str]:
        """Poll every submitted task until it finishes or fails; return the keys still running at timeout."""
        deadline = time.monotonic() + timeout
        while True:
            open_ = [key for key, row in self.rows.items() if row.get("status") == "submitted"]
            for key in open_:
                status = self.http("GET", f"/api/generate/status/{self.rows[key]['task_id']}")
                state = status.get("status")
                if state == "finished":
                    files = status.get("files") or []
                    self.rows[key].update(
                        status="finished",
                        urls=[f.get("file_url") or f.get("audio_url") for f in files],
                        credits=status.get("credits") or status.get("cost"),
                    )
                    self._save()
                elif state == "failed":
                    self.rows[key].update(
                        status="failed", error=status.get("error_message") or status.get("error")
                    )
                    self._save()
            still = [key for key, row in self.rows.items() if row.get("status") == "submitted"]
            if not still or time.monotonic() > deadline:
                return still
            time.sleep(interval)

    def download(self, key: str, dest: Path, index: int = 0) -> Path:
        """Save a finished task's file (``index`` for tasks with several outputs)."""
        row = self.rows.get(key) or {}
        if row.get("status") != "finished":
            raise RuntimeError(f"{key} is {row.get('status', 'unknown')}, not finished")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(row["urls"][index], timeout=300) as response:
            dest.write_bytes(response.read())
        row["downloaded"] = str(dest)
        self._save()
        return dest
