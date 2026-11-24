# Talking-avatar pilot (2026-11-24)

A 55 s tech-tutorial mock-up: editor + terminal "screen recording" with a cartoon of the creator bottom-right,
lip-synced to the narration. Proves the `ve talking-avatar` path (video-editor 8502a9b) end to end. English only.

## Pieces (media is gitignored)

| Step | How | Cost |
|---|---|---|
| Cartoon base | `gen_avatar.py base 2` on PoYo Nano Banana Pro edit, refs `refs/photo.jpg` (identity) + `refs/style.jpg` (style); the user picked **base_0** | 2 × 18 credits |
| Mouths + blink | `gen_avatar.py variants avatar/raw/base_0.png` (8 mouths + blink) | 9 × 18 credits |
| Sprite set | `build_sprites.py` → `avatar/sprites/` (feathered mouth/eye patches over one base, green keyed; H uses C, G uses B) | free |
| Narration | ElevenLabs `eleven_multilingual_v2`, voice "Harish – Professional" (`ATYMJR0SnaN1mSrsnZEi`), `audio/narration.txt` (781 chars) → 55.2 s | 781 chars |
| Cue times | faster_whisper words → `audio/words.json`, `screen/cues.json` (JSON = the occurrence after "status") | free |
| Screen | `screen/render_screen.py screen/cues.json 55.25 screen/screen.mp4` (drawn frame by frame; agent-browser recording hung twice) | free |
| Avatar | `ve talking-avatar --screen screen/screen.mp4 --audio audio/narration.wav --avatar avatar/sprites --script audio/narration.txt --out out/pilot_v1.mp4` (40 s) | free |
| Loudness | two-pass loudnorm I=-14 TP=-1.5 linear → measured −15.0 LUFS | free |

PoYo total $0.99 (ledger `avatar/_runs.json`); PoYo charged 18 credits per 2K image, not the 8 in pricing.yaml.

## Checks on pilot_v1

- 1920×1080, 30 fps, H.264 + AAC, 55.3 s; avatar position constant (no bounce).
- Lip sync vs Whisper word spans: lips close on 27/32 M/B/P words (84%), open on 76/95 a/e/o words (80%).
  Shape mix B 47%, C 20%, E 7%, X 6%, F 6%, A 6%, G 2%, D 0%.
- Code lines appear on their cue words (install 9.36 s, import 17.76, get 20.10, timeout 25.74, status 29.24,
  json 33.08, run 35.54, tip 41.96).

Comparisons from the timing work: `out/compare_*.mp4`; trial notes in tool-lab `trials/2026-11-24-rhubarb-lip-sync`.

## v2 and v3 (2026-11-24)

- v2 (`out/pilot_v2_lead002.mp4`): `--bubbles bubbles.yaml` (4 key/fun bubbles), calm motion, wide mouth on
  stressed syllables (D 0% → 6%). The user picked 20 ms lead over 40 ms by eye (`out/compare_lead_20_vs_40.mp4`).
- v3 (`out/pilot_v3.mp4`): batch 2 sprites from `gen_avatar.py batch2 avatar/raw/base_0.png` (4 × 18 credits,
  ledger total $1.35 of the $1.36 approved): real F/V mouth (mouth_G2), eyebrows-up and glance-left patches.
  mouth_H2 still read as a tongue poking out, so L keeps the C mouth. bubbles.yaml adds 3 glances (import,
  status, run) and an eyebrow raise on "three"; every bubble raises the brows. −15.0 LUFS.

## v4 (2026-11-24): hybrid lip sync

`out/pilot_v4.mp4`: the v3 command, unchanged. `ve talking-avatar` now defaults to `--lipsync hybrid`
(video-editor 1a5b53c): lip closures land on wav2vec2-measured M/B/P letters, open and rounded vowels are filled
in per word. The user picked this ("C") from `out/compare_lipsync_fix_3way.mp4`. Timing check: closures on time
36/36 (v3: 21/36), false closures 1/34 (v3: 15/33). Needs `video-editor/.venv-lipsync` (see the skill docstring);
alignment adds ~20 s per minute of audio and is cached in `out/.talking_avatar/`. Trial:
`tool-lab/trials/2026-11-24-lipsync-phoneme-fix/`.
