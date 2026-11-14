# Video Editor Engine — Verified Improvement Plan (Gemini audit of india-geography)

**Date:** 2026-11-10
**Source:** `Video Editor Bug Audit Report.pdf` (Gemini audit of the 79 s `india-geography-1` render,
11 findings). Baseline for this plan: `main` at `e48aadc`.
**Relation to other docs:** `docs/archive/audits/AUDIT_IMPROVEMENT_PLAN_2026-11-10.md` (written by another tool) answers
the same report with inferred root causes. This plan checks every finding against the code and against
frames of the current render, and replaces those root causes where they differ. Section 4 lists what
changed after the audited render. The repo-split question is answered in
`docs/archive/audits/REPO_SPLIT_PROPOSAL_2026-11-10.md`.
**Numbering:** new issues start at **#338**, continuing `docs/ISSUES_BUGS.md`.

---

## 1. Summary

Of the report's 11 findings, 8 are confirmed in the current render, 2 are partly confirmed, and 1 has
since been fixed. Several of the report's root causes are wrong for this engine. It assumes WhisperX,
`ve align`, a `packages/skills/src/graphics` map module and LLM-written SVG paths; in fact the maps are
drawn from bundled Natural Earth data through one projection (`video_editor_core.geo.View`), and timing
comes from faster-whisper plus `align_words`. The real causes found here are smaller and cheaper to fix.

