# Open Issues and Closed Index

This is the active hand-off list. Only open work belongs here; fixed findings
are summarized in the closed index at the bottom and archived under
`docs/archive/issues/`. The forward plan (transitions, sound design, 2.5D
layering, film-look effects) lives in `docs/archive/plans/ROADMAP_2026-10-28.md`.

**Execution plan for the 2026-10-28 audit:**
`docs/archive/plans/2026-10-28-audit-remediation.md` (task ids `P1.1`…`P4.9`). Findings
and rationale stay in `docs/archive/audits/AUDIT_2026-10-28.md`; this file tracks state.

## Open work

### 2026-11-25 DeveeKitchen tiffin video — #374–#376

Found while re-setting up the tiffin teardown on stock footage (`../video-projects/videos/2026-11-25/tiffin-centre-teardown`).
Fixed the same day: the NVIDIA profile, multi-shot scenes, stock repeat and retry, the transparent caption overlay
(the old pass timed out and fell back to Telugu drawtext), AI labels on every provider, the shaped Telugu label, and
the judge budget. See the closed index and `RENDERING.md`.

- [ ] **#374 Footage pass is serial.** Each shot runs search → download → judge one after another, with up to 6 picks
  per rejected shot. This pass took 10–30 min of a 40 min tiffin render. Run the shots concurrently, within the stock
  APIs' rate limits.
- [ ] **#375 `ve explain` has no captions switch.** There is no `captions: off` script key and no soft-subtitle (`.srt`)
  export without a burn. The owner asked about dropping burned subtitles once.
- [ ] **#376 Caption chunks render one after another.** Each 30 s overlay chunk pays about 5 s of Chrome start-up;
  running 2–3 at once would cut the 9 min burn.

### 2026-11-24 tech tutorial (Python Decoder EP1) — #372–#373

- [ ] **#372 Tutorial screen track is per-project scripts.** `render_scenes.py` (web captures + editor/terminal
  scenes + section cards + callouts), `capture_outputs.py`, `make_sfx.py`, `mix_music.sh` and the channel bumpers live in
  `../video-projects` (`videos/2026-11-24/python-315-features/`, `brand/python-decoder/`). Port them into the engine as
  a `ve tutorial` command (scene YAML instead of Python cue lists) when a second video reuses them. Recipe:
  `docs/TECH_TUTORIALS.md`.
