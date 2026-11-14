---
title: టీ బండి లెక్క
aspect_ratio: "16:9"
voice: elevenlabs:EohubqvAiTiSkWCp1vu7
voice_model: eleven_v4
voice_language: te
voice_pace: 1.1
narration_mode: per_scene
design: cream_editorial
target_seconds: 160
music_mood: warm light indian street morning, soft tabla, unobtrusive
pronunciations:
  "₹15": పదిహేను రూపాయలు
  "₹62": అరవై రెండు రూపాయలు
  "400": నాలుగు వందల
  "12": పన్నెండు
  "60 ml": అరవై మిల్లీలీటర్లు
  "60": అరవై
  "24": ఇరవై నాలుగు
  "3.2": మూడు పాయింట్ రెండు
  "₹45": నలభై ఐదు రూపాయలు
  "₹1,488": వెయ్యి నాలుగు వందల ఎనభై ఎనిమిది రూపాయలు
  "₹180": నూట ఎనభై రూపాయలు
  "₹144": నూట నలభై నాలుగు రూపాయలు
  "₹1,812": వెయ్యి ఎనిమిది వందల పన్నెండు రూపాయలు
  "19": పంతొమ్మిది
  "₹3,081": మూడు వేల ఎనభై ఒక్క రూపాయలు
  "₹616": ఆరు వందల పదహారు రూపాయలు
  "₹2,428": రెండు వేల నాలుగు వందల ఇరవై ఎనిమిది రూపాయలు
  "₹240": రెండు వందల నలభై రూపాయలు
  "₹2,668": రెండు వేల ఆరు వందల అరవై ఎనిమిది రూపాయలు
  "₹6.67": ఆరు రూపాయల అరవై ఏడు పైసలు
  "₹600": ఆరు వందల రూపాయలు
  "₹9,000": తొమ్మిది వేల రూపాయలు
  "₹300": మూడు వందల రూపాయలు
  "₹3,568": మూడు వేల ఐదు వందల అరవై ఎనిమిది రూపాయలు
  "₹6,000": ఆరు వేల రూపాయలు
  "₹2,432": రెండు వేల నాలుగు వందల ముప్పై రెండు రూపాయలు
  "42%": నలభై రెండు శాతం
  "₹2": రెండు రూపాయలు
  "₹1,440": వెయ్యి నాలుగు వందల నలభై రూపాయలు
  "30": ముప్పై
  "₹72,960": డెబ్బై రెండు వేల తొమ్మిది వందల అరవై రూపాయలు
---

## Scene 1 — The question
visual: footage
query: tea stall india street
image: a busy roadside chai stall in Hyderabad at dawn, steam rising from a large kettle, documentary photo, no text, no logos
tally: {label: గల్లా పెట్టె, value: 0}
narration: |
  ఒక కప్పు చాయ్ ₹15. హైదరాబాద్‌లో లీటర్ పాలు ₹62. మరి రోడ్డు పక్క టీ బండి
  ఓనర్‌కి రోజుకి నిజంగా ఎంత మిగులుతుంది? ఈరోజు ఆ గల్లా పెట్టె పక్కనే కూర్చుందాం.

## Scene 2 — Our stall
visual: typographic
layout: big_stat
headline: ₹15
sub: ఒక కప్పు · అంచనా
tally: {label: గల్లా పెట్టె, value: 0}
narration: |
  మన బండి ఒక కాలేజీ దగ్గర ఉంది. రోజుకి 400 కప్పులు అమ్ముడవుతాయి. ఇవి మా
  అంచనాలు. ధరలు మాత్రం నవంబర్ 12 ఈరోజువి.

## Scene 3 — Milk at five
visual: footage
query: milk boiling india
image: milk boiling in a large steel pot on a roadside stall stove at dawn, documentary photo, no text, no logos
tally: {label: గల్లా పెట్టె, value: -1488}
calc:
  - {label: పాలు, expr: "24 L × ₹62", amount: -1488}
narration: |
  తెల్లవారుజాము ఐదు గంటలు. మొదటి పని పాలు. ఒక కప్పుకి 60 ml, అంటే రోజుకి 24
  లీటర్లు. లీటర్ ₹62, అంటే సూర్యుడు రాకముందే ₹1,488 బయటికి.

## Scene 4 — Tea and sugar
visual: footage
query: chai preparation
image: a chai maker adding tea powder and sugar into a boiling kettle, close up, documentary photo, no text, no logos
tally: {label: గల్లా పెట్టె, value: -1812}
calc:
  - {label: టీ పొడి, expr: "1 kg × ₹180", amount: -180}
  - {label: చక్కెర, expr: "3.2 kg × ₹45", amount: -144}
narration: |
  టీ పొడి ఒక కిలో ₹180. చక్కెర 3.2 కిలోలు, కిలో ₹45, అంటే ₹144. గల్లా పెట్టె
  ఇప్పుడు మైనస్ ₹1,812.

