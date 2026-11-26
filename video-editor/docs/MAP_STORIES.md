# Map stories: journeys, states, district photos, bubbles, stickers

Additions to `visual: map` scenes (built 2026-11-12) for the animated
map-story look: a dark neon map, routes with a running km counter and a
travelling icon, states and districts that fill or show a photo, speech
bubbles and stickers. Everything here is opt-in; a map scene without these
keys renders exactly as before. Reference reel and frame study:
`../tool-lab/trials/2026-11-12-map-story-reel/`. First production use:
`../video-projects/videos/2026-11-12/godavari-map-reel/`.

## Frame and design

| Key | Effect |
|---|---|
| `frame: full` | The map fills the whole plate and the title floats over it (map-story reels). The default framed plate keeps its zones. |
| `design: night_map` (front matter) | Dark teal sea, mint coastlines with a glow, neon route, red vehicle, dark km boxes. |

`night_map`'s journey colours are design roles that any design may set under
`layers.map`: `route`, `vehicle`, `km_bg`, `km_ink`, `glow`. Empty `route`
means the pin colour; empty `glow` means no halo, so older designs are
unchanged.

## Journeys

```yaml
journey:
  icon: car          # car | plane | dot | none
  km: true           # a boxed counter rides each leg
  curve: 0.15        # how far a plain leg bows (0 = straight)
  follow: true       # the camera frames each leg as it draws
  total_km: 1465     # optional: scale measured legs to a documented total
  stops:
    - {text: Trimbakeshwar, lon: 73.53, lat: 19.93}
    - {text: Basara, lon: 77.95, lat: 18.88, along: Godavari}
    - {text: Chennai, lon: 80.27, lat: 13.08, km: 505, shape: heart, color: "#FF7A6B"}
```

- **Stops become markers.** Every stop after the first is a leg. A leg draws
  when its destination is spoken: the stop's `cue`, which defaults to its
  `text`. Write the `text` (or `cue`) in the narration's script, e.g. Telugu
  "బాసర".
- **Distance.** `km` on a stop is the real road distance. Without it the leg
  is measured as a great circle and rounded to 10 km, which understates roads.
  `total_km` scales the *measured* legs so the journey sums to the documented
  figure; authored `km` legs are kept.
- **Leg shape.**
  - `via: [[lon, lat], ...]` draws a smooth road through those points.
  - `along: <river>` traces that river's own course between the stops.
  - `shape: heart` loops the leg through an upright heart.
  - `color` gives a leg its own line colour; only real colours are accepted.
- **Counter.** One running total is on screen at a time; the last one stays.
- **Icon.** The icon drives each leg along its own path (CSS `offset-path`)
  and is counter-scaled against the zoom, so it keeps its size.
- **Follow.** `follow` adds one `to:` zoom per leg with `pad: 0.4` and
  `min_span: 5` (degrees), so a short leg never zooms in absurdly far.

## States and districts

```yaml
regions:
  - {state: Andhra Pradesh, color: "#6B7A2E", label: ఆంధ్ర ప్రదేశ్}
  - {district: Nashik, image_prompt: "Trimbakeshwar temple, documentary photo"}
  - {district: Coimbatore, image: photos/me.jpg, cue: కోయంబత్తూరు}
zooms:
  - {to: "district:Nashik", cue: నాశిక్, min_span: 6}
```

- **Data.** Bundled India-only geometry, in `packages/maps/.../geo/`:
  - **36 states and union territories** from Natural Earth 10m admin-1
    (public domain). It is current, so Telangana is included.
  - **750 districts** from geoBoundaries IND ADM2 (2021, ODbL 1.0), with
    Andhra Pradesh's 28 current districts from OpenStreetMap (2026-11-26,
    ODbL 1.0) in place of the 13 pre-2022 ones.
  - Sources, the rebuild command and known gaps are in
    `geo/INDIA_ADMIN_SOURCES.md`.
- **Styles.**
  - `fill` (the default for `state:`/`district:`) uses the entry's `color`
    with a glowing border.
  - `photo` cuts an image into the shape. It is chosen automatically when an
    image is given.
- **Photos.** Three ways to supply one:
  - `image:` a local path, embedded as a data URI.
  - `image_prompt:` the AI image chain (Flow and agy first, free).
  - `image_query:` a free stock photo (Pexels, then Pixabay).

  The pipeline fetches the last two like any other asset (priced, cached) and
  drops the file path in before rendering.
- **Names.** Lookups ignore case, spaces and punctuation, suggest close
  spellings on a miss, and know renamed or misspelt districts (`Hyderabad` →
  `Hydrabad`, `Bengaluru` → `Bangalore`, `Mysuru`, `Prayagraj`, `Kadapa` →
  `YSR Kadapa`, `Nellore` → `Sri Potti Sri Ramulu Nellore`, `Anantapur` →
  `Anantapuramu`; `Eluru` is now its own district).
- **Disputed north.** Jammu and Kashmir and Ladakh, and their districts, are
  refused. The source draws them de facto, which would contradict the plate's
  outline of India.
