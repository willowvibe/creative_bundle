# Godavari map reel: plan

**Status:** plan only, nothing rendered or paid.
**Format:** 9:16 reel, about 55 s, Telugu narration (ElevenLabs Srirama V,
per-scene timing), `night_map` design, full-frame maps.
**Why this topic:** it uses every map slice (states, district photos, a
journey with a km counter, bubbles, stickers), needs no personal photos, and
rests on well-documented facts.

## Story (one line)
ఒక నది, మూడు రాష్ట్రాలు, 1,465 కిలోమీటర్లు: from Trimbakeshwar in
Maharashtra, through Telangana, to the sea in Andhra Pradesh.

## Scenes

| # | Beat (narration idea) | Map features |
|---|---|---|
| 1 | Hook: "ఒక నది. మూడు రాష్ట్రాలు. 1,465 కిలోమీటర్లు." | India, full frame; the Godavari draws on (river geometry, flow on) |
| 2 | Source at Trimbakeshwar, near Nashik | zoom `district:Nashik`; temple photo cut into the district; Maharashtra fills; bubble "नमस्कार" |
| 3 | Into Telangana at Basara | journey leg 1 along the river, km counter rolls; Telangana fills; temple sticker at Basara |
| 4 | Bhadrachalam | leg 2; zoom `district:Bhadradri`; temple photo in the district; bubble "జై శ్రీరామ్" |
| 5 | Rajahmundry | leg 3; zoom `district:East Godavari`; river and bridge photo in the district; Andhra Pradesh fills |
| 6 | The delta and the sea | the river splits (Gautami, Vasishta); counter lands on 1,465 KM; "దక్షిణ గంగ" bubble |
| 7 | Close | whole route lit; comment prompt ("మీ ఊరి నది ఏది?") |

## Facts to verify before writing the narration
- Length 1,465 km and source at Trimbakeshwar (Nashik district).
- Basin area (about 3.1 lakh km²) and the states it drains.
- Order and river distance of Basara, Bhadrachalam and Rajahmundry along the
  course. Leg km must come from the river geometry, scaled to the 1,465 km
  total, not from road distance.

## Engine work this reel needs (in order)
1. **District aliases.** The source spells Hyderabad as `Hydrabad` and keeps
   `Bangalore`, and newer districts are missing (Eluru sits in West Godavari).
   Add an alias table: Hyderabad, Bengaluru, Mysuru, Thoothukudi and the like.
2. **Legs that follow a river.** `along: Godavari` on a journey stop, so the
   leg traces the river's own centreline between stops and the km counter
   measures that course.
3. **Photos fetched by the pipeline.** Region `photo` and image stickers take
   only local paths today. Add `image_query` (stock photo via Pexels/Pixabay)
   and `image_prompt` (the AI still chain), so `ve explain` fetches them like
   footage.
4. **Sharp close zooms.** District photos go soft when the camera zooms in
   far; render the photo shot at the zoomed size rather than enlarging it.
5. **Telugu cues.** Map cues match spoken words, so every stop, region and
   bubble needs `cue:` in Telugu script ("తిరుపతి", not "Tirupati").
   Authoring only, no code.
6. **Bubbles against captions in 9:16.** 9:16 captions sit at 72% height; keep
   bubbles and stickers out of that band (check during the first render).

## Cost (estimate)
| Item | Cost |
|---|---|
| ElevenLabs narration, about 900 Telugu characters | ~$0.07 |
| Music bed (PoYo) | $0.10 |
| Photos: stock (free) or Flow AI stills (free) | $0 |
| Vision checks | ~$0.01 |
| **Total** | **~$0.18** |

## Credits for the description
District boundaries © geoBoundaries / Pathways Data Pvt. Ltd., ODbL 1.0.
Photo credits as the stock libraries ask.
