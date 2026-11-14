# Demo plan — `layers: auto` on a real pack

**Full-feature run 2026-10-28 — PASS, $0.00 spent.** Output:
`outputs/saju-january-layers/final.mp4` (52.833 s). All 19 QA checks passed:
`layers` 1.60 min luma diff, `flourishes` **2** (the registry's limit),
`visual` 9 frames at min stddev 20.1. Actual cost: 22 cached cost lines, all
`$0.00` (`outputs/projects.db`); the `$0.41` estimate is only the cap.

This run exercises every new feature on a real pack:

- **Depth, not just a rate:** the layerize CLI derives a near-background alpha
  mask from `depth.png`; the adapter renders the plate as a far layer (rate
  0.2) plus a depth-masked near copy (rate 0.6), with the cut-out at 1.0.
  Verified in the browser between 1.0 s and 2.0 s: far −0.9 px, near −2.7 px,
  foreground −4.8 px. The mask is applied (a red-fill test shows the masked
  region, 21.9% of the viewport, not the full frame).
- **`sfx: auto`:** eight cues — `card_flip` 4.90, `whoosh` 11.73, `hit` 18.80,
  `whoosh` 27.33, `click` 32.53, `whoosh` 37.27, 42.20, 47.73 — all within
  45 ms of the cut/entrance, +1.7 LUFS over the voice.
- **Motions:** `drift` on the ambient scenes, `flip` on `calendar`, `stamp` on
  `jan_twentieth`, `pop` on `seventeen_days`; `rise` remains on the pills,
  chips and end card.
- **Light leak:** placement `both`, so leaks at `calendar` (4.90) and
  `one_of_eight` (47.73). At 5.3 s the top-left R−B is +36 versus −2.3 at 4.5 s.
- **Design:** `designs/saju_series_layers.json` (a copy with `light_leak`);
  the shipped `saju_series` design is untouched.

The depth mask is intentionally quiet on this still: the hook is a dark charcoal
sky behind a top scrim, so the two background rates are subtle even though the
transforms are correct. A textured background would show it more.

**Build notes.** The first attempt OOM'd in BiRefNet because Ollama held
1.23 GB of the 4 GB card; it failed before any provider call, so spend stayed
$0. The old cache (`8d44662dffc2ba98`, valid depth/fg/bg from the same source)
was migrated to the v2 output format by copying those three files and
generating `near.png` on CPU — no model ran. The new key is
`layerize/afee7baab18df579/`.

Known warnings: HyperFrames reports `duplicate_media_discovery_risk` because
the far and near layers share `bg.png`; the render and the measured transforms
are correct. The QA `sfx` block used to omit scene-cut whooshes and the
voice-only reference; both are fixed (`scene_cuts=True`, `reference=voiced`),
so `qa.json` now reports all eight cues, per-cue added dB and **+1.7 LUFS**.

**Manifest caveat.** `manifest.json` was copied from the source project, so its
old `qa` stage still names `saju-january-animal`; `qa.json` is current. The CLI
does not re-save the manifest after QA (recorded as an open issue).

The rest of this page is the original plan, kept for reproduction.

## Why this pack and scene

`shorts/saju-january-animal/short.yaml`, scene `hook` (still `dragon_moon`: a
jade dragon coiled around a moon over a charcoal sky). It is the cleanest
subject/background split in the shipped packs, the hook is a full 4.87 s scene
so the pan has room, and both the still and the voice lines are already in
`projects/saju-january-animal/.cache/` — a re-run should cost **$0**.

## The change

Copy the pack, do not edit the shipped one:

```bash
mkdir -p shorts/saju-january-layers
cp shorts/saju-january-animal/short.yaml shorts/saju-january-layers/short.yaml
```

Then set the job id and add one line under `scenes[0]`:

```yaml
job_id: saju-january-layers
...
  - id: hook
    still:
      key: dragon_moon
      prompt: >-
        ...unchanged...
    layers: auto          # <-- the only functional change
```

Nothing else changes: same prompts, same voice, same lines, same design.

## Cost

| item | unit | count | cost |
|---|---|---|---|
| stills (`poyo_ai/gpt-image-2.5-fixed`) | $0.03 | 6 | $0.18 |
| voice (`sarvam_tts/bulbul:v4-flash`) | $0.023 | 10 | $0.23 |
| **estimate written by `ve short`** | | | **$0.41** |

With the cache copied, every request is a hit and the ledger records **$0.00**;
the $0.41 cap is only the worst case if a request payload changed (it does
not). The `layers` field is not part of `AssetRequest.cache_key`.

Cache reuse:

```bash
# Same project shape, same content-addressed cache; no provider call on a hit.
cp -a projects/saju-january-animal projects/saju-january-layers
```

The stills (`still_dragon_moon`, …) and the ten `line_*` TTS files are already
there. Whisper still re-runs for the new job (free, a minute or two).

## Run

```bash
# Requires the layerize block in config.local.yaml (already present).
uv run ve short shorts/saju-january-layers/short.yaml --auto-approve
```

Expected: ~2–4 min (whisper + one layerize build + one render). `layerize`
writes `projects/saju-january-layers/layerize/<hash>/{depth,fg,bg}.png`.

If the cache copy is not done, the run would spend up to $0.41. Check the
printed estimate: a cache-hit run still prints $0.41 because the estimate
prices requests, not cache state.

## What to inspect

1. `projects/saju-january-layers/qa.json` — `layers` check passes with a luma
   diff well above 0.5, `visual` passes, loudness/sync unchanged.
2. `outputs/saju-january-layers/final.mp4` — around 0:00–0:05 the dragon should
   move against the sky at a different rate from the background. Grab two
   frames (e.g. 1.0 s and 2.0 s) and compare.
3. The cut edge at 1:1 (reuse the `edge-review` crop method) — no halo, no
   missing whiskers/scales.

## Failure modes and fallbacks

- **The cut-out keeps the whole frame** (dragon + clouds + sky as one blob):
  the pan looks like a global slide. Try `calendar` (window, calendar, sill) or
  `plum_split` (two half-trees) instead; both have a clear background.
- **Halo or chewed whiskers:** switch `layerize.cutout: ben2` (2.39 GB, softer
  edges) and rerun; BEN2 was judged acceptable in the 1:1 review.
- **Layerize OOM:** unload Ollama (`ollama stop qwen3-embedding:0.6b`), then
  rerun. BiRefNet needs 3.06 GB; BEN2 needs 2.39 GB.
- **Cache miss / unexpected spend:** stop and inspect the ledger. The maximum
  authorised by the estimate is $0.41.

## Rollback

```bash
rm -rf shorts/saju-january-layers projects/saju-january-layers outputs/saju-january-layers
```

The shipped `saju-january-animal` pack and project are untouched.
