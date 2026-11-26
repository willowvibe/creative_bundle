# DeveeKitchen — trend scan and next three videos

**Date:** 26 Nov 2026 · **Data:** YouTube Data API (region IN, published since 15 Aug 2026, top 25 by
views per query, 34 queries, 729 videos) + Google Trends (Telangana, Andhra Pradesh, last 3 months).
Files in this folder:

| File | What |
|---|---|
| `yt_trend_scan.py` | 28-query scan (food + sister niches); `python3 -I yt_trend_scan.py 2026-08-15T00:00:00Z` with `YOUTUBE_API_KEY` from `research-engine/.env` exported. ~2,900 API units |
| `yt_trend_scan_telugu_candidates.py` | 6 Telugu validation queries (factory, district, air fryer, protein, economics, animation) |
| `2026-11-26-yt-scan-food.json`, `2026-11-26-yt-scan-telugu-candidates.json` | Raw results: views, likes, comments, duration, views/day, subs, outlier, niche tags |
| `google_trends_pull.py` | Google Trends pull (`uv run --with pytrends --with "urllib3<2" python -I google_trends_pull.py`); rate-limits fast (HTTP 429) |

**Re-running:** the scripts write `results.json`/`results2.json` next to themselves; rename to a dated
file. Compare next month's scan against these JSONs to see which formats are still rising.

`outlier` = views ÷ channel subscribers. Above ~20× means the video broke out of the channel's own
audience: the format did the work, not the channel.

## 1. What is going viral (food + sister niches, last 3 months)

| # | Format | Best examples (views, outlier) | Telugu proof | Fits our stack? |
|---|---|---|---|---|
| A | **"How it's made in a factory" Shorts** | Tricolor ice candy 63M (632×); eggshell toothpaste 39M; Tiranga barfi 25M (1271×) | Trendz-Telugu: pencil crackers 10.7M (100×), notebooks 8.8M, *పలక బలపాలు* 7.5M; Osm Dhruva meal maker 6.5M; CooQueen Telugu soan papdi 3.3M (462×, 10 days old) | Yes, as graphics + licensed footage. Many top ones are AI-faked factories: avoid |
| B | **2D "Ghibli" village cooking / nostalgia** | Village 90's Cooking 55M (240×); Ghibli tree house 37M; *1990s South Indian village hotel that fed everyone* 8.2M (411×) | Format is language-light; "Telugu story" *woman who cooked 500 kg biryani* 8.6M (522×) | Yes, with drama-studio (Flow stills → Wan i2v → ElevenLabs). Needs a policy call (see §4) |
| C | **Animated body/health food explainer** | BeHealthy *5 foods for your arteries — tiny cleanup crew* 88M (510×); digestive system 60s 1.2M (1025×) | Telugu digestive-system explainers 4–6M (2026-11-14 research) | Yes (graphics); health register risk |
| D | **Food map animation** | *10 famous foods of Maharashtra's districts* 4.5M (285×); *Culinary journey across India* 1.3M; *How North Korea invented fast food* 6.1M (647×) | Not found for AP/TG districts: **open gap** | Yes, best fit: map engine + `india_districts.json` already built |
| E | **Cheap vs expensive taste test** | wittySpace ₹30 vs ₹400 noodles 57M; $3 vs $83 hamburger 14.7M | — | No: needs on-camera tasting |
| F | **Food adulteration exposé** | Mridul Madhok 10.4M; fake paneer 8.9M (25×); fake dry fruits (Tamil) 2.5M (50×) | WISE TV D-Mart adulteration 4.0M | Partly; legal/defamation risk; Telugu search interest ~0 |
| G | **Food business economics** | Money Code *dhaba wala kitna kamata hai* 2.3M (31×); Arpit Sharma unlimited food 813K | Nihar Explains tea stall 264K (17×, 11 Nov); other Telugu economics long-form: 266–25K | Yes (tiffin engine). Proven in Hindi; thin in Telugu |
| H | Mukbang / challenge / street-vendor vlogs | 100 gol gappe 50M; biggest samosa 46M | Hyderabad unlimited combo 7M; ₹220 biryani 5.7M | No: needs filming |

## 2. Google Trends (TG/AP, last 3 months, 0–100 within each group)

- Group 1 (web, TG): **biryani 56**, tiffin 13, pani puri 1, fake paneer 0, food adulteration 0.
  On YouTube in AP: biryani 49, pani puri 6, tiffin 5.
- Group 2 (YouTube, TG): **air fryer 68**, protein 43, ghee 16, millets 3, **cooking oil 0.5**.
- Rising YouTube queries around "food" in TG: *platelets increase food* (dengue season), *classic
  mini food* (miniature cooking), *guess the food*.
- Consequences: the planned **oil video has almost no search demand** as framed; reframe it around air
  fryers (*does an air fryer really use less oil?*) or drop it. Pani puri, also weak on search, is now a
  Short at most.

## 3. Next three videos

### 1 — "Every district's famous food": Andhra + Telangana map (Short + 8-min long)
`ఏ జిల్లాకు ఏ ఫేమస్ ఫుడ్?` — Format D, the cheapest gap with proof (Maharashtra 4.5M at 285×), and
nobody has done AP/TG. Districts fill one by one with a photo and dish name (Atreyapuram
*pootharekulu*, Kakinada *kaja*, Bandar *laddu*, Nellore *chepala pulusu*, Guntur *mirchi*, Hyderabad
*haleem*, Karimnagar...). Comments-bait by construction ("you missed my district"), and it seeds a series
(AP Short, TG Short, then one long video). Food only, so it stays inside DeveeKitchen's subject.
- **Have:** `visual: map` district fills with photos, `night_map`, stickers (`video-editor/docs/MAP_STORIES.md`),
  `india_districts.json`, Telugu captions, voice.
