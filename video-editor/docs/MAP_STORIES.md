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
  - **735 districts** from geoBoundaries IND ADM2 (2021, ODbL 1.0).
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
  `Hydrabad`, `Bengaluru` → `Bangalore`, `Mysuru`, `Prayagraj`, `Eluru` → West
  Godavari).
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
"District boundaries © geoBoundaries / Pathways Data Pvt. Ltd., ODbL 1.0".

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
- **Overlap.** Placement is manual; nothing nudges overlapping bubbles or
  stickers apart yet.

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
