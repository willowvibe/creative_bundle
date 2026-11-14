# Drama production playbook (as built on Mantra Rahasyam Ep1 v2, 2026-11-02)

This is the working pattern for a drama episode, written after Ep1 v2 shipped. It replaces the
"proposed" parts of `DRAMA_PIPELINE_V2_2026-11-01.md` wherever the two disagree. The plan's shot grammar
(§2.1–§2.4) held up. Its hosting plan (RunPod serverless, RIFE + SeedVR2 finishing) did not get built:
the user dropped RunPod during the video stage because pods took too long to come up, and the episode
was finished on **PoYo + Flow** APIs.

Reference episode: `videos/2026-10-30/mantra-rahasyam-ep1/`. The `edit/v2_*.py` scripts there are the
template for the next episode. Copy them and change the shot list; don't start from the Ep1 v1 scripts
(`clips.py`, `assemble.py`).

## 0. Stage order and gates

```
1 script → 2 dialogue → 3 voices → 4 images → animatic → 5 videos → sound (score + SFX)
   → 6/7 final cut (assembly + graphics) → promo (thumbnail, Shorts, chapters, subtitles)
   → languages (dub audio tracks)  ← only after the final cut is approved
```

- Every stage is shown to the user and waits for a yes before money is spent on the next one.
  `scripts/drama_review.py` holds the gates (`script, dialogue, voices, images, videos, lipsync, final`);
  remote reviewers use the board (`scripts/drama_board.py publish|pull|invite`).
- Only the user approves. A board press of "Approve the whole stage" by the owner counts; nothing
  else does.
- Quote the price before every billable batch, and record task ids at submit so a stopped run re-collects
  instead of paying twice (each runner keeps a ledger JSON).
- **Other-language audio is made only after the final cut is approved** (user rule, 2026-11-02). Any
  change to the cut after a dub moves line slots, and every dub would have to be re-fitted and re-mixed.
  Translations can be drafted and reviewed earlier, but nothing is voiced until `final` is approved.
- **Story gate (advisory):** `python scripts/story_rules.py <episode>` applies the five story rules and
  writes `review/story_rules.md`. It is deliberately *not* an approval gate: an episode can pass every
  render gate and still be a bad story. See `docs/DRAMA_STORY_AND_CONTINUITY.md`.
- **Continuity ledger:** `python scripts/episode_state.py update|check <episode> --state <series>/state.json`
  records the cast, props and hook per episode and fails a prop or fact that appears before the episode
  that introduces it. Character bibles cover identity; the ledger covers state.
- **Storyboard board:** `python scripts/drama_review.py storyboard <episode>` renders
  `review/storyboard.html` — picture, camera, action, line and the three video directives per shot.
  Look at it before any video batch is paid for.

## 1. Script

- Built from the user's notes into `script.md` plus `shots.json` (scenes, lines, speaker, intended
  emotion per line). Series continuity comes from the character bibles in
  `videos/mantra-rahasyam/assets/characters/*/bible.md`.
- Gate 1. Family reviewers edit the Telugu on the board; read every *Comment* as a change request.

## 2. Dialogue

- Every line carries `te`, `en` and an `emotion`; the narrator carries about 30% of the characters' emotion.
- Gate 2 before any voice is generated (`tts.py lines` calls `require(P, "dialogue")`).

## 3. Voices (ElevenLabs only)

- `edit/tts.py`: ElevenLabs `eleven_v4`, one library voice per character (`CHOSEN`), per-character
  `PACE` and `STABILITY`, the emotion as an inline tag (`[sobbing, desperate] …`). `language=` defaults to
  `te`; the dubs reuse the same function and cast.
- ElevenLabs is voice only; music and SFX come from elsewhere (`providers/elevenlabs_policy.py` enforces it).
- STT-check every take; redo a take by deleting its file and re-running.
- Cost: about $0.23 for a 39-line, 5-minute episode.

## 4. Images

Shot list first, in the serial grammar (`edit/v2_prompts.py` → `v2/visuals.json` + `v2/IMAGE_PROMPTS.md`):
- Stills first, lip-sync is the exception. Ep1 v2 had 80 stills for about 5 minutes: 29 one-window lip-sync
  shots, about 29 motion clips, 4 hero clips, and the rest places and inserts under camera moves.
- A prompt has four independent parts: framing + action / physical expression / identity + wardrobe (no
  expression words) / scene mood. Mood never leaks into faces, and references never bring their expression.
