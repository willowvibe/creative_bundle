# Plugins used with this stack

Plugin-provided skills live in the plugin install, not in `claude/skills/`.
Install the marketplaces first, then the plugins you want.

```bash
/plugin marketplace add anthropics/claude-plugins-official
/plugin marketplace add anthropics/claude-code
```

## Needed for the bundled skills and memory

| Plugin | Marketplace | Why |
|---|---|---|
| `remember` | claude-plugins-official | Loads `video-editor/.remember/` on session start |
| `superpowers` | claude-plugins-official | Workflow skills (brainstorming, TDD, verification) used by repo work |
| `hyperframes` | claude-plugins-official | Full HyperFrames skill set (the local copies are also in `claude/skills/`) |
| `firecrawl` | claude-plugins-official | Research/scrape skills (local copies also present) |
| `youtube-transcriber` | anthropic-plugin-directory | Transcripts for source footage |
| `social-media-skills` | anthropic-plugin-directory | Per-platform publishing guidance |
| `context7` | claude-plugins-official | Library documentation lookup |
| `feature-dev` | claude-plugins-official | Structured feature workflow |
| `code-review`, `pr-review-toolkit` | claude-plugins-official | Review passes before commits |
| `commit-commands` | claude-plugins-official | Commit/PR helpers |
| `frontend-design` | claude-plugins-official | Review-board UI work |
| `explanatory-output-style` | claude-plugins-official | Optional output style |

## Also enabled on the source machine

`claude-plugins-official`: `github`, `pyright-lsp`, `claude-md-management`,
`code-simplifier`, `playwright`, `skill-creator`, `chrome-devtools-mcp`,
`data-engineering`, `ralph-loop`, `claude-code-setup`, `security-guidance`,
`supabase`, `vercel`, `plugin-dev`, `greptile`, `coderabbit`, `kotlin-lsp`,
`expo`, `liquid-skills`, `data-agent-kit`.

Other marketplaces: `zai-coding-plugins` (`glm-plan-usage`), `cloudflare`
(`cloudflare`), `posthog` (`posthog`), `cheapag` (`cheapag`), and
`iamumeransari/claude-show-your-work` (`show-your-work`).

Only the first table is required for these five repos. The rest were part of the
source machine's general setup; install them as needed.

## Not shared

`~/.claude/settings.json`, `settings.local.json`, `.credentials.json`,
`.mcp.json` and plugin installs themselves are machine/account-specific. The
source machine's `settings.local.json` contains personal API keys and machine
paths, so it is deliberately excluded.
