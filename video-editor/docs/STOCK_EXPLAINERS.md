# Stock-first explainers and business breakdowns

Additions to `ve explain` scripts, built 2026-11-12 for business-breakdown
videos that show every rupee on screen, as the reference format does (a
Telugu YouTube business breakdown). First production use:
`../video-projects/videos/2026-11-12/chai-stall-breakdown/`.

For the order to build one in, and the rules learned so far, start with
[`EXPLAINER_PLAYBOOK.md`](EXPLAINER_PLAYBOOK.md). This file is the syntax reference.

## Stock footage scenes

```yaml
## Scene 3 — Milk at five
visual: footage
query: milk boiling india          # English stock search
image: milk boiling in a steel pot on a roadside stall, documentary photo
narration: ...
```

- **Order of attempts.** A footage scene asks the stock chain for a clip
  first (`provider: stock`). The chain is `assets.stock_video_chain`, default
  Pexels then Pixabay. Coverr (now 401 without a key) and Mixkit (404) are
  opt-in until fixed.
- **Vision check.** A model (the router's `critique` task, Gemini Flash, about
  $0.0005 a frame) checks each clip's middle frame. Does it show the query?
  Is a brand or logo readable? The stock licences forbid recognisable
  trademarks. A rejected clip makes the scene try again. Libraries take turns
  (Pexels, Pixabay, Pexels #2, ...), with up to 3 picks each
  (`MAX_TRIES_PER_LIBRARY`). Each retry passes `exclude_ids` (clips rejected for
  this shot plus clips already used), so the search moves down its result list
  instead of returning the same top hit. Only a scene left without a clip
  renders its AI `image`.
- **No repeats.** A stock clip or photo (library + id) appears at most once in a
  video. A repeat is rejected without a judge call ("already used in this
  video"). In the tiffin video, one dosa clip had come back for four shots.
- **Judge budget.** The router stops at `max_llm_attempts_per_job` (default 50).
  The footage pass raises it for its own judge to video shots × 3 × libraries +
  10. Before that change, 12 tiffin clips went unjudged once the budget ran out.
- **Thin subjects.** Pexels and Pixabay have little South Indian tiffin footage
  (idli, dosa, wet grinder, sambar). The judge correctly rejected North Indian,
  Thai and Korean stalls, a cement mixer and pottery, so 13 of 35 tiffin shots
  became AI stills. For those subjects, make Flow stills through Claude in
  Chrome (below) or accept labelled AI stills.
- **Cost gate.** The gate prices both the clip ($0) and the fallback still
  (the worst case).
- **Report.** The run writes `footage_report.json`, and the `footage` QA line
  fails on a stock clip that could not be judged.
- **Queries.** Be specific. "gas stove flame" found a home kitchen hob, while
  "chai boiling stove" fits a roadside stall.
  Indian-specific nouns help ("darshini", "tiffin", "wet grinder batter"). Stock
  photos (`photo:` shots) often beat clips for places and objects; LPG cylinders
  and a Charminar road were among the best tiffin picks.

## Several shots in one scene

```yaml
## Scene 2 — The counter
visual: footage
image: a busy south indian tiffin centre counter, documentary photo   # fallback for every shot
shots:
  - {query: idli steaming restaurant india}
  - {photo: south indian tiffin centre counter}
  - {query: dosa tawa cooking, image: a dosa on a hot tawa documentary photo}
narration: ...
```

- **One of three per shot.** (`file:` is covered in the next section.) `query:` is a stock video, searched through the same
  chain and checked by the same vision judge as a single-query scene. `photo:`
  is a stock photo, fetched by the same search that a map's `image_query` uses
  (`provider: stock`, a `.jpg`, free; there is no vision check and no second
  library to try). A shot may add its own `image:` AI fallback; otherwise the
  scene's `image:` is used. A script is refused when a shot has neither, has
  both `query` and `photo`, has a stray key, or when the scene also has a
  top-level `query`. A scene with only `query:` works exactly as before.
- **Commas.** Inside `{...}` a comma ends a value, so keep prompts comma-free
  (or write the shot as a block map).
- **Timing.** The scene's length (its narration) is split evenly across the
  shots, in order, with hard cuts on frame boundaries. Each shot is muted and
  scaled to cover the frame. A clip shorter than its slot loops and a longer
  one is cut off at the slot. A photo drifts slowly (zoom 1.0 to 1.08, a slight
  pan, direction alternating shot by shot). There are no crossfades.
- **Overlays.** The tally chip, calculation card, labels and captions are
  unchanged and span the whole scene.
- **Requests and cost.** Shot `n` of scene `scene_3` is `footage_scene_3_n`; its
  fallback still is `footage_scene_3_n_fallback_image`. The gate prices each
  shot ($0 for stock) and each fallback (the worst case). Only the shots that
  end without stock media render their still. Narration ids and prompts
  (`narration_scene_3`) do not change, so cached voice is reused.
- **Report.** `footage_report.json` has one row per shot, with `shot`,
  `asset_id` and `kind` (`video` or `photo`) added to the usual fields.

## Flow stills through Claude in Chrome

`file:` is a third shot kind, for media you made yourself:

```yaml
shots:
  - {file: flow/scene1_shot0.webp, ai: true}   # a Flow still
  - {file: clips/counter.mp4}                  # your own clip
  - {query: dosa tawa cooking, image: a dosa on a hot tawa documentary photo}
```

- **Rules.** `file:` is a path relative to the script's folder (absolute is
  fine): an image (`png`, `jpg`, `jpeg`, `webp`) or a clip (`mp4`, `mov`,
  `webm`). The script is refused when the file is missing, is a symlink or
  directory, or has another extension. A shot has exactly one of `query`,
  `photo`, `file`; `ai:` (default `false`) is only allowed with `file`. A file
  shot needs no `image:` fallback and has none.
- **How it runs.** The file goes through the `local_media` provider (a copy,
  $0, no image quota). It is never vision-judged, never retried on stock
  libraries, never checked against stock ids and never replaced by an AI
  still; a file that vanishes before the run is a hard error. Images drift like
  stock photos; clips are muted, cover the frame and loop or cut to the slot.
  WebP is fine (Chrome draws it). Replacing the file changes its cache key, so
  the next run picks up the new one.
- **`ai: true`** puts the script's `ai_label` pill on the still, like any AI
  fallback. On a video it is only recorded (`ai: true` in the report): the pill
  is not burned into video.
- **Report.** `footage_report.json` rows for file shots have `source: "file"`,
  `kind` (`image` or `video`), `file` and `ai`. `footage_check` counts them as
  fine ("2 own file").

Free Google Flow stills (Nano Banana, 0 credits) come from Claude driving Flow
in your own Chrome through the Claude in Chrome extension. flow-mcp's hidden
Chrome broke when Flow renamed its agent toggle, and `flow_image` / `agy_image`
are no longer in the automatic image chain (`poyo_ai` → `openrouter` →
`fal_ai`); they stay in the code and can be named with `--image-provider`.

1. Claude opens a Flow project in your Chrome and picks **Image** mode and
   **Nano Banana**; the cost line reads 0 credits.
2. It enters each shot's prompt and generates.
3. It downloads the results as files (not screenshots) into the video folder,
   e.g. `flow/scene1_shot0.webp`.
4. The script points `file:` at them with `ai: true`. Flow images carry a small
   sparkle mark bottom-right; keep `ai: true` so the pill covers it and the
   viewer is told the shot is generated.

## The running tally (a cash box)

```yaml
tally: {label: గల్లా పెట్టె, value: -1488}
```

- **Where it shows.** Any scene may declare a tally. A chip in the top-left
  counts from the last declared value to this one, with Indian grouping and a
  true minus (`−₹1,488`). Negative values are red and positive ones green,
  step by step. The change that moved it (`−₹324`) shows under the total.
- **Checked when read.** A bad tally fails when the script is read.

## Checked calculations

```yaml
calc:
  - {label: పాలు, expr: "24 L × ₹62", amount: -1488}
  - {label: ఒక కప్పు ఖర్చు, expr: "₹2,668 ÷ 400", amount: 6.67, in_tally: false}
```

- **The card.** A top-right card that appears just before its first row and
  grows as rows are added.
- **Timing.** Each row plays in order: its expression appears when its first
  number is spoken (within 6 s before the amount), then `= amount` appears
  when the amount is spoken, and the tally counts 1.0 s after the scene's last
  `in_tally` result, never before it. Rows match amounts by exact digits, each
  searching after the row above, so two equal amounts land where each is said.
  A row holds at least 1.6 s even if it is the scene's last word.
- **Coin.** Each tally change plays a short synthesized coin at the count
  (`coin_in` up, `coin_out` down, about −8 dB under the voice). Front matter
  `tally_sound: false` turns it off.
- **Arithmetic checks.** All of these are checked before anything is paid
  for:
  - every `expr` is evaluated from its own numbers and must equal `amount`, to
    the rupee or to the shown decimals (`%` rows compare against the ratio);
  - a bare figure must equal its amount;
  - a scene's tally must move by exactly the sum of its `in_tally` rows.
- **Quote expressions.** Inside `{...}` a comma ends the value, so
  `₹3,081 ÷ 5` would arrive as `₹3`. A row with stray keys is refused with
  that hint.

## Hook and cost checklist

```yaml
---
progress: ["పదార్థాలు · Ingredients", "గ్యాస్ · LPG", "అద్దె · Rent"]   # front matter
---
## Scene 1
hook: {headline: "రోజుకి ₹12,000 వస్తుంది…", sub: "మిగిలేది ఎంత?", seconds: 3.5}
## Scene 5
progress: "గ్యాస్ · LPG"
```

- **Hook.** A question panel over the scene's first `seconds` (1–8, default 3),
  centred at 38% height, clear of the tally and captions. It adds no narration,
  so it costs nothing and does not renumber scenes.
- **Checklist.** A scene with `progress:` shows the front-matter list under the
  tally: earlier steps ticked, the current one highlighted, later ones grey. The
  label must be one of the list's labels.

## Closing ledger

```yaml
visual: typographic
layout: ledger
heading: ఒక రోజు లెక్క
rows:
  - {label: పాలు, amount: -1488}
  - {label: అమ్మకం, amount: 6000}
total: {label: రోజుకి మిగులు, amount: 4512}
```

Rows appear in turn, then a ruled total that must be the sum of the rows.
It sits high enough to clear the captions.

## Narration for languages Whisper times badly

`narration_mode: per_scene` voices each scene as its own clip, joins the clips
with a 0.45 s pause, and lays out scenes and words from the measured lengths.
No recogniser runs, so timing is exact. Use it for Telugu: Whisper (turbo and
large-v3) skipped whole passages.

- **Sarvam.** It bills each call as at least 1,000 characters, so ten scenes
  cost ten blocks.
- **ElevenLabs.** It bills by the character, so per-scene costs the same as
  one call.
- `voice_pace` reaches Sarvam as `pace` and ElevenLabs as `speed` (0.7–1.2).
- Spell numbers for the voice with `pronunciations` (`"₹1,488": వెయ్యి ...`).
  Captions keep the digits.

## AI image labels

Flow and agy stills carry Google's sparkle. It is an AI-provenance mark, so it
is not erased: an "AI image" pill is drawn over that corner (front-matter
`ai_label` sets the text; `""` turns it off). Say "AI-generated images" in the
description too, because a mask or crop can hide the pill.

Every AI-generated still gets the pill, whichever provider made it (Flow, agy,
PoYo, OpenRouter, fal, Replicate; the set is `LABELLED_PROVIDERS` in
`ai_label.py`), including the fallback stills of `shots:` scenes. Stock clips and
photos are never labelled. (2026-11-25: the set once held only Flow and agy, so
the PoYo stills of the tiffin video shipped unlabelled.)

## Captions

The burn renders the captions on a transparent HyperFrames page with **no base
video**, as an RGBA PNG sequence in ~30 s chunks (Chrome's cost grows with the
clips on the page), and FFmpeg overlays frame N on frame N of the mixed video
(`h264_nvenc` when available, else `libx264`; audio copied). Embedding the 600 MB
video in the page took 112 min and timed out. If the HyperFrames render fails,
Latin captions may fall back to FFmpeg; Telugu and other complex scripts raise
`ComplexScriptRenderError`, because `drawtext` cannot shape them.

Landscape (16:9) captions sit in an 84–93% band, centred near 89%. Portrait
captions stay at 72%, above the Shorts and Reels interface.

## Costs seen

| Video | Spend |
|---|---|
| Chai-stall pilot, Sarvam (three renders) | ≈ $0.42 |
| Chai-stall, ElevenLabs re-voice | ≈ $0.12 |
| Chai-stall, calculations rewrite | ≈ $0.12 |
| Godavari map reel | $0.14 |

A new 90–160 s breakdown costs about $0.23: ElevenLabs $0.12, music $0.10,
stills free via Flow.
