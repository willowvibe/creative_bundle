# Map listicle Shorts: "famous X of every district" (9:16, `ve explain`)

The recipe for a 45–60 s vertical map Short that counts through items on a map: a big photo card per
item, the map zooming to its district, a pop on each reveal. It comes from the DeveeKitchen Andhra
district-food Short (2026-11-26, nine renders: `../video-projects/videos/2026-11-26/district-food-map/`).
Feature syntax: `MAP_STORIES.md` (journeys, regions, zooms, **Photo cards**). Order of work:
`EXPLAINER_PLAYBOOK.md` (stages 1–12; here stage 1 is a sourced item list, not a number model).

Why this format: the 2026-11-26 trend scan (`../video-projects/docs/research/2026-11-26-deveekitchen-trend-scan.md`)
found district food maps breaking out (Maharashtra districts 4.5M views, 285× the channel's subscribers)
with no Andhra/Telangana version.

## Scene shape

| Scene | Content |
|---|---|
| 1 Teaser (≈5 s) | Whole state, 2–3 quick `cards` (`hold: 1.1`) on early words. No `hook:` panel and no `headline` (both collide with the cards) |
| 2–4 Regions | 3–4 items each: `regions` (district photo fills), `zooms` (one per item, on its cue), `cards` (numbered titles, `badge: GI` where official) |
| 5 Close | Whole state, `headline` = the comment question, label with the count ("10 రుచులు · 6 GI"), teaser for the next part |

```yaml
captions: {mode: sidecar}     # front matter: no burned captions; final.srt for YouTube CC
...
zooms:
  - {to: "district:Guntur", cue: గుంటూరు, min_span: 1.4}
cards:
  - {image: videos/<date>/<slug>/photos/guntur_chilli.jpg, title: "5 · గుంటూరు మిర్చి", badge: GI, cue: గుంటూరు}
regions:
  - {district: Guntur, cue: గుంటూరు, image: videos/<date>/<slug>/photos/guntur_chilli.jpg}
```

- **Numbered card titles** (`1 · …` to `10 · …`) make it a countdown; a pop plays on every card
  (`card_sound: false` turns it off).
- **No bubbles** in card scenes: they repeat the card title and collide with it.
- Card scenes fit the map into the band below the card: down to the caption top (0.72 H) with burned
  captions, or to the Shorts UI line (0.80 H) with `captions: {mode: sidecar}`; `cards_band: {bottom: …}`
  overrides, `cards_fit: frame` restores the full-frame fit. Zoom targets land in the same band.

## Rules learned the hard way

- **Photo paths resolve from the `video-editor` cwd**, not from the script: write
  `videos/<date>/<slug>/photos/x.jpg` (through the `videos` symlink). A bare `photos/x.jpg` fails at render
  time, after the voice is already bought.
- **Crop every photo tight to the food.** A region photo is stretched over the district's bounding box, so
  whatever sits at the photo's edge shows: the mango showed the wooden table, the Bandar laddu the white plate.
- **The last cue of a scene is on screen ~1 s** before the cut. Put the important item earlier in the
  sentence (the mango moved ahead of ragi).
- **Re-render on the same job id** (`-j <same>`) to reuse narration and music: a new job id re-buys both
  (v1 and v2 each paid ~$0.14). Only scenes whose narration changed are re-voiced.
- **Sample frames at the card windows**, not at round seconds: teaser cards live ~1 s, and a contact sheet
  at 1.2/2.6/4.2 s missed all three. Read `data-card-start`/`data-card-exit` from `composition/index.html`.
- Check zoom scenes on frames for the coastline: a projection bug once drew it west of inland Guntur. The map
  tests now pin a coastline vertex to the regions' projection.

## Photos (no AI food)

1. Wikimedia Commons first for specific regional dishes (Commons API search; real photos of Tirupati laddu,
   Bandar laddu, pootharekulu, Kotayya kaja, Banganapalli mango, Guntur chilli yard, Nellore fish curry,
   ragi mudde existed). Mostly **CC BY-SA**: credit author + licence, and say the photos were changed
   ("cropped and masked into district shapes").
2. Pexels/Pixabay for generic items (coffee cherries). Stock search for regional dishes returns wrong food
   (coconut laddu, tea estates for "coffee plantation", chicken for "fish curry"); look at a contact sheet.
3. Owner's own photos or the channel archive for anything low-res (Bandar laddu was 700×525).

Keep `credits.md` (+ `photos/credits.json`) in the video folder; the JPGs are gitignored.

## Cost and time

About $0.15 per Short (ElevenLabs ≈ $0.04 for ~75 Telugu words, PoYo music $0.10; photos free). Render
≈ 3–5 min locally. The Andhra Short cost ≈ $0.31 because of two avoidable re-buys (path error, new job id).

## Packaging

`publish.yaml` next to the video (`social validate` it): `kind: short`, `captions: {te: <cut>.te.srt}`,
numbered item list + GI explanation + photo credits + ODbL map line in the description, `privacy: private`.
Template: `../video-projects/videos/2026-11-26/district-food-map/publish.yaml`. Shorts have no custom
thumbnail upload; pick a card frame in the app.