- Lip-sync stills start with the mouth closed and the face at least ~25% of frame height; listeners are
  off-screen or turned away. Grief stills actually cry.

Engines (measured on Ep1 v2):

| Use | Engine | Cost |
|---|---|---|
| Most stills, anything with strong emotion | **Flow Nano Banana Pro** (`edit/v2_flow.py`) | 0 credits |
| Calm or happy shots, settings | PoYo Kling O3 (`edit/v2_sample.py`) | 3.5 cr |
| Blood or dark beats | PoYo Qwen Image 3 | ~5–6 cr |

- Flow won all three expression A/Bs against Kling O3 (real tears, knotted brows). Kling O3 keeps the
  reference's pleasant face on grief.
- Retakes: put an already-approved still of the same character(s) in the third reference slot, next to
  the face crops.
- Flow stills are 2752×1536 and carry a Gemini sparkle. Remove it with
  `delogo=x=2606:y=1390:w=66:h=66` into `v2/frames_clean/`, and build only from the clean copies.
- **Dedupe every Flow batch.** A quietly failed Flow job saves the previous job's image under the new name.
  This happened twice in Ep1 v2 (s45 == s74, s29 == s62).
- Gate 4 on two contact sheets.
- Cost: 177.6 PoYo cr ($0.89) for 51 tasks including rejects, plus about 55 Flow stills for free.

## 4b. Animatic (before any video spend)

- `edit/v2_animatic.py all`: approved stills under camera moves, cut to the real voice timeline,
  each labelled with its shot id and future route. Output is a 720p animatic and a 540p phone copy.
- Its `v2/animatic/edit_list.json` **is the edit**. Every later stage cuts to it: clips, score and SFX
  cues, graphics, subtitles and dubs.
- Timeline rules:
  - Durations are measured from the wavs.
  - 0.5 s gaps between lines.
  - A speaking shot starts on its voice; any other shot opens half a gap ahead of its line.
  - Slots are whole frames at 25 fps.
- `PAUSE_BEFORE = {"s20": 1.0}` inserts a picture-only beat before a line (the phone rings once before
  the nurse speaks). Later scripts shift every cue after it by the same amount.
- The user reviews pacing here. Order changes are free at this point.

## 5. Videos (PoYo + Flow)

`edit/v2_poyo_clips.py` (ledger `v2/clips/_poyo_runs.json`) routes each shot by type:

| Shot type | Engine | Price | Notes |
|---|---|---|---|
| Speaking shot | **PoYo OmniHuman 2.0** (Ep1 v2) → **MiniMax H3 on Modal from the next episode** | OmniHuman 21 cr/s, 4 s minimum; H3 ≈ $0.08–0.15 a shot | Send the line's own audio slice; assembly trims back to the slot. Both hold grief and multiple faces in frame. H3 decided 2026-11-04 (below). |
| Motion / living still / action | **PoYo Veo 3.1 Lite** (`veo3.1-lite-official`) | 3.6 cr/s, 720p silent | Shortest of 4/6/8 s that covers the slot. About $0.08 a clip. |
| Shots Veo refuses (a child near traffic) | PoYo **Kling O3** standard | 10 cr/s, 3–15 s | `ON_KLING` set. About $0.35–0.55 a clip. |
| Hero / divine / effects | **Flow Omni 1.1 Flash** | 7–12 Flow credits | Pre-paid credits shared across episodes; keep the list short. Remove the sparkle on 1080p with `delogo=x=1698:y=857:w=84:h=86`. |
| Places, inserts, flashbacks | local camera move | free | No faces under camera-only moves. |
| **Directed camera move** (dolly, crane, pan, orbit on a place or wide) — from Ep2 | **Uni3C**: `s3d still-move <approved still> <camera.yaml>` (scene3d-engine) → `rpc run wan21_uni3c --modal` | ≈ $0.18 a 5 s shot, ~4 min | Starts from the approved still, so it follows the images gate. Faces wait for a character-still test. Fallback for big moves: scene3d blocking + `wan22_fun_control`. Verdict: `scene3d-engine/docs/plans/2026-11-04-uni3c-spike.md`. |

