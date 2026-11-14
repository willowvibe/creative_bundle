---
name: vocal-to-song
description: Use when the user has an a cappella singing recording (male or female, vocals only) and wants it auto-tuned / pitch-corrected, wants instruments (tanpura, veena, sitar, flute, manjira, mridangam) added under it, wants a fuller arrangement or a reference-matched master, or wants it made YouTube-ready. Free, fully local pipeline (no API cost) - find the singer's own Sa, drift-aware gentle pitch correction that keeps gamakas, beat-synced instruments in the singer's key and tempo, stage-by-stage arrangement with flute interludes (optional DDSP real-flute), master to -14 LUFS. Also for a short chant voice note (e.g. "Om Saravana Bhava") that should become a japa track repeated 108 (or N) times with verified count, a ding after every chant and a tanpura bed.
---

# Vocal → YouTube song (free, local)

Built and approved on Sruthi's *Lingashtakam* (2026-11-05, v4 in `~/Downloads/lingashtakam_out/`).
**Cost: $0.** Everything runs on local CPU with open-source libraries, and a 3-minute song takes about 1–2 minutes.

Scripts are in `scripts/` next to this file. Work in the session scratchpad, not in the repo.

## Setup (once per session)

```bash
W=<scratchpad>/song && mkdir -p $W && cd $W
uv venv -q -p 3.11 .venv
uv pip install -q -p .venv librosa soundfile pyworld pyloudnorm noisereduce scipy numpy "setuptools<70"
ffmpeg -i "<input audio>" -ar 44100 -ac 1 raw.wav
```
- `pyworld` imports `pkg_resources`, so it needs `setuptools<70`.
- Run every script with `.venv/bin/python -I`.

## 1. Find the singer's Sa (never assume A440)

```bash
.venv/bin/python -I scripts/find_sa.py raw.wav
```
- Sa is the pitch most phrases end on. Untrained singers are often a quarter-tone away from standard tuning. Sruthi's Sa is 289 Hz, between C# and D. Tuning to the singer's own Sa means nothing has to be shifted far.
- Choose `SCALE` in `tune.py` from the histogram peaks, in cents above Sa. The default `[0,100,500,700,1000,1100]` (S R1 M1 P N2 N3) was Lingashtakam's scale. If the user names the raga, use its notes instead.
- Male singers usually have Sa around 130–160 Hz. Use the measured value whatever it is.

## 2. Pitch-correct gently

```bash
.venv/bin/python -I scripts/tune.py raw.wav vocal_tuned.wav <SA_HZ> 0.8
```
- Applies a light noise reduction.
- Estimates where the singer's Sa has drifted in each 6-second window. Without a reference, Sruthi drifted by up to about ±60 cents.
- Snaps stable notes onto the scale on a fixed Sa. The strength is 0.8, so 20% of the natural variation stays.
- For slides and gamakas, the correction is interpolated from the notes either side. Ornaments move as a whole instead of being flattened, which hard auto-tune would do.
- Uses the WORLD vocoder, which keeps the voice's natural timbre.
- **Check it.** Measure how far stable notes are from the nearest scale note, before and after. Lingashtakam went from a median of 27c to 5.6c, and notes within 15c went from 28% to 81%.
- **If it sounds buzzy or robotic,** rerun at strength 0.5–0.6.

## 3. Tanpura and pad that move with the song

```bash
.venv/bin/python -I scripts/backing_beat.py vocal_tuned.wav <SA_HZ> 3 <TOTAL_S>
```
`TOTAL_S` is the vocal's length + 3 s intro + 5 s outro.

What it does:
- Tracks the beat from the vocal. It follows tempo drift: Lingashtakam went from 105 to 92 BPM. It also slows a little into the ending.
- Finds the 8-beat cycle from where the lines start. It prints `downbeat phase` and `votes`. If the strong pluck doesn't land where lines begin, shift the phase.
- Plucks the tanpura once per beat: Pa, Sa, Sa, low Sa, with an accent on beat 1. It drops about 3.7 dB while the singer sings and swells back up in the gaps.
- The pad (low Sa and Pa) swells once per cycle.

