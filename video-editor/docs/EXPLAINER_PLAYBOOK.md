# Explainer playbook: the order we build a `ve explain` video

The process for numbers-led explainers (business breakdowns, cost teardowns, "how much does X really
make"). It comes from the DeveeKitchen tiffin-centre teardown (2026-11-25 → 26: nine renders, one
outside review) and the chai-stall pilot before it. Feature syntax lives in `STOCK_EXPLAINERS.md`,
render speed in `RENDERING.md`; this file is the **order** and the **rules learned the hard way**.

Each stage ends with a check. Do not start the next stage until the check passes: every stage after
the voice is locked is cheap to redo, every stage before it is not.

| # | Stage | Ends with |
|---|---|---|
| 1 | Research and the number model | a model file whose output the script quotes |
| 2 | Story and script | owner-approved narration, digits + pronunciations |
| 3 | On-screen calculations | every `calc`/`tally`/`ledger` row checked against the model |
| 4 | Geography | map scenes with bbox, regions and spoken cues |
| 5 | Screen plan (layout per scene) | one visual per scene, text and chart rules applied |
| 6 | Timing and animation | every element appears on a spoken cue |
| 7 | Stock and stills | approved shots pinned as `file:` |
| 8 | Voice (paid, locks the timing) | per-scene narration cached |
| 9 | Music, SFX | bed chosen, UI sounds on |
| 10 | Captions | digits, no orphan tokens |
| 11 | Hook, end screen, packaging | hook in 3 s, end card clear for end screens |
| 12 | Render, frame review, outside review | QA passes, review fixes applied |

**Changes from the first draft of this order** (owner's list: script → calculations → geography →
text/graphs → animations → stills/stock → audio → captions → rest):

- **Numbers before the script.** The tiffin spec's claim ("the owner keeps almost nothing") did not
  survive the arithmetic (≈ ₹3,760/day, break-even 152 plates). Build the model first, then write
  the story the numbers tell. The calc cards (stage 3) are then a check of the script against the
  model, not new maths.
- **Voice before visuals are final.** Every on-screen cue, caption and chunk boundary is timed from
  the narration's word times. Lock and buy the voice (stage 8) once the script and screen plan are
  final; after that, a text edit re-buys that scene's narration and shifts every later cue.
- **Hook and end screen are planned with the script, not left to "remaining".** They need narration
  and screen space; adding a hook scene later renumbers every scene id and re-buys all narration.
- **Music and SFX stay late**: they never change timing.

---

## 1. Research and the number model

- Put every input in `research.md` with a source, a date and whether it is a **fact** (dated public
  price) or an **estimate** (labelled so on screen). Owner signs off (`verified_by`, `verified_on`).
- Write the model as code in the video folder (`tally.py` with `--table`, `--cities`; `test_tally.py`).
  The script quotes its output; nothing on screen is computed by hand.
- **One base for every rate.** Compare like with like: the review caught the owner's hourly rate on
  30 days × 12 h next to the master's on 25 days × 12 h. Pick the base once in the model.
- Round the way the screen will show it, and let derived figures say "సుమారు / about".

**Check:** `python3 tally.py --table` matches every number you plan to say.

## 2. Story and script (`script.md`)

- **Shape:** cold open with the question (≤ 20 s) → build the total up → take costs off one by one
  → the day's ledger → where the money goes → sensitivity (break-even, slow day, price vs cost) →
  comparison (cities) → the three numbers that decide it → viewer question.
- **Numbers as digits in the narration** (`₹4,218`, `20%`), with the spoken words in
  `pronunciations:`. Captions and calc cues then show and match digits. The voice reads the same
  words, so existing narration is reused.
- **English terms where viewers use them:** `Sales`, `LPG`, `Rent`, `Break-even`, `FSSAI` on labels
  (`అమ్మకం · Sales`).
- **Cut asides before the voice is bought.** Each aside you remove now saves a re-take later. The
  tiffin cut lost a minute of: methodology notes in the first 40 s, a source's side price, how
  batter is made, a point made twice (gas → menu price), a city's cost breakdown before the reveal.
