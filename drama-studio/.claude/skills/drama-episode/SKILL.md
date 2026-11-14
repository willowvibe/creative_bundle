---
name: drama-episode
description: >
  Use when producing, fixing or extending a Telugu drama episode (Mantra Rahasyam or any new series):
  script, dialogue, ElevenLabs voices, stills, animatic, lip-sync and motion clips, score and SFX,
  the assembled cut with HyperFrames graphics, thumbnail/Shorts/chapters/subtitles, and the dub
  audio tracks for other languages. Also for review-board gates and drama cost questions.
---

# Drama episode

Work from `/mnt/data2/git_repos/creative_repos/drama-studio` (split out of `video-editor` on 2026-11-11).
Episode folders live in `../video-projects/videos/`, reached here through the `videos` symlink, so the
paths below work unchanged. Engine commands (`uv run ve ...`) run from `../video-editor`.

Full pattern and measured numbers: `docs/DRAMA_PRODUCTION_PLAYBOOK.md`. Template episode:
`videos/2026-10-30/mantra-rahasyam-ep1/`. Copy its `edit/v2_*.py`, `tts.py`, `score.py` and `sfx.py`
into the new episode folder (including `v2_h3_lipsync.py`) and change the shot list. Don't start from the v1 scripts (`clips.py`,
`assemble.py`).

## Stages (one user gate each, in order)

| # | Stage | Script | Output | Gate |
|---|---|---|---|---|
| 1 | Script | by hand from the user's notes | `script.md`, `shots.json` | `script` |
| 2 | Dialogue | `shots.json` lines: `te`, `en`, `emotion` | — | `dialogue` |
| 3 | Voices | `edit/tts.py lines` (ElevenLabs `eleven_v4`) | `audio/lines/*.wav` | `voices` |
| 4 | Images | `v2_prompts.py` → `v2_flow.py` (Flow Nano Banana Pro) / `v2_sample.py` (PoYo Kling O3, Qwen for blood) | `v2/frames_clean/` | `images` |
| 4b | Animatic | `v2_animatic.py all` | `v2/animatic/edit_list.json` (the edit) | user review |
| 5 | Videos | `v2_h3_lipsync.py` (MiniMax H3 lip-sync on Modal, since 2026-11-04) + `v2_poyo_clips.py --kind motion` (Veo 3.1 Lite, Kling O3 fallback) + Flow Omni 1.1 Flash hero | `v2/clips/{lipsync,motion,hero}/` | `videos` |
| 6 | Sound | `score.py` (PoYo music) + `sfx.py` (Freesound CC0 + earshot), re-cued by `v2_assemble.py beds` | `v2/cut/*_bed.wav` | with the cut |
| 7 | Final cut | `scripts/finish_upscale.py` (SeedVR2 1080p, quote then `--yes`) → `v2_assemble.py all` → `v2_graphics.py all` (HyperFrames title, supers, mantra, cold open, end card) | `*_final.mp4`, `.web.mp4` | `final` |
| 8 | Promo | `v2_promo.py all`, `v2_subs.py`, chapters file | `v2/promo/`, `v2/subtitles/*.srt` | board final stage |
| 9 | Languages | `v2_dub.py all <lang>` | `v2/dub/<lang>/*_audio.{wav,m4a}` | **only after `final` is approved** |
| 10 | Upload | `publish.yaml` in the YouTube folder → `social publish --via browser --yes` (social-engine), then Studio Languages/Details | private video; public only on the user's yes | user reviews private video |

Publish each stage with `scripts/drama_board.py publish <stage> <episode>` and read notes back with `pull`.

**Story and continuity (advisory; they do not have their own gate):**

- After `shots.json` exists, run `python scripts/story_rules.py <episode>` and read `review/story_rules.md`.
  It applies the five story rules and prints a beat table. Fix story failures before spending on stills.
  If rule 4 says `REVIEW`, annotate the scenes with a `turn` (`up`/`down`/`worse`/`better`/`twist`) rather
  than ignoring it. See `docs/DRAMA_STORY_AND_CONTINUITY.md`.
- Before the `images` gate, run `python scripts/episode_state.py check <episode> --state <series>/state.json`.
  It fails a prop or fact that appears before the episode that introduces it, and warns on a speaker who is
  not in the ledger. Run `update` after the cut is approved to record the cast, props and hook.
- Before the `videos` gate, run `python scripts/drama_review.py storyboard <episode>` and look at
  `review/storyboard.html`: picture, camera, action and line per shot. Fixing a still is free; fixing a
  shot is not.

