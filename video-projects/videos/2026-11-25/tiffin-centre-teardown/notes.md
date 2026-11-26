# Notes — DeveeKitchen video 1, tiffin centre teardown

- Video: `టిఫిన్ సెంటర్ నిజంగా ఎంత మిగులుతుంది?` | Tiffin Centre: What Does the Owner Really Keep?
- Spec: `docs/superpowers/specs/2026-11-14-deveekitchen-first-3-videos-design.md` §2.1
- Plan: `docs/superpowers/plans/2026-11-14-deveekitchen-tiffin-teardown.md` (its paths say
  `videos/2026-11-14/`; the work started on 25 Nov, so the folder is `videos/2026-11-25/`).
- Mode: graphics-only (`typographic`, `calc`, `tally`, `ledger`). No footage, no stills, no AI food.
- Numbers: dated public prices; assumptions labelled as estimates on screen. "Typical centre" model,
  not one shop.

## Voice (locked 2026-11-25)

- ElevenLabs **"Harish – Professional"** `ATYMJR0SnaN1mSrsnZEi`, model **`eleven_v4`**, `language_code: te`.
- Replaces the spec's chai-pilot voice (`EohubqvAiTiSkWCp1vu7`). Use it for all three DeveeKitchen videos.
- `eleven_multilingual_v2` (the Python Decoder model) has no Telugu; v4/v3 do.
- Trial: `voice_test/` — same 204-char cold open on v4 (15.5 s) and v3 (16.0 s); owner picked v4.
  Cost: 408 characters.
- Script front matter:

  ```yaml
  voice: elevenlabs:ATYMJR0SnaN1mSrsnZEi
  voice_model: eleven_v4
  voice_language: te
  ```

## Model result changes the claim

The spec's candidate claim ("a full centre leaves the owner almost nothing") does not survive the
arithmetic at default estimates: ~₹3,760/day kept, ~₹1.13 lakh/month, break-even ~152 plates. The
honest story: ₹40 plates become a lakh a month only above ~150 plates/day and only by the owner's
own 12-hour day; price that day at a tiffin master's wage and ~₹84K is left, before capital and risk.
Decide the final wording after the human checks R5 (rent), gas cylinders/month and plates/day.

## Video structure change (owner, 2026-11-25): city comparison at the end

After the Hyderabad month view, a final act runs the same centre in **Chennai and Bengaluru** and
asks where it makes more sense (numbers in research.md, "City comparison").

- New Act 4 (2–3 scenes, before the close): `bar_compare` of kept/month per city; a `ledger` of the
  four inputs that change (gas, rent, staff, plate price); the twist line.
- Twist: costs differ by only ~₹640/day across the three cities; the plate price differs by ₹15 =
  ₹4,500/day. At the same ₹40, Bengaluru keeps the least (wages); it wins only because darshini
  customers pay ~₹55. Chennai edges Hyderabad on wages and gas.
- Close's "three numbers" become: plates/day, plate price the street accepts, owner's own hours.
- Length: still 7–9 min; trim Act 2 cost scenes (merge packaging + utilities + licences) to make room.
- The ₹55 Bengaluru plate and all three rents are estimates and must be labelled so on screen.

## Script review (2026-11-25)

- Owner read the Telugu script (22 scenes, commit c2b3195): no changes needed.
- Still open: owner sign-off on research.md figures (`verified_by` / `verified_on`).

## v5 render (2026-11-25, 22:45): stock re-setup done

- Output: `/mnt/data2/video-editor-data/outputs/tiffin-teardown-1/final.mp4`, 9:20, −14.0 LUFS. The narration
  was reused: ElevenLabs stayed at 4,931 characters.
- Visuals follow the chai-pilot format. 12 footage scenes use 35 shots (stock clips and photos) under the
  cash box and calc cards; 10 chart and map scenes sit between them. 22 shots are stock and 13 are PoYo AI
  stills labelled "AI చిత్రం" (13 AI stills must be disclosed in the description).
- QA: everything passes except duration (560 s against the 480 s target, which the narration fixes).
- Flow alternative for the 13 AI shots: prompts are in `flow/prompts.md`. Generate through Claude in Chrome
  and swap the shots to `{file: flow/sN_M.png, ai: true}` if wanted.
- Older cuts are kept in the outputs folder: `final_graphics_v1`, `final_v3_badcaptions`, `final_v4_labelboxes`.

## 2026-11-26: in-order galla, engagement pass, trims, outside review (v5 → v10)

The general lessons are in `video-editor/docs/EXPLAINER_PLAYBOOK.md`; this is what happened here.

- **Galla order.** The tally moved on the same frame its calc result appeared in every calc scene.
  Now: expression → result → tally (+1.0 s) → ± pill when the count ends. Scene 9's second ₹150 row
  now lands where it is said.
- **Engagement.** Scene 1 hook overlay ("రోజుకి ₹12,000 వస్తుంది…" / "మిగిలేది ఎంత?"), a cost checklist
  under the tally in scenes 4–9, coin on each tally change, swoosh into charts, a hit on big numbers,
  stronger photo motion.
- **Trims** (~1 min, 9:20 → 8:16): methodology note, cycle-cart price, batter-making, the repeated
  gas → menu point, the yearly raise, helper salaries by city, Chennai's breakdown. 483 ElevenLabs
  characters.
- **Outside review** (verdict pasted by the owner). Fixed: donut centre counting ahead of slices,
  ± pill beside a mid-count balance, waterfall and break-even elements shown before they were said,
  x-axis to 330, hourly rates on different bases (master now ₹29,000 ÷ 360 = ₹81, "దాదాపు నాలుగు
  రెట్లు"), map framing and label collisions, 7 off-setting stock shots and a slow 2-shot hook
  (11 Flow stills, 0 credits, see `flow/prompts.md`), "పెద్ద మీట" → "అసలు కీలకం", end card clear for
  end screens. Not changed (claim was wrong on frames): "₹16" typo, "superscript 6", bar
  proportions, the ₹1.07 → ₹1.13 count-up, the tally lingering into the city scenes. 160 ElevenLabs
  characters.
- **Current cut:** `tiffin-centre-teardown.mp4`, 8:21, QA all pass, −14 LUFS; share copy
  `tiffin-centre-teardown_share_720p.mp4` (47 MB). Older cuts kept as `_v1_`, `_v2_galla-early_`,
  `_v3_pre-verdict_`. Not yet watched end to end with sound by the owner.