The user rejected the earlier always-on drone (`backing.py`-style) as "continuous", so always use the beat-synced version.

## 4. Mix and master

```bash
.venv/bin/python -I scripts/make_ir.py hall_ir.wav
bash scripts/mix_master.sh vocal_tuned.wav tanpura_beat.wav pad_beat.wav hall_ir.wav <out_name> <TOTAL_S>
# optional trailing args: INTRO_S=3 REVERB=0.13 TAN_DB=-17 PAD_DB=-29
```
- Vocal chain: high-pass at 90 Hz, −2 dB at 250 Hz, +2.5 dB at 3.5 kHz, de-esser, 3:1 compression, hall reverb.
- Two-pass loudnorm to **−14 LUFS, true peak −1.5**, which is YouTube's playback level.
- Output is `.wav` (24-bit) plus a 320k `.mp3`.

## 5. Full arrangement (stages A–D)

Steps 3–4 give a simple drone version. For a fuller song, use `scripts/arrange.py` and `scripts/mix_stage.sh`. Deliver every stage so the user can hear what each layer adds; this user asked for it "stage by stage".

**First, map the song's structure.**
- Find line starts: phrase starts after a gap of at least 0.2 s, mapped to the nearest tracked beat. Lingashtakam lines start every 8 beats, at beat index ≡ 1 (mod 8).
- Find the refrain: compare each 8-beat line's pitch curve (64 points) with every other line. The refrain is the line position that repeats most closely; in Lingashtakam it's line 4 of each verse, at beats 25 + 32k, matching within 40–60 cents.
- Find verses: groups of 4 lines.
- Set `SA`, `SCALE`, `REFRAINS`, `CUTS` and the line phase at the top of `arrange.py`. They're song-specific.

```bash
DRONE=tanpura .venv/bin/python -I scripts/arrange.py A stA   # or B, C, D; DRONE=veena|sitar
FLU=-9 MAN=-22 MRI=-19 TAN=-17 bash scripts/mix_stage.sh stA stage_A
```

