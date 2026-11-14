# JEV (TypeSafe) classifier

JEV is the engine's **per-item classifier** — a calibrated model that answers a
whole *batch* of typed questions about one piece of state in a single
sub-second call. It complements the chat LLMs in
[`MODEL_ROUTING.md`](MODEL_ROUTING.md): use JEV for the many small,
independent judgments (scoring, judging, detecting, classifying) that would
otherwise each cost a whole-prompt LLM round trip.

- Endpoint: `POST https://api.typesafe.ai/v1/systemone`
- Model: `jev-latest` (configurable)
- Key: `TYPESAFE_API_KEY`
- Client: `packages/llm/src/video_editor_llm/classify.py`

## Client API

```python
from video_editor_llm import (
    ClassifyClient, question_noul, question_choice, question_score, run_sync,
)

client = ClassifyClient.from_config()          # from config.yaml, else env
if client.available():                          # False when no key is set
    body = await client.classify(
        {"state": "…"},
        {
            "a": question_noul("Is `segment` low-value?", data={"segment": text},
                               true_label="Low value", false_label="Keep"),
            "b": question_choice("What kind of moment is `excerpt`?", criteria,
                                 data={"excerpt": text}),
            "c": question_score("Hook strength of `title`?", levels, data={"title": t}),
        },
    )
    answers = body["answers"]                   # {"a": {"noul": 0.83}, "b": {...}, "c": {...}}
```

| Helper | Answer field | Range |
|--------|--------------|-------|
| `question_noul(instruction, *, data, true_label, false_label)` | `noul` (prob. the true label holds) | 0..1 |
| `question_choice(instruction, {option: rubric}, *, data)` | `choice` (+ `confidence`) | one of the options |
| `question_score(instruction, [levels…], *, data)` | `score` | 0..len(levels)-1 |

`classify_batch(items, build)` judges a whole list in as few calls as possible.
`run_sync(coro)` runs a coroutine from sync skill code, even inside a running
loop. Reference `data` fields from the instruction in backticks
(`` `segment` ``).

## Where it runs

JEV is used in **11 engine stages**. Every integration follows the same
contract: **optional, batched (one call per stage), and fallback-safe** — when
`TYPESAFE_API_KEY` is unset or the call fails, the stage behaves exactly as
before (no network, byte-identical output).

| Stage | JEV role | Fallback when unavailable |
|-------|----------|---------------------------|
| `verify` | checklist judge (PASS/FAIL/UNSURE per item + overall) | LLM-as-judge (`verify_judge`) |
| `shorts_factory` | moment scorer: hook strength + moment type | `_HeuristicScorer` |
| `rough_cut` | semantic low-value detection (rambling / filler / off-topic) | deterministic detectors only |
| `broll` | moment extraction (does a segment deserve b-roll?) | LLM (`utility`) → heuristic |
| `sfx_placement` | suitability filter over keyword-mapped SFX moments (capped by confidence) | unfiltered keyword moments |
| `qa` | advisory content / duration-match / render-state checks | no extra checks; verdict from deterministic checks only |
| `captions` | caption-quality flags (too-long / profanity / emphasis) → `CaptionManifest.quality_flags` | empty flags; render unchanged |
| `chapters` | topic-boundary detection (precedence LLM → **JEV** → gap) | gap-based segmentation |
| `thumbnail` | title hook scoring (`score_titles`) blended into selection | existing scorer order |
| `scene_planner` | shot-type (`choice`) + needs-motion (`noul`) → scene `props` | props untouched |
| `second_pass` | per-edit critique (improves-pacing + issue type) → `SecondPassManifest.critique` | empty critique; edit/render unchanged |

## Configuration

The `classify` task is routed in `config.yaml` (provider `typesafe`,
model `jev-latest`):

```yaml
llm:
  providers:
    typesafe:
      name: typesafe
      base_url: https://api.typesafe.ai
      api_key_env: TYPESAFE_API_KEY
      default_model: jev-latest
  tasks:
    classify: { task_type: classify, provider: typesafe, model: jev-latest }
```

`ClassifyClient.from_config()` reads the `classify` task's provider/model (or a
provider literally named `typesafe`), falling back to
`TYPESAFE_BASE_URL` / `TYPESAFE_API_KEY` / `TYPESAFE_MODEL` env vars and the
built-in defaults. Rates live in [`models.yaml`](../models.yaml) under
`providers.typesafe`.

## Integration pattern

Copy the shape used by `skills/sfx_placement/src/sfx_placement/sfx_placement.py`
(`_filter_moments_with_jev`) — the smallest example:

1. Add an **optional** `classify_client: ClassifyClient | None = None` parameter.
2. When `None`, probe `ClassifyClient.from_config()` then `ClassifyClient()`; if
   `not client.available()`, return the existing result unchanged.
3. Build the question map in one go and call `classify`/`classify_batch` **once**.
4. Parse defensively; on **any** exception, log a warning and fall back.
5. Close only the client you created (`owns_client`).

Thresholds are per stage and configurable (e.g. `noul >= 0.5`; `chapters` exposes
`jev_boundary_threshold`, `sfx_placement` exposes `jev_min_score` /
`jev_max_placements`).

## Testing

Tests inject a **fake classify client** (no network), following
`tests/test_sfx_placement_jev.py` / `tests/test_rough_cut_jev.py`. Existing JEV
test files: `test_verify_jev.py`, `test_shorts_factory_jev.py`,
`test_rough_cut_jev.py`, `test_broll_jev.py`, `test_sfx_placement_jev.py`,
`test_qa_jev.py`, `test_captions_jev.py`, `test_chapters_jev.py`,
`test_thumbnail_jev.py`, `test_scene_planner_jev.py`,
`test_second_pass_jev.py`.
