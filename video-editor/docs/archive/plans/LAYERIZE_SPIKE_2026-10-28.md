# 2.5D layerize spike — 2026-10-28

Does the RTX 3050 Laptop (4 GB) run a depth + cut-out + fill stack for the
`layers: auto` feature? Yes, sequentially, with about 0.6 GB of headroom at the
tightest stage. This page records the measurements and the versions so
Slice 6 can be built against them.

## Setup

| piece | version |
|---|---|
| GPU | NVIDIA GeForce RTX 3050 Laptop, 4096 MiB (3.68 GiB usable), driver 580.173.02 |
| Python | 3.12.3, isolated venv at `~/tools/da3-venv` |
| torch | 2.11.0+cu128, torchvision 0.26.0+cu128 |
| numpy | 1.26.4 (DA3 requires `numpy<2`) |
| Depth Anything 3 | `ByteDance-Seed/Depth-Anything-3` at `3d835ec`, run from source |
| transformers | 4.57.6 (for BiRefNet and Depth Anything V2) |
| timm / kornia | 1.0.30 / 0.9.0 (BiRefNet) |
| pycolmap | 4.2.1 (DA3 imports it through `utils.export`) |
| moviepy | 1.0.3 (DA3's Gaussian exporter imports `moviepy.editor`) |
| LaMa | `simple-lama-inpainting` 0.1.2, big-lama.pt (196 MB) |

DA3's repository has **no `__init__.py`**, so `pip install -e .` is unreliable;
put `src` on `PYTHONPATH` instead. `open3d`, `xformers`, `gsplat`, the Gradio app
and the benchmark extras are not needed: xformers falls back to `SwiGLUFFN`,
and `open3d` is only imported by `bench/`. Full minimal requirements are in
`scripts/spikes/layerize_spike.py`'s docstring.

**Ollama competes for the GPU.** A resident `qwen3-embedding:0.6b` holds
1.27 GB, which is enough to make BiRefNet OOM. Unload it before a layerize run
(`ollama stop qwen3-embedding:0.6b`); it reloads on the next embedding request.

## Inputs

- `assets/examples/SOH/000.png` and `010.png` from the DA3 repository (real
  photographic scenes).
- One frame from `assets/examples/robot_unitree.mp4`, extracted at 2 s.

**Edge review (2026-10-28):** a human looked at the 1:1 crops in
`videos/2026-10-28/2.5d-spike/edge-review.png` and judged both cut-outs good:
no halo ring, thin structures kept, no visible stair-stepping. BiRefNet stays
the default for its tighter edge; BEN2 is the fallback when the 3.06 GB peak
does not fit. The halo QA check is therefore not needed. Artifacts:
`videos/2026-10-28/2.5d-spike/` (`--out` of the spike script, git-ignored);
`contact-sheet.png` has source / depth / mask / plate / 2.5D side by side.

## Depth

`DA3MONO-LARGE` is Apache-2.0. `DA3-LARGE` and `DA3-GIANT` are CC BY-NC and
were not used. Depth Anything V2 Small is Apache-2.0.

| model | process res | output | resident | peak VRAM | first / warm |
|---|---|---|---|---|---|
| DA3MONO-LARGE | 504 | 280×504 | 1276 MiB | 1449 MiB | 0.89 / 0.21 s |
| DA3MONO-LARGE | 1008 | 574×1008 | 1276 MiB | 1949 MiB | 1.33 s |
| DA3MONO-LARGE | 1280 | 728×1274 | 1276 MiB | 2343 MiB | 1.25–1.43 s |
| DA3-SMALL | 504 | 280×504 | 132 MiB | 227 MiB | 0.79 / 0.09–0.13 s |
| DA3-BASE | 504 | 280×504 | 518 MiB | 693 MiB | 0.85 / 0.14–0.15 s |
| DA V2 Small | native | 680×1208 | — | 292–297 MiB | 0.23–0.66 s |

Edge-quality proxy — mean percentile of depth gradient at the strongest 10% of
image-gradient pixels (higher is better), computed at the depth map's own
resolution; `flat noise` is depth Laplacian in flat image regions (lower is
better):

| model | 000 | 010 | robot | flat noise |
|---|---|---|---|---|
| DA3MONO-LARGE @504 | 0.698 | 0.690 | 0.640 | ~0 |
| DA3MONO-LARGE @1008 | 0.727 | — | — | ~0 |
| DA3MONO-LARGE @1280 | 0.729 | — | — | ~0 |
| DA V2 Small | 0.611 | 0.603 | 0.638 | 0–0.51 |
| DA3-BASE | 0.440 | 0.476 | 0.633 | 0.87–1.98 |
| DA3-SMALL | 0.466 | 0.487 | 0.616 | 0.91–1.60 |

On the two photographic stills, mono-large puts depth edges on image edges
noticeably better than small/base and clearly better than DA2-Small, and its
depth is much smoother in flat regions. On the robot frame all four models are
within 0.03. **Recommendation: DA3MONO-LARGE at 1280 for finals (2.35 GB peak)
and 504 for previews; skip small/base.** DA2-Small remains a fallback when
memory is tighter, but it trades some edge alignment for a 8× smaller model.

## Cut-out

| model | precision | input | peak VRAM | time | subject | soft edge |
|---|---|---|---|---|---|---|
| BiRefNet | bf16 autocast | 1024² | 3059–3062 MiB | 0.72–0.94 s | 29.5 / 28.6 / ~6% | 0.6–0.7% |
| BEN2 | fp32 | native | 2390 MiB | 0.69–0.83 s | 29 / 28 / 6% | 2.1–2.9% |

- BiRefNet at 1024 in **fp32 OOMs** on this card (it needed another 960 MiB
  with 892 MiB free). bf16 autocast is required, and even then it uses 3.06 GB
  — the tightest stage of the chain.
- The two models agree closely: mask IoU 0.971 / 0.975 on the photos, 0.911 on
  the robot frame.
- BEN2 has a much wider alpha transition band. That can mean better matting or
  a halo; the composited `*-2.5d.png` files are the place to judge it.

## Fill

LaMa (`simple-lama-inpainting`) on the BiRefNet mask, dilated by 7 px:

| still | size | time | peak VRAM |
|---|---|---|---|
| 000 | 1208×680 | 1.53 s | 822 MiB |
| 010 | 1208×680 | 2.75 s | 828 MiB |
| robot-2s | 1024×576 | 0.47 s | 652 MiB |

The plate differs by 72–78 mean absolute levels inside the mask and 0.24–0.26
outside, so the fill is confined to the subject region as intended.

## What this means for Slice 6

- The full per-still chain is ~3–5 s and peaks at 3.06 GB. Run stages
  **sequentially** and free the CUDA cache between them; do not keep all three
  models resident.
- Require ~3.1 GB free before BiRefNet. With Ollama resident, either unload it
  or fall back to BEN2 (2.39 GB, fp32, no autocast surprises).
- Run the models as a subprocess in the pinned env (Task 6.3 already says this);
  the engine's own environment must not import torch.
- Depth is relative, which is fine for parallax. The 1280 map (728×1274) is the
  one to use for 1080p output; 504 is visibly coarse when stretched.
- Still open for human review: edge quality at 1080p, halo on the subject
  boundary, and whether a single depth map per still is enough for the
  intended parallax range.

## Reproduce

```bash
~/tools/da3-venv/bin/python scripts/spikes/layerize_spike.py \
  --images <still> <still> <still> \
  --out videos/2026-10-28/2.5d-spike \
  --process-res 1280 --cutout both
```

Environment: `PYTHONPATH` is added by the script from `DA3_SRC` / `BEN2_SRC`;
set `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` and `HF_HOME` to the
model cache. Model downloads: DA3MONO-LARGE ~1.4 GB, BiRefNet ~0.9 GB, BEN2
~0.4 GB, big-lama 196 MB.

## Integration (landed)

The spike's results became the Slice 6 path:

- `PackScene.layers: auto` makes the adapter replace the single still with
  `<still>__bg` (z=0, parallax rate 0.35) and `<still>__fg` (z=5, rate 1.0).
  When the layerize CLI writes `<still>__near` (a depth-derived alpha mask),
  the background splits into a far plate (rate 0.2) and a masked near copy
  (rate 0.6). `<still>__depth` is the source for that mask.
- `layerize_still` runs `layerize.command` in the model environment, caches
  the three outputs by source hash + model + resolution + cutout, and the
  short pipeline calls it once per still key. The engine never imports torch.
- `parallax` is a registered motion primitive; both rates are percentages, so
  the pan is resolution independent.
- `config.example.yaml` has the empty `layerize:` block; set `command` to the
  isolated Python plus `scripts/spikes/layerize_spike.py` (this machine keeps
  the absolute paths in the git-ignored `config.local.yaml`).
- The `layers` composition gate samples two frames 0.4 s apart in every
  parallax scene and fails below a mean luma difference of 0.5; the smoke render
  measures 0.79. Still to do: a halo check (after the contact sheet is reviewed)
  and the cheaper path where a pack supplies a plate and skips segmentation.
