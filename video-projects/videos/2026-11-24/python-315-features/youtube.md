# YouTube upload pack — Python 3.15 (EP 1, @pythondecoder101)

Machine-readable version: `publish.yaml` (validated with `social validate`, quota estimate 2,102 units).
**Uploaded 2026-11-24 as a private video: https://youtu.be/zDa-6B-8f-s** (Studio: https://studio.youtube.com/video/zDa-6B-8f-s/edit)
- Set by `social publish` (browser lane): title, description + chapters, thumbnail, not made for kids, Private.
- Set in Studio afterwards (Claude in Chrome, owner's account): 16 tags (replacing the channel default tags), AI use = Yes,
  playlist "Modern Python 2026" (new, private), English captions from `out/python315_v3.en.srt` (published track).
- Kept channel defaults: category Education, language English (India).
- **Scheduled to go public Sat 28 Nov 2026, 18:30 IST** (8:00 AM US Eastern). Why: no audience-timing data yet for this
  channel ("Not enough data"); India-first English Python audience peaks ~8–11 PM IST, publish ~1–2 h before; US
  Thanksgiving Thu 26 / Fri 27 avoided. Re-check Studio → Analytics 48 h after release and adjust future slots.
- AI use set to No by the owner (channel rule: own voice clone + real recordings).
- Left to do: end screen + pinned comment (Studio). The stray unsaved draft
  "video title : python tut" from the second upload attempt was deleted on the owner's request (2026-11-24).

| Field | Value |
|---|---|
| Video | `out/python315_v5.mp4` (5:21, intro + section titles + SFX + lo-fi music bed + outro, −15.8 LUFS) |
| Thumbnail | `thumb/flow60/flow60_05_1280.jpg` ("PYTHON 3.15 IS OUT!", laptop, 60% stylised from your photo) — chosen by the user |
| Title | Python 3.15 Is Out: 6 Features You'll Actually Use (50 chars) |
| Captions | `out/python315_v3.en.srt`, English, uploaded as a caption track only (nothing burned into the video) |
| Category | Science & Technology (28) |
| Language | English (video and audio) |
| Audience | Not made for kids |
| Altered/synthetic content | Yes (AI voice + animated avatar); your call — see note below |
| Playlist | Modern Python 2026 (created private if missing) |
| Privacy | Private draft first |

Description, chapters, tags: see `publish.yaml`.

## Before the first upload (one time)

The channel has no social-engine login yet. Create `~/.config/social-engine/youtube/accounts/python-decoder.yaml`
like `mantra-shakti.yaml` and run the OAuth sign-in for the @pythondecoder101 channel (the owner signs in; I never
handle the password).

## In YouTube Studio after upload (not settable through the API)

- **End screen:** Editor → End screen → "1 video, 1 subscribe". Put the video element on the yellow frame (last 12 s)
  and the subscribe element on the circle. Until the uv video exists, use "Best for viewer".
- **Cards:** none yet. When the uv video is out, add a card at 0:43 ("If uv is new to you, my next video…").
- **Test & Compare (thumbnails):** `flow60_05` (default) vs `flow60_04` ("6 NEW TOOLS") vs `flow60_01`
  ("4x FASTER STARTUP"; the 4× is the lazy-import demo only). Ask before starting a test.
- **Pinned comment:**
  > Which 3.15 feature are you trying first? I'm switching every CLI tool to `lazy import` 😄
  > All demo code is in the description commands; tell me if your timings differ.
- **Publish slot:** no analytics for this channel yet. Suggest a weekend morning in IST (Sat 09:00) for the first
  video, then read Studio → Analytics after 48 h and adjust.

## Notes

- Synthetic-content label: YouTube asks for it when realistic content could mislead viewers about a real person or
  event. The voice is an ElevenLabs voice of you reading your own script and the avatar is clearly animated, so it
  may not be required; it's set to *yes* for transparency, and you can switch it off in Studio.
- Thumbnail and video use the Python logo shapes; that's normal community use under the PSF trademark policy
  (not implying PSF endorsement).
- Earlier thumbnail sets (PoYo cartoon `thumb/thumb_p*_1280.jpg`, Pillow drafts) stay as alternatives.
