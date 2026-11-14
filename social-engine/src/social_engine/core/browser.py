"""Python bridge to the Node browser driver.

The driver is stateless: Chrome holds the session and the JSON payload holds one
command. ``browser_call`` writes that payload to a temp file, runs
``node browser/driver.mjs <platform> <command> --payload <file>``, and maps the
single JSON result to a :class:`BrowserResult` or a typed error.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .accounts import load_account
from .errors import BrowserNeedsUser, BrowserUnexpectedScreen, SocialError
from .paths import chrome_profile_dir

DEFAULT_TIMEOUT = 120.0
RESULT_STATUSES = frozenset({"done", "ready_to_confirm", "needs_user", "error"})


@dataclass
class BrowserResult:
    """The one JSON object the driver prints."""

    status: str
    summary: dict[str, Any] = field(default_factory=dict)
    id: str | None = None
    screenshot: str | None = None
    message: str = ""


def driver_path() -> Path:
    """The browser driver in this checkout, overridable for tests and packaging."""
    override = os.environ.get("SOCIAL_ENGINE_DRIVER")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[3] / "browser" / "driver.mjs"


def _tail(text: str | None, lines: int = 8) -> str:
    parts = [line for line in (text or "").strip().splitlines() if line]
    return "\n".join(parts[-lines:]) if parts else "(no output)"


def _payload_with_account(platform: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload)
    account_name = data.get("account")
    if account_name:
        account = load_account(platform, str(account_name))
        data.setdefault("cdp_port", account.cdp_port)
        data.setdefault(
            "chrome_profile_dir",
            str(chrome_profile_dir(platform, account.name)),
        )
    return data


def _parse_result(completed: subprocess.CompletedProcess[str]) -> BrowserResult:
    if completed.returncode != 0:
        detail = _tail(completed.stderr or completed.stdout)
        raise SocialError(
            f"browser driver exited {completed.returncode}: {detail}",
            code="browser_driver_failed",
            next_step="Read the driver output, fix the page or payload, then retry.",
        )
    text = (completed.stdout or "").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        detail = _tail(completed.stderr or text)
        raise SocialError(
            f"browser driver printed invalid JSON: {detail}",
            code="browser_bad_output",
            next_step="Run the driver by hand and inspect its stdout and stderr.",
        ) from exc
    if not isinstance(data, dict):
        raise SocialError(
            "browser driver printed JSON that is not an object",
            code="browser_bad_output",
            next_step="Run the driver by hand and inspect its stdout.",
        )

    status = str(data.get("status") or "error")
    if status not in RESULT_STATUSES:
        raise SocialError(
            f"browser driver printed an unknown status: {status!r}",
            code="browser_bad_output",
            next_step="Run the driver by hand and inspect its stdout.",
        )
    summary = data.get("summary")
    screenshot = data.get("screenshot")
    video_id = data.get("id")
    result = BrowserResult(
        status=status,
        summary=summary if isinstance(summary, dict) else {},
        id=str(video_id) if video_id is not None else None,
        screenshot=str(screenshot) if screenshot is not None else None,
        message=str(data.get("message") or ""),
    )
    if status == "needs_user":
        raise BrowserNeedsUser(
            result.message or "the browser needs the owner",
            screenshot=result.screenshot,
        )
    if status == "error":
        raise BrowserUnexpectedScreen(
            result.message or "unexpected browser screen",
            screenshot=result.screenshot,
        )
    return result


def browser_call(
    platform: str,
    command: str,
    payload: dict[str, Any],
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> BrowserResult:
    """Run one browser-driver command and return its result.

    Every payload that names an account also gets that account's ``cdp_port`` and
    ``chrome_profile_dir``.
    """
    data = _payload_with_account(platform, payload)
    descriptor, temp_name = tempfile.mkstemp(
        prefix="social-engine-browser-",
        suffix=".json",
    )
    payload_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        try:
            completed = subprocess.run(
                [
                    "node",
                    str(driver_path()),
                    platform,
                    command,
                    "--payload",
                    str(payload_path),
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SocialError(
                "node was not found; the browser lane needs Node.js 22 or newer",
                code="browser_driver_missing",
                next_step="Install Node.js 22+ and run `npm install` in this repo.",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise SocialError(
                f"browser driver timed out after {timeout} seconds",
                code="browser_timeout",
                next_step=(
                    "Check the open Chrome window, finish or close the stuck screen, "
                    "then retry."
                ),
            ) from exc
        return _parse_result(completed)
    finally:
        payload_path.unlink(missing_ok=True)
