"""Free-only Muruga stills: agy_image, then flow_image. The chain never reaches a paid provider.

usage (from video-editor/):  uv run python <this> OUT_DIR [ids...]
"""

import asyncio
import json
import sys
from pathlib import Path

from video_editor_asset_ingestion.config import AssetProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers.registry import get_provider

FREE_CHAIN = ["agy_image", "flow_image"]  # deliberately no poyo_ai / openrouter / fal_ai

STYLE = (
    "Traditional South Indian devotional painting, in the style of a classic Tamil temple calendar "
    "oil painting: rich warm gold, saffron and deep peacock blue, soft divine glow, reverent and serene, "
    "highly detailed, graceful correct anatomy, exactly two arms, calm compassionate expression. "
    "Landscape 16:9 composition. Absolutely no text, no letters, no words, no numbers, no captions, "
    "no watermark, no signature, no logo, no frame border."
)

PROMPTS = {
    "m1_vel_peacock": "Lord Murugan standing serenely, holding his golden Vel spear upright in one hand and "
    "blessing with the other (abhaya mudra), a magnificent peacock with open tail feathers behind him, "
    "jewelled golden crown, sandalwood mark on forehead, Palani hills and a hilltop temple at sunrise.",
    "m2_bala_murugan": "Bala Murugan, the divine child form of Lord Murugan, seated gracefully on a peacock "
    "beside a lotus pond at dawn, holding a small golden Vel, gentle innocent smile, lotus flowers and "
    "soft morning mist, golden light.",
    "m3_portrait": "Close portrait of Lord Murugan, youthful radiant face with a gentle smile, ornate "
    "golden crown with jewels, holy ash and red kumkum mark on the forehead, garland of marigold and "
    "jasmine, the tip of his golden Vel beside him, halo of warm golden light, dark temple background.",
    "m4_dandayuthapani": "Lord Murugan as Dandayuthapani of Palani: a young ascetic standing on a hilltop "
    "holding a wooden staff, simple saffron loincloth, shaven head with holy ash, serene meditative gaze, "
    "vast sky at golden sunset, oil lamps glowing at his feet.",
    "m5_temple_arati": "Lord Murugan seated on a peacock throne inside a glowing stone temple sanctum, "
    "holding the golden Vel, surrounded by brass oil lamps and flower offerings, camphor arati flame "
    "glowing in front, peacock feathers and temple bells, divine radiance filling the sanctum.",
}


async def one(cfg: AssetProviderConfig, key: str, out_dir: Path) -> dict:
    prompt = f"{PROMPTS[key]} {STYLE}"
    for name in FREE_CHAIN:
        out = out_dir / f"{key}__{name}.png"
        req = AssetRequest(asset_id=key, job_id="om-saravana-bhava-108", scene_id=key,
                           asset_type=AssetType.IMAGE, prompt=prompt, provider=name,
                           params={"width": 1920, "height": 1080, "aspect_ratio": "16:9"}, output_ext="png")
        try:
            prov = get_provider(name)(cfg.providers.get(name))
            res = await prov.generate(req, out)
            path = getattr(res, "path", None) or getattr(res, "output_path", None) or out
            print(f"OK   {key} via {name}: {path}", flush=True)
            return {"id": key, "provider": name, "path": str(path), "cost": getattr(res, "cost", 0.0)}
        except Exception as e:  # noqa: BLE001 - report and fall to the next free provider
            print(f"FAIL {key} via {name}: {type(e).__name__}: {str(e)[:300]}", flush=True)
    return {"id": key, "provider": None, "path": None}


async def main() -> None:
    out_dir = Path(sys.argv[1]).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    keys = sys.argv[2:] or list(PROMPTS)
    cfg = AssetProviderConfig.load("config.yaml")
    results = [await one(cfg, k, out_dir) for k in keys]  # sequential: agy allows few sessions
    (out_dir / "gen_results.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
