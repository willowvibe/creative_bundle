# Agent Reach skill — provenance

- Upstream: https://github.com/Panniantong/Agent-Reach (MIT)
- Version: agent-reach 1.5.0, commit `94f06c1969dfc1834001269d79d3ad0972d9dee6`
- `SKILL.md` is the upstream English skill (`agent_reach/skill/SKILL_en.md`);
  `references/` are upstream as-is (written in Chinese; commands are universal).
- The `agent-reach` CLI is installed per-user with pipx (not in this repo's
  venv), so both `video-editor` and `research-engine` share one install:

  ```bash
  pipx install https://github.com/Panniantong/agent-reach/archive/<commit>.zip
  agent-reach doctor          # which channels work right now
  ```

To update: `pipx upgrade agent-reach` (or reinstall at a newer commit), then
re-copy `SKILL_en.md` -> `SKILL.md` and `references/` from
`$(pipx environment --value PIPX_LOCAL_VENVS)/agent-reach/lib/python3.12/site-packages/agent_reach/skill/`.