| # | Finding (report) | Verdict | Real cause | Severity |
|---|---|---|---|---|
| #338 | Glued words ("kilometreslong.", "squarekilometres.") | Confirmed | The active-word pop scales a word to 1.12× and covers the single space beside it | High |
| #339 | Punctuation lost at breaks ("Earth the Sundarbans.") | Confirmed | A standalone "—" has no letters, so the aligner drops it | Medium |
| #340 | Orphan words on line 2 ("into / a", "four / hundred") | Confirmed | Greedy character-count line grouping | High |
| #341 | White captions on cream plates | Confirmed | One caption style for every background | High |
| #342 | Chennai pin offshore | Confirmed, different cause | The script's coordinate (80.3, 13.1) is ~3 km offshore; the projection is right | Critical (wrong place shown) |
| #343 | Unofficial India boundary | Confirmed | Natural Earth default shows de-facto lines; India's official claims need its point-of-view dataset | Critical for Indian audiences |
| #344 | Uncentred zoom, empty canvas | Partly | Zoom boxes are authored by hand, and a box with a different aspect from the frame leaves margin | Medium |
| #345 | Cut while the narrator finishes "…on the east." | Partly | The cut lands 0.10 s after the last word: the next scene's 0.12 s lead-in squeezes the tail | Low |
| #346 | "Kutch", "Sundarbans" mispronounced | Confirmed | English voice with no pronunciation lexicon | Medium |
| #347 | Static typographic cards (6–11 s) | Confirmed | `big_stat` / `definition` animate only their entrance | Medium |
| #348 | Monsoon introduced twice | Confirmed | Script content; no planner check for a repeated concept | Low |
| — | Crude overlays: zig-zag range, labels on rivers | Mostly fixed after the audited render | Callouts and joint layout (`7a6737e`), river casing (`8eb0a9f`); the range pattern style remains (#349) | Medium |

## 2. Evidence

Frames are from `outputs/india-geography-1/final.mp4` as rendered on 2026-11-10 after `7a6737e`.

- **#338.** At 74.0 s the caption reads "kilometres**a** wall, a plain,". `align_words` keeps the words
  separate (`kilometres` 73.94–74.84 s, `a` 74.84 s), and `_word_spans` in `compile_composition.py` joins
  the spans with one space. But `.ve-word` is `display:inline-block` and the active word plays
  `ve_word_pop` (`scale(1.12)` at 45%), so the enlarged word paints over the space. The glue appears only
  while a word is highlighted, which matches both reported timestamps.
- **#339.** The script says "kilometres — a wall" and "Earth — the Sundarbans". `_words_with_spans` in
  `explainer_timing.py` skips any token whose normalised width is 0, so "—" never reaches the captions.
- **#340.** `_group_words_to_lines` in `packages/skills/src/captions/timing.py` adds words until
  `max_chars` is passed, then flushes, which strands one short word ("a", "hundred", "west").
- **#341.** The caption style (white text, gold active word, soft shadow) is the same on dark stills and
  on the cream `#F5F2EB` plates (2,400 km at 0–6 s, Monsoon at 67–73 s, 3.3 million km² at 73–79 s).
- **#342.** Point-in-polygon on the bundled geometry: (80.3, 13.1), the script's Chennai, is outside India
  at both 1:110m and 1:50m; the real city centre (80.27, 13.08) is inside. Kolkata and Mumbai are inside.
- **#343.** Natural Earth publishes point-of-view editions: `ne_10m_admin_0_countries_ind.geojson`
  (13 MB, public domain) draws India's borders as India claims them.
- **#344.** The Ganga zoom box (`77,21,92,31`) and the Ghats boxes are typed into the script. A box whose
  aspect differs from the portrait map panel is fitted by one dimension and padded in the other.
- **#345.** Scene 2 ends at 12.40 s; "east." is spoken 12.02–12.30 s; scene 3's first word starts at
  12.52 s. `derive_scene_timings` puts the cut at the next lead-in (12.52 − 0.12), so the tail is 0.10 s.
- **#347.** The cards hold their entrance pose for the rest of the scene; the composition QA motion check
  passes because drift elsewhere carries the frame difference.

## 3. Plan

### Phase 1: caption correctness (small, high value)

**Status: done 2026-11-10** (#338–#341, see `docs/ISSUES_BUGS.md`). Verified on a free re-render of
`india-geography-1`: all 24 QA checks pass, including the new `caption_tokens` (44 frames) and
`caption_contrast` (worst 4.9:1); map and typographic plates get charcoal captions, stills keep white.

| # | Change | Where | Done when |
|---|---|---|---|
| #338 | Pop the active word without covering spaces: keep `scale()` but add matching horizontal margin to `.ve-word` (or use a colour and weight change instead of scale); never let the box grow past its slot | `compile_composition.py` (`_word_spans`, `.ve-word` CSS) | A rendered frame mid-pop shows a visible gap on both sides; a unit test asserts the margin is at least the scale overflow |
| #339 | Attach punctuation-only tokens ("—", "–", "…") to the previous word's display text | `explainer_timing.py` (`_words_with_spans` / `align_words`) | "kilometres —" and "Earth —" survive into captions; timings unchanged |
| #340 | Balanced line breaking: when a line must split, choose the break that minimises raggedness and never leaves a line of one short word; prefer breaks after commas and before conjunctions; add CSS `text-wrap: balance` to the caption box | `captions/timing.py` (`_group_words_to_lines`), caption CSS | The india captions have no second line under 2 words; existing caption tests pass |
| #341 | Scene-aware caption theme: plates with a light background (`paper` designs, typographic and map scenes) get charcoal text with an amber active word, or a dark pill behind the line; stills keep today's style | caption style resolution in `explainer_pipeline.py` / `captions` | Contrast of caption text against its backdrop ≥ 4.5:1 on every scene |

QA gates added with Phase 1 (in `composition_qa.py` / `explainer_qa.py`):
- `caption_tokens`: every caption word must appear in the script (catches glue and lost words); no
  caption line may be a single word of under 4 letters.
- `caption_contrast`: sample the bottom-third backdrop luminance per scene and check the caption colours
  against it (WCAG ratio ≥ 4.5:1).

### Phase 2: map correctness

**Status: done 2026-11-10** (#342–#344, #349). The india script now names its cities and zooms with
`to:`; a free re-render passes all 25 QA checks including the new `map_markers` gate.

| # | Change | Where | Done when |
|---|---|---|---|
| #342 | A gazetteer: `markers: [{place: "Chennai"}]` resolves coordinates from bundled Natural Earth populated places (1:50m, public domain), so scripts never type coordinates; markers that fall just outside their country (within ~10 km) snap to the nearest point on land; a `map_markers_on_land` QA check fails a marker in the sea | `geo/__init__.py`, `map_scenes.py`, `composition_qa.py` | The india Chennai pin sits on the coast; a sea marker fails QA |
| #343 | Point-of-view borders: bundle a compacted India point-of-view admin-0 layer and add `worldview: ind` (default on for maps whose subject is India, overridable). Needs approval to download `ne_10m_admin_0_countries_ind.geojson` (13 MB) | `geo`, `map_scenes.py`, `scripts/compact_natural_earth.py` | India's outline and neighbours follow India's claims when `worldview: ind` |
| #344 | Feature zooms: `zooms: [{to: "Ganges"}]` (or a region or marker name) computes the box from the feature's bounds, pads it to the panel's aspect and safe margins, and keeps the subject centred | `map_scenes.py` | Zooming to the Ganga or the Ghats needs no typed box and leaves balanced margins |
| #349 | Softer range style: a relief-style option (light hachures along slopes, or a soft shaded band) alongside today's ridge marks; keep ridge marks for small scales | `map_scenes.py` | Side-by-side frames reviewed; the default chosen by eye |

### Phase 3: audio and motion

**Status: done 2026-11-10** (#345–#348). A free re-render passes all 26 QA checks. #346 is built and
tested but not yet heard on a render (needs one paid TTS call).

| # | Change | Where | Done when |
|---|---|---|---|
| #345 | Put cuts in the silence: when the gap between sentences is under ~0.5 s, place the cut at the middle of the gap with a minimum 0.15 s tail; add a `sentence_cuts` QA check (no word interval straddles a cut, nothing ends within 0.1 s before it) | `explainer_timing.py`, `explainer_qa.py` | The india cut after "east." moves to ~12.41 s or later with ≥ 0.15 s tail |
| #346 | Pronunciation lexicon: a per-language spoken-form map (e.g. "Kutch" → "Kuchh", "Sundarbans" → "Shoon-dor-bon") applied to the TTS request only, while captions keep the display text. `ve short` content packs already do this per line (`speak_as:`, see `content_pack.py` and `_with_display` in `short_pipeline.py`); extend the same split to explainer scripts. For ElevenLabs, optionally upload a pronunciation dictionary; route Indian topics to an Indian-English voice (Sarvam `en-IN` or an ElevenLabs Indian voice) when the script asks | `scene_assets.py` / TTS providers, script front matter (`pronunciations:`) | A re-render says "Kutch" correctly and captions still read "Kutch" |
| #347 | Living typographic plates: `big_stat` counts up to its number; `definition` reveals the gloss word by word; a slow drift or parallax on the background rings over the hold. Enforce with a per-plate motion check: the plate's own region must change over its hold | `typographic_scenes.py`, `composition_qa.py` | No plate holds still for more than ~1.5 s |
| #348 | A planner lint that flags a key term introduced in two non-adjacent scenes (advisory), plus the same rule in the topic drafter's prompt | `explainer_topic.py`, `explainer_qa.py` advisories | The india script's monsoon repetition is reported as an advisory |

### Order and cost

Phase 1 first: all four fixes are small, contained, and affect every video, not just maps. Phase 2 needs
one download approval (#343). Phase 3's #346 may add a provider cost per video if an Indian voice is used.
All work follows the usual loop: opencode implements test-first, the power-pack `code-reviewer` reviews,
and changes are checked on real frames with `scripts/snap_scene_plan.py` before committing.

## 4. Fixed after the audited render

The audited render predates `7a6737e`. Since then:
- Label collisions: joint layout across markers, rivers and regions, region labels inside their shape or
  called out with a leader line, and `map_motion` checks every label on screen (`7a6737e`).
- Rivers: real Natural Earth courses with a light core over a dark casing and a flow layer, instead of a
  hand-drawn route (`8eb0a9f`).
- Route draw-on: fixed; HyperFrames only seeks CSS animations on HTML elements (`8eb0a9f`).

## 5. The report's tool suggestions

| Suggestion | Verdict |
|---|---|
| `d3-geo` / `topojson-client` | Not needed: `video_editor_core.geo.View` already projects geometry, pins and zoom boxes through one function in Python. TopoJSON could shrink the bundled data later. |
| `datameet/maps` (India boundaries) | Prefer Natural Earth's India point-of-view edition: public domain and in the same format as the existing data. |
| `mapbox/polylabel`, `d3-labeler` | Already covered: region labels use a pole of inaccessibility, and labels share a joint layout with collision checks. |
| `stable-ts` (`regroup=True`) | Worth a look for #340, but the cheaper path is fixing `_group_words_to_lines` plus CSS `text-wrap: balance` (supported by HyperFrames' Chromium). |
| Knuth-Plass JS libraries (`typeset`, `script-line-break`) | Not needed if `text-wrap: balance` and the Python grouping fix are enough. |
| Sarvam TTS, ElevenLabs Indian voices | Yes for #346, behind a script option. |