- **Build:** a `district_reveal` sequence (N districts on a timer, auto-zoom per region), a dish-photo
  sourcing step (archive frames, then licensed stock; no AI food), a 9:16 map layout check.

### 2 — "How is it made?" series, ep 1: Tirupati laddu (or Atreyapuram pootharekulu)
`తిరుపతి లడ్డూ ఎలా తయారవుతుంది?` — Format A, the biggest Telugu outlier lane right now (Trendz-Telugu
7–11M per Short, 70–100×; soan papdi 3.3M in 10 days). Our edge over the AI-factory channels: real
numbers (laddus/day, kg of ghee, boondi per batch) as calc cards, the DeveeKitchen teardown DNA in
60 seconds. Short first, then an 8-min long version with the supply chain (ghee, besan, cashew) as a map.
- **Have:** calc/tally/waterfall cards, map routes, stock pipeline, `scene3d-engine` still-move for
  depth moves on real stills.
- **Build:** an isometric **process-flow composition** (station → station with quantities ticking), a
  footage-licence check (TTD/press footage is not ours; use labelled diagrams + licensed stock).
- **Care:** the 2024 laddu-ghee controversy. Report published facts only, no allegation. If that is too
  hot, start the series with pootharekulu or Kakinada kaja (also nostalgic, also handmade).

### 3 — Animated nostalgia story: "the 1990s Godavari village tiffin hotel" (2D, Short + 6-min)
Format B, the largest view pools in the scan (8–55M, 140–520× outliers), and language-light so it
travels beyond Telugu. Narrated story: a village hotel, a ₹2 idli, the owner who fed everyone on
credit. It callbacks to the tiffin teardown (*then vs now: what that ₹2 idli costs today*) and closes on
a real number from `tally.py`.
- **Have:** `drama-studio` (Flow character sheets, Wan 2.2 i2v on Modal, review board), ElevenLabs
  Telugu narration, `vocal-to-song`/ElevenLabs music for a nostalgic bed, `scene3d-engine` camera moves.
- **Build:** a `animated-story` skill (single-narrator short story: style bible → Flow stills in one
  locked 2D style → Wan motion → narration → captions), reusing drama-studio's stage gates without the
  lip-sync stages.
- **Policy decision needed:** the spec says *never AI-generated food or hands*. That rule was written
  against **photo-real** fakes. A clearly stylised 2D animation is a different case, but it is the
  owner's call. Disclose as AI animation either way.

### Runners-up
- *Does an air fryer really use less oil?* (air fryer 68 on TG YouTube; *do air fryers cause cancer?*
  719K in Telugu) — replaces the oil video.
- Fake paneer / adulteration, as a "how to test at home" Short, citing FSSAI only. Hot nationally,
  ~0 Telugu search.
- Biryani economics (*బిర్యానీ సెంటర్ ఎంత సంపాదిస్తుంది?*) — Hindi version 147K; biryani tops TG
  search. Reuses the tiffin engine end to end.

## 4. Skills and engine work to add to creative_repos

| Item | Repo | For | Size |
|---|---|---|---|
| `district_reveal` map sequence + 9:16 map layout | video-editor `packages/maps` | #1 | medium |
| Dish-photo sourcing step (archive frame grab → licensed stock → never AI food), with credits | video-editor | #1, #2 | small |
| Process-flow (isometric stations, ticking quantities) composition | video-editor typographic scenes | #2 | medium |
| `animated-story` skill (style bible, locked 2D style, Wan motion, narration, no lip-sync) | drama-studio | #3 | large |
| `trend-scan` skill: this script on a schedule, outlier ranking, Trends pull, monthly diff | tool-lab → research-engine | all | small |
| DeveeKitchen archive download + index (still not done; blocks real b-roll) | video-projects | all | medium |

## 5. Follow-up (same day)

- Owner picked #1. Video folder: `videos/2026-11-26/district-food-map/` (notes, research.md draft 2).
- `district_reveal`'s blocker is gone: AP's **current 28 districts** are in the map data
  (video-editor `84f90fa`, OpenStreetMap via Nominatim; refresh with
  `geo/overrides/fetch_osm_districts.py`, see `geo/INDIA_ADMIN_SOURCES.md`).
- Research finding: Telangana district food pairings are barely documented online (23 of 33 districts
  without a source). AP has six GI dishes. The owner's knowledge plus Short comments fill the rest.
- Andhra Short built the same day (v9: numbered photo cards, zoom per district, pops, no burned captions,
  50 s, ≈$0.31); `publish.yaml` ready, upload waits for the DeveeKitchen account setup. Recipe and lessons:
  `../video-editor/docs/MAP_SHORTS.md`. Engine work it needed: photo cards, card-band map fit, card pop
  sound, sidecar captions for `ve explain`, AP 28-district map data.

## 6. Caveats

- YouTube search ranks by views among matches; it samples, it is not a census. Outlier ratios favour
  tiny channels whose subscriber counts lag.
- Google Trends rate-limited part of the pull (429); the YouTube group-1 figures for TG are missing.
- Many viral factory and Ghibli Shorts are mass-produced AI channels. YouTube's inauthentic-content
  policy targets that pattern; our versions must carry original research, numbers and narration.
