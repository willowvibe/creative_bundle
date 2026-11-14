# ShaadiBio Guardrails + Bug Fixes + New Video — Design

**Date:** 2026-07-04
**Status:** Approved
**Scope:** A reusable validator engine feature (the "guardrails"), applied to the `shaadi-bio-builder` commercial; bug fixes (content + blocking-engine); a brand-new corrected commercial built end-to-end through the `ve` pipeline with the new auto-gate.

## Motivation

The first shaadi-bio-builder commercial (`videos/2026-07-04/shaadi-bio-builder/renders/`) shipped with two classes of defect that a pre-build validation gate would have caught:

1. **Hindu/Hindi** — the Frame 6 *language* list is meant to contain `Hindi` (the language); the Frame 5 *community* list correctly contains `Hindu` (the religion). A blunt "ban Hindu" rule would break Frame 5. The validator must be context-sensitive.
2. **Double voiceover at the end** — TTS lines ran ~2× their planned duration (e.g. 02-pain 7.76 s vs 3.8 s planned) while being placed at fixed `adelay` start times, so each overrun bled into the next line and two voices overlapped.

The user wants guardrails that validate **all voice-over script and HyperFrames on-screen text** before any build proceeds, the bugs found and fixed, and a brand-new video produced correctly.

## Decisions (from brainstorming)

- **Validator scope:** reusable engine feature (`packages/validator` + `ve validate` CLI + auto-gate), with a project `validate.yaml` consumed for *this* video.
- **Check categories:** glossary/banned/confusable words; brand/URL/name spelling; VO ↔ on-screen text consistency; profanity/brand-safety.
- **Enforcement:** auto-gate inside `ve run` / `ve graphics` / `ve audio` / `ve enqueue run`; errors block; `--skip-validation` escapes; `--strict` promotes warnings.
- **Architecture:** Hybrid — deterministic rules by default; optional `--llm-judge` that runs only on sentences containing confusable-pair tokens.
- **Bug scope:** content bugs in the commercial **and** engine bugs blocking the end-to-end build.
- **New video source:** same 8-frame script/storyboard with corrections applied (plus a fix for the double-VO overrun).
- **Build path:** full `ve` pipeline end-to-end (enqueue → worker → final.mp4), exercising the new auto-gate.

## Part 1 — Validator engine feature

### Package layout

New `packages/validator` (`video_editor_validator`), mirroring `packages/asset_ingestion`:

```
packages/validator/
  pyproject.toml
  src/video_editor_validator/
    __init__.py
    models.py            # Finding, Severity, ValidationContext, ValidationReport
    rules.py             # Rules dataclasses + load_rules(path) -> Rules
    extract.py           # extract_vo_text(job), extract_onscreen_text(job)
    checkers/
      __init__.py
      base.py            # Checker protocol
      brand_spelling.py
      glossary.py
      confusable_pairs.py
      consistency.py
      profanity.py
      llm_judge.py       # opt-in
    validator.py         # Validator.run(context) -> ValidationReport
    profanity_words.txt  # bundled list
```

### Rules file (`validate.yaml`)

Per-project (lives next to the composition for this video; loaded by `ve validate` and the auto-gate):

```yaml
brand:
  names: ["ShaadiBio"]
  urls: ["shadibio.app"]
glossary:
  allowed_communities: [Hindu, Marathi, Tamil, Gujarati, Muslim, Bengali]
  allowed_languages: [Hindi, Marathi, Tamil, Telugu, Gujarati, Bengali, Urdu, English]
banned_words: []
confusable_pairs: [[Hindu, Hindi], [Muslim, Muslin], [Bengali, Bangali], [ShaadiBio, ShadiBio]]
context_expectations:
  - frame: 5
    field: community_list
    must_contain: [Hindu, Marathi, Tamil, Gujarati, Muslim, Bengali]
  - frame: 6
    field: language_list
    must_contain: [Hindi, Marathi, Tamil, Telugu, Gujarati, Bengali, Urdu, English]
profanity: {enabled: true, extra: []}
llm_judge: {enabled: false, model: gpt-4o-mini}
```

