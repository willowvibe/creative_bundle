# YouTube Publish — Design

> **Moved 2026-11-03.** This work now lives in its own repo, `/mnt/data2/git_repos/creative_repos/social-engine` (shared publishing core + YouTube first; Instagram/Facebook/TikTok later). Current versions: `social-engine/docs/specs/2026-11-03-social-engine-youtube-design.md` and `social-engine/.plan/youtube.md`. Kept here for history; do not build from this copy.

Date: 2026-11-03
Status: approved design, awaiting spec review

## Goal

Publish and manage finished videos on the owner's YouTube channels from this repo: upload long-form
and Shorts, set every detail (metadata, thumbnail, chapters, captions, localizations, playlists, AI
disclosure), add dubbed audio tracks, and read analytics. One manifest per video drives everything.

Channels: several, each a named profile (first two: `mantra-shakti`, `cosmicsaju`).

## Why two lanes

The YouTube Data API and the YouTube Studio web UI each cover things the other cannot:

- The Data API is reliable and scriptable but (a) needs a Google Cloud OAuth client, (b) forces
  uploads from an unaudited API project to private regardless of the requested privacy, until the
  project passes Google's API compliance audit, (c) draws on a per-project daily quota, and (d) has
  no endpoint for multi-language (dubbed) audio tracks.
- Studio has none of those limits but is a UI that changes without notice.

So: **Studio lane** for uploads (until the audit passes) and dubbed audio; **API lane** for
everything structured. Both read the same manifest.

| Need | Lane |
|---|---|
| Upload while the project is unaudited | Studio |
| Upload after audit | API (Studio still available) |
| Title, description, tags, category, language, made-for-kids, privacy, `publish_at` | API (Studio sets them at upload time) |
| Thumbnail | API |
| Chapters | Written into the description from `chapters.json` |
| Caption tracks (te/en/hi/ta SRT) | API |
| Localized titles/descriptions | API |
| Playlists (create, add, order) | API |
| List channel videos, read back IDs | API |
| Analytics (views, watch time, retention) | API (YouTube Analytics API) |
| Impressions click-through rate | Studio only (not exposed by the Analytics API) |
| Altered/synthetic content disclosure | API field if available at build time, else Studio |
| Dubbed audio tracks (hi/ta) | Studio only |

Quota figures for `videos.insert` are reported inconsistently (1,600 units from the shared 10,000
pool vs. a separate uploads bucket). The implementation reads Google's current quota page and keeps
costs in one table, not scattered constants.

## Components

### 1. Manifest — `youtube.yaml`

Lives next to the video it describes (e.g. `videos/mantra-rahasyam/ep1/final/youtube.yaml`).

```yaml
channel: mantra-shakti
video: final.mp4
kind: long            # long | short
title: "..."
description: |
  ...
chapters: chapters.json          # optional; rendered into the description
tags: [...]
category: 24                     # Entertainment
default_language: te
default_audio_language: te
made_for_kids: false
synthetic_media: true            # AI disclosure
privacy: private                 # private | unlisted | public
publish_at: null                 # ISO-8601; requires privacy: private (YouTube rule)
thumbnail: thumbnail.jpg
captions:
  te: subs/te.srt
  en: subs/en.srt
localizations:
  en: {title: "...", description: "..."}
playlists: ["Mantra Rahasyam"]
dubs:                            # Studio lane only
  hi: audio/hi.m4a
  ta: audio/ta.m4a
# written back by the tool, never by hand:
video_id: null
uploaded_via: null
uploaded_at: null
```

Paths are relative to the manifest. `yt init <folder>` drafts a manifest from what the folder
holds (final cut, thumbnail, `chapters.json`, SRTs, dub audio) and leaves text fields for review.

Validation (offline, `yt validate`): required fields; title ≤100 chars, description ≤5,000 bytes,
tags ≤500 chars total; no `<`/`>` in title/description; Shorts are vertical and ≤3 min; thumbnail
JPG/PNG ≤2 MB; every referenced file exists; caption/localization language codes are BCP-47;
`publish_at` is in the future and paired with `privacy: private`; estimated quota cost.

### 2. API lane — `packages/youtube` (Python)

- `auth.py`: installed-app OAuth flow per channel. Client secret at
  `~/.config/video-editor/youtube/client_secret.json`; tokens at
  `~/.config/video-editor/youtube/tokens/<channel>.json`, mode 0600. Scopes: `youtube.upload`,
  `youtube.force-ssl`, `yt-analytics.readonly`. After consent, the token's channel ID is stored and
  every later call checks it matches the profile (guards against signing in to the wrong channel).
