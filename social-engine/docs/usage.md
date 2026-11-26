# social-engine usage

`social` publishes finished videos and manages them afterwards. The manifest is
`publish.yaml`; paths are relative to that file.

State lives under `~/.config/social-engine/` (override with `SOCIAL_ENGINE_HOME`).
Never put secrets or browser profiles in the repo.

## Commands

### Sign in to the browser lane

```bash
social login youtube mantra-shakti
```

Starts the dedicated Chrome profile and waits for the owner to sign in. A
hand-off exits 3 with a screenshot path; finish the screen, then rerun.

### Authorize the API lane

```bash
social auth youtube mantra-shakti
```

Runs the installed-app OAuth flow and stores the token with mode 0600. The
`client_secret.json` must already exist (see the skill's owner setup).

### Draft a manifest

```bash
social init ../video-editor/videos/2026-11-03/ep1 --account mantra-shakti
```

Writes `publish.yaml` in the folder. It refuses to overwrite an existing file.
Edit the `# TODO(owner)` title, description and tags before validating.

### Validate offline

```bash
social validate ../video-editor/videos/2026-11-03/ep1/publish.yaml
```

Checks required fields, referenced files, language codes and every YouTube rule,
then prints a quota estimate. Exit code 1 means at least one error.

### Publish

```bash
# Browser lane is the default until the account has api_audited: true.
social publish ../video-editor/videos/2026-11-03/ep1/publish.yaml

# With --yes it uploads and clicks the final Studio button.
social publish ../video-editor/videos/2026-11-03/ep1/publish.yaml --yes

# Force the API lane (requires the OAuth token and, for visibility, --yes).
social publish ../video-editor/videos/2026-11-03/ep1/publish.yaml --via api --yes
```

Without `--yes`, the API lane prints a preview and exits 0. The browser lane still
opens Studio, fills the details, stops at the final button, prints the
`ready_to_confirm` summary and asks `Publish now? [y/N]`.

> **Live Studio note:** Studio can reset the upload dialog when the Node driver
> disconnects, leaving the upload as a private draft. For a one-pass upload and
> Save, use `--yes`. Without `--yes`, finish the draft in Studio or rerun with
> `--yes` (which starts a fresh upload).
>
> **AI disclosure:** in live Studio it is under **Show advanced settings → AI
> use → Yes, AI was used**.

### Brand Account managers

Manager access granted through YouTube Studio permissions is not recognised by the
YouTube Data API. The browser lane can upload, edit metadata and add dubs for those
accounts, but captions, localizations, playlists and stats need the Brand Account
owner's token or manual Studio work.

### Update a live video

```bash
social update ../video-editor/videos/2026-11-03/ep1/publish.yaml --yes
```

Sends only the sections that differ. A privacy or `publish_at` change is the
visible action; the CLI requires `--yes` before it will act.

### List the channel

```bash
social list youtube mantra-shakti --limit 20
```

Prints newest first: id, privacy, published time, views, title.

### Read stats

```bash
social stats ../video-editor/videos/2026-11-03/ep1/publish.yaml --days 28
```

Window views, watch time, retention, likes and subscribers, plus lifetime
counters. CTR is not exposed by the Analytics API; read it in YouTube Studio.

### YouTube extras

```bash
social yt captions ../video-editor/videos/2026-11-03/ep1/publish.yaml
social yt localize ../video-editor/videos/2026-11-03/ep1/publish.yaml
social yt playlist ../video-editor/videos/2026-11-03/ep1/publish.yaml
social yt dubs ../video-editor/videos/2026-11-03/ep1/publish.yaml --yes
```

Captions insert missing tracks and update changed SRTs; extra tracks are warned
about and never deleted. Localize sets the default language when missing and
replaces the localizations map. Playlist creates missing playlists as private
and adds the video once. Dubs is the browser lane; without `--yes` it stops at
`ready_to_confirm` and prompts.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success, or a preview/prompt that was declined |
| 1 | `validate` found error-severity issues |
| 2 | A `SocialError`: prints `code: message` and `next: ...` |
| 3 | The owner must act in Chrome (`BrowserNeedsUser`); the screenshot path is printed |

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `SOCIAL_ENGINE_HOME` | `~/.config/social-engine` | Tokens, account files, Chrome profiles, logs |
| `SOCIAL_ENGINE_ROOTS` | `/mnt/data2/git_repos/video-editor/videos` | Colon-separated roots the MCP server may read manifests from |
| `SOCIAL_ENGINE_DRIVER` | `<repo>/browser/driver.mjs` | Override the Node browser driver path |
| `SOCIAL_ENGINE_VISIBLE` | unset | Set to `1` to launch the browser lane's Chrome on the owner's desktop instead of a hidden `:99` display (Linux) |
| `SOCIAL_ENGINE_XVFB_DISPLAY` | `:99` | X display the browser lane's hidden Chrome runs on (Linux, when Xvfb is available) |

`social-engine` does not read `.env`; the keys there are not used by this project.
Put the OAuth client and tokens under `SOCIAL_ENGINE_HOME`.

## MCP server

Register the stdio server from `.mcp.json.example`:

```json
{
  "mcpServers": {
    "social-engine": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/social-engine", "social-engine-mcp"],
      "env": {
        "SOCIAL_ENGINE_ROOTS": "/mnt/data2/git_repos/video-editor/videos"
      }
    }
  }
}
```

Tools: `social_validate`, `social_publish`, `social_update`, `social_list`,
`social_stats`. Manifest paths are resolved (after symlinks) and must stay under
`SOCIAL_ENGINE_ROOTS`. `social_update` requires `confirm: true`. The API lane of
`social_publish` requires `confirm: true`; the browser lane stages on the first
call and publishes on a second call with `confirm: true` and the returned id.

> **Live caveat:** Studio can reset the staging dialog when the driver
> disconnects. In that case the returned id is a private draft and the second call
> may not find the dialog. Use the CLI `--yes` path for a one-pass upload and Save,
> or finish the draft in Studio.

## Tests

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
npm run test:browser
```

## Live findings

See `docs/live-verification.md` for the first real run, the Brand Account manager
limitation, the live Studio locators and the dialog-reset behaviour.
