# Claude Code tooling

This repo is primarily a Python video-editing engine, but we use Claude Code for development. This document collects optional tooling that makes Claude sessions cheaper or more effective.

## pxpipe — token-saving Claude Code proxy

[pxpipe](https://github.com/teamchong/pxpipe) is a local HTTP proxy that renders bulky text context (tool results, earlier conversation turns, system prompt/tool docs) as PNG images. The model reads those images through its vision channel instead of consuming text tokens. The repo reports ~59–70% end-to-end token savings on production Claude Code traffic.

### Quick start

Terminal 1 — start the proxy:

```bash
pnpm pxpipe
# or: scripts/pxpipe.sh
```

The dashboard is available at http://127.0.0.1:47821/.

Terminal 2 — run Claude Code through the proxy:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:47821
claude
```

Or combine them:

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:47821 claude
```

### Available scripts

| Script | What it does |
|--------|--------------|
| `pnpm pxpipe` | Starts `pxpipe-proxy` on the default port. |
| `pnpm pxpipe:dashboard` | Opens the dashboard in your default browser. |
| `scripts/pxpipe.sh` | Bash wrapper around `npx pxpipe-proxy`; useful if `pnpm` is not installed globally. |

### Caveats and model allowlist

- **Lossy.** Verbatim recall of exact strings — especially byte-exact identifiers such as 12-character hex hashes — is unreliable from rendered images. Keep that in mind when reviewing code hashes, version IDs, or cryptographic values.
- **Added latency.** Every request is rendered locally before it leaves the machine.
- **Character support.** ASCII and Latin-1 are well tested; CJK is described as working but conservative.
- **Model defaults.** `claude-fable-5` is allowlisted by default. Opus 4.8 and GPT 5.5 must be opted in because they misread dense renders more often.

### Installation

`pxpipe-proxy` is declared as a dev dependency in `package.json`, so `pnpm install` (run by `scripts/bootstrap.sh`) will install it automatically.

### Configuration

A commented placeholder for `ANTHROPIC_BASE_URL` is included in `.env.example`. The proxy is read at process startup by Claude Code itself, not by the video editor engine, so this entry only affects development sessions.
