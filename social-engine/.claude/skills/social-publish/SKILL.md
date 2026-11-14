---
name: social-publish
description: Publish and manage finished videos on the owner's social accounts (YouTube first) with the social CLI or the social-engine MCP server. Use when a finished video folder has a publish.yaml and the owner wants to upload it, change its metadata, add captions/localizations/playlists/dubs, or read stats.
---

# social-publish

Use this skill when the owner wants a finished video (usually from
`../video-editor/videos/<date>/<project>/`) published or managed on YouTube.

## Non-negotiable rules

- **Nothing becomes visible without the owner's explicit yes in chat for that
  action.** A summary is not an approval. One approval covers one action.
- The code enforces the gate: `--yes` on the CLI, `confirm: true` in MCP, and the
  browser lane's `ready_to_confirm` → `confirm` step. Do not route around it.
- **No deletes.** Ever, on any platform.
- The browser lane hands sign-in, 2FA, "verify it's you", CAPTCHA and unknown
  screens back to the owner. Never type passwords or solve a CAPTCHA.
- Default privacy is `private`. Show the summary (title, account, privacy,
  `publish_at`, lane, playlists, dubs, quota) before asking for the yes.

## Owner setup (YouTube, one-time)

1. Google Cloud project → enable **YouTube Data API v3** and **YouTube Analytics
   API** → OAuth consent screen (External, add yourself as a test user, then
   publish to production so tokens do not expire after 7 days) → OAuth client
   "Desktop app" → save as
   `~/.config/social-engine/youtube/client_secret.json`.
2. `social auth youtube mantra-shakti` (and `cosmicsaju`): consent in the browser.
3. `social login youtube mantra-shakti`: sign in to Google in the dedicated Chrome
   window.
4. Optional: apply for the API compliance audit; when granted, set
   `api_audited: true` in the account file under
   `~/.config/social-engine/youtube/accounts/`.

State lives under `~/.config/social-engine/` unless `SOCIAL_ENGINE_HOME` is set.

## Lane table (YouTube)

| Need | Lane |
|---|---|
| Upload while the API project is unaudited | Browser (Studio) |
| Upload after audit | API (browser still available) |
| Title, description, tags, category, language, made-for-kids, privacy, `publish_at` | API (Studio sets them at upload time) |
| Thumbnail | API |
| Chapters | Written into the description from `chapters.json` |
| Caption tracks | API |
| Localized titles/descriptions | API |
| Playlists | API |
| List channel videos | API |
| Analytics (views, watch time, retention) | API |
| Impressions click-through rate | Studio only |
| Altered/synthetic content disclosure | API field when the build supports it, else Studio |
| Dubbed audio tracks | Browser (Studio) only |

Default `--via` for YouTube is `browser` unless the account file has
`api_audited: true`.

**Brand Account managers:** manager access granted through YouTube Studio
permissions is not recognized by the YouTube Data API. The browser lane works for
uploads, metadata and dubs; API-only steps (captions, localizations, playlists,
analytics) need the Brand Account owner's token or manual Studio work.

## Hand-off protocol

When a command exits **3** or the driver returns `needs_user`:

1. Tell the owner exactly what is on screen (sign-in, 2FA, verify it's you,
   CAPTCHA, account chooser) and which Chrome window/account it belongs to.
2. Give the screenshot path printed by the command.
3. Wait for the owner. Do not retry with a different route.
4. Rerun the same command.

When the browser lane returns `ready_to_confirm`, show the summary and ask
`Publish now?` / wait for the owner's yes. Only then run the `confirm` step.

## Drama episode order

1. `social init <folder> --account <account>` — draft `publish.yaml`.
2. Owner edits the `# TODO(owner)` title, description and tags.
3. `social validate <manifest>` — fix every error before uploading.
4. `social publish <manifest> --yes` — publish **private** first.
5. `social yt captions <manifest>` — insert/update SRT tracks.
6. `social yt localize <manifest>` — default language and localized metadata.
7. `social yt playlist <manifest>` — create missing playlists and add the video.
8. `social yt dubs <manifest> --yes` — browser lane, dubbed audio.
9. Owner reviews the private video and approves visibility.
10. `social update <manifest> --yes` — privacy change (the only visible change).
11. `social stats <manifest> --days 28` — window and lifetime metrics.

## Quick commands

```bash
social init <folder> --account mantra-shakti
social validate <folder>/publish.yaml
social publish <folder>/publish.yaml --via browser --yes
social update <folder>/publish.yaml --yes
social list youtube mantra-shakti --limit 20
social stats <folder>/publish.yaml --days 28
social yt captions <folder>/publish.yaml
social yt localize <folder>/publish.yaml
social yt playlist <folder>/publish.yaml
social yt dubs <folder>/publish.yaml --yes
```

MCP: `social_validate`, `social_publish`, `social_update`, `social_list`,
`social_stats`. `social_update` needs `confirm: true`; the API lane of
`social_publish` needs `confirm: true`; the browser lane stages on the first call
and publishes on a second call with `confirm: true` and the returned id.

## Notes

- CTR is read in YouTube Studio; the Analytics API does not expose it.
- Caption tracks not in the manifest are reported as warnings, never deleted.
- Live Studio's AI disclosure is under **Show advanced settings → AI use →
  Yes, AI was used**.
- Studio can reset the upload dialog when the browser driver disconnects, leaving
  the upload as a private draft. Use `--yes` for a one-pass upload and Save, or
  finish the private draft in Studio.
- Tests never touch the network or a real account. Live verification is done
  with the owner on their channel.
- Live findings are recorded in `docs/live-verification.md`, including the 2026-11-04 Studio map for
  the Languages page (per-language title/description, thumbnail, audio, subtitles) and Details page
  (tags, category, title/description language).
- As of 2026-11-04 `social yt dubs` does not match live Studio; until it is rewritten, add dubs,
  subtitles, localizations and per-language thumbnails by driving the Languages page on one CDP
  connection, using that map.
- YouTube may auto-create an AI dub (seen: English). Report it to the owner; never remove it.
- Large uploads (>50 MB) need `setLocalFiles()`; see `browser/lib/files.mjs`.