Before publishing the `dialogue` gate, run `scripts/dialogue_check.py <episode>` (Sarvam second read, 3 runs, majority
vote; writes `review/dialogue_check.md`). Rewrite the flagged lines yourself in `shots.json`; don't paste Sarvam's
rewrites. On Ep1 it caught 7 of 15 reviewer fixes with 2 false alarms and misses elder/devotional register, so the
family gate stays.

## Hard rules

- **Never approve a gate for the user.** Wait for their yes in chat or their owner press on the board.
- **Quote the price before each billable batch.** Save PoYo task ids at submit; every runner keeps a ledger
  (new runners: `scripts/poyo_jobs.py`).
- **Never delete permanently yourself.** Board deletes are the user's click.
- **Dub audio only after the final cut is approved.** A later cut change moves every line slot. Translations
  may be drafted and reviewed earlier.
- **ElevenLabs is voice only.** Music comes from PoYo `generate-music`; SFX from Freesound CC0 or earshot.
- **The animatic's edit list is the timeline for everything downstream.** Change timing there
  (`PAUSE_BEFORE`), never in one later stage alone.
- **Make fixes in the scripts, then re-render.** No hand edits to outputs.
- **No Flow project open → ask the user to click one.** After that, hide Chrome again with
  `FLOW_URL=<project url from curl :9333/json> ~/.flow-mcp/show-flow.sh --hide`.

## Shot routing (serial grammar)

Stills first; lip-sync only where a character is seen speaking, one window (≤ ~5 s) per shot.

| Shot | Route | Cost |
|---|---|---|
| Speaking single | OmniHuman 2.0, own audio slice, padded to 4 s | 21 cr/s |
| Reaction, living still, action | Veo 3.1 Lite 720p silent, 4/6/8 s | 3.6 cr/s |
| Veo refuses (child near traffic) | Kling O3 standard | 10 cr/s |
| Divine / effects / hero | Flow Omni 1.1 Flash | 7–12 Flow cr |
| Place, insert, flashback | local camera move | free |

Faces never get camera-only motion. Grief and inner-voice lines play over crying living stills.

Every video prompt carries three clauses: `single continuous take, no cuts, no camera changes`,
`and nobody else in frame`, and — on any shot with a line — `spoken exactly as written`. Without the
first, the model invents cuts inside the shot; without the second, strangers walk in; without the third,
it paraphrases the Telugu.

## Gotchas that cost time on Ep1 v2

- **Dedupe Flow batches.** Flow can save the previous job's image under a new name.
- **Flow sparkle watermark:** delogo boxes per size.
  - 2752×1536 stills: `x=2606:y=1390:w=66:h=66`
  - 1080p video: `x=1698:y=857:w=84:h=86`
  - 1280×720 thumbnail: `x=1208:y=644:w=44:h=44`
  - 1376×768 Nano Banana Pro thumbnails: star at ≈ (1277, 671), ~48 px. Over lettering use reverse alpha
    blending (playbook §8), not delogo.
- **Per-language thumbnails:** re-letter the chosen thumbnail in Flow (free) per dub language; playbook §8.
- **Title + thumbnail (CTR):** load the `youtube-packaging` skill at stage 8 and again 48 h / 7 days after release.
- **OmniHuman bills at least 4 s of audio.** Trim each clip back to its slot in assembly.
- **A two-action beat goes in `PARTS`** as two clips in one slot. To reuse a clip under another shot id,
  use `CLIP_OF`.
- **Place SFX by the hit inside the file, not the file start.** Find the onset by measuring (the defib has
  a 2.34 s charge-up). Check placement on the rendered audio.
- **Python dict cue tables:** never put an inline comment that swallows the following entries. Keep the
  key-set check in place.
- **Board delete runs in batches of 100** (free-plan CPU limit). The client loops while `remaining`.
- **whisper checks run on CPU** (`faster-whisper`; CUDA libcublas is missing).
- **Use absolute paths in long shell loops.** cwd drifts between calls.

## Before calling a stage done

- Measure the delivered file, not the mix: duration, about −15 LUFS, ≤ −1 dBTP.
- Snapshot HyperFrames overlays at their midpoints and check that the Telugu is shaped.
- Note the stage and its spend in the episode `README.md`.
- Re-run `scripts/story_rules.py` after any script change, and `scripts/episode_state.py update` once the
  cut is approved, so the next episode starts from the right state.