- [ ] **#373 Flow image generation is broken.** (2026-11-25: flow_image was removed from the automatic chain; Flow stills now go through Claude in Chrome as `file:` shots.) Google Flow renamed its agent toggle from "Agent" to "Flow agent";
  flow-mcp (`~/flow-mcp/src/flow.ts`, two `getByRole("button", { name: "Agent", exact: true })` locators) times out on
  every job, so the `flow_image` provider fails too. Fix: match `/^(Flow agent|Agent)$/` and rebuild `dist/`. The
  cheap worker needs `npx cheap-agent allow` in `~/flow-mcp` first. Workaround used: Claude in Chrome in the user's
  Chrome (uploads via flow-mcp's Chrome on CDP 9333, since the extension cannot answer a file picker).

### 2026-11-13 devotional chant video — #370–#371

Found while making Om Saravana Bhava ×108 (see `DEVOTIONAL_CHANTS.md`).

- [ ] **#370 `agy_image` rejects every image agy 1.5.7 makes.** (2026-11-25: agy was removed from the automatic image chain; this fix only matters if it is named explicitly.)
  - agy now produces the image, but saves it at
    `<profile>/.gemini/antigravity-cli/brain/<session>/<name>_<epoch>.jpg` instead of the requested
    `<turn workspace>/image.png`.
  - `AGYImageError ... refusing an unexpected file` then follows, 5 times out of 5, and each image falls
    through to `flow_image` after about 3 minutes.
  - Fix: accept a path inside this run's own profile `brain/` folder (resolved, no symlink escape) that is
    new in this turn. Verify it the same way as the expected file, then copy it to the requested path. Keep
    refusing every other path.
  - Workaround: copy the images out of `brain/` by hand.
- [x] **#371 One ffmpeg graph with about 30 looped image inputs ran out of memory.**
  - A single `filter_complex` with 30 `-loop 1` 2880-px inputs and a chain of 29 xfades reached about 24 GB
    in 45 s and was OOM-killed, taking the Claude session with it.
  - The project script now renders in pieces: per-segment clips, per-transition clips, the concat demuxer,
    then one overlay pass at about 1.5 GB.
  - Long renders run detached under `systemd-run --user --scope -p MemoryMax=14G`.
  - The engine's own renderer was not involved. Apply the same rule if a template ever chains many image
    inputs.

### 2026-11-12 stock-first explainers and map stories — #351–#369

Built for the chai-stall breakdown and the Godavari map reel (see
`STOCK_EXPLAINERS.md`, `MAP_STORIES.md`). Fixed the same day:

- [x] **#351 Telugu alignment was English-only.** `align_words` and the caption-token gate kept `[a-z0-9]`, so
  Telugu words had no times (0% captions) and false "lone word" failures. Both keep letters, marks and digits
  of any script now.
- [x] **#352 Whisper cannot time synthetic Telugu.** Turbo and large-v3 skipped whole passages.
  `narration_mode: per_scene` times scenes from measured clip lengths.
- [x] **#353 Pixabay and Pexels refused real videos.** Both took the 4K file and saved it under the 20 MB image
  ceiling. They now take the smallest file of at least 1080p, under the video ceiling.
- [x] **#354 SFX made build cost unmeasurable.** Freesound results named no provider. Results fall back to the
  request's provider, and SFX are priced per second.
- [x] **#355 ElevenLabs over-priced by block rounding.** Per-character providers are now priced by their
  characters; only Sarvam keeps per-call blocks.
- [x] **#356 `voice_pace` never reached ElevenLabs.** It is sent as `speed` (0.7–1.2) for ElevenLabs voices.
- [x] **#357 Truncated calc expressions passed unchecked.** A comma inside `{...}` cut `₹3,081 ÷ 5` to `₹3`. Rows
  with stray keys are refused, and a bare figure must equal its amount.
- [x] **#358 HyperFrames dropped the calc expression column.** A child with its own `opacity` inside an animated,
  clipped row is not drawn there. Dimmed by colour instead.
- [x] **#359 Tally chip showed two values on a step boundary.** Steps now switch on just after the previous one
  ends.
- [x] **#360 Close map zooms went blocky.** `will-change: transform` on the stage and camera stretched one bitmap.
  Removed; map renders are about 27% slower.
- [x] **#361 Short journey legs zoomed in too far.** Follow-zooms keep `min_span: 5` degrees.
- [x] **#362 Telugu map headline clipped.** `padding-bottom` on the headline gives below-line marks room.
- [x] **#363 Place label unreadable over a photo.** Scenes with photo regions back their labels.
- [x] **#364 Map photos counted as scene stills.** `counts_as_scene_still` skips map photos and footage
  fallbacks.
- [x] **#365 Author colours reached SVG attributes unchecked.** `safe_css_color` validates journey and region
  colours, and they are escaped at the sink.

Still open:

- [ ] **#366 Overlapping bubbles and stickers.** Placement is manual, and nothing nudges overlaps apart.
- [ ] **#367 Coverr and Mixkit providers are dead.** Coverr returns 401 without a key and Mixkit returns 404.
  They are out of the default stock chain until fixed or keyed.
- [ ] **#368 Stock photos are not vision-checked.** `image_query` takes the first hit; only footage clips are
  judged.
- [ ] **#369 AI pill can be masked.** The "AI image" pill can fall outside a district's photo mask. Disclose AI
  images in the description.

### 2026-11-11 follow-ups — #350

- [x] **#350 Explainer estimate under-priced ElevenLabs narration.** `editorial-pilot-malacca-3` was estimated
  at $0.265 and spent $0.305 (ledger: `elevenlabs_tts eleven_v4 $0.08`). The estimate's default model was still
  `eleven_flash_v2_5` ($0.04/1k chars) after the provider default became `eleven_v4` ($0.08/1k) on 2026-10-30.
  Fixed 2026-11-11: `pricing.DEFAULT_PROVIDER_MODELS` now imports the ElevenLabs, Sarvam and PoYo image defaults
  from their provider modules (`models.yaml` estimate defaults updated to match); `tests/test_pricing_defaults.py`.

### 2026-11-10 Gemini audit of india-geography — #338–#349

Verified plan and evidence: `docs/archive/audits/IMPROVEMENT_PLAN_2026-11-10.md` (source: Gemini's "Video Editor Bug Audit
Report"; supersedes the inferred root causes in `docs/archive/audits/AUDIT_IMPROVEMENT_PLAN_2026-11-10.md`). Repo-size
proposal: `docs/archive/audits/REPO_SPLIT_PROPOSAL_2026-11-10.md`.

- [x] **#338 Glued caption words** ("kilometreslong."): the active-word pop scaled a word 1.12× over the
  space beside it. Fixed 2026-11-10: `ve_word_pop` is now a `translateY(-0.12em)` lift, no horizontal growth.
- [x] **#339 Standalone "—" dropped from captions**. Fixed 2026-11-10: `_words_with_spans` attaches a
  letterless token to the previous word (or the next, at the start); timing unchanged.
- [x] **#340 Orphan caption words**. Fixed 2026-11-10: `_group_words_to_lines` keeps the hard breaks and
  greedy's frame count, then re-partitions each sentence with a small DP (balanced widths, lone short word
  penalised, clause breaks preferred). `text-wrap: balance` was not needed: each frame fits one row.
- [x] **#341 White captions on cream plates**. Fixed 2026-11-10: `burn_captions` measures the caption band
  of the uncaptioned render (`band_luminance`, 4 fps) and `captions.theme.theme_for_backdrop` switches a line
  to charcoal text, amber active word and a light halo when the preset colour is under 3:1. New QA gates:
  `caption_tokens` (caption words vs script, lone short frames) and `caption_contrast`; per-line records in
  `caption_lines.json`. Follow-up fixed 2026-11-11: on mid-bright frames (luminance ~0.15–0.30) white text
  passed but the gold active word fell under 3:1. The theme now lifts only the active word to a lighter gold
  (`LIGHT_HIGHLIGHTS`, falling back to the text colour), and `caption_contrast` checks the active word too.
- [x] **#342 Map marker in the sea**. Fixed 2026-11-10: a bundled gazetteer (`geo.find_place`, Natural Earth
  1:50m populated places) lets markers name a place without coordinates (scene country tried first); markers
  within 15 km offshore snap onto the coast (`geo.snap_to_land`); `sea: true` exempts sea labels; new
  `map_markers` QA gate fails any marker still in the sea.
- [x] **#343 Official India boundaries**. Fixed 2026-11-10: `geo/worldview_ind.json` (Natural Earth India
  point-of-view edition, India and 8 neighbours, simplified to 190 KB); `worldview: ind` is the default when the
  map's country is India, `worldview: none` opts out.
- [x] **#344 Feature zooms**. Fixed 2026-11-10: `zooms: [{to: Ganga}]` resolves a marker, region, river
  or country, pads it and fits it to the panel aspect, centred. A feature split at the antimeridian fails
  closed (use `bbox`). Note: a long range (Western Ghats) fitted whole gives a gentle push; use `bbox` for a
  tighter crop.
- [x] **#345 Tight cut tail**. Fixed 2026-11-10: `align_words` keeps each word's `spoken_end`; after a
  pause under 0.5 s the cut goes in the silence with at least 0.15 s after the last word, never before it.
  New `sentence_cuts` QA check; a pause too short for tail plus lead-in (a spoken run-on) is reported, not
  failed. The india cut after "east." moved from 12.40 s to 12.45 s.