### Data models

```python
class Severity(str, Enum): ERROR = "error"; WARNING = "warning"

@dataclass
class Finding:
    severity: Severity
    code: str               # e.g. "confusable_pair", "brand_misspell", "glossary_unknown"
    message: str
    location: dict          # {file, line, frame, field} subset
    checker: str

@dataclass
class ValidationReport:
    findings: list[Finding]
    @property
    def passed(self) -> bool: ...   # True when zero ERROR findings
```

### Checkers

1. **BrandSpellingChecker** — every `brand.names` / `brand.urls` token must appear verbatim in `vo_text ∪ onscreen_text`. Near-misses within Levenshtein ≤ 2 → ERROR with code `brand_misspell` (e.g. "ShadiBio" instead of "ShaadiBio").
2. **GlossaryChecker** — every community/language token found in the text must be in the matching allowed list. Unknown token → ERROR `glossary_unknown` (typo detection). `banned_words` hits → ERROR `banned_word`.
3. **ConfusablePairChecker** — for each `context_expectation`, the text associated with that frame/field must contain every `must_contain` token and must **not** contain a confusable-pair partner (e.g. Frame 6 must contain `Hindi` and must not contain `Hindu`). This is the deterministic Hindu/Hindi catcher.
4. **ConsistencyChecker** — extract named entities (communities, languages, brand, URL, count words "six"/"eight" vs numerals) from VO and from on-screen text; flag spoken-but-not-on-screen (`vo_not_onscreen`) and on-screen-but-not-spoken (`onscreen_not_vo`) as WARNING, and count mismatches as ERROR.
5. **ProfanityChecker** — bundled `profanity_words.txt` + `profanity.extra`; hits → ERROR `profanity`.
6. **LLMJudgeChecker** (opt-in via `--llm-judge` or `llm_judge.enabled`) — for each sentence containing a confusable-pair token, send sentence + the rule's expected form to the LLM; return a finding if the wrong form was used. **Non-blocking** when no API key / provider unavailable (downgrades to a WARNING and notes the skip in the report). Never runs unless opted in.

### Inputs

