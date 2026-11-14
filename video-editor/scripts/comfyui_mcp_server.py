#!/usr/bin/env python3
"""Local stdio MCP server that wraps the ComfyUI API.

Expects the ComfyUI API to be reachable at COMFYUI_URL (default http://localhost:18188).
Use an SSH tunnel to connect to a remote ComfyUI instance:

    ssh -N -L 18188:localhost:18188 -p 30142 root@23.158.136.85

Then add this server to Claude Code via .mcp.json:

    {
      "mcpServers": {
        "comfyui": {
          "command": "/mnt/data2/git_repos/creative_repos/video-editor/.venv/bin/python",
          "args": ["/mnt/data2/git_repos/creative_repos/video-editor/scripts/comfyui_mcp_server.py"],
          "env": {
            "COMFYUI_URL": "http://localhost:18188"
          }
        }
      }
    }
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import anyio
import requests
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool


def _getenv(key: str, default: str) -> str:
    value = os.environ.get(key, default)
    if not value:
        raise RuntimeError(f"Environment variable {key} is not set")
    return value


COMFYUI_URL = _getenv("COMFYUI_URL", "http://localhost:18188").rstrip("/")


class ComfyUIClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.session = requests.Session()

    def _get(self, path: str) -> dict[str, Any]:
        resp = self.session.get(f"{self.base_url}{path}", timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, json_data: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = self.session.post(f"{self.base_url}{path}", json=json_data, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def system_stats(self) -> dict[str, Any]:
        return self._get("/system_stats")

    def queue(self) -> dict[str, Any]:
        return self._get("/queue")

    def history(self, prompt_id: str | None = None) -> dict[str, Any]:
        path = f"/history/{prompt_id}" if prompt_id else "/history"
        return self._get(path)

    def prompt(self, workflow: dict[str, Any]) -> dict[str, Any]:
        payload = {"prompt": workflow}
        return self._post("/prompt", payload)


client = ComfyUIClient(COMFYUI_URL)
app = Server("comfyui")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="comfyui_system_stats",
            description="Get ComfyUI system stats including GPU/VRAM info.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="comfyui_queue_status",
            description="Get current prompt queue status (running and pending).",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="comfyui_prompt_status",
            description="Get the status and outputs of a queued prompt by ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt_id": {
                        "type": "string",
                        "description": "The prompt_id returned by comfyui_queue_prompt",
                    },
                },
                "required": ["prompt_id"],
            },
        ),
        Tool(
            name="comfyui_queue_prompt",
            description="Queue a ComfyUI workflow (prompt). Provide the workflow as a JSON object.",
            inputSchema={
                "type": "object",
                "properties": {
                    "workflow": {
                        "type": "object",
                        "description": "ComfyUI workflow JSON object (node_id -> {class_type, inputs})",
                    },
                    "wait": {
                        "type": "boolean",
                        "description": "If true, block until the prompt completes and return outputs.",
                        "default": False,
                    },
                    "timeout_s": {
                        "type": "number",
                        "description": "Max seconds to wait when wait=true. Default 600.",
                        "default": 600,
                    },
                },
                "required": ["workflow"],
            },
        ),
        Tool(
            name="comfyui_generate_flux_image",
            description="Generate an image using Flux with a text prompt. Returns the output filename and prompt_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Text prompt for the image"},
                    "negative_prompt": {"type": "string", "default": ""},
                    "width": {"type": "integer", "default": 1280},
                    "height": {"type": "integer", "default": 720},
                    "steps": {"type": "integer", "default": 20},
                    "cfg": {"type": "number", "default": 1.0},
                    "seed": {"type": "integer", "default": -1},
                    "filename_prefix": {"type": "string", "default": "flux_cc"},
                    "checkpoint": {"type": "string", "default": "flux1-dev-fp8.safetensors"},
                    "vae": {"type": "string", "default": "ae.safetensors"},
                },
                "required": ["prompt"],
            },
        ),
        Tool(
            name="comfyui_generate_ltxv_video",
            description="Generate a short video using LTX-Video text-to-video. Returns the output filename and prompt_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Text prompt for the video"},
                    "negative_prompt": {
                        "type": "string",
                        "default": "blurry, low quality, distorted",
                    },
                    "width": {"type": "integer", "default": 768},
                    "height": {"type": "integer", "default": 512},
                    "length": {"type": "integer", "default": 97},
                    "steps": {"type": "integer", "default": 25},
                    "cfg": {"type": "number", "default": 3.0},
                    "seed": {"type": "integer", "default": -1},
                    "fps": {"type": "number", "default": 25},
                    "filename_prefix": {"type": "string", "default": "ltxv_cc"},
                    "checkpoint": {
                        "type": "string",
                        "default": "LTXV/ltxv-2b-0.9.8-distilled-fp8.safetensors",
                    },
                    "clip": {"type": "string", "default": "t5xxl_fp8_e4m3fn.safetensors"},
                },
                "required": ["prompt"],
            },
        ),
    ]


def _flux_workflow(
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    seed: int,
    filename_prefix: str,
    checkpoint: str,
    vae: str,
) -> dict[str, Any]:
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": checkpoint},
        },
        "2": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": vae},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["1", 1]},
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative_prompt, "clip": ["1", 1]},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["1", 0],
                "positive": ["3", 0],
                "negative": ["4", 0],
                "latent_image": ["5", 0],
            },
        },
        "7": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["6", 0], "vae": ["2", 0]},
        },
        "8": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": filename_prefix, "images": ["7", 0]},
        },
    }


def _ltxv_workflow(
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    length: int,
    steps: int,
    cfg: float,
    seed: int,
    fps: float,
    filename_prefix: str,
    checkpoint: str,
    clip: str,
) -> dict[str, Any]:
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": checkpoint},
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": clip, "type": "ltxv"},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["2", 0]},
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative_prompt, "clip": ["2", 0]},
        },
        "5": {
            "class_type": "LTXVConditioning",
            "inputs": {"positive": ["3", 0], "negative": ["4", 0], "frame_rate": fps},
        },
        "6": {
            "class_type": "EmptyLTXVLatentVideo",
            "inputs": {"width": width, "height": height, "length": length, "batch_size": 1},
        },
        "7": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler_ancestral",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["1", 0],
                "positive": ["5", 0],
                "negative": ["5", 1],
                "latent_image": ["6", 0],
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["7", 0], "vae": ["1", 2]},
        },
        "9": {
            "class_type": "SaveAnimatedWEBP",
            "inputs": {
                "images": ["8", 0],
                "filename_prefix": filename_prefix,
                "fps": fps,
                "lossless": False,
                "quality": 80,
                "method": "default",
            },
        },
    }


def _wait_for_prompt(prompt_id: str, timeout_s: float) -> dict[str, Any]:
    import time

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            hist = client.history(prompt_id)
        except Exception as exc:  # noqa: BLE001 - keep the helper server alive
            return {"status": "error", "prompt_id": prompt_id, "message": str(exc)}
        entry = hist.get(prompt_id)
        if entry and entry.get("outputs"):
            return {"status": "completed", "prompt_id": prompt_id, "outputs": entry["outputs"]}
        time.sleep(2)
    return {
        "status": "timeout",
        "prompt_id": prompt_id,
        "message": f"Prompt did not complete within {timeout_s}s",
    }


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any] | None = None) -> list[TextContent]:
    arguments = arguments or {}
    try:
        if name == "comfyui_system_stats":
            result = client.system_stats()
        elif name == "comfyui_queue_status":
            result = client.queue()
        elif name == "comfyui_prompt_status":
            result = client.history(arguments["prompt_id"])
        elif name == "comfyui_queue_prompt":
            prompt_id = client.prompt(arguments["workflow"]).get("prompt_id")
            if arguments.get("wait"):
                result = _wait_for_prompt(prompt_id, arguments.get("timeout_s", 600))
            else:
                result = {"status": "queued", "prompt_id": prompt_id}
        elif name == "comfyui_generate_flux_image":
            seed = arguments.get("seed", -1)
            if seed < 0:
                import random

                seed = random.randint(0, 2**32 - 1)
            workflow = _flux_workflow(
                prompt=arguments["prompt"],
                negative_prompt=arguments.get("negative_prompt", ""),
                width=arguments.get("width", 1280),
                height=arguments.get("height", 720),
                steps=arguments.get("steps", 20),
                cfg=arguments.get("cfg", 1.0),
                seed=seed,
                filename_prefix=arguments.get("filename_prefix", "flux_cc"),
                checkpoint=arguments.get("checkpoint", "flux1-dev-fp8.safetensors"),
                vae=arguments.get("vae", "ae.safetensors"),
            )
            prompt_id = client.prompt(workflow).get("prompt_id")
            result = _wait_for_prompt(prompt_id, 600)
        elif name == "comfyui_generate_ltxv_video":
            seed = arguments.get("seed", -1)
            if seed < 0:
                import random

                seed = random.randint(0, 2**32 - 1)
            workflow = _ltxv_workflow(
                prompt=arguments["prompt"],
                negative_prompt=arguments.get("negative_prompt", "blurry, low quality, distorted"),
                width=arguments.get("width", 768),
                height=arguments.get("height", 512),
                length=arguments.get("length", 97),
                steps=arguments.get("steps", 25),
                cfg=arguments.get("cfg", 3.0),
                seed=seed,
                fps=arguments.get("fps", 25),
                filename_prefix=arguments.get("filename_prefix", "ltxv_cc"),
                checkpoint=arguments.get(
                    "checkpoint", "LTXV/ltxv-2b-0.9.8-distilled-fp8.safetensors"
                ),
                clip=arguments.get("clip", "t5xxl_fp8_e4m3fn.safetensors"),
            )
            prompt_id = client.prompt(workflow).get("prompt_id")
            result = _wait_for_prompt(prompt_id, 1200)
        else:
            raise ValueError(f"Unknown tool: {name}")
    except Exception as exc:  # noqa: BLE001 - keep the helper server alive
        return [TextContent(type="text", text=json.dumps({"error": str(exc)}))]

    return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


async def _main() -> int:
    try:
        client.system_stats()
    except Exception as exc:  # noqa: BLE001 - keep the helper server alive
        print(
            f"comfyui_mcp_server: cannot reach ComfyUI at {COMFYUI_URL}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 1

    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )
    return 0


def main() -> int:
    try:
        return anyio.run(_main)
    except Exception as exc:  # noqa: BLE001 - keep the helper server alive
        print(f"comfyui_mcp_server: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
