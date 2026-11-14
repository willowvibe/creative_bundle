---
name: youtube-packaging
description: Use before any Mantra Shakti upload (drama episode, mantra video or Short) and 48 h / 7 days after it, to make and judge the title + thumbnail package for click-through: Telugu devotional rules, per-language variants, the 320 px legibility check, Studio Test & Compare, and reading CTR/AVD in Studio. Loads the general social-media-skills (thumbnail-design, youtube-long-form, youtube-publishing-and-metadata, experimentation-and-ab-testing) for principles and adds what is specific to this channel and pipeline.
---

# YouTube packaging (CTR) for Mantra Shakti

CTR is decided by the title + thumbnail as one package; watch time decides whether the click counts. YouTube's
Test & Compare picks the winner by **watch-time share, not raw CTR**, so an honest package beats a clickbait
one. Never promise what the video doesn't show.

General principles come from these installed skills. Load the one you need before drafting:
- `social-media-skills:thumbnail-design` (CLICK framework, A/B plan)
- `social-media-skills:youtube-long-form` (CTR vs AVD diagnosis)
- `social-media-skills:youtube-publishing-and-metadata` (title, description, tags)
- `social-media-skills:experimentation-and-ab-testing`
- `social-media-skills:youtube-shorts` (for the Shorts that feed the episode)

## Channel facts (Studio, last 28 days to 2 Nov 2026; refresh before each release)

- 636 monthly viewers, 67.5% India, 78.8% mobile, 93.6% of watch time from non-subscribers. Packaging has
  to win cold viewers in the feed, not fans.
- Subtitle use: Hindi 9.4%, Marathi 2.1%. Many viewers are not Telugu-first, so localized titles and
  thumbnails matter.
- Peak hours (IST): 8 PM Sun/Wed, 6–7 PM Fri, 10 PM; secondary 8–10 AM. Publish about 1 h before a peak.
- Analytics API is blocked while we are Brand Account managers; read Studio → Analytics in the browser
  (social-engine `docs/live-verification.md`). With an owner token, `social stats <manifest>` works (no CTR via API).

## Thumbnail rules (this niche)

1. **One emotional face + the divine.** Ep1's pattern: tearful praying father (left) + Shiva in light
   (right), rising soul between. A strong human emotion beats a scene. Never smiling during grief.
2. **3–4 word hook in the viewer's script**, gold on maroon outline, bottom third; a series badge top-left
   (`మంత్ర రహస్యం · EP n`). No sentence, no small text, nothing in the bottom-right (duration badge).
3. **Legible at feed size.** Check every candidate at 320 px wide before calling it done:
   `ffmpeg -i thumb.jpg -vf scale=320:-1 thumb_320.png` and look. If the hook or the face doesn't read, fix it.
4. **Make it in Flow** (Nano Banana Pro, 0 credits). Re-letter per language from the chosen image; remove the
   sparkle with reverse alpha blending (playbook §8). Spell-check every script by reading it.
5. **Consistent series look** so returning viewers recognise it: same badge, palette, lettering.

## Title rules

- 50–70 characters total; the hook lands in the first ~45 (mobile cut). Telugu first, then an English
  keyword tail for search: `<Telugu hook> | <series> EP n | <English keyword phrase>`.
- Curiosity or emotion plus a concrete noun (the mantra, the deity, the stakes). Avoid "Part", dates, ALL CAPS.
- Write the matching localized title per dub language in `publish.yaml` `youtube.localizations`.

## Test & Compare (Studio)

- On this channel the Details page shows **A/B testing** next to the title (seen 2026-11-04). Up to 3
  variants of title, thumbnail, or both; not for Shorts. The winner is chosen by watch-time share; let it
  run at least 7 days or until Studio declares a result. Low traffic may end it inconclusive.
- Make 3 **meaningfully different** concepts that change one variable, e.g. (a) the father's emotion + Shiva,
  (b) a Shiva close-up with the hook, (c) the soul and the Yama messengers (stakes).
- Setting up or changing a test changes public packaging: **ask the user first**. Record variants and the
  result in the episode folder and `publish-log.json`.

## Review cadence

- **48 h:** Studio → video → Analytics → Reach: impressions, CTR, views from Browse/Suggested; Engagement:
  average view duration, the 30-second retention dip. Compare with the channel's typical range that
  Studio shows. Never invent a benchmark.
- **Diagnose:** low CTR + good AVD → fix packaging (new thumbnail/title, or a test). Good CTR + low AVD →
  the package over-promises or the cold open is weak. Both low → topic/audience fit.
- **7 days:** read the Test & Compare result and note what won in the episode README, so the next episode
  starts from the winner.
- Post the Shorts after the episode is public, each linking the full episode (related video).

## Before upload checklist

- [ ] 3 thumbnail concepts made, one chosen, 320 px check passed, per-language versions made and spell-checked
- [ ] 3 title options written; the chosen one fits the mobile cut; localized titles in `publish.yaml`
- [ ] Package matches what the first 30 s actually show (no bait)
- [ ] Publish slot chosen from the current heatmap
- [ ] Test & Compare variants ready (ask the user before starting the test)
