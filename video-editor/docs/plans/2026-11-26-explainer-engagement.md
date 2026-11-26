# Explainer engagement: hook overlay, tally coin sound, cost checklist strip

Approved by the user on 2026-11-26 for the tiffin teardown video and all later `ve explain` videos. None of these
add narration, so no ElevenLabs cost and no scene renumbering. Layers are per scene, so the chunk cache
re-renders only the scenes that use them.

Tests: `.venv/bin/python -m pytest <files>` from the repo root (tests live in `tests/`). Lint: `.venv/bin/ruff check <files>`.
Follow the existing patterns in `packages/core/src/video_editor_core/tally_chip.py` and `calc_card.py`
(markup + extra_css returned to `adapters/from_scene_plan.py`, which wraps it in a `CompositionLayer`).
Script fields reach the renderer through `packages/pipelines/src/video_editor_pipelines/explainer_script.py`
(see how `tally:` and `calc:` are parsed and validated there) into `scene["props"]`.

## 1 Hook overlay on a scene's first seconds
files: packages/core/src/video_editor_core/hook_card.py, packages/core/src/video_editor_core/adapters/from_scene_plan.py, packages/pipelines/src/video_editor_pipelines/explainer_script.py, tests/test_hook_card.py
do: A scene may carry `hook: {headline: <text>, sub: <text, optional>, seconds: <float, default 3.0>}`. Parse and validate it in explainer_script.py like `tally:` (reject an empty headline or seconds outside 1–8) so it lands in `props["hook"]`. In from_scene_plan.py add a `_hook_layers` helper that emits one `kind="html"` layer (preset "hook") for that scene: a dark translucent rounded panel centred horizontally, its vertical centre at about 38% of the height (clear of the captions at the bottom and the tally top-left), width about 70% of the canvas, headline large and bold (white), sub below in the accent yellow used by captions. It fades and slides in at 0.15 s and fades out so it is gone by `seconds` (use animation-delay, clip-relative, like the tally). Its z is above footage and below the tally chip and calc card. Use the same Telugu-capable font stack as calc_card. Telugu text must not be drawn with ffmpeg drawtext.
done-when: `.venv/bin/python -m pytest tests/test_hook_card.py tests/test_adapter_scene_plan.py -q` passes, with tests that a scene with `hook` gets exactly one hook layer whose markup holds the escaped headline and sub and whose CSS fades out by `seconds`; a scene without `hook` gets none; explainer_script rejects an empty headline.
difficulty: medium
kind: change

## 2 Coin sound when the tally counts
files: packages/core/src/video_editor_core/tally_chip.py, packages/core/src/video_editor_core/adapters/from_scene_plan.py, packages/core/src/video_editor_core/sfx_synth.py, packages/core/src/video_editor_core/assets/sfx/, tests/test_tally_sound.py
needs: 1
do: Add `sfx_synth.py` that writes short coin sounds with the Python standard library only (`wave`, `math`, `struct`; no numpy, no network): a metallic clink = sum of a few inharmonic sines (for example 2,093, 3,520 and 5,274 Hz with smaller weights on the higher ones) with fast exponential decay, two hits about 70 ms apart, total about 0.45 s, 48 kHz mono 16-bit, peak about −3 dBFS. `coin_in.wav` (money in) uses those pitches; `coin_out.wav` (money out) is the same shape about a fourth lower and a little softer. Generate both once into `packages/core/src/video_editor_core/assets/sfx/` and commit them (make sure the package data/paths resolve from the installed package the way other bundled assets do in this repo; look for an existing assets folder pattern). In from_scene_plan.py, whenever a scene's tally chip actually counts (its value differs from the previous tally), append an audio cue to the composition's audio list at absolute time = scene start + the tally delay already computed in `_tally_layers`, using coin_in for an increase and coin_out for a decrease, gain about −8 dB under the narration. Find the audio cue model the composition already uses for narration and music and reuse it, with a unique id per cue (e.g. `scene_3_tally_coin`). A plan-level `tally_sound: false` turns it off. Check that the chunked render's `mix_audio_cues` and the single-pass HyperFrames path both pick these cues up.
done-when: `.venv/bin/python -m pytest tests/test_tally_sound.py tests/test_adapter_scene_plan.py tests/test_chunk_media.py -q` passes, with tests that: the generated WAVs are 48 kHz mono, 0.3–0.6 s, peak between −6 and −1 dBFS; a two-scene plan whose tally goes 0 → 12000 gets one coin_in cue at scene start + delay; a decrease gets coin_out; an unchanged tally gets no cue; `tally_sound: false` gets none.
difficulty: medium
kind: change

## 3 Cost checklist strip under the tally
files: packages/core/src/video_editor_core/progress_strip.py, packages/core/src/video_editor_core/adapters/from_scene_plan.py, packages/pipelines/src/video_editor_pipelines/explainer_script.py, tests/test_progress_strip.py
needs: 2
do: The script's front matter may carry `progress: [<label>, ...]` (the ordered list of cost steps), and a scene may carry `progress: <label>` naming the step it covers. Parse both in explainer_script.py (a scene label must be one of the list's labels; error otherwise) and pass the list into each such scene's props so the renderer needs no plan-level lookup (e.g. `props["progress"] = {"items": [...], "current": "<label>"}`). In from_scene_plan.py add `_progress_layers`: for a scene with progress, one `kind="html"` layer (preset "progress"), a compact vertical checklist directly under the tally chip (same left margin as the tally, top just below the tally rect, width about 24% of the canvas), on the same dark translucent panel style as the tally. Items before the current one show "✓" and are dimmed; the current item is bold in the accent yellow with a "▸" marker that animates in at 0.3 s; later items are muted grey. Font sizes scale with width like calc_card. A scene without progress gets no strip.
done-when: `.venv/bin/python -m pytest tests/test_progress_strip.py tests/test_adapter_scene_plan.py -q` passes, with tests that the strip marks earlier items done, the current one highlighted, later ones pending, its rect sits below the tally rect and does not overlap it, and an unknown scene label is rejected by explainer_script.
difficulty: medium
kind: change
