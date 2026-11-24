# DeveeKitchen Video 1 — Tiffin Centre Teardown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce and publish an 8-minute Telugu motion-graphics cost teardown of a Hyderabad tiffin centre, from dated public prices, with every rupee on screen traceable to a source or a labelled estimate.

**Architecture:** A research file (`research.md`) and a reproducible model (`tally.py`) produce every on-screen number; a `ve explain` script (`script.md`) renders typographic, `calc`, `tally` and `ledger` scenes — no footage, no stills, no AI food imagery — through the existing `cream_editorial` design with ElevenLabs Telugu narration.

**Tech Stack:** `ve explain` (video-editor CLI), ElevenLabs TTS (`eleven_v4`, te), Python 3 stdlib (`dataclasses`, `unittest`), ffmpeg via `ve review`.

## Global Constraints

- Aspect ratio **16:9**; `design: cream_editorial`; `target_seconds: 480`; finished length **7–9 minutes**.
- Narration **Telugu only**; voice `elevenlabs:EohubqvAiTiSkWCp1vu7`, `voice_model: eleven_v4`, `voice_language: te`, `voice_pace: 1.1`; `narration_mode: per_scene`.
- Narration rate: ElevenLabs Telugu at `voice_pace: 1.1` measures **~2.5 words/second** (`india-geography-1`: 211 words → 83.2 s narration audio). 480 s needs **~1,200 words**.
- **No `visual: footage`, no `visual: still`, no `visual: map`** in this video. Graphics only: `typographic` (`big_stat`, `bar_compare`, `sequence`, `ledger`) plus `tally` and `calc`.
- Every number on screen must appear in `research.md` with a source and a date, or in the estimate ledger with `basis: estimate`.
- The video is a **model of a typical centre**, not a report on one shop. Say so once, on screen, and in the description.
- Tone: reporting, never advice. No "you should", no health claims.
- Loudness target **−14 LUFS**.
- Judgement baseline: **~570 views** per long-form upload. 2,000 views is 3.5×.
- Commit in `video-projects` after every task.

## File Structure

All under `/mnt/data2/git_repos/creative_repos/video-projects/videos/2026-11-14/tiffin-centre-teardown/`:

| File | Responsibility |
|---|---|
| `research.md` | R1–R9 source rows, estimate ledger, on-screen figures table, channel baseline, verification sign-off |
| `check_research.py` | Fails until every figure row has a value, source, date and basis |
| `tally.py` | The model: every rupee, break-even and the owner-wage twist |
| `test_tally.py` | Invariant tests for the model, stdlib `unittest` |
| `check_script.py` | Parses `script.md` via the engine and checks length and scene count |
| `script.md` | The `ve explain` script |
| `beats.md` | Scene → layout → figure → cue mapping, and the QA checklist |
| `packaging.md` | Title, thumbnail variants, description with sources and estimate disclosure, chapters, Short plan |
| `notes.md` | Decisions and costs as they happen |

---

### Task 1: Project skeleton, research contract, week-0 user checks

**Files:**
- Create: `.../tiffin-centre-teardown/research.md`
- Create: `.../tiffin-centre-teardown/check_research.py`
- Create: `.../tiffin-centre-teardown/notes.md`

**Interfaces:**
- Produces: `research.md` with a `## Figures` markdown table whose columns are exactly `id | label | on-screen | value | unit | source | source_date | basis`, and ids `R1`–`R9`.
- Produces: `check_research.py` exit code 0 only when every row is complete; exit 1 with a list of missing cells.

- [ ] **Step 1: Create the folder and the research contract script**

```python
# check_research.py
"""Fail until every figure row in research.md has a value, source, date and basis."""
from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED_IDS = [f"R{i}" for i in range(1, 10)]
COLUMNS = ["id", "label", "on-screen", "value", "unit", "source", "source_date", "basis"]
VALID_BASIS = {"published", "observed", "estimate"}


def parse_rows(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    in_table = False
    for line in text.splitlines():
        if line.strip().startswith("| id |"):
            in_table = True
            continue
        if in_table:
            if not line.strip().startswith("|"):
                break
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) != len(COLUMNS) or set(cells[0]) <= {"-", " "}:
                continue
            rows.append(dict(zip(COLUMNS, cells)))
    return rows


def main(path: Path) -> int:
    rows = parse_rows(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    seen = {r["id"] for r in rows}
    for rid in REQUIRED_IDS:
        if rid not in seen:
            problems.append(f"{rid}: missing row")
    for row in rows:
        for col in ("label", "on-screen", "value", "source", "source_date", "basis"):
            if not row.get(col):
                problems.append(f"{row['id']}: empty {col}")
        if row.get("basis") and row["basis"] not in VALID_BASIS:
            problems.append(f"{row['id']}: basis must be one of {sorted(VALID_BASIS)}")
        if row.get("source_date") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["source_date"]):
            problems.append(f"{row['id']}: source_date must be YYYY-MM-DD")
    if problems:
        print("\n".join(problems))
        return 1
    print(f"ok: {len(rows)} figures complete")
    return 0


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name("research.md"))
    raise SystemExit(main(target))
```

- [ ] **Step 2: Create `research.md` with the contract and empty rows**