| Stage | Adds |
|---|---|
| A | Vocal doubles on refrain lines only: WORLD resynthesis with ±7 cents slow wander, 19 / 31 ms delays, panned, −9 dB (about 11 dB under the main voice). |
| B | Synthesized manjira (open on beats 1 and 5, closed on 3 and 7) and a soft mridangam pattern, with its ringing stroke tuned to Sa. |
| C | Opens up the timeline: a 15-beat intro with a flute alap (Sa–Ma–Pa–Ni–Sa'), 8-beat interludes after verses 2, 4 and 6 where the flute replays the refrain an octave up, and an outro reply plus a held Sa. The vocal is cut only inside breath gaps; check the vocal level at each cut. |
| D | Build-up: verse 1 has tanpura only, manjira enters at verse 2, mridangam at verse 3, the whole of verse 8 is doubled, and temple bells play at the start, each interlude and the end. |

**Beat instrument (`DRONE`)**, all following the tempo:
- `tanpura`: Pa–Sa–Sa–low Sa, one pluck per beat.
- `veena`: tala strings, one stroke per beat, with low Sa + Pa swept together on beats 1 and 5.
- `sitar`: main string on the beat (Sa, Pa, Sa, Ni) plus chikari high-Sa strokes on the off-beats, with sympathetic-string shimmer.
- Loudness-match each to the tanpura stem. The user then asked for veena and sitar +1.5 dB: veena −13.7, sitar −15.3.

## 6. Real-flute sound with DDSP (optional, free)

`scripts/ddsp_flute.py` replays the flute part through Google Magenta's pretrained DDSP solo-flute model. It keeps the same pitch, slides and timing, and changes only the timbre.

- **Model:** `solo_flute_ckpt` from `storage.googleapis.com/ddsp/models/timbre_transfer_colab/2021-01-06/`, about 58 MB. The code is Apache-2.0; the model files don't state a licence.
- **Environment:** separate from the main venv, about 1.4 GB.
  - `uv venv -p 3.10`, then `tensorflow-cpu==2.11.*`, `tensorflow-probability==0.19.*`, `ddsp==3.7.0 --no-deps` and its runtime deps.
  - Pins: `protobuf==3.20.3`, `numpy<1.24`, `google-cloud-storage==2.7.0`, `googleapis-common-protos<1.57`, `google-api-core<2.11`.
  - Add two stub modules in site-packages: `crepe.py` (used only by the training losses) and `google/colab/__init__.py` (with `files` and `output` namespaces).
  - Run with `-I`.
- `arrange.py` (stage C or D) writes `flute_ctrl.npz` (pitch including vibrato, plus loudness). Then run `ddsp_flute.py <ckpt_dir> <stem_dir>`. It keeps the sine version as `flute_sine.wav`. Rendering takes about 1 minute on CPU.
- **Loudness mapping is the critical part.**
  - Normalise loudness **per phrase**.
  - Map it into the model's note range: phrase peak at −44 dB, dynamics ×0.6, floor −56 dB, from the dataset's note-loudness quantiles (median −55, p90 −44).
  - Then match each phrase's level to the sine flute.
  - A whole-song quantile transform made the interludes go silent (v10 bug).
- Re-match the flute stem's loudness to the sine flute when mixing (`FLU` was −5.3 for v11).

## 7. Reference-matched mastering (optional)

Matchering against a commercial recording the user picks (Lingashtakam: SPB, T-Series, youtu.be/3G3e1UCK-5w).
- Download only the audio with `yt-dlp` into a scratch folder, after asking.
- Then run `mg.process(target, reference, results=[mg.pcm24(...)])` and turn the result down to −14 LUFS.
- **What the SPB reference measured:** −9.1 LUFS, LRA 4, much more bass, +3–5 dB presence and air, twice our stereo width, about 103 BPM.
- **After matching:** mids, highs and width matched (width 0.26 against 0.30). The remaining gap was the deep bass, because our arrangement has no bass instrument.

## 8. Chant ×N (japa) from one voice note

Built and approved on *Om Saravana Bhava* ×108 (2026-11-13). The project is at
`../video-projects/videos/2026-11-12/om-saravana-bhava-108/`, and its NOTES.md has every version and
measurement. It cost $0. Scripts are in `scripts/chant/`.

1. **Transcribe and find the cycle.**
   - Run faster-whisper `medium` with language unset, then `ta`, `te`, `sa`, `hi` and `en`. When they all
     agree, the text is confirmed.
   - A short WhatsApp note is often exactly one cycle. Print a 50 ms RMS and f0 table to see the phrase
     edges.
   - Find Sa with `find_sa.py`. Om Saravana Bhava sat at 210 Hz.
2. **Clean, cut and repeat:** `chant_build.py raw.wav OUT GAP_S SA_HZ`.
   - Clean: noisereduce, profiled on the room tone before the voice, then adeclick, high-pass, light EQ,
     a de-esser and 2.5:1 compression.
   - Cut: keep the **longest voiced run**, where dips under 0.4 s count as voiced, so a click after the
     voice is left out. Fade in over 15 ms and out over 250 ms.
   - Repeat the identical cycle on a fixed grid: period = cycle + gap (1.1 s gap; 7.2 s cycle gives an
     8.3 s period). The intro is 5 s and the outro 8 s.
   - Don't pitch-correct a chant; leave the voice natural.
   - Write `layout.json` with every start time and every ding time, so the video can sync to it.
3. **Backing locked to the chant period.** No beat tracking is needed.
   - Tanpura: 8 plucks per period (Pa, Sa, Sa, low Sa), ducked about 3 dB under the voice, so it opens up
     in the breaths.
   - A quiet shruti pad that breathes once per period.
   - **A ding after every chant**, 0.05 s after the voice ends:
     - `BELL_KIND=manjira` (the default) is the open manjira stroke from `arrange.py`. **The user chose it**
       in an A/B test after calling the sound a "temple bell".
     - `BELL_KIND=temple BELL_F=<Hz>` is the `arrange.py` stage-D bell.
4. **Mix and master:** `chant_mix.sh OUT OUT/name 0.13 -17 -29 -20`.
   - The arguments are reverb, tanpura, pad and ding levels. The user asked for the ding +2 dB, to −20.
   - It ends with two-pass loudnorm to −14 LUFS and −1.5 dBTP.
   - It needs `OUT/hall_ir.wav` from `make_ir.py`.
5. **Verify the count automatically:** `chant_verify.py mix.wav OUT/voice_cycle.wav OUT/layout.json`.
   - (a) Segment the 300–3400 Hz voice-band energy.
   - (b) Run a normalised cross-correlation of the cycle's own envelope against the mix.
   - Both must give N, at 0 ms offset from the layout. Also report the voice vs backing LUFS; the target
     is the voice about 16 dB in front.
   - Then measure every section; [[feedback-check-every-section-after-change]] applies.
6. **Speed:** synthesize a few tanpura plucks once and reuse them, and smooth the full-length envelope
   with `uniform_filter1d`. A 0.5 s `np.convolve` over 40M samples hangs.

The video for these chants (Tamil and English captions at 75% height, a japa counter, petal and glitter
loops, deity images rotating on the dings) is in video-editor `docs/DEVOTIONAL_CHANTS.md`.

## User preferences (learned)

- Reverb wet **0.13**. The first mix used 0.22, which was too wet.
- Keep the instruments subtle.
  - Tanpura **−17 dB**, pad **−29 dB**. The user asked twice for them to be quieter.
  - Manjira −22 dB, mridangam −19 dB, doubles −9 dB, bell −20 dB.
  - Flute −9 dB on the sine stem, after the user asked for +3 dB. Flute reverb 0.30.
- **No continuous drone**: everything must move with the singing tempo.
- **Melodic instruments must keep the singer's continuous pitch curve.**
  - Beat-quantised flute notes sounded "like a keyboard", even though the timing measured tighter (4 ms against 46 ms off the beat), and the user had it reverted.
  - Judge by feel, not by onset statistics.
- The user liked the in-between (interlude) flute. Never let a change drop it; check each section's level against the previous version.
- Deliver to `~/Downloads/<song>_out/` and send the files with SendUserFile:
  - an A/B clip: 20 s of the original, then the same lines finished;
  - the full song.
- Number each revision v2, v3, and so on. Change one thing per version.

## Paid or optional extras (only if asked)

- **ElevenLabs Voice Isolator** (`mcp__elevenlabs__isolate_audio`) for noisy or echoey recordings. Uses credits.
- **Suno "Add Instrumentals"**: full AI backing music from the vocal. Needs a paid plan, which also gives the commercial rights.
- **AnyAccomp** (open source) also generates backing music, but its licence is CC-BY-NC-ND. Don't use it for monetized uploads.

- **PoYo Vocal Remover** ($0.075 per split) separates vocals from finished songs. It doesn't clean up a cappella recordings, so it's the wrong tool here.
- **Better instruments, not yet built:** real recorded samples for tanpura and percussion (Naad's open-source tabla and tanpura WAVs, SampleScience *Sounds of India*, CC0 recordings on Freesound). They would play on the same beat grid. Check each pack's licence.
- **Claude-driven audio editors:** REAPER MCP (bonfire-systems or itsuzef, 58 tools) and ableton-mcp. These would let Claude use professional sample libraries; REAPER costs about $60.
- **Skip:** Neutone/RAVE sitar models and MusicGen melody mode. Both are non-commercial, and MusicGen's melody control is coarse and Western-biased.

## Not built yet

- A soft bass part (low Sa/Pa on each line's first beat) to close the deep-bass gap against commercial references.
- Shruti-box timbre as an alternative to the tanpura.
- Harmony or duet handling: tune each singer on a separate track against the same Sa.
- Making `arrange.py`'s song constants (SA, SCALE, REFRAINS, CUTS) command-line options, worked out automatically from the structure analysis.