## Scene 5 — The cylinder
visual: footage
query: chai boiling stove
image: blue flame of a commercial gas burner under a tea kettle, close up, documentary photo, no text, no logos
tally: {label: గల్లా పెట్టె, value: -2428}
calc:
  - {label: గ్యాస్, expr: "₹3,081 ÷ 5 రోజులు", amount: -616}
narration: |
  కమర్షియల్ గ్యాస్ సిలిండర్ 19 కిలోలు, ఈరోజు ధర ₹3,081. ఐదు రోజులు వస్తుంది
  అనుకుంటే రోజుకి ₹616. గల్లా పెట్టె మైనస్ ₹2,428.

## Scene 6 — One cup
visual: typographic
layout: big_stat
headline: ₹6.67
sub: ఒక కప్పు అసలు ఖర్చు
tally: {label: గల్లా పెట్టె, value: -2668}
calc:
  - {label: పేపర్ కప్పులు, expr: "400 × ₹0.60", amount: -240}
  - {label: ఒక కప్పు ఖర్చు, expr: "₹2,668 ÷ 400", amount: 6.67, in_tally: false}
narration: |
  పేపర్ కప్పులు, ఒక్కోటి 60 పైసలు, రోజుకి ₹240. ఇప్పటిదాకా ఖర్చు ₹2,668. 400
  కప్పులకి పంచితే ఒక కప్పు అసలు ఖర్చు ₹6.67.

## Scene 7 — Wages and rent
visual: footage
query: tea seller india
image: an adult man in his late twenties washing tea glasses at a roadside chai stall, documentary photo, no text, no logos
tally: {label: గల్లా పెట్టె, value: -3568}
calc:
  - {label: హెల్పర్ జీతం, expr: "రోజుకి", amount: -600}
  - {label: అద్దె, expr: "₹9,000 ÷ 30", amount: -300}
narration: |
  హెల్పర్ జీతం రోజుకి ₹600. బండి పెట్టిన చోటుకి అద్దె నెలకి ₹9,000, అంటే రోజుకి
  ₹300. ఒక్క కప్పు అమ్మకముందే గల్లా పెట్టె మైనస్ ₹3,568.

## Scene 8 — The day's count
visual: footage
query: drinking chai street
image: office workers and students drinking chai from paper cups at a street stall, documentary photo, no text, no logos
tally: {label: గల్లా పెట్టె, value: 2432}
calc:
  - {label: అమ్మకం, expr: "400 × ₹15", amount: 6000}
narration: |
  కానీ 400 కప్పులు, ఒక్కొక్కటి ₹15. ₹6,000 లోపలికి. మైనస్ ₹3,568 నుంచి గల్లా
  పెట్టె ఇప్పుడు ప్లస్ ₹2,432.

## Scene 9 — The twist
visual: typographic
layout: big_stat
headline: 42%
sub: ఖర్చులో పాల వాటా
tally: {label: గల్లా పెట్టె, value: 2432}
calc:
  - {label: పాల వాటా, expr: "₹1,488 ÷ ₹3,568", amount: 42, unit: "%", in_tally: false}
  - {label: లీటర్‌కి ₹2 పెరిగితే నెలకి, expr: "24 L × ₹2 × 30", amount: 1440, in_tally: false}
narration: |
  అసలు విషయం ఇక్కడే. మొత్తం ఖర్చులో 42% పాలే. లీటర్ మీద ₹2 పెరిగితే నెలకి
  ₹1,440 పోతుంది. అందుకే పాల ధర పెరిగిన ప్రతిసారి చాయ్ ధర కూడా పెరుగుతుంది.

## Scene 10 — The day's ledger
visual: typographic
layout: ledger
heading: ఒక రోజు లెక్క
rows:
  - {label: పాలు, amount: -1488}
  - {label: టీ పొడి, amount: -180}
  - {label: చక్కెర, amount: -144}
  - {label: గ్యాస్, amount: -616}
  - {label: పేపర్ కప్పులు, amount: -240}
  - {label: హెల్పర్ జీతం, amount: -600}
  - {label: అద్దె, amount: -300}
  - {label: అమ్మకం, amount: 6000}
total: {label: రోజుకి మిగులు, amount: 2432}
tally: {label: గల్లా పెట్టె, value: 2432}
narration: |
  ఒక్కసారి మొత్తం లెక్క చూద్దాం. ఖర్చులన్నీ కలిపి ₹3,568. అమ్మకం ₹6,000.
  రోజుకి మిగిలేది ₹2,432.

## Scene 11 — The month
visual: typographic
layout: big_stat
headline: ₹72,960
sub: నెలకి · అంచనా
tally: {label: గల్లా పెట్టె, value: 2432}
calc:
  - {label: నెలకి, expr: "₹2,432 × 30 రోజులు", amount: 72960, in_tally: false}
narration: |
  నెలకి 30 రోజులు ఇలాగే నడిస్తే ₹72,960. మీ ఊర్లో చాయ్ కప్పు ఎంత? కామెంట్‌లో
  రాయండి.