```markdown
# Tiffin centre teardown — research

**Question:** what does a Hyderabad tiffin centre owner actually keep?
**Method:** model of a typical centre from dated public prices. Estimates are labelled.

## Channel baseline (week 0, from Studio)

| Metric | Value | Date |
|---|---|---|
| Monetisation active |  |  |
| CTR (28-day) |  |  |
| Average view duration |  |  |
| 30-second retention |  |  |
| Median views, recent long-form | ~570 | 2026-11-14 |

## Figures

| id | label | on-screen | value | unit | source | source_date | basis |
|----|-------|-----------|-------|------|--------|-------------|-------|
| R1 | Commercial LPG 19 kg, Hyderabad | ₹ |  | ₹/cylinder |  |  | published |
| R2 | Rice, wholesale | ₹ |  | ₹/kg |  |  | published |
| R3 | Urad dal, wholesale | ₹ |  | ₹/kg |  |  | published |
| R4 | Cooking oil, wholesale | ₹ |  | ₹/litre |  |  | published |
| R5 | Shop rent, 200–300 sq ft | ₹ |  | ₹/month |  |  | published |
| R6 | Helper wage | ₹ |  | ₹/day |  |  | published |
| R7 | Menu price, idli plate | ₹ |  | ₹/plate |  |  | published |
| R8 | Licence and compliance cost | ₹ |  | ₹/year |  |  | published |
| R9 | Plates per day, typical centre |  |  | plates/day |  |  | estimate |

## Estimate ledger

| Assumption | Value | Basis |
|---|---|---|
| Plates per day |  |  |
| Wastage |  |  |
| Cylinders per month |  |  |
| Packaging share of plates |  |  |
| Owner hours per day | 12 |  |

## On-screen figures (generated by `python3 tally.py --table`)

| Figure | Value |
|---|---|

## Verification

verified_by:
verified_on:
```

- [ ] **Step 3: Run the contract check and watch it fail**

```bash
cd /mnt/data2/git_repos/creative_repos/video-projects/videos/2026-11-14/tiffin-centre-teardown
python3 check_research.py
```

Expected: exit 1, listing `R1: empty value` and similar for every row.

- [ ] **Step 4: Write `notes.md` with the decisions already locked**

```markdown
# Notes

- Video: `టిఫిన్ సెంటర్ నిజంగా ఎంత మిగులుతుంది?`
- Mode: graphics-only (`typographic`, `calc`, `tally`, `ledger`). No footage, no stills.
- Why graphics-only: the M1 policy bucket, and it removes the stock-footage and AI-food risks.
- Numbers: public prices with dates; assumptions labelled as estimates on screen.
- Archive b-roll is deferred to a separate engine task before video 2.
```

- [ ] **Step 5: Commit**

```bash
cd /mnt/data2/git_repos/creative_repos/video-projects
git add videos/2026-11-14/tiffin-centre-teardown
git commit -m "feat(tiffin): project skeleton, research contract, decisions"
```

- [ ] **Step 6: Ask the user for the three Studio numbers** (blocking only for the 48-hour judgement, not for production): monetisation active, CTR, 30-second retention. Record them in the baseline table.

---

### Task 2: Fill R1–R9 from dated public sources

**Files:**
- Modify: `.../tiffin-centre-teardown/research.md`

**Interfaces:**
- Consumes: the table contract from Task 1.
- Produces: complete figure rows consumed by Task 3's parameters and Task 6's narration.

- [ ] **Step 1: Pull each figure and fill its row**

Sources to check, in order:

| id | Source |
|---|---|
| R1 | Indane/HP/Bharat distributor price for Hyderabad, or Goodreturns LPG Hyderabad page (the chai pilot used Goodreturns: ₹3,081 on 2026-11-12 — re-verify, do not assume) |
| R2–R4 | Agmarknet Hyderabad mandi prices; if unavailable, a dated wholesale listing (IndiaMart / BigBasket) |
| R5 | 99acres / MagicBricks commercial listings for 200–300 sq ft in Kukatpally, Dilsukhnagar, LB Nagar; take three, record the median |
| R6 | Local job listings or dated press for helper/cook daily wage in Hyderabad |
| R7 | Public menu pages (Swiggy/Zomato) for 2–3 Hyderabad tiffin centres; record the median idli/dosa plate price |
| R8 | FSSAI registration fee and GHMC trade licence schedule; GST threshold only if relevant |
| R9 | Estimate: state the basis (e.g. "reference-format benchmark, labelled estimate") |

- [ ] **Step 2: Record every non-published number in the estimate ledger with a basis**

- [ ] **Step 3: Run the contract check**

```bash
cd /mnt/data2/git_repos/creative_repos/video-projects/videos/2026-11-14/tiffin-centre-teardown
python3 check_research.py
```

Expected: `ok: 9 figures complete`.

- [ ] **Step 4: Commit**

```bash
cd /mnt/data2/git_repos/creative_repos/video-projects
git add videos/2026-11-14/tiffin-centre-teardown/research.md
git commit -m "feat(tiffin): dated public price research, R1-R9"
```

---

### Task 3: The model — `tally.py` and its invariant tests

**Files:**
- Create: `.../tiffin-centre-teardown/tally.py`
- Test: `.../tiffin-centre-teardown/test_tally.py`