- [x] **#346 Toponym pronunciation** (mechanism). Fixed 2026-11-10: `pronunciations:` front matter in
  explainer scripts changes only the TTS text (`ExplainerScript.spoken_narration`); captions and cues keep the
  display words. Heard on india-geography-1 (two ElevenLabs takes, $0.16): with `Kutch: Kuhtch`,
  `Sundarbans: Shoon-dor-bon`, `Thar: Tar` the final take passes all 26 QA checks; the recogniser hears
  "Shundurban" and "Tar", but "Kuhtch" came out as "Kooch" on one take and "Kutch" on the other, so that
  respelling is not reliable on eleven_flash_v2_5. Each take varies (the first had a 1.46 s pause and failed
  silent_gaps). Sarvam take (`sarvam:sarang_en_narration`, bulbul:v4-flash, no respellings, ~$0.03): the
  recogniser hears "Ran of Kutch", "Thar desert" and "Sundarbans" correctly as written, so the
  `pronunciations:` block is not needed with an Indian-English voice. That take is slower (100.2 s narration),
  so it fails the 30–90 s duration gate. Fixed with a new `voice_pace:` front-matter field (0.5–2.0, sent as
  Sarvam's `pace`): at `voice_pace: 1.2` the narration is 83.2 s, the video 84.2 s, and all 26 QA checks pass;
  Kutch, Thar and Sundarbans still transcribe correctly. "Ghats" transcribes as "karts" on both Sarvam takes,
  probably the small.en recogniser on the Indian pronunciation; worth a listen.
- [x] **#347 Static typographic plates**. Fixed 2026-11-10: `big_stat` numbers count up (tabular figures,
  literal kept exactly; formats the counter cannot reproduce stay static), `definition` glosses reveal word by
  word, and both draw an accent rule growing over the whole scene.
- [x] **#348 Repeated concept across scenes**. Fixed 2026-11-10: `repeated_concepts` advisory (two distant
  scenes with the same headline or term) in the `advisories` check, plus a planner prompt rule. The india
  script's monsoon repeat is now reported.
- [x] **#349 Range pattern reads crude**. Fixed 2026-11-10: new `relief` style (soft staggered hills with
  a light shadow fill) is now the default for mountain ranges; `style: range` keeps the ridge hatch.

### 2026-11-10 editorial scenes audit — #329–#337 (all fixed 2026-11-10)

Findings and evidence: `docs/archive/audits/AUDIT_2026-11-10.md` (section 8 has the resolutions). Baseline
`feat/editorial-scenes` at `fcee80a`; evidence was the shipped pilot
`outputs/editorial-pilot-malacca-3/final.mp4` and, for #337, `outputs/india-geography-1/final.mp4`.

- [x] **#329 Map strokes and the focus pin grew with the camera zoom.** Outlines use non-scaling strokes;
  the dashed route's width is animated against the camera scale with interior stops (within ~3% of
  4.6 px through a 4.9× move); the focus pin is an overlay anchor. `7686eb6`.
- [x] **#330 Map art collided with the title block once zoomed.** Headline and subtitle clamp to two
  lines and the portrait map frame starts below the tallest title block (y 470 → 516); the edge fade is
  on a non-scaling `.map-fade` wrapper, transparent on all four edges; zooms past 2.5× draw Natural Earth
  1:50m outlines (`geo/world_50m.json`, `scripts/compact_natural_earth.py`). `7686eb6`. Frame checks of
  the india-geography re-render then fixed a clipped label, a fill halo and an edge hairline: `e34d161`.
- [x] **#331 `focus` pin and markers drew two pins.** A scene with markers draws no focus pin unless it
  sets `focus_pin: true`. `7686eb6`.
- [x] **#332 Dropped zooms and switched-off cues left no record.** `render_map` records
  `dropped_zooms` and logs `map_zoom_dropped`; the topic drafter logs `cue_silenced` and writes
  `build/silenced_cues.json`; both appear in an always-passing `advisories` QA check. `d1251d2`.
- [x] **#333 Captions dropped spelled-out numbers.** Script words wholly inside an unmatched gap are
  spread across the recognised words covering it; offsets inside a merged STT token are timed by
  character position. Malacca pilots: zero-length words 2 → 0 and 2 → 0. `9fcbdc2`, `5e52853`.
