"""Generate the Saju voiceover via ElevenLabs 'Harish voice one'.

Loads the .env from the repo root, calls the existing
ElevenLabsTTSProvider (no duplication of the HTTP/auth logic), and
writes the audio to projects/saju-2026-07-03/voiceover.mp3.

Run with:  uv run python scripts/saju_generate_voiceover.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Load .env so ELEVENLABS_API_KEY is available without exporting manually.
_env = REPO_ROOT / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())

SCRIPT = """There is a pattern behind every life.
A hidden map written at the moment you were born.
Not luck. Not coincidence.
But destiny, waiting to be understood.

In Saju, the Four Pillars reveal the structure of your life,
your strengths, your struggles,
your timing,
and the energy that shapes your path.

The five elements tell a deeper story.
What supports you.
What drains you.
What is missing.
And what must be balanced for life to flow.

Your life moves in cycles.
Some seasons test you.
Some seasons open doors.
When you understand your timing,
you stop forcing life and start moving with it.

Career, health, relationships, purpose.
The answers are not random.
They are already written in your chart,
waiting to be decoded with clarity and wisdom.

This is more than astrology.
This is a guide to understanding yourself,
your direction,
and your true potential.
Welcome to the world of Saju."""

VOICE_ID = "32EwRSV41WufKgzh80Ri"  # "Harish voice one" on this account
JOB_ID = "saju-2026-07-03"
ASSET_ID = "voiceover-main"


async def main() -> None:
    from video_editor_asset_ingestion.config import ProviderConfig
    from video_editor_asset_ingestion.models import AssetRequest, AssetType
    from video_editor_asset_ingestion.providers.tts.elevenlabs_tts import ElevenLabsTTSProvider

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        sys.exit("ELEVENLABS_API_KEY not set in .env or environment")

    cfg = ProviderConfig(
        api_key_env="ELEVENLABS_API_KEY",
        base_url="https://api.elevenlabs.io",
        default_model="eleven_flash_v2_5",
    )

    out = REPO_ROOT / "projects" / JOB_ID / "voiceover.mp3"

    async with ElevenLabsTTSProvider(cfg) as provider:
        req = AssetRequest(
            job_id=JOB_ID,
            asset_id=ASSET_ID,
            asset_type=AssetType.TTS,
            scene_id="voiceover",
            prompt=SCRIPT,
            params={
                "voice_id": VOICE_ID,
                "model": "eleven_flash_v2_5",
                "output_format": "mp3_44100_128",
            },
        )
        result = await provider.generate(req, out)
        print(f"Voiceover: {result.local_path}")
        print(f"Duration:  {result.duration_ms / 1000:.2f}s")
        print(f"Provider:  {result.metadata.get('provider')}")
        print(f"Model:     {result.metadata.get('model')}")


if __name__ == "__main__":
    asyncio.run(main())
