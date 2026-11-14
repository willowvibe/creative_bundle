---
title: గోదావరి
aspect_ratio: "9:16"
voice: elevenlabs:EohubqvAiTiSkWCp1vu7
voice_model: eleven_v4
voice_language: te
voice_pace: 1.1
narration_mode: per_scene
design: night_map
target_seconds: 46
music_mood: calm flowing indian strings, gentle river ambience, hopeful
pronunciations:
  "1,465": వెయ్యి నాలుగు వందల అరవై ఐదు
  "9.5%": తొమ్మిదిన్నర శాతం
---

## Scene 1 — One river
visual: map
country: India
frame: full
headline: గోదావరి
rivers:
  - {name: Godavari, cue: నది, label: గోదావరి, flow: true}
narration: |
  ఒక నది. మూడు రాష్ట్రాలు. 1,465 కిలోమీటర్లు. దక్షిణ గంగ, మన గోదావరి.

## Scene 2 — Source to sea
visual: map
country: India
frame: full
journey:
  icon: dot
  km: true
  follow: true
  total_km: 1465
  stops:
    - {text: త్రయంబకేశ్వర్, lon: 73.53, lat: 19.93}
    - {text: బాసర, lon: 77.95, lat: 18.88, along: Godavari}
    - {text: భద్రాచలం, lon: 80.89, lat: 17.67, along: Godavari}
    - {text: రాజమండ్రి, lon: 81.78, lat: 17.00, along: Godavari}
    - {text: బంగాళాఖాతం, lon: 81.72, lat: 16.33, along: Godavari}
regions:
  - {state: Maharashtra, color: "#6B5A2E", cue: మహారాష్ట్ర}
  - {district: Nashik, cue: నాశిక్, image_prompt: "Trimbakeshwar Shiva temple in the Brahmagiri hills near Nashik, stone gopuram, morning light, documentary photo, no text"}
  - {state: Telangana, color: "#2E6B5A", cue: తెలంగాణ}
  - {district: Nirmal, cue: బాసర, image_prompt: "Gnana Saraswati temple at Basara on the banks of the Godavari river, Telangana, documentary photo, no text"}
  - {district: Bhadradri, cue: భద్రాచలం, image_prompt: "Bhadrachalam Sri Rama temple on the Godavari river at sunrise, Telangana, documentary photo, no text"}
  - {state: Andhra Pradesh, color: "#5A6B2E", cue: ఆంధ్రప్రదేశ్}
  - {district: East Godavari, cue: రాజమండ్రి, image_prompt: "the wide Godavari river at Rajahmundry with the long railway bridge at golden hour, documentary photo, no text"}
bubbles:
  - {text: नमस्कार, at: త్రయంబకేశ్వర్, cue: త్రయంబకేశ్వర్}
  - {text: జై శ్రీరామ్, at: భద్రాచలం, cue: శ్రీరాముడి}
stickers:
  - {icon: temple, at: బాసర, cue: సరస్వతి}
zooms:
  - {to: "district:Nashik", cue: నాశిక్, min_span: 6}
narration: |
  మహారాష్ట్రలో నాశిక్ దగ్గర, త్రయంబకేశ్వర్ కొండల్లో గోదావరి పుడుతుంది. తూర్పుకు
  ప్రవహిస్తూ బాసర దగ్గర తెలంగాణలోకి వస్తుంది. అక్కడ జ్ఞాన సరస్వతి ఆలయం. తర్వాత
  భద్రాచలం, శ్రీరాముడి క్షేత్రం. పాపికొండలు దాటి ఆంధ్రప్రదేశ్‌లో రాజమండ్రి చేరుతుంది.
  కోనసీమ దాటి బంగాళాఖాతంలో కలుస్తుంది.

## Scene 3 — The basin
visual: typographic
layout: big_stat
headline: 9.5%
sub: భారతదేశ నేలలో గోదావరి పరీవాహక ప్రాంతం
narration: |
  దీని పరీవాహక ప్రాంతం దాదాపు మూడు లక్షల చదరపు కిలోమీటర్లు. అంటే భారతదేశ నేలలో
  దాదాపు 9.5%.

## Scene 4 — Close
visual: map
country: India
frame: full
headline: మీ ఊరి నది ఏది?
rivers:
  - {name: Godavari, cue: నది, label: గోదావరి}
narration: |
  కోట్ల మందికి నీళ్లు ఇచ్చే నది ఇది. మీ ఊరి నది ఏది? కామెంట్‌లో రాయండి.