- [x] **#334 No QA gate looked at a plate mid-motion.** New `map_motion` check (pure geometry, no
  render): after each camera move lands, pins, labels and the focus pin must stay inside the map frame
  (anchors outside the move's own zoom box are treated as leaving the shot) and marker labels must not
  overlap. `d1251d2`.
- [x] **#335 `test_musical_parity` failed on `main`.** A one-frame musical tail is folded into the last
  scene. `957c8ce`.
- [x] **#336 Repo-wide lint drift.** 0 ruff findings, every file formatted, behaviour unchanged. `6e02ae2`.
- [x] **#337 CPU transcription fallback dropped a sentence.** Without CUDA, settings fell back to
  `small`, which skipped a line repeating the previous line's words (india-geography: 7.08 s gap, scene
  5's map ~6 s late). The fallback now keeps `large-v3-turbo` (71 s for 78 s of audio, every word).
  `df91da2`.

### 2026-11-01 Drama video stage v2 — #323–#328

Plan: `../drama-studio/docs/DRAMA_PIPELINE_V2_2026-11-01.md` (design record). **2026-11-02:** Ep1 v2 shipped on PoYo + Flow instead of
RunPod (`../drama-studio/docs/DRAMA_PRODUCTION_PLAYBOOK.md`), so #323/#325/#326/#327 only matter if a self-hosted route returns;
#324 (soft 1080p) still applies to the PoYo/Flow 720p clips.
Evidence: `../drama-studio/docs/DRAMA_VIDEO_STACK_RESEARCH_2026-11-01.md`. Found in the Ep1 restart
(`videos/2026-10-30/mantra-rahasyam-ep1/`).

- [x] **#323 Motion clips judder.** `runpod-comfyui/workflows/wan22_i2v.json` saves at 16 fps (node 16) and
  `edit/assemble.py` conforms with `fps=25`, repeating frames unevenly. Fix: RIFE (existing
  `interpolate.json`) to ≥ 25 fps before assembly; any lip-sync repair runs *after* interpolation.
  Parked 2026-11-11: the self-hosted Wan/InfiniteTalk route is retired (Ep1 v2 shipped on PoYo + Flow) and the local GPU (RTX 3050, 4 GB) cannot run these models; reopen only if that route returns, on Modal via `runpod-comfyui`. The RIFE graph (`workflows/interpolate.json`) is ready for it.
- [x] **#324 Soft 1080p master.** All clips are 1280×720 and `assemble.py` lanczos-scales them to 1920×1080.
  Fix: SeedVR2 (or FlashVSR) video upscaler in a finishing stage; never per-frame image upscalers.
  2026-11-04: SeedVR2 3B tried through Freefield on Modal (H100): 720p → 1440p with real detail, 262 s for
  a 5 s clip (≈ $0.06 per output second), peak 21.9 GB; flicker not yet checked. `../tool-lab/trials/2026-11-04-freefield/README.md`.
  *Still open in Ep1 v2:* Veo/OmniHuman/Kling clips are 720p, lanczos-scaled in `edit/v2_assemble.py`.
  Fixed 2026-11-11: `rpc run seedvr2_upscale --modal --set video=clip.mp4` in `runpod-comfyui` (SeedVR2 3B int8, ComfyUI 0.42 core nodes, Modal L40S). Ep1 `clips/lipsync/v05.mp4` 720p → 1440p in 330 s cold (≈ $0.18, ≈ $0.055 per output second), audio and fps passed through; face detail (stubble, eyes, teeth) is real and identity-safe. Flicker measured, not eyeballed: detail added on a static door changes 0.50 grey levels per frame against 0.11 for bicubic (under 1/255, invisible); on v07 the added detail is as stable as the source's. Wiring it into an episode's assembly is drama-studio's step.
- [x] **#325 Damped Wan motion.** LightX2V v1 4-step at strength 1.0 on both experts with cfg 1 and the
  graph's generic prompt. Fix to test blind: high expert LoRA 0.6–0.8 + cfg 2–3.5, per-shot motion prompt;
  always 81 frames.
  Parked 2026-11-11: the self-hosted Wan/InfiniteTalk route is retired (Ep1 v2 shipped on PoYo + Flow) and the local GPU (RTX 3050, 4 GB) cannot run these models; reopen only if that route returns, on Modal via `runpod-comfyui`.
- [x] **#326 InfiniteTalk animates every face.** Test WanVideoWrapper `ref_target_masks` with a silent
  listener track before routing two-face shots to OmniHuman.
  Parked 2026-11-11: the self-hosted Wan/InfiniteTalk route is retired (Ep1 v2 shipped on PoYo + Flow) and the local GPU (RTX 3050, 4 GB) cannot run these models; reopen only if that route returns, on Modal via `runpod-comfyui`. Two-face dialogue already routes to OmniHuman.
- [x] **#327 Serverless endpoints** `ep-talk`, `ep-motion`, `ep-finish`: built images, network volume in a DC
  with 48 GB stock, outputs to object storage (`BUCKET_*`), per-endpoint timeouts.
  Parked 2026-11-11: the self-hosted Wan/InfiniteTalk route is retired (Ep1 v2 shipped on PoYo + Flow) and the local GPU (RTX 3050, 4 GB) cannot run these models; reopen only if that route returns, on Modal via `runpod-comfyui`. Modal (`rpcomfy.modal_app`, per-workflow GPUs, ~30 s cold start) replaced the RunPod endpoint plan.
- [x] **#328 Licence check before production:** LatentSync's face processing reportedly uses InsightFace
  models (non-commercial research licence); LTX-2.x bars competing services. Settle before either ships.
  Decided 2026-11-11: **LatentSync is not used for published work** while its face alignment depends on InsightFace landmarks: InsightFace's pretrained models are licensed for non-commercial research only (insightface.ai licensing page), so a monetised channel would need InsightFace's commercial licence or a different aligner. **LTX-2.x** weights are under the LTX community licence, free for commercial use below $10M annual revenue (group-wide); this channel is far below it and is not a competing service, so LTX is usable (it smiled through grief lines, so it is not routed for those anyway). Neither is in the current production route (OmniHuman/Kling on PoYo, Flow).

### 2026-10-29 Tamil drama series — Route B and #319–#322

Decision and plan: `../drama-studio/docs/DRAMA_FEASIBILITY_2026-10-29.md` rev 5, §11 and §12 (first
full episode: `videos/2026-10-29/mantra-rahasyam-ep1/`, ~$4.66). Dialogue
runs on self-hosted InfiniteTalk (blind-tested level with OmniHuman) through the
separate `/mnt/data2/git_repos/creative_repos/runpod-comfyui` repo (`rpc` CLI, `rpcomfy`
client); OmniHuman on PoYo is the per-line fallback. Test kit and blind results:
`videos/2026-10-29/drama-test-10s/README.md`.

- [x] **Route B engine work** (§11, 2–4 days; 720p dialogue on an RTX PRO 6000 by default): RunPod lip-sync provider wrapping
  `rpcomfy`; trim to audio length; per-line emotion prompts; InfiniteTalk →
  reroll → OmniHuman fallback with ledger entries; route dialogue shots with more than
  one person in frame straight to OmniHuman (InfiniteTalk animates every face; blind 3/3, 2026-10-29); per-episode review sheet;
  **six user review gates: script → dialogue → audio → images → videos → lip-sync + SFX,
  then the stitch** (user rules, 2026-10-29 and 2026-10-30). Standalone gate in `scripts/drama_review.py`;
  port it into the pipeline.
  Depends on the shared work in §8 (per-line speakers, #318, OmniHuman provider,
  screenplay → shot-list planner).
  Parked 2026-11-11: superseded by the PoYo + Flow route (`DRAMA_PRODUCTION_PLAYBOOK.md`); the review gates already live in `../drama-studio`. Reopen with the self-hosted route.
- [x] **ElevenLabs is voice-over only; it is now the default voice-over** (user rule, 2026-10-30).
  - `tts_router` sends every unprefixed voice to ElevenLabs `eleven_v4`, with native Telugu/Tamil narrators by
    default; a `sarvam:`/`kokoro:` prefix still picks that provider. `PackVoice` defaults to `elevenlabs_tts`.
  - `providers/elevenlabs_policy.py` refuses the ElevenLabs music, SFX, forced-alignment, isolation and
    speech-to-speech providers, both in the manager and inside each provider. Defaults moved to `poyo_music` and
    `freesound_sfx`; alignment, isolation and speech-to-speech have no default.
  - Open: no non-ElevenLabs forced aligner, so musical-video lyric captions are skipped unless a provider is given.
- [x] **Remote review board** (2026-10-30): `apps/drama-review` (Cloudflare Worker, R2 + D1) and
  `scripts/drama_board.py`; personal reviewer links, per-item verdicts tied to hashes, owner sign-off feeds the gate.
  Reference images join the images gate; items carry captions.
- [x] **Google Flow automation** (2026-10-31): `flow-mcp` reviewed and installed at `~/flow-mcp` (pinned `84b548f`),
  `scripts/flow_call.py`. Rules: ordinary motion on Wan (RunPod); extraordinary shots on Flow Omni 1.1 Flash.
- [x] **Port the episode image routing into the engine**: Kling O3 default, Qwen for blood or dark beats, Nano Banana
  Pro for redos; per-shot wardrobe and face-crop references; everyday vs divine style (episode `edit/frames.py`,
  feasibility §13).
  Moved 2026-11-11: this is drama pipeline logic and belongs in `../drama-studio` (episode `edit/frames.py`), not the engine.
- [x] **Paid-task persistence in every PoYo caller**: save task IDs when accepted and resume them. Two episode scripts
  lost paid tasks before this was added; PoYo cannot list past tasks.
  Fixed 2026-11-11 in the engine: `providers/poyo_tasks.py` journals each accepted task next to its output (`<file>.poyo-task.json`, keyed by a payload hash); `poyo_ai` and `poyo_music` resume a journalled task instead of resubmitting after a poll timeout or crash, clear it on download or on a PoYo-reported failure. Episode scripts that call PoYo directly still need the same (drama-studio).
- [x] **Kling `elements` fails for two people on PoYo** ("server exception"); plain references work. Report to PoYo or
  retest later.
  Parked 2026-11-11: needs a paid retest; plain references work and are what the route uses.
- [x] **#319 MEDIUM** wrong PoYo model id for budget motion. `wan-2-2-fast` is used by
  `storyteller_planner.py:229`, `data/models.yaml`, `data/pricing.yaml` and
  `pricing.py`, and has no alias mapping in the PoYo provider. PoYo's catalog id
  is `wan2.2-image-to-video-fast` (720p $0.06 per 5 s generation, 32 fps, no
  duration parameter), which the drama test used. Not yet confirmed whether PoYo
  rejects the old id or aliases it; check, then rename and fix the docs that
  quote it (`templates.md`, `Templates_capabilities.md`,
  `prompt_for_optimal_results.md`).
  Fixed 2026-11-11: catalog check (`poyo describe`) confirms `wan2.2-image-to-video-fast` and rejects `wan-2-2-fast` (`model_capability_not_found`). Renamed everywhere; priced per request by resolution (480p $0.03, 720p $0.06) via a new `cost_per_generation` branch in `estimate_video_cost`; the PoYo provider no longer sends `duration`, `aspect_ratio` or `generate_audio` to the fixed-length Wan 2.2 Fast models.
- [x] **#320 LOW** Sarvam `bulbul:v4-flash` price ~2× too high: `pricing.py`
  has $0.023 per 1K chars (≈₹2); Sarvam lists ₹1 per 1K for V4 Flash. Budget
  gates over-reserve, nothing under-bills.
  Closed 2026-11-11, not a bug: the $0.023 rate is invoice-verified (657 chars billed as one $0.023 call, see `pricing.yaml`); the ₹1 public figure is not what the account is charged.
- [x] **#321 LOW** Sarvam TTS writes WAV bytes under any extension:
  `sarvam_tts.py:193` decodes the returned audio and writes it to `output_path`
  without transcoding, so a caller that names the output `.mp3` (seen with the
  `sarvam_clone:` voice path) gets WAV data in an `.mp3` file. Either force a
  `.wav` suffix or transcode.
  Fixed 2026-11-11: confirmed on the india-geography takes (WAV bytes in `.cache/assets/*.mp3`). `sarvam_tts.write_audio` converts WAV to the requested extension with ffmpeg; `.wav` targets are written as-is.
- [x] **#322 MEDIUM** ffmpeg `drawtext` cannot shape Telugu (and likely other
  Indic scripts): conjuncts such as మంత్ర render broken even with
  `text_shaping=1` (ffmpeg n8.1.3, harfbuzz enabled). Any caption or title burned
  with drawtext in an Indic language is wrong. Workaround used in the episode:
  render text in headless Chrome. Fix: route Indic text through the HyperFrames
  (Chrome) renderer, and add a glyph-shaping QA check.
  Fixed 2026-11-11: reproduced on ffmpeg 6.1 with HarfBuzz and Noto Sans Telugu (`text_shaping=1` still splits ్ర and స్య). New `video_editor_core.shaped_text` draws complex-script text with Pillow + libraqm into a PNG and splices it into the chain (`movie=` + `overlay`); thumbnails, the graphics fallback and the motion-graphics fallback use it, Latin text keeps drawtext. `tests/test_shaped_text.py` asserts shaping actually joins the conjuncts.

