# Cosmic Saju rebuild — 2026-10-28

Built with the current `ve short` / Composition IR pipeline.

| File | Source pack | Duration | QA |
|---|---|---|---|
| `saju-v1-your-animal.mp4` | `shorts/saju-v1-your-animal/short.yaml` | 42.5 s | PASS |
| `saju-january-animal.mp4` | `shorts/saju-january-animal/short.yaml` | 52.83 s | PASS |
| `day-master-deck.mp4` | `shorts/day-master-deck/short.yaml` | 74.23 s | PASS |

Commands used:

```bash
uv run ve short shorts/saju-v1-your-animal/short.yaml --auto-approve \
  --work-root projects --output-root videos/2026-10-27/rebuild
uv run ve short shorts/saju-january-animal/short.yaml --auto-approve \
  --work-root projects --output-root videos/2026-10-27/rebuild
uv run ve short shorts/day-master-deck/short.yaml --auto-approve \
  --work-root projects --output-root videos/2026-10-27/rebuild
```

Existing `projects/<job>/.cache/assets` entries were reused; no new paid
provider calls were required. Each job's QA report is at `projects/<job>/qa.json`.

Caption standard for all three: box `x=90, y=1356, 900x180`, matching
`day-master-deck`. The January and V1 cuts were re-rendered after that change.

The older script-only cuts (`what-is-saju`, the pre-pack `saju-zodiac-animal`
build, `saju-lead-gen`, and the July promo) do not have content packs and were
not rebuilt by this pass.
