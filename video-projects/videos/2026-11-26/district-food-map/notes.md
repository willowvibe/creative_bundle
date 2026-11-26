# Notes — DeveeKitchen: ఏ జిల్లాకు ఏ ఫేమస్ ఫుడ్? (famous food of every AP/Telangana district)

- Why this video: `docs/research/2026-11-26-deveekitchen-trend-scan.md` §3.1 (format D). Spec Rev 3.
- Reference format: *10 famous foods of Maharashtra's districts* (Adi Facts, 4.5M, 285×) and
  *A culinary journey across India* (Learn & Rise, 1.3M). Nearest in-house build:
  `videos/2026-11-12/godavari-map-reel/` (9:16, `night_map`, district photo fills, spoken cues).
- Order of work: `../video-editor/docs/EXPLAINER_PLAYBOOK.md` (stage 1 research → … → 12 render).
  No number model here; stage 1 is the dish list with a source per pairing.

## Planned cuts

| Cut | Length | Content |
|---|---|---|
| Short A — Andhra | 45–60 s, 9:16 | ~10 dishes, one per region/town, fastest reveal |
| Short B — Telangana | 45–60 s, 9:16 | ~10 dishes |
| Long — both states | 7–9 min, 16:9 | every dish, a 10–20 s beat each (what, where, why there), GI badges, viewer question |

Shorts ship first (3 days apart), long-form after. Each Short ends on "మీ జిల్లా ఫుడ్ ఏది? కామెంట్ చేయండి"
(the comment hook that drove the Maharashtra video).

## What the engine has / lacks (checked 2026-11-26)

- `india_districts.json` (geoBoundaries 2021): **Telangana has all 33 current districts.**
  **Andhra Pradesh has only the 13 pre-2022 districts** (now 26, plus later additions). Drawing old AP
  shapes would look outdated to Telugu viewers.
- Recommended: Telangana = district fills; Andhra = state fill + **town markers with photo stickers**
  (Atreyapuram, Kakinada, Machilipatnam, Tenali…), which is also truer to how these foods are named.
  The alternative is sourcing a 26/28-district AP boundary file (licence check needed).
- Engine work for this video (delegate when we reach stage 4/5): a `district_reveal` sequence (N
  places on a timer, regional auto-zoom) and a 9:16 check of photo stickers at Short density.
- Photos: archive frames first, then licensed stock (`image_query`); **no AI food** (`image_prompt`
  is not used for dishes). Credit line for district shapes: "District boundaries © geoBoundaries /
  Pathways Data Pvt. Ltd., ODbL 1.0".

## AP boundaries — done 2026-11-26 (owner chose "source new AP map")

- video-editor `84f90fa`: AP now has its **current 28 districts** from OpenStreetMap (incl. Polavaram
  and Markapuram, notified 30 Dec 2025), Telugu names in `name_te`. Old names alias to the new ones.
  So both states use district fills; town markers are optional extras (e.g. Atreyapuram inside Konaseema).
- Test still of all 28 with Telugu labels: `assets/ap-28-districts-test-still.png`, made by
  `assets/render_ap_test_still.py` (run from `video-editor`: `uv run python <path> out.html`, then a
  headless Chrome screenshot at 1080×1920). Labels crowd when all 28 show at once; reveal in sequence.
- Refresh the AP data: `video-editor/packages/maps/.../geo/overrides/fetch_osm_districts.py`.
- Credit line now: "District boundaries © geoBoundaries / Pathways Data Pvt. Ltd. and © OpenStreetMap
  contributors, ODbL 1.0".

## Short A (Andhra) — stage 2 draft, 2026-11-26

- `script-ap-short.md`: 5 scenes (hook → south/Rayalaseema → Krishna–Guntur–Bhimavaram → Godavari/Araku
  → close), 75 words ≈ 53 s, 10 dishes, 6 GI badges, every bubble/region cued on its spoken word.
- Dry run (`echo n | uv run ve explain … -j ap-food-short-preflight`): parses, no warnings, **$0.15**
  (ElevenLabs $0.05, music $0.10, Pexels $0). Nothing bought.
- Photos (checked): Commons has real photos for Tirupati laddu (4000×3000), Bandar laddu (700×525, low-res),
  pootharekulu, Kakinada kaja (Kotayya), Banganapalli mango, all **CC BY-SA** → credit each in the
  description. Stock covers chilli, coffee, fish curry, prawns, ragi. Stage 7 swaps the `image_query`
  placeholders for pinned `image:` files.

## Short A render — v3, 2026-11-26

- Cut: `ap-food-short.mp4` (gitignored; job `ap-food-short-v2` under `/mnt/data2/video-editor-data/`),
  50.0 s, 1080×1920, −14.0 LUFS, all QA gates pass.
- Fixes on the way: photo `image:` paths resolve from the `video-editor` cwd, so they are written as
  `videos/2026-11-26/district-food-map/photos/…`; a region photo is stretched over the district's
  bounding box, so **crop each photo tight to the food** (mango showed wood, Bandar laddu showed the white
  plate); the last cue of a scene shows for ~1 s only, so put the important dish earlier (mango moved
  before ragi).
- Spend ≈ $0.30 in total (v1 job bought voice + music before a path error; v2 new job; v3 re-bought scene 2).
  Lesson: re-render on the **same job id** to reuse narration.
- Known weakness: dish photos are small inside district shapes; much of the 9:16 frame is sea.

## Short A render — v5 with photo cards, 2026-11-26 (current cut)

- Owner chose "build photo cards". video-editor `831ceba` adds `cards:` to map scenes
  (docs `MAP_STORIES.md` → Photo cards): a large photo + title + GI badge pops in on each dish's cue.
- `script-ap-short.md` scenes 2–4 now carry 10 cards; the map bubbles were removed (they repeated the
  card titles and collided with them). District photo fills stay.
- `ap-food-short.mp4`: 50.0 s, −14 LUFS, **all QA gates pass** (composition included). No new spend:
  narration and music reused on job `ap-food-short-v2`.
- v6 (owner notes): closing line → "వచ్చే వీడియోలో తెలంగాణ చూద్దాం." (scene 5 re-voiced); card scenes now fit
  the map into the band below the card (0.50–0.72 H on 9:16, video-editor `471b0d5`), so the named
  district is never behind its card. QA all pass, 50.0 s. `ap-food-short-share.mp4` = 6.6 MB copy.
  Trade-off: the map band is small and the area below the captions is empty; a later option is to move
  card-scene captions lower and widen the band.
- v9 (owner: "zoom as needed, remove captions, make it more interesting"), video-editor `c14c5ea`:
  - `captions: {mode: sidecar}`: no burned captions; `ap-food-short.te.srt` for YouTube CC.
  - zoom to each district on its cue (`zooms:` per scene; the fit lands in the band below the card,
    which now runs to 0.80H, the Shorts UI line, since captions are off).
  - cards numbered `1 · … 10 · …` (countdown), pop sound on every card, 3-photo teaser in scene 1
    (hook panel and scene-1 headline removed: they collided with the teaser).
  - fixed on the way: land drawn only in a band rectangle; coastline mis-projected; titles overlapping
    at card switches. QA: 0 FAIL. No new spend (narration/music reused).
- Reuse for Short B (Telangana): same scene shape — `regions` with photo fills + `cards` per dish, no bubbles.

## Open decisions (owner)

1. Dish list sign-off in `research.md` (the owner knows these pairings better than any web list).