### 2026-10-29 long-form gap analysis — #311–#318

Rationale and evidence: `docs/archive/audits/AUDIT_2026-10-29.md` (the Cosmic Saju
"Reading 8 Characters" 10-minute 16:9 spec).

- [x] **#311 HIGH** chart scenes. Closed 2026-10-29: `PackScene.graphic`
  (component/data/`over`) renders one static html layer, and the component
  library covers `four_pillars`, `element_bars`, `decade_ribbon`,
  `medallion_ring`, `strength_gauge`, `feeding_cycle`, `sixty_cycle_rings` and
  `countdown`; preflight fails closed on unknown names/data.
- [x] **#312 HIGH** creator's cloned voice via Sarvam voice cloning. Landed
  2026-10-29: `ve voice-clone create/list/delete` (multipart `/voices/create`,
  stored under `<data>/cache/voice_clones/`, preview line); the
  `sarvam_clone:<voice_id>` prefix routes `SarvamTTSProvider` to
  `POST /voices/clone` with a 1,000-char guard; `PackLine.pause_after` renders
  measured silences; a `sarvam_clone` pricing placeholder is in
  `models.yaml`/`pricing.py`.
- [x] **#313 MEDIUM** named scene transitions. Closed 2026-10-29:
  `PackScene.transition_out` (with `transition_seconds`) maps to the next
  scene's entrance; `crossfade`, `fade`, `ink_bleed`, `mist_dissolve`,
  `push_through`, `hanji_turn` and `seal_stamp` are emitted from seek-safe
  primitives (ink bleed uses the circle mask, not turbulence).
