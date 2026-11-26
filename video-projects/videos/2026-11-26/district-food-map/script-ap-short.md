---
title: ఆంధ్రలో ఏ జిల్లాకు ఏ ఫేమస్ ఫుడ్?
aspect_ratio: "9:16"
voice: elevenlabs:ATYMJR0SnaN1mSrsnZEi
voice_model: eleven_v4
voice_language: te
voice_pace: 1.1
narration_mode: per_scene
captions: {mode: sidecar}
design: night_map
target_seconds: 55
music_mood: bright playful indian folk percussion, light nadaswaram, upbeat, unobtrusive
pronunciations:
  "28": ఇరవై ఎనిమిది
  "GI": జీ ఐ
  "1891": పద్దెనిమిది వందల తొంభై ఒకటి
  "10": పది
  "6": ఆరు
---

<!--
Stage 2 draft (2026-11-26). Dish list: research.md draft 2 ("Recommendation for the Shorts").
Photos: pinned 2026-11-26 (photos/, credits in credits.md).
Wikimedia Commons for Tirupati laddu, Bandar laddu, pootharekulu, Kakinada kaja, Banganapalli
mango (all CC BY-SA, credit each one); free stock for chilli, coffee, fish curry, prawns, ragi.
Never `image_prompt` (no AI food). GI count = 6: Tirupati laddu, Banaganapalle mango, Guntur chilli,
Bandar laddu, Atreyapuram pootharekulu, Araku coffee. Kakinada kaja is NOT GI (applied 2020).
-->

## Scene 1 — Hook: 28 districts
visual: map
country: India
frame: full
bbox: [76.6, 12.5, 84.9, 19.3]
cards:
  - {image: videos/2026-11-26/district-food-map/photos/tirupati_laddu.jpg, title: తిరుపతి లడ్డూ, cue: "28", hold: 1.1}
  - {image: videos/2026-11-26/district-food-map/photos/pootharekulu.jpg, title: పూతరేకులు, cue: రుచి, hold: 1.1}
  - {image: videos/2026-11-26/district-food-map/photos/guntur_chilli.jpg, title: గుంటూరు మిర్చి, cue: చూడండి, hold: 1.1}
regions:
  - {state: Andhra Pradesh, color: "#2E6B5A", label: ఆంధ్రప్రదేశ్, cue: ఆంధ్రప్రదేశ్‌లో}
narration: |
  ఆంధ్రప్రదేశ్‌లో 28 జిల్లాలు, ఒక్కో జిల్లాకు ఒక్కో రుచి. మీ జిల్లా ఉందో చూడండి.

## Scene 2 — South and Rayalaseema
visual: map
country: India
frame: full
bbox: [76.7, 12.6, 80.6, 16.3]
regions:
  - {district: Tirupati, cue: తిరుపతి, image: videos/2026-11-26/district-food-map/photos/tirupati_laddu.jpg}
  - {district: Sri Potti Sri Ramulu Nellore, cue: నెల్లూరు, image: videos/2026-11-26/district-food-map/photos/chepala_pulusu.jpg}
  - {district: Kurnool, color: "#6B5A2E", cue: రాయలసీమలో}
  - {district: YSR Kadapa, color: "#6B5A2E", cue: రాయలసీమలో}
  - {district: Anantapuramu, color: "#6B5A2E", cue: రాయలసీమలో, image: videos/2026-11-26/district-food-map/photos/ragi_sangati.jpg}
  - {district: Nandyal, cue: బనగానపల్లె, image: videos/2026-11-26/district-food-map/photos/banaganapalle_mango.jpg}
zooms:
  - {to: "district:Tirupati", cue: తిరుపతి, min_span: 1.6}
  - {to: "district:Sri Potti Sri Ramulu Nellore", cue: నెల్లూరు, min_span: 1.8}
  - {to: "district:Nandyal", cue: బనగానపల్లె, min_span: 1.8}
  - {to: "district:YSR Kadapa", cue: రాయలసీమలో, min_span: 3.2}
cards:
  - {image: videos/2026-11-26/district-food-map/photos/tirupati_laddu.jpg, title: "1 · తిరుపతి లడ్డూ", badge: GI, cue: తిరుపతి}
  - {image: videos/2026-11-26/district-food-map/photos/chepala_pulusu.jpg, title: "2 · నెల్లూరు చేపల పులుసు", cue: నెల్లూరు}
  - {image: videos/2026-11-26/district-food-map/photos/banaganapalle_mango.jpg, title: "3 · బనగానపల్లె మామిడి", badge: GI, cue: బనగానపల్లె}
  - {image: videos/2026-11-26/district-food-map/photos/ragi_sangati.jpg, title: "4 · రాయలసీమ రాగి సంగటి", cue: రాయలసీమలో}
