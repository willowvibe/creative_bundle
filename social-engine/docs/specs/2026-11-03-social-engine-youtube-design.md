# social-engine: Core + YouTube — Design

Date: 2026-11-03
Status: approved design (moved from `video-editor/docs/superpowers/specs/2026-11-03-youtube-publish-design.md`)

## Goal

A standalone repo that publishes finished videos to the owner's social accounts and manages them
afterwards. This spec covers the shared core and the first platform, YouTube: upload long-form and
Shorts, set every detail (metadata, thumbnail, chapters, captions, localizations, playlists, AI
disclosure), add dubbed audio tracks, and read analytics. Instagram, Facebook and TikTok get their
own specs later, on the same core.

Accounts: several per platform, each a named account (first two on YouTube: `mantra-shakti`,
`cosmicsaju`).

## Boundary with video-editor

`video-editor` renders; `social-engine` publishes. The only contract is a folder: the video,
thumbnail, `chapters.json`, SRTs, dub audio and a `publish.yaml`. social-engine imports no
video-editor code and video-editor imports none of social-engine. video-editor's drama-episode skill
calls `social publish <folder>` after the promo gate.

## Why two lanes per platform

For YouTube, the Data API and the Studio web UI each cover things the other cannot:

- The Data API is reliable and scriptable but (a) needs a Google Cloud OAuth client, (b) forces
  uploads from an unaudited API project to private regardless of the requested privacy, until the
  project passes Google's API compliance audit, (c) draws on a per-project daily quota, and (d) has
  no endpoint for multi-language (dubbed) audio tracks.
- Studio has none of those limits but is a UI that changes without notice.

The same split is expected on the other platforms (Instagram/Facebook publishing needs a Business
or Creator account linked to a Page and a reviewed Meta app; TikTok's Content Posting API keeps
posts from unaudited apps private), so the browser lane is core, not a YouTube special case.

YouTube lane choice:

| Need | Lane |
|---|---|
| Upload while the API project is unaudited | Browser (Studio) |
| Upload after audit | API (browser still available) |
| Title, description, tags, category, language, made-for-kids, privacy, `publish_at` | API (Studio sets them at upload time) |
| Thumbnail | API |
| Chapters | Written into the description from `chapters.json` |
| Caption tracks (te/en/hi/ta SRT) | API |
| Localized titles/descriptions | API |
| Playlists (create, add) | API |
| List channel videos | API |
| Analytics (views, watch time, retention) | API (YouTube Analytics API) |
| Impressions click-through rate | Studio only (not exposed by the Analytics API) |
| Altered/synthetic content disclosure | API field if present at build time, else Studio |
| Dubbed audio tracks (hi/ta) | Browser (Studio) only |

Quota figures for `videos.insert` are reported inconsistently (1,600 units from the shared 10,000
pool vs. a separate uploads bucket). The implementation reads Google's current quota page and keeps
costs in one table.

## Core

### Manifest — `publish.yaml`

Owner-authored, never rewritten by the tool. Shared fields at the top, one section per platform.

```yaml
video: final.mp4
title: "..."
description: |
  ...
thumbnail: thumbnail.jpg
language: te                     # spoken language of the video
synthetic_media: true            # AI disclosure, all platforms
captions:
  te: subs/te.srt
  en: subs/en.srt

youtube:
  account: mantra-shakti
  kind: long                     # long | short
  title: null                    # optional override of the shared title
  description: null              # optional override
  chapters: chapters.json        # appended to the description
  tags: [...]
  category: 24                   # Entertainment
  made_for_kids: false
  privacy: private               # private | unlisted | public
  publish_at: null               # ISO-8601; requires privacy: private (YouTube rule)
  localizations:
    en: {title: "...", description: "..."}
  playlists: ["Mantra Rahasyam"]
  dubs:                          # browser lane only
    hi: audio/hi.m4a
    ta: audio/ta.m4a
```

Paths are relative to the manifest. Platform sections are optional; a manifest can target one
platform or several.

### Publish log — `publish-log.json`

Written by the tool next to the manifest; the record of what happened, per platform and account:
`{"youtube": {"account": "mantra-shakti", "id": "...", "url": "...", "via": "browser|api",
"published_at": "...", "history": [{"action", "at", "changed", "warnings"}]}}`. A video already in
the log is never uploaded again; `publish` points to `update`. Caption hashes for change detection
are stored here too.

### Accounts and paths

State lives under `~/.config/social-engine/` (env `SOCIAL_ENGINE_HOME` overrides):
`<platform>/client_secret.json`, `<platform>/tokens/<account>.json` (0600),
`<platform>/accounts/<account>.yaml` (`platform_account_id`, `cdp_port`, `api_audited`), and
`browser/<platform>-<account>/` (Chrome profile). After OAuth, the platform's own account id is
stored and checked on every call (guards against signing in to the wrong channel).

### Validation