- [x] **#314 MEDIUM** multi-shot scenes. Closed 2026-10-29: `PackScene.video`
  renders a `media_kind: video` layer, and `PackScene.shots` splits a scene on
  aligned line starts (`at_line`) with still or video per shot.
- [x] **#315 MEDIUM** long-form as chapter packs. Closed 2026-10-29:
  `longform.yaml` + `ve longform` renders each chapter as its own job
  (`<job>-chNN`, per-chapter QA), joins with a cut or a crossfade and runs one
  measured QA pass on the joined file.
- [x] **#316 LOW** YouTube delivery. Closed 2026-10-29: `captions.mode:
  sidecar` writes `final.srt`; `youtube_end_screen: true` gates the end-screen
  zone; `ve musical --swell at:db:duration` applies music gain points.
- [x] **#317 LOW** generated BG/MG/FG stack. Closed 2026-10-29:
  `layers: generated` with three prompts, FFmpeg colorkey on MG/FG, and a
  depth-graded parallax (0.15/0.45/0.9).
- [x] **#318 LOW** image edits / style references (same provider blocker as
  P4.8).
  Fixed 2026-11-11: PoYo now serves references (`nano-banana-2-lite-edit`, `gpt-image-2-edit`, same price; `nano-banana-2.1`, `qwen-image-2.1` directly). Packs take `image.style_reference`, `still.characters` and `still.edit_of`; `short_job_requests` sends `reference_images`, `generate_stills` runs edits after their source, and the PoYo provider hosts local files on fal storage (`providers/_hosting.py`, cached by hash) and switches to the reference variant. Live check: an edit of Ep1 v26 kept Lakshmi's face, saree, bindi, necklace and bangles in a new pose ($0.025). `tests/test_reference_images.py`; usage in `CLI_USAGE.md`.

### 2026-10-28 audit — #298–#310, A9–A13

Rationale and evidence: `docs/archive/audits/AUDIT_2026-10-28.md`. Task ids refer to
`docs/archive/plans/2026-10-28-audit-remediation.md`.

**HIGH — money or safety**

- [x] **#298** blocking dispatchers starved the worker heartbeat, so a live job
  could be reclaimed and run (and billed) twice. Closed 2026-10-28: handlers run
  off the loop, `lease_id` guards heartbeat/complete/fail, and `JobLockError`
  releases the claim without a retry. Tests: `test_worker_heartbeat.py`,
  `test_worker_lock_release.py`, lease cases in `test_queue*.py`. — P1.1, P1.2,
  P1.3
- [x] **#299** MCP `run_command` bypassed the root and budget guards and ran
  sync renders on the stdio loop. Closed 2026-10-28: commands declare
  `mutates`/`spends`, `run_command` refuses either, `config_path` is refused,
  and sync registry handlers run in a thread. — P1.4

**MEDIUM — robustness or compliance**

- [x] **#300** Openverse sent its bearer token to arbitrary audio hosts.
  Closed 2026-10-28: separate unauthenticated download client, https-only,
  private/loopback addresses refused, redirects followed. — P1.5
- [x] **#301** Freesound trusted `results[0]`. Closed 2026-10-28: explicit
  CC0/PDM/CC BY 3.0–4.0 allowlist, duration re-check, `Authorization: Token`,
  no key in the query string. — P1.5
- [x] **#302** the short manifest lagged `qa.json`. Closed 2026-10-28:
  `_stage_done` saves after `layerize`, `voice` and `qa` (pass or fail); a
  copied manifest is replaced by the local run. — P1.6
- [x] **#303** `ve short` and `ve musical` had no worker handler and no MCP
  tool. Closed 2026-10-28: `short`/`musical` handlers (`short` requires a
  prior estimate), guarded MCP `run_short(pack_path, approve_usd)`, payload
  schemas, and the parity matrix in `docs/CLI_USAGE.md`. — P2.3
- [x] **#304** the A8 error policy is advisory only. Closed 2026-10-28:
  `BLE001`/`S110` are enabled, 50 sites carry a named noqa, and
  `scripts/audit_broad_except.py` reports 0 unannotated (146 broad handlers,
  14 re-raise cleanup guards). — P1.7

**LOW — hygiene and drift**

- [x] **#305** media downloads buffered whole responses with no byte cap.
  Closed 2026-10-28: index downloads (`Openverse`, `Freesound`) stream to a
  temp file with a per-type ceiling and `os.replace`; buffered provider
  responses are capped and written atomically. — P3.6
- [x] **#306** the layerize cache was per job. Closed 2026-10-28: cache under
  `<data>/cache/layerize`, a `.done` marker written last, and stale temp dirs
  swept; a partial entry rebuilds. — P3.5
- [x] **#307** the `ve worker` docstring and in-memory warning were stale, and
  `packages/pipelines` printed with `typer.echo`. Closed 2026-10-28: both
  docstrings and the warning are fixed; `run_short_job` and `run_explainer_job`
  return `qa_text` for the CLI to print; the estimate UI is in
  `estimate_ui.py`; and `typer` is a forbidden import for
  `packages/pipelines` (import-linter contract). — P3.1
- [x] **#308** `_add_sfx` built an uncapped manager. Closed 2026-10-28: the
  SFX chain gets `BudgetGovernor(limit=0.0)` with the price registry, so a
  priced provider fails closed instead of spending outside the estimate. — P3.4
- [x] **#309** a corrupt `requirements.json` was swallowed. Closed 2026-10-28:
  `load_requirements` raises `VerificationError("requirements.json is
  invalid: …")`. — P3.2 (landed early)
- [x] **#310** probe failures were replaced with the plan's duration. Closed
  2026-10-28: `render.run` returns `duration_seconds: null` plus
  `duration_error`, never a substitute. — P3.3 (landed early)

**Architecture**

- [x] **A9** the async/sync boundary had no rule. Closed 2026-10-28: the
  sync-core/async-edge rule is documented in `ARCHITECTURE.md`, handlers run
  through `asyncio.to_thread`, and the heartbeat watchdog test guards it.
  `run_async` is kept as an explicit direct-call compatibility path. — P1.1,
  P2.1
- [x] **A10** MCP had two command surfaces with different safety models. Closed
  2026-10-28: `TrustLevel` (`CLI`/`CONTROL`/`MCP`) in
  `video_editor_state/trust.py`; the registry applies containment, filename and
  `config_path` policy to untrusted callers, MCP is read-only, and the control
  channel cannot approve spend. — P1.4, P2.2
- [x] **A11** front-end parity is untracked. Closed 2026-10-28: matrix in
  `docs/CLI_USAGE.md` and `tests/test_short_queue.py` asserts the production
  paths have handlers + payloads + an MCP entry point. — P2.3
- [x] **A12** the creative layer had a fixed vocabulary. Closed 2026-10-29:
  `wipe`, `mask_reveal`, `camera`, `type_on`, `morph` (property morph) and
  `cursor` (offset-path markup) join `draw`/`count`; the sandboxed `html:` shot
  is the escape hatch. True FLIP remains an authoring approximation.
- [x] **A13** run data lived in the checkout (6.3 GB). Closed 2026-10-28:
  `projects/` (4.9 GB) and `outputs/` (264 MB) moved to
  `/mnt/data2/video-editor-data/` with symlinks; `.env` sets
  `VIDEO_EDITOR_DATA_DIR`. The tracked `projects/ad_biodata` demo files were
  untracked; `videos/` stays by convention.

**Creative roadmap (§5.3)** — separate track, gated on P1:
- [x] look-ban gate (P4.1) and director's-brief fields (P4.2), 2026-10-28.
- [x] `ve critique` (P4.3), `ve style-from` (P4.4), `ve brand-capture`
  (P4.5), beat grid (P4.6), UI motion primitives (P4.7), sandboxed HTML layer
  (P4.9).
- [x] character bible (P4.8) — blocked on provider reference-image support
  (#318).
  Done 2026-11-11 with #318: `characters[].reference_images` are sent with every still that names the character. The face-similarity QA is deliberately the review board's images gate: InsightFace-style embeddings are non-commercial (see #328).

### `agy` live verification

- [ ] **Live image leg is blocked by quota.** `agy` 1.4.8 preflight, stream
  framing and the restricted `PreToolUse` guard were verified, but the account's
  `generate_image` quota returned `429 RESOURCE_EXHAUSTED` (reset ~67 h), so a
  real PNG could not be produced during the phase 3 pass. Re-run
  `VE_AGY_INTEGRATION=1 uv run pytest -m integration tests/test_agy_image_integration.py -v`
  after the reset.
  2026-11-11: agy 1.5.4 broke the provider, not the quota: it hands `generate_image` to an "Image Generator" subagent, which the profile guard blocked, so agy reported SUCCESS with no file. Fixed (guard allows `invoke_subagent` for that exact role plus `send_message`/`receive_message`; prompt asks it to wait; provider waits up to 120 s in a session). Verified live with a kept profile: the subagent runs and calls `generate_image`; the PNG is now refused only by quota (`gemini-3.1-flash-image` RESOURCE_EXHAUSTED, reset ~4 h, used up by today's debugging). Still open until one PNG lands; rerun the integration test after the reset. Free fallback now in the chain (2026-11-11): `flow_image` (Google Flow through flow-mcp, Nano Banana, 0 credits, `max_credits: 0`), tried after agy and before paid PoYo. Live: a Lakshmi reference still at a temple doorway, 109 s, 1376×768, character kept; Flow adds its sparkle mark bottom-right. Needs a project open in flow-mcp's Chrome (profile `~/.flow-mcp/chrome-profile`, CDP port 9333), else it steps aside. 2026-11-13: agy 1.5.7 does produce images now; the file lands in the profile's `brain/` folder and is refused (#370).

### Phase 5 — layering and CLI (A1, A2, A8)

- [x] **A1 models:** moved `video_editor_engine.models.*` into
  `video_editor_core.models`, with deprecation shims in the engine.
- [x] **A1 orchestration:** `SkillsPipeline`, `MusicVideoPipeline`, the
  music-video planner, the explainer/short pipelines and `handlers.py` now live
  in `packages/pipelines`; `explainer_topic.py` and the scene planner moved too.
- [x] **Layering enforcement:** `import-linter` contracts keep MCP off the CLI,
  pipelines off apps/MCP, and skills off apps/pipelines/MCP; CI runs
  `lint-imports`.
- [x] **A2 CLI split:** the command groups are in `commands/{short,explain,run,
  queue,assets,ledger,bridges,misc}.py`; `main.py` is down from 3,346 to 589
  lines (registration, queue dispatch and compatibility re-exports), and the
  `E402` ignore is gone.
- [x] **A8 error policy:** documented in `docs/ARCHITECTURE.md`.
- [x] **A8 site audit:** the broad handlers are categorized in
  `docs/ARCHITECTURE.md`; output/spend paths raise or mark degraded, and the
  rest are annotated best-effort side channels.

### Phase 6 — convergence

- [x] **A7 queue automation (2026-10-27):** `JobMessage.idempotency_key` with
  an idempotency lookup on both queue backends; duplicate enqueues return the
  existing job. The CLI now defaults to `PersistentJobQueue` (override with
  `VE_QUEUE_DB`), the worker takes a per-job `flock` on
  `work/<job_id>/.lock`, MCP `run_pipeline` enqueues and returns a job id, and
  the new `job_status` tool reads queue state plus `manifest.json`.
  `ve enqueue` keys on the job id; MCP hashes the run payload.
- [x] **A3 legacy render path — retired 2026-10-27:** the canonical render
  graph is now the only path. The real-media benchmark
  (`projects/day-master-deck/voiced.mp4`, 74.2 s, narration transcript, no
  music) exported in 139.3 s (0.53× realtime, 54.0 MB). Removed the
  `--render-graph/--no-render-graph` option, the `use_render_graph` parameter
  and the per-stage graphics/captions branch; the QA/SFX tests now exercise
  the graph path, and the benchmark no longer has a `--legacy` mode. The Wiz
  scene-plan parity run remains green (SSIM 0.9960, gates pass), and the Saju
  render-graph rebuild is byte-identical to its refreshed baseline (SSIM
  1.0000, 0 delay mismatches). Legacy-path fixes found during the comparison
  (cut-timeline remap for graphics, unique caption layer names) are retained.
  The superseded one-off scripts are archived under `examples/archive/`.
- [x] **A3 musical composer port — completed 2026-10-28:** the engine path is
  `ve musical` → `musical_pipeline.run_musical_job` → `adapters/from_musical.py`
  → one Composition, one HyperFrames render, one music mux and loudness pass.
  `CompositionScene.transition_in` is emitted as a linear per-layer crossfade
  (`eb2dfb8`), and `scripts/parity_rebuild.py --case musical` now rebuilds the
  same fixture through the legacy FFmpeg script plus drawtext and through the
  IR: duration 8.011 s, whole-video SSIM 0.9760, scenes 0.9706 / 0.9814,
  boundary 0.9620, and all four composition gates pass. The script's
  `zoompan` x/y now include the centring term its comment promised, and both
  paths advance the zoom linearly. The two scripts remain as the FFmpeg
  reference/fallback for one release; removing them and their path-based tests
  is the follow-up (plan task 2.4.7).
- [x] **A4 config — completed 2026-10-27:** `models.yaml` and `pricing.yaml`
  ship as package data, load through `importlib.resources`, and accept
  `VIDEO_EDITOR_PRICING` / `VIDEO_EDITOR_MODELS` overrides. The
  `config.local.yaml` overlay and the search order (explicit →
  `VIDEO_EDITOR_CONFIG` → cwd ancestors → repo root) are shared by every
  loader. `Settings` caches one resolved object per `(path, mtime, size)` and
  every runtime caller now injects `settings.config.assets` /
  `settings.config.llm`; the pipeline managers and LLM factories no longer
  re-read YAML. TODO(6b) is resolved: storyteller tier presets have one source.
  `AssetProviderConfig.load()` / `LLMConfig.load()` remain as compatibility
  APIs for standalone callers and tests, with no runtime call sites.
- [x] **A5 workspace (2026-10-27):** the 21 single-module skill packages plus
  the shared protocol now ship from `packages/skills` as one
  `video-editor-skills` distribution. Their top-level import names are
  unchanged, the 21 workspace members and duplicated dependency lists are
  gone, the beat detector is an optional `[detector]` extra, and the wheel
  includes all 22 modules. `mypy apps packages` now covers the consolidated
  source tree.
- [x] **A6 dependencies (2026-10-27):** `hyperframes` is pinned to `0.8.208`
  in `package.json`; the renderer prefers `node_modules/.bin/hyperframes`;
  `ve doctor` checks FFmpeg filters, HyperFrames, Chrome and `agy`; and
  `qa.json`/`QAReport` now carry a cached `tool_versions` block.
- [x] **H2 data dir (2026-10-27):** `VIDEO_EDITOR_DATA_DIR` moves the default
  work/output roots to `<data>/projects` and `<data>/outputs`; per-root env
  vars still win and the in-repo default is unchanged. The queue DB follows
  the output root and is documented in `README.md` and `docs/CLI_USAGE.md`.
- [x] **H5 config overlay (2026-10-27):** adjacent `config.local.yaml` is
  merged recursively by the project, asset and LLM loaders and is gitignored.
- [x] **H4 agent state (2026-10-27):** decided to keep the four `.remember/`
  notes and `.superpowers/sdd/progress.md` tracked; they are handoff context,
  not generated build output.
- [x] **H1 history size:** accepted as-is; no history rewrite per the user's
  decision. Document the 880 MiB `.git` cost if the repo is shared.

## Closed index

| Era / pass | Findings | Archive |
|---|---|---|
| 2026-11-25 tiffin video | NVIDIA HyperFrames profile (PRIME + NVENC); `shots:` (query/photo/file) per footage scene; no repeated stock clip, round-robin retries with `exclude_ids`; transparent caption overlay burn and no drawtext for complex scripts; AI label on every AI provider plus `ai: true` files, Telugu shaped; footage judge budget; agy/flow out of the auto image chain | this file, `RENDERING.md` |
| 2026-10-28 P1 correctness | #298–#302, #304, #309, #310: worker lease + off-loop dispatch, MCP command guards, SFX auth/licence, short manifest, `BLE001`/`S110` | this file |
| 2026-10-28 P2.3 / P3 | #303 queue + MCP parity, A11 matrix, #305 download caps, #306 layerize cache, #307 typer ban, #308 zero-budget SFX | this file |
| 2026-10-28 P4 start | look-ban gate (P4.1), director's-brief fields (P4.2), vision critique + patches (P4.3 partial), measured style extraction (P4.4), beat grid + quantization (P4.6), wipe/mask_reveal/camera primitives (P4.7 partial) | this file |
| 2026-10-29 long-form pass | #311 graphs, #312 voice clone, #313 transitions, #314 shots/video, #315 chapter packs, #316 delivery (sidecar/end-screen/swells), #317 generated BG/MG/FG; P4.3 loop, P4.5 brand capture, P4.7 morph/cursor/type_on, P4.9 HTML | this file |
| 2026-10-28 A13 + SSRF | data move to `/mnt/data2/video-editor-data` with symlinks; redirect target validation | this file |
| 2026-10-27 audit, phases 0–3 | #274–#284, #287, #291–#297 (plus #285, #286, #288, #290) | `docs/archive/issues/2026-10-27.md` |
| 2026-10-27 phase 5 (A1/A2/A8) | models → core; pipelines package; import-linter; command groups; error policy | this file |
| 2026-10-27 provider correctness | #261 FAL duration mapping; #263 per-call TTS billing unit | this file |
| 2026-10-28 MCP spend | `sfx_placement` runs under `job_budget` (MCP and pipeline); `dubbing` tool retired | this file |
| 2026-10-16 to 2026-10-26 | #248–#272, the content-pack slice and cost-gate fixes | `docs/archive/issues/2026-10-22.md` |
| 2026-07-02 to 2026-07-12 | #52–#247, Sessions A–W and the earlier audits | `docs/archive/issues/2026-07.md` |

The MCP `dubbing` tool was removed on 2026-10-28: it had no translator, no TTS
provider and no budget, so every call failed before spending. The
`packages/skills/src/dubbing` library and its tests remain; a working tool
needs a translator decision first. `sfx_placement` was the live uncapped
spender and now builds an `AssetIngestionManager` with the job budget; the
pipeline passes its manager too.

Formatting/type-check work completed in the 2026-10-27 pass: `ruff format` is
enforced in CI, `mypy apps packages` is clean (skills now ship from
`packages/skills`), and the real HyperFrames short e2e is marked `slow`.
