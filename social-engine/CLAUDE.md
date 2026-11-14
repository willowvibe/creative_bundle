# social-engine

Publishing and channel management for the owner's social accounts. Read `README.md` first.

## Rules

- Never make anything visible (upload, publish/schedule click, privacy change) without the owner's
  yes in chat for that action. The code enforces it (`--yes`, `confirm: true`, Studio
  `ready_to_confirm` → `confirm`); don't route around it.
- No delete operations anywhere.
- The browser lane hands sign-in, 2FA, "verify it's you", CAPTCHA and unknown screens to the owner.
  Never type passwords or solve CAPTCHAs.
- Secrets and browser profiles live under `~/.config/social-engine/` (override: `SOCIAL_ENGINE_HOME`),
  never in the repo or `.env`. `social-engine` does not read `.env`.
- Brand Account managers added through YouTube Studio permissions are not recognised by the YouTube
  Data API. Use the browser lane for those accounts; API-only work needs the owner's token or legacy
  Brand Account permissions.
- Live Studio's AI disclosure is under **Show advanced settings → AI use → Yes, AI was used**.
  Studio can reset the upload dialog when the driver disconnects: use `--yes` for a one-pass upload
  and Save, or finish the private draft in Studio.
- Studio closes open dialogs/panels when the CDP client disconnects, and a second CDP client attaching
  mid-flow can hang the driver behind a "Reload site?" prompt. Run a multi-step Studio flow on one
  connection and never probe the window while a flow runs. Agree with the owner who is clicking.
- Files over 50 MB go through `browser/lib/files.mjs` `setLocalFiles()` (CDP `DOM.setFileInputFiles`);
  plain `setInputFiles` refuses them over CDP.
- Studio's language picker ticks "Make this the default for my channel" by default; untick it.
- Live findings and the current Studio selector map: `docs/live-verification.md`.
- Tests never hit the network or a real account: APIs are faked, browser flows run on local HTML
  fixtures.

## Commands

- Python tests: `uv run pytest tests/<file>.py -q` (one file at a time)
- Lint/types: `uv run ruff check .` and `uv run mypy src`
- Browser driver tests: `npm run test:browser`

## Layout

- `src/social_engine/core/`: manifest, accounts, paths, publish log, errors, media, browser bridge
- `src/social_engine/platforms/<platform>/`: API client, operations
- `browser/`: Node driver (`playwright-core`), `browser/platforms/<platform>/` flows
- `src/social_engine/cli.py`, `src/social_engine/mcp_server.py`: surfaces
- `docs/usage.md`, `docs/live-verification.md`: operator usage and live findings