**Interfaces:**
- Produces: `Params` dataclass, `Result` dataclass, `model(p: Params) -> Result`, and `python3 tally.py --table` printing the day table.
- `Result` fields used later: `revenue`, `costs: dict[str, float]`, `total_cost`, `net`, `per_plate_margin`, `break_even_plates`, `owner_hourly`, `month_net`.

- [ ] **Step 1: Write the failing tests**

```python
# test_tally.py
import math
import unittest
from dataclasses import replace

from tally import Params, model


class Invariants(unittest.TestCase):
    def setUp(self):
        self.p = Params(plates=350, price=45.0, rice_rs_kg=55.0, dal_rs_kg=160.0, oil_rs_l=140.0)
        self.r = model(self.p)

    def test_costs_have_exactly_six_lines(self):
        self.assertEqual(
            set(self.r.costs),
            {"ingredients", "gas", "rent", "staff", "packaging", "utilities"},
        )

    def test_net_is_revenue_minus_costs(self):
        self.assertAlmostEqual(self.r.net, self.r.revenue - self.r.total_cost, places=2)

    def test_break_even_gives_zero_net(self):
        p = replace(self.p, plates=math.ceil(self.r.break_even_plates))
        self.assertAlmostEqual(model(p).net, 0.0, delta=self.p.price)

    def test_fewer_plates_lower_net(self):
        quiet = model(replace(self.p, plates=280))
        self.assertLess(quiet.net, self.r.net)

    def test_wastage_raises_cost(self):
        wasteful = model(replace(self.p, wastage_pct=0.12))
        self.assertGreater(wasteful.total_cost, self.r.total_cost)

    def test_owner_hourly_is_net_over_hours(self):
        self.assertAlmostEqual(self.r.owner_hourly, self.r.net / self.p.owner_hours, places=2)

    def test_default_figures_are_pinned(self):
        """These are the numbers the video speaks. If a param moves, script.md must move too."""
        self.assertAlmostEqual(self.r.revenue, 15750.00, places=2)
        self.assertAlmostEqual(self.r.total_cost, 9972.93, places=2)
        self.assertAlmostEqual(self.r.net, 5777.07, places=2)
        self.assertAlmostEqual(self.r.per_plate_margin, 24.75, places=2)
        self.assertAlmostEqual(self.r.break_even_plates, 113.09, places=2)
        self.assertAlmostEqual(self.r.owner_hourly, 481.42, places=2)
        self.assertAlmostEqual(self.r.month_net, 173312.00, places=2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /mnt/data2/git_repos/creative_repos/video-projects/videos/2026-11-14/tiffin-centre-teardown
python3 -m unittest test_tally -v
```

Expected: `ModuleNotFoundError: No module named 'tally'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# tally.py
"""The tiffin-centre model. Every rupee on screen comes from here."""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Params:
    plates: int = 350
    price: float = 45.0
    days: int = 30
    rice_g: float = 120.0
    rice_rs_kg: float = 55.0
    dal_g: float = 40.0
    dal_rs_kg: float = 160.0
    oil_ml: float = 15.0
    oil_rs_l: float = 140.0
    garnish_rs_plate: float = 4.0
    gas_rs_month: float = 18480.0
    rent_rs_month: float = 25000.0
    staff_rs_day: float = 1200.0
    packaging_rs_plate: float = 1.0
    packaging_share: float = 0.25
    utilities_rs_day: float = 150.0
    wastage_pct: float = 0.06
    owner_hours: float = 12.0


@dataclass(frozen=True)
class Result:
    revenue: float
    costs: dict[str, float] = field(default_factory=dict)
    per_plate_margin: float = 0.0
    days: int = 30
    owner_hours: float = 12.0

    @property
    def total_cost(self) -> float:
        return sum(self.costs.values())

    @property
    def net(self) -> float:
        return self.revenue - self.total_cost

    @property
    def fixed_daily(self) -> float:
        return self.costs["gas"] + self.costs["rent"] + self.costs["staff"] + self.costs["utilities"]

    @property
    def break_even_plates(self) -> float:
        return self.fixed_daily / self.per_plate_margin if self.per_plate_margin > 0 else math.inf

    @property
    def month_net(self) -> float:
        return self.net * self.days

    @property
    def owner_hourly(self) -> float:
        return self.net / self.owner_hours


def model(p: Params) -> Result:
    ingredients_per_plate = (
        p.rice_g / 1000 * p.rice_rs_kg
        + p.dal_g / 1000 * p.dal_rs_kg
        + p.oil_ml / 1000 * p.oil_rs_l
        + p.garnish_rs_plate
    )
    variable_per_plate = ingredients_per_plate * (1 + p.wastage_pct)
    costs = {
        "ingredients": variable_per_plate * p.plates,
        "gas": p.gas_rs_month / p.days,
        "rent": p.rent_rs_month / p.days,
        "staff": p.staff_rs_day,
        "packaging": p.packaging_rs_plate * p.packaging_share * p.plates,
        "utilities": p.utilities_rs_day,
    }
    return Result(
        revenue=p.plates * p.price,
        costs=costs,
        per_plate_margin=p.price - variable_per_plate,
        days=p.days,
        owner_hours=p.owner_hours,
    )


def table(p: Params) -> str:
    r = model(p)
    lines = [f"{'item':<14}{'₹':>12}"]
    for name, value in r.costs.items():
        lines.append(f"{name:<14}{value:>12,.0f}")
    lines.append(f"{'out':<14}{r.total_cost:>12,.0f}")
    lines.append(f"{'in':<14}{r.revenue:>12,.0f}")
    lines.append(f"{'kept':<14}{r.net:>12,.0f}")
    lines.append(f"break-even plates: {r.break_even_plates:.0f}")
    lines.append(f"owner ₹/hour: {r.owner_hourly:.1f}")
    lines.append(f"month net: {r.month_net:,.0f}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    if "--table" in sys.argv:
        print(table(Params()))
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /mnt/data2/git_repos/creative_repos/video-projects/videos/2026-11-14/tiffin-centre-teardown
python3 -m unittest test_tally -v
```