- **Watch idiom.** Literal translations read oddly (`పెద్ద మీట` for "lever" → `అసలు కీలకం`).
- **Never insert a scene after the voice is bought.** Scene ids are positional; a new scene re-buys
  every later scene's narration and re-runs every footage search. Overlay instead (`hook:`).
- `target_seconds` close to the real length (QA allows ±10%).

**Check:** the owner reads the Telugu script; every spoken number appears in `tally.py` output.

## 3. On-screen calculations

- `calc` rows: `expr` is evaluated and must equal `amount`; `in_tally: false` for helper rows; the
  scene's `tally` must move by exactly the in-tally rows. Quote any `expr` with a comma.
- **Order on screen is fixed by the renderer:** expression when its first number is spoken → result
  when the amount is spoken → tally counts 1.0 s later → the ± pill when the count ends. Write the
  narration so the expression's numbers are said before the result (within 6 s).
- Two rows with the same amount are fine: each is matched where it is said, in order.
- Drop the tally chip once the story leaves the single centre (city comparison), so it never
  contradicts other numbers on screen.

**Check:** `ve explain` preflight passes the arithmetic checks; read each calc card aloud with its
narration line.

## 4. Geography

- Only when place matters (cities compared, a route). `layout: map` with `state:` regions, `markers`,
  `bubbles` placed `at:` a place or by `lon/lat`, and a `bbox` around the area that matters.
- The map fits the `bbox` with a 4% margin, centred. Anything outside it is not shown, so include a
  river or neighbour in the bbox if the story needs it.
- Each bubble gets a spoken `cue` so cities appear as they are named (a 38 s map with everything at
  once is a still frame). Colliding labels and bubbles are nudged with leader lines automatically;
  still check the frame.

**Check:** QA `map_markers` and `map_motion` pass; frame shows the region filling the plate.

## 5. Screen plan (layout per scene)

- Alternate **footage** scenes (stock or stills under the tally and calc card) with **chart** scenes
  (`donut`, `waterfall`, `line`, `bar_compare`, `ledger`, `big_stat`, `sequence`, `map`). Never two
  near-identical charts back to back.
- **Charts must match the story's numbers:** the line chart's x-axis ends at our case (300 plates,
  not 330); bars are linear (₹40 vs ₹300 must look 7.5×); the donut centre counts up slice by slice;
  the waterfall builds left to right.
- **No blank openings:** every chart scene shows something by 0.6 s (the renderer enforces 0.4 s).
- Text on screen is short: a label, a number, a unit template (`₹{v}/రోజు`, `₹{v} L`). No sentences.
- Cost scenes carry the `progress:` checklist (front-matter list, scene label) so viewers see how far
  through the costs they are.

**Check:** one sentence per scene saying what is on screen and why.

## 6. Timing and animation

- **Every element appears on a spoken cue.** `cue: false` means "visible from the first frame";
  that is what put "మిగతావి" and "మిగులు" on the waterfall before the bars, and "మన సెంటర్" on the
  break-even line before break-even was explained. Give each its word (`cue: కాదు`, `cue: నష్టం`).
- A cue word must be in that scene's narration (QA `cues` fails otherwise).
- **Review pause frames, not just playback.** Reviewers scrub. Anything mid-tween must still read
  correctly: the tally's ± pill now waits for the count to end for this reason.
- Photos get a visible Ken Burns (1.00 → 1.10, alternating pan); clips keep their own motion.
- Labels that sit close (a mark label and a dot label at the same height) collide; keep labels short
  ("Break-even" with the 152 under it, not "Break-even · 152").

## 7. Stock and stills

- Write every shot for **the setting**: Hyderabad / South India, steel plates, banana leaf, no
  cutlery, no foreign signage. The review flagged stock that passed the vision judge but broke the
  setting: vada with fork and knife, a Gujarati puja shop, a North Indian market arch, western
  meal-prep boxes, bakery bags, a resort ceiling, a Hindi sugarcane cart, a checkered bistro.
