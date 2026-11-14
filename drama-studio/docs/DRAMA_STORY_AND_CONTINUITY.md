# Drama story and continuity

**Date:** 2026-11-06
**Applies to:** every drama episode (Mantra Rahasyam and successors)
**Source:** Andy Lo, *"Micro Dramas Made $11B. Make One a Day With Claude"*, 2026-10-31 —
the story rules, the show-notes idea and the storyboard review point. Everything
else in that video is either already built here or is a paid-community funnel
(see §7).

Two things were missing from the drama pipeline and are now implemented:

- a **story gate** (`scripts/story_rules.py`) — the five rules applied to the
  shot list, with a beat table;
- a **state ledger** (`scripts/episode_state.py`) — who knows what and who holds
  which prop, checked per episode.

Plus a cheaper review point (`drama_review.py storyboard`) and three clauses
that video prompts were missing.

---

## 1. The five rules

They exist because the first episodes of a series are where a viewer is won or
lost, and because "does this story work" was being judged only in conversation.
Rules 1, 2, 4 and 5 are mechanical enough to check from `shots.json`; rule 3 is
a staging judgement and is reported, never auto-passed.

| # | Rule | What it means | How the gate checks it |
|---|---|---|---|
| 1 | **Question first** | Open on a moment that makes the viewer ask why, and withhold the answer. | A `?` or an interrogative first word in the first 15 % of scenes (or in the opener's visual). |
| 2 | **Make us care** | The lead does something kind, fair or selfless and is punished for it. | A selfless-act phrase (`save`, `protect`, `rescue`, `stand up for`, `risks his life`…) in the line or visual of the first half. Idioms such as "take care" do not count. |
| 3 | **Viewer knows more** | The audience sees a danger the character cannot. Suspense beats shock. | Staging cues in the visual prompt (`unaware`, `hidden`, `behind him`, `approaching`, `watching`…). No cue → `REVIEW`, not a pass. |
| 4 | **Every beat turns** | Each beat changes the situation; beats connect with *but* / *therefore*, never *and then*. | Scenes carry a declared `turn` (`up`, `down`, `worse`, `better`, `twist`); a run of three or more scenes in one emotional direction fails. Without declarations the rule is `REVIEW` with the flat runs listed. |
| 5 | **Payoff, then reopen** | Deliver the win, open a bigger question, stop. That is where a paywall belongs. | The last scene ends on a `?`, an interrogative, or a forward hook (`will`, `come back`, `not the end`, `remembers`, `awaits`…). |

Rule 4 deliberately allows **two** same-direction beats in a row: bad → worse is
escalation, not a flat "and then". Only a run of three is flagged, and only as a
failure when the author has declared turns (so the gate can tell the difference
between a deliberate escalation and an unexamined sequence).

### Running it

```bash
python scripts/story_rules.py videos/2026-10-30/mantra-rahasyam-ep1
python scripts/story_rules.py <episode> --strict   # exit 1 on a failure
python scripts/story_rules.py <episode> --json     # machine-readable, writes nothing
```

It writes `review/story_rules.md` (verdicts, evidence quotes, beat table) and
`review/story_rules.json`. It is **advisory** in every pipeline: it is not part
of `drama_review.py`'s approval hashes. An episode can pass every render gate
and still be a bad story; that is exactly what this catches, so it must not be
able to block a render on its own.

On Mantra Rahasyam Ep1 v2 (2026-11-06):

| Rule | Verdict | Why |
|---|---|---|
| 1 Question first | PASS | the opener asks a question in scene 3 |
| 2 Make us care | PASS | "He tried to save that little girl…" (scene 3) |
| 3 Viewer knows more | PASS | the lorry "approaching", hidden from the child (scene 2) |
| 4 Every beat turns | REVIEW | 16 scenes declare no `turn`; scenes 5–9 stay dark |
| 5 Payoff then reopen | PASS | "What else did the divine voice tell Abhinav? … To find out…" |

The scene 5–9 run is the hospital act: accident → prayer → surgery → flatline.
It reads as escalation, which is why rule 4 is a review item on an unannotated
episode rather than a failure. Annotating those five beats with `worse` turns the
item green without changing a frame.

---

## 2. The state ledger

The character bibles (`videos/<series>/assets/characters/*/bible.md`) cover
identity: face, wardrobe, signature item, voice, reference images. They do not
cover **state**: who has learned the secret, who is holding the ring, what the
audience knows that the family does not. That is the class of bug no visual QA
check can catch — the face is right and the story contradicts itself.

`state.json` is one file per series (or per episode while a series is starting):

```json
{
  "series": "mantra-rahasyam",
  "episode_order": ["mantra-rahasyam-ep1", "mantra-rahasyam-ep2"],
  "characters": {
    "abhinav": {"aliases": ["Abhi"], "knows": ["divine_voice"], "has": ["rudraksha"]}
  },
  "props": {
    "rudraksha": {"aliases": ["rudraksha", "bracelet"],
                  "introduced_in": "mantra-rahasyam-ep1", "holder": "abhinav"}
  },
  "facts": {
    "divine_voice": {"aliases": ["journey does not end"],
                     "revealed_in": "mantra-rahasyam-ep1", "known_by": ["abhinav"]}
  },
  "episodes": {}
}
```

Aliases must be distinctive phrases. Matching is a case-insensitive substring
over the episode's English and Telugu lines and its visual prompts, so an alias
like `"him"` would match everywhere.

### Running it

```bash
python scripts/episode_state.py update <episode> --state <series>/state.json
python scripts/episode_state.py check  <episode> --state <series>/state.json [--strict]
```

| Output | Meaning |
|---|---|
| `FAIL` | A prop or fact appears before the episode that introduces it. |
| `WARN` | A speaker is not declared in the ledger — add them, or fix the name. |
| `WARN` | A character who is not marked as knowing a fact speaks it after the reveal. |

`update` records the cast, the props and facts mentioned, and the closing hook.
It never invents knowledge — "who learned what" stays a human edit, because that
is a story decision, not a derivable fact.

Without `--state`, the ledger is `<episode>/state.json`. Use one file per series
so the ordering (`episode_order`, or the trailing number in the name) spans
episodes.

---

## 3. The storyboard board

```bash
python scripts/drama_review.py storyboard <episode>   # -> review/storyboard.html
```

One row per visual: still, kind, camera move, action, line, and the three video
directives from §4. Stills are looked up in `v2/frames/<id>.png`, then
`frames/<id>.png`, then the visual's `still` key; a missing still is shown as
such rather than hidden, because a storyboard without pictures is the state you
are reviewing.

This is the cheapest decision point in the pipeline. Fixing a still costs
nothing; fixing a shot costs money. The board is meant to be looked at *before*
any video batch is submitted, and it doubles as the prompt checklist.

---

## 4. Three clauses video prompts were missing

Added to the storyboard's "Video prompt must say" column so they are in front of
the reviewer at the moment the shot is ordered:

1. **`single continuous take, no cuts, no camera changes`** — without it the
   model invents its own cuts inside a 5-second shot, which is how four story
   moments ended up in one clip in the source workflow. One action per shot is
   already the house rule; this makes the *no cuts* part explicit.
2. **`and nobody else in frame`** — name everyone present and exclude strangers.
   Extra people appear when a shot names one character and the model fills the
   street.
3. **`spoken exactly as written`** — for any shot that carries a line, so the
   model does not paraphrase, translate or improvise dialogue.

The first two apply to every video prompt; the third only to shots with a line.
They are display-only in the board: the episode's own prompt script remains the
place they are applied.

---

## 5. What was already built here (do not "adopt" it)

The source workflow is one Claude Code agent with two stops (storyboard, price).
This repo already has the stronger version of most of it:

| Source idea | Here |
|---|---|
| Review before spending | Stills first, lip-sync as the exception; explicit price approval before each batch |
| Two hard stops | Seven review gates with a hash of what the user saw (`drama_review.py`) |
| Per-character reference sheets | `bible.md` per character: wardrobe table, signature item, voice id, refs, continuity rules |
| "If it shows up twice, it gets a sheet" | The bible's reference images plus the still-reuse plan |
| Fix only what is broken | Reroll one clip by deleting its file, not the scene |

---

## 6. Limits

- **English-keyword heuristics.** Rules 1, 2, 3 and 5 read the `en` line and the
  visual prompt. Episodes already carry `en` for every line, so this works, but a
  Telugu-only draft would need its `en` filled first.
- **Rule 4 needs annotations to fail.** Without `turn` fields it is a review
  item. That is deliberate: a valence test cannot tell escalation from flatness,
  and a gate that cries wolf gets ignored.
- **The ledger is a validator, not a writer.** It will not decide that a
  character learned something; it only checks that what you declared holds.

---

## 7. What was not adopted

- **One agent + a skill file as the pipeline.** The repo's typed stages and
  gates exist so a bad render cannot reach delivery. A prompt file is a weaker
  gate, not a replacement.
- **The paywall strategy and the market numbers** ($11B, 100M views in 12 hours,
  220k AI dramas with fewer than 1 in 200 succeeding). The numbers are
  promotional and unverified; the retention logic in §1 stands on its own.
- **Model pricing claims** from the video (a lip-sync model at 4¢/s on a "sale
  ending November 7"). Route on `models.yaml` and `POYO_ROUTING.md`, not on a
  sponsor-adjacent video.
- **Posting mechanics** (episode numbering, the AI-content label). Worth adding
  to the publish checklist, but they are not pipeline code.