narration: |
  తిరుపతి అంటే లడ్డూ, GI ట్యాగ్ ఉంది. నెల్లూరు అంటే ఘాటైన చేపల పులుసు.
  బనగానపల్లె మామిడి, ఇదీ GI. రాయలసీమలో రాగి సంగటి.

## Scene 3 — Krishna–Guntur–West Godavari
visual: map
country: India
frame: full
bbox: [79.4, 15.4, 82.1, 17.3]
regions:
  - {district: Guntur, cue: గుంటూరు, image: videos/2026-11-26/district-food-map/photos/guntur_chilli.jpg}
  - {district: Krishna, cue: బందరు, image: videos/2026-11-26/district-food-map/photos/bandar_laddu.jpg}
  - {district: West Godavari, cue: భీమవరం, image: videos/2026-11-26/district-food-map/photos/royyala_vepudu.jpg}
zooms:
  - {to: "district:Guntur", cue: గుంటూరు, min_span: 1.4}
  - {to: "district:Krishna", cue: బందరు, min_span: 1.4}
  - {to: "district:West Godavari", cue: భీమవరం, min_span: 1.4}
cards:
  - {image: videos/2026-11-26/district-food-map/photos/guntur_chilli.jpg, title: "5 · గుంటూరు మిర్చి", badge: GI, cue: గుంటూరు}
  - {image: videos/2026-11-26/district-food-map/photos/bandar_laddu.jpg, title: "6 · బందరు లడ్డూ", badge: GI, cue: బందరు}
  - {image: videos/2026-11-26/district-food-map/photos/royyala_vepudu.jpg, title: "7 · భీమవరం రొయ్యల వేపుడు", cue: భీమవరం}
narration: |
  గుంటూరు అంటే మిర్చి, GI. మచిలీపట్నం అంటే బందరు లడ్డూ, ఇంకో GI.
  భీమవరం వెళ్తే రొయ్యల వేపుడు తినాల్సిందే.

## Scene 4 — Godavari and the Araku hills
visual: map
country: India
frame: full
bbox: [81.3, 16.2, 83.7, 18.7]
regions:
  - {district: Dr. B.R. Ambedkar Konaseema, cue: ఆత్రేయపురం, image: videos/2026-11-26/district-food-map/photos/pootharekulu.jpg}
  - {district: Kakinada, cue: కాకినాడ, image: videos/2026-11-26/district-food-map/photos/kakinada_kaja.jpg}
  - {district: Alluri Sitharama Raju, cue: అరకు, image: videos/2026-11-26/district-food-map/photos/araku_coffee.jpg}
zooms:
  - {to: "district:Dr. B.R. Ambedkar Konaseema", cue: ఆత్రేయపురం, min_span: 1.2}
  - {to: "district:Kakinada", cue: కాకినాడ, min_span: 1.2}
  - {to: "district:Alluri Sitharama Raju", cue: అరకు, min_span: 2.0}
cards:
  - {image: videos/2026-11-26/district-food-map/photos/pootharekulu.jpg, title: "8 · ఆత్రేయపురం పూతరేకులు", badge: GI, cue: ఆత్రేయపురం}
  - {image: videos/2026-11-26/district-food-map/photos/kakinada_kaja.jpg, title: "9 · కాకినాడ కాజా", cue: కాకినాడ}
  - {image: videos/2026-11-26/district-food-map/photos/araku_coffee.jpg, title: "10 · అరకు కాఫీ", badge: GI, cue: అరకు}
narration: |
  ఆత్రేయపురం పూతరేకులు, కాగితం లాంటి తీపి, GI. కాకినాడ కాజా, 1891 నుంచి అదే రుచి.
  అరకు కొండల కాఫీ, దీనికీ GI.

## Scene 5 — Close: your district?
visual: map
country: India
frame: full
bbox: [76.6, 12.5, 84.9, 19.3]
headline: మీ జిల్లా ఫుడ్ ఏది?
regions:
  - {state: Andhra Pradesh, color: "#2E6B5A", label: "10 రుచులు · 6 GI", cue: రుచుల్లో}
narration: |
  10 రుచుల్లో 6 కి GI ట్యాగ్. మీ జిల్లా ఫుడ్ ఏది? కామెంట్‌లో రాయండి. వచ్చే వీడియోలో తెలంగాణ చూద్దాం.