- **Labels.** State and district labels show even when the unit is small on
  the frame. In a scene with photo regions, place labels get a dark backing so
  they stay legible over a bright photo.
- **Zooms.** `to: "state:Kerala"` or `"district:Nashik"` frames the shape even
  when a marker shares the name. Bare names fall back to states and districts
  after physical regions. `min_span` (degrees) sets the tightest window.

**Credit line** (required wherever district shapes appear):
"District boundaries © geoBoundaries / Pathways Data Pvt. Ltd. and
© OpenStreetMap contributors, ODbL 1.0".

## Bubbles and stickers

```yaml
bubbles:
  - {text: నమస్కారం, at: Eluru}              # any script, at a marker or stop
  - {text: Hello, lon: 80.27, lat: 13.08, cue: Chennai}
stickers:
  - {icon: laptop, at: Chennai}               # laptop tea family heart temple phone chat
  - {image_prompt: "a small river boat sticker", lon: 81.8, lat: 17.0, size: 1.4}
```

- **Placement.** Each one is anchored `at` a marker or stop, or at
  `lon`/`lat`. It pops in on its `cue` (else a stagger) and rides the camera on
  an overlay track: it follows zooms but never scales.
- **Text.** Bubble text is HTML-escaped, and Indic scripts shape through the
  browser's font fallback.
- **Stickers.** Built-in stickers are flat SVG icons with a white outline. An
  image sticker comes from `image`, `image_prompt` or `image_query`.
- **Overlap.** Bubbles go through the same collision pass as marker and region
  labels: a bubble that would overlap a label or an earlier bubble is nudged
  (down, right, up, left, in growing steps) and gets a thin leader line back to
  its anchor. Stickers and callout leader lines are not avoided yet.

## Photo cards

```yaml
cards:
  - {image: videos/2026-11-26/district-food-map/photos/tirupati_laddu.jpg,
     title: తిరుపతి లడ్డూ, badge: GI, cue: తిరుపతి}
  - {image: photos/nellore_fish.jpg, title: నెల్లూరు చేపల పులుసు, cue: నెల్లూరు, hold: 2.0}
```

- **What it is.** A large rounded photo card that pops in when its cue word is
  spoken: cover-cropped at about 4:3, with a thin white border, a soft shadow,
  a bold title underneath and an optional `badge` pill on its top-right
  corner. Cards stay above the map, labels and bubbles; on a 9:16 frame the
  photo is about 70% of the width (38% of the height on 16:9), centred and
  held in the upper part of the frame clear of the caption safe area.
- **View fit.** A scene with cards fits its view (the `bbox`, or the auto-framed
  area) into the free band below the card box (photo + title, plus a 2% gap)
  and above the captions' top (`safe_area.caption_top`), so the card never
  covers the framed districts. On 9:16 that band is 49.7% to 72% of the
  height; the fit margin is unchanged and the width stays full. The rest of
  the map still renders around it. Set `cards_fit: frame` to fit the whole
  frame as before. Scenes without cards are unchanged.
- **Band bottom.** With burned captions (the default) the band ends at
  `caption_top` (72% on 9:16). When the script's front matter says
  `captions: {mode: sidecar}` the video burns no captions, so on 9:16 the band
  extends to the Shorts UI safe bottom (80%, `SHORTS_UI_SAFE_BOTTOM` in
  `safe_area.py`); 16:9 is unchanged. The plan marks map scenes with a private
  `_captions_burned: false` prop. Override per scene with
  `cards_band: {bottom: 0.88}` (a fraction of the height, clamped to 0.5-0.95).
- **Timing.** A card enters on its `cue` (default: its `title`), using the same
  matching as bubbles, stickers and regions. It stays until the next card
  enters, or for `hold` seconds, then exits. The last card stays to 0.3 s
  before the scene end. Only one card is visible at a time. Enter is a
  0.35 s fade + scale 0.85→1 with a small overshoot; exit is a 0.25 s fade +
  scale-down.
- **Images.** `image:` is a local path, embedded as a data URI and downscaled
  to ~1200 px. `image_query:` (stock) and `image_prompt:` (AI) are fetched by
  the pipeline like region and sticker photos.
- **Motion.** The enter/exit keyframes are plain CSS on the HTML card, so a
  HyperFrames seek reproduces any frame. Cards are not on the camera track, so
  zooms never scale them.

## Rivers

River names ignore diacritics: the data's "Godävari" answers to "Godavari".
Mahanadi is an alias for "Mahäna Nadï".

## Rendering notes

- **Sharp zooms.** The stage and camera carry no `will-change: transform`.
  With it, Chrome stretched one bitmap through every zoom and close district
  photos went blocky. The cost is about 27% more render time for map scenes.
- **Seekable animation.** Everything animated (leg draw, icon position and
  visibility, glow blur) is a custom property on the HTML wrapper, because
  HyperFrames seeks animations on HTML elements only.
- **Code layout.** New logic lives in `video_editor_maps/journey.py` and
  `stickers.py`. `scenes.py` only hooks them in.
