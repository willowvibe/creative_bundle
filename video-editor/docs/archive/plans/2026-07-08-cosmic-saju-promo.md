# Plan: Cosmic Saju 16:9 HyperFrames Promo Video

## Goal
Build a 20-second, 1920×1080 30fps cinematic HyperFrames promotional video for **Cosmic Saju** (Korean Four Pillars / Saju astrology platform) using multi-panel / frame-within-frame transitions. Final output: `videos/2026-07-08/cosmic-saju/renders/cosmic-saju-promo.mp4`.

## Approach
Fork the proven direct-HyperFrames composer pattern from `projects/ad_biodata/compose_ad_hyperframes.py`. A single self-contained Python script will:
1. Generate the HTML/CSS/JS composition (no external image assets — all visuals are CSS/SVG).
2. Render a silent MP4 via the local `hyperframes` CLI.
3. Synthesize a soft ambient pad + five transition chimes via deterministic FFmpeg filters.
4. Mix audio onto the silent video and normalize loudness.

This avoids the generic `ScenePlanVideo` Remotion composition (which is not built for split-screen grids) and avoids reliance on FAL/HyperFrames video generation for source assets.

## Scene Breakdown (matches the brief)

| Frame | Time | Duration | Visual |
|-------|------|----------|--------|
| 1. Birth chart reveal | 0.0 – 3.0 | 3.0s | Full-screen dark-navy canvas; Four Pillars (Year/Month/Day/Hour) glyphs animate into a 2×2 symmetrical grid with ink-particle aura. |
| 2. Element grid | 3.0 – 6.0 | 3.0s | 2×2 hyperframe split; four element cards (Fire, Water, Wood, Metal) with vertical percentage bars rising and soft particle color coding; Earth appears as a subtle center glow. |
| 3. Report preview | 6.0 – 10.0 | 4.0s | Frame-within-frame: glass tablet/phone mockup slides in and scrolls faux PDF report pages (chart, career table, timing table) against the cosmic background. |
| 4. Product tiers | 10.0 – 13.0 | 3.0s | Three vertical panels slide and lock into place: Free Sample, Essential Report, Deep Destiny Report — each with price tag and a single report snippet. |
| 5. Compatibility | 13.0 – 16.0 | 3.0s | Panels merge; two circular birth charts overlap and interlock like puzzle pieces, joined by a soft golden connecting light (marriage/compatibility theme). |
| 6. Brand reveal | 16.0 – 20.0 | 4.0s | Final full-screen reveal: "Cosmic Saju" logo forms from dispersing ink, tagline "Your Chart. Your Timing. Your Story.", then CTA "Discover Your Reading — cosmicsaju.com". |

Total runtime: **20.0 seconds**. Transitions: 0.45s cross-dissolves between scenes.

## Visual Design Tokens
- Background: `#0F0B1E` (deep navy) + layered radial ink-wash gradients.
- Accents: gold `#C9A227`, teal `#2DD4BF`, soft violet `#7C3AED`, warm amber `#F59E0B`.
- Typography: `Noto Serif KR` / `Playfair Display` for logo/headlines; `Noto Sans KR` / `Inter` for data labels.
- Textures: SVG fractal-noise grain overlay at 4% opacity; soft radial glows; no neon/sci-fi.
- Motion: slow `scale(1.05 → 1.0)` push-in on hero frames; `opacity` + `translateY` stagger for elements; gentle parallax on background layers.

## Audio Design
To keep the build reproducible and avoid unexpected ElevenLabs API latency during the render, the script will **synthesize audio in FFmpeg**:
- **Ambient bed**: 20s low-frequency pad from mixed sine waves + a slow amplitude envelope + low-pass filter + light reverb, normalized to ~-18 dBFS.
- **Transition chimes**: five short resonant bell sounds (sine + decay) mixed at the exact frame boundary timestamps: 3.0s, 6.0s, 10.0s, 13.0s, 16.0s.
- **Final mix**: `amix=inputs=6:duration=longest:normalize=0` + `loudnorm=I=-16:TP=-1.5:LRA=11`.

Optional: if the user prefers, we can swap the synthesized pad for an ElevenLabs `music-generate` call with a one-line change.

## Files to Create
1. `scripts/compose_cosmic_saju.py` — main composer script.
2. `videos/2026-07-08/cosmic-saju/work/hyperframes/` — HyperFrames project workspace (created by script).
3. `videos/2026-07-08/cosmic-saju/renders/cosmic-saju-promo.mp4` — final deliverable.

Optionally:
4. `tests/test_compose_cosmic_saju.py` — smoke test that the HTML plan is generated correctly and contains the six scenes.

## Implementation Steps
1. Create `scripts/compose_cosmic_saju.py`:
   - Hard-code the 6 scene definitions with `start`, `duration`, `id`, and `caption`.
   - `build_html()` returns a complete 1920×1080 HTML document with:
     - Google Fonts import (with system fallback).
     - CSS for layout, grid, card, mockup, typography, ink-wash gradients, grain overlay.
     - Per-scene HTML functions (`scene_1` … `scene_6`).
     - GSAP timeline that cross-fades scenes, staggers element entrances, drives scale push-ins, and reveals text.
   - `download_gsap()` caches GSAP locally in the project dir.
   - `render_hyperframes()` calls `hyperframes render <project_dir> -o ... --fps 30 --resolution landscape --quality standard --quiet`.
   - `generate_pad()`, `generate_chime()`, `mix_final()` build and mix audio.
2. Run the script end-to-end to produce `videos/2026-07-08/cosmic-saju/renders/cosmic-saju-promo.mp4`.
3. Verify with `ffprobe` that the output is 1920×1080, ~20s, 30fps, and has audio.
4. (Optional) Add a small unit test for the HTML builder.

## Open Questions / User Decisions
- **Audio source**: Should I synthesize the ambient pad/chimes via FFmpeg (fast, free, deterministic) or attempt ElevenLabs music/SFX for higher production value (small cost, possible queue delays)?
- **Voiceover**: The brief has no narration. Do you want a generated English/Korean voiceover, or is text + music sufficient?
- **Logo**: No logo asset exists. I will render "Cosmic Saju" as elegant CSS/SVG typography. If you have a logo file, I can overlay it in Frame 6.

## Success Criteria
- Final MP4 is 1920×1080, 30fps, ~20s, with audio.
- All six requested frames are clearly present and transition on the specified timings.
- Style is premium dark-navy + gold/teal, minimal, no neon cliché.
- Script is self-contained and reproducible.
