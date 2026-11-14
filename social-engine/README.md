# social-engine

Publishes finished videos to the owner's social accounts and manages them afterwards. One
`publish.yaml` next to a video describes it for every platform; `social …` (CLI) and the
`social-engine` MCP server act on it.

Platforms: YouTube first. Instagram, Facebook and TikTok follow on the same core.

Each platform has two lanes:

- **API lane**: the platform's official API with OAuth, for structured work (metadata, captions,
  playlists, stats).
- **Browser lane**: a Playwright driver attached to a dedicated Chrome profile per account, for what
  the API cannot do or will not allow yet (YouTube uploads before the API audit, dubbed audio).
  The owner signs in by hand; the driver hands sign-in, 2FA and CAPTCHA screens back to the owner.

Nothing is made visible without the owner's explicit confirmation. Nothing is deleted.

## Quick start

```bash
social auth youtube mantra-shakti     # once: OAuth token
social login youtube mantra-shakti    # once per profile: owner signs in to Chrome
social init <video-folder> --account mantra-shakti
# edit the TODO(owner) title, description and tags
social validate <video-folder>/publish.yaml
social publish <video-folder>/publish.yaml --yes
```

`publish` defaults to the browser lane until the account file says
`api_audited: true`. Without `--yes` the API lane only prints a preview; the
browser lane stops at `ready_to_confirm` and asks before clicking the final
button.

**Brand Account managers:** manager access granted through YouTube Studio
permissions is not recognised by the YouTube Data API. The browser lane works for
uploads, metadata and dubs; API-only steps need the Brand Account owner's token
or manual Studio work.

`social-engine` does not read `.env`. OAuth tokens, account files and browser
profiles live under `~/.config/social-engine/`.

**Live Studio note:** Studio can reset the upload dialog when the driver
disconnects, leaving a private draft. Use `--yes` for a one-pass upload and Save,
or finish the draft in Studio.

The videos come from `../video-editor` (`videos/<date>/<project>/`), but this repo imports none of
its code: the hand-off is the folder.

- Design: `docs/specs/2026-11-03-social-engine-youtube-design.md`
- Plan: `.plan/youtube.md` (Cheap Agent step format)
- Usage: `docs/usage.md`
- Live verification: `docs/live-verification.md`
- Agent skill: `.claude/skills/social-publish/SKILL.md`
- MCP registration example: `.mcp.json.example`