Expected: 6 tests, OK.

- [ ] **Step 5: Print the table and check it against the research**

```bash
python3 tally.py --table
```

Expected: a table whose `out`, `in` and `kept` lines are the figures the script will speak. If any line is implausible (negative keep, break-even above 350), fix the parameters in Task 2's ledger, not the model.

- [ ] **Step 6: Commit**

```bash
cd /mnt/data2/git_repos/creative_repos/video-projects
git add videos/2026-11-14/tiffin-centre-teardown/tally.py videos/2026-11-14/tiffin-centre-teardown/test_tally.py
git commit -m "feat(tiffin): arithmetic model with invariant tests"
```

---

### Task 4: Wire the research prices into the model and freeze the figures

**Files:**
- Modify: `.../tiffin-centre-teardown/tally.py` (default `Params`)
- Modify: `.../tiffin-centre-teardown/research.md` (`## On-screen figures`)

**Interfaces:**
- Consumes: R1–R9 values and the estimate ledger.
- Produces: the final set of on-screen figures Task 6 may speak.

- [ ] **Step 1: Replace the default `Params` values with the researched ones**

Set `rice_rs_kg`, `dal_rs_kg`, `oil_rs_l`, `rent_rs_month`, `staff_rs_day`, `price`, `gas_rs_month` (cylinders × cylinder price), `plates` from the ledger. Keep every unit explicit in a comment naming the row id:

```python
    rice_rs_kg: float = 55.0   # R2
    dal_rs_kg: float = 160.0   # R3
    oil_rs_l: float = 140.0    # R4
    rent_rs_month: float = 25000.0  # R5
    staff_rs_day: float = 1200.0    # R6
    price: float = 45.0        # R7
    gas_rs_month: float = 18480.0   # R1 × cylinders/month
    plates: int = 350          # R9
```

- [ ] **Step 2: Re-run the tests and the table**

```bash
cd /mnt/data2/git_repos/creative_repos/video-projects/videos/2026-11-14/tiffin-centre-teardown
python3 -m unittest test_tally -v && python3 tally.py --table
```

Expected: tests OK; table prints the researched figures.

- [ ] **Step 3: Paste the printed table into `research.md` under `## On-screen figures`**

- [ ] **Step 4: Commit**

```bash
cd /mnt/data2/git_repos/creative_repos/video-projects
git add videos/2026-11-14/tiffin-centre-teardown
git commit -m "feat(tiffin): research prices wired into the model; figures frozen"
```

---

### Task 5: Human verification gate (blocking)

**Files:**
- Modify: `.../tiffin-centre-teardown/research.md` (`## Verification`)

- [ ] **Step 1: The user checks every figure**

For each of R1–R9: is the source real, and does it say that number on that date? Pay special attention to R5 (rent median) and R7 (menu price median) — those are the two most likely to be wrong.

- [ ] **Step 2: The user fills in**

```markdown
verified_by: <name>
verified_on: 2026-11-<day>
```

- [ ] **Step 3: Do not proceed until this is filled.** A render that costs money before sign-off is waste.

- [ ] **Step 4: Commit**

```bash
cd /mnt/data2/git_repos/creative_repos/video-projects
git add videos/2026-11-14/tiffin-centre-teardown/research.md
git commit -m "chore(tiffin): figures verified by human"
```

---

### Task 6: Write `script.md`

**Files:**
- Create: `.../tiffin-centre-teardown/script.md`
- Create: `.../tiffin-centre-teardown/check_script.py`

**Interfaces:**
- Consumes: the frozen figures from Task 4 and the `calc`/`tally`/`ledger` grammar in `video-editor/docs/STOCK_EXPLAINERS.md`.
- Produces: a script that `load_explainer_script` parses without error, 1,050–1,300 Telugu words, 14–16 scenes.

- [ ] **Step 1: Write the script skeleton with front matter and scene props**

The figures below are `tally.py --table` output for the default `Params`. **If Task 4 changed a
parameter, the printed table wins** — update every literal in this file to match, and keep the
running `tally` arithmetic consistent as shown.

Grammar proven on the chai pilot: costs are **negative** `calc` amounts; the `tally` carries the
running total; derived per-unit figures use `in_tally: false` so they do not move it.

