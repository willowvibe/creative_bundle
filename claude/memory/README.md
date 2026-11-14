# Memory archive

The `remember` plugin stores session memory as Markdown next to the repo it
belongs to, under `.remember/`:

```
.remember/
├── now.md          # current session buffer
├── today-<date>.md # daily notes
├── recent.md       # last 7 days
├── archive.md      # older notes
└── core-memories.md
```

## Already in place

`video-editor/.remember/` is populated with that repo's 41 notes, so the
`remember` plugin picks them up automatically when Claude opens the repo. The
directory carries a `.gitignore` containing `*`, keeping the notes untracked.

## Archives here

| Folder | Source | Use |
|---|---|---|
| `tool-lab/` | `tool-lab/.remember/` | Copy into a future `tool-lab/.remember/` if that repo is added |
| `root/` | `/mnt/data2/git_repos/.remember/` | Parent-workspace notes; copy to the matching project root |

## Using them as persistent Claude memory

If you prefer the file-based memory system over the plugin, copy a repo's notes
into the project memory directory and add an index:

```bash
mkdir -p ~/.claude/projects/<project-slug>/memory
cp video-editor/.remember/*.md ~/.claude/projects/<project-slug>/memory/
```

Then create `MEMORY.md` there with one line per note:

```markdown
- [Provider setup](poyo-music-and-sarvam-tts-providers-2026-07-17.md) — PoYo music + Sarvam TTS
```

The project slug is derived from the project path; check
`ls ~/.claude/projects/` after opening the repo once to see the exact name.

## What is not included

Only Markdown notes are archived. The `.remember/logs/` files (large hook and
session logs, several MB per month) and `tmp/` state were left out.
