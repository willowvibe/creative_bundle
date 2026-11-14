"""2.5D layerize spike: depth, cut-out, fill and a two-layer parallax preview.

Run this inside the isolated GPU environment, not the engine's. It imports
torch, so the engine must later call it as a subprocess (see Slice 6, Task 6.3).

    python scripts/spikes/layerize_spike.py \
        --images still.png --out videos/2026-10-28/2.5d-spike

The script writes, per still: ``<stem>-depth.png``, ``<stem>-cutout.png``,
``<stem>-mask.png``, ``<stem>-plate.png`` and ``<stem>-2.5d.png``, plus one
``results.json`` with timings and peak VRAM per stage. Licences: DA3MONO-LARGE
and DA3-SMALL/BASE are Apache-2.0, Depth Anything V2 Small is Apache-2.0,
BiRefNet and BEN2 are MIT, LaMa is Apache-2.0. Do not substitute
DA3-LARGE/GIANT or RMBG-2.0: they are non-commercial.

Environment used for the 2026-10-28 spike (Python 3.12, RTX 3050 4 GB):
torch 2.11.0+cu128, numpy 1.26.4, transformers 4.57.6, timm 1.0.30,
kornia 0.9.0, pycolmap 4.2.1, moviepy 1.0.3, simple-lama-inpainting 0.1.2.
DA3 needs ``PYTHONPATH=<Depth-Anything-3>/src`` (the repo has no
``__init__.py``); BEN2 needs ``PYTHONPATH=<BEN2>/src``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

OUT_DEFAULT = Path("videos") / "2026-10-28" / "2.5d-spike"


def log(message: str) -> None:
    print(message, flush=True)


def peak_mib() -> float:
    import torch

    return torch.cuda.max_memory_allocated() / 2**20


def reset_peak() -> None:
    import torch

    torch.cuda.reset_peak_memory_stats()


def save_depth(depth, path: Path) -> None:
    import numpy as np
    from PIL import Image

    depth_np = np.asarray(depth)[0] if getattr(depth, "ndim", 0) == 3 else np.asarray(depth)
    np.save(path.with_suffix(".npy"), depth_np)
    normalized = (depth_np - depth_np.min()) / max(depth_np.max() - depth_np.min(), 1e-6)
    Image.fromarray((normalized * 255).astype(np.uint8)).save(path)


def near_mask(depth_npy: Path, fg_png: Path, size: tuple[int, int]) -> Any:
    """A white RGBA mask whose alpha is the depth-near weight.

    DA3 writes larger values for farther pixels, so the near weight is
    ``1 - normalise(depth)``. The normalisation uses the background range only:
    the subject is covered by the cut-out, and stretching the background's
    narrow range makes the far/near split visible. Under the subject the mask
    is transparent, so the far plate shows through the cut-out's soft edge.
    """
    import numpy as np
    from PIL import Image

    if str(depth_npy).endswith(".npy"):
        depth = np.asarray(np.load(depth_npy), dtype=np.float32)
    else:
        depth = np.asarray(Image.open(depth_npy).convert("L"), dtype=np.float32)
    width, height = size
    depth = np.asarray(
        Image.fromarray(depth).resize((width, height), Image.BILINEAR), dtype=np.float32
    )
    alpha = np.asarray(Image.open(fg_png).getchannel("A").resize((width, height), Image.NEAREST))
    background = alpha <= 127
    if background.any():
        low, high = np.percentile(depth[background], [2, 98])
    else:
        low, high = float(depth.min()), float(depth.max())
    normalised = np.clip((depth - low) / max(float(high - low), 1e-6), 0.0, 1.0)
    near = 1.0 - normalised
    near[~background] = 0.0
    rgba = np.full((height, width, 4), 255, dtype=np.uint8)
    rgba[..., 3] = (near * 255).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def run_depth(images: list[Path], out: Path, args) -> dict:
    import torch

    sys.path.insert(0, str(Path(args.depth_src).expanduser()))
    from depth_anything_3.api import DepthAnything3

    started = time.perf_counter()
    model = DepthAnything3.from_pretrained(args.depth_model)
    model = model.to("cuda").eval()
    load_s = time.perf_counter() - started
    log(f"depth: load {load_s:.1f} s, resident {peak_mib():.0f} MiB")

    timings = {}
    for image_path in images:
        import numpy as np
        from PIL import Image

        image = np.asarray(Image.open(image_path).convert("RGB"))
        reset_peak()
        started = time.perf_counter()
        with torch.inference_mode():
            prediction = model.inference([image], process_res=args.process_res)
            torch.cuda.synchronize()
        elapsed = time.perf_counter() - started
        depth = np.asarray(prediction.depth)[0]
        # Depth is relative; store the raw map and a visual PNG.
        np.save(out / f"{image_path.stem}-depth.npy", depth)
        normalized = (depth - depth.min()) / max(depth.max() - depth.min(), 1e-6)
        Image.fromarray((normalized * 255).astype(np.uint8)).save(
            out / f"{image_path.stem}-depth.png"
        )
        timings[image_path.stem] = {
            "seconds": round(elapsed, 2),
            "peak_vram_mib": round(peak_mib(), 1),
            "shape": list(depth.shape),
        }
        log(f"depth: {image_path.stem} {elapsed:.2f} s, peak {peak_mib():.0f} MiB")

    del model
    torch.cuda.empty_cache()
    return {"model": args.depth_model, "load_s": round(load_s, 1), "items": timings}


def run_birefnet(images: list[Path], out: Path) -> dict:
    import torch
    from PIL import Image
    from torchvision import transforms
    from transformers import AutoModelForImageSegmentation

    started = time.perf_counter()
    model = AutoModelForImageSegmentation.from_pretrained(
        "ZhengPeng7/BiRefNet", trust_remote_code=True
    )
    model = model.to("cuda").eval()
    load_s = time.perf_counter() - started
    transform = transforms.Compose(
        [
            transforms.Resize((1024, 1024)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ]
    )
    log(f"birefnet: load {load_s:.1f} s")

    timings = {}
    for image_path in images:
        image = Image.open(image_path).convert("RGB")
        tensor = transform(image).unsqueeze(0).to("cuda")
        reset_peak()
        started = time.perf_counter()
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            prediction = model(tensor)[-1].sigmoid()
            torch.cuda.synchronize()
        elapsed = time.perf_counter() - started
        mask = transforms.ToPILImage()(prediction[0].squeeze().float().cpu()).resize(image.size)
        cutout = image.copy()
        cutout.putalpha(mask)
        mask.save(out / f"{image_path.stem}-mask.png")
        cutout.save(out / f"{image_path.stem}-cutout.png")
        timings[image_path.stem] = {
            "seconds": round(elapsed, 2),
            "peak_vram_mib": round(peak_mib(), 1),
        }
        log(f"birefnet: {image_path.stem} {elapsed:.2f} s, peak {peak_mib():.0f} MiB")

    del model
    torch.cuda.empty_cache()
    return {"model": "ZhengPeng7/BiRefNet", "load_s": round(load_s, 1), "items": timings}


def run_ben2(images: list[Path], out: Path, ben2_src: str) -> dict:
    import torch
    from PIL import Image

    sys.path.insert(0, str(Path(ben2_src).expanduser()))
    from ben2 import AutoModel

    started = time.perf_counter()
    model = AutoModel.from_pretrained("PramaLLC/BEN2").to("cuda").eval()
    load_s = time.perf_counter() - started
    log(f"ben2: load {load_s:.1f} s")

    timings = {}
    for image_path in images:
        image = Image.open(image_path).convert("RGB")
        reset_peak()
        started = time.perf_counter()
        with torch.inference_mode():
            foreground = model.inference(image)
            torch.cuda.synchronize()
        elapsed = time.perf_counter() - started
        foreground.save(out / f"{image_path.stem}-ben2-cutout.png")
        foreground.getchannel("A").save(out / f"{image_path.stem}-ben2-mask.png")
        timings[image_path.stem] = {
            "seconds": round(elapsed, 2),
            "peak_vram_mib": round(peak_mib(), 1),
        }
        log(f"ben2: {image_path.stem} {elapsed:.2f} s, peak {peak_mib():.0f} MiB")

    del model
    torch.cuda.empty_cache()
    return {"model": "PramaLLC/BEN2", "load_s": round(load_s, 1), "items": timings}


def run_fill(images: list[Path], out: Path) -> dict:
    import torch
    from PIL import Image, ImageFilter
    from simple_lama_inpainting import SimpleLama

    started = time.perf_counter()
    lama = SimpleLama()
    load_s = time.perf_counter() - started
    log(f"lama: load {load_s:.1f} s")

    timings = {}
    for image_path in images:
        image = Image.open(image_path).convert("RGB")
        mask_path = out / f"{image_path.stem}-mask.png"
        if not mask_path.exists():
            log(f"lama: skipping {image_path.stem}, no mask")
            continue
        mask = Image.open(mask_path).convert("L")
        # Grow the subject mask a little so the plate has room behind the edge.
        dilated = mask.filter(ImageFilter.MaxFilter(7))
        reset_peak()
        started = time.perf_counter()
        plate = lama(image, dilated)
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - started
        plate.save(out / f"{image_path.stem}-plate.png")
        near_mask(
            out / f"{image_path.stem}-depth.npy", mask_path, (image.width, image.height)
        ).save(out / f"{image_path.stem}-mask-near.png")

        # Two-layer preview: subject nudged and scaled over the filled plate.
        width, height = image.size
        subject = image.copy()
        subject.putalpha(mask)
        subject = subject.resize((int(width * 1.04), int(height * 1.04)), Image.LANCZOS)
        canvas = plate.copy()
        canvas.paste(subject, (int(width * 0.012), 0), subject)
        canvas.save(out / f"{image_path.stem}-2.5d.png")
        timings[image_path.stem] = {
            "seconds": round(elapsed, 2),
            "peak_vram_mib": round(peak_mib(), 1),
            "size": [width, height],
        }
        log(f"lama: {image_path.stem} {elapsed:.2f} s, peak {peak_mib():.0f} MiB")

    del lama
    torch.cuda.empty_cache()
    return {"model": "simple-lama-inpainting 0.1.2", "load_s": round(load_s, 1), "items": timings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", nargs="+", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT)
    parser.add_argument("--depth-model", default="depth-anything/DA3MONO-LARGE")
    parser.add_argument("--process-res", type=int, default=1280)
    parser.add_argument(
        "--depth-src", default=os.environ.get("DA3_SRC", "~/tools/Depth-Anything-3/src")
    )
    parser.add_argument("--ben2-src", default=os.environ.get("BEN2_SRC", "~/tools/BEN2/src"))
    parser.add_argument(
        "--cutout", choices=("birefnet", "ben2", "both", "none"), default="birefnet"
    )
    parser.add_argument("--skip-fill", action="store_true")
    args = parser.parse_args()

    images = [path.resolve() for path in args.images]
    missing = [str(path) for path in images if not path.is_file()]
    if missing:
        raise SystemExit(f"missing images: {', '.join(missing)}")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    results: dict[str, object] = {"images": [str(path) for path in images]}
    results["depth"] = run_depth(images, out, args)
    if args.cutout in ("birefnet", "both"):
        results["birefnet"] = run_birefnet(images, out)
    if args.cutout in ("ben2", "both"):
        results["ben2"] = run_ben2(images, out, args.ben2_src)
    if not args.skip_fill:
        if args.cutout == "none":
            raise SystemExit("--skip-fill needs a cut-out; choose birefnet or ben2")
        results["lama"] = run_fill(images, out)

    (out / "results.json").write_text(json.dumps(results, indent=2))
    log(f"wrote {out / 'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
