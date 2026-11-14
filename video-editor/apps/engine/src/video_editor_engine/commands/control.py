"""JSON-lines control channel over TCP for the engine command registry.

FilmCraft-style: a client opens a TCP connection and sends one JSON request
object per line; the server replies with one JSON response object per line.
The same :func:`_handle_request` helper is what the socket loop calls, so the
protocol can be unit-tested without touching sockets.

Request shapes::

    {"method": "engine.ping"}
    {"method": "engine.commands"}
    {"method": "engine.execute", "params": {"command": "<id>", "params": {...}}}

Response shapes::

    {"ok": true, "result": ...}
    {"ok": false, "error": "..."}
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import structlog
from video_editor_state.trust import TrustLevel

from video_editor_engine.commands.registry import CommandError, default_registry

logger = structlog.get_logger(__name__)

__all__ = ["handle_request", "serve_control"]


async def _handle_request(payload: object) -> dict[str, Any]:
    """Dispatch one control request and return the response dict.

    ``payload`` may be a decoded ``dict`` or a raw line (``str``/``bytes``); a
    malformed line yields ``{"ok": false, "error": "invalid JSON"}`` rather than
    raising, so the socket loop can keep serving the connection.
    """
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError:
            return {"ok": False, "error": "request is not valid UTF-8"}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return {"ok": False, "error": "invalid JSON"}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "request must be a JSON object"}

    method = payload.get("method")

    if method == "engine.ping":
        return {"ok": True, "result": "pong"}

    if method == "engine.commands":
        commands = [command.to_dict() for command in default_registry.list_commands()]
        return {"ok": True, "result": commands}

    if method == "engine.execute":
        raw_params = payload.get("params")
        if raw_params is None:
            raw_params = {}
        if not isinstance(raw_params, dict):
            return {"ok": False, "error": "params must be a JSON object"}

        command_id = raw_params.get("command")
        if not isinstance(command_id, str) or not command_id:
            return {"ok": False, "error": "params.command must be a non-empty string"}

        command_params = raw_params.get("params")
        if command_params is not None and not isinstance(command_params, dict):
            return {"ok": False, "error": "params.params must be a JSON object"}

        try:
            result = await default_registry.execute(
                command_id, command_params, trust=TrustLevel.CONTROL
            )
        except CommandError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "result": result}

    return {"ok": False, "error": f"unknown method: {method!r}"}


#: Public alias for the request dispatcher (the socket loop calls this too).
handle_request = _handle_request


def _encode_response(response: dict[str, Any]) -> str:
    """Serialize a response dict to a single JSON line.

    A handler result that is not JSON-serializable must not kill the server, so
    fall back to an error response.
    """
    try:
        return json.dumps(response)
    except (TypeError, ValueError) as exc:
        logger.error("control_response_not_serializable", error=str(exc))
        return json.dumps({"ok": False, "error": f"result is not JSON-serializable: {exc}"})


async def serve_control(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Serve the control protocol until cancelled.

    Handles one or more sequential/concurrent connections; a malformed line or
    a handler failure produces an error response and the connection stays open.
    """

    async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        logger.info("control_client_connected", peer=str(peer))
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                response = await _handle_request(line)
                writer.write((_encode_response(response) + "\n").encode("utf-8"))
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError) as exc:
            logger.warning("control_client_disconnected", error=str(exc))
        except Exception as exc:  # noqa: BLE001 — one bad client must not kill the server
            logger.error("control_client_error", error=str(exc))
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    server = await asyncio.start_server(_handle_client, host=host, port=port)
    addresses = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    logger.info("control_server_listening", host=host, port=port, addresses=addresses)
    async with server:
        await server.serve_forever()
