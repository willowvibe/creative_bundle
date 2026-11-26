# DeveeKitchen — First Three Videos

**Design spec · 14 Nov 2026** (rev 2 — production order changed after review)
**Channel:** [@DeveeKitchen](https://www.youtube.com/@DeveeKitchen) · `UC4-LIpaZur7StCh76r62J5Q`
**Evidence base:** `research-engine/output/2026-11-14/devee-kitchen-revival.md` (Rev 1) and `devee-ai-production-modes.md` (Rev 2)
**Status:** approved design; implementation plan to follow

> **Rev 3 · 26 Nov 2026 — slate after the tiffin video.** A trend scan
> (`docs/research/2026-11-26-deveekitchen-trend-scan.md`: 729 recent videos ranked by views ÷ subscribers,
> plus Google Trends for TG/AP) replaces videos 2 and 3 below:
>
> | Next | Video | Why |
> |---|---|---|
> | **2 (started)** | ఏ జిల్లాకు ఏ ఫేమస్ ఫుడ్? — famous food of every Andhra/Telangana district (map Shorts + long) | District food maps are breaking out (Maharashtra 4.5M, 285×); no AP/TG version exists; map engine has district fills |
> | 3 | "How is it made?" series — ep 1 Tirupati laddu or Atreyapuram pootharekulu | Telugu factory Shorts 7–11M (70–100×) |
> | 4 | 2D animated nostalgia story — 1990s Godavari village tiffin hotel | 8–55M view pools; **needs owner's call** on stylised AI animation vs the no-AI-food rule |
>
> - **Oil (video 3) is shelved:** "cooking oil" scores ~0.5/100 on TG YouTube; reframe as *does an air
>   fryer really use less oil?* (air fryer 68/100) if revived.
> - **Chilli (video 2) is deferred:** biryani (56) and the district map beat it on demand; Guntur mirchi
>   becomes one district in the map instead.
> - Shorts lead: every video ships a 9:16 Short first; Telugu long-form economics is thin (most < 25K).

---

## 0. Decisions locked

| Decision | Value | Why |
|---|---|---|
| Channel | Revive DeveeKitchen in place | Rev 1 §2; new channel costs YPP re-qualification and the archive |
| Subject | Food stays. Format pivots to explainers + food-business teardowns | Rev 1 §3; format pivot, not hard reset |
| Production mode | M1/M7 — code-rendered graphics explainer, human research, AI script/voice/graphics | Rev 2 §3–4 |
| Food footage | Archive + graphics only. **Never AI-generated food or hands** | Rev 1 §5.1 |
| Teardown ownership | DeveeKitchen owns *food*-business teardowns; non-food businesses go to The Real Margin if it launches | Subject split: food vs not-food |
| **Production order** | **Tiffin → Chilli → Oil** | Telugu-fit evidence: tiffin is the one format proven in this language (175K–207K) and the most native subject (Hyderabad walk-past business) |
| **Tiffin figures** | Dated public prices + published anchor; every assumption labelled as an estimate on screen. No field visit | Chosen route; the chai-pilot method, already produced once |
| Length | 7–9 minutes | Mid-roll threshold at 8:00; watch-hour accumulation; enough room for a sourced claim |
| Language | Telugu narration, Telugu on-screen text, English in CC/description only | One pronunciation pass per render; add tracks after the format is proven |
| Research | AI produces a sourced brief; the human verifies load-bearing claims and owns the angle | ~1–1.5 h of human time per video instead of 3–5 h |
| Rollout | Option B — bank two, publish video 1, produce video 3 while it is live | Feedback two weeks earlier without risking the second upload |
| Voice | One consistent ElevenLabs Telugu voice across all three | The chai pilot's voice (`EohubqvAiTiSkWCp1vu7`, `eleven_v4`) unless a clone is recorded first |

**Baseline to judge against:** ~570 views per long-form upload (Rev 1 §1.3). Every target in this spec is expressed as a multiple of that number.

**Open decision (not needed until chilli is planned):** whether the chilli video asks the origin question or is reframed as a price story ("who really sets the price of your chilli"). The Telugu evidence favours the price question; the format evidence favours origin.

---

## 1. Strategy in one paragraph

The channel has 149K subscribers but a ~570-view recent median: two 2018 videos are ~75% of lifetime views, and the subscribers are seven years stale. Age is not the obstacle (catalogue age vs reach correlates at −0.112 across 176 channels); audience mismatch is. The fix is a format pivot inside food — researched explainers and food-business teardowns — produced as code-rendered motion graphics over the channel's own 475-video footage archive. AI writes the script, narrates, and generates the visuals; the human does the research, which is the moat. The archive is not nostalgia; it is the licence-free b-roll that keeps the channel on the safe side of YouTube's "AI narration over generic stock footage" rule.

---

## 2. The slate

| Slot | Video | Family | Telugu evidence | Tests |
|---|---|---|---|---|
| **1** | టిఫిన్ సెంటర్ నిజంగా ఎంత మిగులుతుంది? — Tiffin centre: what does the owner really keep? | Cost teardown | **Proven** — 175K–207K | Numbers and credibility; the one format already working in Telugu |
| **2** | మీ కూరలో మిర్చి ఎక్కడ నుండి వస్తుంది? — Where does the chilli in your curry come from? | Food origin / map | Zero supply; 3.1M English ceiling | The graphics pipeline and an unproven Telugu lane |
| **3** | మీ వంటనూనె ఎప్పుడు పాడైపోతుంది? — When does cooking oil actually go bad? | Food chemistry | Near-zero supply | Diagram-led explainer in a proven kitchen-safety register |

Order rationale: lead with the only format with proven Telugu demand and the most native subject, then test the format the production line is built for, then the kitchen-safety explainer that reuses the archive's frying footage. Rev 2 preferred chilli first on information-per-upload; the Telugu demand evidence outweighed that.

---

### 2.1 Publish #1 — Tiffin centre (cost teardown)

**Title:** `టిఫిన్ సెంటర్ నిజంగా ఎంత మిగులుతుంది? | Tiffin Centre: What Does the Owner Really Keep?`

**Hook:** a packed tiffin centre at 8am, a full plate board — then the owner's actual take-home.

**Candidate claim (the arithmetic decides the final wording):** a tiffin centre that looks full all morning can still leave the owner with almost nothing, because gas, wastage and staff scale with the crowd — and because the owner's own twelve-hour day is never priced.

**Method:** one day at the cash box, every rupee on screen, a checked arithmetic table, one twist, then the month. Every figure is either a dated public price or an assumption labelled as an estimate on screen. The "owner's own hours" twist is a model result and must be labelled as one.

**Research questions** (each resolves to a source, a date and a figure before the script is written):

| # | Question | Source to check |
|---|---|---|
| R1 | Hyderabad commercial LPG 19 kg price, current | OMC/distributor price, dated |
| R2 | Wholesale prices, same date: rice, urad dal, cooking oil | Agmarknet mandi prices; wholesale listings |
| R3 | Rent for a 200–300 sq ft commercial space in mid-density localities | Rental listings, several, dated |
| R4 | Helper/cook wage norms in Hyderabad | Local listings or press, dated |
| R5 | Menu price points for idli/dosa/poori at neighbourhood tiffin centres | Public menus/aggregators or dated press |
| R6 | Batter yield: plates per kg of rice and dal (the key to cost per plate) | Published recipe/industry figure, or a measured batch |
| R7 | Licence and compliance costs: FSSAI registration, GHMC trade licence, GST threshold | Official fee schedules |
| R8 | One published anchor for small food-service margins or a tiffin franchise disclosure | Published study, disclosure, or dated press |
| R9 | Gas consumption per plate volume (cylinders per month) | Published norm or a stated estimate |

**Estimate ledger:** every non-published number (plates/day, wastage %, gas per day, owner's hours) is listed with its basis and shown on screen as an estimate. The video is a *model of a typical centre*, not a report on one shop, and says so.

**Beat outline (~1,150–1,250 Telugu words):**

1. **Cold open** — the 8am queue vs the take-home.
2. **Claim** — busy ≠ profitable; the numbers decide.
3. **Act 1 — the till.** Plates per day × price; the menu board; the daily revenue line.
4. **Act 2 — the cost stack.** Ingredients (rice, dal, oil), gas, rent, staff, packaging, electricity, wastage — each bar entering the waterfall, each with its source or estimate label.
5. **Act 3 — the twist.** Break-even plates per day; what a 20% quieter morning does; the owner's twelve hours priced at market wage, and what is actually left.
6. **Month view** — the owner's monthly keep, before and after own labour is priced.
7. **Close** — the three numbers that decide it (plates/day, wastage, own hours); source card with dates.

**Visuals:** `cost-teardown` composition — waterfall, per-plate margin bar, break-even chart, month view, menu board, cylinder. Archive: tiffin plates, idli steam, queue shots.

**Risk:** without field data the twist depends on modelled assumptions. Mitigation is the estimate ledger and an explicit "typical centre" framing; a wrong rupee presented as observed fact is the one error this video cannot survive.

---

### 2.2 Publish #2 — Chilli origin (map explainer)

**Title:** `మీ కూరలో మిర్చి ఎక్కడ నుండి వస్తుంది? | Where Does the Chilli in Your Curry Come From?`

**Hook (investigator, 0:00–0:25):** hold a packet of chilli powder; cut to what a farmer receives for a kilo of dried chilli. The gap is the video.

**Candidate claim (the one researched idea — confirmed or replaced by R1–R5):** India grows more chilli than any other country and exports most of what it grows, so Guntur's price is set against export demand — which means the farm-gate price and the packet price in your kitchen move on different clocks. If R4 shows exports are a minority of the crop, the claim becomes the domestic story instead; the video does not proceed on an unverified version.

**Research questions:**

| # | Question | Primary source to check |
|---|---|---|
| R1 | India's annual chilli production and its rank among producers | Spices Board India annual report; APEDA |
| R2 | Andhra Pradesh's share of India's chilli area and production | AP Dept of Agriculture statistics |
| R3 | Guntur Mirchi Yard annual arrivals and value | Guntur Agricultural Market Committee reports; AP Agricultural Marketing Dept |
| R4 | India's chilli exports: volume, value, top destinations | Spices Board; APEDA export data |
| R5 | Farm-gate vs wholesale vs retail price per kg, same month | Agmarknet mandi prices vs retail listing, same date |
| R6 | The 2023–25 price movement (glut, stockpile, holding) | Business press with dates; market committee price series |
| R7 | What happens to chilli between yard and packet (drying, cold storage, grinding, blending) | Industry association or processor disclosure; press |
| R8 | One quality/adulteration fact worth a single beat (aflatoxin or non-permitted colour) | FSSAI notices or published sampling studies |

**Beat outline (~1,150 Telugu words):**

1. **Cold open** — packet price vs farm-gate price, both on screen with sources.
2. **Claim + promise** — one sentence; what the viewer will know in eight minutes.
3. **Act 1 — where it grows.** India choropleth; zoom to Andhra Pradesh, then Guntur; why here, with one sourced reason.
4. **Act 2 — the yard.** Arrivals, auction, cold storage; export arcs from Indian ports to the top destinations; the share that leaves the country versus the share that reaches a Telugu kitchen.
5. **Act 3 — two clocks.** Ten-year price line; the recent glut and holding behaviour; the rupee split from farmer to packet.
6. **Close — what the packet tells you.** One or two label facts, then the source card.

**Visuals:** `map-origin` composition (choropleth + zoom + route arcs), one time-series line, one rupee-split diagram, one packet callout. Archive clips optional as punctuation.

**Open decision:** origin question vs price question (see §0). Decide at planning time using the Telugu evidence table in §2.

---

### 2.3 Publish #3 — Cooking oil (chemistry explainer)

**Title:** `మీ వంటనూనె ఎప్పుడు పాడైపోతుంది? | When Does Your Cooking Oil Actually Go Bad?`

**Hook:** oil darkening and smoking in a pan; the question "how many times have you reused this?"

**Candidate claim (confirmed by R1–R3):** oil does not expire on a date — it degrades with every heating cycle, and India already has an official line for when it is spent (FSSAI total polar compounds ≤25%), which almost no home kitchen measures.

**Register constraint:** health-adjacent. The narrator is a curious explainer citing published sources, never a synthetic doctor, and never gives advice. This single rule keeps the lane monetisable (Rev 2 §5.3).

**Research questions:**

| # | Question | Primary source to check |
|---|---|---|
| R1 | FSSAI's used-cooking-oil rule: the 25% TPC threshold, scope, disposal | FSSAI regulation/guidance, current version with date |
| R2 | What heating does chemically: hydrolysis, oxidation, polymerisation | Peer-reviewed frying reviews; food-chemistry text |
| R3 | How TPC rises with repeated cycles — any measured series | Published repeated-frying study |
| R4 | Smoke points of oils a Telugu kitchen uses (groundnut, sunflower, rice bran, mustard, coconut) | Manufacturer/authority data, consistent basis |
| R5 | What common tests actually detect — colour, smell, filtering, topping up | Published study or authority guidance |
| R6 | ICMR-NIN guidance on cooking oils and reheating, quoted precisely | ICMR-NIN dietary guidelines |
| R7 | One India-specific fact on restaurant reuse or UCO collection volumes | FSSAI UCO programme data; dated press |

**Beat outline (~1,100 Telugu words):**

1. **Cold open** — reused oil, smoking; the question.
2. **Claim** — the date is not the measure.
3. **Act 1 — what heat does.** Triglyceride → free fatty acids → aldehydes as a progressive diagram; smoke point falling per cycle.
4. **Act 2 — the official line.** The 25% TPC threshold; the meter; restaurant obligations; why your kitchen never sees a meter.
5. **Act 3 — the myths.** Colour, smell, filtering, topping up, adding salt — each tested against a source.
6. **Close** — what actually slows degradation, attributed; source card.

**Visuals:** `chem-diagram` composition (molecule path), TPC gauge crossing the line, cycle timeline, smoke-point bar chart, myth/fact split. Archive: frying, oil shimmer, pakoda.

---

## 3. Production pipeline

| Stage | Owner | Tool / artefact |
|---|---|---|
| Question + angle | Human | This spec |
| Research brief | AI, human-verified | `research.md` per video — facts, sources, dates, estimate ledger, verify checklist |
| Script | AI draft, human rewrite | `script.md` — chai-pilot front-matter (`voice_language: te`, `per_scene`, pronunciation glossary), 3 acts |
| Visual beats | AI/human | `beats.md` — composition per beat, data on screen, source line |
| Narration | ElevenLabs Telugu, one voice | TTS with pronunciation glossary |
| Render | `ve` / HyperFrames | `cream_editorial` design + one composition per family |
| Captions | Automatic from TTS timestamps | Burned Telugu captions; English CC in description |
| Packaging | AI variants, human choice | `packaging.md` |
| Final judgement | Human | Watch once before publish |

**Reuse:** `cream_editorial`, the chai pilot voice, per-scene narration, pronunciation glossary, caption pipeline, tally discipline.

**Build once:** `cost-teardown` first (video 1), then `map-origin`, then `chem-diagram`. Re-render only changed beats (content-hash).

**Archive dependency:** download and index the channel's own 475 videos into a searchable b-roll library in week 0. All footage is own content; no AI food imagery anywhere.

**Correction to the chai pilot:** it used AI-generated images in some scenes. These three use archive footage or graphics only.

**QA gates before a render is accepted:**
1. Every on-screen number appears in `research.md` with a source and date, or in the estimate ledger.
2. Arithmetic re-checked independently (the tally table).
3. Telugu pronunciation pass on place names and produce.
4. Loudness ≈ −14 LUFS; no clipped audio.
5. Captions match narration; sources legible for ≥3 s.
6. No AI food or hands anywhere in the cut.

---

## 4. Packaging system

- **Titles:** specific food + specific question, never the abstraction. "Food supply chain explained" is the anti-pattern (3–6K views); "The Geography of Fruit" is the pattern (3.1M).
- **Thumbnails:** one template, three variants — rupee stack (video 1), map (2), molecule (3). One high-contrast subject; ≤4 Telugu words.
- **Description:** claim in one line; dated source list; estimate disclosure for video 1; chapters; "reporting, not advice" line; credits for archive and music.
- **Chapters:** cut from the beat map at the act boundaries.
- **Shorts:** one 45–60s cut per video, built from the strongest single beat, published 2–3 days after the long-form.

---

## 5. Rollout (Option B)

Fixed publishing day and time chosen in week 0 from Studio audience data; never moved afterwards.

| Window | Work | Gate |
|---|---|---|
| **Week 0** (14–20 Nov) | Check monetisation in Studio; post one Community update; rewrite About + trailer to "food, explained"; pull CTR/retention/AVD baseline; download and index the archive; scaffold the `cost-teardown` composition | Monetisation status known; baseline recorded |
| **Week 1** (21–27 Nov) | Tiffin research: public prices, published anchor, estimate ledger; human verification; start chilli research | Tiffin `research.md` signed off by human |
| **Week 2** (28 Nov–4 Dec) | Tiffin script, beats, render, QA | Tiffin render passes all six QA gates |
| **Week 3** (5–11 Dec) | **Publish Tiffin**; chilli research continues; Short 1 | 48h read: CTR + 30s retention vs baseline |
| **Week 4** (12–18 Dec) | Publish Chilli (10 days after); oil research; render chilli | Two videos live in the new register |
| **Week 5** (19–25 Dec) | Publish Oil; Shorts 2 and 3 | All three live |
| **Weeks 6–8** | 28-day read on all three; decide double-down lane | Decision against the gates in §6 |

---

## 6. Measurement and kill criteria

| Horizon | Measure | Threshold |
|---|---|---|
| 48 h per video | CTR and 30-second retention vs channel baseline | Leading indicators; views are ignored at this point |
| 28 days | Views as a multiple of the ~570 baseline | 2,000 views = 3.5×, a pass |
| Any single video | ≥5× baseline (≈3,000 views) | The format works — double down on that family |
| After 8–10 videos | Nothing above 3× (≈1,700) | The format is wrong, not the channel — stop and re-plan |

Expectations are written down in advance because the documented failure mode here is expectation collapse, not distribution failure.

---

## 7. Risks and mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| A wrong number on screen | **High** | Every figure sourced and dated; estimate ledger visible on screen; second human pass on the tally |
| Modelled assumptions read as observed fact | **High** | "Typical centre" framing; estimates labelled on screen; no invented shop |
| Ghost subscribers suppress early signals | High | Judge on CTR/retention; expect normalisation after 10–15 videos |
| Health topics drift into the AI-persona ban | High | Curious explainer citing sources; no synthetic doctor; no advice |
| Graphics-led video reads as templated AI content | High | Distinct research and distinct visual per claim; recurring intro is allowed, recurring substance is not |
| Telugu TTS mispronounces places/produce | Medium | Pronunciation glossary; one review pass per render |
| Archive download/index takes longer than a week | Medium | Start week 0; video 1 needs only tiffin b-roll, not the whole library |
| Monetisation has lapsed | Medium | Check in week 0; recovery is reapplication, not appeal |

---

## 8. Out of scope

- Field visits or owner interviews for these three videos (revisit if the model performs).
- Map videos about places rather than food; geography as a subject (its own channel).
- Factory/process videos that need footage we do not own.
- AI as a subject, except "AI in the kitchen" as a later series.
- Hindi and English audio tracks (until the format is proven).
- Any avatar or synthetic presenter.
- Any AI-generated food, hands, or realistic kitchen scene.
- Launch or scheduling of The Real Margin.
- Deleting, unlisting or re-titling the existing 475-video catalogue.

---

## 9. Open data pulls (week 0, must resolve)

1. Is monetisation still enabled on the channel?
2. Channel CTR, average view duration and 30-second retention baseline from Studio.
3. Tiffin public prices: current LPG, rice, dal, oil, rents, wages — all with the same date.
4. Archive download size and where it lives on disk.
5. Fixed publishing day/time, from Studio audience data.

Any item that cannot be resolved changes the plan before implementation, not during it.

---

## 10. Definition of done, per video

- [ ] `research.md` complete, every on-screen number sourced and dated or in the estimate ledger, human verify checklist signed off
- [ ] `script.md` approved; ~1,050–1,250 Telugu words; pronunciation glossary complete
- [ ] `beats.md` complete; each beat names its composition and its on-screen source/estimate line
- [ ] Render passes all six QA gates
- [ ] `packaging.md`: title, three thumbnail variants, description with dated sources and estimate disclosure, chapters, Short plan
- [ ] Human has watched the final cut once end to end