```markdown
---
title: టిఫిన్ సెంటర్ నిజంగా ఎంత మిగులుతుంది?
aspect_ratio: "16:9"
voice: elevenlabs:EohubqvAiTiSkWCp1vu7
voice_model: eleven_v4
voice_language: te
voice_pace: 1.1
narration_mode: per_scene
design: cream_editorial
target_seconds: 480
music_mood: warm light indian morning, soft mridangam, unobtrusive
reuse_threshold: 0.30
pronunciations:
  "₹45": నలభై ఐదు రూపాయలు
  "350": మూడు వందల యాభై
  "₹15,750": పదిహేను వేల ఏడు వందల యాభై రూపాయలు
  "₹7,086": ఏడు వేల ఎనభై ఆరు రూపాయలు
  "₹9,973": తొమ్మిది వేల తొమ్మిది వందల డెబ్బై మూడు రూపాయలు
  "₹5,777": ఐదు వేల ఏడు వందల డెబ్బై ఏడు రూపాయలు
---

## Scene 1 — ఉదయం ఎనిమిది గంటలు
visual: typographic
layout: big_stat
headline: 350 ప్లేట్లు
sub: ఒక ఉదయం · అంచనా
tally: {label: గల్లా పెట్టె, value: 0}
narration: |
  ఉదయం ఎనిమిది గంటలు. హైదరాబాద్‌లో ఒక టిఫిన్ సెంటర్ తెరిచింది.
  ఈరోజు మూడు వందల యాభై ప్లేట్లు అమ్ముతారు — ఇది మా లెక్క.

## Scene 2 — ప్రశ్న
visual: typographic
layout: sequence
steps:
  - క్యూ కడుతుంది
  - గల్లా నిండుతుంది
  - చేతికి ఏం మిగులుతుంది?
tally: {label: గల్లా పెట్టె, value: 0}
narration: |
  క్యూ కడుతుంది, గల్లా నిండుతుంది. కానీ రోజు చివర్లో, అన్ని ఖర్చులు పోగా
  యజమాని చేతికి ఏం మిగులుతుంది? అది ఎవరూ చెప్పరు. మనం లెక్కిద్దాం.

## Scene 3 — రోజు గల్లా
visual: typographic
layout: big_stat
headline: ₹15,750
sub: రోజు అమ్మకం
tally: {label: గల్లా పెట్టె, value: 15750}
calc:
  - {label: అమ్మకం, expr: "350 × ₹45", amount: 15750}
narration: |
  మొదట గల్లా. మూడు వందల యాభై ప్లేట్లు, ప్లేట్ నలభై ఐదు రూపాయలు.
  అంటే రోజుకి పదిహేను వేల ఏడు వందల యాభై రూపాయలు. ఇది ఇంకా ఖర్చు కాదు.
```

Scenes 4–16, each carrying one cost. The `amount` in the `in_tally` row is what the running
`tally` moves by; the `in_tally: false` rows are shown but inert.