- Per-shot motion prompts that failed on the first take go in `MOTION_FIX`.
- **Every video prompt carries three clauses** (added 2026-11-06): `single continuous take, no cuts, no
  camera changes` (otherwise the model invents cuts inside the shot), `and nobody else in frame` (name
  everyone present, exclude strangers), and `spoken exactly as written` on any shot with a line. The
  storyboard board prints them per row so they are in front of the reviewer when the shot is ordered.
  Details: `docs/DRAMA_STORY_AND_CONTINUITY.md` §4.
- Content refusals and server failures are refunded. Resubmit server failures once.
- A shot that needs two actions (the save, then the lorry) is two clips joined in one slot: `PARTS` in
  `edit/v2_assemble.py`.
- A clip can be reused for another shot (`CLIP_OF = {"s04": "s40"}`), so an early still can play as the
  motion clip made for its later twin.
- Gate 5 on the board.
- Cost:
  - Lip-sync: 2,701.4 cr ($13.51) for 29 shots, **77% of the episode**.
  - Veo: 525.6 cr ($2.63) including rejected first takes.
  - Kling: 110 cr ($0.55).
  - Flow: 34 credits.

**Not chosen, with reasons:**
- RunPod pods (InfiniteTalk + Wan 2.2): each fresh pod took more than 10 minutes to boot. Two workflows on
  one card swap models slowly. The user abandoned it after about $1.19. Since 2026-11-04 the same workflows
  run on Modal (`rpc run --modal`, cold start under a minute, InfiniteTalk ≈ $0.18 a 5 s line on a B200,
  ~$4.49 an episode): `runpod-comfyui/docs/GPU_ECONOMICS.md` §8.
- LTX-2.5 audio-to-video (Freefield `talking`, 2026-11-04): keeps the ElevenLabs audio, 1080p, ~$0.06 a
  5 s line on Modal (about 1/3 of InfiniteTalk), but Lakshmi **smiled or laughed in both seeds on a grief
  line** (v26/c08c) despite the prompt and negative. Not for grief shots; untested on calm lines.
  `../tool-lab/trials/2026-11-04-freefield/README.md`.
- MiniMax H3 (2026-11-04, same doc): with our ElevenLabs line anchored as the soundtrack (`h3-lipsync`, our
  graph) the lips follow the Telugu and grief mostly holds, ~$0.15 a 5 s line on an H100 but only 1056×608.
  Silent motion on a grief still holds the face well; `extend-video` and `reshoot` give a continuation and a
  new camera angle of an existing clip. **Two people in frame** (s32, s39): the non-speaker stayed stiller than
  in the OmniHuman takes and expression held, at $0.08–0.13 a shot against OmniHuman's $0.42, but at 1056×608.
  Strongest candidate to replace OmniHuman next episode; still to test two faces both turned to camera.
- Flow lip-sync: Flow cannot sync to outside audio. Omni Flash speaks a Telugu line written into the prompt
  correctly, and a Flow character (`flow_character`) locks face and voice. It costs about 1/12 of
  OmniHuman, but the on-screen voice is a Flow voice, not the ElevenLabs cast. This is the main candidate
  for cutting cost on the next episode; it needs the user's call on voice consistency.

## 6. Sound (score + SFX)

- `edit/score.py`: three instrumental beds from PoYo `generate-music` (20 cr each). The score sits at
  `SCORE_DB = -17` and is ducked under the voice with `sidechaincompress`. The SFX bed is never ducked.