- **Hook pacing:** in the first 20 s cut about every 3 s (five shots in scene 1).
- **Free stills: Google Flow through Claude in Chrome** (Image, Nano Banana, 0 credits, 16:9, two
  variants). Procedure and gotchas:
  - one prompt at a time; a second prompt typed while one is running is dropped;
  - prompts end `documentary photo, 16:9, no readable text, no logos` (+ `no cutlery` for food);
  - downloads: Chrome blocks a site's second automatic download until the owner allows "download
    multiple files" for flow.google.com; ask once, then download every tile as a file;
  - pick the better variant at full size (look for garbled text, extra fingers, repeated props);
  - save as `footage/<scene>_<what>.webp`, `file:` with `ai: true` (the AI label covers Flow's
    sparkle), and log prompts in `flow/prompts.md`.
- PoYo AI stills (`ve`'s image chain) are the paid fallback (≈ $0.13 a still).
- **Pin every approved shot as `file:`** before any text edit. The stock cache key includes a
  duration estimated from the narration, so editing a scene's text re-runs its stock search and the
  vision judge may pick something new.

**Check:** a contact sheet of every shot against its narration line.

## 8. Voice (ElevenLabs)

- Voice, model and language in front matter (DeveeKitchen: `ATYMJR0SnaN1mSrsnZEi`, `eleven_v4`,
  `te`), `narration_mode: per_scene` for Telugu.
- The narration cache key is the pronunciation-applied spoken text: changing display digits with the
  same `pronunciations` costs nothing; changing words re-buys only that scene.
- Read `character_count` before and after each run and report the difference (tiffin: 483 chars for
  eight trimmed scenes, 160 for two review fixes).

## 9. Music, SFX

- One licensed or generated music bed (`--music-path`), ducked under the voice.
- UI sounds are automatic: coin on each tally change (in/out pitch), swoosh on each cut from footage
  to a chart, a low hit on each `big_stat` number. `tally_sound: false` turns all of them off.
- Per-scene Freesound SFX (`sfx_provider`) stays off for breakdowns: generic per-scene effects fight
  the UI sounds.

## 10. Captions

- Karaoke captions burn as a transparent overlay (never `drawtext` for Telugu).
- Digits in captions come from the narration (stage 2). A long-spoken number never sits alone on a
  line (the builder merges it).
- Check a frame for formed conjuncts (క్యూ) and contrast (QA `caption_contrast`).

## 11. Hook, end screen, packaging

- **Hook:** `hook: {headline, sub, seconds}` on scene 1, a question with the headline number
  ("రోజుకి ₹12,000 వస్తుంది…" / "మిగిలేది ఎంత?"), gone by 3.5 s. No new scene.
- **End screen:** the last `big_stat` with `align: top` and a viewer question
  ("ఇడ్లీ ప్లేట్ ఎంత?" / "మీ ఊరిలో? కామెంట్‌లో చెప్పండి"); the lower 60% stays clear for YouTube
  end-screen cards for the last 10–15 s.
- Description: sources, "AI చిత్రం" disclosure for generated stills, ODbL line for district maps.
- Share copy for reviewers: 720p H.264 CRF 24, AAC 128k, `+faststart` (≈ 50 MB for 8 min).

## 12. Render, frame review, outside review

- Run detached with the memory cap (`RENDERING.md`). The chunk and caption caches make a one-scene
  fix a ~11 min re-run; the footage judge is most of the rest.
- **Frame review checklist** (grab frames at each change, don't trust playback):
  - calculation → result → tally order in every calc scene;
  - chart elements appear with their words; nothing on screen before it is said;
  - labels don't collide; the map fills its plate;
  - every shot fits the setting; no shot repeats a prop in the next scene;
  - end card clear of the end-screen zone; hook visible by 1 s.
- **Outside review:** send the share copy, then **verify every claim on frames before fixing.** In
  the tiffin review, 5 claims were wrong (a "₹16" typo that read ₹15, a "superscript 6" that
  was the ో vowel sign, bar proportions that were right, a mid-count frame, a tally that already
  left before the city scenes). Fix the rest; say which you did not and why.
- Copy `final.mp4` to `videos/<date>/<slug>/<slug>.mp4`; keep older cuts named for what was wrong.