| Scene | Layout | Figure | `calc` rows (label / expr / amount) | tally after |
|---|---|---|---|---|
| 4 | `big_stat` + `calc` | ప్లేటుకి ₹19.10 | బియ్యం `120 గ్రా × ₹55` 6.60 ✱ · పప్పు `40 గ్రా × ₹160` 6.40 ✱ · నూనె `15 ml × ₹140` 2.10 ✱ · మిగతా `ప్లేటుకి` 4.00 ✱ · వృథా 6%తో రోజుకి `₹19.10 × 1.06 × 350` **−7086** | 8,664 |
| 5 | `big_stat` + `calc` | రోజుకి ₹616 | గ్యాస్ `₹18,480 ÷ 30` **−616** | 8,048 |
| 6 | `big_stat` + `calc` | రోజుకి ₹833 | అద్దె `₹25,000 ÷ 30` **−833** | 7,215 |
| 7 | `big_stat` + `calc` | రోజుకి ₹1,200 | జీతం `రోజుకి` **−1200** | 6,015 |
| 8 | `big_stat` + `calc` | రోజుకి ₹238 | ప్యాకింగ్ `350 × ₹1.00 × 25%` −88 · కరెంట్ నీరు `రోజుకి` −150 | 5,777 |
| 9 | `big_stat` | ఖర్చు ₹9,973 | (no rows; states the day's outgo) | 5,777 |
| 10 | `ledger` | ఒక రోజు లెక్క | rows: అమ్మకం 15750; పదార్థాలు −7086; గ్యాస్ −616; అద్దె −833; జీతం −1200; ప్యాకింగ్ −88; కరెంట్ −150; total మిగులు 5777 | 5,777 |
| 11 | `bar_compare` | ఖర్చు విభజన | bars: పదార్థాలు 7086 · జీతం 1200 · అద్దె 833 · గ్యాస్ 616 · మిగతా 238 | 5,777 |
| 12 | `big_stat` + `calc` | రోజుకి 114 ప్లేట్లు | స్థిర ఖర్చు `₹616+₹833+₹1,200+₹150` 2799 ✱ · బ్రేక్-ఈవెన్ `₹2,799 ÷ ₹24.75` 114 ✱ | 5,777 |
| 13 | `big_stat` + `calc` | గంటకి ₹481 | రోజుకి మిగులు `₹5,777 ÷ 12 గంటలు` 481 ✱ · హెల్పర్ గంటకి `₹1,200 ÷ 8 గంటలు` 150 ✱ | 5,777 |
| 14 | `ledger` | నెలకి ₹1,73,310 | rows: రోజుకి మిగులు 5777 × 30; total నెల 173310 | 5,777 |
| 15 | `sequence` | మూడు సంఖ్యలు | 350 ప్లేట్లు · ₹24.75 ప్లేటుకి మిగులు · 114 ప్లేట్లు బ్రేక్-ఈవెన్ | 5,777 |
| 16 | `big_stat` | మూలాలు | ధరలు 2026-11-<day> · ఇది ఒక సాధారణ సెంటర్ లెక్క | 5,777 |

✱ = `in_tally: false`.

Scene 13 is deliberately **not** sold as a shock. The model says the owner keeps ₹481/hour against
a helper's ₹150/hour — the honest line is that the 12 hours are what buy the ₹1.73 lakh month, and
that the money is return on capital and risk, not on time. Write it that way.

- [ ] **Step 2: Write the narration for every scene**

Rules for the prose:
- Scene 2 is the claim: busy is not the same as profitable.
- Every cost scene says the number, then one sentence of what it means; no scene exceeds 120 words.
- Total narration ~1,200 words, because ElevenLabs Telugu at `voice_pace: 1.1` runs **2.5 words
  per second** (measured: `india-geography-1`, 211 words → 83.2 s of narration audio). 480 s at
  that rate is ~1,200 words; 1,050–1,300 words maps to 6:54–8:32.
- The chai pilot's 1.25 w/s is **not** the rate to copy — `chai-stall-pilot-1` used the padded
  non-ElevenLabs variant, whose scenes were floored at 10–17 s each.
- Scene 13 is a model result: say "లెక్క ప్రకారం" (by this calculation), never "నిజంగా" as if observed.
- Costs are spoken as outgo and written negative in `calc`; do not flip the sign per scene.
- No advice. No health claims. No adjective without a number beside it.
- Every `calc` amount must equal its expression; the parser enforces this.
- Every `tally` value must move by exactly the sum of the scene's `in_tally` rows.

- [ ] **Step 3: Write `check_script.py`**

```python
# check_script.py
"""Parse script.md with the engine and check length and scene count."""
from __future__ import annotations

import sys
from pathlib import Path

from video_editor_pipelines.explainer_script import load_explainer_script

MIN_WORDS, MAX_WORDS = 1050, 1300
MIN_SCENES, MAX_SCENES = 14, 16


def main(path: Path) -> int:
    script = load_explainer_script(path)
    counts = [len(scene.narration.split()) for scene in script.scenes]
    total = sum(counts)
    print(f"scenes: {len(script.scenes)}")
    for scene, n in zip(script.scenes, counts):
        print(f"  {n:>4} words  {scene.title}")
    print(f"total: {total} words")
    problems = []
    if not MIN_SCENES <= len(script.scenes) <= MAX_SCENES:
        problems.append(f"scene count {len(script.scenes)} outside {MIN_SCENES}-{MAX_SCENES}")
    if not MIN_WORDS <= total <= MAX_WORDS:
        problems.append(f"word count {total} outside {MIN_WORDS}-{MAX_WORDS}")
    if any(n > 120 for n in counts):
        problems.append("a scene exceeds 120 words")
    if problems:
        print("\n".join(problems))
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name("script.md"))
    raise SystemExit(main(target))
```

- [ ] **Step 4: Run the parse and length check**

```bash
cd /mnt/data2/git_repos/creative_repos/video-editor
.venv/bin/python /mnt/data2/git_repos/creative_repos/video-projects/videos/2026-11-14/tiffin-centre-teardown/check_script.py
```

Expected: `ok`, with 14–16 scenes and 1,050–1,300 words. A `ScriptError` names the bad scene and key — fix it there.

- [ ] **Step 5: Commit**

```bash
cd /mnt/data2/git_repos/creative_repos/video-projects
git add videos/2026-11-14/tiffin-centre-teardown/script.md videos/2026-11-14/tiffin-centre-teardown/check_script.py
git commit -m "feat(tiffin): Telugu script, 14-16 scenes, figures on screen"
```

---

### Task 7: Beats map and cue audit

**Files:**
- Create: `.../tiffin-centre-teardown/beats.md`
- Modify: `.../tiffin-centre-teardown/script.md` if the audit finds a cue the voice never says

**Interfaces:**
- Consumes: `script.md`.
- Produces: a scene table the packaging and Short tasks read.

- [ ] **Step 1: Build the scene table**

```markdown
| # | Scene | Layout | Figure | Cue word | Source line on screen |
|---|-------|--------|--------|----------|-----------------------|
| 1 | 8am, 350 plates | big_stat | 350 | మూడు వందల యాభై | R9 estimate |
| ... | | | | | |
```

- [ ] **Step 2: Audit every cue**

For each `headline`, `stat`, `bar` label, `ledger` row and `sequence` step: does the narration speak that exact word or number? If not, either speak it or set `cue: false` (`headline_cue: false` at scene level). The engine's cue gate fails a cue the scene never says.

- [ ] **Step 3: Record the disclosure beat**

Scene 16 must carry, on screen, in Telugu: "ఇది ఒక సాధారణ టిఫిన్ సెంటర్ యొక్క లెక్క; ధరలు [date] నాటివి" (this is a typical centre's arithmetic; prices as of [date]). Record it in `beats.md`.

- [ ] **Step 4: Commit**

```bash
cd /mnt/data2/git_repos/creative_repos/video-projects
git add videos/2026-11-14/tiffin-centre-teardown
git commit -m "feat(tiffin): beats map, cue audit, disclosure line"
```

---

### Task 8: Free dry run — plates and timing

**Files:**
- No repo files; output under `/mnt/data2/video-editor-data/`.

**Interfaces:**
- Consumes: `script.md`, `video-editor/assets/music/default.mp3`.
- Produces: a timing read used to trim or extend narration before paying for voice.

- [ ] **Step 1: Confirm the free TTS is installed**

```bash
cd /mnt/data2/git_repos/creative_repos/video-editor
.venv/bin/python -c "import kittentts" || uv pip install kittentts
```

- [ ] **Step 2: Run the dry render**

```bash
cd /mnt/data2/git_repos/creative_repos/video-editor
.venv/bin/ve explain \
  /mnt/data2/git_repos/creative_repos/video-projects/videos/2026-11-14/tiffin-centre-teardown/script.md \
  -j tiffin-dry-01 \
  --music-path /mnt/data2/git_repos/creative_repos/video-editor/assets/music/default.mp3 \
  --tts-provider kitten_tts --image-provider stub_image --auto-approve
```

Expected: a rendered MP4 under `/mnt/data2/video-editor-data/outputs/tiffin-dry-01/`, spending nothing.

- [ ] **Step 3: Watch it once and record in `notes.md`**

Check: every plate is legible; every `calc` row appears when its amount is spoken; the ledger clears the captions; each scene changes on its cue.

**Do not judge duration from this render.** `kitten_tts` floors every scene at 10–17 s (that is why
`chai-stall-pilot-1` ran 160 s on 199 words), so its total is meaningless. Judge length from the word
count at 2.5 w/s — `python3 check_script.py` already does that — and confirm the real duration in
Task 9 Step 2 with `ffprobe`. Record any scene to shorten.

- [ ] **Step 4: Fix and re-run if needed**

Edit `script.md`, then repeat Step 2 with `-j tiffin-dry-02`. Do not proceed with a scene that is illegible or mistimed.

- [ ] **Step 5: Commit any script changes**

```bash
cd /mnt/data2/git_repos/creative_repos/video-projects
git add videos/2026-11-14/tiffin-centre-teardown
git commit -m "fix(tiffin): timing and plate fixes from dry run"
```

---

### Task 9: Priced render with the Telugu voice

**Files:**
- Copy output into `.../tiffin-centre-teardown/tiffin-v1.mp4`

- [ ] **Step 1: Run the render, without `--auto-approve`**

```bash
cd /mnt/data2/git_repos/creative_repos/video-editor
.venv/bin/ve explain \
  /mnt/data2/git_repos/creative_repos/video-projects/videos/2026-11-14/tiffin-centre-teardown/script.md \
  -j tiffin-v1 \
  --music-path /mnt/data2/git_repos/creative_repos/video-editor/assets/music/default.mp3
```

Expected: the cost estimate prints and waits. Approve only if it is **≤ $2.00** (expected: ElevenLabs narration ~$0.5–1.0, music $0, graphics $0).

- [ ] **Step 2: Record the actual cost and the true duration**

```bash
ffprobe -v error -show_entries format=duration -of csv=p=0 \
  /mnt/data2/video-editor-data/outputs/tiffin-v1/final.mp4
```

Expected: **420–540 s** (7:00–9:00). Write the cost and the duration into `notes.md`.

Two failure modes to watch for:

- **Duration far above the word estimate** — `target_seconds: 480` is being used to pad. If the
  audio stops well before the video, drop `target_seconds` from the front matter and re-render, so
  the cut ends where the narration ends.
- **Duration well under 7:00** — the narration is short. Add scenes or lines in `script.md` and
  re-render with a new job id; do not stretch audio.

- [ ] **Step 3: Copy the output**

```bash
cp /mnt/data2/video-editor-data/outputs/tiffin-v1/final.mp4 \
  /mnt/data2/git_repos/creative_repos/video-projects/videos/2026-11-14/tiffin-centre-teardown/tiffin-v1.mp4
```

- [ ] **Step 4: Commit the notes (not the MP4 — renders stay untracked)**

```bash
cd /mnt/data2/git_repos/creative_repos/video-projects
git add videos/2026-11-14/tiffin-centre-teardown/notes.md
git commit -m "chore(tiffin): render v1 cost and output"
```

---

### Task 10: QA gates

**Files:**
- Modify: `script.md` / `tally.py` only if a gate fails; then re-render with a new job id.

- [ ] **Step 1: Run the engine review gate**

```bash
cd /mnt/data2/git_repos/creative_repos/video-editor
.venv/bin/ve review tiffin-v1 --expected-aspect-ratio 16:9 --loudness-target-lufs -14
```

Expected: pass, or a named failure (sentence cut, cue, caption, loudness).

- [ ] **Step 2: Check the six gates in `beats.md`**

1. Every on-screen number is in `research.md` with a source and date or an estimate label.
2. Arithmetic: `python3 -m unittest test_tally -v` passes and the printed table matches the narration.
3. Pronunciation: listen to every place and dish name once; fix the glossary and re-voice only.
4. Loudness ≈ −14 LUFS (from the review gate).
5. Captions match narration; every source line is on screen ≥3 s.
6. No AI food, hands or stills anywhere (this video should have none at all).

- [ ] **Step 3: Fix and re-render only if a gate fails**

Use a new job id (`tiffin-v2`) so the old render stays comparable.

- [ ] **Step 4: Commit any fix**

```bash
cd /mnt/data2/git_repos/creative_repos/video-projects
git add videos/2026-11-14/tiffin-centre-teardown
git commit -m "fix(tiffin): QA gate fixes"
```

---

### Task 11: Packaging

**Files:**
- Create: `.../tiffin-centre-teardown/packaging.md`

- [ ] **Step 1: Write the title, description and chapters**

Title (locked):

```
టిఫిన్ సెంటర్ నిజంగా ఎంత మిగులుతుంది? | Tiffin Centre: What Does the Owner Really Keep?
```

Description template:

```markdown
ఒక టిఫిన్ సెంటర్ నిండుగా ఉంటే, యజమాని నిజంగా ఎంత మిగులుతుంది? ఒక రోజు గల్లా,
ప్రతి ఖర్చు, మరియు చివర్లో మిగిలేది — లెక్క ప్రకారం.

ఇది ఒక సాధారణ టిఫిన్ సెంటర్ యొక్క లెక్క. ధరలు <date> నాటివి; ఖర్చుల అంచనాలు
స్క్రీన్‌పై స్పష్టంగా గుర్తించబడ్డాయి.

ఆధారాలు:
- LPG: <source>, <date>
- బియ్యం, పప్పు, నూనె: <source>, <date>
- అద్దె: <source>, <date>
- జీతాలు: <source>, <date>
- మెనూ ధరలు: <source>, <date>

Chapters:
00:00 ...
```

- [ ] **Step 2: Write three thumbnail concepts**

One per variant, each ≤4 Telugu words and one high-contrast subject: (a) a ₹45 plate beside a ₹X keep, (b) a cost-stack bar with the last bar nearly empty, (c) the owner's 12-hour clock beside the hourly wage.

- [ ] **Step 3: Write the Short plan**

Pick the single strongest beat (the owner-wage twist), 45–60s, same voice, vertical reframe, one caption line per number. Record the in/out timestamps from the final cut.

- [ ] **Step 4: Commit**

```bash
cd /mnt/data2/git_repos/creative_repos/video-projects
git add videos/2026-11-14/tiffin-centre-teardown/packaging.md
git commit -m "feat(tiffin): title, description, thumbnails, Short plan"
```

---

### Task 12: Publish and first read

**Files:**
- Modify: `.../tiffin-centre-teardown/notes.md`

- [ ] **Step 1: The user uploads** with the Task 11 title and description, sets the thumbnail, adds chapters, and publishes on the fixed day chosen in week 0. Do not re-title after 24 hours.

- [ ] **Step 2: Record the publish time and URL in `notes.md`**

- [ ] **Step 3: At 48 hours, record CTR and 30-second retention** next to the channel baseline in `research.md`. Do not record views as the verdict.

- [ ] **Step 4: At 28 days, record views as a multiple of 570**

- [ ] **Step 5: Commit**

```bash
cd /mnt/data2/git_repos/creative_repos/video-projects
git add videos/2026-11-14/tiffin-centre-teardown
git commit -m "chore(tiffin): 48h and 28-day reads"
```

---

## Self-review notes

- **Spec coverage:** §2.1 (claim, method, beat outline, visuals, risk) → Tasks 2–7; §3 pipeline and QA gates → Tasks 8–10; §4 packaging → Task 11; §5 week 0–3 → Tasks 1, 12; §6 judgement → Task 12; §10 definition of done → Tasks 5–12.
- **Deviation from spec §2.1:** the spec lists archive b-roll for this video. `ve explain` footage scenes use the stock chain with an AI-image fallback, which would violate the no-AI-food rule. This plan therefore ships **graphics-only** (the policy-safe M1 pattern) and defers archive-clip support to a separate engine task before video 2. Recorded in `notes.md`.
- **Unblocked by design:** the whole plan spends nothing until Task 9, and Task 9 is capped at $2.00.

### Verification done while writing this plan

- `ve review --expected-aspect-ratio 16:9 --loudness-target-lufs -14` and
  `ve explain -j ... --music-path ... --tts-provider kitten_tts --image-provider stub_image
  --auto-approve`: flags confirmed from `--help`.
- `kitten_tts`, `stub_image` confirmed as real provider names; `VALID_LAYOUTS` is exactly
  `big_stat, bar_compare, definition, sequence, array, ledger`.
- `calc` / `tally` / `in_tally` / `ledger` grammar copied from the shipped chai pilot, not invented.
- `tally.py` and `test_tally.py` were run as written: **7 tests pass**, and the model reproduces
  every figure in the scene table (₹15,750 in, ₹9,973 out, ₹5,777 kept, 114-plate break-even,
  ₹481/hour, ₹1,73,312/month). A first draft failed — `Result` lacked `owner_hours` and `days` —
  and was fixed; the class as printed here is the version that runs.
- `check_script.py` runs against the chai script and fails its gates as designed (11 scenes,
  199 words). Its `load_explainer_script` / `.scenes` / `.title` / `.narration` calls are correct.
- Narration rate re-measured: `india-geography-1` = 211 words → 83.2 s audio (**2.5 w/s**).
  `chai-stall-pilot-1`'s 1.25 w/s is the padded `kitten_tts` path and is not a valid calibration.

### Still unverified, by design

- The R1–R9 prices themselves — that is Task 2, and Task 5's human gate exists precisely because
  the research is the part this plan cannot check.
- True render duration, which only Task 9's `ffprobe` can settle.