- **VO text**: parse `SCRIPT.md` line-by-line (each `## Line N` block's indented quote is one VO line); fall back to `transcript.json` word-level text when no `SCRIPT.md`.
- **On-screen text**: extract from `index.html` + `compositions/frames/*.html` — visible text nodes, `data-text`, `alt`, `aria-label`, and inline SVG `<text>`. Strip tags/scripts/styles. Keep a frame-id attribution per text fragment (from the file name / `data-frame`).
- **Rules**: `validate.yaml` resolved via the same path-resolution convention as `AssetProviderConfig` (explicit `--rules` > job dir > project dir > env override). When no rules file is found, the validator runs only `ProfanityChecker` + a default brand-safety pass and emits a WARNING that no project rules were loaded (the gate still allows the build under default rules; `--strict` flips this to an error).

### CLI

`ve validate <job> [--rules <path>] [--llm-judge] [--strict]` — builds a `ValidationContext`, runs `Validator.run`, prints a human-readable report, writes `validate_report.json` to the job's artifacts dir, exits non-zero on any ERROR finding.

### Auto-gate

Inside `ve run` / `ve graphics` / `ve audio` and the `run` worker handler: before doing generation work, run the validator. On ERROR finding → abort the command/handler with a `RuntimeError` carrying the report path. `--skip-validation` flag escapes the gate. `--strict` promotes WARNING to ERROR. The gate reuses the same rules-resolution as `ve validate`.

### Tests

- Per-checker unit tests (brand, glossary, confusable, consistency, profanity, llm-judge stub).
- `tests/test_cli.py::TestValidateCli` — `ve validate` exit codes + report file.
- `tests/test_pipeline.py::test_pipeline_validate_gate_aborts_on_error` and `..._passes_when_clean` — the auto-gate inside `ve run`.
- **Regression test** `tests/test_validator_regression_shaadi.py` — feeds the *buggy* shaadi script (Hindu in the Frame 6 language list) and asserts an ERROR `confusable_pair` finding is produced; feeds the corrected script and asserts `passed is True`.

## Part 2 — Bug fixes

### Content bugs

- **C1 Hindu/Hindi** — Frame 6 language list must use `Hindi` (language), not `Hindu`. Native-script `हिंदी` is already correct; the romanized pill label must say `Hindi`. Audit every frame for the same class of community/language confusion.
- **C2 Double-VO overrun** — root cause: TTS lines ~2× planned duration + fixed `adelay` start times → overruns overlap the next line. Fix: place VOs on a **sequential timeline** (each VO starts after the previous ends) and `atrim` each VO to its slot, so no VO can overlap the next regardless of generated duration.
- **C3 Script/storyboard consistency** — verify community list (Frame 5) vs language list (Frame 6) are each internally consistent, brand spelling (`ShaadiBio`), URL (`shadibio.app`), and counts ("six templates"/"eight languages") all match across SCRIPT.md, STORYBOARD.md, and the frames.

### Engine bugs blocking the end-to-end build

- **E1 HyperFrames "Composition has zero duration" / `__hf.duration: 0`** on extended timelines with many `<audio>` elements — the failure that forced the ffmpeg-mix bypass in the prior session. Investigate the timeline duration computation in `engine/src/video_editor_core/hyperframes_cli.py`; fix the root cause, or add a documented fallback path so the render step degrades gracefully instead of producing a zero-duration file.
- **E2 VO timeline with overrun protection** — the engine has no concept of a per-line VO timeline that prevents overlap. Add a `voiceover_timeline` helper in `packages/ffmpeg` (sequential placement + per-line `atrim`) used by `ve audio` / `mux_voiceover` / the pipeline, so overlapping VOs cannot be produced. This is the engine-side fix for C2.
- **E3 Further `ve run` pipeline gaps** discovered while building the new video end-to-end — log and fix as found.

## Part 3 — Brand new video

1. Correct SCRIPT.md / STORYBOARD.md (C1, C3) and re-author the HyperFrames composition (`index.html` + 8 frames + assets) with corrected on-screen text and the corrected language pill labels.
2. Author a `validate.yaml` for the project (the rules block shown above).
3. Render the composition via the engine (`ve graphics` / hyperframes render), fixing E1 if it blocks.
4. Generate TTS (ElevenLabs Rachel) on the sequential VO timeline (fixes C2/E2).
5. Run `ve validate` against the job — must pass (proves the gate catches the original Hindu/Hindi bug; this is also exercised by the regression test).
6. Run `ve run` end-to-end with `--qa` and the new auto-gate → produce `final_v2.mp4` under `videos/2026-07-04/shaadi-bio-builder/renders/` (original `video_with_audio.mp4` preserved).
7. Verify the output: VO audible with no overlap, music ducked, captions present, aspect 16:9, brand-safe, validator passed.

## Risks / open items

- **E1 root cause may be in upstream HyperFrames runtime**, not our code. If the fix is upstream-only, the fallback is the documented ffmpeg-mix path (already proven). The new video is still produced; only the "pure `ve run` render" claim is downgraded.
- **ElevenLabs TTS pacing** may still overrun even on a sequential timeline; the per-line `atrim` in E2 is the hard guarantee, so this is contained.
- **LLM judge is opt-in** — a missing API key never blocks a build (downgrades to WARNING).

## Out of scope

- Generalizing the validator to non-HyperFrames composition formats.
- A web UI for findings (CLI report + JSON only).
- Replacing the existing commercial's files in place (originals are preserved).