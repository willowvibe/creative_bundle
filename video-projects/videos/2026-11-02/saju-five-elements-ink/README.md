# The Five Elements: ink-wash test short (2026-11-02)

A Cosmic Saju short drawn entirely in code, to test whether Lemo-Opuscar's
**Chinese Ink Wash** style suits the series. It uses no image or video model.

- **Output:** `saju_five_elements_ink.mp4`, 1080×1920, 30 fps, 46.6 s.
- **Spend:** Sarvam voice $0.009 (390 characters, `bulbul:v4-flash`, `sarang_en_narration`). Picture and score $0.
- **Script:** `script.md`, ten lines. The CTA names cosmicsaju.com once, and the URL appears only on the end card.

## Build

| Step | File | Run |
|---|---|---|
| Voice | `build/tts.py` | `uv run python videos/2026-11-02/saju-five-elements-ink/build/tts.py` (skips existing WAVs) |
| Picture | `build/hf/` (HyperFrames; `scene.js` repaints every frame from `t`) | `cd build/hf && npx hyperframes render -o ../picture.mp4 --fps 30` |
| Score | `build/music/score.py` (guqin synthesis, needs numpy, scipy, soundfile, numba) | `python build/music/score.py` |
| Mix and mux | `build/mix.sh` | `sh build/mix.sh` |

Timing lives in two places that must agree: `VO` in `build/hf/scene.js` and
`STARTS` in `build/mix.sh`.

## Credits

- **Brush engine, compositor and landscape helpers:** `build/hf/engine/`. **Guqin synthesis:** `build/music/vendor/`. Both are vendored from
  [lemomo-ai/lemo-opuscar](https://github.com/lemomo-ai/lemo-opuscar) @ `bebbbe1`, MIT, © LemoLab.
  The only change is the `lib.js` import path. The story, shots, score and seal are our own.
- **Fonts:** Cormorant Garamond and Ma Shan Zheng, SIL OFL 1.1. Licence files are in `build/hf/fonts/`.