- `client.py`: thin wrapper over `google-api-python-client` behind a small interface so tests use
  a fake. Resumable uploads with exponential backoff on 5xx/connection errors.
- `manifest.py`: load, validate, render description (chapters appended), write back IDs.
- `ops.py`: `upload`, `update`, `set_thumbnail`, `sync_captions`, `sync_localizations`,
  `sync_playlists`, `list_videos`, `stats`. Each is idempotent: with `video_id` set, `upload`
  refuses and points to `update`; caption/playlist sync only adds or replaces what differs.
- Error mapping: `quotaExceeded`, `uploadLimitExceeded`, the unaudited private lock (detected by
  reading back `status.privacyStatus` after upload), invalid/expired token, wrong channel; each
  becomes a one-line plain-English message plus the next step.
- Writes each publish to the project ledger (`NullLedger` fallback as elsewhere).

### 3. Studio lane — `packages/youtube/studio/` (Node, `playwright-core`)

Same pattern as flow-mcp: a dedicated Chrome profile per channel at
`~/.config/video-editor/youtube/chrome-<channel>/`, started with remote debugging; the driver
attaches over CDP. The owner signs in by hand once (`yt studio login --channel <name>`); the
session persists in that profile.

Flows:
- `upload`: Create → Upload → file → details (title, description, thumbnail, playlists, kids,
  altered content) → visibility. Stops **before** the final Save/Publish/Schedule click, prints the
  summary, and waits for confirmation; then clicks and reads the new video ID from the share link.
- `add-dubs`: open the video's Languages page, add each language from `dubs`, upload audio, stop
  before Publish, confirm, publish.

Robustness: select elements by accessible role and visible label, not CSS classes; after every
step assert the expected screen (heading/dialog present); on any unexpected screen, stop, save a
screenshot under the project's `logs/`, and report where it stopped. Never retries a click blindly.

Hand-off to the owner: sign-in, 2FA, "verify it's you", CAPTCHA, account choosers, and any screen
the driver does not recognise. The driver pauses, says what is on screen, and resumes when told.
The driver never types passwords or solves CAPTCHAs.

Python calls the Node driver as a subprocess with JSON in/out and a timeout, like the existing
HyperFrames calls.

### 4. Surfaces

- CLI `ve yt …`: `auth`, `studio login`, `init`, `validate`, `upload [--via studio|api]`,
  `update`, `captions`, `playlist`, `dubs`, `list`, `stats`. Default `--via` is `studio` until
  `~/.config/video-editor/youtube/<channel>.yaml` records `api_audited: true`.
- MCP tools in the existing `video-editor` server: `yt_validate`, `yt_upload`, `yt_update`,
  `yt_list`, `yt_stats` (the MCP upload tool requires `confirm: true`, mirroring the spend cap
  pattern).
- Skill `youtube-publish`: when to use which lane, the confirmation rule, the hand-off protocol,
  and the drama hook (publishing is the step after the promo gate in `drama-episode`).

## Safety rules

- Default privacy is `private`. Public/unlisted/scheduled only when the manifest says so.
- Every upload, Publish/Schedule click and visibility change waits for the owner's explicit yes in
  chat. Approval covers that one action.
- No delete operations in either lane.
- Tokens and Chrome profiles stay under `~/.config/video-editor/youtube/`, outside the repo and
  outside `.env`; `.gitignore` is not relied on.

## Testing

- Unit: manifest validation and description rendering; ops against a fake client (idempotency,
  error mapping, write-back); CLI argument wiring; MCP `confirm` guard.
- Studio driver: step functions tested against saved HTML fixtures of the Studio screens for
  selector logic; no live Studio in CI.
- Live smoke (manual, once per lane): upload a 5-second test clip as private to one channel, set
  thumbnail + one caption + playlist, read stats, then the owner removes it in Studio.

## Out of scope

Comment moderation, community posts, live streaming, monetization settings, bulk back-catalogue
edits, video deletion.

## Owner setup (one-time)

1. Google Cloud project → enable YouTube Data API v3 and YouTube Analytics API → OAuth consent
   screen (External, add yourself as test user, then publish to production so tokens do not expire
   after 7 days) → OAuth client "Desktop app" → download `client_secret.json`.
2. `ve yt auth --channel mantra-shakti` (and `cosmicsaju`): consent in the browser.
3. `ve yt studio login --channel mantra-shakti`: sign in to Google in the dedicated Chrome window.
4. Optional: apply for the API compliance audit; when granted, set `api_audited: true`.
