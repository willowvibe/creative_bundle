# Claude context for the shared repos

Everything in this folder is meant to be installed **once**, outside the repos,
into the friend's own Claude Code setup.

## Skills

`skills/` holds the user-level skills the stack was built with:

| Skill | Why it matters here |
|---|---|
| `video-editor` (repo-local) | `ve` workflows; already in `video-editor/.claude/skills/` |
| `social-publish` (repo-local) | publishing flow; already in `social-engine/.claude/skills/` |
| `drama-episode` (repo-local) | drama production; already in `drama-studio/.claude/skills/` |
| `vocal-to-song` | the music skill, linked to the bundled repo |
| `hyperframes`, `hyperframes-cli`, `hyperframes-registry` | composition/render CLI used by `ve` |
| `media-use`, `remotion-to-hyperframes`, `remotion-best-practices`, `slideshow` | motion-graphics and asset workflows |
| `map-animation` | animated map recipes used by explainers |
| `poyo-ai-models`, `figma`, `agent-reach`, `delegating-to-opencode` | provider/model, design, browse and delegation helpers |
| `find-skills`, `firecrawl*`, `supabase*` | general research and database skills |

Install them by symlink so updates in the bundle stay live:

```bash
cd /path/to/creative_bundle
mkdir -p ~/.claude/skills
for d in claude/skills/*/; do
  ln -sfn "$PWD/$d" ~/.claude/skills/"$(basename "$d")"
done
```

`vocal-to-song` is a relative symlink to `../../vocal-to-song`, so it resolves
to the bundled repo. If you copy instead of symlink, link that one explicitly:

```bash
ln -sfn /path/to/creative_bundle/vocal-to-song ~/.claude/skills/vocal-to-song
```

**Not copied:** plugin-provided skills (see `PLUGINS.md`) and the standard
Anthropic document skills (`docx`, `pdf`, `pptx`, `xlsx`, `canvas-design`,
`theme-factory`). Those were dangling links into `/tmp` on the source machine;
install them from the official skills/plugin marketplace instead.

## Memory

Two memory systems are represented:

1. **`remember` plugin memory** (`video-editor/.remember/`). The bundled
   `video-editor` already has its 41 memory notes (`now.md`, `recent.md`,
   `archive.md`, `today-*.md`, and the provider/config notes) restored in place.
   With the `remember` plugin enabled, Claude loads them when it opens that repo.
   The nested `.remember/.gitignore` (`*`) keeps them out of git inside that
   repo; in this combined `creative_bundle` repository they are force-added.
2. **Archive for repos not in the bundle.** `memory/tool-lab/` and `memory/root/`
   hold the markdown memory from `tool-lab` and the parent workspace. Copy them
   into the matching project's `.remember/` if you add those repos later.

To use persistent Claude memory instead of the plugin, copy a repo's notes into
`~/.claude/projects/<project-slug>/memory/` and add a `MEMORY.md` index; see
`memory/README.md`.

## Docs

`docs/INDEX.md` maps every documentation folder and the notable entries in it.

## Plugins

`PLUGINS.md` lists the plugins this stack used and the ones that matter for the
skills above.

## Settings

No `settings.json` or `settings.local.json` is shared: the source machine's
local settings contain machine paths and personal API keys. `~/.claude/settings.json`
on that machine only contributes plugin enablement (captured in `PLUGINS.md`)
and a `CHEAPAG_PROVIDER` env value; configure your own.
