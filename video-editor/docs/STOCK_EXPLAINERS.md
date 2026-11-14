# Stock-first explainers and business breakdowns

Additions to `ve explain` scripts, built 2026-11-12 for business-breakdown
videos that show every rupee on screen, as the reference format does (a
Telugu YouTube business breakdown). First production use:
`../video-projects/videos/2026-11-12/chai-stall-breakdown/`.

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
  trademarks. A rejected clip makes the scene try the next library. Only a
  scene left without a clip renders its AI `image`.
- **Cost gate.** The gate prices both the clip ($0) and the fallback still
  (the worst case).
- **Report.** The run writes `footage_report.json`, and the `footage` QA line
  fails on a stock clip that could not be judged.
- **Queries.** Be specific. "gas stove flame" found a home kitchen hob, while
  "chai boiling stove" fits a roadside stall.

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
- **Timing.** Each row appears when its amount is spoken, and holds at least
  1.6 s even if it is the scene's last word.
- **Arithmetic checks.** All of these are checked before anything is paid
  for:
  - every `expr` is evaluated from its own numbers and must equal `amount`, to
    the rupee or to the shown decimals (`%` rows compare against the ratio);
  - a bare figure must equal its amount;
  - a scene's tally must move by exactly the sum of its `in_tally` rows.
- **Quote expressions.** Inside `{...}` a comma ends the value, so
  `₹3,081 ÷ 5` would arrive as `₹3`. A row with stray keys is refused with
  that hint.

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

## Captions

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