Offline, `social validate`: required fields; every referenced file exists; language keys are
BCP-47-like; plus each platform's rules (YouTube: title ≤100 chars, description ≤5,000 bytes, no
`<`/`>`, tags ≤500 chars, Shorts vertical and ≤3 min, thumbnail JPG/PNG ≤2 MB, `publish_at` future
and with `privacy: private`) and a quota estimate.

### Browser driver — `browser/` (Node, `playwright-core` 1.63.0)

Same pattern as flow-mcp: a dedicated Chrome profile per platform account, started with remote
debugging; the driver attaches over CDP; the owner signs in by hand once
(`social login youtube mantra-shakti`); the session persists in the profile.

The driver is a CLI: `node browser/driver.mjs <platform> <command> --payload <file>` printing one
JSON object: `status ∈ {done, ready_to_confirm, needs_user, error}` plus `summary`, `id`,
`screenshot`, `message`. It is stateless; Chrome holds the state, so a flow can stop at the final
dialog (`ready_to_confirm`) and a later `confirm` call re-attaches and clicks.

Robustness: elements by accessible role and visible label, never CSS classes; every step asserts
the expected screen; any unexpected screen stops with a screenshot under `logs/`. No blind retries.

Hand-off: sign-in, 2FA, "verify it's you", CAPTCHA, account choosers and any unrecognised screen
return `needs_user`; the owner acts in that Chrome window and the command is rerun.

(The shared repo guidance prefers the `agent-browser` CLI for interactive browsing. That stays the
tool for exploring Studio by hand while writing flows; the driver uses playwright-core because it
must run unattended and repeatably, as flow-mcp does.)

### Confirmation gate

Every action that makes something visible requires an explicit confirmation: `--yes` on the CLI
after a printed summary, `confirm: true` in MCP, and the browser lane's `ready_to_confirm` →
`confirm` step. Default privacy is private. No delete operations exist.

## YouTube platform

- `auth`: installed-app OAuth flow per account. Scopes `youtube.upload`, `youtube.force-ssl`,
  `yt-analytics.readonly`.
- `api`: thin wrapper over `google-api-python-client` behind a Protocol so tests use a fake.
  Resumable uploads with exponential backoff on 5xx and connection errors.
- `ops`: `upload`, `update`, `set_thumbnail`, `sync_captions`, `sync_localizations`,
  `sync_playlists`, `list_videos`, `stats`; each idempotent against the publish log.
- Errors: `quotaExceeded`, `uploadLimitExceeded`, the unaudited private lock (detected by reading
  back `status.privacyStatus` after upload), expired token, wrong channel, custom thumbnails not
  allowed (unverified channel); each becomes a one-line message plus the next step.
- Browser flows: `upload` (Create → Upload → details → visibility; stops before the final
  Save/Publish/Schedule and returns `ready_to_confirm` with the new video id) and `add-dubs` (video's
  Languages page → add language → add dub → upload audio; stops before Publish).

## Surfaces

- CLI `social` (Typer): `login <platform> <account>`, `auth <platform> <account>`, `init <folder>`,
  `validate <manifest>`, `publish <manifest> [--platform youtube] [--via browser|api] [--yes]`,
  `update <manifest> [--yes]`, `list <platform> <account>`, `stats <manifest>`, and YouTube extras
  under `social yt`: `captions`, `localize`, `playlist`, `dubs`. Default `--via` for YouTube is
  `browser` until the account file says `api_audited: true`.
- MCP server `social-engine` (stdio, Python `mcp` SDK): `social_validate`, `social_publish`
  (requires `confirm: true`), `social_update`, `social_list`, `social_stats`. Manifest paths must
  resolve inside allowed roots (default: `/mnt/data2/git_repos/creative_repos/video-editor/videos`, configurable).
- Skill `social-publish` in this repo's `.claude/skills/`.

## Testing

- Unit: manifest + validation, publish log, accounts/auth with fake flows, YouTube ops against a
  fake API, CLI with CliRunner, MCP confirm guard and path checks.
- Browser driver: step engine and flows against local HTML fixtures in headless Chromium.
- Live (manual, with the owner): a 5-second private test clip through both lanes on one channel;
  owner removes it in Studio afterwards.

## Out of scope (this spec)

Instagram, Facebook, TikTok (own specs next); comment moderation, community posts, live streaming,
monetization, bulk back-catalogue edits, deletion.

## Owner setup (one-time, YouTube)

1. Google Cloud project → enable YouTube Data API v3 and YouTube Analytics API → OAuth consent
   screen (External, add yourself as test user, then publish to production so tokens do not expire
   after 7 days) → OAuth client "Desktop app" → save as
   `~/.config/social-engine/youtube/client_secret.json`.
2. `social auth youtube mantra-shakti` (and `cosmicsaju`): consent in the browser.
3. `social login youtube mantra-shakti`: sign in to Google in the dedicated Chrome window.
4. Optional: apply for the API compliance audit; when granted, set `api_audited: true`.
