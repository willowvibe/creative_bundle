# Cosmic Saju — Reading 8 Characters (long-form production)

**Source:** the 2026-10-29 spec (`docs/AUDIT_2026-10-29.md` and the attached
script). **Status:** all eight chapter packs are authored and pass
`preflight_short`; **nothing has been rendered and no spend has been approved.**

| Item | Value |
|---|---|
| Canvas | 1920 × 1080, 30 fps (`aspect: "16:9"`) |
| Design | `designs/cosmic_saju_landscape.json` (5 % title-safe; captions at y 880) |
| Chapters | 8 packs in `chapters/`, joined by `longform.yaml` |
| Scenes / lines | 34 scenes, 34 voice lines (about 1,400 words) |
| Images priced | 52 generation requests (stills + BG/MG/FG plates) |
| Narration | Sarvam TTS, one call per line; swap `voice_id` to the clone |
| Captions | `captions.mode: sidecar` → `final.srt` |
| End card | disabled in every chapter; the last scene carries the CTA |
| Chapter join | `crossfade`, 0.8 s |
| Estimate | **$2.08** total (`pricing.yaml` 2026-10-22); re-price before approving |

## Chapter map

| Pack | Shots | Scenes | Notes |
|---|---|---|---|
| `00-cold-open.yaml` | S01–S04 | 4 | mist dissolve, push-through, seal stamp, push-through |
| `01-what-saju-is.yaml` | S05–S08 | 4 | medallion ring, 60-cycle grid, hanji turn |
| `02-building-her-chart.yaml` | S09–S12 | 4 | sundial, plum tree, four-pillars chart |
| `03-the-day-master.yaml` | S13–S16 | 4 | chart, jewel, polishing, countdown |
| `04-element-balance.yaml` | S17–S20 | 4 | element bars ×2, forest jewel, strength gauge |
| `05-the-element-she-needs.yaml` | S21–S24 | 4 | feeding cycle, mountain, sinking jewel, CTA |
| `06-luck-decades.yaml` | S25–S30 | 6 | seasons path, decade ribbon ×2, earth decades, combination, 2028 |
| `07-your-turn.yaml` | S31–S34 | 4 | open gate, snapshot, reading, end screen |

## Art

The prompt-level art direction lives in each chapter (`image.style_suffix`
carries the shared ink-wash line). The spec's I01–I28 map onto the packs:

- **I01** `00` S01 / `07` S34 (regenerated as `i28` because packs do not share
  still keys across jobs).
- **I02** `00` S02 (BG/MG/FG).
- **I03/I14** `00` S03, `03` S16, `04` S18, `05` S24 (separate keys per pack).
- **I04** `00` S04; **I25** `07` S31 (open gate, generated).
- **I05** `01` S05; **I06** `01` S06 (medallion ring is HTML, not 12 images);
  **I07** `01` S07 (60-cycle grid is HTML); **I08** `01` S08.
- **I09–I13** `02` S09–S12 and `03` S14–S15.
- **I15–I20** `04` S17–S20 and `05` S21–S23.
- **I21–I24** `06` S25–S29; **I26–I27** `07` S32–S33.

Charts are **static HTML components** (`PackScene.graphic`), not generated
images: four pillars, element bars, decade ribbon, medallion ring, 60-cycle
grid, feeding cycle, strength gauge and countdown.

## What still needs a human decision

1. **Voice.** `voice_id` is `sarvam:shubh` (built-in) in all packs. Replace it
   with `sarvam_clone:<voice_id>` from `ve voice-clone create` once the clone is
   approved. The spec asks for the creator's own voice.
2. **Music.** `music.path` is empty in every pack; the spec wants one
   gayageum/piano bed at −22 dB with swells at the cold open and final reveal.
   Set `music.path` (a local file) and optional `music.swells` in each pack, or
   add the bed only on the joined file.
3. **Art.** 52 images are priced but not generated. Review the prompts in the
   packs against the spec before approving the render.
4. **Spend.** `ve longform --auto-approve` approves every chapter's estimate.
   Review the total first.

## Render

```bash
# One chapter as a pilot (no join):
ve short shorts/cosmic-saju-longform/chapters/00-cold-open.yaml --auto-approve

# Full video (renders 8 jobs, joins, runs joined QA):
ve longform shorts/cosmic-saju-longform/longform.yaml --auto-approve

# Quality pass after a build:
ve critique cosmic-saju-longform-ch00 --pack shorts/cosmic-saju-longform/chapters/00-cold-open.yaml
```

Outputs: `outputs/cosmic-saju-longform-chNN/final.mp4` per chapter,
`outputs/cosmic-saju-longform/final.mp4` for the joined file (plus
`final.srt` per chapter from the sidecar mode).

## Known approximations

- On-screen text uses the design's **pill** rows (`lines[].show`); the spec's
  free-positioned title cards and lower-thirds are not modelled yet.
- Chapter packs cannot share still keys; reused spec images are regenerated
  (noted above).
- Ink bleed uses the `mask_reveal` circle, not an SVG turbulence mask.
- True FLIP is not modelled; `morph` is an explicit CSS property morph.
- The music bed is local only (no generated track in this path).

## Pilot result (2026-10-29)

Rendered `chapters/00-cold-open.yaml` end to end:

- Output: `/mnt/data2/video-editor-data/outputs/cosmic-saju-ch0/final.mp4`
  (37.9 s, 1920×1080, 4.8 MB) + `final.srt`.
- QA: **PASS** on every gate (captions, stills, cues, safe area, layers,
  loudness, cost). The only warning is the composition lint note about 18
  timed elements on one HyperFrames track.
- Actual spend: **$0.29** (6 images via poyo after agy failed, 4 Sarvam calls).
  `bulbul:v4-flash` returned 400, so the pack now pins `model: bulbul:v3`; the
  estimate and actual match at $0.29.
- Music: Openverse/Jamendo, “Rameau in Meditation” by Lena Selyanina,
  CC BY 3.0, mixed at −22 dB with the cold-open/final swells. See
  `music/CREDITS.md`.
- TTS reads faster than the 145 wpm target: chapter 0 is 37.9 s, not 50 s.

Visual follow-ups before the full run:

- S03's lanterns came out **lit**; the prompt says "unlit" but the model lit
  them. Add "no flame, no glow, unlit paper" and regenerate.
- S02's generated MG (the woman) is not visible; the FG lattice dominates.
  Strengthen the MG prompt and soften the FG ("mostly transparent, thin
  lattice at the edges") before regenerating that scene.
