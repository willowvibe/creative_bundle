# Beats — animation plan per scene

Rule: graphics only. No footage, no stills, no AI food or hands (spec §0). Every number on
screen comes from `tally.py` / research.md. The running cash box (`tally`) rides scenes 1–17.

**Status key:** ✅ exists in `ve explain` today and is in `script.md` · 🔧 was a new engine
feature — all four built 2026-11-25 (video-editor 8be5ea5, c344bbd, 02d9372, d5730e3) and now used in `script.md`.

| # | Scene | Animation | Status |
|---|---|---|---|
| 1 | ఉదయం ఎనిమిది గంటలు | India map → zoom to Telangana, Hyderabad marker, tea sticker pops, "300 ప్లేట్లు" title; cash box at ₹0 | ✅ map |
| 2 | ప్రశ్న | `sequence`: queue → till fills → "what's left?" step by step | ✅ |
| 3 | రోజు గల్లా | `big_stat` ₹12,000 counts up; calc card `300 × ₹40`; cash box ₹0 → ₹12,000 (green) | ✅ |
| 4 | ఒక ప్లేట్‌లో ఏముంది | calc rows rice/dal/oil/chutney appear as spoken; ₹13.02 counts up | ✅ · 🔧 E4 donut of the four parts |
| 5 | పదార్థాలు, వృథాతో | −₹4,218; cash box drops (red) | ✅ · 🔧 E1 icon `wheat` |
| 6 | గ్యాస్ | −₹1,232; calc `12 × ₹3,081` | ✅ · 🔧 E1 icon `flame` |
| 7 | అద్దె | −₹583 | ✅ · 🔧 E1 icon `store` |
| 8 | జీతాలు | −₹1,900; master + helpers rows | ✅ · 🔧 E1 icon `users` |
| 9 | చిన్న ఖర్చులు | `sequence` + three calc rows; cash box lands on ₹3,764 | ✅ · 🔧 E1 icons `package`, `zap`, `file-badge` |
| 10 | ఒక రోజు లెక్క | `ledger`: rows drop in, ruled total ₹3,764 | ✅ |
| 11 | డబ్బు ఎక్కడికి పోతుంది | `bar_compare` of the four big costs | ✅ · 🔧 **E2 waterfall**: ₹12,000 bar steps down cost by cost to ₹3,764 |
| 12 | బ్రేక్-ఈవెన్ | `big_stat` 152 + calc rows | ✅ · 🔧 **E3 line**: kept vs plates, line crosses zero at 152 (marked) |
| 13 | నెమ్మదిగా ఉన్న రోజు | `big_stat` ≈ ₹2,240 | ✅ · 🔧 E3 same chart, a dot slides 300 → 240 |
| 14 | గ్యాస్ పెంపు vs ధర పెంపు | `bar_compare` ₹40 vs ₹300 per day | ✅ |
| 15 | యజమాని పన్నెండు గంటలు | `big_stat` ₹314/hour; calc ₹97 | ✅ · 🔧 E1 icon `clock` |
| 16 | లెక్కలో లేనివి | `sequence`: deposit → equipment → closed days | ✅ |
| 17 | నెల లెక్క | `big_stat` ≈ ₹1.13 లక్షలు counts up | ✅ |
| 18 | మూడు నగరాలు | South-India map: Telangana / Karnataka / Tamil Nadu fill, three city markers, daily-cost bubbles | ✅ map |
| 19 | నెలకి ఎవరికి ఎంత | `bar_compare` 113 / 120 / 235 thousand | ✅ |
| 20 | అసలు తేడా: ప్లేట్ ధర | `big_stat` "₹55 vs ₹40" | ✅ |
| 21 | మూడు సంఖ్యలు | `sequence` plates · price · hours | ✅ |
| 22 | మూలాలు | `big_stat` sources card (date, "typical centre", estimates labelled) | ✅ |

## Engine additions (video-editor) — built 2026-11-25, wired into script.md

Priority order. Each is a typographic layout or prop, renders offline, costs nothing per video,
and is reused by the chilli and oil videos.

| id | Feature | Used in | Size | Why |
|---|---|---|---|---|
| E2 | `layout: waterfall` — start bar, cost steps on cue, end bar | 11 (also 10's twin) | medium | The signature shot of a cost teardown (spec: `cost-teardown` composition) |
| E3 | `layout: line` — x/y line with a marked crossing and a moving dot | 12, 13 | medium | Break-even is a picture, not a number |
| E1 | `icon:` on `big_stat` — Lucide (ISC), reuse EP3's `lucide-static` rasterizer | 5–9, 15 | easy | Each cost reads at a glance |
| E4 | `layout: donut` — parts of a whole, segments on cue | 4 | easy–medium | The plate's ₹13.02 split |

## QA notes

- Map scenes: markers, regions and bubbles carry `cue: false` (the Telugu narration says
  "హైదరాబాద్‌లో", not the bare name), so they reveal on the stagger. Check the reveal timing
  at the first render.
- Map scenes have no district shapes, so no geoBoundaries credit is needed.
- Stickers: built-in `tea` icon only; no `image_prompt` stickers (that would be AI imagery).

## Wired (2026-11-25)

- Scene 4: `donut` (rice ₹3.00 · dal ₹2.40 · oil ₹1.62 · chutney/sambar ₹6.00, centre ₹13.02).
- Scenes 5–8, 15: `icon:` wheat, flame, store, users, clock; scene 14 bars: flame on the cylinder bar.
- Scene 11: `waterfall` ₹12,000 → ingredients, staff, gas, rent, others (₹303) → ₹3,764.
- Scenes 12–13: `line` through whole-rupee points on y = 25.44·x − 3,868 (tally.py), break-even mark at
  152, dot at 300; scene 13 slides the dot to 240 (₹2,238) on the spoken "240".
- Sequence steps in scenes 2, 9, 16, 21 carry explicit cues on words the narration actually says.
- Preview stills (final animation state, not motion): `/tmp/claude-1000/tiffin_preview/s{4,6,11,12,13}.png`.