- `edit/sfx.py`: Freesound **CC0 only** (re-read each sound's licence) plus earshot designed hits. Each
  cue has a measured target (RMS for beds, peak for one-shots), measured on the segment actually used.
- For v2, `v2_assemble.py beds` monkeypatches the cue tables (`SFX_V2`, `SCORE_V2`) onto the animatic
  timeline and renders into `v2/cut/`. Cue times are pre-shift; `shifted()` adds any `PAUSE_BEFORE`.
- Check hit timing against the picture by detecting onsets in the rendered audio, not by eye. The defib
  file has a 2.34 s charge-up before the shock, so the cue time is the shock time minus 2.34 s.
- Candidate generator for one-off effects: Stable Audio 3 medium (Freefield `sfx` on Modal), a 10 s stereo
  effect in 3 s for under a cent. Not yet judged by ear, and its Community Licence needs checking before use
  in a published episode (2026-11-04, `../tool-lab/trials/2026-11-04-freefield/README.md`).
- Guard against cue-table typos. In Ep1 v2 an inline comment knocked three cues out of the dict, and the
  `SFX_V2` key check caught it before a render.

## 7. Final cut (assembly + graphics)

- `edit/v2_assemble.py all` produces the 1080p25 master and the 720p `.web.mp4` for the board.
  - Speaking clips play in real time from the cut.
  - Motion clips are slowed up to `SLOW_CAP` to fill their slot, then the last frame holds.
  - `USE` trims a clip to its usable head.
  - Ep1 v2 clips were 720p, lanczos-scaled to 1080p. **From the next episode, finish them first:**
    `python -m scripts.finish_upscale <ep>/v2/clips/<kind> <ep>/v2/clips_1080/<kind>` prints the plan and price
    (SeedVR2 on Modal, 1.5x = native 1920x1080, ≈ $0.031 per second, cold start included); add `--yes` to run.
    Point assembly at `clips_1080/` so its 1920x1080 FIT is a no-op. Measured on Ep1 v05: real face detail,
    flicker under one grey level, 192 s for 3.2 s (video-editor #324, runpod-comfyui `seedvr2_upscale`).
- New PoYo runners use `scripts/poyo_jobs.py` (`Ledger.submit/collect/download`): the task id is saved the moment
  PoYo accepts it, so a crash never pays twice.
- `edit/v2_graphics.py all`: HyperFrames compositions rendered to transparent ProRes 4444 and overlaid by
  FFmpeg:
  - series title, place supers and name tags on first appearance;
  - on-screen mantra with each word lit as it's sung (word times from the chant's loudness dips);
  - a 4 s cold open (crash, flatline, divine light);
  - a 15 s end card with the channel icon and name, "To be continued" and Subscribe (no link).

  Telugu shapes correctly in HyperFrames with Noto Serif Telugu.
- Loudness:
  - The mix is loudnormed to −14, and the delivered file measures about −15 LUFS / −2 dBTP.
  - 256k AAC with headroom.
  - The web copy's audio is taken from the PCM mix, never from the master's AAC.
- Gate 7 (`final`) on the board.
- User fixes on Ep1 v2, all made in the scripts, never by hand:
  - a ring-only pause;
  - a reused clip;
  - the crash hit moved three times, ending on the lorry insert;
  - the defib moved 2 s earlier;
  - an added save shot.

## 8. Promo (after the cut, before upload)

- **Thumbnail:** Flow Nano Banana Pro paints the scene *and* the Telugu lettering; check the spelling. On
  1280×720 the sparkle is at `delogo=x=1208:y=644:w=44:h=44`. The poster variant was the pick.
  HyperFrames-lettered versions are the fallback. Ep1 used the clean variant (user, 2026-11-04).
- **Per-language thumbnails** (YouTube Languages page has a Thumbnail column per language; available on
  Mantra Shakti since it has multi-language audio). Make them in Flow, free: Nano Banana Pro, the chosen
  thumbnail as the only reference, prompt "Recreate this exact YouTube thumbnail with only the text changed…
  Replace the big bottom title with <script>, spelled exactly: … Replace the top-left badge text with: … No
  Telugu text anywhere". 2 variants each; all 8 for hi/ta/kn/en spelled correctly on the first try.
  Ep1 texts: hi मृत्यु को जीतने वाला मंत्र, ta மரணத்தை வென்ற மந்திரம், kn ಮೃತ್ಯುವನ್ನು ಗೆದ್ದ ಮಂತ್ರ,
  en THE MANTRA THAT DEFEATED DEATH (badge: series name · EP 1 in the same script).
- **Sparkle on these 1376×768 Flow images** sits at the same place every time: centre ≈ (1277, 671),
  ~48 px, peak opacity ~0.32. Where it overlaps gold lettering, `delogo` smears gold into the box. Remove it by
  reverse alpha blending instead: estimate the alpha map from two variants where the star is over flat
  background (`a = (obs − bg)/(255 − bg)`, bg = median of a ring around the box, take the per-pixel min of the
  two), then `bg = (obs − a·255)/(1 − a)` in the box on every image. On flat background a light `delogo` after
  that clears the JPEG residue; keep its box off the letters. Then resize to 1280×720. Delivered as
  `thumbnails/thumbnail_<lang>.jpg` in the YouTube folder.
- **Shorts:** `edit/v2_promo.py shorts` makes three 35–40 s spans of the *final* file (so overlays come
  along). Each is 1080×1920: the 16:9 picture over a blurred fill of itself, plus a HyperFrames title/CTA
  overlay on a dark panel, clear of the Shorts UI (bottom ~420 px, right edge).
- **Chapters:** `v2/youtube_chapters.txt` from the edit list plus the cold-open offset.
- **Subtitles:** `edit/v2_subs.py` writes `.srt` files per language, timed as line start + cold open,
  ≤ 42 characters per line, two lines max. Long lines are split at the sentence break nearest the middle.
  The mantra gets one short cue because it's on screen.
- Everything goes on the board's final stage (`promo/*.jpg`, `promo/*.mp4`).

## 9. Languages (only after `final` is approved)

For YouTube multi-language audio (Studio → Languages → Add audio track):

1. **Translate** every spoken line (not the Sanskrit chant) into `v2/dub/translations.json`. Have it
   reviewed: board folder `<episode>-v2-<lang>` with `shots.json` carrying the translation as the
   dialogue text, or the user approves it in chat.
2. **Voice** it with `edit/v2_dub.py voice <lang>`, using the same cast, pace, stability and emotion tag as
   Telugu with `language_code` set.
3. **Fit** it with `v2_dub.py fit <lang>`. A take longer than its Telugu slot is sped up with `atempo` up
   to `MAX_SPEED = 1.25`; a shorter take keeps its pace and ends early. Over the cap → one faster re-voice,
   then report the line.
   - Ep1 Hindi: median 0.98×, two lines at about 1.25×.
   - Ep1 Tamil: median 1.00×, one line at 1.24×.
4. **Mix** it with `v2_dub.py mix <lang>`, reusing the Telugu score and SFX beds and the chant, plus the
   cold-open and end-card audio taken from the Telugu final. The track is then exactly the upload's length.
   Outputs a WAV master, an `.m4a` upload copy and a review MP4 on the board.
5. Spot-check with whisper (CPU `faster-whisper`; CUDA libcublas is missing on this machine).

Cost: under $1 of ElevenLabs per language.

## 9b. Upload (social-engine, after the user approves the final package)

Publishing lives in `/mnt/data2/git_repos/creative_repos/social-engine` (its `docs/live-verification.md` has the live Studio
map). Ep1 went up on 2026-11-04 as private `jsF4rYisfPM`.

1. In the YouTube delivery folder write `publish.yaml` (title ≤ 100 chars, Telugu-first description with an
   English summary, mantra text, audio/subtitle language lines, fiction + AI note, hashtags; tags; category 24;
   `chapters: chapters.json`; captions; `youtube.dubs`; `youtube.localizations` for en/hi/ta/kn;
   `localized_thumbnails`) and `chapters.json` from `v2/youtube_chapters.txt`. `social validate` must say ok.
2. `social publish <manifest> --via browser --yes` uploads **private**. Mantra Shakti is a Brand Account where
   we are managers, so the API lane is blocked; everything is browser.
3. Then, on the video's Studio **Languages** page: set the video language (untick "default for my channel"),
   per language add title/description, thumbnail, audio (dub) and subtitles; upload the original-language
   SRT via "Upload manual". On **Details**: tags and "Title and description language" = Telugu.
   `social yt dubs` didn't match live Studio on 2026-11-04, so this was driven by hand on one CDP connection.
4. YouTube auto-dubbed an English track by itself; the user decides keep/unpublish.
5. Going public is a separate yes from the user, after they review the private video.

## 10. Cost of Ep1 v2 (measured)

| Stage | Engine | Cost |
|---|---|---|
| Voices + score (reused from the restart) | ElevenLabs + PoYo music | ~$0.53 |
| Stills | PoYo Kling O3/Qwen + Flow (free) | $0.89 |
| Lip-sync, 29 shots | OmniHuman 2.0 | $13.51 |
| Motion | Veo 3.1 Lite + Kling O3 | $3.18 |
| Hero + Flow tests | Flow Omni 1.1 Flash | 55 Flow credits |
| Animatic, assembly, graphics, promo | local | $0 |
| **PoYo total** | confirmed by balance 10,757.4 → 7,420.4 | **3,514.6 cr = $17.57** |
| Dubs, 2 languages | ElevenLabs | < $2 |
| RunPod (abandoned) | | ~$1.19 |

Lip-sync is the one lever that moves the total: Flow lip-sync would bring it down to about $5 per episode.

## 11. Open items for the next episode

- **Lip-sync route decided (user, 2026-11-04): MiniMax H3 with our ElevenLabs line anchored (`h3-lipsync` on
  Modal, `../tool-lab/trials/2026-11-04-freefield/README.md`) replaces OmniHuman for speaking shots.** The user rated its
  lip-sync and emotion good on v26, s32 and s39. One catch: once the line ends, H3 keeps mouthing as if still
  talking, because the clip runs past the audio (its length snaps to 24 fps × (17k + 5) frames). Handling:
  - **"End of the line" means where the voice stops, not where the audio slice stops** (user, 2026-11-04: s39's
    2.60 s slice is speech to 1.81 s then a pause, and H3 mouthed through the pause). `v2_h3_lipsync.py` finds the
    voice end with silencedetect (−35 dB, 0.15 s), gives H3 only the speech, and cuts there.
  - **Always cut the clip at the end of the line in assembly** (user, 2026-11-04: s39 still moved its lips after
    the line even with padding). If the slot runs longer, hold the last closed-mouth frame or cut to a reaction
    / living still for the pause.
  - Render the shortest H3 length that covers the line (5 + 17k frames at 24 fps: 2.33, 3.04, 3.75, 4.46,
    5.17 s…), so the tail stays under ~0.7 s; e.g. a 2.6 s line → 3.04 s, not 4.46 s.
  - Always pad the audio slice with silence to the full clip length (so the anchor covers the tail) and add
    "After the last word they fall silent, lips closed, listening, until the end." to the prompt. Tested
    2026-11-04 on s32/s39: mouth motion after the line −66% / −39%, lip-sync in the line unchanged; the user
    found s32 clean but s39's 1.8 s tail still mouthing, hence the cut and the short length above.
  - Mux the original ElevenLabs slice back over the picture (H3's soundtrack is a close re-synthesis, not exact).
  - Output is 1056×608 at the `h3-photo` scale; raise it (node 119 `megapixels`) or upscale for the 1080p master.
  - Wired 2026-11-04: `edit/v2_h3_lipsync.py [--only ids] [--out dir] [--workers 3]` in the Ep1 template folder
    renders every `speak` shot through `rpc run h3_lipsync --modal` with all of the above applied, into
    `v2/clips/lipsync/` (skipping clips already there), ledger `v2/clips/_h3_runs.json`. Run it instead of
    `v2_poyo_clips.py --kind lipsync`; keep `v2_poyo_clips.py --kind motion` for Veo.
- 1080p finishing (SeedVR2 or a better upscaler), #324. SeedVR2 3B measured 2026-11-04: real detail on a
  720p Wan clip at 2×, ≈ $0.06 per output second on an H100 (~$19 an episode), flicker not yet checked. Use it
  for hero shots and the thumbnail first.
- **Directed camera moves (decided 2026-11-04, Uni3C GO):** places and wides get a real camera path from their
  approved still (route table above). Ep2 script v2.1 uses it for 5 shots. Not yet tested on faces.
- **Ep2 budget estimate (2026-11-05)**, scaled from §10 to ≈ 9:35, 60 lines, ~140 stills, 40–50 H3 lip-sync, ~50
  motion, ~18 FLF chain clips, 5 Uni3C, ~8 hero:

  | | With Flow | Without Flow |
  |---|---|---|
  | Voices + score | ~$1.6 | ~$1.6 |
  | Stills | ~$1–1.5 (Flow NBP free) | ~$3–9 (NBP on PoYo ≈ $0.09) |
  | H3 lip-sync | ~$4–8 | ~$4–8 |
  | Motion (Veo/Wan) + Kling for child shots | ~$6–8 | ~$6–8 |
  | FLF chains + Uni3C | ~$2.5–3.3 | ~$2.5–3.3 |
  | Hero/effects | 60–100 Flow credits | ~$4–8 (PoYo Kling/Veo) |
  | Thumbnails | $0 | ~$1 |
  | **Episode** | **≈ $15–25 + 60–100 Flow credits** | **≈ $25–40** |
  | Dubs (hi/ta/kn/en) | ~$5 | ~$5 |

  Fixed subscriptions (ElevenLabs, Flow Pro, Ollama Cloud) and agent tokens are not included. The swing items are
  the lip-sync count and its retakes, and whether the emotional stills stay on Flow.
- Continuity check across neighbouring motion clips. In Ep1 v2 the ball is red in s11 and white in s12.
- Optional code guard: `v2_dub.py` could `require(P, "final")` the way `tts.py` requires `dialogue`.
